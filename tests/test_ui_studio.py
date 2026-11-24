"""0.6.0 Studio page: every tab builds, and each one drives the real service behind it."""
from __future__ import annotations

import pytest

from vcstudio.app.application import AppContext
from vcstudio.core.resources import models_dir
from vcstudio.ui.main_window import MainWindow


@pytest.fixture
def win(qtbot, paths):
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()
    ctx.shutdown()


def studio(win):
    win.goto("studio")
    return win.pages[win.page_keys().index("studio")]


def test_studio_page_builds_all_tabs(win):
    page = studio(win)
    assert page.names and page.tabs.count() == len(page.names)
    for i in range(page.tabs.count()):
        page.tabs.setCurrentIndex(i)
        win._tick()                       # each tab's live refresh runs without errors


def test_models_tab_checks_registers_and_lists(win, qtbot):
    tab = studio(win).part("AI Models")
    tab.run_check(str(models_dir() / "face_detection_yunet_2023mar.onnx"), "face_detection")
    qtbot.waitUntil(lambda: tab.check_btn.isEnabled(), timeout=30000)
    assert tab.report is not None and tab.report.fits and tab.reg_btn.isEnabled()
    assert "SHA-256" in tab.result.text()
    tab.name.setText("yunet copy")
    tab.register()
    assert tab.table.rowCount() == 1 and tab.table.item(0, 0).text() == "yunet-copy"
    tab.table.setCurrentCell(0, 0)
    tab.toggle()
    assert tab.table.item(0, 4).text().startswith(("no", "нет"))
    tab.remove()
    assert tab.table.rowCount() == 0


def test_models_tab_backend_check(win, qtbot):
    tab = studio(win).part("AI Models")
    tab.check_backends()
    qtbot.waitUntil(lambda: tab.be_btn.isEnabled(), timeout=30000)
    assert "CPU" in tab.be_text.text()


def test_multicam_tab_adds_sources_and_writes_the_scene(win, qtbot):
    tab = studio(win).part("Multi-Camera")
    tab.enabled.setChecked(True)
    tab.layout_box.setCurrentIndex(tab.layout_box.findData("side_by_side"))
    assert tab.add_source({"method": "test_pattern", "name": "pattern"}) == "cam1"
    assert tab.add_source({"method": "test_pattern"}) == "cam2"
    body = win.ctx.profiles.active()["body"]["multicam"]
    assert body["enabled"] and body["layout"] == "side_by_side" and [s["id"] for s in body["sources"]] == ["cam1", "cam2"]
    assert win.ctx.pipeline.scene.multicam.enabled and win.ctx.pipeline.scene.multicam.layout == "side_by_side"
    tab.fallback.setCurrentIndex(tab.fallback.findData("cam2"))
    tab.auto.setCurrentIndex(tab.auto.findData("people"))
    assert win.ctx.pipeline.scene.multicam.fallback == "cam2" and win.ctx.pipeline.scene.multicam.auto == "people"
    tab.table.setCurrentCell(0, 0)
    tab.remove_source()
    assert [s["id"] for s in win.ctx.profiles.active()["body"]["multicam"]["sources"]] == ["cam2"]
    win.on_action("camera_next")
    assert win.ctx.multicam.override == "cam2"
    tab.tick()


def test_vision_tab_switches_the_service(win, qtbot):
    tab = studio(win).part("AI Vision")
    tab.face.setChecked(True)
    tab.pose.setChecked(False)
    tab.fps.setValue(4)
    tab.enabled.setChecked(True)
    v = win.ctx.settings.get("vision")
    assert v["enabled"] and v["fps"] == 4 and v["pose"] is False
    assert win.ctx.vision.cfg.enabled and win.ctx.vision.running
    tab.tick()
    tab.enabled.setChecked(False)
    qtbot.waitUntil(lambda: not win.ctx.vision.running, timeout=5000)


def test_replacement_page_background_colour_and_quality(win):
    page = win.pages[win.page_keys().index("replacement")]
    page.bg_mode.setCurrentIndex(page.bg_mode.findData("gradient"))
    page._pick_bg_color(0, "#ff0000")
    page._pick_bg_color(1, "#0000ff")
    page.bg_quality.setCurrentIndex(page.bg_quality.findData("quality"))
    b = win.ctx.profiles.active()["body"]["background"]
    assert b["mode"] == "gradient" and b["color"] == "#ff0000" and b["color2"] == "#0000ff" and b["quality"] == "quality"
    assert win.ctx.background.active and win.ctx.background.seg.smoothing == 0.7
    page.bg_mode.setCurrentIndex(page.bg_mode.findData("video"))
    assert page.bg_image.itemText(0).lower().startswith(("(choose a video", "(выберите видео"))


