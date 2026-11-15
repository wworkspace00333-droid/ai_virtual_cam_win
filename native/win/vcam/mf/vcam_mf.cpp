// vcam_mf.dll — "AI Virtual Camera" as a Media Foundation virtual camera (Windows 11, build 22000+).
//
// vcam-setup.exe registers this COM server and calls MFCreateVirtualCamera with its CLSID. The Windows
// Camera Frame Server service then loads the DLL, creates the IMFActivate below and activates the media
// source. Every app that uses cameras through Media Foundation (the Camera app, Teams, browsers, ...) sees
// it like a hardware camera. Frames come from AI Virtual Camera Studio through shared memory
// (../vcam_shm.h); the service runs in session 0 and opens the app's section by its session NT path.
//
// Objects: Activator (IMFActivate) -> MediaSource (IMFMediaSourceEx, IMFGetService, IKsControl)
//          -> one MediaStream (IMFMediaStream2). Media types: NV12 and RGB32, 1080p/720p, 30/60 fps.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <initguid.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mfobjects.h>
#include <wrl/client.h>
#include <wrl/implements.h>

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

#include "../vcam_frames.h"

// ks.h redefines GUID_NULL as a macro: include it after every header that pulls in cguid.h
#include <ks.h>
#include <ksmedia.h>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "ole32.lib")

// IKsControl (declared in ksproxy.h, which does not combine with the Media Foundation headers here)
MIDL_INTERFACE("28F54685-06FD-11D2-B27A-00A0C9223196")
IKsControl : public IUnknown {
public:
	virtual HRESULT STDMETHODCALLTYPE KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG *) = 0;
	virtual HRESULT STDMETHODCALLTYPE KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG *) = 0;
	virtual HRESULT STDMETHODCALLTYPE KsEvent(PKSEVENT, ULONG, LPVOID, ULONG, ULONG *) = 0;
};

using Microsoft::WRL::ChainInterfaces;
using Microsoft::WRL::ClassicCom;
using Microsoft::WRL::ComPtr;
using Microsoft::WRL::Make;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;

// {20C0F518-9F28-4168-9F73-030F243FB3B9} — keep in sync with vcstudio/platform/win/vcam_status.py
static const GUID CLSID_AIVirtualCameraMF = {0x20c0f518, 0x9f28, 0x4168, {0x9f, 0x73, 0x03, 0x0f, 0x24, 0x3f, 0xb3, 0xb9}};
static const wchar_t kClsidStr[] = L"{20C0F518-9F28-4168-9F73-030F243FB3B9}";
static HMODULE g_module = nullptr;
static std::atomic<long> g_objects{0};

#define RETURN_IF_FAILED(x) do { HRESULT _hr = (x); if (FAILED(_hr)) return _hr; } while (0)

struct Mode { int w, h, fps; bool nv12; };
static const Mode kModes[] = {
    {1920, 1080, 30, true}, {1920, 1080, 60, true}, {1280, 720, 30, true}, {1280, 720, 60, true},
    {1920, 1080, 30, false}, {1280, 720, 30, false},
};

