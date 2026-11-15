// vcam-setup.exe — installs / removes the "AI Virtual Camera" devices. Runs elevated (manifest), started by
// the app's Virtual Camera page (UAC prompt) or by the installer.
//
//   vcam-setup --install     register vcam_dshow.dll (DirectShow camera, Windows 10/11)
//   vcam-setup --uninstall   unregister it
//   vcam-setup --status      print what is registered
// Exit codes (read by vcstudio/camera/camera_manager.py): 0 ok, 1 error, 4 component file missing.
// The DLLs are expected next to this executable.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <string>

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

int wmain(int argc, wchar_t **argv) {
	std::wstring cmd = argc > 1 ? argv[1] : L"--status";
	const wchar_t *dshow = L"vcam_dshow.dll";
	if (cmd == L"--install") return call_export(dshow, "DllRegisterServer");
	if (cmd == L"--uninstall") return call_export(dshow, "DllUnregisterServer");
	if (cmd == L"--status") {
		printf("directshow: %s\n", registered(L"{59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE}") ? "registered" : "not registered");
		return 0;
	}
	fwprintf(stderr, L"usage: vcam-setup --install | --uninstall | --status\n");
	return 1;
}
