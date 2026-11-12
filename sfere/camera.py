"""Camera / video-file capture thread with V4L2 controls (PLAN.md §1.4, §3).

V4L2 controls are applied at start and the previous values restored on stop, so
sfere leaves the webcam as it found it.
"""
from __future__ import annotations

import atexit
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

# Order matters: an automatic mode must be off before its manual value can be set.
CONTROL_ORDER = [
    "auto_exposure", "exposure_dynamic_framerate", "exposure_time_absolute", "gain",
    "white_balance_automatic", "white_balance_temperature",
    "focus_automatic_continuous", "focus_absolute", "power_line_frequency",
]


class V4L2Controls:
    def __init__(self, device: str):
        self.device = device
        self.available = bool(shutil.which("v4l2-ctl")) and Path(device).exists()
        self._saved: dict[str, int] = {}

    def _run(self, *args: str) -> str:
        r = subprocess.run(["v4l2-ctl", "-d", self.device, *args], capture_output=True, text=True, timeout=5)
        return r.stdout

    def get(self, names: list[str]) -> dict[str, int]:
        if not self.available:
            return {}
        out = {}
        for line in self._run(f"--get-ctrl={','.join(names)}").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                try:
                    out[k.strip()] = int(v.strip())
                except ValueError:
                    pass
        return out

    def ranges(self) -> dict[str, tuple[int, int]]:
        out = {}
        if not self.available:
            return out
        for line in self._run("--list-ctrls").splitlines():
            parts = line.split()
            if len(parts) > 1 and "min=" in line:
                kv = dict(p.split("=", 1) for p in parts if "=" in p)
                try:
                    out[parts[0]] = (int(kv["min"]), int(kv["max"]))
                except (KeyError, ValueError):
                    pass
        return out

    def set(self, values: dict[str, int]) -> None:
        if not self.available or not values:
            return
        if not self._saved:
            self._saved = self.get(CONTROL_ORDER)
            atexit.register(self.restore)
        for name in CONTROL_ORDER:
            if name in values and values[name] is not None:
                self._run(f"--set-ctrl={name}={int(values[name])}")

    def restore(self) -> None:
        if self._saved:
            saved, self._saved = self._saved, {}
            for name in CONTROL_ORDER:
                if name in saved:
                    self._run(f"--set-ctrl={name}={saved[name]}")


def camera_controls_from_settings(cam: dict) -> dict[str, int]:
    v = {"auto_exposure": 1}  # manual exposure
    if cam.get("exposure") is not None:
        v["exposure_time_absolute"] = cam["exposure"]
    if cam.get("gain") is not None:
        v["gain"] = cam["gain"]
    if cam.get("steady_framerate", True):
        v["exposure_dynamic_framerate"] = 0
    if cam.get("lock_white_balance", True):
        v["white_balance_automatic"] = 0
    if cam.get("lock_focus", True):
        v["focus_automatic_continuous"] = 0
    return v


class CaptureThread:
    """Reads frames from a webcam or a video file and hands each to `on_frame(frame, t)`.

    For a file, frames are paced to real time using their timestamps and the file loops.
    `t` is time.monotonic() at capture, the same clock the renderer uses.
    """

    def __init__(self, source: str, on_frame: Callable[[np.ndarray, float], None],
                 width: int = 1280, height: int = 720, fps: int = 30, loop: bool = True):
        self.source = source
        self.on_frame = on_frame
        self.size = (width, height)
        self.fps_req = fps
        self.loop = loop
        self.is_file = not source.startswith("/dev/video") and not source.isdigit()
        self.fps = 0.0
        self.status = "starting"
        self.frame_count = 0
        self.on_loop: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _open(self):
        if self.is_file:
            return cv2.VideoCapture(self.source)
        cap = cv2.VideoCapture(int(self.source) if self.source.isdigit() else self.source, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        cap.set(cv2.CAP_PROP_FPS, self.fps_req)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        times: list[float] = []
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.status = f"cannot open {self.source}"
                self._stop.wait(2.0)
                continue
            self.status = "running"
            start_wall, start_pos = time.monotonic(), None
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    if self.is_file and self.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        start_wall, start_pos = time.monotonic(), None
                        if self.on_loop:
                            self.on_loop()
                        continue
                    self.status = "ended" if self.is_file else "lost camera"
                    break
                if self.is_file:  # pace to the file's own timestamps
                    pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    if start_pos is None:
                        start_pos = pos
                    due = start_wall + (pos - start_pos)
                    wait = due - time.monotonic()
                    if wait > 0:
                        self._stop.wait(wait)
                t = time.monotonic()
                times.append(t)
                times = times[-30:]
                if len(times) > 1:
                    self.fps = (len(times) - 1) / (times[-1] - times[0])
                self.frame_count += 1
                try:
                    self.on_frame(frame, t)
                except Exception as e:  # never let a tracking bug kill capture
                    self.status = f"frame error: {e}"
            cap.release()
            if self.is_file and not self.loop:
                return
            self._stop.wait(1.0)
