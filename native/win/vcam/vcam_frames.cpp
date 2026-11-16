#include "vcam_frames.h"
#include "vcam_name.h"

#include <winternl.h>
#include <wtsapi32.h>

#include <algorithm>
#include <cstring>
#include <string>

#pragma comment(lib, "wtsapi32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")

namespace vcam {

size_t frame_bytes(PixFmt f, int w, int h) {
	switch (f) {
	case PixFmt::YUY2: return (size_t)w * h * 2;
	case PixFmt::NV12: return (size_t)w * h * 3 / 2;
	case PixFmt::RGB24_BOTTOMUP: return (size_t)((w * 3 + 3) & ~3) * h;      // DIB rows are DWORD-aligned
	default: return (size_t)w * h * 4;
	}
}

// --------------------------------------------------------------------------------------- open
typedef NTSTATUS(NTAPI *NtOpenSection_t)(PHANDLE, ACCESS_MASK, POBJECT_ATTRIBUTES);

static HANDLE open_session_section(DWORD session) {
	static NtOpenSection_t ntOpenSection =
	    (NtOpenSection_t)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtOpenSection");
	if (!ntOpenSection) return nullptr;
	std::wstring path = L"\\Sessions\\" + std::to_wstring(session) + L"\\BaseNamedObjects\\" + VCAM_SHM_NAME;
	UNICODE_STRING us;
	us.Buffer = (PWSTR)path.c_str();
	us.Length = (USHORT)(path.size() * sizeof(wchar_t));
	us.MaximumLength = us.Length;
	OBJECT_ATTRIBUTES oa;
	InitializeObjectAttributes(&oa, &us, OBJ_CASE_INSENSITIVE, nullptr, nullptr);
	HANDLE h = nullptr;
	return ntOpenSection(&h, SECTION_MAP_READ | SECTION_QUERY, &oa) >= 0 ? h : nullptr;
}

// The app's section in a given session, if it is there and the app sent a frame recently; returns the heartbeat.
static uint64_t session_heartbeat(HANDLE h) {
	void *v = MapViewOfFile(h, FILE_MAP_READ, 0, 0, sizeof(vcam_shm_header));
	if (!v) return 0;
	const vcam_shm_header *hdr = (const vcam_shm_header *)v;
	uint64_t hb = (hdr->magic == VCAM_SHM_MAGIC && hdr->version == VCAM_SHM_VERSION) ? hdr->heartbeat_ns : 0;
	UnmapViewOfFile(v);
	return hb;
}

bool FrameReader::open(bool fromService) {
	close();
	HANDLE h = nullptr;
	if (fromService) {
		// Frame Server runs in session 0; the app runs in a user session: the console, or a Remote Desktop session
		// that is not the console. WTSEnumerateSessions does not list those to LOCAL SERVICE (measured on Windows
		// Server 2025 over RDP), so probe the session namespaces directly and take the section whose writer sent
		// a frame most recently (two users running the app: the one that is streaming wins).
		uint64_t best = 0;
		DWORD console = WTSGetActiveConsoleSessionId();
		for (DWORD s = 1; s <= 64; ++s) {
			HANDLE c = open_session_section(s);
			if (!c) continue;
			uint64_t hb = session_heartbeat(c);
			if (hb > best || (!h && s == console)) {
				if (h) CloseHandle(h);
				h = c;
				best = hb > best ? hb : best;
			} else {
				CloseHandle(c);
			}
		}
	}
	if (!h)       // own session (DirectShow filter in a client app, or the MF source activated in-process)
		h = OpenFileMappingW(FILE_MAP_READ, FALSE, (std::wstring(L"Local\\") + VCAM_SHM_NAME).c_str());
	if (!h) return false;
	void *base = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0);
	if (!base) { CloseHandle(h); return false; }
	const vcam_shm_header *hdr = (const vcam_shm_header *)base;
	if (hdr->magic != VCAM_SHM_MAGIC || hdr->version != VCAM_SHM_VERSION) {
		UnmapViewOfFile(base);
		CloseHandle(h);
		return false;
	}
	map_ = h;
	base_ = (const uint8_t *)base;
	hdr_ = hdr;
	return true;
}

void FrameReader::close() {
	if (base_) UnmapViewOfFile(base_);
	if (map_) CloseHandle(map_);
	base_ = nullptr;
	map_ = nullptr;
	hdr_ = nullptr;
}

bool FrameReader::live() const {
	if (!hdr_ || !hdr_->active) return false;
	uint64_t hb = hdr_->heartbeat_ns, now = now_ns();
	return hb != 0 && now >= hb ? now - hb < VCAM_STALE_NS : hb != 0;
}

