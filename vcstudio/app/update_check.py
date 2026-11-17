"""Checking for a newer version — only when the user clicks "Check for updates" (Settings page).

One HTTPS GET to the GitHub releases list of this project; nothing is sent besides what any web request carries
(no identifiers, no settings, no statistics), nothing is downloaded or installed, and nothing runs in the background
or at start-up. Off when VCS_NO_NETWORK=1 or the setting privacy.allow_update_check is false (the button is then
disabled). The result only says whether a newer release exists and links to its page.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Callable

REPO = "wworkspace00333-droid/ai_virtual_cam_win"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
PAGE_PREFIX = f"https://github.com/{REPO}/releases/"
TIMEOUT_S = 15
MAX_BYTES = 2_000_000


class UpdateCheckError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    current: str
    latest: str = ""
    url: str = ""
    newer: bool = False
    prerelease: bool = False


def allowed(settings=None) -> bool:
    if os.environ.get("VCS_NO_NETWORK", "").strip() not in ("", "0"):
        return False
    if settings is not None:
        try:
            return bool(settings.get("privacy.allow_update_check"))
        except KeyError:
            return True
    return True


def parse_version(tag: str) -> tuple[int, ...] | None:
    """'v0.4.1' / '0.5.0' -> (0, 4, 1); anything else (a test tag, '0.5.0-rc1') -> None and is ignored."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release(releases: list) -> tuple[tuple[int, ...], dict] | None:
    best = None
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        v = parse_version(str(r.get("tag_name", "")))
        if v is not None and (best is None or v > best[0]):
            best = (v, r)
    return best


def _default_opener(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "ai-virtual-camera-studio-update-check"})
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)          # noqa: S310 (fixed https URL)


def check(current: str, opener: Callable[[str], object] | None = None) -> UpdateInfo:
    """Raises UpdateCheckError on a network or format problem."""
    opener = opener or _default_opener
    try:
        resp = opener(API_URL)
        with resp as r:                                             # type: ignore[attr-defined]
            raw = r.read(MAX_BYTES + 1)
    except Exception as e:
        raise UpdateCheckError(f"{type(e).__name__}: {e}") from e
    if len(raw) > MAX_BYTES:
        raise UpdateCheckError("unexpectedly large answer")
    try:
        releases = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateCheckError(f"not a release list: {e}") from e
    if not isinstance(releases, list):
        raise UpdateCheckError("not a release list")
    info = UpdateInfo(current=current)
    best = latest_release(releases)
    if best is None:
        return info
    v, r = best
    info.latest = str(r.get("tag_name", "")).lstrip("v")
    url = str(r.get("html_url", ""))
    info.url = url if url.startswith(PAGE_PREFIX) else PAGE_PREFIX      # never link anywhere else
    info.prerelease = bool(r.get("prerelease"))
    cur = parse_version(current)
    info.newer = cur is not None and v > cur
    return info
