// mf_client_test.exe — reads frames from the Media Foundation camera like a client app would.
//   mf_client_test --inproc <path\vcam_mf.dll> [frames]   activate the media source in this process
//   mf_client_test --device "AI Virtual Camera" [frames]   open the camera from MFEnumDeviceSources (Frame Server)
//   mf_client_test --list                                 list Media Foundation video capture devices
// Prints the negotiated format, frame count, rate and the centre pixel of the last frame. Exit 0 = frames read.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <wrl/client.h>

#include <chrono>
#include <cstdio>
#include <string>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "mfreadwrite.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "ole32.lib")

using Microsoft::WRL::ComPtr;
static const GUID CLSID_AIVirtualCameraMF = {0x20c0f518, 0x9f28, 0x4168, {0x9f, 0x73, 0x03, 0x0f, 0x24, 0x3f, 0xb3, 0xb9}};

static HRESULT inproc_source(const wchar_t *dll, IMFMediaSource **out) {
	HMODULE m = LoadLibraryW(dll);
	if (!m) return HRESULT_FROM_WIN32(GetLastError());
	typedef HRESULT(STDAPICALLTYPE * GetCO)(REFCLSID, REFIID, void **);
	GetCO get = (GetCO)GetProcAddress(m, "DllGetClassObject");
	ComPtr<IClassFactory> f;
	HRESULT hr = get(CLSID_AIVirtualCameraMF, IID_PPV_ARGS(&f));
	if (FAILED(hr)) return hr;
	ComPtr<IMFActivate> a;
	hr = f->CreateInstance(nullptr, IID_PPV_ARGS(&a));
	if (FAILED(hr)) return hr;
	return a->ActivateObject(IID_PPV_ARGS(out));
}

static HRESULT device_source(const wchar_t *name, IMFMediaSource **out, bool list) {
	ComPtr<IMFAttributes> at;
	MFCreateAttributes(&at, 1);
	at->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID);
	IMFActivate **devs = nullptr;
	UINT32 n = 0;
	HRESULT hr = MFEnumDeviceSources(at.Get(), &devs, &n);
	if (FAILED(hr)) return hr;
	hr = MF_E_NOT_FOUND;
	for (UINT32 i = 0; i < n; ++i) {
		wchar_t *fn = nullptr, *link = nullptr;
		UINT32 len;
		devs[i]->GetAllocatedString(MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME, &fn, &len);
		devs[i]->GetAllocatedString(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK, &link, &len);
		if (list) wprintf(L"device %u: %ls  [%ls]\n", i, fn ? fn : L"?", link ? link : L"");
		if (!list && fn && name && wcscmp(fn, name) == 0 && hr == MF_E_NOT_FOUND)
			hr = devs[i]->ActivateObject(IID_PPV_ARGS(out));
		CoTaskMemFree(fn);
		CoTaskMemFree(link);
		devs[i]->Release();
	}
	CoTaskMemFree(devs);
	if (list) printf("%u device(s)\n", n);
	return list ? S_OK : hr;
}

int wmain(int argc, wchar_t **argv) {
	if (argc < 2) { fwprintf(stderr, L"usage: --inproc <dll> [n] | --device <name> [n] | --list\n"); return 2; }
	CoInitializeEx(nullptr, COINIT_MULTITHREADED);
	MFStartup(MF_VERSION);
	std::wstring mode = argv[1];
	ComPtr<IMFMediaSource> src;
	HRESULT hr;
	if (mode == L"--list") return FAILED(device_source(nullptr, nullptr, true)) ? 1 : 0;
	if (argc < 3) return 2;
	hr = mode == L"--inproc" ? inproc_source(argv[2], &src) : device_source(argv[2], &src, false);
	if (FAILED(hr)) { printf("open failed: 0x%08lX\n", (unsigned long)hr); return 1; }
	int want = argc > 3 ? _wtoi(argv[3]) : 90;
	ComPtr<IMFAttributes> ra;
	MFCreateAttributes(&ra, 1);
	ra->SetUINT32(MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, TRUE);
	ComPtr<IMFSourceReader> reader;
	hr = MFCreateSourceReaderFromMediaSource(src.Get(), ra.Get(), &reader);
	if (FAILED(hr)) { printf("reader failed: 0x%08lX\n", (unsigned long)hr); return 1; }
	// ask for RGB32 so the centre pixel can be compared with what the app sent
	ComPtr<IMFMediaType> rgb;
	MFCreateMediaType(&rgb);
	rgb->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
	rgb->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
	hr = reader->SetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, rgb.Get());
	if (FAILED(hr)) printf("note: RGB32 output not set (0x%08lX), reading native type\n", (unsigned long)hr);
	ComPtr<IMFMediaType> cur;
	reader->GetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, &cur);
	UINT32 w = 0, h = 0, num = 0, den = 1;
	MFGetAttributeSize(cur.Get(), MF_MT_FRAME_SIZE, &w, &h);
	MFGetAttributeRatio(cur.Get(), MF_MT_FRAME_RATE, &num, &den);
	GUID sub = GUID_NULL;
	cur->GetGUID(MF_MT_SUBTYPE, &sub);
	printf("format %ux%u @ %.2f fps, subtype %08lX\n", w, h, den ? (double)num / den : 0.0, sub.Data1);
	int got = 0;
	unsigned centre = 0;
	auto t0 = std::chrono::steady_clock::now();
	for (int i = 0; i < want * 3 && got < want; ++i) {
		DWORD flags = 0, stream = 0;
		LONGLONG ts = 0;
		ComPtr<IMFSample> s;
		hr = reader->ReadSample((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &stream, &flags, &ts, &s);
		if (FAILED(hr)) { printf("ReadSample failed: 0x%08lX\n", (unsigned long)hr); break; }
		if (!s) continue;
		++got;
		ComPtr<IMFMediaBuffer> b;
		if (SUCCEEDED(s->ConvertToContiguousBuffer(&b))) {
			BYTE *p; DWORD len;
			if (SUCCEEDED(b->Lock(&p, nullptr, &len))) {
				size_t off = ((size_t)(h / 2) * w + w / 2) * 4;
				if (sub == MFVideoFormat_RGB32 && off + 4 <= len) centre = *(unsigned *)(p + off);
				b->Unlock();
			}
		}
	}
	double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	printf("frames %d in %.2f s (%.1f fps), centre BGRA %02X %02X %02X\n", got, secs, got / secs, centre & 255,
	       (centre >> 8) & 255, (centre >> 16) & 255);
	src->Shutdown();
	MFShutdown();
	return got > 0 ? 0 : 1;
}
