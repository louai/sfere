"""Rendering, effects and shader loaders, headless (EGL) — M2/M4 checks (PLAN.md §7).

Snapshots are written to out/snapshots/ for inspection.
"""
from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

import cv2
import moderngl
import numpy as np
import pytest

from sfere.audio.analyser import AudioState
from sfere.ballfilter import BallState
from sfere.fx.base import Frame, ScreenBall
from sfere.fx.shaders import ISFEffect, ShadertoyEffect, discover
from sfere.render.gl import GL
from sfere.render.post import Pipeline
from sfere.scenes import PRESETS, SceneManager

OUT = Path(__file__).resolve().parents[1] / "out" / "snapshots"
SIZE = (960, 540)


@pytest.fixture(scope="module")
def gl():
    ctx = moderngl.create_standalone_context(require=330, backend="egl")
    yield GL(ctx)
    ctx.release()


def ball_at(t: float) -> BallState:
    """A ball circling the frame, with a pause (stall) every 3 s and a short disappearance."""
    x = 0.5 + 0.28 * math.cos(t * 2.2)
    y = 0.5 + 0.3 * math.sin(t * 2.9)
    events = []
    if int(t * 60) % 180 == 0 and t > 0:
        events.append("stall")
    vis = 0.0 if 4.0 < (t % 6.0) < 4.25 else 1.0
    return BallState(x=x, y=y, vx=-0.6 * math.sin(t * 2.2), vy=0.87 * math.cos(t * 2.9), r=0.03, visible=vis,
                     state="tracking" if vis else "lost", speed=0.4, accel=0.2, t=t, events=events)


def audio_at(t: float, beat_every: float = 60 / 123) -> AudioState:
    ph = (t / beat_every) % 1.0
    events = ["beat"] if ph < 1 / 60 / beat_every else []
    if ((t / beat_every) + 0.5) % 1.0 < 1 / 60 / beat_every:
        events.append("onset")
    spec = (np.exp(-np.linspace(0, 4, 64)) * (0.6 + 0.4 * math.exp(-4 * ph))).astype("f4")
    return AudioState(sub=0.5 + 0.5 * math.exp(-4 * ph), bass=0.6, mid=0.3, high=0.2, level=0.6,
                      beat=math.exp(-4 * ph), beat_phase=ph, bpm=123.0, locked=True, spectrum=spec, events=events)


def read(tex: moderngl.Texture) -> np.ndarray:
    a = np.frombuffer(tex.read(), np.uint8 if tex.dtype == "f1" else np.float16).reshape(tex.height, tex.width, -1)
    return a[::-1, :, :3].astype(np.float32) / (255.0 if tex.dtype == "f1" else 1.0)


def save(name: str, img: np.ndarray):
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), (np.clip(img, 0, 1)[:, :, ::-1] * 255).astype(np.uint8))


def test_every_scene_renders(gl):
    if OUT.exists():
        shutil.rmtree(OUT)
    mgr = SceneManager(gl, SIZE)
    assert len(mgr.names) >= 5
    timings = {}
    for i, name in enumerate(mgr.names):
        mgr.select(i, immediate=True)
        assert not mgr.current.errors, mgr.current.errors
        t, ms = 0.0, []
        for k in range(150):  # 2.5 s at 60 fps
            t += 1 / 60
            t0 = time.perf_counter()
            out = mgr.render(t, 1 / 60, ball_at(t), audio_at(t))
            gl.ctx.finish()
            ms.append((time.perf_counter() - t0) * 1000)
        img = read(out)
        save(f"{i}_{name.replace(' ', '_')}", img)
        timings[name] = float(np.median(ms[20:]))
        assert img.mean() > 0.005, f"{name} rendered black"
        # brightest region should be near the ball (scenes are about the ball)
        b = ball_at(t)
        bx, by = int(b.x * SIZE[0]), int(b.y * SIZE[1])
        assert img[max(0, by - 40):by + 40, max(0, bx - 40):bx + 40].mean() > img.mean(), name
    print("\nmedian ms per frame at %dx%d:" % SIZE, {k: round(v, 2) for k, v in timings.items()})


