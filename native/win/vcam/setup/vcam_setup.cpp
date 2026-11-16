// vcam-setup.exe — installs / removes the "AI Virtual Camera" devices. Runs elevated (manifest), started by
// the app's Virtual Camera page (UAC prompt) or by the installer.
//
//   vcam-setup --install [--dshow-only | --mf-only | --both]   register the camera(s)
//   vcam-setup --uninstall                            remove both
//   vcam-setup --rename "<name>"                      rename the camera(s): re-registers what is installed
//   vcam-setup --reinstall                            remove both, then register the same set again (repair)
//   vcam-setup --status                               print what is registered and the camera name
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

#include "../vcam_name.h"

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "advapi32.lib")

using Microsoft::WRL::ComPtr;
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

static HRESULT open_vc(const std::wstring &name, IMFVirtualCamera **vc) {
	CreateVC create = create_vc();
	if (!create) return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
	return create(MFVirtualCameraType_SoftwareCameraSource, MFVirtualCameraLifetime_System,
	              MFVirtualCameraAccess_AllUsers, name.c_str(), kMfClsid, nullptr, 0, vc);
}

static HRESULT remove_vc(const std::wstring &name) {
	ComPtr<IMFVirtualCamera> vc;
	HRESULT hr = open_vc(name, &vc);
	if (SUCCEEDED(hr)) hr = vc->Remove();
	if (vc) vc->Shutdown();
	return hr;
}

static int install_mf() {
	if (!create_vc()) {
		printf("media foundation: MFCreateVirtualCamera not available (Windows 11 22000+ required) - skipped\n");
		return 0;
	}
	int rc = call_export(L"vcam_mf.dll", "DllRegisterServer");
	if (rc) return rc;
	ComPtr<IMFVirtualCamera> vc;
	HRESULT hr = open_vc(vcam::camera_name(), &vc);
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
	const std::wstring name = vcam::camera_name();
	HRESULT hr = remove_vc(name);
	if (FAILED(hr) && name != vcam::kDefaultName) hr = remove_vc(vcam::kDefaultName);   // created before a rename
	printf("media foundation: Remove: 0x%08lX\n", (unsigned long)hr);
	call_export(L"vcam_mf.dll", "DllUnregisterServer");
	return 0;
}

static bool dshow_installed();

// Rename: store the name, then re-register whatever is installed under it. The Media Foundation camera is
// removed under its old name and created again; the DirectShow filter is registered again (same CLSID).
static int rename_camera(const std::wstring &name) {
	if (!vcam::valid_name(name)) {
		fwprintf(stderr, L"invalid name: 1-%zu characters, no control characters\n", vcam::kNameMax);
		return 1;
	}
	const std::wstring old = vcam::camera_name();
	const bool mf = registered(kMfClsid), ds = dshow_installed();
	if (mf && create_vc()) {
		HRESULT hr = remove_vc(old);
		printf("media foundation: Remove \"%ls\": 0x%08lX\n", old.c_str(), (unsigned long)hr);
	}
	LONG r = vcam::set_camera_name(name);
	if (r != ERROR_SUCCESS) {
		fprintf(stderr, "cannot store the name (%ld)\n", r);
		if (mf) install_mf();                   // put the old camera back
		return 1;
	}
	printf("name: %ls\n", name.c_str());
	int rc = 0;
	if (ds) rc |= call_export(L"vcam_dshow.dll", "DllRegisterServer");
	if (mf) rc |= install_mf();
	if (!mf && !ds) printf("no camera installed: the name applies to the next --install\n");
	return rc;
}

static bool dshow_installed() { return registered(kDShowClsid); }

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
			// both devices: DirectShow ("<name>", any number of apps at once) + Media Foundation
			// ("<name> (Windows Virtual Camera)"); DirectShow alone when the MF camera cannot start -> 3
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
			if (rc == 0 && create_vc() && install_mf() != 0) rc = 3;
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
	} else if (cmd == L"--rename") {
		rc = rename_camera(opt);
	} else if (cmd == L"--reinstall") {
		// Repair: a camera that stopped working (moved files, a failed update, a Frame Server that kept a stale
		// registration) is removed completely and registered again, the same devices as before.
		const bool ds = registered(kDShowClsid), mf = registered(kMfClsid);
		printf("reinstall: directshow %s, media foundation %s\n", ds ? "yes" : "no", mf ? "yes" : "no");
		uninstall_mf();
		if (ds) call_export(L"vcam_dshow.dll", "DllUnregisterServer");
		if (ds && mf) {
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
			if (rc == 0 && install_mf() != 0) rc = 3;
		} else if (ds) {
			rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
		} else if (mf) {
			rc = install_mf();
			if (rc != 0) { rc = call_export(L"vcam_dshow.dll", "DllRegisterServer"); if (rc == 0) rc = 3; }
		} else {                                // nothing was registered: a normal install
			if (create_vc() && install_mf() == 0) {
				printf("installed: media foundation camera\n");
			} else {
				rc = call_export(L"vcam_dshow.dll", "DllRegisterServer");
				if (rc == 0 && create_vc()) rc = 3;
			}
		}
	} else if (cmd == L"--status") {
		printf("name: %ls\n", vcam::camera_name().c_str());
		printf("directshow: %s\n", registered(kDShowClsid) ? "registered" : "not registered");
		printf("media foundation: %s%s\n", registered(kMfClsid) ? "registered" : "not registered",
		       create_vc() ? "" : " (MFCreateVirtualCamera unavailable on this Windows)");
	} else {
		fwprintf(stderr, L"usage: vcam-setup --install [--dshow-only|--mf-only|--both] | --uninstall | --reinstall | "
		                 L"--rename <name> | --status\n");
		rc = 1;
	}
	MFShutdown();
	return rc;
}