bool FrameReader::read_newest(uint64_t last, std::vector<uint8_t> &out, uint64_t &frame, uint32_t &w, uint32_t &h) {
	if (!hdr_) return false;
	for (int attempt = 0; attempt < 4; ++attempt) {
		uint64_t n = hdr_->latest;
		MemoryBarrier();
		if (n == 0 || n == last) return false;
		const vcam_slot_info &s = hdr_->slot[n % VCAM_SHM_SLOTS];
		uint64_t seq = s.seq;
		MemoryBarrier();
		uint32_t sw = s.width, sh = s.height, stride = s.stride;
		if (seq != 2 * n || sw == 0 || sh == 0 || sw > VCAM_MAX_WIDTH || sh > VCAM_MAX_HEIGHT || stride != sw * 4 ||
		    s.format != VCAM_FMT_BGRA)
			continue;
		size_t bytes = (size_t)stride * sh;
		out.resize(bytes);
		memcpy(out.data(), base_ + VCAM_SHM_HEADER_BYTES + (n % VCAM_SHM_SLOTS) * VCAM_SHM_SLOT_BYTES, bytes);
		MemoryBarrier();
		if (s.seq == seq) {
			frame = n; w = sw; h = sh;
			return true;
		}
	}
	return false;
}

// ----------------------------------------------------------------------------------- pixels
void scale_bgra(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh) {
	if (sw == dw && sh == dh) { memcpy(dst, src, (size_t)sw * sh * 4); return; }
	const float fx = (float)sw / dw, fy = (float)sh / dh;
	std::vector<int> x0(dw), x1(dw);
	std::vector<float> ax(dw);
	for (int x = 0; x < dw; ++x) {
		float sx = std::max(0.0f, (x + 0.5f) * fx - 0.5f);
		x0[x] = std::min((int)sx, sw - 1);
		x1[x] = std::min(x0[x] + 1, sw - 1);
		ax[x] = sx - x0[x];
	}
	for (int y = 0; y < dh; ++y) {
		float sy = std::max(0.0f, (y + 0.5f) * fy - 0.5f);
		int y0 = std::min((int)sy, sh - 1), y1 = std::min(y0 + 1, sh - 1);
		float ay = sy - y0;
		const uint8_t *r0 = src + (size_t)y0 * sw * 4, *r1 = src + (size_t)y1 * sw * 4;
		uint8_t *o = dst + (size_t)y * dw * 4;
		for (int x = 0; x < dw; ++x) {
			const uint8_t *a = r0 + x0[x] * 4, *b = r0 + x1[x] * 4, *c = r1 + x0[x] * 4, *d = r1 + x1[x] * 4;
			for (int k = 0; k < 4; ++k) {
				float top = a[k] + (b[k] - a[k]) * ax[x], bot = c[k] + (d[k] - c[k]) * ax[x];
				o[x * 4 + k] = (uint8_t)(top + (bot - top) * ay + 0.5f);
			}
		}
	}
}

// Limited-range YCbCr coefficients x256. BT.601: what DirectShow apps and OpenCV assume for YUY2 / NV12.
// BT.709: what the Windows video processor applies to HD NV12 from the Media Foundation camera (measured: it
// decodes HD NV12 as BT.709 even when the type declares BT.601), declared in the MF media types.
struct Coef { int yr, yg, yb, ur, ug, ub, vr, vg, vb; };
static const Coef kBT601 = {66, 129, 25, -38, -74, 112, 112, -94, -18};
static const Coef kBT709 = {47, 157, 16, -26, -87, 112, 112, -102, -10};
static thread_local const Coef *g_coef = &kBT601;
static inline uint8_t Y(int r, int g, int b) { const Coef &k = *g_coef; return (uint8_t)((k.yr * r + k.yg * g + k.yb * b + 128) / 256 + 16); }
static inline uint8_t U(int r, int g, int b) { const Coef &k = *g_coef; return (uint8_t)((k.ur * r + k.ug * g + k.ub * b + 128) / 256 + 128); }
static inline uint8_t V(int r, int g, int b) { const Coef &k = *g_coef; return (uint8_t)((k.vr * r + k.vg * g + k.vb * b + 128) / 256 + 128); }