static HRESULT make_type(const Mode &m, IMFMediaType **out) {
	ComPtr<IMFMediaType> t;
	RETURN_IF_FAILED(MFCreateMediaType(&t));
	t->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
	t->SetGUID(MF_MT_SUBTYPE, m.nv12 ? MFVideoFormat_NV12 : MFVideoFormat_RGB32);
	MFSetAttributeSize(t.Get(), MF_MT_FRAME_SIZE, m.w, m.h);
	MFSetAttributeRatio(t.Get(), MF_MT_FRAME_RATE, m.fps, 1);
	MFSetAttributeRatio(t.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
	t->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
	t->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
	t->SetUINT32(MF_MT_DEFAULT_STRIDE, m.nv12 ? m.w : m.w * 4);
	t->SetUINT32(MF_MT_SAMPLE_SIZE, (UINT32)vcam::frame_bytes(m.nv12 ? vcam::PixFmt::NV12 : vcam::PixFmt::BGRA32, m.w, m.h));
	t->SetUINT32(MF_MT_FIXED_SIZE_SAMPLES, TRUE);
	*out = t.Detach();
	return S_OK;
}

// =============================================================================================== stream
class MediaSource;

class MediaStream : public RuntimeClass<RuntimeClassFlags<ClassicCom>,
                                        ChainInterfaces<IMFMediaStream2, IMFMediaStream, IMFMediaEventGenerator>> {
public:
	HRESULT RuntimeClassInitialize(MediaSource *src, IMFMediaSource *srcItf) {
		source_ = src;
		sourceItf_ = srcItf;
		RETURN_IF_FAILED(MFCreateEventQueue(&queue_));
		std::vector<ComPtr<IMFMediaType>> types(ARRAYSIZE(kModes));
		std::vector<IMFMediaType *> raw;
		for (size_t i = 0; i < ARRAYSIZE(kModes); ++i) {
			RETURN_IF_FAILED(make_type(kModes[i], &types[i]));
			raw.push_back(types[i].Get());
		}
		RETURN_IF_FAILED(MFCreateStreamDescriptor(0, (DWORD)raw.size(), raw.data(), &sd_));
		ComPtr<IMFMediaTypeHandler> h;
		RETURN_IF_FAILED(sd_->GetMediaTypeHandler(&h));
		RETURN_IF_FAILED(h->SetCurrentMediaType(raw[0]));
		sd_->SetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, PINNAME_VIDEO_CAPTURE);
		sd_->SetUINT32(MF_DEVICESTREAM_STREAM_ID, 0);
		sd_->SetUINT32(MF_DEVICESTREAM_FRAMESERVER_SHARED, 1);
		sd_->SetUINT32(MF_DEVICESTREAM_ATTRIBUTE_FRAMESOURCE_TYPES, MFFrameSourceTypes_Color);
		RETURN_IF_FAILED(MFCreateAttributes(&attrs_, 4));
		attrs_->SetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, PINNAME_VIDEO_CAPTURE);
		attrs_->SetUINT32(MF_DEVICESTREAM_STREAM_ID, 0);
		attrs_->SetUINT32(MF_DEVICESTREAM_FRAMESERVER_SHARED, 1);
		attrs_->SetUINT32(MF_DEVICESTREAM_ATTRIBUTE_FRAMESOURCE_TYPES, MFFrameSourceTypes_Color);
		return S_OK;
	}
	IMFStreamDescriptor *descriptor() { return sd_.Get(); }
	IMFAttributes *attributes() { return attrs_.Get(); }

	HRESULT Start() {
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		ComPtr<IMFMediaTypeHandler> h;
		ComPtr<IMFMediaType> t;
		RETURN_IF_FAILED(sd_->GetMediaTypeHandler(&h));
		RETURN_IF_FAILED(h->GetCurrentMediaType(&t));
		UINT32 w = 0, hh = 0, num = 30, den = 1;
		MFGetAttributeSize(t.Get(), MF_MT_FRAME_SIZE, &w, &hh);
		MFGetAttributeRatio(t.Get(), MF_MT_FRAME_RATE, &num, &den);
		GUID sub = GUID_NULL;
		t->GetGUID(MF_MT_SUBTYPE, &sub);
		w_ = (int)w; h_ = (int)hh;
		fmt_ = sub == MFVideoFormat_NV12 ? vcam::PixFmt::NV12 : vcam::PixFmt::BGRA32;
		period100ns_ = den && num ? (LONGLONG)10000000 * den / num : 333333;
		producer_.set_format(w_, h_, fmt_);
		next_ = 0;
		state_ = MF_STREAM_STATE_RUNNING;
		PROPVARIANT pv; PropVariantInit(&pv);
		return queue_->QueueEventParamVar(MEStreamStarted, GUID_NULL, S_OK, &pv);
	}
	HRESULT Stop() {
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		state_ = MF_STREAM_STATE_STOPPED;
		reader_.close();
		PROPVARIANT pv; PropVariantInit(&pv);
		return queue_->QueueEventParamVar(MEStreamStopped, GUID_NULL, S_OK, &pv);
	}
	void Shutdown() {
		std::lock_guard<std::mutex> l(m_);
		if (queue_) { queue_->Shutdown(); queue_.Reset(); }
		reader_.close();
		sourceItf_ = nullptr;
	}

	// IMFMediaEventGenerator
	IFACEMETHODIMP GetEvent(DWORD f, IMFMediaEvent **e) override {
		ComPtr<IMFMediaEventQueue> q;
		{ std::lock_guard<std::mutex> l(m_); q = queue_; }
		return q ? q->GetEvent(f, e) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP BeginGetEvent(IMFAsyncCallback *cb, IUnknown *s) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->BeginGetEvent(cb, s) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP EndGetEvent(IMFAsyncResult *r, IMFMediaEvent **e) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->EndGetEvent(r, e) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP QueueEvent(MediaEventType t, REFGUID g, HRESULT hr, const PROPVARIANT *v) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->QueueEventParamVar(t, g, hr, v) : MF_E_SHUTDOWN;
	}
	// IMFMediaStream
	IFACEMETHODIMP GetMediaSource(IMFMediaSource **s) override {
		std::lock_guard<std::mutex> l(m_);
		if (!sourceItf_) return MF_E_SHUTDOWN;
		*s = sourceItf_;
		(*s)->AddRef();
		return S_OK;
	}
	IFACEMETHODIMP GetStreamDescriptor(IMFStreamDescriptor **d) override {
		if (!d) return E_POINTER;
		*d = sd_.Get();
		(*d)->AddRef();
		return S_OK;
	}
	IFACEMETHODIMP RequestSample(IUnknown *token) override {
		std::unique_lock<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		if (state_ != MF_STREAM_STATE_RUNNING) return MF_E_INVALIDREQUEST;
		// pace to the negotiated frame rate on the QPC-based MF clock
		LONGLONG now = MFGetSystemTime();
		if (next_ == 0) next_ = now;
		if (next_ > now) {
			l.unlock();
			Sleep((DWORD)((next_ - now) / 10000));
			l.lock();
			if (!queue_ || state_ != MF_STREAM_STATE_RUNNING) return MF_E_INVALIDREQUEST;
			now = MFGetSystemTime();
		} else if (now - next_ > 4 * period100ns_) {
			next_ = now;                                      // consumer stalled: resync
		}
		next_ += period100ns_;
		ComPtr<IMFSample> sample;
		ComPtr<IMFMediaBuffer> buf;
		DWORD size = (DWORD)vcam::frame_bytes(fmt_, w_, h_);
		RETURN_IF_FAILED(MFCreateSample(&sample));
		RETURN_IF_FAILED(MFCreateMemoryBuffer(size, &buf));
		BYTE *p = nullptr;
		RETURN_IF_FAILED(buf->Lock(&p, nullptr, nullptr));
		producer_.produce(reader_, true, p, fmt_ == vcam::PixFmt::NV12 ? w_ : w_ * 4);
		buf->Unlock();
		buf->SetCurrentLength(size);
		sample->AddBuffer(buf.Get());
		sample->SetSampleTime(now);
		sample->SetSampleDuration(period100ns_);
		if (token) sample->SetUnknown(MFSampleExtension_Token, token);
		return queue_->QueueEventParamUnk(MEMediaSample, GUID_NULL, S_OK, sample.Get());
	}
	// IMFMediaStream2
	IFACEMETHODIMP SetStreamState(MF_STREAM_STATE s) override {
		if (s == MF_STREAM_STATE_RUNNING) return Start();
		if (s == MF_STREAM_STATE_STOPPED) return Stop();
		return MF_E_INVALID_STATE_TRANSITION;
	}
	IFACEMETHODIMP GetStreamState(MF_STREAM_STATE *s) override {
		if (!s) return E_POINTER;
		*s = state_;
		return S_OK;
	}

