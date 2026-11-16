"""A video file as the input ("input.method" = "video_file"): processed like a camera and sent to the output camera.

Decoding reuses the replacement-video decoders (vcstudio/replacement/video.py: PyAV on Windows, AVFoundation on
macOS). Frames are delivered at the file's own frame rate in real time, paced by the file's timestamps, so a 30 fps
clip plays at normal speed. At the end the clip starts again (loop, the default) or the last picture is held, so the
camera keeps showing something instead of reporting a lost signal. Larger than 1080p is scaled down while decoding
(the camera offers up to 1080p).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from vcstudio.core.frame import Frame, host_time_s
from vcstudio.core.video_capture import CaptureError, CaptureSource

if TYPE_CHECKING:
    from vcstudio.replacement.video import VideoInfo

log = logging.getLogger(__name__)

MAX_SIDE = 1920
VIDEO_FILTER = "*.mp4 *.mov *.m4v *.mkv *.webm *.avi *.wmv *.ts"


class VideoFileSource(CaptureSource):
    kind = "video_file"

    def __init__(self, path: str | Path, loop: bool = True, max_side: int = MAX_SIDE, start_s: float = 0.0):
        self.path = Path(path) if path else Path()
        self.loop = loop
        self.max_side = max_side
        self.start_s = max(0.0, float(start_s or 0.0))   # where playback (and every loop) begins
        self.name = f"Video file: {self.path.name}" if path else "Video file"
        self.info: VideoInfo | None = None
        self.loops = 0                      # how many times the clip restarted
        self.ended = False                  # reached the end without looping (holding the last picture)
        self.position = 0.0                 # media time of the last delivered frame, seconds
        self._paused = threading.Event()
        self._seek_to: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._nominal = (0, 0, 0.0)

    # ---------------- playback control (any thread; the reading thread acts on it)
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def duration(self) -> float:
        return float(self.info.duration) if self.info else 0.0

    def pause(self) -> None:
        """Freeze on the current picture (still delivered at the clip's rate, so the camera keeps a signal)."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def seek(self, seconds: float) -> None:
        """Continue from `seconds` (clamped to the clip); also leaves a held end picture."""
        d = self.duration
        self._seek_to = max(0.0, min(float(seconds), max(0.0, d - 0.05) if d > 0 else float(seconds)))

    def restart(self) -> None:
        self.seek(self.start_s)

    @property
    def nominal(self):
        return self._nominal

    def is_present(self) -> bool:
        return self.path.is_file()

    def start(self, on_frame, on_event) -> None:
        from vcstudio.replacement.video import VideoError, probe
        if not str(self.path) or not self.path.is_file():
            raise CaptureError(f"video file not found: {self.path.name or '(none chosen)'}")
        try:
            info = probe(self.path)
        except VideoError as e:
            raise CaptureError(str(e)) from e
        self.info = info
        fps = info.fps if 1 <= info.fps <= 240 else 30.0
        from vcstudio.replacement.video import _target_size
        w, h = _target_size(info, self.max_side)
        self._nominal = (w, h, fps)
        log.info("video file input: %s, %dx%d @ %.2f fps, %.1f s, %s (%s), from %.1f s", self.path.name, w, h, fps,
                 info.duration, info.codec, info.decoder, self.start_s)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._owned, args=(self._stop, info, fps, on_frame, on_event),
                                        name="video-file", daemon=True)
        self._thread.start()

    def _owned(self, stop, info, fps, on_frame, on_event) -> None:
        try:
            self._run(stop, info, fps, on_frame)
        except Exception as e:                       # noqa: BLE001 (reported; the supervisor reconnects)
            log.exception("video file input failed")
            on_event("disconnected", f"video file: {e}")

    def _run(self, stop, info: VideoInfo, fps: float, on_frame) -> None:
        from vcstudio.replacement.video import open_decoder
        period = 1.0 / fps
        seq = 0
        last = None
        if info.duration and self.start_s >= info.duration:
            self.start_s = 0.0                       # a start position past the end: play from the beginning
        pos = self.start_s

        def emit(img) -> None:
            nonlocal seq
            seq += 1
            now = host_time_s()
            on_frame(Frame(seq, now, now, img.shape[1], img.shape[0], array=img, source=self.name))

        def hold() -> None:                          # repeat the current picture for one frame period
            stop.wait(period)
            if last is not None and not stop.is_set():
                emit(last.copy())

        while not stop.is_set():
            dec = open_decoder(self.path, info, pos, self.max_side)
            t0, base, got, jump = time.perf_counter(), None, 0, None
            try:
                for pts, img in dec:
                    if stop.is_set():
                        return
                    while self._paused.is_set() and self._seek_to is None and not stop.is_set():
                        hold()
                        t0 += period                 # the clip's clock stands still while paused
                    if self._seek_to is not None:
                        jump, self._seek_to = self._seek_to, None
                        break
                    if base is None:
                        base = pts
                    delay = t0 + (pts - base) - time.perf_counter()
                    if delay > 0:
                        stop.wait(delay)
                    elif delay < -0.5:               # far behind (slow PC, debugger): catch up, keep real time
                        t0 = time.perf_counter() - (pts - base)
                    if not (img.flags.writeable and img.flags.c_contiguous):
                        img = img.copy()
                    img[..., 3] = 255                # input is opaque (premultiplied alpha over black)
                    got += 1
                    last = img
                    self.position = pts
                    emit(img)
            finally:
                dec.close()
            if jump is not None:
                pos, self.ended = jump, False
                continue
            if got == 0:
                raise CaptureError("no frames could be decoded")
            if self.loop:
                self.loops += 1
                pos = self.start_s
                continue
            self.ended = True
            while not stop.is_set() and self._seek_to is None:   # hold the last picture at the clip's rate
                hold()
            if self._seek_to is not None:
                pos, self._seek_to, self.ended = self._seek_to, None, False
                continue
            return

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3)
        self._thread = None
