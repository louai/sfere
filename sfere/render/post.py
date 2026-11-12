"""Per-scene post chain (PLAN.md §5.1): layer -> feedback (+shockwave) -> bloom -> composite.

Effects draw additively into `layer`. The feedback pass blends the previous accumulated
frame back in (faded, zoomed, rotated, tinted, warped by shockwaves) and adds the layer:
that is light painting for free. Composite applies bloom, kaleidoscope folding, tone
mapping, vignette, black-out and flips, and writes an 8-bit frame.
"""
from __future__ import annotations

import math

import moderngl

from .gl import GL, Target

FEEDBACK_FS = """#version 330
uniform sampler2D prev;
uniform sampler2D layer;
uniform float decay;
uniform float zoom;
uniform float rotate;
uniform float hue_shift;
uniform vec2 aspect;          // (w/h, 1)
uniform vec4 shocks[4];       // (x, y, radius, strength) in uv
in vec2 uv;
out vec4 color;

vec3 hueRotate(vec3 c, float a) {
    const vec3 k = vec3(0.57735);
    float ca = cos(a);
    return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}

void main() {
    vec2 p = (uv - 0.5) * aspect;
    float s = sin(rotate), c = cos(rotate);
    p = mat2(c, -s, s, c) * p / zoom;
    vec2 q = p / aspect + 0.5;
    for (int i = 0; i < 4; i++) {
        vec4 sh = shocks[i];
        if (sh.w <= 0.0) continue;
        vec2 d = (q - sh.xy) * aspect;
        float r = length(d);
        float ring = exp(-pow((r - sh.z) * 28.0, 2.0));
        q -= normalize(d + 1e-6) / aspect * ring * sh.w * 0.012;
    }
    vec3 old = texture(prev, q).rgb;
    if (q.x < 0.0 || q.x > 1.0 || q.y < 0.0 || q.y > 1.0) old = vec3(0.0);
    old = hueRotate(old, hue_shift) * decay;
    color = vec4(old + texture(layer, uv).rgb, 1.0);
}
"""

DOWN_FS = """#version 330
uniform sampler2D src;
uniform vec2 texel;
uniform float threshold;   // < 0: no bright pass
in vec2 uv;
out vec4 color;
void main() {
    vec3 c = texture(src, uv + texel * vec2(-1, -1)).rgb + texture(src, uv + texel * vec2(1, -1)).rgb
           + texture(src, uv + texel * vec2(-1, 1)).rgb + texture(src, uv + texel * vec2(1, 1)).rgb;
    c *= 0.25;
    if (threshold >= 0.0) {
        float br = max(c.r, max(c.g, c.b));
        float soft = clamp(br - threshold + 0.25, 0.0, 0.5);
        soft = soft * soft / 0.5;
        c *= max(soft, br - threshold) / max(br, 1e-4);
    }
    color = vec4(c, 1.0);
}
"""

UP_FS = """#version 330
uniform sampler2D src;
uniform vec2 texel;
in vec2 uv;
out vec4 color;
void main() {   // 9-tap tent
    vec3 c = texture(src, uv).rgb * 4.0;
    c += (texture(src, uv + texel * vec2(1, 0)).rgb + texture(src, uv - texel * vec2(1, 0)).rgb
        + texture(src, uv + texel * vec2(0, 1)).rgb + texture(src, uv - texel * vec2(0, 1)).rgb) * 2.0;
    c += texture(src, uv + texel).rgb + texture(src, uv - texel).rgb
       + texture(src, uv + texel * vec2(1, -1)).rgb + texture(src, uv + texel * vec2(-1, 1)).rgb;
    color = vec4(c / 16.0, 1.0);
}
"""

