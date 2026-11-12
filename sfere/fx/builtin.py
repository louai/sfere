"""The hand-written effects of PLAN.md §5.2."""
from __future__ import annotations

import math
from collections import deque

import moderngl
import numpy as np

from ..render.gl import set_uniforms
from .base import (Effect, Frame, Param, Particles, Ribbons, Sprites, catmull_rom, hsv, palette_color,
                   segments_to_quads, strip_from_path)

rng = np.random.default_rng()


def _ball_color(f: Frame, offset: float = 0.0, speed: float = 0.05) -> np.ndarray:
    return palette_color(f.palette, f.t * speed + offset)


class Orb(Effect):
    """The ball itself: a glowing orb at the tracked position."""
    name = "orb"
    params = {
        "size": Param(2.2, 0.5, 6.0, help="glow size relative to the ball"),
        "brightness": Param(1.2, 0.0, 4.0),
        "pulse": Param(0.6, 0.0, 2.0, help="growth with sub-bass"),
        "color_shift": Param(0.0, 0.0, 1.0, kind="hue"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.sprites = Sprites(gl, 4)

    def draw(self, f: Frame):
        b = f.ball
        if b.visible <= 0.01:
            return
        c = _ball_color(f, self["color_shift"])
        size = max(b.r, 0.012) * self["size"] * (1.0 + self["pulse"] * f.audio.sub * 0.6)
        a = self["brightness"] * b.visible
        data = np.array([[b.x, b.y, size, *c, a, 0.0],
                         [b.x, b.y, size * 1.8, *c, a * 0.18 * (0.4 + f.audio.bass), 0.3]], "f4")
        self.sprites.draw(data[1:], f.aspect, shape=0)
        self.sprites.draw(data[:1], f.aspect, shape=2)

    def release(self):
        self.sprites.release()


class Trail(Effect):
    """Ribbon through the ball's recent path; width grows with speed, color drifts."""
    name = "trail"
    params = {
        "length": Param(0.6, 0.1, 3.0, help="seconds of history"),
        "width": Param(0.025, 0.002, 0.12),
        "speed_width": Param(1.5, 0.0, 4.0),
        "brightness": Param(1.0, 0.0, 3.0),
        "color_spread": Param(0.35, 0.0, 1.0, help="how far along the palette the tail reaches"),
        "taper": Param(1.0, 0.0, 1.0, help="thin the tail; 0 for even lines (light painting)"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.points: deque = deque(maxlen=1200)
        self.ribbons = Ribbons(gl, 4096)

    def update(self, f: Frame):
        b = f.ball
        if b.visible > 0.05:
            self.points.append((f.t, b.x, b.y, b.speed, b.visible))
        while self.points and f.t - self.points[0][0] > self["length"]:
            self.points.popleft()

    def draw(self, f: Frame):
        if len(self.points) < 3:
            return
        arr = np.array(self.points, "f4")
        step = max(1, len(arr) // 160)
        arr = arr[::step]
        pts = catmull_rom(arr[:, 1:3], 3)
        n = len(pts)
        age = np.linspace(1.0, 0.0, n, dtype="f4")         # 1 = oldest
        spd = np.interp(np.linspace(0, len(arr) - 1, n), np.arange(len(arr)), arr[:, 3]).astype("f4")
        vis = np.interp(np.linspace(0, len(arr) - 1, n), np.arange(len(arr)), arr[:, 4]).astype("f4")
        w = self["width"] * (1.0 + self["speed_width"] * spd) * (1.0 - age * self["taper"]) ** 0.7 * (0.6 + 0.6 * f.audio.bass)
        cols = np.array([palette_color(f.palette, f.t * 0.05 + a * self["color_spread"]) for a in age[::8]], "f4")
        cols = np.repeat(cols, 8, axis=0)[:n]
        alpha = (1.0 - age * self["taper"]) ** 1.5 * self["brightness"] * vis
        colors = np.concatenate([cols, alpha[:, None]], axis=1)
        self.ribbons.draw(strip_from_path(pts, w, colors), f.aspect)

    def release(self):
        self.ribbons.release()


RING_FS = """#version 330
in vec2 c;
in vec4 col;
in float seed;     // ring thickness (fraction of radius)
out vec4 color;
void main() {
    float r = length(c);
    float th = max(seed, 0.01);
    float a = exp(-pow((r - (1.0 - th)) / th, 2.0) * 2.0);
    a *= smoothstep(1.0, 0.97, r);
    color = vec4(col.rgb * col.a * a, 1.0);
}
"""


class Ripples(Effect):
    """Concentric rings expanding from the ball on beats (and on stalls)."""
    name = "ripples"
    params = {
        "speed": Param(0.55, 0.05, 2.0, help="expansion, screen heights per second"),
        "life": Param(1.4, 0.2, 4.0),
        "thickness": Param(0.06, 0.01, 0.3),
        "brightness": Param(1.2, 0.0, 3.0),
        "on_beat": Param(1.0, 0, 1, kind="bool"),
        "on_stall": Param(1.0, 0, 1, kind="bool"),
        "rings_per_beat": Param(1, 1, 4, kind="int"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        from .base import SPRITE_VS
        self.prog = gl.program(RING_FS, SPRITE_VS)
        self.sprites = Sprites(gl, 64)
        self.sprites.prog = self.prog
        self.sprites.vao = gl.ctx.vertex_array(self.prog, [
            (self.sprites.corners, "2f", "corner"),
            (self.sprites.inst, "2f 1f 4f 1f/i", "i_pos", "i_size", "i_color", "i_seed")])
        self.rings: list[list] = []

    def _spawn(self, f: Frame, strength: float):
        b = f.ball
        if b.visible < 0.2:
            return
        for k in range(int(self["rings_per_beat"])):
            c = _ball_color(f, 0.1 * k)
            self.rings.append([b.x, b.y, f.t + k * 0.08, strength, *c])

    def update(self, f: Frame):
        if self["on_beat"] and "beat" in f.events:
            self._spawn(f, 0.6 + 0.6 * f.audio.bass)
        if self["on_stall"] and "stall" in f.events:
            self._spawn(f, 1.0)
        self.rings = [r for r in self.rings if f.t - r[2] < self["life"]][-48:]

    def draw(self, f: Frame):
        if not self.rings:
            return
        data = []
        for x, y, t0, s, r, g, b in self.rings:
            age = f.t - t0
            if age < 0:
                continue
            k = age / self["life"]
            data.append([x, y, 0.02 + age * self["speed"], r, g, b, s * self["brightness"] * (1 - k) ** 1.5,
                         self["thickness"] * (1 - 0.5 * k)])
        if data:
            set_uniforms(self.prog, aspect=f.aspect)
            n = len(data)
            self.sprites.inst.write(np.array(data, "f4").tobytes())
            self.sprites.vao.render(moderngl.TRIANGLE_STRIP, instances=n)

    def release(self):
        self.sprites.release()


class Sparkles(Effect):
    """Glittery bursts on onsets (sized by energy), beats and flicks."""
    name = "sparkles"
    params = {
        "amount": Param(60, 5, 400, kind="int", help="particles per burst"),
        "speed": Param(0.6, 0.05, 2.5),
        "life": Param(1.2, 0.2, 4.0),
        "gravity": Param(-0.5, -3.0, 1.0),
        "size": Param(0.012, 0.002, 0.05),
        "on_onset": Param(1.0, 0, 1, kind="bool"),
        "on_subdivision": Param(0.0, 0, 1, kind="bool"),
        "on_beat": Param(1.0, 0, 1, kind="bool"),
        "on_flick": Param(1.0, 0, 1, kind="bool"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.p = Particles(20000)
        self.sprites = Sprites(gl, 20000)

    def burst(self, f: Frame, scale: float):
        b = f.ball
        if b.visible < 0.2:
            return
        n = int(self["amount"] * scale)
        ang = rng.uniform(0, 2 * np.pi, n)
        sp = self["speed"] * scale * rng.uniform(0.2, 1.0, n) ** 0.5
        vel = np.stack([np.cos(ang) * sp + b.vx * 0.3, np.sin(ang) * sp + b.vy * 0.3], 1)
        col = np.array([[*palette_color(f.palette, f.t * 0.05 + rng.uniform(0, 0.3)), 1.0] for _ in range(min(n, 8))], "f4")
        col = col[rng.integers(0, len(col), n)]
        self.p.spawn(n, np.array([b.x, b.y]) + rng.normal(0, b.r * 0.4, (n, 2)), vel,
                     self["life"] * rng.uniform(0.5, 1.0, n), self["size"] * rng.uniform(0.5, 1.5, n), col)

    def update(self, f: Frame):
        e = f.events
        if self["on_beat"] and "beat" in e:
            self.burst(f, 1.0 + f.audio.bass)
        if self["on_onset"] and "onset" in e and "beat" not in e:
            self.burst(f, 0.3 + 0.5 * f.audio.level)
        if self["on_subdivision"] and "subdivision" in e:
            self.burst(f, 0.25)
        if self["on_flick"] and "flick" in e:
            self.burst(f, 1.5)
        self.p.step(f.dt, (0.0, self["gravity"]), drag=1.2)

    def draw(self, f: Frame):
        self.sprites.draw(self.p.instances(), f.aspect, shape=1, time=f.t)

    def release(self):
        self.sprites.release()


class Embers(Effect):
    """A comet tail of embers streaming off the ball, emission rate following speed."""
    name = "embers"
    params = {
        "rate": Param(500, 0, 3000, help="particles per second at full speed"),
        "idle_rate": Param(30, 0, 500),
        "life": Param(1.6, 0.2, 5.0),
        "rise": Param(0.25, -1.0, 1.5),
        "size": Param(0.008, 0.002, 0.04),
        "warmth": Param(0.06, 0.0, 1.0, kind="hue", help="hue of the embers"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.p = Particles(30000)
        self.sprites = Sprites(gl, 30000)
        self._acc = 0.0

    def update(self, f: Frame):
        b = f.ball
        if b.visible > 0.2:
            self._acc += (self["idle_rate"] + self["rate"] * b.speed * (0.5 + f.audio.high)) * f.dt
            n = int(self._acc)
            self._acc -= n
            if n:
                vel = np.stack([-b.vx * 0.15 + rng.normal(0, 0.05, n), -b.vy * 0.15 + rng.normal(0, 0.05, n)], 1)
                heat = rng.uniform(0, 1, n)
                col = np.array([[*hsv(self["warmth"] + 0.08 * h, 1.0 - 0.6 * h, 1.0), 0.9] for h in heat[:16]], "f4")
                col = col[rng.integers(0, len(col), n)]
                self.p.spawn(n, np.array([b.x, b.y]) + rng.normal(0, b.r * 0.5, (n, 2)), vel,
                             self["life"] * rng.uniform(0.4, 1.0, n), self["size"] * rng.uniform(0.6, 1.4, n), col)
        self.p.step(f.dt, (0.0, self["rise"]), drag=0.8)

    def draw(self, f: Frame):
        self.sprites.draw(self.p.instances(), f.aspect, shape=0)

    def release(self):
        self.sprites.release()


class Orbiters(Effect):
    """Satellites orbiting the ball; radius follows bass, spin locks to the tempo."""
    name = "orbiters"
    params = {
        "count": Param(6, 1, 24, kind="int"),
        "radius": Param(3.5, 1.0, 12.0, help="in ball radii"),
        "bass_radius": Param(2.0, 0.0, 8.0),
        "beats_per_turn": Param(4.0, 0.5, 16.0),
        "size": Param(0.012, 0.002, 0.05),
        "trails": Param(1.0, 0, 1, kind="bool"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.sprites = Sprites(gl, 64)
        self.angle = 0.0

    def update(self, f: Frame):
        bpm = f.audio.bpm if f.audio.bpm else 120.0
        self.angle += 2 * np.pi * f.dt * (bpm / 60.0) / self["beats_per_turn"]

    def draw(self, f: Frame):
        b = f.ball
        if b.visible < 0.05:
            return
        n = int(self["count"])
        rad = max(b.r, 0.015) * (self["radius"] + self["bass_radius"] * f.audio.bass)
        a = self.angle + np.arange(n) * 2 * np.pi / n
        wob = 1.0 + 0.15 * np.sin(a * 3 + f.t * 2)
        data = np.zeros((n, 8), "f4")
        data[:, 0] = b.x + np.cos(a) * rad * wob
        data[:, 1] = b.y + np.sin(a) * rad * wob * 0.85
        data[:, 2] = self["size"] * (1 + f.audio.beat * 0.8)
        for i in range(n):
            data[i, 3:6] = palette_color(f.palette, i / n + f.t * 0.03)
        data[:, 6] = b.visible * 1.3
        self.sprites.draw(data, f.aspect, shape=0)

    def release(self):
        self.sprites.release()


BAR_VS = """#version 330
in vec2 corner;
in vec4 i_bar;     // angle, inner radius, length, half-width (radians)
in vec4 i_color;
uniform vec2 center;
uniform float aspect;
out vec2 c;
out vec4 col;
void main() {
    c = corner;
    col = i_color;
    float a = i_bar.x + corner.x * i_bar.w;
    float r = i_bar.y + (corner.y * 0.5 + 0.5) * i_bar.z;
    vec2 p = center + vec2(cos(a), sin(a)) * r;
    gl_Position = vec4(p.x / aspect * 2.0 - 1.0, p.y * 2.0 - 1.0, 0.0, 1.0);
}
"""

BAR_FS = """#version 330
in vec2 c;
in vec4 col;
out vec4 color;
void main() {
    float a = (1.0 - abs(c.x)) * (0.4 + 0.6 * (c.y * 0.5 + 0.5));
    color = vec4(col.rgb * col.a * a, 1.0);
}
"""


class SpectrumHalo(Effect):
    """The spectrum as radial bars around the ball."""
    name = "spectrum"
    params = {
        "radius": Param(2.2, 1.0, 10.0, help="in ball radii"),
        "length": Param(0.12, 0.01, 0.5),
        "brightness": Param(0.9, 0.0, 3.0),
        "mirror": Param(1.0, 0, 1, kind="bool"),
        "spin": Param(0.15, -2.0, 2.0),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.prog = gl.program(BAR_FS, BAR_VS)
        self.corners = gl.ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4").tobytes())
        self.inst = gl.ctx.buffer(reserve=128 * 8 * 4, dynamic=True)
        self.vao = gl.ctx.vertex_array(self.prog, [(self.corners, "2f", "corner"),
                                                   (self.inst, "4f 4f/i", "i_bar", "i_color")])
        self.rot = 0.0
        self.smooth = np.zeros(64, "f4")

    def update(self, f: Frame):
        self.rot += self["spin"] * f.dt
        s = f.audio.spectrum
        self.smooth = np.maximum(s, self.smooth * math.exp(-6 * f.dt))

    def draw(self, f: Frame):
        b = f.ball
        if b.visible < 0.05:
            return
        spec = self.smooth
        if self["mirror"]:
            spec = np.concatenate([spec, spec[::-1]])
        n = len(spec)
        ang = self.rot + np.arange(n) * 2 * np.pi / n
        data = np.zeros((n, 8), "f4")
        data[:, 0] = ang
        data[:, 1] = max(b.r, 0.015) * self["radius"]
        data[:, 2] = 0.004 + spec * self["length"]
        data[:, 3] = np.pi / n * 0.7
        for i in range(0, n, 8):
            data[i:i + 8, 4:7] = palette_color(f.palette, i / n + f.t * 0.02)
        data[:, 7] = b.visible * self["brightness"] * (0.3 + spec)
        self.inst.write(data.tobytes())
        set_uniforms(self.prog, center=(b.x, b.y), aspect=f.aspect)
        self.vao.render(moderngl.TRIANGLE_STRIP, instances=n)

    def release(self):
        self.vao.release()
        self.inst.release()
        self.corners.release()


class Constellation(Effect):
    """The path becomes stars joined by thin lines: a museum-like look."""
    name = "constellation"
    params = {
        "interval": Param(0.12, 0.03, 0.6, help="seconds between stars"),
        "life": Param(6.0, 1.0, 20.0),
        "link": Param(0.12, 0.0, 0.5, help="also link stars closer than this"),
        "star_size": Param(0.01, 0.002, 0.04),
        "line_width": Param(0.0018, 0.0005, 0.01),
        "brightness": Param(0.8, 0.0, 3.0),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.stars: deque = deque(maxlen=400)
        self.sprites = Sprites(gl, 400)
        self.ribbons = Ribbons(gl, 6 * 2000)
        self._last = -1.0

    def update(self, f: Frame):
        b = f.ball
        beat = "beat" in f.events
        if b.visible > 0.3 and (f.t - self._last > self["interval"] or beat) and (b.speed > 0.03 or beat):
            self._last = f.t
            self.stars.append((b.x, b.y, f.t, 1.6 if beat else 1.0))
        while self.stars and f.t - self.stars[0][2] > self["life"]:
            self.stars.popleft()

    def draw(self, f: Frame):
        if len(self.stars) < 1:
            return
        s = np.array(self.stars, "f4")
        k = 1.0 - (f.t - s[:, 2]) / self["life"]
        col = np.array([palette_color(f.palette, 0.5 + 0.2 * kk) for kk in k], "f4")
        tw = 0.75 + 0.25 * np.sin(f.t * 3 + np.arange(len(s)))
        data = np.zeros((len(s), 8), "f4")
        data[:, :2] = s[:, :2]
        data[:, 2] = self["star_size"] * s[:, 3]
        data[:, 3:6] = col
        data[:, 6] = k * tw * self["brightness"] * 1.5
        self.sprites.draw(data, f.aspect, shape=1, time=f.t)
        if len(s) >= 2:
            a, b2 = s[:-1, :2], s[1:, :2]
            kk = np.minimum(k[:-1], k[1:])
            pairs_a, pairs_b, pk = [a], [b2], [kk]
            if self["link"] > 0 and len(s) > 3:
                d = np.linalg.norm(s[:, None, :2] - s[None, :, :2], axis=2)
                i, j = np.nonzero(np.triu(d < self["link"], 2))
                sel = slice(0, 300)
                pairs_a.append(s[i[sel], :2]); pairs_b.append(s[j[sel], :2])
                pk.append(np.minimum(k[i[sel]], k[j[sel]]) * 0.5)
            A, B, K = np.concatenate(pairs_a), np.concatenate(pairs_b), np.concatenate(pk)
            colors = np.concatenate([np.tile(palette_color(f.palette, 0.55), (len(A), 1)),
                                     (K * self["brightness"] * 0.6)[:, None]], 1).astype("f4")
            self.ribbons.draw(segments_to_quads(A, B, self["line_width"], colors), f.aspect,
                              mode=moderngl.TRIANGLES, softness=0.9)

    def release(self):
        self.sprites.release()
        self.ribbons.release()


class Shockwave(Effect):
    """Screen-space ripples that warp the feedback buffer, on drops, beats or stalls."""
    name = "shockwave"
    params = {
        "strength": Param(1.0, 0.0, 3.0),
        "every_beats": Param(4, 1, 16, kind="int", help="on every Nth beat"),
        "on_stall": Param(1.0, 0, 1, kind="bool"),
        "on_drop": Param(1.0, 0, 1, kind="bool"),
    }

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.beats = 0

    def update(self, f: Frame):
        b = f.ball
        fire = False
        if "beat" in f.events:
            self.beats += 1
            fire = self.beats % int(self["every_beats"]) == 0
        fire |= bool(self["on_stall"]) and "stall" in f.events
        fire |= bool(self["on_drop"]) and "drop" in f.events
        if fire and b.visible > 0.2:
            f.pipeline.add_shock(b.u, b.v, self["strength"] * (0.6 + 0.6 * f.audio.bass))


class Backdrop(Effect):
    """A generated background: slow gradient, starfield or noise field (never camera pixels)."""
    name = "backdrop"
    params = {
        "style": Param(1, 0, 2, kind="int", help="0 gradient, 1 stars, 2 noise"),
        "brightness": Param(0.06, 0.0, 0.5),
        "react": Param(0.5, 0.0, 2.0, help="how much it pulses with the bass"),
    }

    FS = """#version 330
in vec2 uv;
uniform float time;
uniform float aspect;
uniform int style;
uniform float brightness;
uniform float pulse;
uniform vec3 c1;
uniform vec3 c2;
out vec4 color;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
void main() {
    vec2 p = vec2(uv.x * aspect, uv.y);
    vec3 c;
    if (style == 0) {
        c = mix(c1, c2, uv.y + 0.2 * sin(time * 0.1 + uv.x * 2.0));
    } else if (style == 1) {
        vec2 g = p * 60.0;
        vec2 id = floor(g);
        float h = hash(id);
        float star = step(0.985, h) * exp(-length(fract(g) - 0.5) * 9.0) * (0.5 + 0.5 * sin(time * (1.0 + h * 3.0) + h * 30.0));
        c = c2 * 0.3 * noise(p * 2.0 + time * 0.02) + vec3(star) * 4.0;
    } else {
        float n = noise(p * 3.0 + vec2(time * 0.05, 0)) * 0.6 + noise(p * 7.0 - time * 0.08) * 0.4;
        c = mix(c1, c2, n) * n;
    }
    color = vec4(c * brightness * (1.0 + pulse), 1.0);
}
"""

    def __init__(self, gl, **v):
        super().__init__(gl, **v)
        self.prog = gl.program(self.FS)

    def draw(self, f: Frame):
        self.gl.draw_quad(self.prog, time=f.t, aspect=f.aspect, style=int(self["style"]),
                          brightness=self["brightness"], pulse=self["react"] * f.audio.sub,
                          c1=tuple(palette_color(f.palette, 0.0)), c2=tuple(palette_color(f.palette, 0.5)))


BUILTIN = {c.name: c for c in (Orb, Trail, Ripples, Sparkles, Embers, Orbiters, SpectrumHalo, Constellation,
                               Shockwave, Backdrop)}