void convert(const uint8_t *bgra, int w, int h, PixFmt f, uint8_t *dst, int stride, Matrix m) {
	g_coef = m == Matrix::BT709 ? &kBT709 : &kBT601;
	switch (f) {
	case PixFmt::BGRA32:
		for (int y = 0; y < h; ++y) memcpy(dst + (size_t)y * stride, bgra + (size_t)y * w * 4, (size_t)w * 4);
		break;
	case PixFmt::RGB32_BOTTOMUP:          // DirectShow RGB32 with positive biHeight is bottom-up
		for (int y = 0; y < h; ++y) memcpy(dst + (size_t)(h - 1 - y) * stride, bgra + (size_t)y * w * 4, (size_t)w * 4);
		break;
	case PixFmt::RGB24_BOTTOMUP:
		for (int y = 0; y < h; ++y) {
			const uint8_t *s = bgra + (size_t)y * w * 4;
			uint8_t *o = dst + (size_t)(h - 1 - y) * stride;
			for (int x = 0; x < w; ++x, s += 4, o += 3) { o[0] = s[0]; o[1] = s[1]; o[2] = s[2]; }
		}
		break;
	case PixFmt::YUY2:
		for (int y = 0; y < h; ++y) {
			const uint8_t *s = bgra + (size_t)y * w * 4;
			uint8_t *o = dst + (size_t)y * stride;
			for (int x = 0; x + 1 < w; x += 2, s += 8, o += 4) {
				int b0 = s[0], g0 = s[1], r0 = s[2], b1 = s[4], g1 = s[5], r1 = s[6];
				int r = (r0 + r1) >> 1, g = (g0 + g1) >> 1, b = (b0 + b1) >> 1;
				o[0] = Y(r0, g0, b0); o[1] = U(r, g, b); o[2] = Y(r1, g1, b1); o[3] = V(r, g, b);
			}
		}
		break;
	case PixFmt::NV12: {
		uint8_t *uv = dst + (size_t)stride * h;
		for (int y = 0; y < h; ++y) {
			const uint8_t *s = bgra + (size_t)y * w * 4;
			uint8_t *o = dst + (size_t)y * stride;
			for (int x = 0; x < w; ++x, s += 4) o[x] = Y(s[2], s[1], s[0]);
		}
		for (int y = 0; y + 1 < h; y += 2) {
			const uint8_t *s0 = bgra + (size_t)y * w * 4, *s1 = s0 + (size_t)w * 4;
			uint8_t *o = uv + (size_t)(y / 2) * stride;
			for (int x = 0; x + 1 < w; x += 2) {
				const uint8_t *a = s0 + x * 4, *b = a + 4, *c = s1 + x * 4, *d = c + 4;
				int r = (a[2] + b[2] + c[2] + d[2]) >> 2, g = (a[1] + b[1] + c[1] + d[1]) >> 2,
				    bl = (a[0] + b[0] + c[0] + d[0]) >> 2;
				o[x] = U(r, g, bl); o[x + 1] = V(r, g, bl);
			}
		}
		break;
	}
	}
}

void waiting_card(int w, int h, std::vector<uint8_t> &out) {
	out.assign((size_t)w * h * 4, 0);
	BITMAPINFO bi = {};
	bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
	bi.bmiHeader.biWidth = w;
	bi.bmiHeader.biHeight = -h;                       // top-down
	bi.bmiHeader.biPlanes = 1;
	bi.bmiHeader.biBitCount = 32;
	void *bits = nullptr;
	HDC dc = CreateCompatibleDC(nullptr);
	HBITMAP bmp = dc ? CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0) : nullptr;
	if (!bmp) {
		if (dc) DeleteDC(dc);
		for (size_t i = 0; i < out.size(); i += 4) { out[i] = 40; out[i + 1] = 32; out[i + 2] = 28; out[i + 3] = 255; }
		return;
	}
	HGDIOBJ old = SelectObject(dc, bmp);
	RECT rc = {0, 0, w, h};
	HBRUSH bg = CreateSolidBrush(RGB(28, 32, 40));
	FillRect(dc, &rc, bg);
	DeleteObject(bg);
	SetBkMode(dc, TRANSPARENT);
	HFONT title = CreateFontW(-h / 14, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
	HFONT body = CreateFontW(-h / 30, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
	SelectObject(dc, title);
	SetTextColor(dc, RGB(230, 237, 243));
	RECT t = {0, h / 2 - h / 10, w, h / 2};
	const std::wstring cam_name = camera_name();                       // the user-chosen camera name
	DrawTextW(dc, cam_name.c_str(), -1, &t, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
	SelectObject(dc, body);
	SetTextColor(dc, RGB(139, 148, 158));
	RECT b = {0, h / 2 + h / 60, w, h / 2 + h / 10};
	DrawTextW(dc, L"Waiting for AI Virtual Camera Studio…", -1, &b, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
	GdiFlush();
	memcpy(out.data(), bits, out.size());
	for (size_t i = 3; i < out.size(); i += 4) out[i] = 255;
	SelectObject(dc, old);
	DeleteObject(title);
	DeleteObject(body);
	DeleteObject(bmp);
	DeleteDC(dc);
}

bool FrameProducer::produce(FrameReader &reader, bool fromService, uint8_t *dst, int stride) {
	uint64_t now = now_ns();
	if (!reader.is_open() && now >= next_open_try_) {
		if (!reader.open(fromService)) next_open_try_ = now + 500000000ull;    // app not running: retry in 0.5 s
	}
	bool app = false;
	if (reader.is_open()) {
		uint64_t n;
		uint32_t fw, fh;
		if (reader.read_newest(last_, raw_, n, fw, fh)) {
			last_ = n; lw_ = fw; lh_ = fh;
		}
		app = reader.live() && lw_ != 0;
	}
	const uint8_t *src;
	if (app) {
		if ((int)lw_ != w_ || (int)lh_ != h_) {
			scaled_.resize((size_t)w_ * h_ * 4);
			scale_bgra(raw_.data(), lw_, lh_, scaled_.data(), w_, h_);
			src = scaled_.data();
		} else {
			src = raw_.data();
		}
	} else {
		if (card_.empty()) waiting_card(w_, h_, card_);
		src = card_.data();
		last_ = 0;                                    // re-read the newest frame when the app comes back
	}
	convert(src, w_, h_, fmt_, dst, stride, matrix_);
	return app;
}

}  // namespace vcam
