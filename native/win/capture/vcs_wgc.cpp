// vcs_wgc.dll — screen and window capture through Windows Graphics Capture (Windows 10 1903+), the API the OS
// itself uses for screen sharing: sees hardware-accelerated content (browsers, games in windowed / borderless
// mode) that GDI BitBlt shows black, and captures one window even when other windows cover it.
//
//   int   wgc_supported()
//   void *wgc_open_monitor(int index, int *w, int *h, wchar_t *err, int errcap)   index over EnumDisplayMonitors
//   void *wgc_open_window(HWND hwnd, int *w, int *h, wchar_t *err, int errcap)
//   int   wgc_read(h, uint8_t *dst, int dst_bytes, int timeout_ms, int *w, int *h)
//            1 frame (BGRA top-down, stride w*4) | 0 no new frame | -1 closed (window gone) | -2 buffer too small
//            -3 size changed (w / h hold the new size; call again with a buffer of that size)
//   void  wgc_close(h)
// The yellow capture border Windows 11 draws is switched off where the OS allows it (IsBorderRequired).
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <inspectable.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Metadata.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "windowsapp.lib")
#pragma comment(lib, "user32.lib")

namespace wgc = winrt::Windows::Graphics::Capture;
namespace wdx = winrt::Windows::Graphics::DirectX;
namespace d3d = winrt::Windows::Graphics::DirectX::Direct3D11;

struct Cap {
	winrt::com_ptr<ID3D11Device> dev;
	winrt::com_ptr<ID3D11DeviceContext> ctx;
	d3d::IDirect3DDevice rtdev{nullptr};
	wgc::GraphicsCaptureItem item{nullptr};
	wgc::Direct3D11CaptureFramePool pool{nullptr};
	wgc::GraphicsCaptureSession session{nullptr};
	winrt::event_token arrived{}, closed_tok{};
	HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
	std::atomic<bool> closed{false};
	winrt::com_ptr<ID3D11Texture2D> staging;
	int w = 0, h = 0;          // size of the frames we deliver (staging texture)
	~Cap() {
		try {
			if (pool) { pool.FrameArrived(arrived); pool.Close(); }
			if (session) session.Close();
			if (item) item.Closed(closed_tok);
		} catch (...) {
		}
		CloseHandle(event);
	}
};

static void set_err(wchar_t *err, int cap, const wchar_t *what, HRESULT hr) {
	if (err && cap > 0) swprintf(err, cap, L"%ls (0x%08lX)", what, (unsigned long)hr);
}

static bool make_device(Cap *c) {
	D3D_FEATURE_LEVEL fl;
	HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
	                               nullptr, 0, D3D11_SDK_VERSION, c->dev.put(), &fl, c->ctx.put());
	if (FAILED(hr))         // no hardware adapter (servers, remote sessions): software Direct3D
		hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
		                       D3D11_SDK_VERSION, c->dev.put(), &fl, c->ctx.put());
	if (FAILED(hr)) return false;
	auto dxgi = c->dev.as<IDXGIDevice>();
	winrt::com_ptr<::IInspectable> insp;
	if (FAILED(CreateDirect3D11DeviceFromDXGIDevice(dxgi.get(), insp.put()))) return false;
	c->rtdev = insp.as<d3d::IDirect3DDevice>();
	return true;
}

static bool make_staging(Cap *c, int w, int h) {
	D3D11_TEXTURE2D_DESC td{};
	td.Width = (UINT)w; td.Height = (UINT)h; td.MipLevels = 1; td.ArraySize = 1;
	td.Format = DXGI_FORMAT_B8G8R8A8_UNORM; td.SampleDesc.Count = 1;
	td.Usage = D3D11_USAGE_STAGING; td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
	c->staging = nullptr;
	if (FAILED(c->dev->CreateTexture2D(&td, nullptr, c->staging.put()))) return false;
	c->w = w; c->h = h;
	return true;
}