COMPOSITE_FS = """#version 330
uniform sampler2D accum;
uniform sampler2D bloom;
uniform float bloom_amount;
uniform float exposure;
uniform float vignette;
uniform float blackout;     // 0 visible .. 1 black
uniform float kaleido;      // segments, 0 = off
uniform float kaleido_rot;
uniform vec2 kaleido_center;   // fold around the ball, so it is always in view
uniform vec2 aspect;
uniform vec2 flip;          // 1 or -1
in vec2 uv;
out vec4 color;

vec2 fold(vec2 q) {
    vec2 p = (q - kaleido_center) * aspect;
    float r = length(p);
    float a = atan(p.y, p.x) + kaleido_rot;
    float seg = 6.2831853 / kaleido;
    a = mod(a, seg);
    a = min(a, seg - a);
    a -= kaleido_rot;   // the wedge we sample starts at the ball's side
    return vec2(cos(a), sin(a)) * r / aspect + kaleido_center;
}

vec3 aces(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

void main() {
    vec2 q = (uv - 0.5) * flip + 0.5;
    if (kaleido >= 2.0) q = fold(q);
    vec3 c = texture(accum, q).rgb + texture(bloom, q).rgb * bloom_amount;
    c = aces(c * exposure);
    vec2 v = (uv - 0.5) * aspect;
    c *= mix(1.0, smoothstep(1.25, 0.25, length(v)), vignette);
    c *= 1.0 - blackout;
    color = vec4(pow(c, vec3(1.0 / 2.2)), 1.0);
}
"""

MIX_FS = """#version 330
uniform sampler2D a;
uniform sampler2D b;
uniform float t;
in vec2 uv;
out vec4 color;
void main() { color = mix(texture(a, uv), texture(b, uv), t); }
"""

BLIT_FS = """#version 330
uniform sampler2D src;
uniform float gain;
in vec2 uv;
out vec4 color;
void main() { color = vec4(texture(src, uv).rgb * gain, 1.0); }
"""

DEFAULT_POST = {
    "decay": 0.82, "zoom": 1.0, "rotate": 0.0, "hue_shift": 0.0,
    "bloom": 0.9, "bloom_threshold": 0.6, "exposure": 1.0, "vignette": 0.35,
    "kaleido": 0.0, "kaleido_spin": 0.0,
}