private:
	std::mutex m_;
	MediaSource *source_ = nullptr;
	IMFMediaSource *sourceItf_ = nullptr;      // weak: the source owns the stream
	ComPtr<IMFMediaEventQueue> queue_;
	ComPtr<IMFStreamDescriptor> sd_;
	ComPtr<IMFAttributes> attrs_;
	MF_STREAM_STATE state_ = MF_STREAM_STATE_STOPPED;
	int w_ = 1920, h_ = 1080;
	vcam::PixFmt fmt_ = vcam::PixFmt::NV12;
	LONGLONG period100ns_ = 333333, next_ = 0;
	vcam::FrameReader reader_;
	vcam::FrameProducer producer_;
};

// =============================================================================================== source
class MediaSource : public RuntimeClass<RuntimeClassFlags<ClassicCom>,
                                        ChainInterfaces<IMFMediaSourceEx, IMFMediaSource, IMFMediaEventGenerator>,
                                        IMFGetService, IKsControl> {
public:
	MediaSource() { ++g_objects; }
	~MediaSource() { --g_objects; }
	HRESULT RuntimeClassInitialize() {
		RETURN_IF_FAILED(MFCreateEventQueue(&queue_));
		RETURN_IF_FAILED(MFCreateAttributes(&attrs_, 4));
		RETURN_IF_FAILED(Microsoft::WRL::MakeAndInitialize<MediaStream>(&stream_, this, static_cast<IMFMediaSource *>(this)));
		IMFStreamDescriptor *sd = stream_->descriptor();
		RETURN_IF_FAILED(MFCreatePresentationDescriptor(1, &sd, &pd_));
		RETURN_IF_FAILED(pd_->SelectStream(0));
		return S_OK;
	}

	// IMFMediaEventGenerator
	IFACEMETHODIMP GetEvent(DWORD f, IMFMediaEvent **e) override {
		ComPtr<IMFMediaEventQueue> q;
		{ std::lock_guard<std::mutex> l(m_); q = queue_; }
		return q ? q->GetEvent(f, e) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP BeginGetEvent(IMFAsyncCallback *cb, IUnknown *s) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->BeginGetEvent(cb, s) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP EndGetEvent(IMFAsyncResult *r, IMFMediaEvent **e) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->EndGetEvent(r, e) : MF_E_SHUTDOWN;
	}
	IFACEMETHODIMP QueueEvent(MediaEventType t, REFGUID g, HRESULT hr, const PROPVARIANT *v) override {
		std::lock_guard<std::mutex> l(m_);
		return queue_ ? queue_->QueueEventParamVar(t, g, hr, v) : MF_E_SHUTDOWN;
	}
	// IMFMediaSource
	IFACEMETHODIMP GetCharacteristics(DWORD *c) override {
		if (!c) return E_POINTER;
		*c = MFMEDIASOURCE_IS_LIVE;
		return S_OK;
	}
	IFACEMETHODIMP CreatePresentationDescriptor(IMFPresentationDescriptor **pd) override {
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		return pd_->Clone(pd);
	}
	IFACEMETHODIMP Start(IMFPresentationDescriptor *pd, const GUID *timeFormat, const PROPVARIANT *start) override {
		if (!pd || !start) return E_INVALIDARG;
		if (timeFormat && *timeFormat != GUID_NULL) return MF_E_UNSUPPORTED_TIME_FORMAT;
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		BOOL selected = FALSE;
		ComPtr<IMFStreamDescriptor> sd;
		RETURN_IF_FAILED(pd->GetStreamDescriptorByIndex(0, &selected, &sd));
		if (selected) {
			// the caller may have changed the current media type on its copy of the descriptor
			ComPtr<IMFMediaTypeHandler> theirs, ours;
			ComPtr<IMFMediaType> t;
			if (SUCCEEDED(sd->GetMediaTypeHandler(&theirs)) && SUCCEEDED(theirs->GetCurrentMediaType(&t)) &&
			    SUCCEEDED(stream_->descriptor()->GetMediaTypeHandler(&ours)))
				ours->SetCurrentMediaType(t.Get());
			PROPVARIANT unk; PropVariantInit(&unk);
			unk.vt = VT_UNKNOWN;
			unk.punkVal = static_cast<IMFMediaStream *>(stream_.Get());
			RETURN_IF_FAILED(queue_->QueueEventParamVar(started_ ? MEUpdatedStream : MENewStream, GUID_NULL, S_OK, &unk));
			RETURN_IF_FAILED(stream_->Start());
			started_ = true;
		}
		PROPVARIANT t0; PropVariantInit(&t0);
		t0.vt = VT_I8;
		t0.hVal.QuadPart = MFGetSystemTime();
		return queue_->QueueEventParamVar(MESourceStarted, GUID_NULL, S_OK, &t0);
	}
	IFACEMETHODIMP Stop() override {
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		stream_->Stop();
		PROPVARIANT pv; PropVariantInit(&pv);
		return queue_->QueueEventParamVar(MESourceStopped, GUID_NULL, S_OK, &pv);
	}
	IFACEMETHODIMP Pause() override { return MF_E_INVALID_STATE_TRANSITION; }
	IFACEMETHODIMP Shutdown() override {
		std::lock_guard<std::mutex> l(m_);
		if (!queue_) return MF_E_SHUTDOWN;
		stream_->Shutdown();
		queue_->Shutdown();
		queue_.Reset();
		return S_OK;
	}
	// IMFMediaSourceEx
	IFACEMETHODIMP GetSourceAttributes(IMFAttributes **a) override {
		if (!a) return E_POINTER;
		*a = attrs_.Get();
		(*a)->AddRef();
		return S_OK;
	}
	IFACEMETHODIMP GetStreamAttributes(DWORD id, IMFAttributes **a) override {
		if (!a) return E_POINTER;
		if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
		*a = stream_->attributes();
		(*a)->AddRef();
		return S_OK;
	}
	IFACEMETHODIMP SetD3DManager(IUnknown *) override { return S_OK; }     // system-memory samples
	// IMFGetService
	IFACEMETHODIMP GetService(REFGUID, REFIID, LPVOID *) override { return MF_E_UNSUPPORTED_SERVICE; }
	// IKsControl: no camera controls (exposure, focus...) on a virtual camera
	IFACEMETHODIMP KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG *) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
	IFACEMETHODIMP KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG *) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
	IFACEMETHODIMP KsEvent(PKSEVENT, ULONG, LPVOID, ULONG, ULONG *) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }

private:
	std::mutex m_;
	ComPtr<IMFMediaEventQueue> queue_;
	ComPtr<IMFAttributes> attrs_;
	ComPtr<IMFPresentationDescriptor> pd_;
	ComPtr<MediaStream> stream_;
	bool started_ = false;
};

// ============================================================================================ activator
// IMFActivate: Frame Server creates this, may set attributes on it, then activates the media source.
class Activator : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ChainInterfaces<IMFActivate, IMFAttributes>> {
public:
	Activator() { ++g_objects; }
	~Activator() { --g_objects; }
	HRESULT RuntimeClassInitialize() { return MFCreateAttributes(&a_, 4); }

	IFACEMETHODIMP ActivateObject(REFIID riid, void **ppv) override {
		std::lock_guard<std::mutex> l(m_);
		if (!source_) RETURN_IF_FAILED(Microsoft::WRL::MakeAndInitialize<MediaSource>(&source_));
		return source_.CopyTo(riid, ppv);
	}
	IFACEMETHODIMP ShutdownObject() override {
		std::lock_guard<std::mutex> l(m_);
		if (source_) { source_->Shutdown(); source_.Reset(); }
		return S_OK;
	}
	IFACEMETHODIMP DetachObject() override {
		std::lock_guard<std::mutex> l(m_);
		source_.Reset();
		return S_OK;
	}

