"""Touch screens: detection, and the choice of display backend that makes touch work.

GLFW's Wayland backend ignores touch events (it listens to the pointer and keyboard only),
so on a native Wayland window a finger does nothing. XWayland turns touches into pointer
events for clients that don't ask for touch, so when a touch screen is present sfere runs
through XWayland unless touch mode is turned off.
"""
from __future__ import annotations

import os
from pathlib import Path

UDEV_DATA = Path("/run/udev/data")


def touchscreen_present(udev_data: Path = UDEV_DATA) -> bool:
    """True if udev tagged any input device as a touch screen. Readable without root; the
    device names (e.g. "CSW1322:00 3558:14FD") often don't say "touch"."""
    try:
        files = list(udev_data.glob("c13:*"))
    except OSError:
        return False
    for f in files:
        try:
            if "E:ID_INPUT_TOUCHSCREEN=1" in f.read_text(errors="replace").splitlines():
                return True
        except OSError:
            continue
    return False


def touch_mode(setting: str, present: bool | None = None) -> bool:
    """Larger controls and finger scrolling: "on", "off", or "auto" (on when a touch screen
    is connected)."""
    if setting in ("on", "off"):
        return setting == "on"
    return touchscreen_present() if present is None else present


def wants_xwayland(setting: str, present: bool | None = None, env=None) -> bool:
    """Whether to run through XWayland so the touch screen reaches the control window."""
    env = os.environ if env is None else env
    present = touchscreen_present() if present is None else present
    return bool(env.get("WAYLAND_DISPLAY")) and present and touch_mode(setting, present)
