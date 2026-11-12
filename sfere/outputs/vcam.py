"""The output as a camera other apps can pick ("sfere").

Two ways, picked automatically:
- v4l2loopback: a /dev/video device every app can open (Zoom, Teams, OBS, browsers). Needs
  the kernel module, which sfere can't load itself; see `setup_hint()`.
- PipeWire: a camera node through GStreamer's pipewiresink. No setup, but only apps that
  take PipeWire cameras see it (OBS 30+ "Video Capture Device (PipeWire)", Firefox, Chrome
  with PipeWire camera support).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .pipe import FramePipe

NAME = "sfere"


def loopback_devices() -> list[str]:
    """/dev/video* devices that are v4l2loopback outputs."""
    out = []
    for d in sorted(Path("/sys/devices/virtual/video4linux").glob("video*")):
        dev = Path("/dev") / d.name
        if dev.exists():
            out.append(str(dev))
    return out


def pipewire_available() -> bool:
    if not shutil.which("gst-launch-1.0") or not shutil.which("gst-inspect-1.0"):
        return False
    import subprocess
    try:
        return subprocess.run(["gst-inspect-1.0", "pipewiresink"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def setup_hint() -> str:
    return ("For a camera every app can use, install v4l2loopback once:\n"
            "  sudo apt install v4l2loopback-dkms\n"
            f"  sudo modprobe v4l2loopback video_nr=10 card_label={NAME} exclusive_caps=1\n"
            "With Secure Boot on, the install asks for a password to enroll at the next reboot.")


class VirtualCamera:
    """Takes RGB frames (top row first) of a fixed size and publishes them."""

    def __init__(self, size: tuple[int, int], fps: int = 30, backend: str = "auto"):
        self.size, self.fps = size, fps
        w, h = size
        devices = loopback_devices() if backend in ("auto", "v4l2loopback") else []
        if devices:
            self.backend, self.where = "v4l2loopback", devices[0]
            cmd = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                   "-r", str(fps), "-i", "-", "-pix_fmt", "yuyv422", "-f", "v4l2", self.where]
        elif backend in ("auto", "pipewire") and pipewire_available():
            self.backend, self.where = "pipewire", f"PipeWire camera “{NAME}”"
            props = (f"props,media.class=Video/Source,media.role=Camera,node.name={NAME},"
                     f"node.description={NAME}")
            cmd = ["gst-launch-1.0", "-q", "fdsrc", "fd=0", "do-timestamp=true", "!",
                   "rawvideoparse", "format=rgb", f"width={w}", f"height={h}", f"framerate={fps}/1", "!",
                   "videoconvert", "!", "video/x-raw,format=YUY2", "!",
                   "pipewiresink", "mode=provide", f"client-name={NAME}", f"stream-properties={props}"]
        else:
            raise RuntimeError("no virtual camera: neither v4l2loopback nor GStreamer's PipeWire sink is available")
        self.pipe = FramePipe(cmd, f"vcam:{self.backend}", queue=2)
        self._next = 0.0

    @property
    def alive(self) -> bool:
        return self.pipe.alive

    def wants_frame(self, now: float) -> bool:
        if now < self._next:
            return False
        self._next = max(self._next + 1.0 / self.fps, now - 0.5 / self.fps)
        return True

    def push(self, rgb: bytes) -> None:
        self.pipe.push(rgb)

    def close(self) -> None:
        self.pipe.close(timeout=3.0)
