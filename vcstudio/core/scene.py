"""Scenes: one-key switching between complete looks, applied atomically to the video.

A scene is a profile (regions, replacements, assets, background, AI settings) plus a little scene data in the
profile body under "scene":
  slot   1..9   the hotkey Ctrl+Alt+<slot> switches to it (without explicit slots, the n-th profile in list order)
  input  null | {"method": ..., "device_unique_id"/"device_name_hint"/"video_path"/"video_loop"/"screen_target": ...}
         an input of its own (a camera, a file, a screen): switching to the scene switches the input too

Atomic scene state. The output thread used to read regions, replacement specs and the background from three services,
each with its own lock, and a profile switch updated them one after another: a frame rendered in between could
combine the new regions with the old replacements or the old background. Now the app builds one immutable SceneState
(regions + specs + assets + background config and its decoded image) after everything for the new scene is loaded
and publishes it with a single assignment; the output thread takes that one reference at the start of every frame
and renders the whole frame from it. A frame is therefore entirely the old scene or entirely the new one; the
version of the scene is recorded in each output frame (`frame.meta["scene"]`). Detection keeps running across a
switch (the tracker is shared), and an input change re-opens the capture, so frames of the old input can show for
the moment the new one takes to open.
"""
from __future__ import annotations

import itertools
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SLOTS = range(1, 10)
INPUT_KEYS = ("method", "device_unique_id", "device_name_hint", "video_path", "video_loop", "video_start_s",
              "screen_target")
_versions = itertools.count(1)


@dataclass(frozen=True)
class SceneState:
    """Everything the output thread needs to draw one frame of a scene. Never mutated after publishing."""
    version: int
    name: str
    rois: tuple = ()
    specs: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)
    bg_cfg: Any = None                       # replacement.background.BackgroundConfig
    bg_image: Any = None                     # decoded background image (numpy) or None
    framing: Any = None                      # replacement.framing.FramingConfig
    lighting: Any = None                     # replacement.lighting.LightingConfig

    @property
    def replacement_active(self) -> bool:
        return any(s.enabled and (s.mode == "effect" or s.asset_id in self.assets) for s in self.specs.values())

    @property
    def background_active(self) -> bool:
        c = self.bg_cfg
        return c is not None and c.mode != "off" and (c.mode != "image" or self.bg_image is not None)


def build_state(name: str, rois, replacement, background, body: dict | None = None) -> SceneState:
    """Snapshot of the services after they loaded a profile (called before publishing)."""
    from vcstudio.replacement.framing import FramingConfig
    from vcstudio.replacement.lighting import LightingConfig
    specs, assets = replacement.state()
    cfg, image = background.snapshot() if background is not None else (None, None)
    return SceneState(next(_versions), name or "", tuple(rois.rois()), specs, assets, cfg, image,
                      FramingConfig.from_profile(body or {}), LightingConfig.from_profile(body or {}))


def scene_data(body: dict) -> dict:
    d = body.get("scene") or {}
    slot = d.get("slot")
    inp = d.get("input")
    return {"slot": slot if slot in SLOTS else None,
            "input": {k: v for k, v in inp.items() if k in INPUT_KEYS} if isinstance(inp, dict) and inp else None}


class SceneManager:
    """Scene slots, per-scene inputs and thumbnails on top of the profile store."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.thumbnails: dict[str, Any] = {}          # scene name -> small BGRA preview of its output
        self._lock = threading.Lock()

    # ---------------- slots
    def slot_of(self, name: str) -> int | None:
        try:
            return scene_data(self.ctx.profiles.get(name)["body"])["slot"]
        except Exception:
            return None

    def name_for_slot(self, slot: int) -> str | None:
        names = self.ctx.profiles.names()
        for n in names:
            if self.slot_of(n) == slot:
                return n
        explicit = {self.slot_of(n) for n in names} - {None}
        free = [n for n in names if self.slot_of(n) is None]    # without explicit slots: list order, free slots only
        free_slots = [s for s in SLOTS if s not in explicit]
        if slot in free_slots:
            i = free_slots.index(slot)
            return free[i] if i < len(free) else None
        return None

    def set_slot(self, name: str, slot: int | None) -> None:
        if slot is not None and slot not in SLOTS:
            raise ValueError(f"slot must be 1..9, got {slot}")
        for n in self.ctx.profiles.names():                    # a slot belongs to one scene
            if n != name and slot is not None and self.slot_of(n) == slot:
                self._update(n, lambda b: b.setdefault("scene", {}).__setitem__("slot", None))
        self._update(name, lambda b: b.setdefault("scene", {}).__setitem__("slot", slot))

    # ---------------- per-scene input
    def input_of(self, name: str) -> dict | None:
        try:
            return scene_data(self.ctx.profiles.get(name)["body"])["input"]
        except Exception:
            return None

    def set_input(self, name: str, override: dict | None) -> None:
        clean = {k: v for k, v in (override or {}).items() if k in INPUT_KEYS} or None
        self._update(name, lambda b: b.setdefault("scene", {}).__setitem__("input", clean))

    def capture_current_input(self) -> dict:
        i = self.ctx.settings.get("input")
        return {k: i.get(k) for k in INPUT_KEYS if k in i}

    # ---------------- switching
    def switch(self, name: str) -> None:
        """Switch to a scene: its input first (if it has its own), then its look (published atomically)."""
        self.remember_thumbnail()
        override = self.input_of(name)
        if override:
            self._apply_input(override)
        self.ctx.profiles.switch(name)
        log.info("Scene: %s", name)

    def switch_slot(self, slot: int) -> str | None:
        name = self.name_for_slot(slot)
        if name is not None and name != self.ctx.profiles.active_name:
            self.switch(name)
        return name

    def _apply_input(self, override: dict) -> None:
        s = self.ctx.settings
        cur = s.get("input")
        changed = {k: v for k, v in override.items() if cur.get(k) != v}
        if not changed:
            return
        for k, v in changed.items():
            s.set(f"input.{k}", v, persist=False)
        s.save()
        cap = self.ctx.capture
        if cap.running:                                        # re-open the capture with the scene's input
            cap.stop()
            cap.start(self.ctx.capture_factory())

    # ---------------- thumbnails
    def remember_thumbnail(self) -> None:
        """Keep a small picture of the current output for the active scene (shown on the Scenes page)."""
        name = self.ctx.profiles.active_name
        pair, _ = self.ctx.pipeline.processed.peek()
        if not name or pair is None:
            return
        try:
            img = pair[1].downscaled_copy(320)
        except Exception:
            return
        with self._lock:
            self.thumbnails[name] = img

    def thumbnail(self, name: str):
        with self._lock:
            return self.thumbnails.get(name)

    def _update(self, name: str, fn) -> None:
        import copy
        body = copy.deepcopy(self.ctx.profiles.get(name)["body"])
        fn(body)
        self.ctx.profiles.update_body(name, body)
