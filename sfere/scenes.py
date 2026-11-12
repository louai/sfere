"""Scenes: named stacks of effects with a palette, post settings and bindings (PLAN.md §5.3, §5.5).

A binding drives an effect parameter from the music or the ball's motion:
    "bind": {"glow": ["audio.sub", 0.5, 2.0, 0.2]}   # source, value at 0, value at 1, smoothing (s)
Point parameters (ISF point2D) follow the ball automatically unless bound otherwise.
"""
from __future__ import annotations

import copy
import math
import time

import moderngl
import numpy as np

from .ballfilter import BallState
from .fx.base import Frame, ScreenBall
from .fx.builtin import BUILTIN
from .fx.shaders import ShaderEffect, discover
from .render.gl import GL
from .render.post import MIX_FS, Pipeline


def hexrgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


PRESETS: list[dict] = [
    {
        "name": "Rave",
        "palette": ["#ff2bd6", "#7a2bff", "#2bd9ff", "#2bff88"],
        "post": {"decay": 0.86, "bloom": 1.1, "bloom_threshold": 0.5, "vignette": 0.4},
        "layers": [
            {"effect": "backdrop", "values": {"style": 1, "brightness": 0.05}},
            {"effect": "trail", "values": {"length": 0.7, "width": 0.03}},
            {"effect": "sparkles", "values": {"amount": 70, "feedback": 1}},
            {"effect": "ripples", "values": {"rings_per_beat": 2}},
            {"effect": "orb"},
            {"effect": "shockwave", "values": {"every_beats": 8}},
        ],
    },
    {
        "name": "Museum",
        "palette": ["#f2e6c9", "#c9d8f2", "#e8c9f2", "#c9f2e1"],
        "post": {"decay": 0.97, "bloom": 0.6, "bloom_threshold": 0.7, "vignette": 0.6, "exposure": 0.9},
        "layers": [
            {"effect": "backdrop", "values": {"style": 0, "brightness": 0.012}},
            {"effect": "constellation", "values": {"life": 8.0}},
            {"effect": "orb", "values": {"size": 1.6, "brightness": 0.7, "pulse": 0.2}},
        ],
        "bind": {"orb.brightness": ["audio.level", 0.5, 1.2, 0.3]},
    },
    {
        "name": "Cosmic",
        "palette": ["#3b1cff", "#00e1ff", "#ff6bf3", "#fff36b"],
        "post": {"decay": 0.9, "bloom": 1.0, "kaleido": 6, "kaleido_spin": 0.15, "zoom": 1.004},
        "layers": [
            {"effect": "backdrop", "values": {"style": 2, "brightness": 0.04}},
            {"effect": "orbiters", "values": {"count": 8}},
            {"effect": "spectrum", "values": {"radius": 2.6}},
            {"effect": "orb"},
        ],
        "bind": {"post.kaleido_spin": ["audio.bass", 0.05, 0.6, 0.5]},
    },
    {
        "name": "Ember Storm",
        "palette": ["#ff5a00", "#ffb000", "#ff2a2a", "#ffe08a"],
        "post": {"decay": 0.8, "bloom": 1.3, "bloom_threshold": 0.45, "hue_shift": 0.0},
        "layers": [
            {"effect": "embers", "values": {"rate": 900, "feedback": 1}},
            {"effect": "trail", "values": {"length": 0.35, "width": 0.018}},
            {"effect": "sparkles", "values": {"amount": 40, "gravity": 0.6}},
            {"effect": "orb", "values": {"size": 2.6}},
            {"effect": "shockwave", "values": {"every_beats": 4}},
        ],
        "bind": {"embers.rise": ["audio.sub", 0.1, 0.9, 0.2]},
    },
    {
        "name": "Light Painting",
        "palette": ["#00ffd5", "#ff00a8", "#ffe600", "#4d7cff"],
        "post": {"decay": 0.985, "bloom": 0.9, "hue_shift": 0.25, "vignette": 0.3},
        "layers": [
            {"effect": "trail", "values": {"length": 0.06, "width": 0.01, "speed_width": 2.5, "feedback": 1,
                                           "brightness": 0.35, "taper": 0.0}},
            {"effect": "orb", "values": {"size": 1.4}},
            {"effect": "ripples", "values": {"brightness": 0.6}},
        ],
    },
    {
        "name": "Neon Shaders",
        "palette": ["#ff3df2", "#3dfcff", "#b6ff3d", "#ff9d3d"],
        "post": {"decay": 0.8, "bloom": 1.0},
        "layers": [
            {"effect": "isf:hex_glow"},
            {"effect": "shadertoy:plasma_orb", "bind": {"glow": ["audio.sub", 0.6, 2.0, 0.15]}},
            {"effect": "isf:radial_burst", "bind": {"intensity": ["audio.bass", 0.3, 1.6, 0.1]}},
            {"effect": "isf:rgb_split"},
            {"effect": "orb", "values": {"size": 1.8}},
        ],
    },
    {
        "name": "Ink Tunnel",
        "palette": ["#7b5cff", "#00d2ff", "#ff5ca8", "#5cffb0"],
        "post": {"decay": 0.75, "bloom": 0.8},
        "layers": [
            {"effect": "shadertoy:tunnel"},
            {"effect": "shadertoy:ink_trails"},
            {"effect": "sparkles", "values": {"amount": 40}},
            {"effect": "orb"},
        ],
    },
]


