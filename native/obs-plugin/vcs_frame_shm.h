/*
 * Shared-memory frame transport between the OBS plugin (writer) and AI Virtual Camera Studio (reader).
 *
 * macOS:   POSIX shared memory "/vcs-obs-frames", sized for the maximum resolution up front.
 * Windows: named file mapping "Local\vcs-obs-frames" (the user's session), created with SEC_RESERVE
 *          for the maximum size; the writer commits only the header and the bytes the current
 *          resolution needs, and the reader never touches more than width*height*4 of a slot.
 * Layout (both): a 4 KiB header followed by VCS_SHM_SLOTS frame slots of VCS_SHM_SLOT_BYTES.
 *
 * Triple buffering + per-slot sequence lock:
 *   writer:  n = latest + 1; slot = n % SLOTS
 *            slot.seq = 2n - 1   (odd: being written) -> copy pixels + metadata -> slot.seq = 2n
 *            latest = n          (release)
 *   reader:  n = latest (acquire); s = slot[n % SLOTS].seq; require s == 2n;
 *            copy; re-read seq; equal -> consistent frame, otherwise retry with the newest frame.
 * The writer never waits for the reader; a slow reader just gets the newest frame (no queueing).
 * Pixel format: BGRA 8-bit, full range, top-down rows of `stride` bytes.
 * timestamp_ns / heartbeat_ns: os_gettime_ns() (macOS: mach_absolute_time; Windows: QueryPerformanceCounter).
 */
#pragma once
#include <stdint.h>

#define VCS_SHM_NAME "/vcs-obs-frames"
#define VCS_SHM_NAME_WIN L"Local\\vcs-obs-frames"
#define VCS_SHM_MAGIC 0x46534356u /* 'VCSF' little-endian */
#define VCS_SHM_VERSION 1u
#define VCS_SHM_SLOTS 3u
#define VCS_SHM_HEADER_BYTES 4096u
#define VCS_SHM_MAX_WIDTH 3840u
#define VCS_SHM_MAX_HEIGHT 2160u
#define VCS_SHM_SLOT_BYTES ((uint64_t)VCS_SHM_MAX_WIDTH * VCS_SHM_MAX_HEIGHT * 4u)
#define VCS_SHM_TOTAL_BYTES (VCS_SHM_HEADER_BYTES + VCS_SHM_SLOTS * VCS_SHM_SLOT_BYTES)
#define VCS_FMT_BGRA 1u

/* Atomics: C11 <stdatomic.h> where available; MSVC C uses aligned volatile 64/32-bit accesses, which are
 * single-copy atomic on x64/ARM64, with explicit barriers for release/acquire ordering. */
#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
#define VCS_ATOMIC(T) volatile T
static __forceinline void vcs_store_u64(volatile uint64_t *p, uint64_t v)
{
	_ReadWriteBarrier();
#if defined(_M_ARM64)
	__dmb(_ARM64_BARRIER_ISH);
#endif
	*p = v;
}
static __forceinline uint64_t vcs_load_u64(volatile uint64_t *p)
{
	uint64_t v = *p;
#if defined(_M_ARM64)
	__dmb(_ARM64_BARRIER_ISH);
#endif
	_ReadWriteBarrier();
	return v;
}
static __forceinline void vcs_store_u32(volatile uint32_t *p, uint32_t v)
{
	_ReadWriteBarrier();
	*p = v;
}
#else
#include <stdatomic.h>
#define VCS_ATOMIC(T) _Atomic T
#define vcs_store_u64(p, v) atomic_store_explicit((p), (v), memory_order_release)
#define vcs_load_u64(p) atomic_load_explicit((p), memory_order_acquire)
#define vcs_store_u32(p, v) atomic_store_explicit((p), (v), memory_order_release)
#endif

struct vcs_slot_info {
	VCS_ATOMIC(uint64_t) seq; /* 2n when frame n is complete, odd while being written */
	uint32_t width;
	uint32_t height;
	uint32_t stride;
	uint32_t format;
	uint64_t timestamp_ns;     /* OBS video timestamp (os_gettime_ns) */
};

struct vcs_shm_header {
	uint32_t magic;
	uint32_t version;
	uint32_t slots;
	uint32_t header_bytes;
	uint64_t slot_bytes;
	VCS_ATOMIC(uint64_t) latest;       /* number of the newest complete frame (0 = none yet) */
	VCS_ATOMIC(uint64_t) heartbeat_ns; /* writer clock at the last frame */
	int32_t writer_pid;
	uint32_t fps_num;
	uint32_t fps_den;
	VCS_ATOMIC(uint32_t) active;       /* 1 while the writer is publishing, 0 after it stopped */
	struct vcs_slot_info slot[VCS_SHM_SLOTS];
};

#if defined(__cplusplus)
static_assert(sizeof(struct vcs_shm_header) <= VCS_SHM_HEADER_BYTES, "header too large");
#elif defined(_MSC_VER)
typedef char vcs_header_fits[(sizeof(struct vcs_shm_header) <= VCS_SHM_HEADER_BYTES) ? 1 : -1];
#else
_Static_assert(sizeof(struct vcs_shm_header) <= VCS_SHM_HEADER_BYTES, "header too large");
#endif

static inline uint8_t *vcs_slot_pixels(void *base, uint32_t slot)
{
	return (uint8_t *)base + VCS_SHM_HEADER_BYTES + (uint64_t)slot * VCS_SHM_SLOT_BYTES;
}
