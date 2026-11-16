// vcs_mfcap.dll — camera capture through Media Foundation (IMFSourceReader), for the app's "Media Foundation"
// capture method. The opencv-python wheels for Windows are built without the MSMF backend, so the app captures
// Media Foundation devices itself.
//
// The source reader runs asynchronously; each delivered sample is converted by the reader's advanced video
// processor (decodes MJPG, converts NV12 / YUY2 / ... with the colour attributes the device declares) to RGB32 =
// BGRA in memory, copied into a back buffer and swapped in as the newest frame. The caller polls with a timeout and
// always receives the newest frame (older ones are dropped, like the DirectShow path's 1-frame buffer).
//
//   void* mfcap_open(symlink, want_w, want_h, want_fps, &w, &h, &fps, subtype_out, 32, err, errcap)
//   int   mfcap_read(cap, dst, dst_bytes, &ts_100ns, timeout_ms)   1 frame | 0 timeout | -1 device lost | -2 size
//   long  mfcap_error(cap)   HRESULT that ended the stream (after -1)
//   void  mfcap_close(cap)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <shlwapi.h>
#include <wrl/client.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <mutex>
#include <vector>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "mfreadwrite.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shlwapi.lib")

using Microsoft::WRL::ComPtr;

static void set_err(wchar_t *err, int cap, const wchar_t *what, HRESULT hr) {
	if (err && cap > 0) swprintf(err, cap, L"%ls (0x%08lX)", what, (unsigned long)hr);
}

static int subtype_rank(const GUID &s) {     // equal size and rate: prefer formats that convert cheaply
	if (s == MFVideoFormat_NV12) return 0;
	if (s == MFVideoFormat_YUY2) return 1;
	if (s == MFVideoFormat_RGB32 || s == MFVideoFormat_ARGB32) return 2;
	if (s == MFVideoFormat_MJPG) return 3;
	return 4;
}

static void subtype_name(const GUID &s, wchar_t *out, int cap) {
	if (!out || cap <= 0) return;
	if (s == MFVideoFormat_NV12) wcsncpy_s(out, cap, L"NV12", _TRUNCATE);
	else if (s == MFVideoFormat_YUY2) wcsncpy_s(out, cap, L"YUY2", _TRUNCATE);
	else if (s == MFVideoFormat_MJPG) wcsncpy_s(out, cap, L"MJPG", _TRUNCATE);
	else if (s == MFVideoFormat_RGB32) wcsncpy_s(out, cap, L"RGB32", _TRUNCATE);
	else if (s == MFVideoFormat_ARGB32) wcsncpy_s(out, cap, L"ARGB32", _TRUNCATE);
	else if (s == MFVideoFormat_RGB24) wcsncpy_s(out, cap, L"RGB24", _TRUNCATE);
	else swprintf(out, cap, L"%08lX", s.Data1);
}

