"""Microphone sound in recordings and instant replay: device listing, capture thread, AAC muxing in sync with the
video (a tone and a white frame at the same moment), trimming / gap filling, the service and the Settings card.
The development server has no microphone: capture is fed with generated audio."""
from __future__ import annotations

import time

import numpy as np
import pytest

av = pytest.importorskip("av")

from vcstudio.core.audio import RATE, AudioSource, parse_dshow_devices  # noqa: E402
from vcstudio.core.recorder import InstantReplay, Recorder, RecordingService  # noqa: E402

T0 = 100.0


def tone_chunk(t: float, n: int = 480, start: float = 1.0, length: float = 0.1) -> np.ndarray:
    ts = t - T0 + np.arange(n) / RATE
    x = ((ts >= start) & (ts < start + length)) * 12000 * np.sin(2 * np.pi * 1000 * ts)
    return np.stack([x, x], 1).astype(np.int16)


def frame(white=False):
    a = np.zeros((180, 320, 4), np.uint8)
    a[..., 3] = 255
    if white:
        a[..., :3] = 255
    return a


def first_flash_and_tone(path):
    with av.open(str(path)) as c:
        flash = next((f.time for f in c.decode(c.streams.video[0]) if f.to_ndarray(format="gray").mean() > 128), None)
    with av.open(str(path)) as c:
        tone, total = None, 0.0
        for f in c.decode(c.streams.audio[0]):
            x = f.to_ndarray()
            total = f.time + x.shape[1] / f.sample_rate
            if tone is None and np.abs(x).max() > 0.1:
                tone = f.time + int(np.argmax(np.abs(x).max(axis=0) > 0.1)) / f.sample_rate
    return flash, tone, total


# ---------------------------------------------------------------- devices and capture
def test_parse_dshow_device_list():
    lines = ['"AI Virtual Camera (Windows Virtual Camera)"', "(video", ")", "",
             'Alternative name "@device_pnp_\\\\?\\swd#vcam"',
             '"Microphone (Realtek(R) Audio)" (audio)', 'Alternative name "@device_cm_{33D9A762}\\wave_{1}"',
             '"Headset Microphone (USB)"', "(audio", ")",
             '"OBS Virtual Camera"', "(none)", "",
             '"Microphone (Realtek(R) Audio)" (audio)']                         # listed twice: once
    assert parse_dshow_devices(lines) == ["Microphone (Realtek(R) Audio)", "Headset Microphone (USB)"]
    assert parse_dshow_devices(["Could not enumerate audio only devices (or none found)."]) == []


def test_list_microphones_never_fails():
    from vcstudio.core.audio import list_microphones
    assert isinstance(list_microphones(), list)               # the server has none; a PC lists its microphones


def test_audio_source_timestamps_chunks_on_the_video_clock():
    clock = {"t": 50.0}

    def chunks():
        for _ in range(5):
            clock["t"] += 0.01
            yield np.ones((480, 2), np.int16)
    got = []
    src = AudioSource("Mic", chunks=chunks, clock=lambda: clock["t"])
    src.listeners.append(lambda s, t: got.append((len(s), round(t, 4))))
    src.start()
    src._t.join(2)
    assert [n for n, _ in got] == [480] * 5 and src.samples == 2400
    assert got[0][1] == 50.0 and got[-1][1] == 50.04          # chunk end time minus its length

    def broken():
        raise OSError("device busy")
        yield
    src = AudioSource("Mic", chunks=broken)
    src.start(); src._t.join(2)
    assert "device busy" in src.error


# ---------------------------------------------------------------- recorder
def record(tmp_path, audio_times, frames=90, name="a.mp4"):
    p = tmp_path / name
    r = Recorder(p, 30, ("mpeg4",), queue_frames=500, audio=True)
    r.start()
    for i in range(frames):
        r.push(frame(30 <= i < 33), T0 + i / 30, block=True)
    for t in audio_times:
        r.push_audio(tone_chunk(t), t)
    return p, r.stop()


def test_sound_is_in_sync_with_the_picture(tmp_path):
    p, st = record(tmp_path, [T0 + k * 0.01 for k in range(300)])
    assert st.state == "done" and st.frames == 90 and abs(st.audio_seconds - 3.0) < 0.01
    with av.open(str(p)) as c:
        assert [(s.type, s.codec_context.name) for s in c.streams] == [("video", "mpeg4"), ("audio", "aac")]
    flash, tone, total = first_flash_and_tone(p)
    assert flash == pytest.approx(1.0, abs=1e-3) and tone == pytest.approx(1.0, abs=0.005)   # same moment
    assert total == pytest.approx(3.0, abs=0.05)


