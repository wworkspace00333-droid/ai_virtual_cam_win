"""Video device enumeration and camera permission (macOS: AVFoundation; Windows: vcstudio.platform.win)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from vcstudio import CAMERA_NAME

log = logging.getLogger(__name__)
OUR_EXTENSION_MODEL_HINTS = (CAMERA_NAME,)


@dataclass(frozen=True)
class VideoDevice:
    unique_id: str
    name: str
    model_id: str
    is_own_output: bool   # True => our virtual camera; must never be used as input (feedback loop)

    @property
    def is_obs(self) -> bool:
        return "OBS" in self.name or "OBS" in self.model_id


def is_own_output(name: str, model_id: str) -> bool:
    return any(h.casefold() in (name.casefold(), model_id.casefold()) for h in OUR_EXTENSION_MODEL_HINTS)


def list_video_devices() -> list[VideoDevice]:
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        try:
            from vcstudio.platform.win.devices import list_video_devices as win_list
        except ImportError:
            return []              # Windows device enumeration arrives with porting stage 4
        return win_list()
    try:
        import AVFoundation as AV  # type: ignore
    except ImportError:
        log.error("PyObjC AVFoundation not installed; cannot enumerate devices")
        return []
    types = [AV.AVCaptureDeviceTypeExternal, AV.AVCaptureDeviceTypeBuiltInWideAngleCamera]
    for extra in ("AVCaptureDeviceTypeContinuityCamera", "AVCaptureDeviceTypeDeskViewCamera"):
        if hasattr(AV, extra):
            types.append(getattr(AV, extra))
    sess = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        types, AV.AVMediaTypeVideo, AV.AVCaptureDevicePositionUnspecified)
    out = []
    for d in sess.devices():
        name, model = str(d.localizedName()), str(d.modelID())
        out.append(VideoDevice(str(d.uniqueID()), name, model, is_own_output(name, model)))
    return out


def camera_authorization_status() -> str:
    from vcstudio.platform import IS_WINDOWS
    if IS_WINDOWS:
        from vcstudio.platform.win.privacy import camera_authorization_status as win_status
        return win_status()
    try:
        import AVFoundation as AV  # type: ignore
    except ImportError:
        return "unavailable"
    s = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}.get(int(s), str(s))