def test_privacy_tab_drives_mode_and_shield(win):
    tab = studio(win).part("Privacy Shield")
    tab.level.setCurrentIndex(tab.level.findData("faces"))
    from vcstudio.replacement import privacy_mode as pm
    assert pm.level_of(win.ctx.profiles.active()["body"]) == "faces"
    tab.effect.setCurrentIndex(tab.effect.findData("black"))
    tab.strict.setChecked(True)
    tab.documents.setChecked(True)
    tab.pattern_boxes["address"].setChecked(False)
    tab.keywords.setText("orion, budget")
    tab.save()
    tab.enabled.setChecked(True)
    d = win.ctx.settings.get("privacy_shield")
    assert d["enabled"] and d["effect"] == "black" and d["strict"] and "address" not in d["patterns"]
    assert d["keywords"] == ["orion", "budget"] and d["documents"] and win.ctx.shield.cfg.documents
    assert tab.add_area("regions", "0.1, 0.1, 0.2, 0.2") and tab.add_area("exclude", "0.5,0.5,0.1,0.1")
    assert not tab.add_area("regions", "nonsense")
    assert win.ctx.shield.cfg.regions == ((0.1, 0.1, 0.2, 0.2),) and len(win.ctx.shield.cfg.exclude) == 1
    tab.areas.setCurrentRow(0)
    tab.remove_area()
    assert win.ctx.shield.cfg.regions == ()
    tab.tick()


def test_color_tab_writes_the_scene_imports_luts_and_presets(win, tmp_path):
    tab = studio(win).part("Color Studio")
    tab.enabled.setChecked(True)
    tab.sliders["exposure"].setValue(tab._to_slider("exposure", 0.5))
    tab.sliders["saturation"].setValue(tab._to_slider("saturation", 1.4))
    c = win.ctx.pipeline.scene.color
    assert c.enabled and c.exposure == 0.5 and c.saturation == 1.4 and c.active
    cube = tmp_path / "warm.cube"
    cube.write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
    dest = tab.import_lut(str(cube))
    assert dest is not None and win.ctx.pipeline.scene.color.lut == str(dest)
    bad = tmp_path / "bad.cube"
    bad.write_text("x")
    assert tab.import_lut(str(bad)) is None
    tab.save_preset("Warm")
    tab.reset()
    assert win.ctx.pipeline.scene.color.exposure == 0.0
    tab.apply_preset("Warm")
    assert win.ctx.pipeline.scene.color.exposure == 0.5 and win.ctx.pipeline.scene.color.lut == str(dest)
    tab.tick()


def test_automation_tab_builds_runs_and_shows_history(win, qtbot):
    from vcstudio.ui.studio.automation_tab import parse_action, parse_trigger
    assert parse_trigger("people_count", "<=0") == {"type": "people_count", "op": "<=", "n": 0}
    assert parse_trigger("timer", "abc")["every_s"] == 60.0 and parse_trigger("hotkey", "9")["slot"] == 1
    assert parse_action("effects", "off") == {"type": "effects", "on": False}
    tab = studio(win).part("Automation")
    win.ctx.profiles.create("Away")
    tab.load()
    tab.name.setText("go away")
    tab.trigger.setCurrentIndex(tab.trigger.findData("manual"))
    tab.action.setCurrentIndex(tab.action.findData("scene"))
    tab.action_arg.setText("Nope")
    assert tab.add_rule() is None                                       # unknown scene refused
    tab.action_arg.setText("Away")
    assert tab.add_rule() == "rule1"
    tab.enabled.setChecked(True)
    assert win.ctx.automation.running and win.ctx.automation.rules[0].action == {"type": "scene", "name": "Away"}
    tab.table.setCurrentCell(0, 0)
    tab.run_now()
    qtbot.waitUntil(lambda: win.ctx.profiles.active_name == "Away", timeout=5000)
    tab.tick()
    assert tab.hist.rowCount() >= 1 and tab.hist.item(0, 4).text().startswith("scene")
    tab.toggle()
    assert win.ctx.settings.get("automation")["rules"][0]["enabled"] is False
    tab.remove()
    assert win.ctx.settings.get("automation")["rules"] == []
    tab.enabled.setChecked(False)


