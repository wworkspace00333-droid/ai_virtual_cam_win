// vcam_dshow.dll — "AI Virtual Camera" as a DirectShow video capture source (Windows 10 and 11).
//
// Client apps (Zoom, Discord, OBS, browsers using DirectShow) load this filter in-process, find it in the
// "Video Capture Sources" category and connect its single capture pin. Frames come from AI Virtual Camera
// Studio through shared memory (../vcam_shm.h); without the app the camera shows a "waiting" card, so the
// device never disappears from a client while the app restarts.
//
// Built on Microsoft's DirectShow BaseClasses (third_party/baseclasses, MIT). Registration:
// regsvr32 vcam_dshow.dll (administrator), done by vcam-setup.exe / the installer.
#include <streams.h>
#include <initguid.h>
#include <dvdmedia.h>
#include <ks.h>
#include <ksmedia.h>

#include <memory>
#include <vector>

#include "../vcam_frames.h"

// {59E4F394-D9A7-48BF-8D06-8DB7D8CF1ECE} — keep in sync with vcstudio/platform/win/vcam_status.py
DEFINE_GUID(CLSID_AIVirtualCameraDShow, 0x59e4f394, 0xd9a7, 0x48bf, 0x8d, 0x06, 0x8d, 0xb7, 0xd8, 0xcf, 0x1e, 0xce);
DEFINE_GUID(MEDIASUBTYPE_NV12_, 0x3231564E, 0x0000, 0x0010, 0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71);

static const wchar_t kName[] = L"AI Virtual Camera";

struct Mode {
	int w, h, fps;
	vcam::PixFmt fmt;
};
// Order matters: many clients take the first entry. YUY2 is the most widely accepted DirectShow format.
static const Mode kModes[] = {
    {1920, 1080, 30, vcam::PixFmt::YUY2}, {1920, 1080, 60, vcam::PixFmt::YUY2}, {1280, 720, 30, vcam::PixFmt::YUY2},
    {1280, 720, 60, vcam::PixFmt::YUY2},  {1920, 1080, 30, vcam::PixFmt::NV12}, {1920, 1080, 60, vcam::PixFmt::NV12},
    {1280, 720, 30, vcam::PixFmt::NV12},  {1280, 720, 60, vcam::PixFmt::NV12},  {1920, 1080, 30, vcam::PixFmt::RGB32_BOTTOMUP},
    {1280, 720, 30, vcam::PixFmt::RGB32_BOTTOMUP},
    // RGB24: older apps and OpenCV (which asks for RGB24 first and mis-reads RGB32 sources)
    {1920, 1080, 30, vcam::PixFmt::RGB24_BOTTOMUP}, {1920, 1080, 60, vcam::PixFmt::RGB24_BOTTOMUP},
    {1280, 720, 30, vcam::PixFmt::RGB24_BOTTOMUP},  {1280, 720, 60, vcam::PixFmt::RGB24_BOTTOMUP},
};
static const int kModeCount = sizeof(kModes) / sizeof(kModes[0]);

static void fill_media_type(const Mode &m, CMediaType *mt) {
	VIDEOINFOHEADER *vih = (VIDEOINFOHEADER *)mt->AllocFormatBuffer(sizeof(VIDEOINFOHEADER));
	ZeroMemory(vih, sizeof(VIDEOINFOHEADER));
	BITMAPINFOHEADER &b = vih->bmiHeader;
	b.biSize = sizeof(BITMAPINFOHEADER);
	b.biWidth = m.w;
	b.biHeight = m.h;
	b.biPlanes = 1;
	switch (m.fmt) {
	case vcam::PixFmt::YUY2: b.biBitCount = 16; b.biCompression = MAKEFOURCC('Y', 'U', 'Y', '2'); break;
	case vcam::PixFmt::NV12: b.biBitCount = 12; b.biCompression = MAKEFOURCC('N', 'V', '1', '2'); break;
	case vcam::PixFmt::RGB24_BOTTOMUP: b.biBitCount = 24; b.biCompression = BI_RGB; break;
	default: b.biBitCount = 32; b.biCompression = BI_RGB; break;
	}
	b.biSizeImage = (DWORD)vcam::frame_bytes(m.fmt, m.w, m.h);
	vih->AvgTimePerFrame = UNITS / m.fps;
	vih->dwBitRate = (DWORD)(b.biSizeImage * 8ull * m.fps);
	SetRectEmpty(&vih->rcSource);
	SetRectEmpty(&vih->rcTarget);
	mt->SetType(&MEDIATYPE_Video);
	mt->SetFormatType(&FORMAT_VideoInfo);
	mt->SetTemporalCompression(FALSE);
	mt->SetSampleSize(b.biSizeImage);
	const GUID sub = m.fmt == vcam::PixFmt::YUY2 ? MEDIASUBTYPE_YUY2
	                 : m.fmt == vcam::PixFmt::NV12 ? MEDIASUBTYPE_NV12_
	                 : m.fmt == vcam::PixFmt::RGB24_BOTTOMUP ? MEDIASUBTYPE_RGB24 : MEDIASUBTYPE_RGB32;
	mt->SetSubtype(&sub);
}