def test_audio_before_the_first_frame_is_cut_and_gaps_are_silence(tmp_path):
    # starts 0.5 s early; a 0.4 s dropout between 1.5 and 1.9 s
    times = [T0 - 0.5 + k * 0.01 for k in range(200)] + [T0 + 1.9 + k * 0.01 for k in range(110)]
    p, st = record(tmp_path, times)
    flash, tone, total = first_flash_and_tone(p)
    assert tone == pytest.approx(1.0, abs=0.005)               # still aligned: the early half second was trimmed
    assert total == pytest.approx(3.0, abs=0.05)               # the dropout became silence, not a shift


def test_a_slow_microphone_clock_stays_in_lip_sync(tmp_path):
    """The microphone delivers 1 % fewer samples than its timestamps say (clock drift): the sound may lag by at most
    the correction threshold (40 ms), never accumulate."""
    times = [T0 + k * 0.0101 for k in range(297)]               # 480 samples every 10.1 ms
    p, _ = record(tmp_path, times)
    flash, tone, total = first_flash_and_tone(p)
    assert abs(tone - flash) <= 0.045 and total == pytest.approx(3.0, abs=0.06)


def test_jitter_does_not_shift_the_sound(tmp_path):
    rng = np.random.default_rng(1)
    times = [T0 + k * 0.01 + rng.uniform(-0.004, 0.004) for k in range(300)]   # arrival-time jitter of ±4 ms
    p, _ = record(tmp_path, times)
    _, tone, total = first_flash_and_tone(p)
    assert tone == pytest.approx(1.0, abs=0.01) and total == pytest.approx(3.0, abs=0.05)


def test_without_audio_there_is_no_audio_stream(tmp_path):
    r = Recorder(tmp_path / "v.mp4", 30, ("mpeg4",), queue_frames=50)
    r.start()
    for i in range(5):
        r.push(frame(), T0 + i / 30, block=True)
    assert not r.push_audio(tone_chunk(T0), T0)
    r.stop()
    with av.open(str(tmp_path / "v.mp4")) as c:
        assert [s.type for s in c.streams] == ["video"]


def test_instant_replay_keeps_and_saves_the_sound(tmp_path):
    rp = InstantReplay(seconds=2.0, max_side=320)
    for i in range(150):                                        # 5 s; the tone at 4.0 s is inside the last 2 s
        t = T0 + i / 30
        rp.push(frame(i == 120), t)
    for k in range(500):
        t = T0 + k * 0.01
        rp.push_audio(tone_chunk(t, start=4.0), t)
    assert rp.memory_bytes() > 2 * RATE * 2 * 2                  # ~2.5 s of 48 kHz stereo 16-bit kept
    st = rp.save(tmp_path / "r.mp4", ("mpeg4",))
    assert st.state == "done" and st.audio_seconds > 1.5
    flash, tone, _ = first_flash_and_tone(tmp_path / "r.mp4")
    assert flash is not None and tone == pytest.approx(flash, abs=0.01)


# ---------------------------------------------------------------- service and settings
class FakeSource:
    instances: list = []

    def __init__(self, device):
        self.device, self.listeners, self.error = device, [], ""
        self.started = self.stopped = False
        FakeSource.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class Settings:
    def __init__(self, **kw):
        self.d = {"recording.folder": "", "recording.replay_enabled": False, "recording.replay_seconds": 30,
                  "recording.audio_device": "", "output.fps": 30, **kw}

    def get(self, k):
        return self.d.get(k)


class Pipe:
    def __init__(self):
        self.taps = []


def test_service_runs_the_microphone_only_when_needed(tmp_path):
    FakeSource.instances = []
    s = Settings(**{"recording.audio_device": "Mic A"})
    svc = RecordingService(Pipe(), s, encoders=("mpeg4",), audio_factory=FakeSource)
    assert svc.audio is None                                   # nothing records: microphone closed
    svc.start_recording(tmp_path / "x.mp4")
    assert svc.audio is not None and svc.audio.started and svc.recorder.audio
    src = svc.audio
    svc.stop_recording()
    assert src.stopped and svc.audio is None
    s.d["recording.replay_enabled"] = True
    svc.apply_settings()
    assert svc.audio is not None and svc.audio.device == "Mic A"
    s.d["recording.audio_device"] = "Mic B"                    # another microphone chosen: reopened
    svc.apply_settings()
    assert svc.audio.device == "Mic B" and FakeSource.instances[-2].stopped
    s.d["recording.audio_device"] = ""                         # no sound
    svc.apply_settings()
    assert svc.audio is None
    svc.close()


def test_service_records_sound_end_to_end(tmp_path):
    s = Settings(**{"recording.audio_device": "Mic"})
    pipe = Pipe()
    svc = RecordingService(pipe, s, encoders=("mpeg4",), audio_factory=FakeSource)
    p = svc.start_recording(tmp_path / "e.mp4")
    k = 0
    for i in range(60):
        t = T0 + i / 30
        for tap in pipe.taps:
            tap(frame(i == 30), t)
        while T0 + k * 0.01 < t + 1 / 30:                      # continuous 10 ms microphone chunks
            ta = T0 + k * 0.01
            for cb in svc.audio.listeners:
                cb(tone_chunk(ta), ta)
            k += 1
        time.sleep(0.002)
    st = svc.stop_recording()
    assert st.state == "done" and st.audio_seconds > 1.5
    flash, tone, _ = first_flash_and_tone(p)
    assert tone == pytest.approx(flash, abs=0.01)


def test_settings_card_microphone_choice(qtbot, paths):
    from vcstudio.app.application import AppContext
    from vcstudio.ui.main_window import MainWindow
    ctx = AppContext(paths, configure_logging=False)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    try:
        page = next(p for p in w.pages if p.key == "settings")
        page.fill_microphones(["Microphone (USB)", "Headset"])
        assert [page.mic.itemData(i) for i in range(page.mic.count())] == ["", "Microphone (USB)", "Headset"]
        page.mic.setCurrentIndex(2)
        assert ctx.settings.get("recording.audio_device") == "Headset"
        page.fill_microphones([])                               # unplugged: still shown, still chosen
        assert page.mic.currentData() == "Headset" and page.mic.count() == 2
        page.replay_secs.setValue(45)
        assert ctx.settings.get("recording.replay_seconds") == 45
    finally:
        ctx.shutdown()


def test_noise_suppression_latency_is_constant_and_compensated(tmp_path):
    """The audio chain's noise suppression delays the sound by one STFT frame (1024 samples); the delay is the same
    for any block size, and the recording service subtracts it, so the tone still lands on the flash."""
    from vcstudio.core.audio_fx import AudioChain, AudioFxConfig
    for blk in (480, 1500):
        ch = AudioChain(AudioFxConfig(denoise=True, denoise_strength=0.0, limiter=False))
        x = np.zeros((24000, 2), np.int16)
        x[12000:12048] = 20000
        y = np.concatenate([ch.process(x[i:i + blk]) for i in range(0, len(x), blk)])
        assert len(y) == len(x) and abs(int(np.argmax(np.abs(y[:, 0]))) - 12000 - 1024) <= 2
        assert ch.latency_s == pytest.approx(1024 / RATE)

    class Settings:
        def __init__(self):
            self.d = {"recording.replay_enabled": False, "recording.replay_seconds": 30,
                      "recording.audio_device": "", "recording.folder": str(tmp_path),
                      "audio": {"denoise": True, "denoise_strength": 0.0, "limiter": False}}

        def get(self, k):
            return self.d[k]

    class Pipe:
        taps: list = []
    svc = RecordingService(Pipe(), Settings())
    assert svc.fx.cfg.denoise
    p = tmp_path / "dn.mp4"
    r = Recorder(p, 30, ("mpeg4",), queue_frames=500, audio=True)
    r.start()
    svc.recorder = r
    for i in range(90):
        r.push(frame(30 <= i < 33), T0 + i / 30, block=True)
    for k in range(300):                       # through the service: the chain runs and the latency is subtracted
        t = T0 + k * 0.01
        svc._on_audio(tone_chunk(t), t)
    svc.recorder = None
    r.stop()
    flash, tone, total = first_flash_and_tone(p)
    assert abs(tone - flash) <= 0.01, (flash, tone)   # without compensation the tone came 21 ms late
