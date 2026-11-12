"""M1 acceptance: tracker + filter on the two sample videos (PLAN.md §7)."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from sfere.ballfilter import BallFilter
from sfere.tracker import Tracker, TrackerConfig

VIDEOS = Path.home() / "Videos" / "Camera"
SAMPLE_A = VIDEOS / "Recording from 2026-11-11 21-38-10.703597.mp4"
SAMPLE_B = VIDEOS / "Recording from 2026-11-11 22-08-06.462397.mp4"


def ball_pixels(frame) -> int:
    """Ground truth for the dark stage: the research probe's criterion (red > 60, > 2x green, > 1.5x blue)."""
    s = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA).astype(int)
    b, g, r = s[..., 0], s[..., 1], s[..., 2]
    return int(np.count_nonzero((r > 60) & (r > 2 * g) & (r > 1.5 * b)))


def run(path: Path, config: TrackerConfig):
    cap = cv2.VideoCapture(str(path))
    tracker, filt = Tracker(config), BallFilter()
    rows, events, ms, truth = [], [], [], []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        obs = tracker.process(frame, t, filt.prediction(), filt.gate())
        filt.update(obs, t)
        st = filt.state_at(t)
        events += [(i, e) for e in st.events]
        rows.append((i, obs, st.state, st.x, st.y))
        ms.append(tracker.last_ms)
        truth.append(ball_pixels(frame))
        i += 1
    return rows, events, ms, truth


def jitter_px(rows):
    """Median deviation of the filtered path from the midpoint of its neighbours, in px at 320 wide."""
    pts = [(x, y) for _, _, s, x, y in rows if s == "tracking"]
    d = [math.hypot(p[0] - (a[0] + b[0]) / 2, (p[1] - (a[1] + b[1]) / 2) * 180 / 320) * 320
         for a, p, b in zip(pts, pts[1:], pts[2:])]
    return float(np.median(d))


@pytest.mark.skipif(not SAMPLE_B.exists(), reason="sample B not present")
def test_sample_b_dark_stage():
    rows, events, ms, truth = run(SAMPLE_B, TrackerConfig(profile="auto"))
    hidden = {i for i, n in enumerate(truth) if n < 3}  # completely black frames (research §1.1)
    assert 60 <= len(hidden) <= 75, len(hidden)
    visible = [r for r in rows if r[0] not in hidden]
    detected = sum(1 for r in visible if r[1] is not None)
    false_pos = sum(1 for r in rows if r[0] in hidden and r[1] is not None)
    lost = [i for i, e in events if e == "lost"]
    found = [i for i, e in events if e == "found"]
    print(f"\nB: tracked {detected}/{len(visible)} visible, false {false_pos}, lost at {lost}, found at {found}, "
          f"jitter {jitter_px(rows):.2f}px, tracker {np.median(ms):.2f} ms median / {np.percentile(ms, 99):.2f} p99")
    assert detected == len(visible)
    assert false_pos == 0
    assert any(146 <= i <= 160 for i in lost) and any(413 <= i <= 425 for i in lost)
    assert any(190 <= i <= 196 for i in found)
    assert jitter_px(rows) < 2.0
    assert np.median(ms) < 3.0


@pytest.mark.skipif(not SAMPLE_A.exists(), reason="sample A not present")
def test_sample_a_lit_room():
    # The window on the right reflects the ball: excluded the way calibration would.
    cfg = TrackerConfig(profile="auto", exclusion=[[0.905, 0.0, 1.0, 1.0]])
    rows, events, ms, _ = run(SAMPLE_A, cfg)
    absent = set(range(422, 471))  # performer walks to the camera, ball not visible
    visible = [r for r in rows if r[0] not in absent]
    detected = sum(1 for r in visible if r[1] is not None)
    reflection = sum(1 for r in rows if r[1] is not None and r[1].x > 0.9)
    print(f"\nA: tracked {detected}/{len(visible)} = {detected / len(visible):.1%}, reflection lock-ons {reflection}, "
          f"jitter {jitter_px(rows):.2f}px, tracker {np.median(ms):.2f} ms")
    assert reflection == 0
    assert detected / len(visible) >= 0.97
    assert jitter_px(rows) < 2.0
    assert np.median(ms) < 3.0
