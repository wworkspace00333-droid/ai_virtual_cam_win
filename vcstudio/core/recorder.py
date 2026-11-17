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
    audio_seconds: float = 0.0
    audio_dropped: int = 0


def open_writer(path: Path, w: int, h: int, fps: int, encoders=ENCODERS):
    """(container, stream, encoder name) for the first encoder that really opens — an encoder FFmpeg knows can still
    fail to open (no GPU of that vendor, Media Foundation encoder missing on a Windows N edition)."""
    container, st, name, _a = open_writer_av(path, w, h, fps, encoders, audio=False)
    return container, st, name


def open_writer_av(path: Path, w: int, h: int, fps: int, encoders=ENCODERS, audio: bool = False):
    """As open_writer, plus an AAC stereo 48 kHz audio stream (FFmpeg's own AAC encoder, LGPL) when `audio`."""
    import av

    from vcstudio.core.audio import RATE
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
            ast = None
            if audio:
                ast = container.add_stream("aac", rate=RATE)
                ast.codec_context.layout = "stereo"
                ast.bit_rate = 128_000
                ast.codec_context.open()
            return container, st, name, ast
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

    def __init__(self, path: Path, fps: float = 30, encoders=ENCODERS, queue_frames: int = QUEUE_FRAMES,
                 audio: bool = False, audio_queue: int = 2000):
        self.path = Path(path)
        self.fps = max(1, int(round(fps)))
        self.encoders = tuple(encoders)
        self.audio = audio
        self.stats = RecorderStats()
        self._q: queue.Queue = queue.Queue(maxsize=queue_frames)
        self._aq: queue.Queue = queue.Queue(maxsize=audio_queue)   # (samples (n, 2) int16, start time)
        self._t: threading.Thread | None = None
        self._stop = threading.Event()

    def push_audio(self, samples: np.ndarray, t: float) -> bool:
        """Hand microphone samples over (same clock as the frames). Never blocks; False when dropped."""
        if not self.audio or not self.active or self._stop.is_set():
            return False
        try:
            self._aq.put_nowait((samples, float(t)))
            return True
        except queue.Full:
            self.stats.audio_dropped += 1
            return False

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
        aw: _AudioWriter | None = None
        t0 = 0.0
        last_ms = -1
        try:
            while not (self._stop.is_set() and self._q.empty()):
                if aw is not None:
                    aw.drain(self._aq)
                try:
                    img, pts = self._q.get(timeout=0.1)
                except queue.Empty:
                    continue
                h, w = img.shape[:2]
                if container is None:
                    container, st, name, ast = open_writer_av(self.path, w - w % 2, h - h % 2, self.fps,
                                                             self.encoders, audio=self.audio)
                    self.stats.encoder = name
                    t0 = pts
                    if ast is not None:
                        aw = _AudioWriter(container, ast, t0, self.stats)
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
            if aw is not None:
                aw.drain(self._aq)
                aw.finish()
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


