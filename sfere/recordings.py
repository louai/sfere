"""Videos to play as sfere's input instead of the webcam (Camera tab, MCP `play_video`).

sfere's own recordings come first: a `…-camera.mp4` is paired with the `…-audio.wav` of the
same recording, so playing it replays the whole session. Stacked recordings are listed but
flagged, since their top half is the visuals rather than the camera's view.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

RECORDINGS = Path.home() / "Videos" / "sfere"
CAMERA_CLIPS = Path.home() / "Videos" / "Camera"
VIDEO_TYPES = (".mp4", ".webm", ".mkv", ".mov", ".avi")


@dataclass
class Video:
    path: Path
    audio: Path | None = None     # the music recorded with it, if any
    seconds: float = 0.0
    note: str = ""                # e.g. why it won't track well

    @property
    def label(self) -> str:
        m, s = divmod(int(self.seconds), 60)
        extra = "  + its music" if self.audio else ""
        note = f"  ({self.note})" if self.note else ""
        return f"{self.path.name}   {m}:{s:02d}{extra}{note}"


def _duration(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    try:
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS)
        return float(n / fps) if fps > 0 and n > 0 else 0.0
    finally:
        cap.release()


def list_videos(recordings: Path = RECORDINGS, clips: Path = CAMERA_CLIPS) -> list[Video]:
    """sfere recordings (newest first), then other clips (newest first)."""
    out = []
    if recordings.is_dir():
        for p in sorted(recordings.glob("sfere-*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if p.suffix.lower() not in VIDEO_TYPES:
                continue
            stem = p.stem
            if stem.endswith("-camera"):
                wav = p.with_name(stem[: -len("-camera")] + "-audio.wav")
                out.append(Video(p, wav if wav.exists() else None))
            elif stem.endswith("-stacked"):
                out.append(Video(p, note="visuals on top: not a camera view"))
    if clips.is_dir():
        files = [p for p in clips.iterdir() if p.suffix.lower() in VIDEO_TYPES]
        out += [Video(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]
    for v in out:
        v.seconds = _duration(v.path)
    return out
