"""Windows system information: OS build, CPU, memory, display adapters, UI language.

Only the standard library (winreg, ctypes) and psutil are used, so this works in a frozen app
without WMI. Display adapters come from DXGI (the same list Direct3D and DirectML see).
"""
from __future__ import annotations

import ctypes
import logging
import platform
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)


def windows_build() -> tuple[int, int, int]:
    """(major, minor, build) from RtlGetVersion (not affected by app-compat manifests)."""
    class OSVERSIONINFOEXW(ctypes.Structure):
        _fields_ = [("dwOSVersionInfoSize", wintypes.DWORD), ("dwMajorVersion", wintypes.DWORD),
                    ("dwMinorVersion", wintypes.DWORD), ("dwBuildNumber", wintypes.DWORD),
                    ("dwPlatformId", wintypes.DWORD), ("szCSDVersion", wintypes.WCHAR * 128),
                    ("wServicePackMajor", wintypes.WORD), ("wServicePackMinor", wintypes.WORD),
                    ("wSuiteMask", wintypes.WORD), ("wProductType", ctypes.c_byte), ("wReserved", ctypes.c_byte)]
    info = OSVERSIONINFOEXW()
    info.dwOSVersionInfoSize = ctypes.sizeof(info)
    try:
        if ctypes.WinDLL("ntdll").RtlGetVersion(ctypes.byref(info)) == 0:
            return info.dwMajorVersion, info.dwMinorVersion, info.dwBuildNumber
    except OSError:
        pass
    v = sys.getwindowsversion()
    return v.major, v.minor, v.build


def is_windows_11() -> bool:
    """Windows 11 reports 10.0 with build >= 22000."""
    major, _, build = windows_build()
    return major >= 10 and build >= 22000


def product_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = str(winreg.QueryValueEx(k, "ProductName")[0])
            try:
                disp = str(winreg.QueryValueEx(k, "DisplayVersion")[0])
            except OSError:
                disp = ""
    except OSError:
        return platform.platform()
    # ProductName still says "Windows 10" on Windows 11 client builds
    if is_windows_11() and name.startswith("Windows 10"):
        name = "Windows 11" + name[len("Windows 10"):]
    return f"{name} {disp}".strip()


def cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor()


# ------------------------------------------------------------------------------------------- DXGI
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", _LUID), ("Flags", wintypes.UINT)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


_IID_IDXGIFactory1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
_IID_IDXGIDevice = _GUID(0x54EC77FA, 0x1377, 0x44E6, (ctypes.c_ubyte * 8)(0x8C, 0x32, 0x88, 0xFD, 0x5F, 0x44, 0xC8, 0x4C))
_IID_ID3D12Device = _GUID(0x189819F1, 0x1DB6, 0x4B57, (ctypes.c_ubyte * 8)(0xBE, 0x54, 0x18, 0x21, 0x33, 0x9B, 0x85, 0xF7))
FEATURE_LEVELS = {0xC100: "12_1", 0xC000: "12_0", 0xB100: "11_1", 0xB000: "11_0", 0xA100: "10_1", 0xA000: "10_0",
                  0x9300: "9_3", 0x9200: "9_2", 0x9100: "9_1"}
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x1414: "Microsoft", 0x5143: "Qualcomm"}
DXGI_ADAPTER_FLAG_SOFTWARE = 2


