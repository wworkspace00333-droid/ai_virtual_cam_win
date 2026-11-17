# Plugin SDK (API version 1)

Plugins are your own effects, written in Python, applied to the camera output: the picture apps receive. They run
after everything the app does itself (background, regions, replacements, auto-framing) and before the camera, the
recording and instant replay. Code: `vcstudio/plugins/`. Example: [`examples/plugins/vignette.py`](../examples/plugins/vignette.py).

## Installing a plugin

1. Settings → **Plugins** → **Open plugins folder** (`%LOCALAPPDATA%\AIVirtualCameraStudio\plugins`).
2. Copy the plugin's `.py` file there and click **Reload list**.
3. Tick the plugin. The app asks once whether you trust it; only then does its code run.

The approval is bound to the file's SHA-256: when the file changes (an update, an edit), the plugin is off again
until you tick it again. Starting the app with the environment variable `VCS_NO_PLUGINS=1` loads no plugin at all.

## Writing a plugin

One file, `<id>.py` (`id`: letters, digits, `_`, `-`; a name starting with `_` is ignored). At its top level:

```python
import numpy as np

API_VERSION = 1                       # plain constants: the app reads them WITHOUT running the file
NAME = "My effect"
DESCRIPTION = "What it does, one line."

def create(host):                     # called once when the plugin is switched on (or at app start)
    return MyEffect(host)

class MyEffect:
    def __init__(self, host):
        self.host = host

    def process(self, frame, info):
        # frame: read-only numpy view, shape (height, width, 4), dtype uint8, channels B, G, R, A
        # return a new array of the same shape and dtype, or None to leave the frame as it is
        out = frame.copy()
        out[..., 2] = np.minimum(255, frame[..., 2].astype(np.uint16) + 30)   # a little warmer
        return out

    def close(self):                  # optional: switched off, or the app closes
        pass
```

### `info` (FrameInfo)

| Field | Meaning |
|---|---|
| `pts` | the frame's time in seconds (monotonic) |
| `width`, `height` | output size in pixels |
| `scene` | name of the active scene (profile) |
| `objects` | tuple of `PluginObject(label, track_id, confidence, box)`; `box` = `(x0, y0, x1, y1)` in output pixels — the objects the AI tracks at this moment (`person`, `face`, COCO classes, open-vocabulary queries) |

### `host` (PluginHostAPI)

| Member | Meaning |
|---|---|
| `host.log(message, level="INFO")` | writes to the app's event journal (Diagnostics), prefixed with the plugin id |
| `host.options` | a dict saved with the app settings (`plugins.options.<id>`); keep it JSON-compatible |
| `host.plugin_id`, `host.api_version` | the plugin's id, the host's API version |

## Rules and limits

* **Time.** `process` runs on the output thread for every frame: at 60 fps the whole output has about 16 ms. The
  Plugins list shows a plugin as *slow* when it averages over 15 ms. Precompute what you can (see the example's
  cached vignette mask); do heavy work in your own thread and use its latest result.
* **Errors.** An exception, a result of the wrong shape or type: that frame goes on unchanged; three in a row and
  the plugin is switched off and the error recorded. It can not stop or freeze the camera through an exception.
  A plugin that blocks forever (an endless loop) does stall the output: Python offers no way to interrupt it.
* **Security.** A plugin is ordinary Python running inside the app, with your rights: it can read your files and
  use the network. There is no sandbox. Install only plugins whose source you have read or whose author you trust.
* **Compatibility.** `API_VERSION` changes only when the interface above changes in an incompatible way; a plugin
  with another version is listed as *incompatible* and not run.
* **Not covered by API 1:** settings UI for plugins, plugins that replace the detector, native (C/C++) plugins.