static bool mode_of(const CMediaType *mt, Mode &out) {
	if (*mt->Type() != MEDIATYPE_Video || *mt->FormatType() != FORMAT_VideoInfo || mt->FormatLength() < sizeof(VIDEOINFOHEADER))
		return false;
	const VIDEOINFOHEADER *vih = (const VIDEOINFOHEADER *)mt->Format();
	vcam::PixFmt f;
	if (*mt->Subtype() == MEDIASUBTYPE_YUY2) f = vcam::PixFmt::YUY2;
	else if (*mt->Subtype() == MEDIASUBTYPE_NV12_) f = vcam::PixFmt::NV12;
	else if (*mt->Subtype() == MEDIASUBTYPE_RGB32) f = vcam::PixFmt::RGB32_BOTTOMUP;
	else if (*mt->Subtype() == MEDIASUBTYPE_RGB24) f = vcam::PixFmt::RGB24_BOTTOMUP;
	else return false;
	int w = vih->bmiHeader.biWidth, h = abs(vih->bmiHeader.biHeight);
	int fps = vih->AvgTimePerFrame > 0 ? (int)((UNITS + vih->AvgTimePerFrame / 2) / vih->AvgTimePerFrame) : 30;
	for (const Mode &m : kModes)
		if (m.w == w && m.h == h && m.fmt == f && (m.fps == fps || fps <= 0)) { out = m; return true; }
	for (const Mode &m : kModes)                       // any listed size/format, client's own frame rate (5..60)
		if (m.w == w && m.h == h && m.fmt == f && fps >= 5 && fps <= 60) { out = m; out.fps = fps; return true; }
	return false;
}

class CVCamStream : public CSourceStream, public IAMStreamConfig, public IKsPropertySet {
public:
	CVCamStream(HRESULT *phr, CSource *parent) : CSourceStream(NAME("AI Virtual Camera"), phr, parent, L"Capture") {
		mode_ = kModes[0];
		fill_media_type(mode_, &m_mt);
	}

	DECLARE_IUNKNOWN
	STDMETHODIMP NonDelegatingQueryInterface(REFIID riid, void **ppv) override {
		if (riid == IID_IAMStreamConfig) return GetInterface((IAMStreamConfig *)this, ppv);
		if (riid == IID_IKsPropertySet) return GetInterface((IKsPropertySet *)this, ppv);
		return CSourceStream::NonDelegatingQueryInterface(riid, ppv);
	}

	// ---- CSourceStream
	HRESULT GetMediaType(int pos, CMediaType *mt) override {
		if (pos < 0) return E_INVALIDARG;
		if (format_set_) {                                // after IAMStreamConfig::SetFormat: offer only that format
			if (pos > 0) return VFW_S_NO_MORE_ITEMS;
			*mt = m_mt;
			return S_OK;
		}
		if (pos >= kModeCount) return VFW_S_NO_MORE_ITEMS;
		fill_media_type(kModes[pos], mt);
		return S_OK;
	}
	HRESULT CheckMediaType(const CMediaType *mt) override {
		Mode m;
		return mode_of(mt, m) ? S_OK : E_INVALIDARG;
	}
	HRESULT SetMediaType(const CMediaType *mt) override {
		Mode m;
		if (!mode_of(mt, m)) return E_INVALIDARG;
		mode_ = m;
		return CSourceStream::SetMediaType(mt);
	}
	HRESULT DecideBufferSize(IMemAllocator *alloc, ALLOCATOR_PROPERTIES *req) override {
		CAutoLock lock(m_pFilter->pStateLock());
		req->cBuffers = std::max<long>(req->cBuffers, 2);
		req->cbBuffer = (long)vcam::frame_bytes(mode_.fmt, mode_.w, mode_.h);
		if (req->cbAlign <= 0) req->cbAlign = 1;
		ALLOCATOR_PROPERTIES actual;
		HRESULT hr = alloc->SetProperties(req, &actual);
		if (FAILED(hr)) return hr;
		return actual.cbBuffer < req->cbBuffer ? E_FAIL : S_OK;
	}
	HRESULT OnThreadCreate() override {
		frame_ = 0;
		start_ns_ = vcam::now_ns();
		producer_.set_format(mode_.w, mode_.h, mode_.fmt);
		return S_OK;
	}
	HRESULT OnThreadDestroy() override {
		reader_.close();
		return S_OK;
	}
	HRESULT FillBuffer(IMediaSample *s) override {
		const REFERENCE_TIME period = UNITS / mode_.fps;
		// pace on the QPC clock: sample n is due at start + n * period
		uint64_t due = start_ns_ + (uint64_t)frame_ * (uint64_t)period * 100ull;
		uint64_t now = vcam::now_ns();
		if (due > now) Sleep((DWORD)((due - now) / 1000000ull));
		else if (now - due > 4ull * period * 100ull) {           // fell behind (client stalled): resync
			frame_ = (uint64_t)((now - start_ns_) / (period * 100ull));
		}
		BYTE *data = nullptr;
		HRESULT hr = s->GetPointer(&data);
		if (FAILED(hr)) return hr;
		long size = s->GetSize();
		size_t need = vcam::frame_bytes(mode_.fmt, mode_.w, mode_.h);
		if ((size_t)size < need) return E_FAIL;
		int stride = mode_.fmt == vcam::PixFmt::YUY2 ? mode_.w * 2
		             : mode_.fmt == vcam::PixFmt::NV12 ? mode_.w
		             : mode_.fmt == vcam::PixFmt::RGB24_BOTTOMUP ? ((mode_.w * 3 + 3) & ~3) : mode_.w * 4;
		producer_.produce(reader_, false, data, stride);
		REFERENCE_TIME t0 = (REFERENCE_TIME)frame_ * period, t1 = t0 + period;
		s->SetTime(&t0, &t1);
		s->SetActualDataLength((long)need);
		s->SetSyncPoint(TRUE);
		s->SetDiscontinuity(frame_ == 0);
		++frame_;
		return S_OK;
	}
	STDMETHODIMP Notify(IBaseFilter *, Quality) override { return E_NOTIMPL; }

