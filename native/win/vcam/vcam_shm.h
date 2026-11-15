/*
 * Frame transport from AI Virtual Camera Studio (writer) to the "AI Virtual Camera" devices (readers):
 * the DirectShow filter (vcam_dshow.dll, loaded into client apps) and the Media Foundation source
 * (vcam_mf.dll, loaded by the Frame Server service).
 *
 * Named file mapping "Local\AIVirtualCamera.Frames" in the user's session, created by the app with a DACL
 * that also lets LOCAL SERVICE (Frame Server) and app containers read it. The Frame Server runs in
 * session 0 and opens it as \Sessions\<id>\BaseNamedObjects\AIVirtualCamera.Frames.
 *
 * Layout and protocol mirror native/obs-plugin/vcs_frame_shm.h: 4 KiB header + 3 slots, per-slot seqlock:
 *   writer:  n = latest + 1; slot = n % SLOTS; slot.seq = 2n - 1; copy; fill slot info; slot.seq = 2n;
 *            latest = n; heartbeat_ns = now
 *   reader:  n = latest; s = slot[n % SLOTS].seq; need s == 2n; copy; re-check seq.
 * Pixels: BGRA 8-bit, top-down, stride = width * 4. Clock: QueryPerformanceCounter in nanoseconds
 * (Python time.perf_counter_ns() in the app), the same in every process of the machine.
 * The Python writer is vcstudio/platform/win/vcam_writer.py; keep both in sync.
 */
#pragma once
#include <stdint.h>

#define VCAM_SHM_NAME L"AIVirtualCamera.Frames"
#define VCAM_SHM_MAGIC 0x4D414356u /* 'VCAM' little-endian */
#define VCAM_SHM_VERSION 1u
#define VCAM_SHM_SLOTS 3u
#define VCAM_SHM_HEADER_BYTES 4096u
#define VCAM_MAX_WIDTH 1920u
#define VCAM_MAX_HEIGHT 1080u
#define VCAM_SHM_SLOT_BYTES ((uint64_t)VCAM_MAX_WIDTH * VCAM_MAX_HEIGHT * 4u)
#define VCAM_SHM_TOTAL_BYTES (VCAM_SHM_HEADER_BYTES + VCAM_SHM_SLOTS * VCAM_SHM_SLOT_BYTES)
#define VCAM_FMT_BGRA 1u
#define VCAM_STALE_NS 1000000000ull /* no frame for 1 s -> the camera shows its "waiting" card */

#pragma pack(push, 8)
struct vcam_slot_info {
	volatile uint64_t seq;
	uint32_t width;
	uint32_t height;
	uint32_t stride;
	uint32_t format;
	uint64_t timestamp_ns;
};

struct vcam_shm_header {
	uint32_t magic;
	uint32_t version;
	uint32_t slots;
	uint32_t header_bytes;
	uint64_t slot_bytes;
	volatile uint64_t latest;
	volatile uint64_t heartbeat_ns;
	int32_t writer_pid;
	uint32_t fps_num;          /* output frame rate the app is sending (settings) */
	uint32_t fps_den;
	volatile uint32_t active;  /* 1 while the app is running, 0 after it closed the output */
	uint32_t width;            /* output size the app is sending (settings) */
	uint32_t height;
	uint32_t reserved[2];
	struct vcam_slot_info slot[VCAM_SHM_SLOTS];
};
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(vcam_shm_header) <= VCAM_SHM_HEADER_BYTES, "header too large");
static_assert(offsetof(vcam_shm_header, slot) == 72, "layout shared with vcam_writer.py");
#endif