class Capture : public IMFSourceReaderCallback {
public:
	std::atomic<ULONG> ref{1};
	ComPtr<IMFMediaSource> source;
	ComPtr<IMFSourceReader> reader;
	std::mutex mu;
	std::vector<uint8_t> front, back;
	int w = 0, h = 0;
	LONGLONG ts = 0;
	uint64_t seq = 0, delivered = 0;
	bool lost = false;
	HRESULT last_hr = S_OK;          // why the stream ended (mfcap_error)
	std::atomic<bool> closing{false};
	HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);

	~Capture() { CloseHandle(event); }

	// IUnknown
	STDMETHODIMP QueryInterface(REFIID iid, void **ppv) override {
		static const QITAB qit[] = {QITABENT(Capture, IMFSourceReaderCallback), {nullptr, 0}};
		return QISearch(this, qit, iid, ppv);
	}
	STDMETHODIMP_(ULONG) AddRef() override { return ++ref; }
	STDMETHODIMP_(ULONG) Release() override {
		ULONG r = --ref;
		if (r == 0) delete this;
		return r;
	}

	// IMFSourceReaderCallback
	STDMETHODIMP OnReadSample(HRESULT hr, DWORD, DWORD flags, LONGLONG t, IMFSample *sample) override {
		if (closing) return S_OK;
		if (FAILED(hr) || (flags & (MF_SOURCE_READERF_ERROR | MF_SOURCE_READERF_ENDOFSTREAM))) {
			{ std::lock_guard<std::mutex> g(mu); lost = true; last_hr = FAILED(hr) ? hr : (HRESULT)(0x80000000u | flags); }
			SetEvent(event);
			return S_OK;
		}
		if (sample) copy_sample(sample, t);
		if (!closing && reader)
			if (FAILED(reader->ReadSample((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, nullptr, nullptr, nullptr, nullptr))) {
				std::lock_guard<std::mutex> g(mu);
				lost = true;
				SetEvent(event);
			}
		return S_OK;
	}
	STDMETHODIMP OnFlush(DWORD) override { return S_OK; }
	STDMETHODIMP OnEvent(DWORD, IMFMediaEvent *ev) override {
		MediaEventType type = MEUnknown;
		if (ev && SUCCEEDED(ev->GetType(&type)) && (type == MEError || type == MEVideoCaptureDeviceRemoved)) {
			HRESULT st = S_OK;
			ev->GetStatus(&st);
			{ std::lock_guard<std::mutex> g(mu); lost = true; last_hr = FAILED(st) ? st : E_FAIL; }
			SetEvent(event);
		}
		return S_OK;
	}

	void copy_sample(IMFSample *sample, LONGLONG t) {
		ComPtr<IMFMediaBuffer> buf;
		if (FAILED(sample->GetBufferByIndex(0, &buf))) return;
		const size_t row = (size_t)w * 4;
		back.resize(row * h);
		ComPtr<IMF2DBuffer> b2;
		BYTE *p = nullptr;
		LONG pitch = 0;
		if (SUCCEEDED(buf.As(&b2)) && SUCCEEDED(b2->Lock2D(&p, &pitch))) {
			for (int y = 0; y < h; ++y) memcpy(back.data() + y * row, p + (ptrdiff_t)y * pitch, row);   // pitch < 0: bottom-up
			b2->Unlock2D();
		} else {
			DWORD len = 0;
			if (FAILED(buf->Lock(&p, nullptr, &len))) return;
			if (len >= row * h) memcpy(back.data(), p, row * h);
			buf->Unlock();
		}
		{
			std::lock_guard<std::mutex> g(mu);
			front.swap(back);
			ts = t;
			++seq;
		}
		SetEvent(event);
	}
};

// MFStartup / MFShutdown are reference counted by Windows too, but the first startup and the last shutdown
// must not interleave between two threads opening / closing captures: one lock around the count.
static std::mutex g_mf_mu;
static int g_started = 0;

static HRESULT mf_acquire() {
	std::lock_guard<std::mutex> g(g_mf_mu);
	if (g_started == 0) {
		HRESULT hr = MFStartup(MF_VERSION);
		if (FAILED(hr)) return hr;
	}
	++g_started;
	return S_OK;
}

static void mf_release() {
	std::lock_guard<std::mutex> g(g_mf_mu);
	if (g_started > 0 && --g_started == 0) MFShutdown();
}