	// ---- IAMStreamConfig
	STDMETHODIMP SetFormat(AM_MEDIA_TYPE *pmt) override {
		if (!pmt) return E_POINTER;
		CMediaType mt(*pmt);
		Mode m;
		if (!mode_of(&mt, m)) return VFW_E_INVALIDMEDIATYPE;
		CAutoLock lock(m_pFilter->pStateLock());
		if (m_pFilter->IsActive()) return VFW_E_WRONG_STATE;
		mode_ = m;
		m_mt = mt;
		format_set_ = true;
		IPin *peer = nullptr;
		if (SUCCEEDED(ConnectedTo(&peer)) && peer) {            // reconnect with the new format
			peer->Release();
			m_pFilter->GetFilterGraph()->Reconnect(this);
		}
		return S_OK;
	}
	STDMETHODIMP GetFormat(AM_MEDIA_TYPE **ppmt) override {
		if (!ppmt) return E_POINTER;
		*ppmt = CreateMediaType(&m_mt);
		return *ppmt ? S_OK : E_OUTOFMEMORY;
	}
	STDMETHODIMP GetNumberOfCapabilities(int *count, int *size) override {
		if (!count || !size) return E_POINTER;
		*count = kModeCount;
		*size = sizeof(VIDEO_STREAM_CONFIG_CAPS);
		return S_OK;
	}
	STDMETHODIMP GetStreamCaps(int i, AM_MEDIA_TYPE **ppmt, BYTE *caps) override {
		if (i < 0 || i >= kModeCount) return S_FALSE;
		if (!ppmt || !caps) return E_POINTER;
		CMediaType mt;
		fill_media_type(kModes[i], &mt);
		*ppmt = CreateMediaType(&mt);
		if (!*ppmt) return E_OUTOFMEMORY;
		const Mode &m = kModes[i];
		VIDEO_STREAM_CONFIG_CAPS *c = (VIDEO_STREAM_CONFIG_CAPS *)caps;
		ZeroMemory(c, sizeof(*c));
		c->guid = FORMAT_VideoInfo;
		c->InputSize = c->MinCroppingSize = c->MaxCroppingSize = c->MinOutputSize = c->MaxOutputSize = {m.w, m.h};
		c->MinFrameInterval = UNITS / 60;
		c->MaxFrameInterval = UNITS / 5;
		long bits = (long)(vcam::frame_bytes(m.fmt, m.w, m.h) * 8);
		c->MinBitsPerSecond = bits * 5;
		c->MaxBitsPerSecond = bits * 60;
		return S_OK;
	}