def _vcall(obj, index: int, restype, *argtypes):
    """Function at `index` of a COM object's vtable."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def _driver_version(ad) -> str:
    """User-mode driver version (IDXGIAdapter::CheckInterfaceSupport), e.g. "32.0.15.6094"; "" when unknown."""
    v = ctypes.c_longlong()
    fn = _vcall(ad, 9, ctypes.c_long, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_longlong))
    if fn(ad, ctypes.byref(_IID_IDXGIDevice), ctypes.byref(v)) != 0:
        return ""
    x = v.value
    return f"{(x >> 48) & 0xFFFF}.{(x >> 32) & 0xFFFF}.{(x >> 16) & 0xFFFF}.{x & 0xFFFF}"


def _d3d11_feature_level(ad) -> str:
    """Highest Direct3D 11 feature level of the adapter (a device is created and released at once)."""
    try:
        d3d11 = ctypes.WinDLL("d3d11")
    except OSError:
        return ""
    create = d3d11.D3D11CreateDevice
    for levels in ((0xC100, 0xC000, 0xB100, 0xB000, 0xA100, 0xA000), (0xB000, 0xA100, 0xA000)):
        arr = (ctypes.c_uint * len(levels))(*levels)
        dev, ctx, got = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
        # D3D_DRIVER_TYPE_UNKNOWN (an explicit adapter), no flags, D3D11_SDK_VERSION 7
        hr = create(ad, 0, None, 0, arr, len(levels), 7, ctypes.byref(dev), ctypes.byref(got), ctypes.byref(ctx))
        for obj in (ctx, dev):
            if obj.value:
                _vcall(obj, 2, wintypes.ULONG)(obj)
        if hr == 0:
            return FEATURE_LEVELS.get(got.value, f"0x{got.value:X}")
    return ""


def _d3d12_ok(ad) -> bool:
    """Direct3D 12 at feature level 11_0 on this adapter: what DirectML needs (checked without creating a device)."""
    try:
        d3d12 = ctypes.WinDLL("d3d12")
    except OSError:
        return False
    hr = d3d12.D3D12CreateDevice(ad, 0xB000, ctypes.byref(_IID_ID3D12Device), None)
    return hr in (0, 1)                                      # S_FALSE: it would succeed


def display_adapters(details: bool = False) -> list[dict]:
    """DXGI adapters in system order: name, vendor, dedicated VRAM, software flag, LUID; with `details` also the
    driver version, the Direct3D 11 feature level and Direct3D 12 support (a few ms per adapter)."""
    out: list[dict] = []
    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError:
        return out
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory)) != 0:
        return out
    try:
        enum1 = _vcall(factory, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))  # EnumAdapters1
        i = 0
        while True:
            ad = ctypes.c_void_p()
            if enum1(factory, i, ctypes.byref(ad)) != 0:      # DXGI_ERROR_NOT_FOUND ends the list
                break
            try:
                desc = _DXGI_ADAPTER_DESC1()
                if _vcall(ad, 10, ctypes.c_long, ctypes.POINTER(_DXGI_ADAPTER_DESC1))(ad, ctypes.byref(desc)) == 0:
                    out.append({"index": i, "name": desc.Description, "vendor_id": desc.VendorId,
                                "vendor": VENDORS.get(desc.VendorId, f"0x{desc.VendorId:04X}"),
                                "device_id": desc.DeviceId,
                                "dedicated_vram_mb": int(desc.DedicatedVideoMemory // 2**20),
                                "shared_memory_mb": int(desc.SharedSystemMemory // 2**20),
                                "software": bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE),
                                "luid": (desc.AdapterLuid.HighPart << 32) | desc.AdapterLuid.LowPart})
                    if details:
                        for key, fn, default in (("driver_version", _driver_version, ""),
                                                 ("d3d11_feature_level", _d3d11_feature_level, ""),
                                                 ("d3d12", _d3d12_ok, False)):
                            try:
                                out[-1][key] = fn(ad)
                            except Exception:            # noqa: BLE001 - details are informative only
                                out[-1][key] = default
            finally:
                _vcall(ad, 2, wintypes.ULONG)(ad)              # Release
            i += 1
    finally:
        _vcall(factory, 2, wintypes.ULONG)(factory)
    return out


def hardware_gpus(details: bool = False) -> list[dict]:
    """Adapters that can actually accelerate work (excludes WARP / Basic Render Driver)."""
    return [a for a in display_adapters(details)
            if not a["software"] and a["vendor_id"] != 0x1414]


def ui_language() -> str:
    """ISO language of the Windows display language, e.g. 'ru' or 'en'."""
    try:
        import locale
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(lcid, "")
        return name.split("_")[0].lower()
    except Exception:
        return ""


def onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort  # type: ignore
        return list(ort.get_available_providers())
    except Exception:
        return []


def inspect_environment() -> dict:
    import shutil

    import psutil
    major, minor, build = windows_build()
    return {
        "os": "windows",
        "windows": product_name(),
        "windows_build": build,
        "windows_11": is_windows_11(),
        "arch": platform.machine(),
        "cpu": cpu_name(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "memory_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": sys.version.split()[0],
        "display_adapters": display_adapters(),
        "hardware_gpu": bool(hardware_gpus()),
        "onnx_providers": onnx_providers(),
        "ffmpeg": shutil.which("ffmpeg"),
    }