def test_audio_tab_writes_the_chain_and_meters(win):
    tab = studio(win).part("Audio Studio")
    tab.switches["comp"].setChecked(True)
    tab.nums["comp_ratio"].setValue(6)
    tab.switches["mute"].setChecked(True)
    a = win.ctx.settings.get("audio")
    assert a["comp"] and a["comp_ratio"] == 6 and a["mute"]
    assert win.ctx.recording.fx.cfg.comp and win.ctx.recording.fx.cfg.comp_ratio == 6
    tab.switches["mute"].setChecked(False)
    tab.live.setChecked(True)                      # no microphone chosen: nothing opens, no error
    assert win.ctx.recording.audio is None
    tab.tick()
    tab.live.setChecked(False)


def test_remote_tab_switches_the_server_and_renews_the_code(win):
    tab = studio(win).part("Remote Control")
    tab.port.setValue(tab.port.value())
    win.ctx.settings.set("remote.port", 0)                  # any free port for the test
    tab.enabled.setChecked(True)
    assert win.ctx.remote.running and win.ctx.remote.httpd.server_address[0] == "127.0.0.1"
    tab._refresh()
    assert tab.url.text().startswith("http://127.0.0.1:") and tab.code.text() == win.ctx.remote.token
    old = tab.code.text()
    tab.new_code()
    assert tab.code.text() != old
    tab.enabled.setChecked(False)
    assert not win.ctx.remote.running
    tab.lan.setChecked(True)                                # with the server off: no network socket is opened
    assert tab.lan_warn.isVisibleTo(tab.widget) and win.ctx.settings.get("remote")["lan"] and not win.ctx.remote.running
    tab.lan.setChecked(False)
    tab.tick()


def test_vision_tab_people_lock_and_locked_only_framing(win):
    from vcstudio.ai.detection import Detection
    tab = studio(win).part("AI Vision")
    tr_ = win.ctx.ai.tracker
    t = 0.0
    for _ in range(4):
        tr_.update([Detection("person", 0.9, 100, 100, 180, 300)], t, (640, 360))
        t += 0.1
    objs = tr_.snapshot(t)
    tab.refresh_people(objs)
    assert tab.people.rowCount() == 1 and tab.people.item(0, 1).text() == "person"
    tab.people.selectRow(0)
    assert tab.set_locked(True) and tr_.snapshot(t)[0].locked
    assert tab.set_locked(False) and not tr_.snapshot(t)[0].locked
    assert not tab.set_locked(True, track_id=999)
    tab.locked_only.setChecked(True)
    assert win.ctx.pipeline.scene.framing.locked_only
    tab.tick()


def test_vision_mesh_checkbox_and_background_depth_source(win):
    tab = studio(win).part("AI Vision")
    tab.mesh.setChecked(True)
    assert win.ctx.settings.get("vision")["mesh"] is True and win.ctx.vision.cfg.mesh
    tab.mesh.setChecked(False)
    page = win.pages[win.page_keys().index("replacement")]
    page.bg_mode.setCurrentIndex(page.bg_mode.findData("blur"))
    page.bg_source.setCurrentIndex(page.bg_source.findData("depth"))
    assert win.ctx.profiles.active()["body"]["background"]["source"] == "depth"
    assert win.ctx.background.cfg.source == "depth"


def test_models_tab_use_and_unuse(win, qtbot):
    from vcstudio.core.resources import models_dir as md
    tab = studio(win).part("AI Models")
    tab.run_check(str(md() / "human_segmentation_pphumanseg_2023mar.onnx"), "background_segmentation")
    qtbot.waitUntil(lambda: tab.check_btn.isEnabled(), timeout=30000)
    tab.name.setText("seg")
    tab.register()
    tab.table.setCurrentCell(0, 0)
    assert "seg" in tab.use(True) and win.ctx.settings.get("models")["background"] == "seg"
    assert "✓" in tab.table.item(0, 0).text()
    tab.table.setCurrentCell(0, 0)
    tab.use(False)
    assert win.ctx.settings.get("models")["background"] == ""


def test_vision_tab_gaze_eye_contact_and_calibration(win):
    tab = studio(win).part("AI Vision")
    d = dict(win.ctx.settings.get("vision"))
    d["gaze_calibration"] = {"yaw": 1.0, "pitch": -3.0}
    win.ctx.settings.set("vision", d)
    tab.eye_contact.setChecked(True)
    tab.eye_strength.setValue(40)
    v = win.ctx.settings.get("vision")
    assert v["eye_contact"] is True and v["eye_contact_strength"] == 0.4
    assert v["gaze_calibration"] == {"yaw": 1.0, "pitch": -3.0}          # saving the tab keeps the calibration
    cfg = win.ctx.vision.cfg
    assert cfg.eye_contact and cfg.needs_mesh and cfg.calibration == (1.0, -3.0)
    assert tab.calibrate() is None                                      # nothing analysed yet: nothing saved
    tab.reset_calibration()
    assert win.ctx.settings.get("vision")["gaze_calibration"] == {}
    tab.tick()
    tab.eye_contact.setChecked(False)


