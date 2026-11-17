"""The Camera tab's video list: recordings paired with their music, clips, stacked ones flagged."""
from __future__ import annotations

import os

import cv2
import numpy as np

from sfere.recordings import list_videos


def _video(path, frames=30, fps=30):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 36))
    for _ in range(frames):
        w.write(np.zeros((36, 64, 3), np.uint8))
    w.release()


def test_pairs_recordings_with_their_music(tmp_path):
    rec, clips = tmp_path / "sfere", tmp_path / "Camera"
    rec.mkdir(), clips.mkdir()
    _video(rec / "sfere-20261101-100000-camera.mp4")
    (rec / "sfere-20261101-100000-audio.wav").write_bytes(b"RIFF")
    _video(rec / "sfere-20261102-100000-camera.mp4", frames=60)          # newer, no music kept
    _video(rec / "sfere-20261103-100000-stacked.mp4")
    _video(rec / "sfere-20261103-100000-output.mp4")                     # the visuals: not an input
    _video(clips / "Recording from today.mp4")
    (clips / "notes.txt").write_text("x")
    for i, p in enumerate(sorted(rec.iterdir())):
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    vids = list_videos(rec, clips)
    names = [v.path.name for v in vids]
    assert names == ["sfere-20261103-100000-stacked.mp4", "sfere-20261102-100000-camera.mp4",
                     "sfere-20261101-100000-camera.mp4", "Recording from today.mp4"]
    assert vids[0].note and vids[1].audio is None and vids[2].audio.name == "sfere-20261101-100000-audio.wav"
    assert abs(vids[1].seconds - 2.0) < 0.1 and "+ its music" in vids[2].label
    assert list_videos(tmp_path / "none", tmp_path / "none") == []