	// ---- IKsPropertySet: identifies this as a capture pin (many apps require PIN_CATEGORY_CAPTURE)
	STDMETHODIMP Set(REFGUID, DWORD, LPVOID, DWORD, LPVOID, DWORD) override { return E_NOTIMPL; }
	STDMETHODIMP Get(REFGUID set, DWORD id, LPVOID, DWORD, LPVOID data, DWORD size, DWORD *ret) override {
		if (set != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
		if (id != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
		if (!data && !ret) return E_POINTER;
		if (ret) *ret = sizeof(GUID);
		if (!data) return S_OK;
		if (size < sizeof(GUID)) return E_UNEXPECTED;
		*(GUID *)data = PIN_CATEGORY_CAPTURE;
		return S_OK;
	}
	STDMETHODIMP QuerySupported(REFGUID set, DWORD id, DWORD *support) override {
		if (set != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
		if (id != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
		if (support) *support = KSPROPERTY_SUPPORT_GET;
		return S_OK;
	}

private:
	Mode mode_;
	bool format_set_ = false;
	uint64_t frame_ = 0, start_ns_ = 0;
	vcam::FrameReader reader_;
	vcam::FrameProducer producer_;
};

class CVCam : public CSource {
public:
	static CUnknown *WINAPI CreateInstance(LPUNKNOWN outer, HRESULT *phr) {
		CVCam *f = new CVCam(outer, phr);
		if (!f && phr) *phr = E_OUTOFMEMORY;
		return f;
	}
	CVCam(LPUNKNOWN outer, HRESULT *phr) : CSource(NAME("AI Virtual Camera"), outer, CLSID_AIVirtualCameraDShow) {
		new CVCamStream(phr, this);                      // registers itself with the filter (m_paStreams)
	}
	STDMETHODIMP NonDelegatingQueryInterface(REFIID riid, void **ppv) override {
		if (riid == IID_IAMStreamConfig || riid == IID_IKsPropertySet)    // some apps ask the filter
			return m_paStreams[0]->QueryInterface(riid, ppv);
		return CSource::NonDelegatingQueryInterface(riid, ppv);
	}
};

// ------------------------------------------------------------------------------- registration
static const AMOVIESETUP_MEDIATYPE kPinTypes = {&MEDIATYPE_Video, &MEDIASUBTYPE_NULL};
static const AMOVIESETUP_PIN kPin = {const_cast<LPWSTR>(L"Capture"), FALSE, TRUE, FALSE, FALSE, &CLSID_NULL, nullptr, 1, &kPinTypes};
static const AMOVIESETUP_FILTER kFilter = {&CLSID_AIVirtualCameraDShow, kName, MERIT_DO_NOT_USE, 1, &kPin};

CFactoryTemplate g_Templates[] = {{kName, &CLSID_AIVirtualCameraDShow, CVCam::CreateInstance, nullptr, &kFilter}};
int g_cTemplates = sizeof(g_Templates) / sizeof(g_Templates[0]);

static HRESULT register_category(bool reg) {
	IFilterMapper2 *fm = nullptr;
	HRESULT hr = CoCreateInstance(CLSID_FilterMapper2, nullptr, CLSCTX_INPROC_SERVER, IID_IFilterMapper2, (void **)&fm);
	if (FAILED(hr)) return hr;
	if (reg) {
		REGFILTERPINS2 pin = {};
		REGPINTYPES types = {&MEDIATYPE_Video, &MEDIASUBTYPE_NULL};
		pin.dwFlags = REG_PINFLAG_B_OUTPUT;
		pin.nMediaTypes = 1;
		pin.lpMediaType = &types;
		pin.clsPinCategory = &PIN_CATEGORY_CAPTURE;
		REGFILTER2 rf = {};
		rf.dwVersion = 2;
		rf.dwMerit = MERIT_DO_NOT_USE;
		rf.cPins2 = 1;
		rf.rgPins2 = &pin;
		hr = fm->RegisterFilter(CLSID_AIVirtualCameraDShow, kName, nullptr, &CLSID_VideoInputDeviceCategory, nullptr, &rf);
	} else {
		hr = fm->UnregisterFilter(&CLSID_VideoInputDeviceCategory, nullptr, CLSID_AIVirtualCameraDShow);
	}
	fm->Release();
	return hr;
}

STDAPI DllRegisterServer() {
	HRESULT hr = AMovieDllRegisterServer2(TRUE);
	if (FAILED(hr)) return hr;
	HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
	hr = register_category(true);
	if (SUCCEEDED(co)) CoUninitialize();
	return hr;
}

STDAPI DllUnregisterServer() {
	HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
	register_category(false);
	if (SUCCEEDED(co)) CoUninitialize();
	return AMovieDllRegisterServer2(FALSE);
}

extern "C" BOOL WINAPI DllEntryPoint(HINSTANCE, ULONG, LPVOID);
BOOL APIENTRY DllMain(HANDLE module, DWORD reason, LPVOID reserved) {
	return DllEntryPoint((HINSTANCE)module, reason, reserved);
}