	// IMFAttributes: delegated to an attribute store
	IFACEMETHODIMP GetItem(REFGUID k, PROPVARIANT *v) override { return a_->GetItem(k, v); }
	IFACEMETHODIMP GetItemType(REFGUID k, MF_ATTRIBUTE_TYPE *t) override { return a_->GetItemType(k, t); }
	IFACEMETHODIMP CompareItem(REFGUID k, REFPROPVARIANT v, BOOL *r) override { return a_->CompareItem(k, v, r); }
	IFACEMETHODIMP Compare(IMFAttributes *t, MF_ATTRIBUTES_MATCH_TYPE m, BOOL *r) override { return a_->Compare(t, m, r); }
	IFACEMETHODIMP GetUINT32(REFGUID k, UINT32 *v) override { return a_->GetUINT32(k, v); }
	IFACEMETHODIMP GetUINT64(REFGUID k, UINT64 *v) override { return a_->GetUINT64(k, v); }
	IFACEMETHODIMP GetDouble(REFGUID k, double *v) override { return a_->GetDouble(k, v); }
	IFACEMETHODIMP GetGUID(REFGUID k, GUID *v) override { return a_->GetGUID(k, v); }
	IFACEMETHODIMP GetStringLength(REFGUID k, UINT32 *n) override { return a_->GetStringLength(k, n); }
	IFACEMETHODIMP GetString(REFGUID k, LPWSTR s, UINT32 n, UINT32 *l) override { return a_->GetString(k, s, n, l); }
	IFACEMETHODIMP GetAllocatedString(REFGUID k, LPWSTR *s, UINT32 *l) override { return a_->GetAllocatedString(k, s, l); }
	IFACEMETHODIMP GetBlobSize(REFGUID k, UINT32 *n) override { return a_->GetBlobSize(k, n); }
	IFACEMETHODIMP GetBlob(REFGUID k, UINT8 *b, UINT32 n, UINT32 *s) override { return a_->GetBlob(k, b, n, s); }
	IFACEMETHODIMP GetAllocatedBlob(REFGUID k, UINT8 **b, UINT32 *s) override { return a_->GetAllocatedBlob(k, b, s); }
	IFACEMETHODIMP GetUnknown(REFGUID k, REFIID r, LPVOID *p) override { return a_->GetUnknown(k, r, p); }
	IFACEMETHODIMP SetItem(REFGUID k, REFPROPVARIANT v) override { return a_->SetItem(k, v); }
	IFACEMETHODIMP DeleteItem(REFGUID k) override { return a_->DeleteItem(k); }
	IFACEMETHODIMP DeleteAllItems() override { return a_->DeleteAllItems(); }
	IFACEMETHODIMP SetUINT32(REFGUID k, UINT32 v) override { return a_->SetUINT32(k, v); }
	IFACEMETHODIMP SetUINT64(REFGUID k, UINT64 v) override { return a_->SetUINT64(k, v); }
	IFACEMETHODIMP SetDouble(REFGUID k, double v) override { return a_->SetDouble(k, v); }
	IFACEMETHODIMP SetGUID(REFGUID k, REFGUID v) override { return a_->SetGUID(k, v); }
	IFACEMETHODIMP SetString(REFGUID k, LPCWSTR v) override { return a_->SetString(k, v); }
	IFACEMETHODIMP SetBlob(REFGUID k, const UINT8 *b, UINT32 n) override { return a_->SetBlob(k, b, n); }
	IFACEMETHODIMP SetUnknown(REFGUID k, IUnknown *u) override { return a_->SetUnknown(k, u); }
	IFACEMETHODIMP LockStore() override { return a_->LockStore(); }
	IFACEMETHODIMP UnlockStore() override { return a_->UnlockStore(); }
	IFACEMETHODIMP GetCount(UINT32 *n) override { return a_->GetCount(n); }
	IFACEMETHODIMP GetItemByIndex(UINT32 i, GUID *k, PROPVARIANT *v) override { return a_->GetItemByIndex(i, k, v); }
	IFACEMETHODIMP CopyAllItems(IMFAttributes *d) override { return a_->CopyAllItems(d); }

private:
	std::mutex m_;
	ComPtr<IMFAttributes> a_;
	ComPtr<MediaSource> source_;
};