def test_crossfade(gl):
    mgr = SceneManager(gl, SIZE)
    mgr.select(1)
    assert mgr.previous is not None and mgr.fade == 0.0
    t = 0.0
    for _ in range(int(2.2 * 60)):
        t += 1 / 60
        mgr.render(t, 1 / 60, ball_at(t), audio_at(t))
    assert mgr.previous is None and mgr.fade == 1.0 and mgr.current.name == mgr.names[1]


def _frame(gl, pl, ball_uv, beat):
    a = audio_at(0.0)
    a.beat = beat
    sb = ScreenBall(x=ball_uv[0] * pl.aspect, y=ball_uv[1], r=0.03, visible=1.0, u=ball_uv[0], v=ball_uv[1])
    return Frame(t=1.0, dt=1 / 60, aspect=pl.aspect, size=pl.size, ball=sb, audio=a, events=set(),
                 palette=[(1, 0.2, 0.8), (0.2, 0.8, 1)], pipeline=pl, gl=gl)


def _render_shader(gl, make, ball_uv, beat, frames=30):
    eff = make()  # fresh state per run: multipass shaders accumulate
    pl = Pipeline(gl, SIZE)
    for k, p in eff.params.items():
        if p.kind == "point":
            eff.values[k] = ball_uv
    for _ in range(frames):
        f = _frame(gl, pl, ball_uv, beat)
        pl.begin_layer()
        eff.update(f)
        eff.draw(f)
    img = read(pl.direct.tex)
    pl.release()
    return img


def _seed_prev(gl, pl):
    """Filters need something to filter: put a bright pattern in the previous frame."""
    w, h = pl.size
    x = np.linspace(0, 40, w)
    stripes = (0.5 + 0.5 * np.sin(x))[None, :, None] * np.ones((h, 1, 4))
    stripes[..., 3] = 1
    for t in pl.scene:
        t.tex.write(stripes.astype(np.float16).tobytes())


OWN = sorted(k for k in discover() if "/" not in k)            # written for sfere
PACKS = sorted(k for k in discover() if "/" in k)              # downloaded collections