def map_ball(b: BallState, aspect: float, cam_aspect: float, mode: str, flip=(1.0, 1.0)) -> ScreenBall:
    """Camera coords (x right, y down, 0..1) -> output (u, v up) and screen units (x 0..aspect)."""
    if mode == "stretch":
        sw, sh = 1.0, 1.0
    elif (mode == "fit") == (aspect > cam_aspect):
        sw, sh = cam_aspect / aspect, 1.0          # camera rect narrower than the output
    else:
        sw, sh = 1.0, aspect / cam_aspect
    u = 0.5 + (b.x - 0.5) * sw
    v = 0.5 + (0.5 - b.y) * sh
    if flip[0] < 0:
        u = 1 - u
    if flip[1] < 0:
        v = 1 - v
    scale_x = sw * aspect                           # one camera width in screen units
    r = b.r * scale_x
    return ScreenBall(x=u * aspect, y=v, r=min(r, 0.06), r_raw=r, vx=b.vx * scale_x * flip[0], vy=-b.vy * sh * flip[1],
                      speed=b.speed, accel=b.accel, angle=b.angle, curvature=b.curvature, visible=b.visible,
                      u=u, v=v)


class Binding:
    def __init__(self, target: str, spec):
        self.target = target
        self.source, self.lo, self.hi = spec[0], float(spec[1]), float(spec[2])
        self.smooth = float(spec[3]) if len(spec) > 3 else 0.1
        self.value = None

    def read(self, ball: ScreenBall, audio) -> float:
        kind, _, field = self.source.partition(".")
        if kind == "audio":
            x = float(getattr(audio, field, 0.0))
        elif kind == "ball":
            x = float(getattr(ball, field, 0.0))
        else:
            x = 0.0
        return float(np.clip(x, 0.0, 1.0))

    def step(self, dt: float, ball, audio) -> float:
        target = self.lo + (self.hi - self.lo) * self.read(ball, audio)
        if self.value is None or self.smooth <= 0:
            self.value = target
        else:
            self.value += (target - self.value) * (1 - math.exp(-dt / self.smooth))
        return self.value


class Scene:
    def __init__(self, gl: GL, spec: dict, size: tuple[int, int], shader_index: dict):
        self.gl = gl
        self.spec = copy.deepcopy(spec)
        self.name = spec["name"]
        self.palette = [hexrgb(c) if isinstance(c, str) else c for c in spec.get("palette", ["#ffffff"])]
        self.post = dict(spec.get("post", {}))
        self.pipeline = Pipeline(gl, size)
        self.layers: list = []
        self.bindings: list[Binding] = []
        self.errors: list[str] = []
        for k, v in spec.get("bind", {}).items():
            self.bindings.append(Binding(k, v))
        for i, layer in enumerate(spec.get("layers", [])):
            eff = self._make(layer, shader_index)
            if eff is None:
                continue
            self.layers.append(eff)
            for k, v in layer.get("bind", {}).items():
                self.bindings.append(Binding(f"{i}:{k}", v))

    def _make(self, layer: dict, shader_index: dict):
        key = layer["effect"]
        try:
            if key in BUILTIN:
                return BUILTIN[key](self.gl, **layer.get("values", {}))
            if key in shader_index:
                cls, path = shader_index[key]
                eff = cls(self.gl, path, **layer.get("values", {}))
                eff.key = key
                if eff.error:
                    self.errors.append(f"{key}: {eff.error}")
                return eff
            self.errors.append(f"{key}: no such effect")
        except Exception as e:
            self.errors.append(f"{key}: {e}")
        return None

    def reload_changed(self, shader_index: dict) -> list[str]:
        """Hot reload: rebuild shader layers whose files changed, keeping their values."""
        reloaded = []
        for i, eff in enumerate(self.layers):
            if isinstance(eff, ShaderEffect) and eff.changed():
                cls, path = type(eff), eff.path
                new = cls(self.gl, path, **eff.values)
                new.key = getattr(eff, "key", path.stem)
                eff.release()
                self.layers[i] = new
                reloaded.append(f"{new.key}: {'error: ' + new.error if new.error else 'reloaded'}")
        return reloaded

    def _resolve(self, target: str):
        """'post.decay' | '<effect name>.<param>' | '<layer index>:<param>' -> (dict, key)"""
        if target.startswith("post."):
            return self.post, target[5:]
        if ":" in target and target.split(":", 1)[0].isdigit():
            i, p = target.split(":", 1)
            i = int(i)
            return (self.layers[i].values, p) if i < len(self.layers) else (None, None)
        name, _, p = target.partition(".")
        for e in self.layers:
            if e.name == name or getattr(e, "key", None) == name:
                return e.values, p
        return None, None

    def render(self, t: float, dt: float, sball: ScreenBall, audio, events: set, blackout: float, flip) -> moderngl.Texture:
        pl = self.pipeline
        for b in self.bindings:
            d, k = self._resolve(b.target)
            if d is not None and k in d:
                d[k] = b.step(dt, sball, audio)
        for e in self.layers:  # point inputs follow the ball
            for k, p in e.params.items():
                if p.kind == "point" and not any(b.target.endswith(":" + k) for b in self.bindings):
                    e.values[k] = (sball.u, sball.v)
        f = Frame(t=t, dt=dt, aspect=pl.aspect, size=pl.size, ball=sball, audio=audio, events=events,
                  palette=self.palette, pipeline=pl, gl=self.gl)
        for e in self.layers:
            e.update(f)
        pl.begin_layer()
        for e in self.layers:
            pl.bind(bool(e.values.get("feedback", False)))
            e.draw(f)   # shader effects render to their own targets, then add themselves
        center = (sball.u, sball.v) if sball.visible > 0.2 else pl.kaleido_center
        return pl.finish(self.post, dt, blackout, flip, center)

    def release(self):
        for e in self.layers:
            e.release()
        self.pipeline.release()