class Factory : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IClassFactory> {
public:
	IFACEMETHODIMP CreateInstance(IUnknown *outer, REFIID riid, void **ppv) override {
		if (outer) return CLASS_E_NOAGGREGATION;
		ComPtr<Activator> a;
		RETURN_IF_FAILED(Microsoft::WRL::MakeAndInitialize<Activator>(&a));
		return a.CopyTo(riid, ppv);
	}
	IFACEMETHODIMP LockServer(BOOL lock) override {
		lock ? ++g_objects : --g_objects;
		return S_OK;
	}
};

// ============================================================================================= exports
static bool g_mf = false;

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void **ppv) {
	if (clsid != CLSID_AIVirtualCameraMF) return CLASS_E_CLASSNOTAVAILABLE;
	if (!g_mf) g_mf = SUCCEEDED(MFStartup(MF_VERSION, MFSTARTUP_LITE));
	ComPtr<Factory> f = Make<Factory>();
	return f ? f.CopyTo(riid, ppv) : E_OUTOFMEMORY;
}

STDAPI DllCanUnloadNow() { return g_objects == 0 ? S_OK : S_FALSE; }

static HRESULT set_value(HKEY root, const std::wstring &key, const wchar_t *name, const std::wstring &v) {
	HKEY k;
	LONG r = RegCreateKeyExW(root, key.c_str(), 0, nullptr, 0, KEY_WRITE | KEY_WOW64_64KEY, nullptr, &k, nullptr);
	if (r != ERROR_SUCCESS) return HRESULT_FROM_WIN32(r);
	r = RegSetValueExW(k, name, 0, REG_SZ, (const BYTE *)v.c_str(), (DWORD)((v.size() + 1) * sizeof(wchar_t)));
	RegCloseKey(k);
	return HRESULT_FROM_WIN32(r);
}

