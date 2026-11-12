"""Effect framework: parameters, the per-frame context, and shared drawing helpers.

Screen space for effects: x in 0..aspect, y in 0..1, y up. Effects draw additively into
the scene's layer target. Every effect exposes `params` (name -> Param), which the
control UI turns into sliders and scene bindings can drive from audio or motion.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field

import moderngl
import numpy as np

from ..render.gl import GL, set_uniforms


@dataclass
class Param:
    default: float
    lo: float = 0.0
    hi: float = 1.0
    kind: str = "float"   # float | int | bool | hue | point | color
    help: str = ""


@dataclass
class ScreenBall:
    x: float = 0.0          # screen units
    y: float = 0.0
    r: float = 0.02         # capped so effects stay sane when the ball is right at the camera
    r_raw: float = 0.02     # uncapped (close-ups can be ~40% of the frame)
    vx: float = 0.0
    vy: float = 0.0
    speed: float = 0.0      # 0..1
    accel: float = 0.0
    angle: float = 0.0
    curvature: float = 0.0
    visible: float = 0.0
    u: float = 0.5          # 0..1 across the output (for shaders)
    v: float = 0.5


@dataclass
class Frame:
    t: float
    dt: float
    aspect: float
    size: tuple
    ball: ScreenBall
    audio: object           # AudioState
    events: set
    palette: list
    pipeline: object        # render.post.Pipeline
    gl: GL = None
    extra: dict = field(default_factory=dict)


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> tuple[float, float, float]:
    return colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, v))


def palette_color(palette: list, x: float) -> np.ndarray:
    """Smoothly interpolated color from a list of RGB triples, x wraps 0..1."""
    n = len(palette)
    x = (x % 1.0) * n
    i = int(x)
    f = x - i
    a, b = np.array(palette[i % n], "f4"), np.array(palette[(i + 1) % n], "f4")
    return a * (1 - f) + b * f


class Effect:
    name = "effect"
    params: dict[str, Param] = {}
    feedback_default = False   # draw into the feedback loop (streaks) instead of the direct layer

    def __init__(self, gl: GL, **values):
        self.gl = gl
        self.ctx: moderngl.Context = gl.ctx
        if "feedback" not in self.params:
            self.params = {**self.params, "feedback": Param(float(self.feedback_default), 0, 1, "bool",
                                                            "leave light-painting streaks")}
        self.values = {k: p.default for k, p in self.params.items()}
        self.values.update({k: v for k, v in values.items() if k in self.params})

    def __getitem__(self, k):
        return self.values[k]

    def update(self, f: Frame) -> None:
        pass

    def draw(self, f: Frame) -> None:
        pass

    def release(self) -> None:
        pass


# --------------------------------------------------------------------------- sprites
SPRITE_VS = """#version 330
in vec2 corner;
in vec2 i_pos;
in float i_size;
in vec4 i_color;
in float i_seed;
uniform float aspect;
out vec2 c;
out vec4 col;
out float seed;
void main() {
    c = corner;
    col = i_color;
    seed = i_seed;
    vec2 p = i_pos + corner * i_size;
    gl_Position = vec4(p.x / aspect * 2.0 - 1.0, p.y * 2.0 - 1.0, 0.0, 1.0);
}
"""

SPRITE_FS = """#version 330
in vec2 c;
in vec4 col;
in float seed;
uniform int shape;      // 0 soft dot, 1 sparkle, 2 orb, 3 ring
uniform float time;
out vec4 color;
void main() {
    float r = length(c);
    float a;
    if (shape == 0) {
        a = exp(-r * r * 6.0);
    } else if (shape == 1) {
        float tw = 0.6 + 0.4 * sin(time * 18.0 + seed * 40.0);
        float core = exp(-r * r * 22.0);
        float rays = exp(-abs(c.x) * 40.0) * exp(-abs(c.y) * 3.0) + exp(-abs(c.y) * 40.0) * exp(-abs(c.x) * 3.0);
        a = (core + rays * 0.6) * tw;
    } else if (shape == 2) {
        float core = smoothstep(0.32, 0.22, r);
        float glow = exp(-r * r * 5.0) * 0.8;
        a = core * 1.6 + glow;
    } else {
        a = exp(-pow((r - 0.8) * 9.0, 2.0));
    }
    a *= smoothstep(1.0, 0.85, r);
    color = vec4(col.rgb * col.a * a, 1.0);
}
"""


class Sprites:
    """Instanced camera-facing quads. Instance layout: x, y, size, r, g, b, a, seed (8 floats)."""

    def __init__(self, gl: GL, capacity: int):
        self.gl, self.capacity = gl, capacity
        self.prog = gl.program(SPRITE_FS, SPRITE_VS)
        self.corners = gl.ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4").tobytes())
        self.inst = gl.ctx.buffer(reserve=capacity * 8 * 4, dynamic=True)
        self.vao = gl.ctx.vertex_array(self.prog, [
            (self.corners, "2f", "corner"),
            (self.inst, "2f 1f 4f 1f/i", "i_pos", "i_size", "i_color", "i_seed"),
        ])

    def draw(self, data: np.ndarray, aspect: float, shape: int, time: float = 0.0) -> None:
        n = min(len(data), self.capacity)
        if n == 0:
            return
        self.inst.write(np.ascontiguousarray(data[:n], "f4").tobytes())
        set_uniforms(self.prog, aspect=aspect, shape=shape, time=time)
        self.vao.render(moderngl.TRIANGLE_STRIP, instances=n)

    def release(self):
        self.vao.release()
        self.inst.release()
        self.corners.release()


# --------------------------------------------------------------------------- ribbons / lines
RIBBON_VS = """#version 330
in vec2 in_pos;
in vec4 in_color;
in float in_side;
uniform float aspect;
out vec4 col;
out float side;
void main() {
    col = in_color;
    side = in_side;
    gl_Position = vec4(in_pos.x / aspect * 2.0 - 1.0, in_pos.y * 2.0 - 1.0, 0.0, 1.0);
}
"""

RIBBON_FS = """#version 330
in vec4 col;
in float side;
uniform float softness;
out vec4 color;
void main() {
    float a = 1.0 - smoothstep(1.0 - softness, 1.0, abs(side));
    a += exp(-side * side * 8.0) * 0.6;   // bright core
    color = vec4(col.rgb * col.a * a, 1.0);
}
"""


class Ribbons:
    """Soft-edged triangle geometry. Vertex layout: x, y, r, g, b, a, side (7 floats)."""

    def __init__(self, gl: GL, capacity: int):
        self.gl, self.capacity = gl, capacity
        self.prog = gl.program(RIBBON_FS, RIBBON_VS)
        self.vbo = gl.ctx.buffer(reserve=capacity * 7 * 4, dynamic=True)
        self.vao = gl.ctx.vertex_array(self.prog, [(self.vbo, "2f 4f 1f", "in_pos", "in_color", "in_side")])

    def draw(self, verts: np.ndarray, aspect: float, mode=moderngl.TRIANGLE_STRIP, softness: float = 0.7):
        n = min(len(verts), self.capacity)
        if n < 3:
            return
        self.vbo.write(np.ascontiguousarray(verts[:n], "f4").tobytes())
        set_uniforms(self.prog, aspect=aspect, softness=softness)
        self.vao.render(mode, vertices=n)

    def release(self):
        self.vao.release()
        self.vbo.release()


def strip_from_path(pts: np.ndarray, widths: np.ndarray, colors: np.ndarray) -> np.ndarray:
    """Triangle-strip vertices for a ribbon along pts (N,2) with per-point width/color."""
    n = len(pts)
    if n < 2:
        return np.zeros((0, 7), "f4")
    d = np.gradient(pts, axis=0)
    ln = np.linalg.norm(d, axis=1, keepdims=True)
    d = d / np.maximum(ln, 1e-6)
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    out = np.zeros((n * 2, 7), "f4")
    out[0::2, :2] = pts + nrm * widths[:, None]
    out[1::2, :2] = pts - nrm * widths[:, None]
    out[0::2, 2:6] = colors
    out[1::2, 2:6] = colors
    out[0::2, 6] = 1.0
    out[1::2, 6] = -1.0
    return out


def segments_to_quads(a: np.ndarray, b: np.ndarray, width: float, colors: np.ndarray) -> np.ndarray:
    """TRIANGLES vertices for thick soft line segments a[i] -> b[i]."""
    if len(a) == 0:
        return np.zeros((0, 7), "f4")
    d = b - a
    d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1) * width
    p = [a + nrm, a - nrm, b + nrm, b - nrm]
    sides = [1.0, -1.0, 1.0, -1.0]
    order = [0, 1, 2, 2, 1, 3]
    out = np.zeros((len(a) * 6, 7), "f4")
    for k, o in enumerate(order):
        out[k::6, :2] = p[o]
        out[k::6, 2:6] = colors
        out[k::6, 6] = sides[o]
    return out


def catmull_rom(pts: np.ndarray, sub: int = 4) -> np.ndarray:
    """Smooth a polyline (N,2) by Catmull-Rom subdivision."""
    if len(pts) < 3:
        return pts
    p = np.vstack([pts[0], pts, pts[-1]])
    t = np.linspace(0, 1, sub, endpoint=False)[:, None]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(pts[-1:])
    return np.vstack(out)


# --------------------------------------------------------------------------- particles
class Particles:
    """Vectorized particle store (never loops over particles in Python)."""

    def __init__(self, capacity: int):
        self.cap = capacity
        self.pos = np.zeros((capacity, 2), "f4")
        self.vel = np.zeros((capacity, 2), "f4")
        self.life = np.zeros(capacity, "f4")
        self.max_life = np.ones(capacity, "f4")
        self.size = np.zeros(capacity, "f4")
        self.color = np.zeros((capacity, 4), "f4")
        self.seed = np.random.rand(capacity).astype("f4")
        self._next = 0

    def spawn(self, n: int, pos, vel, life, size, color) -> None:
        n = int(min(n, self.cap))
        if n <= 0:
            return
        dead = np.flatnonzero(self.life <= 0)
        if len(dead) < n:  # recycle the oldest slots round-robin
            idx = (np.arange(n) + self._next) % self.cap
            self._next = (self._next + n) % self.cap
        else:
            idx = dead[:n]
        self.pos[idx] = pos
        self.vel[idx] = vel
        self.life[idx] = life
        self.max_life[idx] = life
        self.size[idx] = size
        self.color[idx] = color

    def step(self, dt: float, gravity=(0.0, 0.0), drag: float = 0.0) -> None:
        alive = self.life > 0
        if not alive.any():
            return
        self.vel[alive] += np.asarray(gravity, "f4") * dt
        if drag:
            self.vel[alive] *= math.exp(-drag * dt)
        self.pos[alive] += self.vel[alive] * dt
        self.life[alive] -= dt

    def instances(self, fade_size: bool = True) -> np.ndarray:
        alive = np.flatnonzero(self.life > 0)
        k = np.clip(self.life[alive] / self.max_life[alive], 0, 1)
        out = np.empty((len(alive), 8), "f4")
        out[:, 0:2] = self.pos[alive]
        out[:, 2] = self.size[alive] * (0.4 + 0.6 * k if fade_size else 1.0)
        out[:, 3:6] = self.color[alive, :3]
        out[:, 6] = self.color[alive, 3] * k
        out[:, 7] = self.seed[alive]
        return out

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.life > 0))
