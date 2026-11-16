// Shared by the DirectShow and Media Foundation cameras: read the app's frames from shared memory,
// scale them to the format a client negotiated, convert BGRA to the client's pixel format, and draw the
// "waiting for the app" card when no recent frame exists.
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <cstdint>
#include <vector>

#include "vcam_shm.h"

namespace vcam {

enum class PixFmt { BGRA32, RGB32_BOTTOMUP, RGB24_BOTTOMUP, YUY2, NV12 };
enum class Matrix { BT601, BT709 };       // YCbCr matrix of YUY2 / NV12 output (limited range)

inline uint64_t now_ns() {
	static LARGE_INTEGER freq = [] { LARGE_INTEGER f; QueryPerformanceFrequency(&f); return f; }();
	LARGE_INTEGER c;
	QueryPerformanceCounter(&c);
	return (uint64_t)((double)c.QuadPart * 1e9 / (double)freq.QuadPart);
}

size_t frame_bytes(PixFmt f, int w, int h);

class FrameReader {
public:
	~FrameReader() { close(); }
	// fromService: open the mapping of the active console session through its NT path (Frame Server runs
	// in session 0); otherwise "Local\..." in the caller's own session.
	bool open(bool fromService);
	void close();
	bool is_open() const { return hdr_ != nullptr; }
	// Copies the newest complete frame newer than `last` into `out` (BGRA). Returns true with frame
	// number/size when one was copied; false when none (keep showing the previous one).
	bool read_newest(uint64_t last, std::vector<uint8_t> &out, uint64_t &frame, uint32_t &w, uint32_t &h);
	// The app is running and sent a frame within VCAM_STALE_NS.
	bool live() const;
	uint32_t app_width() const { return hdr_ ? hdr_->width : 0; }
	uint32_t app_height() const { return hdr_ ? hdr_->height : 0; }

private:
	HANDLE map_ = nullptr;
	const uint8_t *base_ = nullptr;
	const vcam_shm_header *hdr_ = nullptr;
};

// Bilinear BGRA scale (src -> dst, both tightly packed).
void scale_bgra(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh);
// BGRA (top-down, tightly packed) -> client format into `dst` with the given row stride for packed
// formats (NV12: luma stride, chroma plane follows at stride * h).
void convert(const uint8_t *bgra, int w, int h, PixFmt f, uint8_t *dst, int stride, Matrix m = Matrix::BT601);
// "Waiting for AI Virtual Camera Studio..." card (BGRA), drawn with GDI.
void waiting_card(int w, int h, std::vector<uint8_t> &out);

// Per-stream helper: keeps the last frame, produces the bytes for one output sample.
class FrameProducer {
public:
	void set_format(int w, int h, PixFmt f, Matrix m = Matrix::BT601) { w_ = w; h_ = h; fmt_ = f; matrix_ = m; card_.clear(); }
	// Fills dst (frame_bytes(fmt, w, h) bytes, given stride) with the newest app frame or the card.
	// Returns true if an app frame (not the card) was produced.
	bool produce(FrameReader &reader, bool fromService, uint8_t *dst, int stride);

private:
	int w_ = 0, h_ = 0;
	PixFmt fmt_ = PixFmt::YUY2;
	Matrix matrix_ = Matrix::BT601;
	uint64_t last_ = 0;
	uint32_t lw_ = 0, lh_ = 0;
	std::vector<uint8_t> raw_, scaled_, card_;
	uint64_t next_open_try_ = 0;
};

}  // namespace vcam
