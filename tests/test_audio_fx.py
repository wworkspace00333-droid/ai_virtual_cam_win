"""0.6.0 Phase 8: the Audio Studio chain on measured synthetic signals, and its place in recordings."""
from __future__ import annotations

import numpy as np
import pytest

from vcstudio.core import audio_fx as fx

R = fx.RATE


def tone(freq=1000.0, db_fs=-20.0, secs=1.0):
    t = np.arange(int(R * secs)) / R
    a = fx.undb(db_fs) * np.sqrt(2) * np.sin(2 * np.pi * freq * t)       # RMS = db_fs
    return np.stack([a, a], 1)


def i16(x):
    return np.clip(x * 32768, -32768, 32767).astype(np.int16)


def run(chain, x, block=960):
    out = [chain.process(i16(x[i:i + block])) for i in range(0, len(x), block)]
    return np.concatenate(out).astype(np.float32) / 32768


def rms_db(x):
    return fx.db(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))))


def test_config_from_settings_is_clamped():
    class S:
        def get(self, k):
            return {"gain_db": 99, "comp_ratio": 0, "limiter": False, "eq_low_db": "x"}
    c = fx.AudioFxConfig.from_settings(S())
    assert c.gain_db == 30 and c.comp_ratio == 1 and c.limiter is False and c.eq_low_db == 0


