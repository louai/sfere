"""Smaller unit checks: motion events, settings round trip, shader hot reload, ball mapping."""
from __future__ import annotations

import math
import time

import moderngl
import pytest

from sfere import settings
from sfere.ballfilter import BallFilter, BallState
from sfere.scenes import map_ball
from sfere.tracker import Observation


def feed(filt, path, t0=0.0, dt=1 / 30):
    events = []
    t = t0
    for p in path:
        t += dt
        filt.update(None if p is None else Observation(x=p[0], y=p[1], r=0.03, conf=1.0, t=t), t)
        events += filt.state_at(t).events
    return events, t


def test_lost_found_stall_loop():
    f = BallFilter()
    # fast sweep then a sudden stop -> stall
    fast = [(0.1 + 0.03 * i, 0.5) for i in range(20)]
    stop = [(0.1 + 0.03 * 19, 0.5)] * 15
    ev, t = feed(f, fast + stop)
    assert "found" in ev and "stall" in ev
    # disappear for 0.5 s -> lost; come back -> found
    ev, t = feed(f, [None] * 15 + [(0.5, 0.5)] * 5, t)
    assert ev.index("lost") < ev.index("found")
    # two full circles in ~1.2 s -> loop
    circle = [(0.5 + 0.2 * math.cos(a), 0.5 + 0.2 * math.sin(a)) for a in [i * 2 * math.pi / 18 for i in range(40)]]
    ev, t = feed(f, circle, t)
    assert "loop" in ev


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "s.toml")
    s = settings.load()
    s["tracking"]["exclusion"] = [[0.9, 0.0, 1.0, 1.0]]
    s["audio"]["gains"] = {"alsa_output.x.monitor": 0.5, "@DEFAULT_MONITOR@": 1.0}
    s["scene"] = "Museum"
    settings.save(s)
    back = settings.load()
    assert back["tracking"]["exclusion"] == [[0.9, 0.0, 1.0, 1.0]]
    assert back["audio"]["gains"]["@DEFAULT_MONITOR@"] == 1.0
    assert back["scene"] == "Museum"


def test_map_ball_fit_and_fill():
    b = BallState(x=1.0, y=0.0, r=0.1, visible=1)
    # 16:9 camera on a 21:9 screen, fit: the camera rect is narrower than the screen
    sb = map_ball(b, 21 / 9, 16 / 9, "fit")
    assert 0.5 < sb.u < 1.0 and abs(sb.v - 1.0) < 1e-6
    sb = map_ball(b, 21 / 9, 16 / 9, "fill")
    assert abs(sb.u - 1.0) < 1e-6 and sb.v > 1.0  # cropped top/bottom
    sb = map_ball(b, 21 / 9, 16 / 9, "stretch")
    assert (sb.u, sb.v) == (1.0, 1.0)


def test_shader_hot_reload(tmp_path):
    from sfere.fx.shaders import ShadertoyEffect
    from sfere.render.gl import GL
    from sfere.scenes import Scene
    ctx = moderngl.create_standalone_context(require=330, backend="egl")
    gl = GL(ctx)
    p = tmp_path / "x.glsl"
    p.write_text("void mainImage(out vec4 c, in vec2 q) { c = vec4(1.0, 0.0, 0.0, 1.0); }\n")
    idx = {"shadertoy:x": (ShadertoyEffect, p)}
    s = Scene(gl, {"name": "t", "layers": [{"effect": "shadertoy:x", "values": {"gain": 0.5}}]}, (64, 36), idx)
    time.sleep(0.01)
    p.write_text("void mainImage(out vec4 c, in vec2 q) { c = vec4(0.0, 1.0, 0.0, 1.0); }\n")
    msgs = s.reload_changed(idx)
    assert msgs == ["shadertoy:x: reloaded"] and s.layers[0].values["gain"] == 0.5
    p.write_text("void mainImage(out vec4 c, in vec2 q) { c = oops; }\n")
    msgs = s.reload_changed(idx)
    assert "error" in msgs[0]  # broken save: reported, scene keeps running
    s.release()
    ctx.release()


def test_touchscreen_detection_and_backend(tmp_path):
    from sfere.touch import touch_mode, touchscreen_present, wants_xwayland
    (tmp_path / "c13:65").write_text("E:ID_INPUT=1\nE:ID_INPUT_TOUCHPAD=1\n")
    assert not touchscreen_present(tmp_path)
    (tmp_path / "c13:72").write_text("E:ID_INPUT=1\nE:ID_INPUT_TOUCHSCREEN=1\n")
    assert touchscreen_present(tmp_path)
    assert not touchscreen_present(tmp_path / "missing")
    assert touch_mode("auto", True) and not touch_mode("auto", False)
    assert touch_mode("on", False) and not touch_mode("off", True)
    wayland = {"WAYLAND_DISPLAY": "wayland-0"}
    assert wants_xwayland("auto", True, wayland)
    assert not wants_xwayland("off", True, wayland)
    assert not wants_xwayland("on", False, wayland)     # large controls only: nothing to route
    assert not wants_xwayland("auto", True, {})          # already on X11


def test_guide_renders_in_help_tab():
    from sfere.ui import GUIDE, TABS, parse_markdown
    blocks = parse_markdown(GUIDE.read_text())
    headings = [b[2] for b in blocks if b[0] == "h"]
    assert headings[0] == "sfere guide" and "Using a touch screen" in headings
    items = [b for b in blocks if b[0] == "li"]
    assert any(b[1] == 1 for b in items)                              # nested list
    assert any(b[2].startswith("1. ") for b in items)                 # numbered steps keep numbers
    keys = next(b[1] for b in blocks if b[0] == "table" and b[1][0] == ["Key", "Action"])
    assert ["Ctrl+Q", "Quit"] in keys
    text = " ".join(str(b) for b in blocks)
    assert "**" not in text and "](" not in text
    assert all(t in text for t in TABS)


def test_scene_library_file(tmp_path, monkeypatch):
    from sfere import library
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.toml")
    # first start after the upgrade: legacy layer edits from settings.toml are carried over
    legacy = {"scene_layers": {"Rave": [{"effect": "orb"}]}}
    specs = library.load(legacy)
    rave = next(s for s in specs if s["name"] == "Rave")
    assert rave["layers"] == [{"effect": "orb"}] and rave["preset"] == "Rave"
    specs.append(library.blank(library.unique_name("Rave", [s["name"] for s in specs])))
    library.save(specs)
    assert library.load({}) == specs and specs[-1]["name"] == "Rave 2"
    library.path().write_text("{ not json")
    assert [s["name"] for s in library.load({})][0] == "Rave"       # broken file: defaults, file kept aside
    assert (tmp_path / "scenes.json.broken").exists()
