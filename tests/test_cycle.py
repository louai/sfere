"""The scene cycle's timing: interval, beat alignment, order, manual switches."""
from __future__ import annotations

import random

from sfere.cycle import MAX_BEAT_WAIT, SceneCycle

NAMES = ["A", "B", "C", "D"]


def run(cyc, start, until, scene, beats=(), locked=False, dt=0.01):
    """Simulate frames; returns [(time, new scene)] and the scene at the end."""
    out = []
    for k in range(round((until - start) / dt)):
        t = round(start + k * dt, 6)
        i = cyc.tick(t, scene, NAMES, beat=any(abs(t - b) < dt / 2 for b in beats), locked=locked)
        if i is not None:
            scene = NAMES[i]
            out.append((round(t, 2), scene))
    return out, scene


def test_cycles_in_order_every_interval():
    cyc = SceneCycle({"enabled": True, "seconds": 10})
    changes, _ = run(cyc, 0.0, 35.0, "A")
    assert changes == [(10.0, "B"), (20.0, "C"), (30.0, "D")]


def test_off_does_nothing_and_turning_on_starts_a_full_countdown():
    cfg = {"enabled": False, "seconds": 5}
    cyc = SceneCycle(cfg)
    assert run(cyc, 0.0, 20.0, "A")[0] == []
    cfg["enabled"] = True
    (t, scene), = run(cyc, 20.0, 26.0, "A")[0]
    assert scene == "B" and abs(t - 25.0) <= 0.011          # counted from the last frame it was off


def test_waits_for_the_beat_but_not_forever():
    cyc = SceneCycle({"enabled": True, "seconds": 10, "on_beat": True})
    changes, _ = run(cyc, 0.0, 11.0, "A", beats=[9.5, 10.37, 10.8], locked=True)
    assert changes == [(10.37, "B")]
    cyc = SceneCycle({"enabled": True, "seconds": 10, "on_beat": True})
    changes, _ = run(cyc, 0.0, 15.0, "A", beats=[], locked=True)       # tempo locked but no beat comes
    assert changes == [(round(10 + MAX_BEAT_WAIT, 2), "B")]
    cyc = SceneCycle({"enabled": True, "seconds": 10, "on_beat": True})
    assert run(cyc, 0.0, 11.0, "A", beats=[10.4], locked=False)[0] == [(10.0, "B")]   # no tempo: on time


def test_manual_switch_restarts_the_countdown():
    cyc = SceneCycle({"enabled": True, "seconds": 10})
    run(cyc, 0.0, 7.0, "A")
    changes, _ = run(cyc, 7.0, 18.0, "C")          # someone picked C at 7 s
    assert changes == [(17.0, "D")]


def test_shuffle_never_repeats_the_current_scene():
    cyc = SceneCycle({"enabled": True, "seconds": 1, "order": "shuffle"}, rng=random.Random(3))
    changes, _ = run(cyc, 0.0, 60.0, "A")
    seq = ["A"] + [s for _, s in changes]
    assert len(changes) >= 55 and all(a != b for a, b in zip(seq, seq[1:])) and set(seq) == set(NAMES)