static void *start(Cap *c, int *out_w, int *out_h, wchar_t *err, int errcap) {
	try {
		auto size = c->item.Size();
		c->pool = wgc::Direct3D11CaptureFramePool::CreateFreeThreaded(
		    c->rtdev, wdx::DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
		HANDLE ev = c->event;
		c->arrived = c->pool.FrameArrived([ev](auto &&, auto &&) { SetEvent(ev); });
		Cap *self = c;
		c->closed_tok = c->item.Closed([self](auto &&, auto &&) { self->closed = true; SetEvent(self->event); });
		c->session = c->pool.CreateCaptureSession(c->item);
		using winrt::Windows::Foundation::Metadata::ApiInformation;
		if (ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession", L"IsCursorCaptureEnabled"))
			c->session.IsCursorCaptureEnabled(false);
		if (ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession", L"IsBorderRequired"))
			c->session.IsBorderRequired(false);
		if (!make_staging(c, size.Width, size.Height)) { set_err(err, errcap, L"staging texture", E_FAIL); delete c; return nullptr; }
		c->session.StartCapture();
		if (out_w) *out_w = c->w;
		if (out_h) *out_h = c->h;
		return c;
	} catch (winrt::hresult_error const &e) {
		set_err(err, errcap, L"capture session failed", e.code());
		delete c;
		return nullptr;
	}
}

extern "C" __declspec(dllexport) int wgc_supported() {
	try {
		winrt::init_apartment(winrt::apartment_type::multi_threaded);
	} catch (...) {
	}
	try {
		return wgc::GraphicsCaptureSession::IsSupported() ? 1 : 0;
	} catch (...) {
		return 0;
	}
}

struct MonEnum { int want, k; HMONITOR found; };
static BOOL CALLBACK mon_cb(HMONITOR m, HDC, LPRECT, LPARAM p) {
	auto *e = reinterpret_cast<MonEnum *>(p);
	if (e->k++ == e->want) { e->found = m; return FALSE; }
	return TRUE;
}

extern "C" __declspec(dllexport) void *wgc_open_monitor(int index, int *out_w, int *out_h, wchar_t *err, int errcap) {
	if (!wgc_supported()) { set_err(err, errcap, L"Windows Graphics Capture is not available", E_NOTIMPL); return nullptr; }
	MonEnum e{index, 0, nullptr};
	EnumDisplayMonitors(nullptr, nullptr, mon_cb, reinterpret_cast<LPARAM>(&e));
	if (!e.found) { set_err(err, errcap, L"no such monitor", E_INVALIDARG); return nullptr; }
	auto *c = new Cap();
	if (!make_device(c)) { set_err(err, errcap, L"Direct3D device", E_FAIL); delete c; return nullptr; }
	try {
		auto interop = winrt::get_activation_factory<wgc::GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
		HRESULT hr = interop->CreateForMonitor(e.found, winrt::guid_of<wgc::GraphicsCaptureItem>(), winrt::put_abi(c->item));
		if (FAILED(hr)) { set_err(err, errcap, L"CreateForMonitor failed", hr); delete c; return nullptr; }
	} catch (winrt::hresult_error const &ex) {
		set_err(err, errcap, L"CreateForMonitor failed", ex.code()); delete c; return nullptr;
	}
	return start(c, out_w, out_h, err, errcap);
}

extern "C" __declspec(dllexport) void *wgc_open_window(HWND hwnd, int *out_w, int *out_h, wchar_t *err, int errcap) {
	if (!wgc_supported()) { set_err(err, errcap, L"Windows Graphics Capture is not available", E_NOTIMPL); return nullptr; }
	if (!IsWindow(hwnd)) { set_err(err, errcap, L"no such window", E_INVALIDARG); return nullptr; }
	auto *c = new Cap();
	if (!make_device(c)) { set_err(err, errcap, L"Direct3D device", E_FAIL); delete c; return nullptr; }
	try {
		auto interop = winrt::get_activation_factory<wgc::GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
		HRESULT hr = interop->CreateForWindow(hwnd, winrt::guid_of<wgc::GraphicsCaptureItem>(), winrt::put_abi(c->item));
		if (FAILED(hr)) { set_err(err, errcap, L"CreateForWindow failed", hr); delete c; return nullptr; }
	} catch (winrt::hresult_error const &ex) {
		set_err(err, errcap, L"CreateForWindow failed", ex.code()); delete c; return nullptr;
	}
	return start(c, out_w, out_h, err, errcap);
}

extern "C" __declspec(dllexport) int wgc_read(void *h, uint8_t *dst, int dst_bytes, int timeout_ms, int *out_w, int *out_h) {
	auto *c = static_cast<Cap *>(h);
	if (!c || c->closed) return -1;
	try {
		auto frame = c->pool.TryGetNextFrame();
		if (!frame) {
			WaitForSingleObject(c->event, (DWORD)timeout_ms);
			if (c->closed) return -1;
			frame = c->pool.TryGetNextFrame();
			if (!frame) return 0;
		}
		auto cs = frame.ContentSize();
		if (cs.Width != c->w || cs.Height != c->h) {             // window resized / display mode changed
			c->pool.Recreate(c->rtdev, wdx::DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, cs);
			make_staging(c, cs.Width, cs.Height);
			if (out_w) *out_w = c->w;
			if (out_h) *out_h = c->h;
			return -3;
		}
		if ((size_t)dst_bytes < (size_t)c->w * c->h * 4) return -2;
		auto access = frame.Surface().as<Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
		winrt::com_ptr<ID3D11Texture2D> tex;
		if (FAILED(access->GetInterface(IID_PPV_ARGS(tex.put())))) return 0;
		D3D11_TEXTURE2D_DESC td;
		tex->GetDesc(&td);
		D3D11_BOX box{0, 0, 0, (UINT)std::min<int>(c->w, (int)td.Width), (UINT)std::min<int>(c->h, (int)td.Height), 1};
		c->ctx->CopySubresourceRegion(c->staging.get(), 0, 0, 0, 0, tex.get(), 0, &box);
		D3D11_MAPPED_SUBRESOURCE m;
		if (FAILED(c->ctx->Map(c->staging.get(), 0, D3D11_MAP_READ, 0, &m))) return 0;
		for (int y = 0; y < c->h; ++y)
			memcpy(dst + (size_t)y * c->w * 4, (uint8_t *)m.pData + (size_t)y * m.RowPitch, (size_t)c->w * 4);
		c->ctx->Unmap(c->staging.get(), 0);
		if (out_w) *out_w = c->w;
		if (out_h) *out_h = c->h;
		return 1;
	} catch (...) {
		return -1;
	}
}

extern "C" __declspec(dllexport) void wgc_close(void *h) {
	delete static_cast<Cap *>(h);
}
