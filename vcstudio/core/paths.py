"""Filesystem locations. Everything is local; override root with VCSTUDIO_HOME.

macOS: ~/Library/Application Support/AIVirtualCameraStudio
Windows: %LOCALAPPDATA%\\AIVirtualCameraStudio (models and assets are large, so not the roaming profile)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from vcstudio.platform import IS_WINDOWS

APP_DIR_NAME = "AIVirtualCameraStudio"


def _default_root() -> Path:
    env = os.environ.get("VCSTUDIO_HOME")
    if env:
        return Path(env).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_DIR_NAME
    return Path.home() / "Library" / "Application Support" / APP_DIR_NAME


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def database_file(self) -> Path:
        return self.root / "studio.db"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def ensure(self) -> "AppPaths":
        for p in (self.root, self.profiles_dir, self.logs_dir, self.models_dir, self.assets_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(_default_root())
