"""Streaming music analysis (PLAN.md §4.2).

Feed mono float32 blocks of any size; every hop (512 samples) it computes band
levels with automatic gain, spectral-flux onsets, kick onsets (30-120 Hz), a
tempo from the onset envelope's autocorrelation (90-160 BPM), and a beat grid
fitted to the *kick* onsets, never to the full mix (which lands on off-beats).
Beats are emitted from the predicted grid, so they arrive on time, not late.

Times are "audio time": seconds of audio analysed, at the centre of each FFT frame.
`clock` maps audio time to time.monotonic() for rendering.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

BANDS = {"sub": (20, 60), "bass": (60, 250), "mid": (250, 2000), "high": (2000, 12000)}
KICK = (30, 120)
N_SPECTRUM = 64


@dataclass
class AudioState:
    sub: float = 0.0
    bass: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    level: float = 0.0
    beat: float = 0.0            # 1 at a beat, decays over ~1/4 beat
    beat_phase: float = 0.0      # 0..1 within the current beat
    bar_phase: float = 0.0       # 0..1 within a 4-beat bar
    bpm: float = 0.0
    locked: bool = False         # tempo estimate stable
    bar: int = 0
    spectrum: np.ndarray = field(default_factory=lambda: np.zeros(N_SPECTRUM, np.float32))
    events: list = field(default_factory=list)   # 'beat', 'onset', 'kick', 'subdivision'
    beat_count: int = 0


class _AutoGain:
    """Normalizes a dB level against its own recent range (fast up, slow down)."""

    def __init__(self, fps: float, span_db: float = 30.0):
        self.hi = -120.0
        self.lo = -60.0
        self.fps = fps
        self.span = span_db

    def __call__(self, db: float) -> float:
        dt = 1.0 / self.fps
        self.hi = max(db, self.hi - 2.0 * dt)          # peak falls 2 dB/s
        self.lo = min(db, self.lo + 1.5 * dt)          # floor rises 1.5 dB/s
        self.lo = max(self.lo, self.hi - 60.0)
        rng = max(self.hi - self.lo, 6.0)
        rng = min(rng, self.span)
        return float(np.clip((db - (self.hi - rng)) / rng, 0.0, 1.0))


class Analyser:
    def __init__(self, sr: int = 48000, n: int = 2048, hop: int = 512, history_s: float = 6.0):
        self.sr, self.n, self.hop = sr, n, hop
        self.fps = sr / hop
        self.window = np.hanning(n).astype(np.float32)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        self.band_bins = {k: (freqs >= a) & (freqs < b) for k, (a, b) in BANDS.items()}
        self.kick_bins = (freqs >= KICK[0]) & (freqs < KICK[1])
        self.click_bins = (freqs >= 1500) & (freqs < 8000)
        self.flux_bins = (freqs >= 30) & (freqs < 11000)
        edges = np.geomspace(30, min(16000, sr / 2 - 1), N_SPECTRUM + 1)
        self.spec_idx = np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1)
        self.gains = {k: _AutoGain(self.fps) for k in list(BANDS) + ["level"]}
        self.spec_gain = _AutoGain(self.fps, span_db=40.0)

        self._buf = np.zeros(n, np.float32)
        self._pending = np.zeros(0, np.float32)
        self._samples = 0
        self._prev_logp = None
        self._prev_kick = None
        self._prev_click = None
        self._max_power = 1e-12

        H = int(history_s * self.fps)
        self.onset_env = deque(maxlen=H)
        self.kick_env = deque(maxlen=H)
        self._flux_win = deque(maxlen=int(self.fps))     # ~1 s for adaptive thresholds
        self._kick_win = deque(maxlen=int(self.fps))
        self._peak_wait = deque(maxlen=7)                # (t, flux, kflux) for 3-hop local-max test
        self._last_onset = -1.0
        self._last_kick = -1.0
        self._kick_peak = 0.0
        self._hops_since_tempo = 0

        self.bpm = 0.0
        self.tempo_conf = 0.0
        self._bpm_hist = deque(maxlen=5)
        self.locked = False
        self.last_beat = None          # audio time of the most recent grid beat
        self.period = 0.5
        self.beat_count = 0
        self.beat_times: list[float] = []   # emitted beats (audio time), for tests/debug
        self.kick_times: list[float] = []
        self.onset_times: list[float] = []
        self.bpm_track: list[tuple[float, float]] = []

        self._lock = threading.Lock()
        self._events: list[str] = []
        self._state = AudioState()
        self._beat_env = 0.0
        self._wall_at_audio0 = None     # time.monotonic() - audio_time, smoothed
        self.latency_s = 0.0

    # ------------------------------------------------------------------ input
    def feed(self, samples: np.ndarray, wall: float | None = None) -> None:
        samples = np.asarray(samples, np.float32).reshape(-1)
        data = np.concatenate([self._pending, samples]) if len(self._pending) else samples
        i = 0
        while len(data) - i >= self.hop:
            self._buf = np.concatenate([self._buf[self.hop:], data[i:i + self.hop]])
            self._samples += self.hop
            self._process()
            i += self.hop
        self._pending = data[i:].copy()
        if wall is not None:
            # the newest sample was captured just before `wall`
            offset = wall - (self._samples + len(self._pending)) / self.sr
            if self._wall_at_audio0 is None or abs(offset - self._wall_at_audio0) > 0.25:
                self._wall_at_audio0 = offset
            else:
                self._wall_at_audio0 += 0.02 * (offset - self._wall_at_audio0)

    @property
    def audio_time(self) -> float:
        return (self._samples - self.n / 2) / self.sr

    # ------------------------------------------------------------------ per hop
    def _process(self) -> None:
        t = self.audio_time
        spec = np.fft.rfft(self._buf * self.window)
        P = (spec.real ** 2 + spec.imag ** 2).astype(np.float64)
        total = P.sum()
        self._max_power = max(total, self._max_power * (1 - 0.2 / self.fps))
        eps = self._max_power * 1e-7 + 1e-20  # flux floor relative to recent loudness: gain-invariant

        st = self._state
        levels = {}
        for k, mask in self.band_bins.items():
            db = 10 * np.log10(P[mask].sum() + 1e-20)
            levels[k] = self.gains[k](db)
        levels["level"] = self.gains["level"](10 * np.log10(total + 1e-20))
        for k, v in levels.items():
            cur = getattr(st, k)
            a = 0.6 if v > cur else 0.12   # fast attack, slower release
            setattr(st, k, cur + a * (v - cur))
        sp = np.add.reduceat(P, self.spec_idx[:-1])[:N_SPECTRUM]
        sdb = 10 * np.log10(sp + 1e-20)
        top = sdb.max()
        self.spec_gain(top)
        rng = 40.0
        st.spectrum = np.clip((sdb - (self.spec_gain.hi - rng)) / rng, 0, 1).astype(np.float32)

        logp = np.log(P[self.flux_bins] + eps)
        flux = 0.0 if self._prev_logp is None else float(np.maximum(logp - self._prev_logp, 0).mean())
        self._prev_logp = logp
        k_log = np.log(P[self.kick_bins].sum() + eps)
        kflux = 0.0 if self._prev_kick is None else max(0.0, float(k_log - self._prev_kick))
        self._prev_kick = k_log
        c_log = np.log(P[self.click_bins].sum() + eps)
        cflux = 0.0 if self._prev_click is None else max(0.0, float(c_log - self._prev_click))
        self._prev_click = c_log
        self.onset_env.append(flux)
        self.kick_env.append(kflux)
        self._flux_win.append(flux)
        self._kick_win.append(kflux)
        self._peak_wait.append((t, flux, kflux, cflux))
        self._detect_onsets()

        self._hops_since_tempo += 1
        if self._hops_since_tempo >= self.fps / 2 and len(self.onset_env) >= 4 * self.fps:
            self._hops_since_tempo = 0
            self._estimate_tempo(t)
        self._advance_grid(t)

    def _detect_onsets(self) -> None:
        if len(self._peak_wait) < 7:
            return
        t, f, kf, _ = self._peak_wait[3]
        fl = [x[1] for x in self._peak_wait]
        kl = [x[2] for x in self._peak_wait]
        click = max(x[3] for x in list(self._peak_wait)[1:6])  # within ±2 hops (~±23 ms)
        fw, kw = np.array(self._flux_win), np.array(self._kick_win)
        med, mad = np.median(fw), np.median(np.abs(fw - np.median(fw))) + 1e-9
        if f == max(fl) and f > med + 3 * mad and f > 0.02 and t - self._last_onset > 0.08:
            self._last_onset = t
            self.onset_times.append(t)
            self._emit("onset")
            if self.locked and self.last_beat is not None:
                ph = ((t - self.last_beat) / self.period) % 1.0
                if min(ph, 1 - ph) > 0.12:
                    self._emit("subdivision")
            elif not self.locked:
                self._emit("subdivision")
        kmed, kmad = np.median(kw), np.median(np.abs(kw - np.median(kw))) + 1e-9
        # A kick must also be strong relative to recent kicks: the off-beat bassline and
        # ghost hits share the 30-120 Hz band but are less than half as sharp. And a drum
        # kick has a beater click (1.5-8 kHz) at the same moment, a bass note doesn't;
        # very strong hits are accepted without one (sub-only 808 kicks).
        self._kick_peak = max(kf, self._kick_peak * (1 - 0.08 / self.fps))
        strong = kf >= 0.8 * self._kick_peak
        if (kf == max(kl) and kf > kmed + 4 * kmad and kf > 0.3 and kf >= 0.5 * self._kick_peak
                and (click > 0.5 or strong) and t - self._last_kick > 0.15):
            self._last_kick = t
            self.kick_times.append(t)
            self._emit("kick")
            if not self.locked:  # until the tempo settles, kicks stand in for beats
                self._beat(t)

    # ------------------------------------------------------------------ tempo + grid
    def _estimate_tempo(self, t: float) -> None:
        env = np.asarray(self.onset_env, np.float64)
        env = env - env.mean()
        n = len(env)
        f = np.fft.rfft(env, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        if ac[0] <= 0:
            return
        ac /= ac[0]
        best, best_score = 0.0, -1.0
        for bpm in np.arange(90.0, 160.01, 0.25):
            lag = self.fps * 60.0 / bpm
            score = self._interp(ac, lag) + 0.5 * self._interp(ac, 2 * lag) if 2 * lag < n - 1 else self._interp(ac, lag)
            if score > best_score:
                best, best_score = bpm, score
        best = self._refine_bpm(ac, best)
        self.tempo_conf = float(best_score)
        self._bpm_hist.append(best)
        med = float(np.median(self._bpm_hist))
        stable = len(self._bpm_hist) >= 3 and max(abs(b - med) for b in list(self._bpm_hist)[-3:]) <= 1.5
        if best_score > 0.15 and stable:
            new_period = 60.0 / med
            if not self.locked:
                self.locked = True
            self.bpm = med
            self.period = new_period
            self._fit_phase(t)
        self.bpm_track.append((t, self.bpm if self.locked else best))

    def _refine_bpm(self, ac: np.ndarray, bpm: float) -> float:
        """Sub-hop beat period: parabolic peak fits at 1, 2, 3 and 4 beats, averaged.

        Linear interpolation on the coarse grid pulls the estimate toward whole hops
        (at 48 kHz, 123 BPM is 45.7 hops and came out as 122.25)."""
        L = self.fps * 60.0 / bpm
        ests, weights = [], []
        for k in (1, 2, 3, 4):
            c = int(round(k * L))
            if c + 3 >= len(ac):
                break
            i = c - 2 + int(np.argmax(ac[c - 2:c + 3]))
            y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
            den = y0 - 2 * y1 + y2
            off = 0.5 * (y0 - y2) / den if den < 0 else 0.0
            if y1 > 0:
                ests.append((i + off) / k)
                weights.append(y1 * k)  # longer lags pin the period down more precisely
        if not ests:
            return bpm
        refined = 60.0 * self.fps / float(np.average(ests, weights=weights))
        return refined if abs(refined - bpm) < 2.0 else bpm

    def _fit_phase(self, t: float) -> None:
        """Place the grid where the kicks are: maximize kick flux summed at grid points."""
        k = np.asarray(self.kick_env, np.float64)
        n = len(k)
        per = self.period * self.fps          # in hops
        best_phi, best = 0.0, -1.0
        for phi in np.arange(0.0, per, 0.25):  # hops back from the newest hop to the last beat
            idx = (n - 1) - phi - per * np.arange(int((n - 1 - phi) / per) + 1)
            score = sum(self._interp(k, i) for i in idx)
            if score > best:
                best, best_phi = score, phi
        fitted = t - best_phi / self.fps     # audio time of the most recent grid beat
        if self.last_beat is None:
            self.last_beat = fitted
            return
        # compare with where our running grid says the latest beat is
        steps = round((fitted - self.last_beat) / self.period)
        predicted = self.last_beat + steps * self.period
        err = fitted - predicted
        if abs(err) < 0.2 * self.period:
            self.last_beat += 0.5 * err      # small drift: nudge toward the kicks
        else:
            self.last_beat = fitted          # tempo change or bad start: re-anchor (fitted <= t)

    def _advance_grid(self, t: float) -> None:
        if not self.locked or self.last_beat is None:
            return
        while self.last_beat + self.period <= t:
            self.last_beat += self.period
            self._beat(self.last_beat)

    def _beat(self, at: float) -> None:
        self.beat_count += 1
        self.beat_times.append(at)
        self._emit("beat")
        with self._lock:
            self._beat_env = 1.0

    @staticmethod
    def _interp(a: np.ndarray, x: float) -> float:
        i = int(x)
        if i < 0 or i >= len(a) - 1:
            return 0.0
        fr = x - i
        return float(a[i] * (1 - fr) + a[i + 1] * fr)

    def _emit(self, name: str) -> None:
        with self._lock:
            self._events.append(name)

    # ------------------------------------------------------------------ output
    def state(self, wall: float | None = None) -> AudioState:
        """Snapshot for rendering; drains events. `wall` = time.monotonic() of the frame."""
        with self._lock:
            events, self._events = self._events, []
        st = self._state
        now_audio = self.audio_time
        if wall is not None and self._wall_at_audio0 is not None:
            now_audio = wall - self._wall_at_audio0 - self.n / 2 / self.sr + self.latency_s
        out = AudioState(sub=st.sub, bass=st.bass, mid=st.mid, high=st.high, level=st.level,
                         spectrum=st.spectrum.copy(), events=events, bpm=self.bpm, locked=self.locked,
                         beat_count=self.beat_count)
        if self.locked and self.last_beat is not None:
            since = now_audio - self.last_beat
            out.beat_phase = float((since / self.period) % 1.0)
            out.bar = self.beat_count // 4
            out.bar_phase = float(((self.beat_count % 4) + out.beat_phase) / 4.0)
            out.beat = float(np.exp(-4.0 * out.beat_phase))
        else:
            out.beat = self._beat_env
            self._beat_env *= 0.85
        return out


def analyse_file(samples: np.ndarray, sr: int, block: int = 1024) -> Analyser:
    """Offline helper (tests, tools): run a whole mono signal through the analyser."""
    a = Analyser(sr=sr)
    for i in range(0, len(samples), block):
        a.feed(samples[i:i + block])
    return a
