"""Example plugin for AI Virtual Camera Studio (Plugin SDK API 1, docs/PLUGIN_SDK.md).

Darkens the corners of the picture and draws a thin frame around each detected person. Copy this file into the
plugins folder (Settings → Plugins → Open plugins folder) and switch it on there.
"""
import numpy as np

API_VERSION = 1
NAME = "Vignette"
DESCRIPTION = "Darkens the corners and outlines detected people (example plugin)."


def create(host):
    host.options.setdefault("strength", 0.45)          # saved with the app settings
    return Vignette(host)


class Vignette:
    def __init__(self, host):
        self.host = host
        self._mask = None
        self._size = None

    def _weights(self, h, w):
        if self._size != (h, w):                        # computed once per output size, not per frame
            y = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
            x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
            r = np.clip((x * x + y * y) / 2, 0, 1)
            k = float(self.host.options.get("strength", 0.45))
            self._mask = (1 - k * r)[..., None]
            self._size = (h, w)
        return self._mask

    def process(self, frame, info):
        h, w = frame.shape[:2]
        out = frame.copy()                              # the frame is read-only: return a new picture
        out[..., :3] = (frame[..., :3] * self._weights(h, w)).astype(np.uint8)
        for o in info.objects:
            if o.label != "person":
                continue
            x0, y0, x1, y1 = (int(v) for v in o.box)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w - 1, x1), min(h - 1, y1)
            if x1 > x0 and y1 > y0:
                out[y0, x0:x1, :3] = out[y1, x0:x1, :3] = (80, 200, 255)
                out[y0:y1, x0, :3] = out[y0:y1, x1, :3] = (80, 200, 255)
        return out

    def close(self):
        self.host.log("closed")
