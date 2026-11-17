"""Track preparation: the beat grid of a synthetic dance track, mix points, pitch-preserving stretch."""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from sfere.music.prepare import SR, beat_grid, mix_points, stretch


def dance_track(bpm=126.0, seconds=150.0, offset=0.37, outro_bars=16, seed=0):
    """Kick on every beat (louder on the bar's first beat), hats on the off-beats, a bass
    tone, an outro where the kick stops. The bar starts at beat 2 so the downbeat isn't 0."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = 0.05 * np.sin(2 * np.pi * 55 * t)
    period = 60.0 / bpm
    beats = np.arange(offset, seconds - 0.2, period)
    kick = np.sin(2 * np.pi * 60 * np.arange(int(0.12 * SR)) / SR) * np.exp(-np.arange(int(0.12 * SR)) / (0.03 * SR))
    hat = rng.normal(0, 1, int(0.03 * SR)) * np.exp(-np.arange(int(0.03 * SR)) / (0.005 * SR)) * 0.15
    outro = seconds - outro_bars * 4 * period
    for i, b in enumerate(beats):
        if b > outro:
            break
        s = int(b * SR)
        gain = 1.0 if (i - 2) % 4 == 0 else 0.6
        x[s:s + len(kick)] += gain * kick[: n - s]
        h = int((b + period / 2) * SR)
        x[h:h + len(hat)] += hat[: max(0, n - h)]
    return np.stack([x, x], 1).astype(np.float32), beats


def test_grid_finds_tempo_phase_and_downbeat():
    pcm, beats = dance_track(126.0)
    assert abs(beat_grid(pcm, 63.0).bpm - 63.0) < 0.05     # the tag's octave is kept (the set counts in it)
    for hint in (126.0, 125.0, 127.3):                     # Audius's tag may be off by a little
        g = beat_grid(pcm, hint)
        assert abs(g.bpm - 126.0) < 0.05, (hint, g.bpm)
        k = np.round((beats - g.first) / g.period)
        err = beats - (g.first + k * g.period)
        assert np.abs(err[:200]).max() < 0.010            # within 10 ms over the whole track
        assert (int(round((beats[2] - g.first) / g.period)) - g.downbeat) % 4 == 0


def test_mix_points_land_on_bars_before_the_outro():
    pcm, beats = dance_track(126.0, seconds=150.0, outro_bars=16)
    g = beat_grid(pcm, 126.0)
    mix_in, mix_out = mix_points(pcm, g, mix_bars=16)
    bar = 4 * g.period
    assert abs(((mix_in - beats[2]) / bar) - round((mix_in - beats[2]) / bar)) < 0.01
    outro = 150.0 - 16 * bar
    assert mix_out < outro and mix_out + 16 * bar > outro - 8 * bar


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stretch_changes_length_not_pitch():
    t = np.arange(int(4 * SR)) / SR
    tone = np.stack([np.sin(2 * np.pi * 440 * t)] * 2, 1).astype(np.float32)
    out = stretch(tone, 1.08)            # changing pitch with it would give 475 Hz
    assert abs(len(out) / len(tone) - 1 / 1.08) < 0.01
    mid = out[len(out) // 4: 3 * len(out) // 4, 0]
    up = np.flatnonzero((mid[:-1] < 0) & (mid[1:] >= 0))    # zero crossings: robust to sidebands
    freq = (len(up) - 1) / ((up[-1] - up[0]) / SR)
    assert abs(freq - 440) < 0.015 * 440
