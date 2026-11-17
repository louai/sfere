"""Wires capture -> tracker -> filter, and audio, into one object the renderer reads."""
from __future__ import annotations

import threading
import time

import numpy as np

from .audio.sources import AudioEngine, DEFAULT_MONITOR
from .ballfilter import BallFilter, BallState
from .camera import CaptureThread, V4L2Controls, camera_controls_from_settings
from .tracker import Tracker, TrackerConfig


class Engine:
    def __init__(self, settings: dict, video: str | None = None, audio_file: str | None = None,
                 audio_sources: list[str] | None = None, play_audio: bool = True):
        self.settings = settings
        cam = settings["camera"]
        self.tracker = Tracker(TrackerConfig.from_settings(settings["tracking"]))
        self.filter = BallFilter()
        self.mirror = bool(cam.get("mirror", True)) and video is None
        self.preview = None            # latest downscaled frame (BGR, 320x180)
        self.preview_mask = None
        self.last_full_frame = None
        self.recording = None          # a Recording that wants camera frames, or None
        self._lock = threading.Lock()
        self.controls = None
        self.video = video                 # the file playing instead of the webcam, or None
        self.audio_file = audio_file
        self.play_audio = play_audio
        self._live_sources = audio_sources  # live audio as started (None: the saved sources)

        self.audio = AudioEngine(settings["audio"].get("latency_ms", 0.0))
        self.capture = None
        self._start_capture(video)
        if audio_file:
            self.audio.play_file(audio_file, play_audio=play_audio)
        else:
            self._enable_live_audio()

    def _start_capture(self, video: str | None):
        cam = self.settings["camera"]
        if video is None and self.controls is None:
            self.controls = V4L2Controls(cam["device"])
            self.controls.set(camera_controls_from_settings(cam))
        self.capture = CaptureThread(video or cam["device"], self._on_frame, cam["width"], cam["height"], cam["fps"])
        self.capture.on_loop = self._on_loop
        self.capture.start()

    def _enable_live_audio(self):
        gains = self.settings["audio"].get("gains", {})
        ids = self._live_sources if self._live_sources is not None \
            else self.settings["audio"].get("sources", [DEFAULT_MONITOR])
        for sid in ids:
            self.audio.enable(sid, gains.get(sid, 1.0), kind="system" if "monitor" in sid.lower() else "input")

    def set_source(self, video: str | None, audio_file: str | None = None) -> None:
        """Switch the input while running: a video file (with its music, if given) or, with
        video=None, the webcam and the live audio sources."""
        self.capture.stop()
        if self.video is None and video is not None and self.controls:
            self.controls.restore()           # the webcam goes back to how it was
            self.controls = None
        # a recording's music replaces the live audio; otherwise the live audio plays
        for sid, src in list(self.audio.sources.items()):
            if src.kind == "file" or audio_file:
                self.audio.disable(sid)
        if audio_file:
            self.audio.play_file(audio_file, play_audio=self.play_audio)
        elif not self.audio.sources:
            self._enable_live_audio()
        self.video, self.audio_file = video, audio_file
        self.mirror = bool(self.settings["camera"].get("mirror", True)) and video is None
        with self._lock:
            self.filter = BallFilter()
        self.preview = self.preview_mask = self.last_full_frame = None
        self._start_capture(video)

    def _on_loop(self):
        with self._lock:
            self.filter = BallFilter()

    def _on_frame(self, frame: np.ndarray, t: float):
        with self._lock:
            filt = self.filter
        obs = self.tracker.process(frame, t, filt.prediction(), filt.gate())
        filt.update(obs, t)
        self.preview = self.tracker.small
        self.preview_mask = self.tracker.mask
        self.last_full_frame = frame
        rec = self.recording
        if rec is not None:            # as the Camera tab shows it, so a replay looks the same
            rec.push_camera(frame[:, ::-1] if self.mirror else frame)

    def ball(self, t: float | None = None) -> BallState:
        with self._lock:
            filt = self.filter
        st = filt.state_at(time.monotonic() if t is None else t)
        if self.mirror:
            st.x = 1.0 - st.x
            st.vx = -st.vx
        return st

    def calibrate_at(self, x: float, y: float) -> dict:
        """Click-to-calibrate in preview coordinates (as displayed, i.e. mirrored if mirroring)."""
        if self.last_full_frame is None:
            return {}
        if self.mirror:
            x = 1.0 - x
        return self.tracker.sample_color(self.last_full_frame, x, y)

    def close(self):
        self.capture.stop()
        self.audio.close()
        if self.controls:
            self.controls.restore()
