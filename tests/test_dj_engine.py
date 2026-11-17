"""The mixer: beat-aligned transitions, even loudness through the crossfade, the bass swap, splices."""
from __future__ import annotations

import numpy as np

from sfere.music.dj import Deck, Engine
from sfere.music.prepare import SR, Grid, Prepared

BPM = 128.0
P = 60.0 / BPM


def clicks(first, seconds, freq, bass=0.0, noise=0.0, seed=0):
    """A click on every beat from `first`; optionally a bass tone and noise."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = bass * np.sin(2 * np.pi * 50 * t) + noise * np.random.default_rng(seed).normal(0, 1, n)
    burst = np.sin(2 * np.pi * freq * np.arange(240) / SR) * np.hanning(240)
    for b in np.arange(first, seconds - 0.01, P):
        s = int(b * SR)
        x[s:s + 240] += burst[: n - s]
    return np.stack([x, x], 1).astype(np.float32)


def prepared(first, seconds=60.0, **kw):
    pcm = clicks(first, seconds, **kw)
    g = Grid(period=P, first=first, downbeat=0)
    p = Prepared(meta={"id": "x"}, file=None, grid=g, mix_in=first, mix_out=first + 16 * 4 * P, tempo=BPM,
                 start=first)
    p.pcm = pcm[int(first * SR):]            # as prepare() does: from the first bar, at its tempo
    return p


def render_all(eng, seconds, block=1024):
    return np.concatenate([eng.render(block) for _ in range(int(seconds * SR / block))])


def onsets(x, thresh=0.3):
    e = np.abs(x[:, 0])
    above = np.flatnonzero((e[1:] > thresh) & (e[:-1] <= thresh))
    keep = [above[0]] if len(above) else []
    for i in above[1:]:
        if i - keep[-1] > int(0.05 * SR):
            keep.append(i)
    return np.array(keep) / SR


def test_transition_lines_up_the_beats():
    a, b = prepared(0.40, freq=1000), prepared(1.13, freq=3000)      # different offsets in their files
    eng = Engine()
    da = Deck.load(a, start=0)
    eng.play(da)
    t0 = da.clock_of(a.grid.first + 8 * 4 * P)                       # A's bar 8
    db = Deck.load(b, start=t0)
    eng.schedule(da, db, t0, bars=4)
    out = render_all(eng, 30.0)
    beats = onsets(out)
    grid = (beats - 0.0) / P                                         # A's beats are at k * P from its start
    assert np.abs(grid - np.round(grid)).max() * P < 0.005          # every click on the grid: within 5 ms
    assert eng.deck is db and eng.mix is None


def test_crossfade_keeps_loudness_and_swaps_the_bass():
    a = prepared(0.0, freq=1000, noise=0.2, seed=1)
    b = prepared(0.0, freq=1000, noise=0.2, bass=0.5, seed=2)
    a.pcm[:] = np.random.default_rng(1).normal(0, 0.2, a.pcm.shape)    # plain noise: loudness is easy to read
    eng = Engine()
    da = Deck.load(a, start=0)
    eng.play(da)
    t0 = da.clock_of(round(10 / (4 * P)) * 4 * P)
    db = Deck.load(b, start=t0)
    bars = 8
    eng.schedule(da, db, t0, bars=bars)
    out = render_all(eng, t0 / SR + bars * 4 * P + 2)
    L = bars * 4 * int(round(P * SR))

    def rms(at, w=int(0.25 * SR)):
        seg = out[at:at + w, 0]
        return float(np.sqrt(np.mean(seg ** 2)))

    def bass(at, w=int(0.5 * SR)):                     # 50 Hz content
        seg = out[at:at + w, 0]
        f = np.fft.rfft(seg)
        return float(np.abs(f[int(50 * w / SR)]) / w)

    before = rms(t0 - int(SR))
    assert abs(rms(t0 + L // 4) / before - 1) < 0.2               # no dip or bump in the mix
    assert bass(t0 + int(0.5 * SR)) < 0.03                        # B's bass held back at first
    assert bass(t0 + L - int(SR)) > 0.15                          # and in after the swap


def test_splice_takes_over_on_time_and_refuses_the_past():
    a = prepared(0.0, freq=1000)
    eng = Engine()
    d = Deck.load(a, start=0)
    eng.play(d)
    render_all(eng, 2.0)
    marker = np.full((SR * 5, 2), 0.25, np.float32)
    at = d.clock_of(4 * 4 * P)                                     # bar 4, ~7.5 s
    assert eng.splice(d, at, 4 * 4 * P, 1.0, marker)
    out = render_all(eng, 8.0)
    rendered_from = (int(2.0 * SR) // 1024) * 1024
    j = at - rendered_from
    assert np.allclose(out[j + 2000:j + 4000, 0], 0.25, atol=1e-2)
    assert not eng.splice(d, 10, 0.0, 1.0, marker)                # that moment has passed