class SceneManager:
    def __init__(self, gl: GL, size: tuple[int, int], presets: list[dict] | None = None):
        self.gl = gl
        self.size = size
        self.presets = presets or PRESETS
        self.shader_index = discover()
        self.current: Scene | None = None
        self.previous: Scene | None = None
        self.fade = 1.0
        self.fade_time = 2.0
        self.mix_target = gl.target(size, dtype="f1")
        self.p_mix = gl.program(MIX_FS)
        self.blackout = 0.0
        self.blackout_target = 0.0
        self.aspect_mode = "fit"
        self.cam_aspect = 16 / 9
        self.flip = (1.0, 1.0)
        self.messages: list[str] = []
        self._last_reload = 0.0
        self.last_frame_ms = 0.0
        self.select(0, immediate=True)

    @property
    def names(self) -> list[str]:
        return [p["name"] for p in self.presets]

    def select(self, index_or_name, immediate: bool = False) -> None:
        idx = index_or_name if isinstance(index_or_name, int) else self.names.index(index_or_name)
        idx = max(0, min(idx, len(self.presets) - 1))
        if self.current and self.current.name == self.presets[idx]["name"] and not immediate:
            return
        new = Scene(self.gl, self.presets[idx], self.size, self.shader_index)
        self.messages += new.errors
        if self.previous:
            self.previous.release()
        self.previous = None if immediate else self.current
        if immediate and self.current:
            self.current.release()
        self.current = new
        self.fade = 1.0 if immediate else 0.0

    def resize(self, size: tuple[int, int]) -> None:
        if size == self.size:
            return
        self.size = size
        self.mix_target.release()
        self.mix_target = self.gl.target(size, dtype="f1")
        name = self.current.name if self.current else self.names[0]
        if self.previous:
            self.previous.release()
            self.previous = None
        self.select(name, immediate=True)

    def render(self, t: float, dt: float, ball: BallState, audio) -> moderngl.Texture:
        t0 = time.perf_counter()
        if t - self._last_reload > 1.0:  # shader hot reload
            self._last_reload = t
            self.shader_index = discover()
            for s in (self.current, self.previous):
                if s:
                    self.messages += s.reload_changed(self.shader_index)
            self.messages = self.messages[-20:]
        self.blackout += (self.blackout_target - self.blackout) * min(1.0, dt * 3.0)
        aspect = self.size[0] / self.size[1]
        sball = map_ball(ball, aspect, self.cam_aspect, self.aspect_mode)
        events = set(ball.events) | set(audio.events)
        out = self.current.render(t, dt, sball, audio, events, self.blackout, self.flip)
        if self.previous is not None and self.fade < 1.0:
            self.fade = min(1.0, self.fade + dt / self.fade_time)
            old = self.previous.render(t, dt, sball, audio, events, self.blackout, self.flip)
            self.mix_target.use()
            self.gl.ctx.viewport = (0, 0, *self.size)
            self.gl.ctx.disable(moderngl.BLEND)
            self.gl.draw_quad(self.p_mix, a=old, b=out, t=float(self.fade))
            out = self.mix_target.tex
            if self.fade >= 1.0:
                self.previous.release()
                self.previous = None
        self.last_frame_ms = (time.perf_counter() - t0) * 1000
        return out