def test_gain_mute_and_meters():
    x = tone(db_fs=-20, secs=0.5)
    ch = fx.AudioChain(fx.AudioFxConfig(gain_db=6, limiter=False))
    y = run(ch, x)
    assert abs(rms_db(y[R // 10:]) - (-14)) < 0.3
    assert abs(ch.meter.in_rms_db - (-20)) < 0.5 and abs(ch.meter.out_rms_db - (-14)) < 0.5
    muted = run(fx.AudioChain(fx.AudioFxConfig(mute=True)), x)
    assert not muted.any()


def test_limiter_never_lets_a_peak_through():
    x = tone(db_fs=-3, secs=0.5) * 2.0                                     # peaks well above full scale
    y = run(fx.AudioChain(fx.AudioFxConfig(limiter=True, limiter_ceiling_db=-1)), x)
    assert np.abs(y).max() <= fx.undb(-1) + 1e-3


def test_compressor_reduces_level_above_threshold_by_the_ratio():
    x = tone(db_fs=-8, secs=2.0)                                           # 12 dB over a -20 dB threshold
    ch = fx.AudioChain(fx.AudioFxConfig(comp=True, comp_threshold_db=-20, comp_ratio=4, limiter=False))
    y = run(ch, x)
    out = rms_db(y[R:])                                                    # steady state
    assert abs(out - (-20 + 12 / 4)) < 1.0                                 # -17 dB expected
    quiet = run(fx.AudioChain(fx.AudioFxConfig(comp=True, comp_threshold_db=-20, comp_ratio=4, limiter=False)),
                tone(db_fs=-30, secs=1.0))
    assert abs(rms_db(quiet[R // 2:]) - (-30)) < 0.5                       # below threshold: untouched


def test_gate_closes_on_quiet_noise_and_opens_for_a_voice():
    rng = np.random.default_rng(1)
    hiss = rng.normal(0, fx.undb(-60), (R, 2))
    loud = tone(db_fs=-20, secs=1.0)
    ch = fx.AudioChain(fx.AudioFxConfig(gate=True, gate_threshold_db=-45, limiter=False))
    y = run(ch, np.concatenate([hiss, loud]))
    assert rms_db(y[R // 2:R]) < -60 - 25                                  # hiss pushed far down
    assert abs(rms_db(y[R + R // 2:]) - (-20)) < 0.5                       # voice passes


def test_equaliser_low_shelf_boosts_bass_not_mids():
    ch = fx.AudioChain(fx.AudioFxConfig(eq=True, eq_low_db=9, limiter=False))
    bass = run(ch, tone(60, -30, 1.0))
    ch2 = fx.AudioChain(fx.AudioFxConfig(eq=True, eq_low_db=9, limiter=False))
    mid = run(ch2, tone(3000, -30, 1.0))
    assert 7.5 < rms_db(bass[R // 2:]) - (-30) < 9.5
    assert abs(rms_db(mid[R // 2:]) - (-30)) < 1.0


def test_noise_suppression_improves_signal_to_noise():
    rng = np.random.default_rng(2)
    secs = 3.0
    noise = rng.normal(0, fx.undb(-40), (int(R * secs), 2)).astype(np.float32)
    sig = tone(440, -20, secs).astype(np.float32)
    on_off = np.zeros(len(sig), np.float32)
    on_off[int(R * 1.5):] = 1                                              # 1.5 s of noise only, then the tone
    x = noise + sig * on_off[:, None]
    ch = fx.AudioChain(fx.AudioFxConfig(denoise=True, denoise_strength=1.0, limiter=False))
    y = run(ch, x)
    noise_before, noise_after = rms_db(x[int(R * 0.8):int(R * 1.4)]), rms_db(y[int(R * 0.8):int(R * 1.4)])
    tone_after = rms_db(y[int(R * 2.2):])
    assert noise_after < noise_before - 10                                 # noise down by more than 10 dB
    assert tone_after > -22                                                # the tone keeps its level


def test_agc_brings_a_quiet_voice_towards_the_target():
    ch = fx.AudioChain(fx.AudioFxConfig(agc=True, agc_target_db=-20, agc_max_gain_db=18, gate_threshold_db=-70,
                                        limiter=False))
    y = run(ch, tone(db_fs=-32, secs=12.0))
    assert rms_db(y[-R:]) > -22                                            # +10 dB or more after a few seconds


@pytest.mark.parametrize("block", [64 * 7, 64 * 30])
def test_result_does_not_depend_on_block_size(block):
    x = tone(db_fs=-8, secs=1.0) + tone(150, -25, 1.0)
    cfg = fx.AudioFxConfig(comp=True, eq=True, eq_low_db=4, limiter=True)
    a = run(fx.AudioChain(cfg), x, block=64 * 15)
    b = run(fx.AudioChain(cfg), x, block=block)
    assert np.abs(a - b).max() < 0.02


def test_recording_service_applies_the_chain(paths):
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        got = []

        class Rec:
            active = True

            def push_audio(self, s, t):
                got.append(s)
        ctx.settings.set("audio.mute", True)
        ctx.recording.recorder = Rec()
        ctx.recording._on_audio(i16(tone(secs=0.05)), 0.0)
        assert got and not got[0].any()                                    # muted by the chain before the file
        ctx.settings.set("audio.mute", False)
        ctx.recording._on_audio(i16(tone(secs=0.05)), 0.1)
        assert got[1].any()
        ctx.recording.recorder = None
    finally:
        ctx.shutdown()


class FakeDevice:
    def __init__(self):
        self.written, self.released, self.closed = [], [], False

    def write(self, hdr):
        self.written.append(hdr)

    def release(self, hdr):
        self.released.append(hdr)

    def close(self):
        self.closed = True


def test_monitor_queue_is_bounded_and_reuses_finished_blocks():
    from vcstudio.core import audio_monitor as am
    dev = FakeDevice()
    m = am.Monitor(device_factory=lambda: dev)
    assert m.start() and m.running
    blk = i16(tone(secs=0.02))
    for _ in range(am.MAX_IN_FLIGHT + 3):                   # the device plays nothing: queue fills, rest dropped
        m.push(blk)
    assert m.played == am.MAX_IN_FLIGHT and m.dropped == 3
    for h in dev.written:
        h.dwFlags |= am.WHDR_DONE                          # the device finished them
    m.push(blk)
    assert m.played == am.MAX_IN_FLIGHT + 1 and len(dev.released) == am.MAX_IN_FLIGHT
    m.stop()
    assert dev.closed and not m.running


def test_monitor_without_output_device_reports_and_does_not_break_recording(paths):
    from vcstudio.core import audio_monitor as am

    def no_device():
        raise OSError("no audio output (waveOutOpen error 2)")
    m = am.Monitor(device_factory=no_device)
    assert not m.start() and "no audio output" in m.error
    m.push(i16(tone(secs=0.01)))                          # ignored, no exception
    from vcstudio.app.application import AppContext
    ctx = AppContext(paths, configure_logging=False)
    try:
        ctx.recording.monitor = m
        got = []

        class Rec:
            active = True

            def push_audio(self, s, t):
                got.append(s)
        ctx.recording.recorder = Rec()
        ctx.recording._on_audio(i16(tone(secs=0.02)), 0.0)
        assert got                                         # the file still gets its sound
        ctx.recording.recorder = None
    finally:
        ctx.shutdown()


def test_deesser_turns_down_sibilants_only():
    c = fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=-30.0))
    s = run(c, tone(7500, -18, 0.5))                                      # a loud "s": mostly above 5.5 kHz
    assert rms_db(s[R // 10:]) < -18 - 5 and c.meter.deess_gain_db < -5
    c2 = fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=-30.0))
    v = run(c2, tone(300, -18, 0.5))                                      # a vowel: untouched
    assert abs(rms_db(v[R // 10:]) + 18) < 0.3
    c3 = fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=-30.0))
    quiet = run(c3, tone(7500, -40, 0.5))                                 # under the threshold: untouched
    assert abs(rms_db(quiet[R // 10:]) + 40) < 0.5
    mix = tone(300, -14, 0.5) + tone(7500, -30, 0.5)                      # bright voice: the vowel dominates
    c4 = fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=-35.0))
    out = run(c4, mix)
    assert abs(rms_db(out[R // 10:]) - rms_db(mix[R // 10:])) < 0.3


def test_deesser_bands_add_up_and_block_size_does_not_matter():
    x = tone(1000, -20, 0.3) + tone(7000, -26, 0.3)
    a = run(fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=0.0)), x, 480)
    for f in (300, 1000, 3000, 5500, 8000, 12000):                      # never triggered: flat magnitude
        y = run(fx.AudioChain(fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=0.0)), tone(f, -20, 0.3))
        assert abs(rms_db(y[R // 20:]) + 20) < 0.1, f
    assert abs(rms_db(a[R // 20:]) - rms_db(x[R // 20:])) < 0.1
    cfg = fx.AudioFxConfig(limiter=False, deess=True, deess_threshold_db=-40.0)
    b1 = run(fx.AudioChain(cfg), x, 480)
    b2 = run(fx.AudioChain(cfg), x, 1440)
    assert np.abs(b1 - b2).max() < 1e-2               # gains interpolate within a block, as for gate / compressor


def test_presets_are_valid_settings_and_recognised():
    for name, p in fx.PRESETS.items():
        cfg = fx.AudioFxConfig.from_settings(_S({"audio": dict(p)}))
        for k, v in p.items():
            assert getattr(cfg, k) == v, (name, k)
        assert fx.preset_name({**p, "gain_db": 3.0}) == name
    assert fx.preset_name({"denoise": True}) == ""


class _S:
    def __init__(self, d):
        self.d = d

    def get(self, k):
        return self.d[k]
