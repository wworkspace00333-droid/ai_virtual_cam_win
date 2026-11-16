// vcs_dupl.dll — screen capture through DXGI Desktop Duplication (Windows 8+): what DWM composes, including
// hardware-accelerated windows (browsers, games in borderless / windowed mode) that GDI BitBlt shows black.
//
//   void *dupl_open(int monitor, int *w, int *h, wchar_t *name, int namecap, wchar_t *err, int errcap)
//   int   dupl_read(h, uint8_t *dst, int dst_bytes, int timeout_ms)   1 new frame | 0 no change | -1 lost (reopen)
//                                                                     -2 buffer too small
//   void  dupl_close(h)
//   int   dupl_monitors()                                             number of outputs on all adapters
// The frame is copied as BGRA, top-down, stride = width * 4. "Lost" happens on a mode change, a secure desktop
// (UAC, lock screen), a full-screen exclusive app or a session switch: the caller closes and opens again.
// One duplication per output per process. Not thread-safe per handle: one reading thread owns it.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <cstdio>
#include <cstring>
#include <vector>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")

using Microsoft::WRL::ComPtr;

struct Dupl {
	ComPtr<ID3D11Device> dev;
	ComPtr<ID3D11DeviceContext> ctx;
	ComPtr<IDXGIOutputDuplication> dup;
	ComPtr<ID3D11Texture2D> staging;
	int w = 0, h = 0;
	bool have_frame = false;
};

static void set_err(wchar_t *err, int cap, const wchar_t *what, HRESULT hr) {
	if (err && cap > 0) swprintf(err, cap, L"%ls (0x%08lX)", what, (unsigned long)hr);
}

// the n-th output over all adapters, and its adapter
static bool find_output(int n, ComPtr<IDXGIAdapter1> &adapter, ComPtr<IDXGIOutput1> &out1, DXGI_OUTPUT_DESC &desc) {
	ComPtr<IDXGIFactory1> f;
	if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&f)))) return false;
	int k = 0;
	ComPtr<IDXGIAdapter1> a;
	for (UINT ai = 0; f->EnumAdapters1(ai, &a) != DXGI_ERROR_NOT_FOUND; ++ai) {
		ComPtr<IDXGIOutput> o;
		for (UINT oi = 0; a->EnumOutputs(oi, &o) != DXGI_ERROR_NOT_FOUND; ++oi) {
			if (k++ == n) {
				if (FAILED(o.As(&out1))) return false;
				o->GetDesc(&desc);
				adapter = a;
				return true;
			}
			o.Reset();
		}
		a.Reset();
	}
	return false;
}

extern "C" __declspec(dllexport) int dupl_monitors() {
	ComPtr<IDXGIFactory1> f;
	if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&f)))) return 0;
	int k = 0;
	ComPtr<IDXGIAdapter1> a;
	for (UINT ai = 0; f->EnumAdapters1(ai, &a) != DXGI_ERROR_NOT_FOUND; ++ai) {
		ComPtr<IDXGIOutput> o;
		for (UINT oi = 0; a->EnumOutputs(oi, &o) != DXGI_ERROR_NOT_FOUND; ++oi) { ++k; o.Reset(); }
		a.Reset();
	}
	return k;
}

extern "C" __declspec(dllexport) void *dupl_open(int monitor, int *out_w, int *out_h, wchar_t *name, int namecap,
                                                 wchar_t *err, int errcap) {
	ComPtr<IDXGIAdapter1> adapter;
	ComPtr<IDXGIOutput1> out1;
	DXGI_OUTPUT_DESC desc{};
	if (!find_output(monitor, adapter, out1, desc)) {
		set_err(err, errcap, L"no such monitor", DXGI_ERROR_NOT_FOUND);
		return nullptr;
	}
	auto *d = new Dupl();
	D3D_FEATURE_LEVEL fl;
	HRESULT hr = D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
	                               &d->dev, &fl, &d->ctx);
	if (FAILED(hr)) { set_err(err, errcap, L"D3D11CreateDevice failed", hr); delete d; return nullptr; }
	hr = out1->DuplicateOutput(d->dev.Get(), &d->dup);
	if (FAILED(hr)) {
		const wchar_t *why = hr == E_ACCESSDENIED ? L"access denied (secure desktop, or not the active session)"
		                   : hr == DXGI_ERROR_UNSUPPORTED ? L"desktop duplication not supported on this display"
		                   : hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE ? L"too many duplications already"
		                   : L"DuplicateOutput failed";
		set_err(err, errcap, why, hr);
		delete d;
		return nullptr;
	}
	DXGI_OUTDUPL_DESC dd;
	d->dup->GetDesc(&dd);
	d->w = (int)dd.ModeDesc.Width;
	d->h = (int)dd.ModeDesc.Height;
	D3D11_TEXTURE2D_DESC td{};
	td.Width = d->w; td.Height = d->h; td.MipLevels = 1; td.ArraySize = 1;
	td.Format = DXGI_FORMAT_B8G8R8A8_UNORM; td.SampleDesc.Count = 1;
	td.Usage = D3D11_USAGE_STAGING; td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
	hr = d->dev->CreateTexture2D(&td, nullptr, &d->staging);
	if (FAILED(hr)) { set_err(err, errcap, L"staging texture", hr); delete d; return nullptr; }
	if (out_w) *out_w = d->w;
	if (out_h) *out_h = d->h;
	if (name && namecap > 0) swprintf(name, namecap, L"%ls", desc.DeviceName);
	return d;
}

extern "C" __declspec(dllexport) int dupl_read(void *h, uint8_t *dst, int dst_bytes, int timeout_ms) {
	auto *d = static_cast<Dupl *>(h);
	if (!d || !d->dup) return -1;
	const size_t need = (size_t)d->w * d->h * 4;
	if ((size_t)dst_bytes < need) return -2;
	DXGI_OUTDUPL_FRAME_INFO fi;
	ComPtr<IDXGIResource> res;
	HRESULT hr = d->dup->AcquireNextFrame((UINT)timeout_ms, &fi, &res);
	if (hr == DXGI_ERROR_WAIT_TIMEOUT) return 0;
	if (FAILED(hr)) return -1;                                  // DXGI_ERROR_ACCESS_LOST and friends
	int rc = 0;
	// a new image, not only a mouse move. The first frame after DuplicateOutput can come with LastPresentTime 0 and an
	// empty (black) surface (seen on Windows Server 2025 over RDP), so it is not taken as a picture either.
	if (fi.LastPresentTime.QuadPart != 0) {
		ComPtr<ID3D11Texture2D> tex;
		if (SUCCEEDED(res.As(&tex))) {
			d->ctx->CopyResource(d->staging.Get(), tex.Get());
			D3D11_MAPPED_SUBRESOURCE m;
			if (SUCCEEDED(d->ctx->Map(d->staging.Get(), 0, D3D11_MAP_READ, 0, &m))) {
				for (int y = 0; y < d->h; ++y)
					memcpy(dst + (size_t)y * d->w * 4, (uint8_t *)m.pData + (size_t)y * m.RowPitch, (size_t)d->w * 4);
				d->ctx->Unmap(d->staging.Get(), 0);
				d->have_frame = true;
				rc = 1;
			}
		}
	}
	d->dup->ReleaseFrame();
	return rc;
}

extern "C" __declspec(dllexport) void dupl_close(void *h) {
	delete static_cast<Dupl *>(h);
}
