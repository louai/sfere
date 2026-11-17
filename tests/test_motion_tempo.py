"""Movement tempo: cycles of the ball's path become a BPM in the genre's range."""
from __future__ import annotations

import math

import numpy as np

from sfere.motion_tempo import MotionTempo, cycle_period, fold


def circles(period, seconds=8.0, fps=30.0, noise=0.0, drop=0.0, t0=0.0, seed=1):
    rng = np.random.default_rng(seed)
    for k in range(int(seconds * fps)):
        t = t0 + k / fps
        w = 2 * math.pi / period
        vx, vy = -math.sin(w * t) * w * 0.2, math.cos(w * t) * w * 0.2
        vx += rng.normal(0, noise)
        vy += rng.normal(0, noise)
        yield t, vx, vy, 0.0 if rng.random() < drop else 1.0


def test_fold_into_genre_ranges():
    assert fold(60, 118, 128) == 120
    assert fold(250, 125, 140) == 125      # 125 is 250 halved
    assert fold(86, 165, 178) == 172
    assert 118 <= fold(100, 118, 128) <= 128   # no octave lands inside: the nearest edge


def test_cycle_period_of_loops_and_arcs():
    for period in (0.5, 0.36, 1.2):
        t, vx, vy, _ = np.array(list(circles(period, noise=0.05))).T
        p, conf = cycle_period(t, vx, vy)
        assert abs(p - period) < 0.01 * period + 0.004 and conf > 0.7, (period, p, conf)
    # a pendulum swing (back and forth along x): one full swing is the cycle
    t = np.arange(0, 8, 1 / 30)
    p, conf = cycle_period(t, np.cos(2 * math.pi * t / 0.96), np.zeros_like(t))
    assert abs(p - 0.96) < 0.01 and conf > 0.8
    # random wandering has no strong cycle
    rng = np.random.default_rng(0)
    _, conf = cycle_period(t, rng.normal(0, 1, len(t)), rng.normal(0, 1, len(t)))
    assert conf < 0.4


def run(mt, gen, until_t):
    for t, vx, vy, vis in gen:
        mt.feed(t, vx, vy, vis)
        mt.update(t)
        if t >= until_t:
            break
    return mt


def test_target_moves_only_after_the_new_tempo_holds():
    mt = MotionTempo("house")
    assert mt.target_bpm == 123                       # the genre's middle until it hears the ball
    run(mt, circles(0.5, seconds=20, drop=0.05), 20)  # 120 per minute
    assert abs(mt.target_bpm - 120) < 1.5 and mt.confidence > 0.6
    # speeds up to 0.48 s cycles (125): nothing happens for the first seconds of the change
    mt2 = run(mt, circles(0.48, seconds=30, t0=20.0), 24)
    assert abs(mt2.target_bpm - 120) < 1.5
    run(mt, circles(0.48, seconds=30, t0=24.0), 40)
    assert abs(mt.target_bpm - 125) < 1.5


def test_lock_manual_and_lost_ball_hold_the_target():
    mt = MotionTempo("techno")
    run(mt, circles(0.46, seconds=16), 16)              # 130.4
    held = mt.target_bpm
    mt.locked = True
    run(mt, circles(0.42, seconds=30, t0=16.0), 40)
    assert mt.target_bpm == held
    mt.locked = False
    mt.manual_bpm = 133
    assert mt.update(41) == 133
    mt.manual_bpm = None
    run(mt, circles(0.46, seconds=12, t0=42.0, drop=1.0), 53)   # ball lost the whole time
    assert mt.confidence == 0.0
