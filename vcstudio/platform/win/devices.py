"""Video capture device enumeration on Windows: DirectShow and Media Foundation.

The two APIs list different sets of cameras: physical webcams appear in both, while virtual cameras
that are DirectShow filters (OBS Virtual Camera on most versions, this app's DirectShow camera) appear
only in DirectShow, and Media Foundation virtual cameras (Windows 11) appear in both. OpenCV opens a
device by its *index in that backend's own order*, so each backend keeps its enumeration order here.

  DirectShow:        ICreateDevEnum -> CLSID_VideoInputDeviceCategory -> IMoniker -> IPropertyBag
                     (FriendlyName, DevicePath / CLSID); formats from the output pin's IAMStreamConfig.
  Media Foundation:  MFEnumDeviceSources(VIDCAP) -> IMFActivate (friendly name, symbolic link);
                     formats from the source's presentation descriptor (media types of stream 0).

unique_id is the DevicePath / symbolic link (identical for a physical camera in both APIs), or
"dshow-clsid:{CLSID}" for DirectShow filters without a device path.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass

from vcstudio.core.devices import VideoDevice, is_own_output
from vcstudio.core.video_capture import DeviceFormat
from vcstudio.platform.win.com import (CLSCTX_INPROC_SERVER, GUID, VARIANT, VT_BSTR, com_object, init_thread, ole32,
                                       oleaut32, release, subtype_name, uninit_thread, vcall)

log = logging.getLogger(__name__)

# ------------------------------------------------------------------------------------------- GUIDs
CLSID_SystemDeviceEnum = GUID.of("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")
IID_ICreateDevEnum = GUID.of("29840822-5B84-11D0-BD3B-00A0C911CE86")
CLSID_VideoInputDeviceCategory = GUID.of("860BB310-5D01-11D0-BD3B-00A0C911CE86")
IID_IPropertyBag = GUID.of("55272A00-42CB-11CE-8135-00AA004BB851")
IID_IBaseFilter = GUID.of("56A86895-0AD3-11CE-B03A-0020AF0BA770")
IID_IAMStreamConfig = GUID.of("C6E13340-30AC-11D0-A18C-00A0C9118956")
FORMAT_VideoInfo = GUID.of("05589F80-C356-11CE-BF01-00AA0055595A")
FORMAT_VideoInfo2 = GUID.of("F72A76A0-EB0A-11D0-ACE4-0000C0CC16BA")

MF_VERSION = 0x00020070
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE = GUID.of("C60AC5FE-252A-478F-A0EF-BC8FA5F7CAD3")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID = GUID.of("8AC3587A-4AE7-42D8-99E0-0A6013EEF90F")
MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME = GUID.of("60D0E559-52F8-4FA2-BBCE-ACDB34A8EC01")
MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK = GUID.of("58F0AAD8-22BF-4F8A-BB3D-D2C4978C6E2F")
IID_IMFMediaSource = GUID.of("279A808D-AEC7-40C8-9C6B-A6B492C78A66")
MF_MT_SUBTYPE = GUID.of("F7E34C9A-42E8-4714-B74B-CB29D72C35E5")
MF_MT_FRAME_SIZE = GUID.of("1652C33D-D6B2-4012-B834-72030849A37D")
MF_MT_FRAME_RATE = GUID.of("C459A2E8-3D2C-4E44-B132-FEE5156C7BB0")
MF_MT_FRAME_RATE_RANGE_MAX = GUID.of("D2E7558C-DC1F-403F-9A72-D28BB1EB3B5E")
MF_MT_FRAME_RATE_RANGE_MIN = GUID.of("D2E7558D-DC1F-403F-9A72-D28BB1EB3B5E")

_lock = threading.Lock()


@dataclass(frozen=True)
class BackendDevice:
    index: int              # position in this backend's enumeration (what OpenCV opens)
    name: str
    unique_id: str
    backend: str            # "directshow" | "mediafoundation"


# --------------------------------------------------------------------------------------- COM thread
def _on_com_thread(fn):
    """Run fn on a short-lived MTA thread of its own: enumeration must not depend on the caller's
    apartment (the Qt GUI thread is an STA) and must not leave COM initialised on a worker thread."""
    box: list = []

    def run():
        owned = init_thread()
        try:
            box.append(fn())
        except Exception as e:      # reported to the caller
            box.append(e)
        finally:
            if owned:
                uninit_thread()
    t = threading.Thread(target=run, name="com-enum", daemon=True)
    t.start()
    t.join(20)
    if not box:
        raise TimeoutError("device enumeration did not finish")
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


# --------------------------------------------------------------------------------------- DirectShow
def _bag_string(bag, name: str) -> str | None:
    v = VARIANT()
    hr = vcall(bag, 3, ctypes.c_long, wintypes.LPCWSTR, ctypes.POINTER(VARIANT), ctypes.c_void_p)(
        bag, name, ctypes.byref(v), None)                                  # IPropertyBag::Read
    try:
        if hr == 0 and v.vt == VT_BSTR and v.p:
            return ctypes.wstring_at(v.p)
        return None
    finally:
        oleaut32.VariantClear(ctypes.byref(v))


def _dshow_monikers():
    """Yield (index, IMoniker) for every video input device; caller releases each moniker."""
    dev_enum = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_SystemDeviceEnum), None, CLSCTX_INPROC_SERVER,
                                ctypes.byref(IID_ICreateDevEnum), ctypes.byref(dev_enum))
    if hr != 0:
        raise OSError(f"CoCreateInstance(SystemDeviceEnum) failed: 0x{hr & 0xFFFFFFFF:08X}")
    with com_object(dev_enum):
        enum = ctypes.c_void_p()
        hr = vcall(dev_enum, 3, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD)(
            dev_enum, ctypes.byref(CLSID_VideoInputDeviceCategory), ctypes.byref(enum), 0)   # CreateClassEnumerator
        if hr != 0 or not enum:          # S_FALSE: the category is empty
            return
        with com_object(enum):
            nxt = vcall(enum, 3, ctypes.c_long, wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.ULONG))                               # IEnumMoniker::Next
            i = 0
            while True:
                mon, got = ctypes.c_void_p(), wintypes.ULONG(0)
                if nxt(enum, 1, ctypes.byref(mon), ctypes.byref(got)) != 0 or got.value == 0:
                    break
                yield i, mon
                i += 1


def _dshow_identity(mon) -> tuple[str, str]:
    bag = ctypes.c_void_p()
    hr = vcall(mon, 9, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
               ctypes.POINTER(ctypes.c_void_p))(mon, None, None, ctypes.byref(IID_IPropertyBag), ctypes.byref(bag))
    if hr != 0:                                                                       # IMoniker::BindToStorage
        return "?", ""
    with com_object(bag):
        name = _bag_string(bag, "FriendlyName") or "?"
        path = _bag_string(bag, "DevicePath")
        if not path:
            clsid = _bag_string(bag, "CLSID")
            path = f"dshow-clsid:{clsid}" if clsid else f"dshow-name:{name}"
        return name, path


def dshow_devices() -> list[BackendDevice]:
    def work():
        out = []
        for i, mon in _dshow_monikers():
            with com_object(mon):
                name, uid = _dshow_identity(mon)
            out.append(BackendDevice(i, name, uid, "directshow"))
        return out
    return _on_com_thread(work)


class _AM_MEDIA_TYPE(ctypes.Structure):
    _fields_ = [("majortype", GUID), ("subtype", GUID), ("bFixedSizeSamples", wintypes.BOOL),
                ("bTemporalCompression", wintypes.BOOL), ("lSampleSize", wintypes.ULONG), ("formattype", GUID),
                ("pUnk", ctypes.c_void_p), ("cbFormat", wintypes.ULONG), ("pbFormat", ctypes.c_void_p)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _VIDEO_STREAM_CONFIG_CAPS(ctypes.Structure):
    _fields_ = [("guid", GUID), ("VideoStandard", wintypes.ULONG), ("InputSize", _SIZE),
                ("MinCroppingSize", _SIZE), ("MaxCroppingSize", _SIZE), ("CropGranularityX", ctypes.c_int),
                ("CropGranularityY", ctypes.c_int), ("CropAlignX", ctypes.c_int), ("CropAlignY", ctypes.c_int),
                ("MinOutputSize", _SIZE), ("MaxOutputSize", _SIZE), ("OutputGranularityX", ctypes.c_int),
                ("OutputGranularityY", ctypes.c_int), ("StretchTapsX", ctypes.c_int), ("StretchTapsY", ctypes.c_int),
                ("ShrinkTapsX", ctypes.c_int), ("ShrinkTapsY", ctypes.c_int), ("MinFrameInterval", ctypes.c_longlong),
                ("MaxFrameInterval", ctypes.c_longlong), ("MinBitsPerSecond", wintypes.LONG),
                ("MaxBitsPerSecond", wintypes.LONG)]


def _free_media_type(pmt) -> None:
    mt = pmt.contents
    if mt.pbFormat:
        ole32.CoTaskMemFree(mt.pbFormat)
    if mt.pUnk:
        release(ctypes.c_void_p(mt.pUnk))
    ole32.CoTaskMemFree(pmt)


def _fps_from_100ns(interval: int) -> float:
    return round(1e7 / interval, 2) if interval > 0 else 0.0


def _dshow_filter(mon, unique_id: str):
    """IBaseFilter for a device moniker. Virtual cameras registered as plain filters (no DevicePath) are
    created from their CLSID directly when binding fails.

    Measured with OBS Virtual Camera 33: the first object its class factory creates in a process does not
    answer QueryInterface(IBaseFilter) (E_NOINTERFACE, also through IMoniker::BindToObject); the next
    CoCreateInstance returns a working filter. Hence a few attempts."""
    filt = ctypes.c_void_p()
    hr = vcall(mon, 8, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
               ctypes.POINTER(ctypes.c_void_p))(mon, None, None, ctypes.byref(IID_IBaseFilter), ctypes.byref(filt))
    if hr == 0:                                                                       # IMoniker::BindToObject
        return filt
    if unique_id.startswith("dshow-clsid:"):
        clsid = GUID.of(unique_id.split(":", 1)[1])
        for _ in range(3):
            filt = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                                        ctypes.byref(IID_IBaseFilter), ctypes.byref(filt))
            if hr == 0:
                return filt
    log.debug("cannot bind DirectShow filter %s: 0x%08X", unique_id, hr & 0xFFFFFFFF)
    return None


def _dshow_formats_of(mon, unique_id: str) -> list[DeviceFormat]:
    filt = _dshow_filter(mon, unique_id)
    if filt is None:
        return []
    out: list[DeviceFormat] = []
    with com_object(filt):
        pins = ctypes.c_void_p()
        if vcall(filt, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(filt, ctypes.byref(pins)) != 0:
            return []                                                                 # IBaseFilter::EnumPins
        with com_object(pins):
            nxt = vcall(pins, 3, ctypes.c_long, wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.ULONG))
            while True:
                pin, got = ctypes.c_void_p(), wintypes.ULONG(0)
                if nxt(pins, 1, ctypes.byref(pin), ctypes.byref(got)) != 0 or got.value == 0:
                    break
                with com_object(pin):
                    direction = ctypes.c_int(-1)
                    vcall(pin, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(pin, ctypes.byref(direction))
                    if direction.value != 1:                                          # PINDIR_OUTPUT
                        continue
                    cfg = ctypes.c_void_p()
                    if vcall(pin, 0, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                            pin, ctypes.byref(IID_IAMStreamConfig), ctypes.byref(cfg)) != 0:
                        continue
                    with com_object(cfg):
                        count, size = ctypes.c_int(0), ctypes.c_int(0)
                        vcall(cfg, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))(
                            cfg, ctypes.byref(count), ctypes.byref(size))             # GetNumberOfCapabilities
                        caps_buf = (ctypes.c_byte * max(size.value, ctypes.sizeof(_VIDEO_STREAM_CONFIG_CAPS)))()
                        get_caps = vcall(cfg, 6, ctypes.c_long, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_AM_MEDIA_TYPE)),
                                         ctypes.c_void_p)                             # GetStreamCaps
                        for i in range(count.value):
                            pmt = ctypes.POINTER(_AM_MEDIA_TYPE)()
                            if get_caps(cfg, i, ctypes.byref(pmt), caps_buf) != 0 or not pmt:
                                continue
                            try:
                                mt = pmt.contents
                                if mt.formattype == FORMAT_VideoInfo and mt.cbFormat >= 88:
                                    bmi_off = 48
                                elif mt.formattype == FORMAT_VideoInfo2 and mt.cbFormat >= 112:
                                    bmi_off = 72
                                else:
                                    continue
                                avg = ctypes.c_longlong.from_address(mt.pbFormat + 40).value
                                w = ctypes.c_int32.from_address(mt.pbFormat + bmi_off + 4).value
                                h = abs(ctypes.c_int32.from_address(mt.pbFormat + bmi_off + 8).value)
                                caps = _VIDEO_STREAM_CONFIG_CAPS.from_buffer(caps_buf)
                                fmax = _fps_from_100ns(caps.MinFrameInterval) or _fps_from_100ns(avg)
                                fmin = _fps_from_100ns(caps.MaxFrameInterval) or fmax
                                if w > 0 and h > 0:
                                    out.append(DeviceFormat(w, h, subtype_name(mt.subtype), min(fmin, fmax), fmax))
                            finally:
                                _free_media_type(pmt)
                break                         # first output pin (the capture pin)
    return out


def dshow_formats(unique_id: str) -> list[DeviceFormat]:
    def work():
        for _, mon in _dshow_monikers():
            with com_object(mon):
                _, uid = _dshow_identity(mon)
                if uid == unique_id:
                    return _dshow_formats_of(mon, uid)
        return []
    return _dedupe(_on_com_thread(work))


# --------------------------------------------------------------------------------- Media Foundation
_mf_started = False


def _mf():
    global _mf_started
    mfplat = ctypes.WinDLL("mfplat")
    mf = ctypes.WinDLL("mf")
    if not _mf_started:
        if mfplat.MFStartup(MF_VERSION, 0) != 0:
            raise OSError("MFStartup failed")
        _mf_started = True
    return mfplat, mf


def _mf_activates():
    """List of IMFActivate pointers for video capture sources (caller releases them)."""
    mfplat, mf = _mf()
    attrs = ctypes.c_void_p()
    if mfplat.MFCreateAttributes(ctypes.byref(attrs), 1) != 0:
        raise OSError("MFCreateAttributes failed")
    with com_object(attrs):
        vcall(attrs, 24, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(            # IMFAttributes::SetGUID
            attrs, ctypes.byref(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE), ctypes.byref(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID))
        arr, count = ctypes.POINTER(ctypes.c_void_p)(), wintypes.UINT(0)
        hr = mf.MFEnumDeviceSources(attrs, ctypes.byref(arr), ctypes.byref(count))
        if hr != 0:
            raise OSError(f"MFEnumDeviceSources failed: 0x{hr & 0xFFFFFFFF:08X}")
        items = [ctypes.c_void_p(arr[i]) for i in range(count.value)]
        if arr:
            ole32.CoTaskMemFree(arr)
        return items


def _mf_string(obj, key: GUID) -> str:
    p, n = ctypes.c_wchar_p(), wintypes.UINT(0)
    hr = vcall(obj, 13, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(wintypes.UINT))(
        obj, ctypes.byref(key), ctypes.byref(p), ctypes.byref(n))                     # GetAllocatedString
    if hr != 0:
        return ""
    s = p.value or ""
    ole32.CoTaskMemFree(ctypes.cast(p, ctypes.c_void_p))
    return s


def mf_devices() -> list[BackendDevice]:
    def work():
        out = []
        for i, act in enumerate(_mf_activates()):
            with com_object(act):
                out.append(BackendDevice(i, _mf_string(act, MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME) or "?",
                                         _mf_string(act, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK),
                                         "mediafoundation"))
        return out
    return _on_com_thread(work)


def _mf_u64(obj, key: GUID) -> int | None:
    v = ctypes.c_uint64(0)
    hr = vcall(obj, 8, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64))(obj, ctypes.byref(key), ctypes.byref(v))
    return v.value if hr == 0 else None                                               # IMFAttributes::GetUINT64


def _ratio(v: int | None) -> float:
    if not v:
        return 0.0
    num, den = v >> 32, v & 0xFFFFFFFF
    return round(num / den, 2) if den else 0.0


def mf_formats(unique_id: str) -> list[DeviceFormat]:
    def work():
        out: list[DeviceFormat] = []
        acts = _mf_activates()
        try:
            for act in acts:
                if _mf_string(act, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK) != unique_id:
                    continue
                src = ctypes.c_void_p()
                if vcall(act, 33, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                        act, ctypes.byref(IID_IMFMediaSource), ctypes.byref(src)) != 0:   # IMFActivate::ActivateObject
                    break
                try:
                    pd = ctypes.c_void_p()
                    if vcall(src, 8, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(src, ctypes.byref(pd)) != 0:
                        break                                       # IMFMediaSource::CreatePresentationDescriptor
                    with com_object(pd):
                        sel, sd = wintypes.BOOL(), ctypes.c_void_p()
                        if vcall(pd, 34, ctypes.c_long, wintypes.DWORD, ctypes.POINTER(wintypes.BOOL),
                                 ctypes.POINTER(ctypes.c_void_p))(pd, 0, ctypes.byref(sel), ctypes.byref(sd)) != 0:
                            break                                   # GetStreamDescriptorByIndex
                        with com_object(sd):
                            th = ctypes.c_void_p()
                            if vcall(sd, 34, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(sd, ctypes.byref(th)) != 0:
                                break                               # IMFStreamDescriptor::GetMediaTypeHandler
                            with com_object(th):
                                n = wintypes.DWORD(0)
                                vcall(th, 4, ctypes.c_long, ctypes.POINTER(wintypes.DWORD))(th, ctypes.byref(n))
                                for i in range(n.value):
                                    mt = ctypes.c_void_p()
                                    if vcall(th, 5, ctypes.c_long, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                                            th, i, ctypes.byref(mt)) != 0:  # GetMediaTypeByIndex
                                        continue
                                    with com_object(mt):
                                        size = _mf_u64(mt, MF_MT_FRAME_SIZE)
                                        if not size:
                                            continue
                                        sub = GUID()
                                        vcall(mt, 10, ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID))(
                                            mt, ctypes.byref(MF_MT_SUBTYPE), ctypes.byref(sub))   # GetGUID
                                        fps = _ratio(_mf_u64(mt, MF_MT_FRAME_RATE))
                                        fmax = _ratio(_mf_u64(mt, MF_MT_FRAME_RATE_RANGE_MAX)) or fps
                                        fmin = _ratio(_mf_u64(mt, MF_MT_FRAME_RATE_RANGE_MIN)) or fps
                                        out.append(DeviceFormat(size >> 32, size & 0xFFFFFFFF, subtype_name(sub),
                                                                min(fmin, fmax), fmax))
                finally:
                    vcall(src, 12, ctypes.c_long)(src)              # IMFMediaSource::Shutdown
                    release(src)
                    vcall(act, 34, ctypes.c_long)(act)              # IMFActivate::ShutdownObject
                break
        finally:
            for a in acts:
                release(a)
        return out
    return _dedupe(_on_com_thread(work))


# ------------------------------------------------------------------------------------- public API
def _dedupe(fmts: list[DeviceFormat]) -> list[DeviceFormat]:
    seen, out = set(), []
    for f in fmts:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def backend_devices(method: str) -> list[BackendDevice]:
    """Devices of one backend in its own order ("directshow" | "mediafoundation")."""
    with _lock:
        try:
            return dshow_devices() if method == "directshow" else mf_devices()
        except Exception as e:
            log.warning("%s device enumeration failed: %s", method, e)
            return []


def list_video_devices() -> list[VideoDevice]:
    """All cameras (union of both backends). model_id says which APIs can open the device."""
    ds, mf = backend_devices("directshow"), backend_devices("mediafoundation")
    out: dict[str, VideoDevice] = {}
    apis: dict[str, list[str]] = {}
    for d in ds + mf:
        key = d.unique_id or f"{d.backend}:{d.name}"
        apis.setdefault(key, []).append("DirectShow" if d.backend == "directshow" else "Media Foundation")
        if key not in out:
            out[key] = VideoDevice(key, d.name, "", is_own_output(d.name, ""))
    return [VideoDevice(v.unique_id, v.name, " + ".join(apis[k]), v.is_own_output) for k, v in out.items()]


def formats_for_device_id(unique_id: str) -> list[DeviceFormat]:
    with _lock:
        for fn in (mf_formats, dshow_formats):
            try:
                f = fn(unique_id)
            except Exception as e:
                log.debug("%s(%s) failed: %s", fn.__name__, unique_id, e)
                continue
            if f:
                return f
    return []
