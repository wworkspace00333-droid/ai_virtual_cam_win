// The user-chosen name of the camera devices ("AI Virtual Camera" by default).
// Stored in HKLM\SOFTWARE\AIVirtualCameraStudio\Camera, value "Name" (REG_SZ), written by vcam-setup --rename
// (elevated); read by vcam-setup when it registers the devices and by the camera DLLs (waiting card). Windows 11
// shows the Media Foundation camera as "<name> (Windows Virtual Camera)"; that suffix is added by Windows.
#pragma once
#include <windows.h>

#include <string>

#pragma comment(lib, "advapi32.lib")

namespace vcam {

inline constexpr wchar_t kDefaultName[] = L"AI Virtual Camera";
inline constexpr wchar_t kNameKey[] = L"SOFTWARE\\AIVirtualCameraStudio\\Camera";
inline constexpr size_t kNameMax = 63;

// 1..63 printable characters, no control characters, not only spaces
inline bool valid_name(const std::wstring &n) {
	if (n.empty() || n.size() > kNameMax) return false;
	bool visible = false;
	for (wchar_t c : n) {
		if (c < 0x20 || c == 0x7F) return false;
		if (c != L' ') visible = true;
	}
	return visible;
}

inline std::wstring camera_name() {
	wchar_t buf[kNameMax + 2] = {};
	DWORD size = sizeof(buf) - sizeof(wchar_t), type = 0;
	if (RegGetValueW(HKEY_LOCAL_MACHINE, kNameKey, L"Name", RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY, &type, buf,
	                 &size) == ERROR_SUCCESS) {
		std::wstring n(buf);
		if (valid_name(n)) return n;
	}
	return kDefaultName;
}

inline LONG set_camera_name(const std::wstring &n) {
	HKEY k;
	LONG r = RegCreateKeyExW(HKEY_LOCAL_MACHINE, kNameKey, 0, nullptr, 0, KEY_SET_VALUE | KEY_WOW64_64KEY, nullptr, &k,
	                         nullptr);
	if (r != ERROR_SUCCESS) return r;
	r = RegSetValueExW(k, L"Name", 0, REG_SZ, reinterpret_cast<const BYTE *>(n.c_str()),
	                   (DWORD)((n.size() + 1) * sizeof(wchar_t)));
	RegCloseKey(k);
	return r;
}

}  // namespace vcam
