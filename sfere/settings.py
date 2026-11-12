"""Persistent settings in ~/.config/sfere/settings.toml (read with tomllib, written by hand)."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

PATH = Path(os.environ.get("SFERE_CONFIG", Path.home() / ".config" / "sfere" / "settings.toml"))

DEFAULTS: dict = {
    "camera": {
        "device": "/dev/video0",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "mirror": True,
        # Applied through V4L2 at start, restored on exit. None = leave as found.
        "exposure": 38,
        "gain": None,
        "lock_white_balance": True,
        "lock_focus": True,
        "steady_framerate": True,
    },
    "tracking": {
        "profile": "auto",  # auto | dark | lit
        "hue": 0.0,          # ball hue in degrees (red)
        "hue_tolerance": 15.0,
        "min_saturation": 0.55,
        "min_value": 0.24,    # dark stage: the sample ball is ~150/255 red
        "exclusion": [],      # list of [x0, y0, x1, y1] in normalized camera coords
    },
    "audio": {
        "sources": ["@DEFAULT_MONITOR@"],
        "gains": {},
        "latency_ms": 0.0,
    },
    "output": {
        "mode": "window",     # window | fullscreen | borderless
        "monitor": "",
        "start_on_external": True,   # fullscreen on an external display at start and on hot-plug
        "render_width": 1920,
        "render_height": 1080,
        "render_scale": 1.0,
        "fps_limit": 0,       # 0 = the display's refresh rate
        "aspect": "fit",      # fit | fill | stretch
        "flip_x": False,
        "flip_y": False,
    },
    "ui": {
        # auto = on when a touch screen is connected: larger controls, finger scrolling, and
        # XWayland so touches reach the window (sfere/touch.py)
        "touch": "auto",      # auto | on | off
    },
    "scene": "Rave",
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        with open(PATH, "rb") as f:
            return _merge(DEFAULTS, tomllib.load(f))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except tomllib.TOMLDecodeError:
        PATH.rename(PATH.with_suffix(".toml.broken"))
        return copy.deepcopy(DEFAULTS)


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(type(v))


def _key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else _value(k)


def dumps(data: dict) -> str:
    lines, tables = [], []
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{_key(k)} = {_value(v)}")
    for name, table in tables:
        lines.append(f"\n[{_key(name)}]")
        for k, v in table.items():
            if v is not None:
                lines.append(f"{_key(k)} = {_value(v)}")
    return "\n".join(lines) + "\n"


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(dumps(data))
    tmp.replace(PATH)
