"""The mixer: two decks on one sample clock, beat-matched crossfades, mid-track tempo changes.

Every deck holds a track already stretched to the set's tempo (prepare.py), so two decks at
the same tempo have the same beat length: starting the incoming track on the outgoing one's
bar lines up every beat after it. A transition is an equal-power crossfade over N bars with
a bass swap: the incoming track comes in without its low end, and halfway through the two
basses trade places (two kicks at once just thud).

`render(frames)` is called from the audio callback and only slices arrays; anything slow
(stretching, filtering) happens on the session's worker thread, which hands finished arrays
over under a lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from .prepare import SR, Prepared

SPLICE = int(0.02 * SR)          # 20 ms crossfade where re-rendered audio joins


def low_band(pcm: np.ndarray, cutoff: float = 180.0) -> np.ndarray:
    """The low end (kick, bass) of a stretch of audio: zero-phase, soft above `cutoff`."""
    n = len(pcm)
    if n == 0:
        return np.zeros((0, 2), np.float32)
    spec = np.fft.rfft(np.asarray(pcm, np.float32), axis=0)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = np.clip((cutoff * 1.4 - f) / (cutoff * 0.8), 0.0, 1.0)
    mask = 0.5 - 0.5 * np.cos(np.pi * mask)
    return np.fft.irfft(spec * mask[:, None], n, axis=0).astype(np.float32)


@dataclass
class Deck:
    prep: Prepared
    start: int                     # clock sample at which pcm[0] plays
    pcm: np.ndarray = field(repr=False)        # float16 (n, 2), stretched
    # out-time mapping of the current segment: pcm index `seg_at` plays the original's
    # second `seg_orig`, at `seg_ratio` (original seconds per output second)
    seg_at: int = 0
    seg_orig: float = 0.0
    seg_ratio: float = 1.0
    low: dict = field(default_factory=dict, repr=False)   # pcm index -> low band from there

    @classmethod
    def load(cls, prep: Prepared, start: int) -> "Deck":
        return cls(prep=prep, start=start, pcm=prep.pcm.astype(np.float16), seg_at=0,
                   seg_orig=prep.start, seg_ratio=prep.ratio)

    @property
    def tempo(self) -> float:
        return self.prep.grid.bpm * self.seg_ratio

    @property
    def end(self) -> int:
        return self.start + len(self.pcm)

    def clock_of(self, orig: float) -> int:
        """The clock sample at which the original's second `orig` plays (current segment)."""
        return self.start + self.seg_at + int(round((orig - self.seg_orig) / self.seg_ratio * SR))

    def orig_at(self, clock: int) -> float:
        return self.seg_orig + (clock - self.start - self.seg_at) / SR * self.seg_ratio

    def next_bar(self, clock: int, ahead_s: float = 0.0) -> float:
        """The original's second of the first bar starting at or after clock + ahead_s."""
        g = self.prep.grid
        t = self.orig_at(clock + int(ahead_s * SR))
        k = np.ceil((t - g.first) / g.period)
        k += (g.downbeat - k) % 4
        return g.first + k * g.period


@dataclass
class Transition:
    out: Deck
    inn: Deck
    t0: int                        # clock sample the crossfade starts (the in deck's first bar)
    length: int                    # samples
    beat: int                      # one beat, for the bass swap ramp


class Engine:
    def __init__(self, volume: float = 1.0):
        self.lock = threading.Lock()
        self.clock = 0             # samples rendered so far
        self.deck: Deck | None = None          # the one playing (the outgoing one during a mix)
        self.mix: Transition | None = None
        self.volume = volume
        self.on_change = None      # called (from the audio thread) when a transition finishes

    # ----------------------------------------------------------------- control (worker thread)
    def play(self, deck: Deck) -> None:
        with self.lock:
            self.deck, self.mix = deck, None

    def schedule(self, out: Deck, inn: Deck, t0: int, bars: int) -> None:
        """Mix `inn` in, starting at clock `t0` (on a bar of `out`), over `bars` bars."""
        beat = int(round(60.0 / inn.tempo * SR))
        length = bars * 4 * beat
        inn.low[0] = low_band(inn.pcm[:length]).astype(np.float16)
        o = t0 - out.start
        out.low[o] = low_band(out.pcm[o:o + length]).astype(np.float16)
        with self.lock:
            self.mix = Transition(out=out, inn=inn, t0=t0, length=length, beat=beat)

    def stop_deck(self) -> None:
        with self.lock:
            self.deck, self.mix = None, None

    def cancel_mix(self) -> None:
        with self.lock:
            self.mix = None

    def splice(self, deck: Deck, at_clock: int, orig: float, ratio: float, pcm: np.ndarray) -> bool:
        """From `at_clock` on (the original's second `orig`), play `pcm` (stretched by `ratio`)."""
        i = at_clock - deck.start
        with self.lock:
            if i <= self.clock - deck.start + SPLICE or i >= len(deck.pcm):
                return False                       # too late: that moment has passed
        new = np.array(pcm, np.float32)            # a copy: decoded audio is read-only
        head = deck.pcm[:i].astype(np.float32)
        fade = np.linspace(0.0, 1.0, SPLICE, dtype=np.float32)[:, None]
        tail_old = deck.pcm[i:i + SPLICE].astype(np.float32)
        if len(tail_old) == SPLICE and len(new) >= SPLICE:
            new[:SPLICE] = tail_old * (1 - fade) + new[:SPLICE] * fade
        joined = np.concatenate([head, new]).astype(np.float16)
        with self.lock:
            if i <= self.clock - deck.start:
                return False
            deck.pcm, deck.seg_at, deck.seg_orig, deck.seg_ratio = joined, i, orig, ratio
            deck.low = {}
        return True

    # ----------------------------------------------------------------- audio thread
    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), np.float32)
        with self.lock:
            c0 = self.clock
            mix, deck = self.mix, self.deck
            if mix is None:
                if deck is not None:
                    out += self._slice(deck, c0, frames)
            else:
                x = (np.arange(frames) + c0 - mix.t0) / mix.length
                xc = np.clip(x, 0.0, 1.0)
                g_out = np.where(x < 0, 1.0, np.cos(xc * np.pi / 2)).astype(np.float32)
                g_in = np.where(x < 0, 0.0, np.sin(xc * np.pi / 2)).astype(np.float32)
                # bass swap around the middle, over one beat
                ramp = np.clip(((np.arange(frames) + c0 - mix.t0) - mix.length / 2) / mix.beat + 0.5, 0, 1)
                out += self._slice(mix.out, c0, frames, low_keep=np.where(x < 0, 1.0, 1 - ramp)) * g_out[:, None]
                if (x >= 0).any():
                    out += self._slice(mix.inn, c0, frames, low_keep=ramp) * g_in[:, None]
                if x[-1] >= 1.0:                    # done: the incoming deck plays on alone
                    self.deck, self.mix = mix.inn, None
                    if self.on_change:
                        self.on_change(mix.inn)
            self.clock = c0 + frames
        return out * self.volume

    @staticmethod
    def _slice(deck: Deck, c0: int, frames: int, low_keep: np.ndarray | None = None) -> np.ndarray:
        out = np.zeros((frames, 2), np.float32)
        a, b = c0 - deck.start, c0 - deck.start + frames
        lo, hi = max(a, 0), min(b, len(deck.pcm))
        if hi <= lo:
            return out
        out[lo - a:hi - a] = deck.pcm[lo:hi]
        if low_keep is not None:
            for at, low in deck.low.items():        # take out the part of the low end not kept
                l0, l1 = max(lo, at), min(hi, at + len(low))
                if l1 > l0:
                    cut = (1.0 - low_keep[l0 - a:l1 - a])[:, None]
                    out[l0 - a:l1 - a] -= low[l0 - at:l1 - at].astype(np.float32) * cut
        return out