extern "C" __declspec(dllexport) void *mfcap_open(const wchar_t *symlink, int want_w, int want_h, double want_fps,
                                                  int *out_w, int *out_h, double *out_fps, wchar_t *subtype,
                                                  int subtype_cap, wchar_t *err, int errcap) {
	// COM for the source reader. Deliberately not balanced with CoUninitialize: the capture objects live on
	// after this call, and leaving the multithreaded apartment could tear it down under them. Repeated calls
	// on one thread only raise COM's per-thread count (RPC_E_CHANGED_MODE is fine: MF is free-threaded).
	(void)CoInitializeEx(nullptr, COINIT_MULTITHREADED);
	HRESULT hr;
	if (FAILED(hr = mf_acquire())) {
		set_err(err, errcap, L"MFStartup failed", hr);
		return nullptr;
	}
	auto fail = [&](const wchar_t *what, HRESULT code) -> void * {
		set_err(err, errcap, what, code);
		mf_release();
		return nullptr;
	};
	ComPtr<IMFAttributes> at;
	MFCreateAttributes(&at, 2);
	at->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID);
	at->SetString(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK, symlink);
	Capture *c = new Capture();
	if (FAILED(hr = MFCreateDeviceSource(at.Get(), &c->source))) {
		c->Release();
		return fail(hr == E_ACCESSDENIED ? L"camera access denied (Settings > Privacy & security > Camera)"
		                                 : L"cannot open the device (in use, unplugged or its driver failed)", hr);
	}
	ComPtr<IMFAttributes> ra;
	MFCreateAttributes(&ra, 3);
	ra->SetUnknown(MF_SOURCE_READER_ASYNC_CALLBACK, static_cast<IMFSourceReaderCallback *>(c));
	ra->SetUINT32(MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, TRUE);
	ra->SetUINT32(MF_LOW_LATENCY, TRUE);
	if (FAILED(hr = MFCreateSourceReaderFromMediaSource(c->source.Get(), ra.Get(), &c->reader))) {
		c->source->Shutdown(); c->Release();
		return fail(L"cannot create the source reader", hr);
	}
	// choose the device's own mode: requested size first, then the nearest rate (at least the requested one
	// when possible), then the cheapest format
	const DWORD stream = (DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM;
	ComPtr<IMFMediaType> best;
	double best_score = 1e30;
	for (DWORD i = 0;; ++i) {
		ComPtr<IMFMediaType> t;
		if (FAILED(c->reader->GetNativeMediaType(stream, i, &t))) break;
		UINT32 tw = 0, th = 0, n = 0, d = 1;
		GUID sub = GUID_NULL;
		MFGetAttributeSize(t.Get(), MF_MT_FRAME_SIZE, &tw, &th);
		MFGetAttributeRatio(t.Get(), MF_MT_FRAME_RATE, &n, &d);
		t->GetGUID(MF_MT_SUBTYPE, &sub);
		double f = d ? (double)n / d : 0.0;
		double size = std::fabs((double)tw * th - (double)want_w * want_h) / ((double)want_w * want_h + 1);
		double rate = f >= want_fps - 0.5 ? (f - want_fps) / 1000.0 : (want_fps - f) / 10.0;
		double score = size * 1000.0 + rate + subtype_rank(sub) * 1e-6;
		if (score < best_score) { best_score = score; best = t; }
	}
	if (!best) { c->reader.Reset(); c->source->Shutdown(); c->Release(); return fail(L"the device offers no video modes", E_FAIL); }
	GUID native_sub = GUID_NULL;
	best->GetGUID(MF_MT_SUBTYPE, &native_sub);
	if (FAILED(hr = c->reader->SetCurrentMediaType(stream, nullptr, best.Get()))) {
		c->reader.Reset(); c->source->Shutdown(); c->Release();
		return fail(L"the device refused its own mode", hr);
	}
	ComPtr<IMFMediaType> rgb;
	MFCreateMediaType(&rgb);
	best->CopyAllItems(rgb.Get());                                   // same size and rate, BGRA out
	rgb->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
	rgb->DeleteItem(MF_MT_DEFAULT_STRIDE);
	rgb->DeleteItem(MF_MT_SAMPLE_SIZE);
	// copied from a YUV type these would make the processor output "studio" RGB (16-235): ask for full range
	rgb->DeleteItem(MF_MT_YUV_MATRIX);
	rgb->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_0_255);
	if (FAILED(hr = c->reader->SetCurrentMediaType(stream, nullptr, rgb.Get()))) {
		c->reader.Reset(); c->source->Shutdown(); c->Release();
		return fail(L"no converter to BGRA for this mode", hr);
	}
	ComPtr<IMFMediaType> cur;
	c->reader->GetCurrentMediaType(stream, &cur);
	UINT32 cw = 0, ch = 0, n = 0, d = 1;
	MFGetAttributeSize(cur.Get(), MF_MT_FRAME_SIZE, &cw, &ch);
	MFGetAttributeRatio(cur.Get(), MF_MT_FRAME_RATE, &n, &d);
	c->w = (int)cw; c->h = (int)ch;
	if (out_w) *out_w = c->w;
	if (out_h) *out_h = c->h;
	if (out_fps) *out_fps = d ? (double)n / d : 0.0;
	subtype_name(native_sub, subtype, subtype_cap);
	if (FAILED(hr = c->reader->ReadSample(stream, 0, nullptr, nullptr, nullptr, nullptr))) {
		c->reader.Reset(); c->source->Shutdown(); c->Release();
		return fail(L"the device did not start streaming", hr);
	}
	// Errors that stop the stream at once arrive asynchronously; wait for the first frame (or the error) so the
	// caller gets a clear reason instead of an immediate "device lost".
	WaitForSingleObject(c->event, 4000);
	bool lost;
	HRESULT why;
	{ std::lock_guard<std::mutex> g(c->mu); lost = c->lost; why = c->last_hr; }
	if (c->seq) SetEvent(c->event);                                  // keep the first frame readable
	if (lost) {
		c->closing = true;
		c->source->Shutdown(); c->reader.Reset(); c->Release();
		// Windows lets one app control a camera at a time ("controlling mode"); a second one is refused
		if (why == MF_E_HW_MFT_FAILED_START_STREAMING || why == HRESULT_FROM_WIN32(ERROR_SHARING_VIOLATION))
			return fail(L"the camera is in use by another app (Windows lets one app use a camera at a time)", why);
		return fail(L"the device stopped right after starting", why);
	}
	return c;
}

