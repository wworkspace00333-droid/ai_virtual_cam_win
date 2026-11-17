"""Recording the camera output to an MP4 file, and instant replay (the last seconds, saved on demand).

Encoding uses PyAV with the LGPL FFmpeg build (no x264): the first encoder that opens of
  h264_mf (the Windows Media Foundation H.264 encoder — hardware where the GPU offers it, else Microsoft's software
  encoder), h264_nvenc / h264_qsv / h264_amf (GPU vendors), mpeg4 (always available, larger files).
The output thread only hands frames over: a bounded queue, and a frame that does not fit is dropped and counted —
recording can never slow the camera down. A writer thread converts and encodes. Timestamps come from the frames'
own times (millisecond time base, variable frame rate), so a pause in the video is a pause in the file.

Instant replay keeps the last `seconds` of the output as downscaled JPEG frames in memory (roughly 30–80 KB per
720p frame) and writes them as a clip only when asked.
"""
from __future__ import annotations

import collections
import datetime as _dt
import logging
import os
import queue
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

ENCODERS = ("h264_mf", "h264_nvenc", "h264_qsv", "h264_amf", "mpeg4")
HW_SUFFIXES = ("_mf", "_qsv", "_nvenc", "_amf")
QUEUE_FRAMES = 8
TIME_BASE = Fraction(1, 1000)


def default_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Videos"
    return base / "AI Virtual Camera Studio"


def new_name(prefix: str = "recording") -> str:
    return f"{prefix}-{_dt.datetime.now():%Y%m%d-%H%M%S}.mp4"


@dataclass
class RecorderStats:
    state: str = "idle"              # idle | recording | stopping | done | error
    path: str = ""
    encoder: str = ""
    frames: int = 0
    dropped: int = 0
    seconds: float = 0.0
    error: str = ""


def open_writer(path: Path, w: int, h: int, fps: int, encoders=ENCODERS):
    """(container, stream, encoder name) for the first encoder that really opens — an encoder FFmpeg knows can still
    fail to open (no GPU of that vendor, Media Foundation encoder missing on a Windows N edition)."""
    import av
    last: Exception | None = None
    for name in encoders:
        container = av.open(str(path), mode="w")
        try:
            st = container.add_stream(name, rate=fps)
            st.width, st.height = w, h
            st.pix_fmt = "nv12" if name.endswith(HW_SUFFIXES) else "yuv420p"
            st.bit_rate = int(w * h * fps * 0.12)               # ~7.5 Mbit/s at 1080p30
            st.codec_context.time_base = TIME_BASE
            st.time_base = TIME_BASE
            st.codec_context.open()                             # fail here, not on the first frame
            return container, st, name
        except Exception as e:
            last = e
            log.info("encoder %s unavailable: %s", name, e)
            try:
                container.close()
            except Exception:
                pass
    raise RuntimeError(f"no video encoder could be opened ({last})")