class _AudioWriter:
    """Places microphone chunks on the file's timeline: sample index = (chunk time - first video frame time) × rate.
    Small timing jitter is ignored (samples continue back to back); a gap over GAP_S is filled with silence, an
    overlap over GAP_S is cut, audio from before the first frame is trimmed — so the sound never drifts more than
    GAP_S from the picture (lip-sync errors become noticeable around 45 ms). Encodes 1024-sample AAC frames."""
    GAP_S = 0.04

    def __init__(self, container, stream, t0: float, stats: RecorderStats):
        import av

        from vcstudio.core.audio import RATE
        self.container, self.stream, self.t0, self.stats = container, stream, t0, stats
        self.rate = RATE
        self.pos: int | None = None                 # next sample index on the file timeline
        self.fifo = av.AudioFifo()

    def _write(self, samples: np.ndarray) -> None:
        import av
        if len(samples) == 0:
            return
        assert self.pos is not None
        planar = np.ascontiguousarray(samples.T.astype(np.float32) / 32768.0)     # (2, n) float planar
        fr = av.AudioFrame.from_ndarray(planar, format="fltp", layout="stereo")
        fr.sample_rate = self.rate
        fr.pts = self.pos
        fr.time_base = Fraction(1, self.rate)
        self.fifo.write(fr)
        self.pos += len(samples)
        size = self.stream.codec_context.frame_size or 1024
        while self.fifo.samples >= size:
            for pkt in self.stream.encode(self.fifo.read(size)):
                self.container.mux(pkt)
        self.stats.audio_seconds = self.pos / self.rate

    def add(self, samples: np.ndarray, t: float) -> None:
        idx = int(round((t - self.t0) * self.rate))
        if self.pos is None:
            if idx + len(samples) <= 0:
                return                               # entirely before the first frame
            if idx < 0:
                samples, idx = samples[-idx:], 0
            self.pos = 0
            if idx > 0:
                self._write(np.zeros((idx, 2), np.int16))     # the file starts with the video: lead-in silence
        gap = idx - self.pos
        tol = int(self.GAP_S * self.rate)
        if gap > tol:
            self._write(np.zeros((gap, 2), np.int16))
        elif gap < -tol:
            samples = samples[min(len(samples), -gap):]
        self._write(samples)

    def drain(self, q: queue.Queue) -> None:
        while True:
            try:
                samples, t = q.get_nowait()
            except queue.Empty:
                return
            self.add(samples, t)

    def finish(self) -> None:
        if self.fifo.samples:
            for pkt in self.stream.encode(self.fifo.read()):
                self.container.mux(pkt)
        for pkt in self.stream.encode(None):
            self.container.mux(pkt)


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
        self._abuf: collections.deque = collections.deque()      # (start time, samples (n, 2) int16): ~192 KB/s
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

    def push_audio(self, samples: np.ndarray, t: float) -> None:
        """Microphone samples (same clock as the frames); kept as long as the frames."""
        with self._lock:
            self._abuf.append((float(t), np.array(samples, np.int16, copy=True)))
            while self._abuf and t - self._abuf[0][0] > self.seconds + 0.5:
                self._abuf.popleft()

    @property
    def held_seconds(self) -> float:
        with self._lock:
            return (self._buf[-1][0] - self._buf[0][0]) if len(self._buf) > 1 else 0.0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def memory_bytes(self) -> int:
        with self._lock:
            return sum(len(j) for _, j in self._buf) + sum(a.nbytes for _, a in self._abuf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._abuf.clear()
        self._last = self._last_offer = -1e9

    def save(self, path: Path, encoders=ENCODERS) -> RecorderStats:
        """Write the held frames (and microphone audio, if any) as an MP4 (blocking; call it off the UI thread).
        Safe while frames keep coming."""
        import cv2
        with self._lock:
            frames = list(self._buf)
            audio = list(self._abuf)
        if not frames:
            return RecorderStats(state="error", path=str(path), error="nothing to save yet")
        rec = Recorder(path, 30, encoders, queue_frames=16, audio=bool(audio), audio_queue=0)
        rec.start()
        for t, samples in audio:                    # queued before the frames; placed by time when the file opens
            rec.push_audio(samples, t)
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

    def __init__(self, pipeline, settings, log_event=None, encoders=ENCODERS, audio_factory=None):
        from vcstudio.core.audio import AudioSource
        self.pipeline, self.settings = pipeline, settings
        self._log = log_event or (lambda lv, m: None)
        self.encoders = encoders
        self.recorder: Recorder | None = None
        self.replay: InstantReplay | None = None
        self.last_path: Path | None = None
        self.audio = None                                # AudioSource while a recording / replay needs the mic
        self._audio_factory = audio_factory or AudioSource
        self._lock = threading.Lock()
        self.apply_settings()

    # ---- microphone
    def audio_device(self) -> str:
        return str(self.settings.get("recording.audio_device") or "").strip()

    def _on_audio(self, samples: np.ndarray, t: float) -> None:
        rec, rp = self.recorder, self.replay
        if rec is not None:
            rec.push_audio(samples, t)
        if rp is not None:
            rp.push_audio(samples, t)

    def _sync_audio(self) -> None:
        """Run the microphone exactly while a recording or the instant replay wants sound."""
        dev = self.audio_device()
        want = bool(dev) and (self.recording or self.replay is not None)
        cur = self.audio
        if cur is not None and (not want or cur.device != dev):
            cur.stop()
            self.audio = None
        if want and self.audio is None:
            src = self._audio_factory(dev)
            src.listeners.append(self._on_audio)
            src.start()
            self.audio = src
            self._log("INFO", f"microphone on: {dev}")

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
            rec = Recorder(p, fps, self.encoders, audio=bool(self.audio_device()))
            rec.start()
            self.recorder = rec
            self.pipeline.taps.append(rec.push)
        self._sync_audio()
        self._log("INFO", f"recording started: {p}" + (f" (sound: {self.audio_device()})" if rec.audio else ""))
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
        self._sync_audio()
        self.last_path = rec.path
        lv = "ERROR" if st.state == "error" else "INFO"
        sound = f", sound {st.audio_seconds:.1f} s" if rec.audio else ""
        mic_err = f" (microphone: {self.audio.error})" if self.audio is not None and self.audio.error else ""
        self._log(lv, f"recording stopped: {st.path} ({st.frames} frames, {st.seconds:.1f} s, {st.encoder or '-'}"
                      f", {st.dropped} dropped{sound}){' ' + st.error if st.error else ''}{mic_err}")
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
        self._sync_audio()

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
        if self.audio is not None:
            self.audio.stop()
            self.audio = None