STDAPI DllRegisterServer() {
	wchar_t path[MAX_PATH];
	GetModuleFileNameW(g_module, path, MAX_PATH);
	std::wstring base = std::wstring(L"CLSID\\") + kClsidStr;
	RETURN_IF_FAILED(set_value(HKEY_CLASSES_ROOT, base, nullptr, L"AI Virtual Camera (Media Foundation)"));
	RETURN_IF_FAILED(set_value(HKEY_CLASSES_ROOT, base + L"\\InprocServer32", nullptr, path));
	return set_value(HKEY_CLASSES_ROOT, base + L"\\InprocServer32", L"ThreadingModel", L"Both");
}

STDAPI DllUnregisterServer() {
	std::wstring base = std::wstring(L"CLSID\\") + kClsidStr;
	LONG r = RegDeleteTreeW(HKEY_CLASSES_ROOT, base.c_str());
	return r == ERROR_SUCCESS || r == ERROR_FILE_NOT_FOUND ? S_OK : HRESULT_FROM_WIN32(r);
}

BOOL APIENTRY DllMain(HMODULE m, DWORD reason, LPVOID) {
	if (reason == DLL_PROCESS_ATTACH) {
		g_module = m;
		DisableThreadLibraryCalls(m);
	}
	return TRUE;
}
