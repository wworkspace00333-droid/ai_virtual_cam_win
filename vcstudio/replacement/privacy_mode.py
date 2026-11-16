"""Privacy mode: one switch that hides what should not leave the PC, in levels.

  off
  faces        every detected face pixelated (the "Hide face" region)
  screens      + monitors, laptops, phones and books pixelated (COCO classes tv, laptop, cell phone, book): their
                 screens or pages can show private information
  all          + the background behind people blurred

It edits the active profile (scene) with regions of its own (ids "quickface", "privacy_screens") and remembers the
background it replaced, so switching privacy off restores the scene as it was. Regions the user made are left alone.
"""
from __future__ import annotations

import copy

from vcstudio.roi.model import QUICK_FACE_ID, ROI, quick_face_roi

LEVELS = ("off", "faces", "screens", "all")
LABELS = {"off": "Privacy off", "faces": "Hide faces", "screens": "Hide faces and screens",
          "all": "Hide faces, screens and background"}
SCREEN_ID = "privacy_screens"
SCREEN_CLASSES = ["tv", "laptop", "cell phone", "book"]


def screens_roi() -> ROI:
    from vcstudio.replacement.spec import ReplacementSpec
    r = ROI(id=SCREEN_ID, name="Hide screens", mode="auto", classes=list(SCREEN_CLASSES), padding=0.08, priority=90)
    r.replacement = ReplacementSpec(enabled=True, mode="effect", effect="pixelate", pixel_size=32,
                                    feather_px=4.0).to_dict()
    return r


def level_of(body: dict) -> str:
    p = body.get("privacy") or {}
    lv = p.get("level", "off")
    return lv if lv in LEVELS else "off"


def set_level(body: dict, level: str) -> None:
    """Apply a privacy level to a profile body in place."""
    if level not in LEVELS:
        raise ValueError(f"privacy level must be one of {LEVELS}")
    p = body.setdefault("privacy", {})
    before = level_of(body)
    if before == "off" and level != "off":
        p["saved_background"] = copy.deepcopy(body.get("background") or {"mode": "off"})
    rois = [r for r in body.get("rois", []) if r.get("id") not in (QUICK_FACE_ID, SCREEN_ID)]
    if level in ("faces", "screens", "all"):
        rois.append(quick_face_roi().to_dict())
        body.setdefault("ai", {})["face_detection"] = "vision"
        body["ai"]["enabled"] = True
    if level in ("screens", "all"):
        rois.append(screens_roi().to_dict())
    body["rois"] = rois
    if level == "all":
        bg = dict(body.get("background") or {})
        bg.update(mode="blur", strength=max(25, int(bg.get("strength", 25))))
        body["background"] = bg
    elif "saved_background" in p:
        body["background"] = copy.deepcopy(p["saved_background"])
    if level == "off":
        p.pop("saved_background", None)
    p["level"] = level
    if level != "off":
        p["last_on"] = level


def toggle(body: dict) -> str:
    """Off -> the level used last (default faces and screens); on -> off. Returns the new level."""
    new = "off" if level_of(body) != "off" else (body.get("privacy") or {}).get("last_on", "screens")
    set_level(body, new if new in LEVELS else "screens")
    return level_of(body)