extern "C" __declspec(dllexport) int mfcap_read(void *h, uint8_t *dst, int dst_bytes, long long *ts_100ns,
                                                int timeout_ms) {
	Capture *c = static_cast<Capture *>(h);
	if (!c || c->closing) return -1;
	// The caller must not close the capture while it reads (the Python side lets the reading thread close it).
	// As a second line of defence the read holds its own reference, so a close that lands during the wait
	// below wakes it (mfcap_close sets the event) and the object outlives this call.
	c->AddRef();
	struct Unref { Capture *c; ~Unref() { c->Release(); } } unref{c};
	bool fresh;
	{
		std::lock_guard<std::mutex> g(c->mu);
		if (c->lost) return -1;
		fresh = c->seq != c->delivered;
	}
	if (!fresh) WaitForSingleObject(c->event, (DWORD)timeout_ms);       // outside the lock
	std::lock_guard<std::mutex> g(c->mu);
	if (c->lost || c->closing) return -1;
	if (c->seq == c->delivered) return 0;
	const size_t need = (size_t)c->w * c->h * 4;
	if ((size_t)dst_bytes < need || c->front.size() < need) return -2;
	memcpy(dst, c->front.data(), need);
	if (ts_100ns) *ts_100ns = c->ts;
	c->delivered = c->seq;
	return 1;
}

extern "C" __declspec(dllexport) long mfcap_error(void *h) {
	Capture *c = static_cast<Capture *>(h);
	if (!c) return 0;
	std::lock_guard<std::mutex> g(c->mu);
	return (long)c->last_hr;
}

extern "C" __declspec(dllexport) void mfcap_close(void *h) {
	Capture *c = static_cast<Capture *>(h);
	if (!c) return;
	c->closing = true;
	SetEvent(c->event);                       // wake a read waiting for a frame
	if (c->source) c->source->Shutdown();     // stops the device; pending callbacks see `closing`
	c->reader.Reset();
	c->source.Reset();
	c->Release();                             // the reader's reference to the callback goes with the reader
	mf_release();
}