@pytest.mark.parametrize("key", OWN)
def test_shader_follows_ball_and_beat(gl, key):
    cls, path = discover()[key]
    eff = cls(gl, path)
    assert eff.error is None, eff.error
    assert "MIT" in eff.license_line()  # every bundled shader carries its license
    is_filter = isinstance(eff, ISFEffect) and eff.images
    if is_filter:
        # filters: output depends on the previous frame; check it isn't empty and reacts to the beat
        pl = Pipeline(gl, SIZE)
        _seed_prev(gl, pl)
        f0 = _frame(gl, pl, (0.3, 0.6), 0.0)
        pl.begin_layer(); eff.draw(f0); quiet = read(pl.direct.tex)
        f1 = _frame(gl, pl, (0.3, 0.6), 1.0)
        pl.begin_layer(); eff.draw(f1); loud = read(pl.direct.tex)
        pl.release()
        assert quiet.mean() > 0.01
        assert np.abs(loud - quiet).mean() > 1e-4  # split amount follows uBeat
        save(f"shader_{key.replace(':', '_')}", loud)
        return
    make = lambda: cls(gl, path)
    left = _render_shader(gl, make, (0.25, 0.5), 0.0)
    right = _render_shader(gl, make, (0.75, 0.5), 0.0)
    loud = _render_shader(gl, make, (0.25, 0.5), 1.0)
    save(f"shader_{key.replace(':', '_')}", loud)
    h, w, _ = left.shape
    lhalf, rhalf = (lambda im: im[:, : w // 2].mean()), (lambda im: im[:, w // 2:].mean())
    assert left.mean() > 0.001, "draws nothing"
    assert lhalf(left) > rhalf(left) and rhalf(right) > lhalf(right), "does not follow the ball"
    assert loud.mean() > left.mean() * 1.05, "does not react to uBeat"


def test_downloaded_shaders_load_and_draw(gl):
    """Every shader in a downloaded pack compiles, carries a license and draws something."""
    assert len(PACKS) >= 190
    bad = []
    for key in PACKS:
        cls, path = discover()[key]
        eff = cls(gl, path)
        if eff.error or eff.unsupported or "no license info" in eff.license_line():
            bad.append(f"{key}: {eff.error or eff.unsupported or 'no license'}")
            continue
        pl = Pipeline(gl, (320, 180))
        _seed_prev(gl, pl)
        for i in range(20):  # delay-buffer filters need a few frames to fill
            f = _frame(gl, pl, (0.3 + 0.01 * i, 0.6), 0.5)
            pl.begin_layer(); eff.update(f); eff.draw(f)
        img = read(pl.direct.tex)
        if not (np.isfinite(img).all() and img.mean() > 0.002):
            bad.append(f"{key}: draws nothing")
        eff.release(); pl.release()
    assert not bad, bad


def test_scene_layers_add_and_remove(gl):
    mgr = SceneManager(gl, (320, 180))
    mgr.select("Rave", immediate=True)
    n = len(mgr.current.layers)
    bound = {b.target for b in mgr.current.bindings if b.target[:1].isdigit()}
    assert mgr.add_layer("isf:vidvox/Bad TV") and len(mgr.current.layers) == n + 1
    assert not mgr.add_layer("isf:nope")
    mgr.remove_layer(0)   # bindings to later layers move down with them
    assert {b.target for b in mgr.current.bindings if b.target[:1].isdigit()} == \
        {f"{int(t.split(':')[0]) - 1}:{t.split(':')[1]}" for t in bound if not t.startswith("0:")}
    for t in range(3):
        mgr.render(t / 60, 1 / 60, ball_at(t / 60), audio_at(t / 60))
    mgr.select("Museum", immediate=True)
    mgr.select("Rave", immediate=True)    # edits are kept with the preset
    assert [getattr(e, "key", None) for e in mgr.current.layers][-1] == "isf:vidvox/Bad TV"
    assert len(mgr.current.layers) == n


def test_broken_shader_reports_error(gl, tmp_path):
    (tmp_path / "shadertoy").mkdir()
    (tmp_path / "isf").mkdir()
    bad = tmp_path / "shadertoy" / "bad.glsl"
    bad.write_text("void mainImage(out vec4 c, in vec2 p) { c = vec4(undefined_thing); }\n")
    badisf = tmp_path / "isf" / "bad.fs"
    badisf.write_text('/*{"INPUTS": []}*/\nvoid main() { gl_FragColor = nope; }\n')
    for cls, p in ((ShadertoyEffect, bad), (ISFEffect, badisf)):
        eff = cls(gl, p)
        assert eff.error and "bad" in eff.error
        pl = Pipeline(gl, SIZE)
        f = _frame(gl, pl, (0.5, 0.5), 1.0)
        pl.begin_layer()
        eff.draw(f)  # must not raise
        pl.release()
    # a scene with a broken layer still renders its other layers
    from sfere import scenes
    idx = {"shadertoy:bad": (ShadertoyEffect, bad)}
    spec = {"name": "t", "layers": [{"effect": "shadertoy:bad"}, {"effect": "orb"}]}
    s = scenes.Scene(gl, spec, SIZE, idx)
    out = s.render(1.0, 1 / 60, ScreenBall(x=0.9, y=0.5, r=0.03, visible=1, u=0.5, v=0.5), audio_at(0), set(), 0.0, (1, 1))
    assert read(out).mean() > 0.001
    s.release()


def test_unsupported_features_reported(gl, tmp_path):
    (tmp_path / "x.glsl").write_text("uniform samplerCube cube;\nvoid mainImage(out vec4 c, in vec2 p) { c = vec4(1.0); }\n")
    eff = ShadertoyEffect(gl, tmp_path / "x.glsl")
    assert "cubemap channels" in eff.unsupported


def test_scene_edits_survive_switching_and_round_trip(gl):
    mgr = SceneManager(gl, (320, 180))
    mgr.select("Museum", immediate=True)
    s = mgr.current
    # the preset's 'orb.brightness' binding is tied to the orb layer by index
    orb = next(i for i, e in enumerate(s.layers) if e.name == "orb")
    assert s.binding(f"{orb}:brightness") is not None
    s.layers[orb].values["size"] = 2.25
    s.layers[0].enabled = False
    s.post["bloom"] = 1.7
    s.palette[0] = (1.0, 0.0, 0.0)
    s.set_binding("post.zoom", "audio.bass", 1.0, 1.01, 0.2)
    mgr.select("Rave", immediate=True)
    mgr.select("Museum", immediate=True)
    s = mgr.current
    assert s.layers[orb].values["size"] == 2.25 and not s.layers[0].enabled
    assert s.post["bloom"] == 1.7 and mgr.presets[mgr.index]["palette"][0] == "#ff0000"
    assert s.binding("post.zoom").source == "audio.bass" and s.binding(f"{orb}:brightness")
    spec = s.to_spec()
    assert "zoom" not in spec["post"] and spec["bind"]["post.zoom"][0] == "audio.bass"
    assert "brightness" not in spec["layers"][orb].get("values", {})    # bound: the binding drives it
    import json
    assert json.loads(json.dumps(spec)) == spec


def test_layer_tools_keep_bindings_with_their_layer(gl):
    mgr = SceneManager(gl, (320, 180))
    mgr.select("Neon Shaders", immediate=True)
    keys = lambda: [getattr(e, "key", e.name) for e in mgr.current.layers]
    bound = lambda: {getattr(mgr.current.layers[int(b.target.split(":")[0])], "key", None): b.source
                     for b in mgr.current.bindings if not b.target.startswith("post.")}
    before = bound()
    mgr.move_layer(1, 1)                 # plasma orb one step up
    assert keys()[2] == "shadertoy:plasma_orb" and bound() == before
    mgr.duplicate_layer(0)
    assert keys()[:2] == ["isf:hex_glow", "isf:hex_glow"]
    i = keys().index("isf:radial_burst")
    mgr.reset_layer(i)
    assert "isf:radial_burst" not in bound()
    mgr.current.layers[0].enabled = False
    out = mgr.render(0.1, 1 / 60, ball_at(0.1), audio_at(0.1))
    assert np.isfinite(read(out)).all()
    mgr.remove_layer(0)
    assert keys()[0] == "isf:hex_glow" and mgr.current.layers[0].enabled


def test_missing_effect_keeps_its_layer(gl):
    from sfere import scenes
    spec = {"name": "t", "layers": [{"effect": "isf:gone", "values": {"x": 1}, "bind": {"x": ["audio.sub", 0, 1]}},
                                    {"effect": "orb"}]}
    s = scenes.Scene(gl, spec, SIZE, {})
    assert isinstance(s.layers[0], scenes.Missing) and s.errors
    assert s.to_spec()["layers"][0] == spec["layers"][0]
    s.release()


def test_scene_library_tools(gl):
    mgr = SceneManager(gl, (320, 180))
    n = len(mgr.names)
    mgr.select("Cosmic", immediate=True)
    mgr.new_scene(copy_current=True)
    assert mgr.current.name == "Cosmic copy" and mgr.names.index("Cosmic copy") == mgr.names.index("Cosmic") + 1
    assert "preset" not in mgr.presets[mgr.index]
    assert not mgr.rename("Rave") and mgr.rename("Galaxy") and mgr.current.name == "Galaxy"
    mgr.move(-1)
    assert mgr.names.index("Galaxy") < mgr.names.index("Cosmic")
    mgr.new_scene()
    assert mgr.current.name == "New scene" and len(mgr.names) == n + 2
    assert mgr.delete() and "New scene" not in mgr.names
    mgr.select("Rave", immediate=True)
    mgr.remove_layer(0)
    mgr.reset_to_preset()
    assert len(mgr.current.layers) == len(PRESETS[0]["layers"]) and mgr.preset_of_current() == "Rave"
    for t in range(3):
        mgr.render(t / 60, 1 / 60, ball_at(t / 60), audio_at(t / 60))
