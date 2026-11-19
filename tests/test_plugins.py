"""Plugin SDK (roadmap P3.3): listing runs no code, approval bound to SHA-256, guarded calls (a failing plugin is
switched off, the frame goes on), plugins in the pipeline, the example plugin, the Settings card."""
from __future__ import annotations

import shutil
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest

from vcstudio.plugins import FrameInfo, PluginManager, PluginObject

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "vignette.py"


class FakeSettings:
    def __init__(self):
        self.d = {"plugins.approved": {}, "plugins.options": {}}

    def get(self, k):
        import copy
        return copy.deepcopy(self.d.get(k))

    def set(self, k, v):
        self.d[k] = v


def write(folder: Path, name: str, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{name}.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


INVERT = """
    import numpy as np
    API_VERSION = 1
    NAME = "Invert"
    DESCRIPTION = "inverts colours"
    def create(host):
        host.options["created"] = host.options.get("created", 0) + 1
        return P()
    class P:
        closed = False
        def process(self, frame, info):
            out = frame.copy(); out[..., :3] = 255 - frame[..., :3]; return out
        def close(self):
            P.closed = True
"""


def frame(v=50, w=64, h=48):
    a = np.full((h, w, 4), v, np.uint8)
    a[..., 3] = 255
    return a


INFO = FrameInfo(0.0, 64, 48)


def test_listing_never_runs_plugin_code(tmp_path):
    marker = tmp_path / "ran.txt"
    write(tmp_path / "pl", "sneaky", f"""
        open(r"{marker}", "w").write("x")
        API_VERSION = 1
        NAME = "Sneaky"
        def create(host):
            return None
    """)
    pm = PluginManager(tmp_path / "pl", FakeSettings())
    infos = pm.discover()
    assert [i.name for i in infos] == ["Sneaky"] and infos[0].state == "off"
    assert not marker.exists()


def test_incompatible_and_invalid_files_are_listed_not_run(tmp_path):
    pl = tmp_path / "pl"
    write(pl, "old", "API_VERSION = 0\nNAME='Old'\ndef create(h):\n    return None\n")
    write(pl, "broken", "API_VERSION = 1\ndef create(:\n")
    write(pl, "nocreate", "API_VERSION = 1\nNAME='X'\n")
    write(pl, "_private", INVERT)
    pm = PluginManager(pl, FakeSettings())
    st = {i.id: i.state for i in pm.discover()}
    assert st == {"broken": "invalid", "nocreate": "invalid", "old": "incompatible"}
    assert pm.enable("old").state == "incompatible" and not pm.active


def test_enable_runs_and_approval_is_bound_to_the_file(tmp_path):
    pl = tmp_path / "pl"
    p = write(pl, "invert", INVERT)
    s = FakeSettings()
    pm = PluginManager(pl, s)
    pm.discover()
    info = pm.enable("invert")
    assert info.state == "running" and pm.active
    assert s.d["plugins.approved"]["invert"] == info.sha256
    out = pm.run(frame(50), INFO)
    assert out[0, 0, 0] == 205 and out[0, 0, 3] == 255
    # a new manager (next app start) starts it again; a changed file is NOT started
    pm.close()
    pm2 = PluginManager(pl, s)
    pm2.start_approved()
    assert pm2.active and s.d["plugins.options"]["invert"]["created"] == 1     # options saved at close
    pm2.close()
    p.write_text(p.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    pm3 = PluginManager(pl, s)
    pm3.start_approved()
    assert not pm3.active and not pm3.infos["invert"].approved
    # changed while running: the next listing stops it
    pm3.enable("invert")
    assert pm3.active
    p.write_text(p.read_text(encoding="utf-8") + "\n# again\n", encoding="utf-8")
    pm3.discover()
    assert not pm3.active


def test_failing_plugin_is_switched_off_and_the_frame_goes_on(tmp_path):
    pl = tmp_path / "pl"
    write(pl, "bad", """
        API_VERSION = 1
        NAME = "Bad"
        def create(host):
            return P()
        class P:
            n = 0
            def process(self, frame, info):
                P.n += 1
                if P.n == 1:
                    frame[0, 0, 0] = 1          # the frame is read-only: raises
                if P.n == 2:
                    return frame[:10]           # wrong shape
                raise SystemExit("careless")    # even SystemExit must not end the app
    """)
    write(pl, "invert", INVERT)
    events = []
    pm = PluginManager(pl, FakeSettings(), lambda lv, m: events.append((lv, m)))
    pm.discover()
    pm.enable("bad"); pm.enable("invert")
    src = frame(50)
    for _ in range(3):
        out = pm.run(src, INFO)
        assert out[0, 0, 0] == 205                  # the other plugin still ran; the input was not modified
    assert src[0, 0, 0] == 50
    assert pm.infos["bad"].state == "error" and pm.infos["bad"].failures == 3
    assert [lp.info.id for lp in pm._active] == ["invert"]
    assert any(lv == "ERROR" and "switched off" in m for lv, m in events)
    pm.discover()
    assert pm.infos["bad"].state == "error"         # stays failed until switched on again


def test_plugin_that_fails_to_start(tmp_path):
    pl = tmp_path / "pl"
    write(pl, "boom", "API_VERSION = 1\nNAME='Boom'\ndef create(host):\n    raise RuntimeError('no')\n")
    write(pl, "noproc", "API_VERSION = 1\nNAME='N'\ndef create(host):\n    return object()\n")
    pm = PluginManager(pl, FakeSettings())
    pm.discover()
    assert pm.enable("boom").state == "error" and "no" in pm.infos["boom"].error
    assert pm.enable("noproc").state == "error"
    assert not pm.active


def test_disable_calls_close(tmp_path):
    pl = tmp_path / "pl"
    write(pl, "invert", INVERT)
    s = FakeSettings()
    pm = PluginManager(pl, s)
    pm.discover()
    pm.enable("invert")
    mod = pm._active[0].obj.__class__
    pm.disable("invert")
    assert mod.closed and not pm.active and "invert" not in s.d["plugins.approved"]


def test_example_plugin(tmp_path):
    pl = tmp_path / "pl"
    pl.mkdir()
    shutil.copy(EXAMPLE, pl / "vignette.py")
    pm = PluginManager(pl, FakeSettings())
    infos = pm.discover()
    assert infos[0].name == "Vignette" and infos[0].state == "off"
    assert pm.enable("vignette").state == "running"
    f = frame(200, 320, 180)
    info = FrameInfo(0.0, 320, 180, "s", (PluginObject("person", 1, 0.9, (100, 40, 200, 160)),))
    out = pm.run(f, info)
    assert out[90, 160, 0] > out[2, 2, 0]                        # corners darker than the centre
    assert tuple(out[40, 150, :3]) == (80, 200, 255)             # outline of the person
    t0 = time.perf_counter()
    for _ in range(20):
        pm.run(frame(200, 1280, 720), FrameInfo(0.0, 1280, 720))
    assert (time.perf_counter() - t0) / 20 < 0.1                 # 720p: well under 100 ms on the 2-core server


def test_plugins_in_the_pipeline(paths, tmp_path):
    from vcstudio.app.application import AppContext
    write(paths.root / "plugins", "invert", INVERT)
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.settings.set("input.method", "test_pattern")
        ctx.settings.set("output.camera_enabled", False)
        ctx.plugins.discover()
        ctx.plugins.enable("invert")
        ctx.start_capture()
        end = time.monotonic() + 10
        while time.monotonic() < end and ctx.plugins.infos["invert"].calls < 5:
            time.sleep(0.05)
        assert ctx.plugins.infos["invert"].calls >= 5
        pair, _ = ctx.pipeline.processed.peek()
        src, out = pair
        assert out.source.endswith("+plugins")
        with src.bgra() as a, out.bgra() as b:
            if a.shape == b.shape:
                assert int(a[5, 5, 0]) + int(b[5, 5, 0]) == 255
        ctx.pipeline.set_passthrough(True, "test")              # emergency passthrough bypasses plugins
        time.sleep(0.3)
        pair, _ = ctx.pipeline.processed.peek()
        assert not pair[1].source.endswith("+plugins")
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_safe_start_loads_no_plugins(paths, monkeypatch):
    from vcstudio.app.application import AppContext
    write(paths.root / "plugins", "invert", INVERT)
    ctx = AppContext(paths, configure_logging=False)
    ctx.plugins.discover(); ctx.plugins.enable("invert")
    ctx.shutdown()
    monkeypatch.setenv("VCS_NO_PLUGINS", "1")
    ctx = AppContext(paths, configure_logging=False)
    try:
        assert not ctx.plugins.active
    finally:
        ctx.shutdown()
    monkeypatch.delenv("VCS_NO_PLUGINS")
    ctx = AppContext(paths, configure_logging=False)
    try:
        assert ctx.plugins.active                                # approved and unchanged: started at app start
    finally:
        ctx.shutdown()


@pytest.mark.parametrize("trust", [True, False])
def test_settings_card(qtbot, paths, trust):
    from PySide6.QtCore import Qt
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    write(paths.root / "plugins", "invert", INVERT)
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "settings")
        page.refresh_plugins()
        asked = []
        page.confirm_plugin = lambda info: asked.append(info.id) or trust
        assert page.plugin_list.count() == 1
        page.plugin_list.item(0).setCheckState(Qt.CheckState.Checked)
        qtbot.wait(50)
        assert asked == ["invert"] and ctx.plugins.active is trust
        assert (page.plugin_list.item(0).checkState() == Qt.CheckState.Checked) is trust
    finally:
        ctx.shutdown()


SLEEPY = """
    import time
    API_VERSION = 1
    NAME = "Sleepy"
    def create(host):
        return P()
    class P:
        delay = {delay}
        def process(self, frame, info):
            time.sleep(P.delay)
            return None
"""


def test_a_plugin_that_does_not_fit_the_frame_budget_is_switched_off(tmp_path, monkeypatch):
    """Audit 2026-11-19: a 40 ms plugin held 1080p60 output at 20 fps and stayed on. Over the budget on average it is
    now switched off; a single call over HANG_MS switches it off at once."""
    from vcstudio.plugins import host
    monkeypatch.setattr(host, "BUDGET_CALLS", 5)
    write(tmp_path, "slow", SLEEPY.format(delay=0.035))
    logs = []
    pm = PluginManager(tmp_path, FakeSettings(), lambda lv, m: logs.append((lv, m)))
    pm.discover(); pm.enable("slow")
    for _ in range(8):
        pm.run(frame(), INFO)
    i = pm.infos["slow"]
    assert i.state == "error" and "too slow" in i.error and not pm.active
    assert any(lv == "ERROR" and "slow switched off" in m for lv, m in logs)
    monkeypatch.setattr(host, "HANG_MS", 50.0)
    write(tmp_path, "hang", SLEEPY.format(delay=0.08))
    pm.discover(); pm.enable("hang")
    pm.run(frame(), INFO)
    assert pm.infos["hang"].state == "error" and "one frame took" in pm.infos["hang"].error


def test_a_plugin_call_that_never_returns_is_reported_and_dropped(tmp_path):
    import threading
    write(tmp_path, "stuck", SLEEPY.format(delay=0.6))
    pm = PluginManager(tmp_path, FakeSettings())
    pm.discover(); pm.enable("stuck")
    t = threading.Thread(target=pm.run, args=(frame(), INFO), daemon=True); t.start()
    time.sleep(0.3)
    assert pm.stalled(0.1) == "stuck" and pm.stalled(5.0) is None
    pm.switch_off_stalled("stuck")
    assert not pm.active and pm.infos["stuck"].state == "error" and "stopped responding" in pm.infos["stuck"].error
    t.join(2)
