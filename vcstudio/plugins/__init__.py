"""Plugin SDK (API version 1): your own Python effects on the camera output. See docs/PLUGIN_SDK.md.

A plugin is one .py file in the plugins folder (%LOCALAPPDATA%\\AIVirtualCameraStudio\\plugins; Settings → Plugins →
Open folder). Its top level must define plain constants and a factory:

    API_VERSION = 1
    NAME = "Vignette"
    DESCRIPTION = "Darkens the corners."

    def create(host):                 # host: PluginHostAPI — log(), options (a dict saved with the app settings)
        return Vignette()

    class Vignette:
        def process(self, frame, info):   # frame: read-only (H, W, 4) uint8 BGRA view of the finished output
            ...                           # info: FrameInfo (pts, width, height, scene, objects)
            return new_frame              # a new (H, W, 4) uint8 array, or None to leave the frame unchanged
        def close(self):                  # optional
            ...

Safety rules of the host (vcstudio/plugins/host.py):
  * listing plugins never runs their code: NAME / DESCRIPTION / API_VERSION are read from the file's syntax tree;
  * a plugin runs only after the user switched it on; the approval is bound to the file's SHA-256, so a changed
    file is off again until approved again;
  * every call is guarded: an exception, a wrong result or 3 failures in a row switch the plugin off and are recorded
    in the journal; the frame goes on unchanged — a plugin can not stop the camera;
  * plugins run in the app's process with the app's rights (Python can not sandbox Python): install only plugins
    whose source you have read or trust.
"""
from vcstudio.plugins.host import (API_VERSION, FrameInfo, PluginHostAPI, PluginInfo, PluginManager,  # noqa: F401
                                   PluginObject)