class Recorder:
    """Records BGRA frames to an MP4 in a background thread. push() never blocks (unless asked to)."""

    def __init__(self, path: Path, fps: float = 30, encoders=ENCODERS, queue_frames: int = QUEUE_FRAMES):
        self.path = Path(path)
        self.fps = max(1, int(round(fps)))
        self.encoders = tuple(encoders)
        self.stats = RecorderStats()
        self._q: queue.Queue = queue.Queue(maxsize=queue_frames)
        self._t: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def active(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stats = RecorderStats(state="recording", path=str(self.path))
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._t.start()

    def push(self, bgra: np.ndarray, pts: float, block: bool = False) -> bool:
        """Hand a frame over (copied). False if it was dropped because the writer is behind."""
        if not self.active or self._stop.is_set():
            return False
        try:
            self._q.put((np.array(bgra, copy=True, order="C"), float(pts)), block=block, timeout=5 if block else None)
            return True
        except queue.Full:
            self.stats.dropped += 1
            return False

    def stop(self, timeout: float = 15.0) -> RecorderStats:
        if self._t is None:
            return self.stats
        if self.stats.state == "recording":
            self.stats.state = "stopping"
        self._stop.set()
        self._t.join(timeout)
        if self._t.is_alive():
            log.warning("recorder did not finish within %.0f s", timeout)
        self._t = None
        return self.stats

    def _run(self) -> None:
        import av
        container: Any = None
        st: Any = None
        t0 = 0.0
        last_ms = -1
        try:
            while not (self._stop.is_set() and self._q.empty()):
                try:
                    img, pts = self._q.get(timeout=0.1)
                except queue.Empty:
                    continue
                h, w = img.shape[:2]
                if container is None:
                    container, st, name = open_writer(self.path, w - w % 2, h - h % 2, self.fps, self.encoders)
                    self.stats.encoder = name
                    t0 = pts
                if (h, w) != (st.height, st.width):
                    if (h - h % 2, w - w % 2) == (st.height, st.width):  # 4:2:0 encoders want even sizes
                        img = np.ascontiguousarray(img[:st.height, :st.width])
                    else:                                                # size changed mid-recording
                        import cv2
                        img = cv2.resize(img, (st.width, st.height), interpolation=cv2.INTER_AREA)
                t_ms = max(int(round((pts - t0) * 1000)), last_ms + 1)  # timestamps must increase strictly
                last_ms = t_ms
                vf = av.VideoFrame.from_ndarray(img, format="bgra").reformat(format=st.pix_fmt)
                vf.pts, vf.time_base = t_ms, TIME_BASE
                for pkt in st.encode(vf):
                    container.mux(pkt)
                self.stats.frames += 1
                self.stats.seconds = t_ms / 1000
            if st is not None:
                for pkt in st.encode():
                    container.mux(pkt)
            self.stats.state = "done"
        except Exception as e:
            self.stats.state, self.stats.error = "error", f"{type(e).__name__}: {e}"
            log.exception("recording failed")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    log.exception("closing the recording failed")


class InstantReplay:
    """Keeps the last `seconds` of output as JPEG-compressed, downscaled frames; save() writes them as a clip.

    offer() is for the output thread: it only queues a copy (dropped when the worker is behind); the worker started
    by start() compresses. push() compresses in the calling thread (tests, save)."""

    def __init__(self, seconds: float = 30.0, max_side: int = 1280, quality: int = 80, max_fps: float = 30):
        self.seconds = float(seconds)
        self.max_side = max_side
        self.quality = quality
        self.min_interval = 1.0 / max_fps
        self.dropped = 0
        self._buf: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._last = -1e9
        self._last_offer = -1e9
        self._q: queue.Queue = queue.Queue(maxsize=3)
        self._t: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- background worker
    @property
    def running(self) -> bool:
        return self._t is not None and self._t.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._work, name="instant-replay", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(5)
        self._t = None

    def offer(self, bgra: np.ndarray, pts: float) -> None:
        if not self.running or pts - self._last_offer < self.min_interval * 0.9:
            return
        self._last_offer = pts
        try:
            self._q.put_nowait((np.array(bgra, copy=True, order="C"), pts))
        except queue.Full:
            self.dropped += 1

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                img, pts = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.push(img, pts)
            except Exception:
                log.exception("instant replay: frame not kept")

    def push(self, bgra: np.ndarray, pts: float) -> bool:
        import cv2
        if pts - self._last < self.min_interval * 0.9:
            return False
        self._last = pts
        h, w = bgra.shape[:2]
        s = min(1.0, self.max_side / max(w, h))
        img = bgra[..., :3]
        if s < 1:
            img = cv2.resize(img, (max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)),
                             interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", np.ascontiguousarray(img), [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return False
        with self._lock:
            self._buf.append((pts, jpg))
            while self._buf and pts - self._buf[0][0] > self.seconds:
                self._buf.popleft()
        return True

    @property
    def held_seconds(self) -> float:
        with self._lock:
            return (self._buf[-1][0] - self._buf[0][0]) if len(self._buf) > 1 else 0.0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def memory_bytes(self) -> int:
        with self._lock:
            return sum(len(j) for _, j in self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
        self._last = self._last_offer = -1e9

    def save(self, path: Path, encoders=ENCODERS) -> RecorderStats:
        """Write the held frames as an MP4 (blocking; call it off the UI thread). Safe while frames keep coming."""
        import cv2
        with self._lock:
            frames = list(self._buf)
        if not frames:
            return RecorderStats(state="error", path=str(path), error="nothing to save yet")
        rec = Recorder(path, 30, encoders, queue_frames=16)
        rec.start()
        for pts, jpg in frames:
            img = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if not rec.push(cv2.cvtColor(img, cv2.COLOR_BGR2BGRA), pts, block=True):
                break
        return rec.stop(timeout=120)


class RecordingService:
    """The app's recording and instant replay: attaches to the pipeline's output taps (settings section
    "recording"). Events go to `log_event(level, message)` (the journal)."""

    def __init__(self, pipeline, settings, log_event=None, encoders=ENCODERS):
        self.pipeline, self.settings = pipeline, settings
        self._log = log_event or (lambda lv, m: None)
        self.encoders = encoders
        self.recorder: Recorder | None = None
        self.replay: InstantReplay | None = None
        self.last_path: Path | None = None
        self._lock = threading.Lock()
        self.apply_settings()

    def folder(self) -> Path:
        f = (self.settings.get("recording.folder") or "").strip()
        return Path(f) if f else default_dir()

    # ---- recording
    @property
    def recording(self) -> bool:
        return self.recorder is not None and self.recorder.active

    def start_recording(self, path: Path | None = None) -> Path:
        with self._lock:
            if self.recording:
                assert self.recorder is not None
                return self.recorder.path
            p = Path(path) if path else self.folder() / new_name("recording")
            fps = self.settings.get("output.fps") or 30
            rec = Recorder(p, fps, self.encoders)
            rec.start()
            self.recorder = rec
            self.pipeline.taps.append(rec.push)
        self._log("INFO", f"recording started: {p}")
        return p

    def stop_recording(self) -> RecorderStats | None:
        with self._lock:
            rec, self.recorder = self.recorder, None
            if rec is None:
                return None
            try:
                self.pipeline.taps.remove(rec.push)
            except ValueError:
                pass
        st = rec.stop()
        self.last_path = rec.path
        lv = "ERROR" if st.state == "error" else "INFO"
        self._log(lv, f"recording stopped: {st.path} ({st.frames} frames, {st.seconds:.1f} s, {st.encoder or '-'}"
                      f", {st.dropped} dropped){' ' + st.error if st.error else ''}")
        return st

    def toggle_recording(self) -> bool:
        if self.recording:
            self.stop_recording()
            return False
        self.start_recording()
        return True

    # ---- instant replay
    def apply_settings(self) -> None:
        on = bool(self.settings.get("recording.replay_enabled"))
        secs = float(self.settings.get("recording.replay_seconds") or 30)
        with self._lock:
            if on and self.replay is None:
                self.replay = InstantReplay(secs)
                self.replay.start()
                self.pipeline.taps.append(self.replay.offer)
            elif not on and self.replay is not None:
                try:
                    self.pipeline.taps.remove(self.replay.offer)
                except ValueError:
                    pass
                self.replay.stop()
                self.replay = None
            elif self.replay is not None:
                self.replay.seconds = secs

    def save_replay(self, path: Path | None = None) -> RecorderStats:
        rp = self.replay
        if rp is None:
            return RecorderStats(state="error", error="instant replay is off")
        p = Path(path) if path else self.folder() / new_name("replay")
        st = rp.save(p, self.encoders)
        if st.state != "error":
            self.last_path = p
        self._log("ERROR" if st.state == "error" else "INFO",
                  f"instant replay saved: {p} ({st.seconds:.1f} s)" if st.state != "error"
                  else f"instant replay not saved: {st.error}")
        return st

    def close(self) -> None:
        self.stop_recording()
        if self.replay is not None:
            self.replay.stop()
