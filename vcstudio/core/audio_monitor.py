"""Microphone monitoring (0.6.0 Phase 8): hear the processed microphone on the default output device.

Windows waveOut (winmm) through ctypes — no extra package. Blocks of 48 kHz stereo 16-bit come from the audio
capture thread after the Audio Studio chain (vcstudio/core/audio_fx.py); at most MAX_IN_FLIGHT blocks are queued in
the driver (about 100-150 ms): when the device falls behind, new blocks are dropped and counted, so the delay never
grows. Use headphones: through speakers the microphone hears itself (feedback).

On a PC without an audio output (a server, a remote session without sound) opening fails and `error` says why; the
recording is not affected.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

import numpy as np

log = logging.getLogger(__name__)

RATE = 48000
MAX_IN_FLIGHT = 6
WHDR_DONE = 0x1
WAVE_MAPPER = 0xFFFFFFFF


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                    ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]


class WaveOut:
    """The real device (Windows)."""

    def __init__(self):
        self.winmm = ctypes.WinDLL("winmm")
        self.h = ctypes.c_void_p()
        fmt = WAVEFORMATEX(1, 2, RATE, RATE * 4, 4, 16, 0)                 # PCM, stereo, 16-bit
        rc = self.winmm.waveOutOpen(ctypes.byref(self.h), ctypes.c_uint(WAVE_MAPPER), ctypes.byref(fmt), None, None, 0)
        if rc != 0:
            raise OSError(f"no audio output (waveOutOpen error {rc})")

    def write(self, hdr: WAVEHDR) -> None:
        n = ctypes.sizeof(WAVEHDR)
        if self.winmm.waveOutPrepareHeader(self.h, ctypes.byref(hdr), n) != 0 or \
                self.winmm.waveOutWrite(self.h, ctypes.byref(hdr), n) != 0:
            raise OSError("waveOutWrite failed")

    def release(self, hdr: WAVEHDR) -> None:
        self.winmm.waveOutUnprepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

    def close(self) -> None:
        self.winmm.waveOutReset(self.h)
        self.winmm.waveOutClose(self.h)


class Monitor:
    def __init__(self, device_factory=None):
        self._factory = device_factory or WaveOut
        self.dev = None
        self.error = ""
        self.played = 0
        self.dropped = 0
        self._busy: list[tuple[WAVEHDR, ctypes.Array]] = []   # headers in the driver + their buffers (kept alive)
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.dev is not None

    def start(self) -> bool:
        with self._lock:
            if self.dev is not None:
                return True
            if sys.platform != "win32" and self._factory is WaveOut:
                self.error = "monitoring needs Windows"
                return False
            try:
                self.dev = self._factory()
                self.error = ""
                log.info("microphone monitoring on")
                return True
            except Exception as e:                       # noqa: BLE001 - no output device: report it
                self.error = str(e)
                self.dev = None
                return False

    def stop(self) -> None:
        with self._lock:
            dev, self.dev = self.dev, None
            if dev is not None:
                try:
                    dev.close()
                    for hdr, _buf in self._busy:
                        dev.release(hdr)
                except Exception:
                    log.exception("closing the monitor output failed")
            self._busy = []

    def push(self, samples: np.ndarray) -> None:
        """Queue one (n, 2) int16 block; dropped when MAX_IN_FLIGHT blocks are still playing."""
        with self._lock:
            dev = self.dev
            if dev is None or not len(samples):
                return
            still = []
            for hdr, buf in self._busy:                    # hand finished headers back
                if hdr.dwFlags & WHDR_DONE:
                    dev.release(hdr)
                else:
                    still.append((hdr, buf))
            self._busy = still
            if len(self._busy) >= MAX_IN_FLIGHT:
                self.dropped += 1
                return
            data = np.ascontiguousarray(samples, np.int16).tobytes()
            buf = ctypes.create_string_buffer(data, len(data))
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(data)
            try:
                dev.write(hdr)
            except Exception as e:                         # noqa: BLE001 - device gone: stop quietly
                self.error = str(e)
                return
            self._busy.append((hdr, buf))
            self.played += 1
