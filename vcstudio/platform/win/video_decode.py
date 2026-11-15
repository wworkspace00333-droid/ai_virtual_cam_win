"""Video asset probing and decoding on Windows with PyAV (FFmpeg libraries bundled with the wheel).

Replaces AVFoundation/VideoToolbox. Behaviour matches the macOS decoder the player relies on:
  - codec reported as the container FourCC when there is one ("avc1", "ap4h"), else FFmpeg's codec name
  - frame-accurate start: seek to the keyframe before start_s, then decode and drop frames before it
  - output: BGRA uint8 at the requested size (downscaled when larger than needed), rotated upright,
    premultiplied when the stream has alpha (ProRes 4444, PNG/QuickTime Animation, ...)
Containers: MP4, MOV, M4V, MKV, WEBM, AVI. Decoding is software (FFmpeg); VideoInfo.hardware is False.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _rotation(stream, first_frame=None) -> int:
    """Clockwise degrees to display upright (like AVFoundation's preferredTransform)."""
    try:
        r = stream.metadata.get("rotate")
        if r is not None:
            return int(r) % 360
    except Exception:
        pass
    rot = getattr(first_frame, "rotation", 0) if first_frame is not None else 0   # display matrix, counter-clockwise
    return int(round(-float(rot or 0))) % 360


def _codec_name(stream) -> str:
    tag = ""
    try:
        tag = str(stream.codec_context.codec_tag or "")
    except Exception:
        pass
    tag = tag.strip("\x00 ")
    if len(tag) == 4 and tag.isprintable() and tag.isascii() and tag.strip():
        return tag
    return stream.codec_context.name


def _has_alpha(stream) -> bool:
    fmt = getattr(stream.codec_context, "pix_fmt", None) or ""
    return fmt.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya")) or fmt in ("pal8",)


def probe(path: Path):
    import av

    from vcstudio.replacement.video import VideoError, VideoInfo
    path = Path(path)
    if not path.exists():
        raise VideoError(f"File not found: {path.name}")
    try:
        with av.open(str(path)) as c:
            if not c.streams.video:
                raise VideoError(f"{path.name}: no video track")
            s = c.streams.video[0]
            fps = float(s.average_rate or s.guessed_rate or 30.0)
            if s.duration is not None and s.time_base is not None:
                dur = float(s.duration * s.time_base)
            elif c.duration is not None:
                dur = c.duration / 1e6
            else:
                dur = 0.0
            first = None
            try:
                first = next(c.decode(s))
            except (StopIteration, av.error.FFmpegError):
                pass
            w, h = s.codec_context.width, s.codec_context.height
            if first is not None and (not w or not h):
                w, h = first.width, first.height
            if not w or not h or first is None:
                raise VideoError(f"{path.name}: cannot decode the video track")
            return VideoInfo(int(w), int(h), round(fps, 3), round(dur, 3), _codec_name(s), _has_alpha(s), "pyav",
                             False, _rotation(s, first))
    except av.error.FFmpegError as e:
        raise VideoError(f"{path.name}: cannot open video ({e})") from e


class PyAVDecoder:
    """Iterates (pts_seconds, BGRA ndarray) from start_s; premultiplied if the stream has alpha."""

    def __init__(self, path: Path, info, start_s: float, max_side: int | None):
        import av

        from vcstudio.replacement.video import VideoError, _target_size
        self.info = info
        tw, th = _target_size(info, max_side)
        self._size = (th, tw) if info.rotation in (90, 270) else (tw, th)     # decode size before rotation
        try:
            self._c = av.open(str(path))
        except av.error.FFmpegError as e:
            raise VideoError(f"cannot open {Path(path).name}: {e}") from e
        self._s = self._c.streams.video[0]
        self._s.thread_type = "AUTO"
        self._start = max(0.0, float(start_s))
        self._half = 0.5 / max(info.fps, 1.0)
        if self._start > 0 and self._s.time_base:
            self._c.seek(int(self._start / self._s.time_base), stream=self._s, backward=True, any_frame=False)

    def _convert(self, frame) -> np.ndarray:
        from vcstudio.replacement.video import _premultiply
        w, h = self._size
        if (frame.width, frame.height) != (w, h):
            img = frame.reformat(width=w, height=h, format="bgra").to_ndarray()
        else:
            img = frame.to_ndarray(format="bgra")
        img = np.ascontiguousarray(img)
        if self.info.rotation:
            img = np.ascontiguousarray(np.rot90(img, k=-(self.info.rotation // 90)))
        if self.info.has_alpha:
            img = _premultiply(img)
        return img

    def __iter__(self):
        """Starts with the frame that is on screen at start_s (as AVAssetReader does): the last frame whose
        timestamp is <= start_s (within half a frame), even when it is the final frame of the file."""
        import av

        from vcstudio.replacement.video import VideoError
        limit = self._start + self._half
        pending = None                                    # (t, frame): candidate covering start_s
        started = False
        try:
            for frame in self._c.decode(self._s):
                if frame.pts is None:
                    continue
                t = float(frame.pts * self._s.time_base)
                if not started:
                    if t <= limit:                        # still at or before start_s (after a keyframe seek)
                        pending = (t, frame)
                        continue
                    started = True
                    if pending is not None:
                        yield pending[0], self._convert(pending[1])
                        pending = None
                yield t, self._convert(frame)
            if not started and pending is not None:       # start_s falls on the last frame of the file
                yield pending[0], self._convert(pending[1])
        except av.error.FFmpegError as e:
            raise VideoError(f"decode failed: {e}") from e

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass
