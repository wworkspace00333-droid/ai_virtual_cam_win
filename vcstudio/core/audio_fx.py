"""Audio Studio (0.6.0 Phase 8): a processing chain for the microphone of recordings and instant replay.

Settings section "audio". The chain runs on the audio capture thread (vcstudio/core/audio.py), block by block
(about 10-40 ms of 48 kHz stereo), before the samples reach the recorder; its state (filters, envelopes, noise
profile) carries over between blocks, so block size does not change the result. Order:
  input gain / mute -> noise suppression -> noise gate -> equaliser -> compressor -> automatic gain -> limiter
  -> level meters (input and output, peak and RMS in dBFS)

  noise suppression  spectral gating: 1024-point STFT (hop 512, Hann, overlap-add); the noise floor of each frequency
                     is tracked as a slow running minimum of the magnitude ("minimum statistics"), and bins near it
                     are attenuated by `strength` (never below -24 dB, so speech does not sound underwater). Plain
                     DSP on the CPU (numpy FFT, ~1 % of a core here); not an AI model
  noise gate         sub-blocks of 64 samples: below `threshold_db` the gain falls to -40 dB with `release_ms`,
                     above it opens with `attack_ms`
  equaliser          low shelf 120 Hz, peak 1 kHz (Q 0.9), high shelf 6 kHz (RBJ biquads, scipy sosfilt with state)
  compressor         feed-forward, RMS per 64-sample sub-block, `threshold_db`, `ratio`, attack / release, make-up
  automatic gain     slow (3 s) gain towards `target_db` RMS while speech is present, at most `max_gain_db`
  limiter            peak per sub-block with instant attack and 50 ms release to `ceiling_db`, then a hard clip
The virtual camera has no sound (Windows cameras are video only); this chain is for files the app records. A
system-wide virtual microphone needs a signed audio driver: see reports/VIRTUAL_AUDIO_DEVICE.md.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

RATE = 48000
SUB = 64                                      # samples per envelope step (1.3 ms)
FLOOR_DB = -120.0


def db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-6 else FLOOR_DB


def undb(d: float) -> float:
    return 10 ** (d / 20)


def _f(d: dict, key: str, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(d.get(key, default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AudioFxConfig:
    gain_db: float = 0.0
    mute: bool = False
    denoise: bool = False
    denoise_strength: float = 0.7
    gate: bool = False
    gate_threshold_db: float = -50.0
    gate_attack_ms: float = 5.0
    gate_release_ms: float = 150.0
    eq: bool = False
    eq_low_db: float = 0.0
    eq_mid_db: float = 0.0
    eq_high_db: float = 0.0
    comp: bool = False
    comp_threshold_db: float = -20.0
    comp_ratio: float = 3.0
    comp_attack_ms: float = 10.0
    comp_release_ms: float = 120.0
    comp_makeup_db: float = 0.0
    agc: bool = False
    agc_target_db: float = -20.0
    agc_max_gain_db: float = 18.0
    limiter: bool = True
    limiter_ceiling_db: float = -1.0

    @staticmethod
    def from_settings(settings) -> AudioFxConfig:
        try:
            d = settings.get("audio") or {}
        except KeyError:
            d = {}
        return AudioFxConfig(
            _f(d, "gain_db", -30, 30, 0), bool(d.get("mute", False)), bool(d.get("denoise", False)),
            _f(d, "denoise_strength", 0, 1, 0.7), bool(d.get("gate", False)), _f(d, "gate_threshold_db", -90, 0, -50),
            _f(d, "gate_attack_ms", 0.5, 200, 5), _f(d, "gate_release_ms", 10, 2000, 150), bool(d.get("eq", False)),
            _f(d, "eq_low_db", -15, 15, 0), _f(d, "eq_mid_db", -15, 15, 0), _f(d, "eq_high_db", -15, 15, 0),
            bool(d.get("comp", False)), _f(d, "comp_threshold_db", -60, 0, -20), _f(d, "comp_ratio", 1, 20, 3),
            _f(d, "comp_attack_ms", 0.5, 200, 10), _f(d, "comp_release_ms", 10, 2000, 120),
            _f(d, "comp_makeup_db", 0, 24, 0), bool(d.get("agc", False)), _f(d, "agc_target_db", -40, -6, -20),
            _f(d, "agc_max_gain_db", 0, 30, 18), bool(d.get("limiter", True)), _f(d, "limiter_ceiling_db", -20, 0, -1))


def _coef(ms: float, step: int = SUB) -> float:
    """One-pole smoothing coefficient per `step` samples for a time constant of `ms`."""
    return math.exp(-step / (RATE * ms / 1000.0)) if ms > 0 else 0.0


def biquads(cfg: AudioFxConfig) -> np.ndarray:
    """(3, 6) SOS: RBJ low shelf 120 Hz, peaking 1 kHz, high shelf 6 kHz."""
    out = []
    for kind, f0, gain in (("low", 120.0, cfg.eq_low_db), ("peak", 1000.0, cfg.eq_mid_db),
                           ("high", 6000.0, cfg.eq_high_db)):
        A = 10 ** (gain / 40)
        w0 = 2 * math.pi * f0 / RATE
        cw, sw = math.cos(w0), math.sin(w0)
        if kind == "peak":
            alpha = sw / (2 * 0.9)
            b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
            a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
        else:
            alpha = sw / 2 * math.sqrt(2)
            sq = 2 * math.sqrt(A) * alpha
            if kind == "low":
                b = [A * ((A + 1) - (A - 1) * cw + sq), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sq)]
                a = [(A + 1) + (A - 1) * cw + sq, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sq]
            else:
                b = [A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sq)]
                a = [(A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq]
        out.append([b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]])
    return np.array(out, np.float64)


class _Denoiser:
    N, HOP = 1024, 512

    def __init__(self):
        self.win = np.sqrt(np.hanning(self.N + 1)[:-1]).astype(np.float32)   # sqrt-Hann analysis + synthesis
        self.inbuf = np.zeros((0, 2), np.float32)
        self.prev = np.zeros((self.N, 2), np.float32)       # last analysis frame
        self.ola = np.zeros((self.HOP, 2), np.float32)       # overlap tail waiting for the next frame
        self.noise: np.ndarray | None = None                 # per-bin noise floor
        # one hop of silence up front: output is then always available and the delay is constant (LATENCY)
        self.outbuf = np.zeros((self.HOP, 2), np.float32)

    def process(self, x: np.ndarray, strength: float) -> np.ndarray:
        """Same number of samples out as in; delayed by N samples (21 ms), the recorder's clock is not shifted —
        the delay is below one video frame at 30 fps and constant."""
        self.inbuf = np.concatenate([self.inbuf, x])
        frames = []
        while len(self.inbuf) >= self.HOP:
            hop, self.inbuf = self.inbuf[:self.HOP], self.inbuf[self.HOP:]
            frame = np.concatenate([self.prev[self.HOP:], hop])
            self.prev = frame
            spec = np.fft.rfft(frame * self.win[:, None], axis=0)
            mag = np.abs(spec).mean(axis=1)
            if self.noise is None:
                self.noise = mag.copy()
            else:                                            # minimum statistics: fall fast, rise slowly
                self.noise = np.where(mag < self.noise, 0.7 * self.noise + 0.3 * mag, self.noise * 1.002)
            snr = mag / (self.noise * 3.0 + 1e-9)          # measured: -16 dB on white noise, tone kept
            g = np.clip(1.0 - strength / np.maximum(snr, 1e-3), 0.063, 1.0)  # floor -24 dB
            y = np.fft.irfft(spec * g[:, None], n=self.N, axis=0).astype(np.float32) * self.win[:, None]
            frames.append(self.ola + y[:self.HOP])
            self.ola = y[self.HOP:]
        if frames:
            self.outbuf = np.concatenate([self.outbuf] + frames)
        n = len(x)
        if len(self.outbuf) < n:                             # cannot happen with the hop of silence; kept as a guard
            pad = np.zeros((n - len(self.outbuf), 2), np.float32)
            self.outbuf = np.concatenate([self.outbuf, pad])
        out, self.outbuf = self.outbuf[:n], self.outbuf[n:]
        return out


@dataclass
class Meter:
    in_peak_db: float = FLOOR_DB
    in_rms_db: float = FLOOR_DB
    out_peak_db: float = FLOOR_DB
    out_rms_db: float = FLOOR_DB
    gate_open: bool = True
    comp_gain_db: float = 0.0
    agc_gain_db: float = 0.0
    limiter_gain_db: float = 0.0
    blocks: int = 0


class AudioChain:
    """Stateful processing of (n, 2) int16 blocks; thread-safe configure()."""

    def __init__(self, cfg: AudioFxConfig | None = None):
        self._lock = threading.Lock()
        self.meter = Meter()
        self.configure(cfg or AudioFxConfig())

    @property
    def latency_s(self) -> float:
        """Constant delay the chain adds (noise suppression only: one STFT frame); recordings subtract it from the
        audio timestamps so sound stays in sync with the picture."""
        return _Denoiser.N / RATE if self.cfg.denoise and not self.cfg.mute else 0.0

    def configure(self, cfg: AudioFxConfig) -> None:
        with self._lock:
            old = getattr(self, "cfg", None)
            self.cfg = cfg
            if old is None or (old.eq_low_db, old.eq_mid_db, old.eq_high_db) != (cfg.eq_low_db, cfg.eq_mid_db,
                                                                                  cfg.eq_high_db):
                from scipy.signal import sosfilt_zi
                self.sos = biquads(cfg)
                zi = sosfilt_zi(self.sos)
                self.zi = np.stack([zi * 0.0, zi * 0.0], axis=-1)      # (sections, 2, channels), start silent
            if old is None:
                self.gate_g = 1.0
                self.comp_g = 1.0
                self.agc_g = 1.0
                self.lim_g = 1.0
                self.den = _Denoiser()
            elif not cfg.denoise:
                self.den = _Denoiser()

    def process(self, block: np.ndarray) -> np.ndarray:
        """(n, 2) int16 -> (n, 2) int16."""
        with self._lock:
            cfg = self.cfg
            x = block.astype(np.float32) / 32768.0
            n = len(x)
            m = self.meter
            if n:
                m.in_peak_db = db(float(np.abs(x).max()))
                m.in_rms_db = db(float(np.sqrt(np.mean(x * x))))
            if cfg.mute or n == 0:
                out = np.zeros_like(block)
                m.out_peak_db = m.out_rms_db = FLOOR_DB
                m.blocks += 1
                return out
            if cfg.gain_db:
                x *= undb(cfg.gain_db)
            if cfg.denoise:
                x = self.den.process(x, cfg.denoise_strength)
            if cfg.gate or cfg.comp or cfg.agc:
                x = self._dynamics(x, cfg)
            if cfg.eq and (cfg.eq_low_db or cfg.eq_mid_db or cfg.eq_high_db):
                from scipy.signal import sosfilt
                y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
                x = y.astype(np.float32)
            if cfg.limiter:
                x = self._limit(x, cfg)
            np.clip(x, -1.0, 32767 / 32768, out=x)
            m.out_peak_db = db(float(np.abs(x).max()))
            m.out_rms_db = db(float(np.sqrt(np.mean(x * x))))
            m.blocks += 1
            return (x * 32768.0).astype(np.int16)

    def _envelope_steps(self, x: np.ndarray) -> tuple[np.ndarray, int]:
        n = len(x)
        k = max(1, (n + SUB - 1) // SUB)
        pad = np.zeros((k * SUB - n, 2), np.float32)
        xs = np.concatenate([x, pad]).reshape(k, SUB, 2)
        rms = np.sqrt(np.mean(xs * xs, axis=(1, 2)))
        return rms, k

    @staticmethod
    def _apply_steps(x: np.ndarray, gains: np.ndarray) -> np.ndarray:
        """Per-sub-block gains, linearly interpolated over the samples (no zipper noise)."""
        n = len(x)
        centers = (np.arange(len(gains)) + 0.5) * SUB
        g = np.interp(np.arange(n), centers, gains).astype(np.float32)
        return x * g[:, None]

    def _dynamics(self, x: np.ndarray, cfg: AudioFxConfig) -> np.ndarray:
        rms, k = self._envelope_steps(x)
        lvl = np.array([db(float(r)) for r in rms])
        gains = np.ones(k, np.float32)
        ga, gr = _coef(cfg.gate_attack_ms), _coef(cfg.gate_release_ms)
        ca, cr = _coef(cfg.comp_attack_ms), _coef(cfg.comp_release_ms)
        agc_c = _coef(3000.0)
        thr, ratio = cfg.comp_threshold_db, cfg.comp_ratio
        for i in range(k):
            g = 1.0
            if cfg.gate:
                target = 1.0 if lvl[i] > cfg.gate_threshold_db else undb(-40)
                c = ga if target > self.gate_g else gr
                self.gate_g = target + (self.gate_g - target) * c
                g *= self.gate_g
            if cfg.comp:
                over = lvl[i] - thr
                target = undb(-over * (1 - 1 / ratio)) if over > 0 else 1.0
                c = ca if target < self.comp_g else cr
                self.comp_g = target + (self.comp_g - target) * c
                g *= self.comp_g * undb(cfg.comp_makeup_db)
            if cfg.agc and lvl[i] > cfg.gate_threshold_db + 6 and lvl[i] > -70:     # only while there is a voice
                want = min(undb(cfg.agc_max_gain_db), max(undb(-cfg.agc_max_gain_db),
                                                          undb(cfg.agc_target_db - lvl[i])))
                self.agc_g = want + (self.agc_g - want) * agc_c
            if cfg.agc:
                g *= self.agc_g
            gains[i] = g
        self.meter.gate_open = self.gate_g > 0.5
        self.meter.comp_gain_db = db(self.comp_g) if cfg.comp else 0.0
        self.meter.agc_gain_db = db(self.agc_g) if cfg.agc else 0.0
        return self._apply_steps(x, gains)

    def _limit(self, x: np.ndarray, cfg: AudioFxConfig) -> np.ndarray:
        n = len(x)
        k = max(1, (n + SUB - 1) // SUB)
        pad = np.zeros((k * SUB - n, 2), np.float32)
        peaks = np.abs(np.concatenate([x, pad]).reshape(k, SUB * 2)).max(axis=1)
        ceil = undb(cfg.limiter_ceiling_db)
        rel = _coef(50.0)
        gains = np.ones(k, np.float32)
        for i in range(k):
            need = min(1.0, ceil / peaks[i]) if peaks[i] > 1e-9 else 1.0
            self.lim_g = need if need < self.lim_g else need + (self.lim_g - need) * rel   # instant attack
            gains[i] = self.lim_g
        self.meter.limiter_gain_db = db(float(gains.min()))
        y = x * np.repeat(gains, SUB)[:n, None]                # per sub-block: a peak never passes through
        return np.clip(y, -ceil, ceil)
