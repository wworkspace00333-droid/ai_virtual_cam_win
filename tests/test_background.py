"""Background replacement and blur behind people (PP-HumanSeg mask, whole frame, before region replacements)."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from vcstudio.ai.person_segmentation import composite_background, model_path

ROOT = Path(__file__).resolve().parents[1]
needs_model = pytest.mark.skipif(not model_path().exists(), reason="run scripts/fetch_models.py")


def _frame(w=320, h=180):
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 0] = np.arange(w, dtype=np.uint8)[None, :]          # a horizontal gradient: blur visibly changes it
    img[..., 3] = 255
    return img


def test_composite_keeps_the_person_and_replaces_or_blurs_the_rest():
    img = _frame()
    mask = np.zeros((192, 192), np.float32)
    mask[48:144, 64:128] = 1.0                                    # "person" in the middle
    green = np.zeros_like(img); green[..., 1] = 200; green[..., 3] = 255
    out = composite_background(img, mask, "image", image_bgra=green)
    assert out.shape == img.shape and out.dtype == np.uint8 and (out[..., 3] == 255).all()
    assert tuple(out[5, 5, :3]) == (0, 200, 0)                    # corner: background replaced
    assert np.array_equal(out[90, 160, :3], img[90, 160, :3])     # centre: the person stays
    blur = composite_background(img, mask, "blur", strength=41)
    assert np.array_equal(blur[90, 160, :3], img[90, 160, :3])
    assert abs(int(blur[5, 300, 0]) - int(img[5, 300, 0])) > 3 or abs(int(blur[5, 5, 0]) - int(img[5, 5, 0])) > 3
    assert composite_background(img, mask, "off") is img          # nothing to do


def test_blend_weights_are_reused_for_the_same_mask_version_only():
    """Frames that share a mask (segmentation 5-15 /s, frames 60 /s) reuse its upscaled weights; a new version, size
    or edge recomputes them. Results equal the uncached blend, also when written into `out`."""
    from vcstudio.ai import person_segmentation as ps
    img = _frame()
    green = np.zeros_like(img); green[..., 1] = 200; green[..., 3] = 255
    a = np.zeros((192, 192), np.float32); a[48:144, 64:128] = 1.0
    b = np.zeros((192, 192), np.float32); b[48:144, 0:64] = 1.0
    ref_a = composite_background(img, a, "image", image_bgra=green)
    ref_b = composite_background(img, b, "image", image_bgra=green)
    assert np.array_equal(composite_background(img, a, "image", image_bgra=green, mask_key=("t", 1)), ref_a)
    cached = ps._weights_cache
    out = np.empty_like(img)
    r = composite_background(img, a, "image", image_bgra=green, out=out, mask_key=("t", 1))
    assert r is out and np.array_equal(out, ref_a) and ps._weights_cache is cached          # reused, written in place
    assert np.array_equal(composite_background(img, b, "image", image_bgra=green, mask_key=("t", 2)), ref_b)
    assert ps._weights_cache is not cached                                                   # new version: recomputed
    big = composite_background(np.zeros((360, 640, 4), np.uint8), b, "image",
                               image_bgra=np.zeros((360, 640, 4), np.uint8), mask_key=("t", 2))
    assert big.shape == (360, 640, 4)                                                         # new size: recomputed
    soft = np.full((192, 192), 0.5, np.float32)                       # edge decides: 0.1 -> person, 0.9 -> background
    assert not np.array_equal(composite_background(img, soft, "image", image_bgra=green, edge=0.1, mask_key=("t", 3)),
                              composite_background(img, soft, "image", image_bgra=green, edge=0.9, mask_key=("t", 3)))
    same = composite_background(img, a, "image", image_bgra=green, out=img.copy())          # out aliasing is safe
    assert np.array_equal(same, ref_a)


def test_wide_blur_runs_at_one_eighth_and_matches_the_quarter_size_blur():
    from vcstudio.ai.person_segmentation import blur_scale
    assert blur_scale(15) == 4 and blur_scale(25) == 8 and blur_scale(99) == 8
    import cv2
    rng = np.random.default_rng(1)
    img = cv2.GaussianBlur(rng.integers(0, 255, (360, 640, 4), np.uint8), (0, 0), 3)
    img[..., 3] = 255
    mask = np.zeros((192, 192), np.float32)
    out8 = composite_background(img, mask, "blur", strength=41).astype(np.float32)
    small = cv2.GaussianBlur(cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA), (0, 0), 41 / 4)
    out4 = cv2.resize(small, (640, 360), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    psnr = 10 * np.log10(255 ** 2 / np.mean((out8[..., :3] - out4[..., :3]) ** 2))
    assert psnr > 40, psnr                                                   # visually the same background


def test_background_config_is_clamped():
    from vcstudio.replacement.background import BackgroundConfig
    c = BackgroundConfig.from_profile({"background": {"mode": "weird", "strength": 500, "edge": 5, "fps": 0}})
    assert (c.mode, c.strength, c.edge, c.fps) == ("off", 99, 0.9, 2.0)


@needs_model
def test_person_segmenter_finds_the_people_on_the_sample_photo():
    from vcstudio.ai.person_segmentation import PersonSegmenter
    from vcstudio.core.bench_source import sample_scene
    p = PersonSegmenter(compute_units="cpu_only").probability(sample_scene(1280, 720))
    assert p.shape == (192, 192) and 0.0 <= float(p.min()) and float(p.max()) <= 1.0
    share = float((p > 0.5).mean())
    assert 0.03 < share < 0.5, share                               # people, not the whole frame or nothing


class _FakeSeg:
    backend = "fake"

    def __init__(self, fail=False):
        self.fail = fail

    def probability(self, bgra):
        if self.fail:
            raise RuntimeError("simulated segmentation failure")
        m = np.zeros((192, 192), np.float32)
        m[48:144, 64:128] = 1.0
        return m


def _ctx(paths, mode="blur", fail=False):
    from vcstudio.app.application import AppContext
    from vcstudio.replacement.background import BackgroundService
    ctx = AppContext(paths, configure_logging=False)
    ctx.background = BackgroundService(ctx.capture, ctx.assets, segmenter_factory=lambda: _FakeSeg(fail))
    ctx.pipeline.background = ctx.background
    ctx.settings.set("input.method", "test_pattern")
    ctx.settings.set("output.camera_enabled", False)
    ctx.update_active_profile(lambda b: b.__setitem__("background", {"mode": mode, "strength": 25}))
    return ctx


def _out_source(ctx):
    item = ctx.pipeline.processed.peek()[0]
    return item[1].source if item is not None else None


def _wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_pipeline_outputs_the_background_processed_frame_and_keeps_the_original_preview(paths):
    ctx = _ctx(paths)
    try:
        ctx.start_capture()
        assert _wait_for(lambda: _out_source(ctx) == "background")
        orig, out = ctx.pipeline.processed.peek()[0]
        assert "background" not in orig.source                     # the "original" preview is untouched
        assert ctx.background.seg.backend == "fake" and ctx.background.applied > 0
        ctx.update_active_profile(lambda b: b["background"].__setitem__("mode", "off"))
        assert _wait_for(lambda: not ctx.background.seg.running)  # switching off stops the segmentation thread
        assert _wait_for(lambda: _out_source(ctx) == "passthrough")
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def test_segmentation_failure_never_stops_the_camera_output(paths):
    ctx = _ctx(paths, fail=True)
    try:
        ctx.start_capture()
        assert _wait_for(lambda: ctx.pipeline.stats().frames_out > 30)
        assert _out_source(ctx) == "passthrough"      # unchanged frames, no stall
        assert "simulated segmentation failure" in ctx.background.seg.error
    finally:
        ctx.stop_capture()
        ctx.shutdown()


def _bg_service(mode, image=None):
    from vcstudio.replacement.background import BackgroundConfig, BackgroundService

    class _Cap:                                          # BackgroundService only needs .slot for its thread
        slot = None
    svc = BackgroundService(_Cap(), None, segmenter_factory=lambda: _FakeSeg())
    svc.cfg = BackgroundConfig(mode=mode, strength=31, asset_id="a1")
    svc._image = image
    svc.seg.masks.put(_FakeSeg().probability(None))
    return svc


@pytest.mark.parametrize("mode", ["blur", "image"])
def test_background_layers_render_the_same_on_cpu_and_gpu(tmp_path, mode):
    from vcstudio.core.bench_source import sample_scene
    from vcstudio.core.frame import Frame
    from vcstudio.platform import GPU_COMPOSITOR
    from vcstudio.replacement.assets import AssetLibrary
    from vcstudio.replacement.compositor import Compositor
    img = sample_scene(640, 360)
    pic = np.zeros((200, 300, 4), np.uint8); pic[..., 1] = 180; pic[..., 2] = 40; pic[..., 3] = 255
    svc = _bg_service(mode, pic)
    ops = svc.ops(Frame(1, 0, 0, 640, 360, array=img))
    assert len(ops) == 1 and ops[0].kind == "composite" and ops[0].bbox == (0, 0, 640, 360)
    lib = AssetLibrary(tmp_path / "a")
    cpu, gpu = Compositor(lib, "cpu"), Compositor(lib, GPU_COMPOSITOR)
    try:
        a = cpu.render(img, [], {}, {}, pre_ops=ops)
        if not gpu.active.name.startswith(GPU_COMPOSITOR):
            pytest.skip(f"no GPU compositor: {gpu.backend_error}")
        b = gpu.render(img, [], {}, {}, pre_ops=ops)
    finally:
        cpu.close(); gpu.close()
    d = np.abs(a.astype(int) - b.astype(int))[..., :3]
    # same layer maths on both backends (filtering rounding differs by a few levels at mask edges, as in test_metal)
    assert d.max() <= 6 and d.mean() < 0.5 and (d > 3).mean() < 0.001, (d.max(), d.mean(), (d > 3).mean())
    centre, corner = a[180, 320, :3], a[10, 10, :3]
    assert np.array_equal(centre, img[180, 320, :3])     # the person (mask centre) is untouched
    if mode == "image":
        assert tuple(corner) == (0, 180, 40)             # replaced


def test_no_mask_yet_means_no_layers():
    from vcstudio.core.frame import Frame
    svc = _bg_service("blur")
    svc.seg.masks.clear()
    assert svc.ops(Frame(1, 0, 0, 64, 36, array=np.zeros((36, 64, 4), np.uint8))) == []


def test_gpu_layers_only_with_a_hardware_gpu_compositor():
    svc = _bg_service("blur")
    assert svc.use_gpu_layers("d3d11") and svc.use_gpu_layers("metal")
    assert not svc.use_gpu_layers("d3d11-warp") and not svc.use_gpu_layers("cpu")


def test_pipeline_uses_gpu_layers_when_the_compositor_is_a_hardware_gpu(paths, monkeypatch):
    """Simulated hardware backend name: the background goes to the compositor as layers (pre_ops)."""
    ctx = _ctx(paths)
    seen = []
    real = ctx.replacement.render_pb

    def spy(frame, regions, t=None, pre_ops=None, state=None):
        seen.append(len(pre_ops or []))
        return real(frame, regions, t, pre_ops, state=state)
    monkeypatch.setattr(ctx.replacement, "render_pb", spy)
    monkeypatch.setattr(ctx.background, "use_gpu_layers", lambda name: True)
    try:
        ctx.start_capture()
        assert _wait_for(lambda: any(n == 1 for n in seen))
        assert _wait_for(lambda: _out_source(ctx) == "background")
    finally:
        ctx.stop_capture()
        ctx.shutdown()
