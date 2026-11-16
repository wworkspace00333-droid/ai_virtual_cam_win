"""Global (system-wide) hotkeys on Windows through RegisterHotKey.

Same interface as the macOS implementation (vcstudio/native/hotkeys.py): apply(bindings, on_action)
returns {action: error} for bindings that could not be registered, clear(), count, available.

RegisterHotKey posts WM_HOTKEY to the message queue of the thread that registered the key, so a
dedicated thread owns all registrations and runs a GetMessage loop. Requests from other threads are
handed over with PostThreadMessage and waited for. `on_action` is called on the hotkey thread; the
main window re-emits it through a Qt signal so the action runs on the GUI thread.

Qt portable key text ("Ctrl+Alt+Right") maps to MOD_* flags and virtual-key codes. As on macOS a
modifier is required, so plain keys are never taken system-wide. Qt's "Meta" is the Windows key.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
QT_MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "meta": MOD_WIN}
WM_HOTKEY, WM_QUIT, WM_APP = 0x0312, 0x0012, 0x8000
WM_VCS_CALL = WM_APP + 1
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

NAMED_KEYS = {
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "SPACE": 0x20, "RETURN": 0x0D, "ENTER": 0x0D,
    "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B, "BACKSPACE": 0x08, "DEL": 0x2E, "DELETE": 0x2E, "INS": 0x2D,
    "INSERT": 0x2D, "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDOWN": 0x22, "PAUSE": 0x13, "PRINT": 0x2C,
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
_user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
_user32.VkKeyScanW.restype = ctypes.c_short


def parse(seq: str) -> tuple[int, int] | None:
    """Qt portable key sequence -> (virtual key, MOD_* flags); None if unmappable or without a modifier."""
    if not seq:
        return None
    parts = seq.split("+")
    key = parts[-1].strip() or "+"
    mods = 0
    for p in parts[:-1]:
        m = QT_MODS.get(p.strip().lower())
        if m is None:
            return None
        mods |= m
    if mods == 0:                       # never steal plain keys system-wide
        return None
    k = key.upper()
    if k in NAMED_KEYS:
        return NAMED_KEYS[k], mods
    if len(key) == 1:
        if "A" <= k <= "Z" or "0" <= k <= "9":
            return ord(k), mods
        vk = _user32.VkKeyScanW(key)
        if vk != -1 and (vk >> 8) & 0xFF == 0:   # reachable without an extra Shift/Ctrl/Alt on this layout
            return vk & 0xFF, mods
    return None


class GlobalHotkeys:
    def __init__(self):
        self.available = True
        self.errors: dict[str, str] = {}
        self._ids: dict[int, str] = {}
        self._on_action: Callable[[str], None] | None = None
        self._calls: queue.Queue = queue.Queue()
        self._tid = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="global-hotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.available = False

    # ------------------------------------------------------------------ hotkey thread
    def _run(self) -> None:
        msg = wintypes.MSG()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)    # create this thread's message queue
        self._tid = _kernel32.GetCurrentThreadId()
        self._ready.set()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                action = self._ids.get(int(msg.wParam))
                cb = self._on_action
                if action and cb:
                    try:
                        cb(action)
                    except Exception:
                        log.exception("global hotkey handler failed")
            elif msg.message == WM_VCS_CALL:
                while True:
                    try:
                        fn, done, box = self._calls.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        box.append(fn())
                    except Exception as e:     # pragma: no cover - reported to the caller
                        box.append(e)
                    done.set()
        self._unregister_all()

    def _call(self, fn):
        """Run fn on the hotkey thread (registrations belong to it) and return its result."""
        if not self.available or not self._thread.is_alive():
            raise RuntimeError("hotkey thread not running")
        done = threading.Event()
        box: list = []
        self._calls.put((fn, done, box))
        _user32.PostThreadMessageW(self._tid, WM_VCS_CALL, 0, 0)
        if not done.wait(5):
            raise RuntimeError("hotkey thread did not respond")
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]

    def _unregister_all(self) -> None:
        for hid in list(self._ids):
            _user32.UnregisterHotKey(None, hid)
        self._ids = {}

    # ------------------------------------------------------------------ public API
    def apply(self, bindings: dict[str, str], on_action: Callable[[str], None]) -> dict[str, str]:
        self.errors = {}
        if not self.available:
            return {a: "hotkey thread unavailable" for a in bindings}
        self._on_action = on_action

        def register() -> dict[str, str]:
            errors: dict[str, str] = {}
            self._unregister_all()
            for i, (action, seq) in enumerate(sorted(bindings.items()), start=1):
                if not seq:
                    continue
                p = parse(seq)
                if p is None:
                    errors[action] = f"'{seq}' cannot be a global hotkey (needs a modifier and a standard key)"
                    continue
                vk, mods = p
                if not _user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                    err = ctypes.get_last_error()
                    errors[action] = (f"'{seq}' is already used by the system or another app"
                                      if err == ERROR_HOTKEY_ALREADY_REGISTERED else f"'{seq}': RegisterHotKey error {err}")
                    continue
                self._ids[i] = action
            return errors

        self.errors = self._call(register)
        log.info("Global hotkeys registered: %d (%d errors)", len(self._ids), len(self.errors))
        return self.errors

    def clear(self) -> None:
        if self.available and self._thread.is_alive():
            self._call(self._unregister_all)

    @property
    def count(self) -> int:
        return len(self._ids)

    def close(self) -> None:
        if self._thread.is_alive():
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
