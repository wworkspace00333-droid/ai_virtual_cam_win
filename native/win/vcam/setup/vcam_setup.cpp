// vcam-setup.exe — installs / removes the "AI Virtual Camera" devices. Runs elevated (manifest), started by
// the app's Virtual Camera page (UAC prompt) or by the installer.
//
//   vcam-setup --install [--dshow-only | --mf-only]   register the camera(s)
//   vcam-setup --uninstall                            remove both
//   vcam-setup --status                               print what is registered
//
// DirectShow camera (vcam_dshow.dll): Windows 10 and 11, loaded by DirectShow apps in-process.
// Media Foundation camera (vcam_mf.dll): Windows 11 virtual camera (MFCreateVirtualCamera, system-wide,
// all users), loaded by the Frame Server service; seen by every Media Foundation app.
// --install registers both where available. The DLLs must sit next to this executable in a folder the
// LOCAL SERVICE account can read (Program Files after installation).
// Exit codes (read by vcstudio/camera/camera_manager.py): 0 ok, 1 error, 3 installed the DirectShow fallback
// because the Media Foundation camera could not start, 4 component file missing.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mfvirtualcamera.h>
#include <wrl/client.h>

#include <cstdio>
#include <string>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "advapi32.lib")

using Microsoft::WRL::ComPtr;
static const wchar_t kName[] = L"AI Virtual Camera";
static const wchar_t kDShowClsid[] = L"{59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE}";
static const wchar_t kMfClsid[] = L"{20C0F518-9F28-4168-9F73-030F243FB3B9}";

static std::wstring here(const wchar_t *file) {
	wchar_t p[MAX_PATH];
	GetModuleFileNameW(nullptr, p, MAX_PATH);
	std::wstring s(p);
	return s.substr(0, s.find_last_of(L"\\/") + 1) + file;
}

static int call_export(const wchar_t *dll, const char *fn) {
	std::wstring path = here(dll);
	if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) {
		fwprintf(stderr, L"missing %ls\n", path.c_str());
		return 4;
	}
	HMODULE m = LoadLibraryExW(path.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
	if (!m) {
		fwprintf(stderr, L"cannot load %ls (%lu)\n", path.c_str(), GetLastError());
		return 1;
	}
	typedef HRESULT(STDAPICALLTYPE * Fn)();
	Fn f = (Fn)GetProcAddress(m, fn);
	HRESULT hr = f ? f() : E_NOINTERFACE;
	FreeLibrary(m);
	fprintf(hr == S_OK ? stdout : stderr, "%ls %s: 0x%08lX\n", dll, fn, (unsigned long)hr);
	return SUCCEEDED(hr) ? 0 : 1;
}

static bool registered(const wchar_t *clsid) {
	std::wstring key = std::wstring(L"CLSID\\") + clsid + L"\\InprocServer32";
	HKEY k;
	if (RegOpenKeyExW(HKEY_CLASSES_ROOT, key.c_str(), 0, KEY_READ | KEY_WOW64_64KEY, &k) != ERROR_SUCCESS) return false;
	RegCloseKey(k);
	return true;
}

// MFCreateVirtualCamera lives in mfsensorgroup.dll on Windows 11 only: resolve it at run time.
typedef HRESULT(STDAPICALLTYPE *CreateVC)(__MIDL___MIDL_itf_mfvirtualcamera_0000_0000_0001,
                                          __MIDL___MIDL_itf_mfvirtualcamera_0000_0000_0002,
                                          __MIDL___MIDL_itf_mfvirtualcamera_0000_0000_0003, LPCWSTR, LPCWSTR,
                                          const GUID *, ULONG, IMFVirtualCamera **);
static CreateVC create_vc() {
	HMODULE m = LoadLibraryW(L"mfsensorgroup.dll");
	return m ? (CreateVC)GetProcAddress(m, "MFCreateVirtualCamera") : nullptr;
}

static HRESULT open_vc(IMFVirtualCamera **vc) {
	CreateVC create = create_vc();
	if (!create) return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
	return create(MFVirtualCameraType_SoftwareCameraSource, MFVirtualCameraLifetime_System,
	              MFVirtualCameraAccess_AllUsers, kName, kMfClsid, nullptr, 0, vc);
}

static int install_mf() {
	if (!create_vc()) {
		printf("media foundation: MFCreateVirtualCamera not available (Windows 11 22000+ required) - skipped\n");
		return 0;
	}
	int rc = call_export(L"vcam_mf.dll", "DllRegisterServer");
	if (rc) return rc;
	ComPtr<IMFVirtualCamera> vc;
	HRESULT hr = open_vc(&vc);
	if (SUCCEEDED(hr)) hr = vc->Start(nullptr);
	printf("media foundation: MFCreateVirtualCamera + Start: 0x%08lX\n", (unsigned long)hr);
	if (FAILED(hr)) {
		if (hr == E_ACCESSDENIED)
			printf("media foundation: access denied - camera access is turned off in Settings > Privacy & security > "
			       "Camera (\"Let apps access your camera\")\n");
		if (vc) vc->Remove();               // do not leave a half-installed device behind
		call_export(L"vcam_mf.dll", "DllUnregisterServer");
	}
	if (vc) vc->Shutdown();
	return SUCCEEDED(hr) ? 0 : 1;
}

static int uninstall_mf() {
	if (!create_vc()) return 0;
	ComPtr<IMFVirtualCamera> vc;
	HRESULT hr = open_vc(&vc);
	if (SUCCEEDED(hr)) hr = vc->Remove();
	printf("media foundation: Remove: 0x%08lX\n", (unsigned long)hr);
	if (vc) vc->Shutdown();
	call_export(L"vcam_mf.dll", "DllUnregisterServer");
	return 0;
}

int wmain(int argc, wchar_t **argv) {
	std::wstring cmd = argc > 1 ? argv[1] : L"--status";
	std::wstring opt = argc > 2 ? argv[2] : L"";
	CoInitializeEx(nullptr, COINIT_MULTITHREADED);
	MFStartup(MF_VERSION);
	int rc = 0;
	if (cmd == L"--install") {
		// Default: Windows 11 -> the Media Foundation camera only (DirectShow apps see it through the Windows
		// bridge, so a second DirectShow device would show up twice); if it cannot start, the DirectShow
		// camera instead. Windows 10 -> DirectShow. --both / --dshow-only / --mf-only override.
		if (opt == L"--dshow-only") {
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
		} else if (opt == L"--mf-only") {
			rc = install_mf();
		} else if (opt == L"--both") {
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer") | install_mf();
		} else if (create_vc() && install_mf() == 0) {
			if (registered(kDShowClsid)) call_export(L"vcam_dshow.dll", "DllUnregisterServer");
			printf("installed: media foundation camera\n");
		} else {
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
			printf("installed: directshow camera%s\n", create_vc() ? " (media foundation camera could not start)" : "");
			if (rc == 0 && create_vc()) rc = 3;     // installed, but not the preferred camera
		}
	} else if (cmd == L"--uninstall") {
		uninstall_mf();
		rc = call_export(L"vcam_dshow.dll", "DllUnregisterServer");
	} else if (cmd == L"--status") {
		printf("directshow: %s\n", registered(kDShowClsid) ? "registered" : "not registered");
		printf("media foundation: %s%s\n", registered(kMfClsid) ? "registered" : "not registered",
		       create_vc() ? "" : " (MFCreateVirtualCamera unavailable on this Windows)");
	} else {
		fwprintf(stderr, L"usage: vcam-setup --install [--dshow-only|--mf-only] | --uninstall | --status\n");
		rc = 1;
	}
	MFShutdown();
	return rc;
}
