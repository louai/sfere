"""A tempo from the ball's movement: how fast the performer's arcs and loops repeat.

The ball's velocity (x and y) over the last 8 seconds is resampled to 50 Hz and
autocorrelated; the strongest repeating period is the movement's cycle, refined between
samples with a parabola. One cycle counts as one beat, then the tempo is moved by octaves
(×2, ÷2) into the chosen genre's range, so a slow loop and a fast flick both land on a
danceable BPM. The *target* only moves once a new tempo has held for a few seconds.
"""
from __future__ import annotations

import collections
import math

import numpy as np

# BPM ranges per genre (Music tab); drum & bass also accepts half time below its range
GENRES = {
    "house": ("House / tech house", 118.0, 128.0, "House"),
    "techno": ("Techno", 125.0, 140.0, "Techno"),
    "trance": ("Trance / progressive", 128.0, 140.0, "Trance"),
    "dnb": ("Drum & bass", 165.0, 178.0, "Drum & Bass"),
}
RATE = 50.0               # Hz the velocity is resampled to
WINDOW = 8.0              # seconds analysed
MIN_PERIOD, MAX_PERIOD = 0.25, 2.0   # cycles from 240 down to 30 per minute


def fold(bpm: float, lo: float, hi: float) -> float:
    """Move bpm by octaves into [lo, hi]; if no octave lands inside, the nearest edge."""
    if bpm <= 0:
        return (lo + hi) / 2
    cands = [bpm * 2.0 ** k for k in range(-4, 5)]
    inside = [c for c in cands if lo <= c <= hi]
    if inside:
        return min(inside, key=lambda c: abs(math.log(c / bpm)))
    best = min(cands, key=lambda c: min(abs(math.log(c / lo)), abs(math.log(c / hi))))
    return min(max(best, lo), hi)


def cycle_period(t: np.ndarray, vx: np.ndarray, vy: np.ndarray) -> tuple[float, float]:
    """(period in seconds, confidence 0..1) of the repeating part of a 2-D velocity."""
    if len(t) < 10 or t[-1] - t[0] < 4.0:
        return 0.0, 0.0
    grid = np.arange(t[0], t[-1], 1.0 / RATE)
    total = np.zeros(len(grid))
    norm = 0.0
    for v in (vx, vy):
        s = np.interp(grid, t, v)
        s = s - s.mean()
        e = float(np.dot(s, s))
        if e < 1e-9:
            continue
        n = len(s)
        f = np.fft.rfft(s, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        total += ac                  # weighted by energy: the axis that moves more counts more
        norm += e
    if norm <= 0:
        return 0.0, 0.0
    n = len(total)
    ac = total / norm * n / np.maximum(n - np.arange(n), 1)    # unbiased: long lags aren't penalised
    lo, hi = int(MIN_PERIOD * RATE), min(int(MAX_PERIOD * RATE), n - 2)
    if hi <= lo + 2:
        return 0.0, 0.0
    seg = ac[lo:hi + 1]
    peaks = [i for i in range(1, len(seg) - 1) if seg[i] >= seg[i - 1] and seg[i] > seg[i + 1] and seg[i] > 0]
    if not peaks:
        return 0.0, 0.0
    best = max(seg[i] for i in peaks)
    # the shortest strong period: a cycle also repeats at twice and three times its length
    i = next(i for i in peaks if seg[i] >= 0.85 * best)
    a, b, c = seg[i - 1], seg[i], seg[i + 1]
    den = a - 2 * b + c
    off = 0.5 * (a - c) / den if den else 0.0
    return (lo + i + off) / RATE, float(min(1.0, max(0.0, b)))


class MotionTempo:
    """Feed it the ball each camera frame; read `raw_bpm`, `confidence` and `target_bpm`."""

    HOLD = 6.0            # seconds a new tempo must hold before the target moves
    TOLERANCE = 0.03      # a tempo within 3 % of the target is the same tempo
    MIN_CONFIDENCE = 0.4

    def __init__(self, genre: str = "techno"):
        self.samples: collections.deque = collections.deque()
        self.genre = genre
        self.raw_bpm = 0.0            # the movement's tempo, folded into the genre range
        self.cycle_s = 0.0            # the movement's own period, unfolded
        self.confidence = 0.0
        self.target_bpm = self.center
        self.locked = False           # hold the target (or manual_bpm, if set)
        self.manual_bpm: float | None = None
        self._candidate = None        # (bpm, since)
        self._next_estimate = 0.0

    @property
    def range(self) -> tuple[float, float]:
        _, lo, hi, _ = GENRES.get(self.genre, GENRES["techno"])
        return lo, hi

    @property
    def center(self) -> float:
        lo, hi = self.range
        return round((lo + hi) / 2)

    def set_genre(self, genre: str) -> None:
        if genre == self.genre:
            return
        self.genre = genre
        self.target_bpm = fold(self.target_bpm, *self.range)
        self._candidate = None

    def feed(self, t: float, vx: float, vy: float, visible: float) -> None:
        self.samples.append((t, vx, vy, visible))
        while self.samples and t - self.samples[0][0] > WINDOW:
            self.samples.popleft()

    def update(self, now: float) -> float:
        """Re-estimate (twice a second) and move the target if a new tempo has held. Returns it."""
        if self.manual_bpm:
            self.target_bpm = float(self.manual_bpm)
            return self.target_bpm
        if now < self._next_estimate:
            return self.target_bpm
        self._next_estimate = now + 0.5
        arr = np.array(self.samples) if self.samples else np.zeros((0, 4))
        seen = arr[arr[:, 3] > 0.5] if len(arr) else arr
        if len(arr) == 0 or len(seen) < 0.7 * len(arr):
            self.confidence = 0.0          # the ball is lost too often to say
            return self.target_bpm
        period, conf = cycle_period(seen[:, 0], seen[:, 1], seen[:, 2])
        self.confidence = conf
        if period <= 0:
            return self.target_bpm
        self.cycle_s = period
        self.raw_bpm = fold(60.0 / period, *self.range)
        if self.locked or conf < self.MIN_CONFIDENCE:
            return self.target_bpm
        if abs(self.raw_bpm / self.target_bpm - 1) <= self.TOLERANCE:
            self._candidate = None
            self.target_bpm += 0.1 * (self.raw_bpm - self.target_bpm)    # follow small drift gently
        elif self._candidate is None or abs(self.raw_bpm / self._candidate[0] - 1) > self.TOLERANCE:
            self._candidate = (self.raw_bpm, now)
        elif now - self._candidate[1] >= self.HOLD:
            self.target_bpm, self._candidate = self.raw_bpm, None
        return self.target_bpm