class Pipeline:
    LEVELS = 5

    def __init__(self, gl: GL, size: tuple[int, int]):
        self.gl = gl
        self.size = size
        w, h = size
        self.layer = gl.target(size)      # feeds the feedback loop (light painting)
        self.direct = gl.target(size)     # added once per frame, never fed back
        self.accum = [gl.target(size), gl.target(size)]
        self.scene = [gl.target(size), gl.target(size)]   # accum + direct, this frame and last
        self.bloom = [gl.target((max(1, w >> (i + 1)), max(1, h >> (i + 1)))) for i in range(self.LEVELS)]
        self.out = gl.target(size, dtype="f1")
        self.cur = 0
        self.shocks: list[list[float]] = []   # [x, y, age, strength]
        self.kaleido_angle = 0.0
        self.kaleido_center = (0.5, 0.5)
        self.p_feedback = gl.program(FEEDBACK_FS)
        self.p_down = gl.program(DOWN_FS)
        self.p_up = gl.program(UP_FS)
        self.p_comp = gl.program(COMPOSITE_FS)
        self.p_blit = gl.program(BLIT_FS)
        for t in self.accum + self.scene:
            t.use(clear=(0, 0, 0, 1))

    @property
    def aspect(self) -> float:
        return self.size[0] / self.size[1]

    @property
    def prev(self) -> moderngl.Texture:
        """Last frame's full scene, before bloom (shader effects get it as uPrevFrame)."""
        return self.scene[self.cur].tex

    def begin_layer(self) -> None:
        self.direct.use(clear=(0, 0, 0, 1))
        self.layer.use(clear=(0, 0, 0, 1))
        self.bind(False)

    def bind(self, feedback: bool) -> None:
        """Draw the next effect into the feedback layer (streaks, light painting) or the direct one."""
        (self.layer if feedback else self.direct).use()
        self.gl.ctx.viewport = (0, 0, *self.size)
        self.gl.ctx.enable(moderngl.BLEND)
        self.gl.ctx.blend_func = moderngl.ONE, moderngl.ONE

    def add_shock(self, x_uv: float, y_uv: float, strength: float = 1.0) -> None:
        self.shocks.append([x_uv, y_uv, 0.0, strength])
        self.shocks = self.shocks[-4:]

    def finish(self, post: dict, dt: float, blackout: float = 0.0, flip=(1.0, 1.0),
               center=(0.5, 0.5)) -> moderngl.Texture:
        ctx, gl = self.gl.ctx, self.gl
        ctx.disable(moderngl.BLEND)
        p = {**DEFAULT_POST, **post}
        aspect = (self.aspect, 1.0)
        # shockwaves: expanding rings, fading
        shocks = []
        for s in self.shocks:
            s[2] += dt
        self.shocks = [s for s in self.shocks if s[2] < 1.2]
        for s in self.shocks[:4]:
            shocks += [s[0], s[1], 0.05 + s[2] * 0.9, s[3] * (1.0 - s[2] / 1.2)]
        shocks += [0.0] * (16 - len(shocks))
        nxt = 1 - self.cur
        self.accum[nxt].use()
        ctx.viewport = (0, 0, *self.size)
        gl.draw_quad(self.p_feedback, prev=self.accum[self.cur].tex, layer=self.layer.tex,
                     # decay and zoom are per 1/60 s, so other refresh rates look the same
                     decay=float(p["decay"]) ** (dt * 60.0), zoom=float(p["zoom"]) ** (dt * 60.0),
                     rotate=float(p["rotate"]) * dt,
                     hue_shift=float(p["hue_shift"]) * dt, aspect=aspect, shocks=shocks)
        self.cur = nxt
        # scene = accumulated feedback + this frame's direct layer
        scene = self.scene[self.cur]
        scene.use()
        gl.draw_quad(self.p_blit, src=self.accum[self.cur].tex, gain=1.0)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        gl.draw_quad(self.p_blit, src=self.direct.tex, gain=1.0)
        ctx.disable(moderngl.BLEND)
        # bloom: bright pass + downsample chain, then tent upsample back up (additive)
        src = scene.tex
        for i, t in enumerate(self.bloom):
            t.use()
            ctx.viewport = (0, 0, *t.size)
            gl.draw_quad(self.p_down, src=src, texel=(1.0 / src.width, 1.0 / src.height),
                         threshold=float(p["bloom_threshold"]) if i == 0 else -1.0)
            src = t.tex
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        for i in range(self.LEVELS - 1, 0, -1):
            t = self.bloom[i - 1]
            t.use()
            ctx.viewport = (0, 0, *t.size)
            s = self.bloom[i].tex
            gl.draw_quad(self.p_up, src=s, texel=(1.0 / s.width, 1.0 / s.height))
        ctx.disable(moderngl.BLEND)
        self.kaleido_angle += float(p["kaleido_spin"]) * dt
        k = min(1.0, dt * 4.0)   # follow the ball smoothly
        self.kaleido_center = tuple(a + (b - a) * k for a, b in zip(self.kaleido_center, center))
        self.out.use()
        ctx.viewport = (0, 0, *self.size)
        gl.draw_quad(self.p_comp, accum=scene.tex, bloom=self.bloom[0].tex, bloom_amount=float(p["bloom"]),
                     exposure=float(p["exposure"]), vignette=float(p["vignette"]), blackout=float(blackout),
                     kaleido=float(p["kaleido"]), kaleido_rot=self.kaleido_angle,
                     kaleido_center=self.kaleido_center, aspect=aspect,
                     flip=tuple(float(f) for f in flip))
        return self.out.tex

    def release(self) -> None:
        for t in [self.layer, self.direct, *self.accum, *self.scene, *self.bloom, self.out]:
            t.release()
