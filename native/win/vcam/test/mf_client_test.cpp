// mf_client_test.exe — reads frames from the Media Foundation camera like a client app would.
//   mf_client_test --inproc <path\vcam_mf.dll> [frames [native]]   activate the media source in this process
//   mf_client_test --device "AI Virtual Camera" [frames [native]]   open the camera from MFEnumDeviceSources (Frame Server)
//     native: index of the camera's own format to use (the list is printed); default = the reader's choice
//   mf_client_test --shared "AI Virtual Camera" [frames [native]]   the same in shared mode (sensor group, several readers)
//   mf_client_test --list                                list Media Foundation video capture devices
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
#pragma comment(lib, "mfsensorgroup.lib")

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

// Shared mode: open the device through a sensor group with MFSensorDeviceMode_Shared, the way apps that support
// several readers at once do (Windows Camera, Teams); a second reader in exclusive mode gets "device in use".
static HRESULT shared_source(const wchar_t *link, IMFMediaSource **out) {
	ComPtr<IMFSensorGroup> group;
	HRESULT hr = MFCreateSensorGroup(link, &group);
	if (FAILED(hr)) { printf("MFCreateSensorGroup failed: 0x%08lX\n", (unsigned long)hr); return hr; }
	DWORD count = 0;
	group->GetSensorDeviceCount(&count);
	for (DWORD i = 0; i < count; ++i) {
		ComPtr<IMFSensorDevice> dev;
		if (FAILED(group->GetSensorDevice(i, &dev))) continue;
		hr = dev->SetSensorDeviceMode(MFSensorDeviceMode_Shared);
		if (FAILED(hr)) { printf("SetSensorDeviceMode(Shared) failed: 0x%08lX\n", (unsigned long)hr); return hr; }
	}
	printf("sensor group: %lu device(s), shared mode\n", count);
	return group->CreateMediaSource(out);
}

static HRESULT device_source(const wchar_t *name, IMFMediaSource **out, bool list, bool shared = false) {
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
			hr = shared ? shared_source(link, out) : devs[i]->ActivateObject(IID_PPV_ARGS(out));
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
	hr = mode == L"--inproc" ? inproc_source(argv[2], &src) : device_source(argv[2], &src, false, mode == L"--shared");
	if (FAILED(hr)) { printf("open failed: 0x%08lX\n", (unsigned long)hr); return 1; }
	int want = argc > 3 ? _wtoi(argv[3]) : 90;
	ComPtr<IMFAttributes> ra;
	MFCreateAttributes(&ra, 1);
	// the advanced processor honours MF_MT_YUV_MATRIX / MF_MT_VIDEO_NOMINAL_RANGE (the basic one assumes BT.709 for HD)
	ra->SetUINT32(MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, TRUE);
	ComPtr<IMFSourceReader> reader;
	hr = MFCreateSourceReaderFromMediaSource(src.Get(), ra.Get(), &reader);
	if (FAILED(hr)) { printf("reader failed: 0x%08lX\n", (unsigned long)hr); return 1; }
	ComPtr<IMFMediaType> rgb;
	MFCreateMediaType(&rgb);
	if (argc > 4) {   // pick one of the camera's own formats (listed), then let the reader convert it to RGB32
		int pick = _wtoi(argv[4]);
		for (DWORD i = 0;; ++i) {
			ComPtr<IMFMediaType> nt;
			if (FAILED(reader->GetNativeMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, i, &nt))) break;
			UINT32 nw = 0, nh = 0, nn = 0, nd = 1;
			GUID ns = GUID_NULL;
			MFGetAttributeSize(nt.Get(), MF_MT_FRAME_SIZE, &nw, &nh);
			MFGetAttributeRatio(nt.Get(), MF_MT_FRAME_RATE, &nn, &nd);
			nt->GetGUID(MF_MT_SUBTYPE, &ns);
			UINT32 mat = MFGetAttributeUINT32(nt.Get(), MF_MT_YUV_MATRIX, 0);
			UINT32 rng = MFGetAttributeUINT32(nt.Get(), MF_MT_VIDEO_NOMINAL_RANGE, 0);
			printf("native %lu: %ux%u @ %.2f fps, subtype %08lX, matrix %u, range %u%s\n", i, nw, nh,
			       nd ? (double)nn / nd : 0.0, ns.Data1, mat, rng, (int)i == pick ? "  <- selected" : "");
			if ((int)i == pick) {
				hr = reader->SetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, nt.Get());
				if (FAILED(hr)) { printf("native type %d not set: 0x%08lX\n", pick, (unsigned long)hr); return 1; }
				nt->CopyAllItems(rgb.Get());   // same size and rate; only the subtype changes below
			}
		}
	}
	// ask for RGB32 so the centre pixel can be compared with what the app sent; "raw" keeps the camera's own
	// NV12 and decodes the centre here with BT.601 limited range (what the type declares)
	bool raw = argc > 5 && std::wstring(argv[5]) == L"raw";
	if (!raw) {
		rgb->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
		rgb->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
		rgb->DeleteItem(MF_MT_YUV_MATRIX);                       // full-range RGB out (not "studio" 16-235)
		rgb->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_0_255);
		rgb->DeleteItem(MF_MT_DEFAULT_STRIDE);
		rgb->DeleteItem(MF_MT_SAMPLE_SIZE);
		hr = reader->SetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, rgb.Get());
		if (FAILED(hr)) printf("note: RGB32 output not set (0x%08lX), reading native type\n", (unsigned long)hr);
	}
	ComPtr<IMFMediaType> cur;
	reader->GetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, &cur);
	UINT32 w = 0, h = 0, num = 0, den = 1;
	MFGetAttributeSize(cur.Get(), MF_MT_FRAME_SIZE, &w, &h);
	MFGetAttributeRatio(cur.Get(), MF_MT_FRAME_RATE, &num, &den);
	GUID sub = GUID_NULL;
	cur->GetGUID(MF_MT_SUBTYPE, &sub);
	UINT32 matrix = MFGetAttributeUINT32(cur.Get(), MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT601);
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
				size_t yo = (size_t)(h / 2) * w + w / 2, uvo = (size_t)w * h + (size_t)(h / 4) * w + (w / 2 & ~1u);
				if (sub == MFVideoFormat_NV12 && uvo + 1 < len) {        // limited range, matrix the type declares
					double c = 1.164 * (p[yo] - 16), d = p[uvo] - 128, e = p[uvo + 1] - 128;
					auto cl = [](double v) { return (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v + 0.5); };
					if (matrix == MFVideoTransferMatrix_BT709)
						centre = cl(c + 2.112 * d) | cl(c - 0.213 * d - 0.533 * e) << 8 | cl(c + 1.793 * e) << 16;
					else
						centre = cl(c + 2.017 * d) | cl(c - 0.392 * d - 0.813 * e) << 8 | cl(c + 1.596 * e) << 16;
				}
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
