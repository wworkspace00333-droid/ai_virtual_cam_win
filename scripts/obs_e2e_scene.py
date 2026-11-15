"""Create an OBS scene collection and profile "VCS-E2E" for the end-to-end test (OBS -> plugin -> AI -> camera).

The scene is 1920x1080 at 60 fps: a dark background and the bus photo (people the AI detects) in the middle,
placed exactly like the synthetic scene of `--pipeline-e2e-test`. Existing collections and profiles are not
touched. Then start OBS with it:

    python scripts/obs_e2e_scene.py [--image path] [--fps 60]
    "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe" --collection VCS-E2E --profile VCS-E2E --disable-shutdown-check

(start obs64.exe from its own folder: OBS looks for its data relative to the working directory).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "VCS-E2E"
BG_ABGR = 0xFF1E3C1E
BG_BGR = (0x1E, 0x3C, 0x1E)          # what the app and camera clients see outside the photo


def _item(name: str, src_uuid: str, item_id: int, x: float, y: float) -> dict:
    return {"name": name, "source_uuid": src_uuid, "visible": True, "locked": False, "rot": 0.0, "align": 5,
            "bounds_type": 0, "bounds_align": 0, "bounds_crop": False, "crop_left": 0, "crop_top": 0,
            "crop_right": 0, "crop_bottom": 0, "id": item_id, "group_item_backup": False, "pos": {"x": x, "y": y},
            "scale": {"x": 1.0, "y": 1.0}, "bounds": {"x": 0.0, "y": 0.0}, "scale_filter": "disable",
            "blend_method": "default", "blend_type": "normal", "private_settings": {}}


def collection(image: Path) -> dict:
    bg, img, scene = (str(uuid.uuid4()) for _ in range(3))
    sources = [
        {"name": "Background", "uuid": bg, "id": "color_source_v3", "versioned_id": "color_source_v3",
         # OBS colours are 0xAABBGGRR: dark green (B 30, G 60, R 30), distinct from the camera's waiting card
         "settings": {"color": BG_ABGR, "width": 1920, "height": 1080}, "enabled": True},
        {"name": "Bus photo", "uuid": img, "id": "image_source", "versioned_id": "image_source",
         "settings": {"file": str(image)}, "enabled": True},
        {"name": "Scene", "uuid": scene, "id": "scene", "versioned_id": "scene", "enabled": True,
         "settings": {"id_counter": 2, "custom_size": False,
                      "items": [_item("Background", bg, 1, 0, 0), _item("Bus photo", img, 2, 555, 0)]}},
    ]
    return {"name": NAME, "sources": sources, "groups": [], "scene_order": [{"name": "Scene"}],
            "current_scene": "Scene", "current_program_scene": "Scene", "current_transition": "Fade",
            "transition_duration": 300, "transitions": [], "quick_transitions": [], "saved_projectors": [],
            "preview_locked": False, "modules": {}, "version": 1}


def profile(fps: int) -> str:
    return (f"[General]\nName={NAME}\n\n[Video]\nBaseCX=1920\nBaseCY=1080\nOutputCX=1920\nOutputCY=1080\n"
            f"FPSType=1\nFPSInt={fps}\nFPSCommon={fps}\nFPSNum={fps}\nFPSDen=1\nScaleType=bicubic\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", type=Path, default=ROOT / "tests" / "data" / "bus.jpg")
    ap.add_argument("--fps", type=int, default=60)
    a = ap.parse_args(argv)
    base = Path(os.environ["APPDATA"]) / "obs-studio" / "basic"
    scenes, prof = base / "scenes", base / "profiles" / NAME
    scenes.mkdir(parents=True, exist_ok=True); prof.mkdir(parents=True, exist_ok=True)
    (scenes / f"{NAME}.json").write_text(json.dumps(collection(a.image.resolve()), indent=2), encoding="utf-8")
    (prof / "basic.ini").write_text(profile(a.fps), encoding="utf-8")
    print(f"wrote {scenes / (NAME + '.json')} and {prof / 'basic.ini'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