def test_audio_tab_presets_and_deesser(win):
    tab = studio(win).part("Audio Studio")
    tab.preset.setCurrentIndex(tab.preset.findData("podcast"))
    a = win.ctx.settings.get("audio")
    assert a["deess"] is True and a["agc"] is True and a["comp_ratio"] == 4.0
    assert tab.switches["deess"].isChecked() and tab.preset.currentData() == "podcast"
    tab.nums["comp_ratio"].setValue(6.0)                               # an own change: the preset says "Custom"
    assert tab.preset.currentData() == "" and win.ctx.settings.get("audio")["comp_ratio"] == 6.0
    assert win.ctx.recording.fx.cfg.deess


def test_replacement_page_chroma_key_controls(win):
    page = win.pages[win.page_keys().index("replacement")]
    page.bg_mode.setCurrentIndex(page.bg_mode.findData("color"))
    page.bg_source.setCurrentIndex(page.bg_source.findData("chroma"))
    assert page.key_sim.isEnabled()
    page.key_color.setCurrentIndex(page.key_color.findData("#0047bb"))
    page.key_spill.setValue(0.3)
    page.set_key_color("#10c050")
    b = win.ctx.profiles.active()["body"]["background"]
    assert b["source"] == "chroma" and b["key_color"] == "#10c050" and b["key_spill"] == 0.3
    cfg = win.ctx.background.cfg
    assert cfg.source == "chroma" and cfg.key_color == "#10c050"
    page.bg_source.setCurrentIndex(page.bg_source.findData("person"))
    assert not page.key_sim.isEnabled()


def test_automation_tab_condition_groups(win):
    tab = studio(win).part("Automation")
    tab.trigger.setCurrentIndex(tab.trigger.findData("timer"))
    tab.trigger_arg.setText("60")
    tab.action.setCurrentIndex(tab.action.findData("replay"))
    tab.cond_text.setText("people>=2 and gaze or recording")
    assert tab.add_rule() is None and "or" in tab.msg.text()
    tab.cond_text.setText("not recording and time 09:00-18:00")
    rid = tab.add_rule()
    r = next(r for r in win.ctx.settings.get("automation")["rules"] if r["id"] == rid)
    assert r["condition"]["match"] == "all" and len(r["condition"]["terms"]) == 2
    assert "time 09:00-18:00" in tab.table.item(tab.table.rowCount() - 1, 3).text()


def test_multicam_tab_per_camera_colour_and_privacy(win):
    win.ctx.settings.set("color_presets", {"Warm": {"brightness": 0.2}})
    tab = studio(win).part("Multi-Camera")
    tab.load()
    tab.add_source({"method": "test_pattern", "name": "pattern"})
    tab.table.setCurrentCell(0, 0)
    tab.src_color.setCurrentIndex(tab.src_color.findData("Warm"))
    tab.src_privacy.setCurrentIndex(tab.src_privacy.findData("hide"))
    s = win.ctx.profiles.active()["body"]["multicam"]["sources"][0]
    assert s["color_preset"] == "Warm" and s["privacy"] == "hide"
    assert win.ctx.pipeline.scene.multicam.sources[0]["privacy"] == "hide"
    assert win.ctx._color_preset("Warm").active and win.ctx._color_preset("nope") is None


def test_colour_tab_virtual_lights(win):
    tab = studio(win).part("Color Studio")
    lc = tab.lights
    lc.preset.setCurrentIndex(lc.preset.findData("dramatic"))
    b = win.ctx.profiles.active()["body"]["lighting"]
    assert b["vl_enabled"] is True and b["vl_key_angle"] == -75.0 and b["vl_fill"] == 0.0
    assert win.ctx.pipeline.scene.lighting.vl_enabled and lc.preset.currentData() == "dramatic"
    lc.nums["vl_rim"].setValue(0.1)
    assert lc.preset.currentData() == "" and win.ctx.profiles.active()["body"]["lighting"]["vl_rim"] == 0.1
    tab.tick()
    assert "face" in lc.status.text()
    lc.enabled.setChecked(False)
    assert not win.ctx.pipeline.scene.lighting.vl_enabled
