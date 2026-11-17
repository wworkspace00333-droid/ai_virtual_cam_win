"""Microphone audio for recordings and instant replay (vcstudio/core/recorder.py).

Capture goes through FFmpeg's DirectShow input in the bundled LGPL build (PyAV): no extra package. Audio is
resampled to 48 kHz stereo 16-bit and handed to listeners as (samples (n, 2) int16, start time) — the start time is
on the same clock as the video frames (vcstudio.core.frame.host_time_s), so recordings stay in sync.
The virtual camera itself carries no sound (Windows cameras are video only); audio is only for files.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterator

import numpy as np

log = logging.getLogger(__name__)

RATE = 48000
CHANNELS = 2


def parse_dshow_devices(lines: list[str]) -> list[str]:
    """Audio device names from FFmpeg's `-list_devices` log. A device is a quoted name followed (on the same or a
    following log line) by "(audio)"; video devices and "Alternative name" lines are skipped."""
    out: list[str] = []
    name: str | None = None
    for raw in lines:
        s = raw.strip()
        m = re.match(r'^"(.+?)"(.*)$', s)
        if m:                                       # a device name, maybe with its type on the same line
            name = m.group(1)
            if "(audio" in m.group(2):
                out.append(name)
                name = None
            elif "(" in m.group(2):
                name = None
            continue
        if name is not None and s.startswith("(audio"):
            out.append(name)
            name = None
        elif s.startswith("("):
            name = None
    return list(dict.fromkeys(out))


def list_microphones() -> list[str]:
    """Names of the audio capture devices DirectShow sees (empty when there are none or listing fails)."""
    try:
        import av
        import av.logging as avlog
        with avlog.Capture() as logs:
            try:
                av.open("dummy", format="dshow", options={"list_devices": "true"})
            except Exception:                      # FFmpeg "exits" after listing: expected
                pass
        return parse_dshow_devices([msg for _lv, _name, msg in logs])
    except Exception as e:
        log.info("listing microphones failed: %s", e)
        return []


def to_stereo_s16(frame) -> np.ndarray:
    """(n, 2) int16 from a resampled PyAV AudioFrame (s16 packed stereo)."""
    a = frame.to_ndarray()
    return np.ascontiguousarray(a.reshape(-1, CHANNELS))


class AudioSource:
    """Background capture from one microphone. listeners: callables(samples (n, 2) int16, start time)."""

    def __init__(self, device: str, chunks: Callable[[], Iterator[np.ndarray]] | None = None,
                 clock: Callable[[], float] | None = None):
        from vcstudio.core.frame import host_time_s
        self.device = device
        self._chunks = chunks                    # tests: a generator of (n, 2) int16 chunks instead of a device
        self._clock = clock or host_time_s
        self.listeners: list[Callable[[np.ndarray, float], None]] = []
        self.error = ""
        self.samples = 0
        self._t: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self.error = ""
        self._t = threading.Thread(target=self._run, args=(self._stop,), name="audio-capture", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(3)
        self._t = None

    def _device_chunks(self, stop: threading.Event) -> Iterator[np.ndarray]:
        import av
        container = av.open(f"audio={self.device}", format="dshow",
                            options={"audio_buffer_size": "40", "sample_rate": str(RATE)})
        try:
            stream = container.streams.audio[0]
            res = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
            for packet in container.demux(stream):
                if stop.is_set():
                    break
                for fr in packet.decode():
                    for out in res.resample(fr):
                        yield to_stereo_s16(out)
        finally:
            container.close()

    def _run(self, stop: threading.Event) -> None:
        try:
            gen = self._chunks() if self._chunks is not None else self._device_chunks(stop)
            for chunk in gen:
                if stop.is_set():
                    break
                if chunk.size == 0:
                    continue
                # the chunk ends "now": its first sample was captured len/RATE seconds ago
                t = self._clock() - len(chunk) / RATE
                self.samples += len(chunk)
                for cb in tuple(self.listeners):
                    try:
                        cb(chunk, t)
                    except Exception:
                        log.exception("audio listener failed")
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("microphone %r stopped: %s", self.device, self.error)
