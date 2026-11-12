"""Shadertoy and ISF shaders as effect layers (PLAN.md §5.5).

Drop files into effects/shadertoy/*.glsl or effects/isf/*.fs. Each loaded shader also
gets sfere's uniforms: uBall (x, y, radius, visible in 0..1 output uv), uBallVel, uSub,
uBass, uMid, uHigh, uLevel, uBeat, uBeatPhase, uBpm, uPrevFrame. Shadertoy's iMouse and
ISF point2D inputs follow the ball. A shader that fails to compile shows its error and
draws nothing; the show keeps running.

Sidecar <name>.toml (optional): title, author, source, license, [params] for extra
uniforms (Shadertoy), [channels] for iChannel bindings, gain, resolution scale.
"""
from __future__ import annotations

import json
import re
import time
import tomllib
from pathlib import Path

import moderngl
import numpy as np

from ..render.gl import GL, Target, set_uniforms
from .base import Effect, Frame, Param

EFFECTS_DIR = Path(__file__).resolve().parents[2] / "effects"

SFERE_UNIFORMS = """
uniform vec4 uBall;
uniform vec2 uBallVel;
uniform float uSub;
uniform float uBass;
uniform float uMid;
uniform float uHigh;
uniform float uLevel;
uniform float uBeat;
uniform float uBeatPhase;
uniform float uBpm;
uniform sampler2D uPrevFrame;
"""

BLEND_FS = """#version 330
uniform sampler2D src;
uniform float gain;
in vec2 uv;
out vec4 color;
void main() { color = vec4(max(texture(src, uv).rgb, 0.0) * gain, 1.0); }
"""


def _sidecar(path: Path) -> dict:
    side = path.with_suffix(".toml")
    if side.exists():
        try:
            return tomllib.loads(side.read_text())
        except tomllib.TOMLDecodeError as e:
            return {"_error": f"{side.name}: {e}"}
    return {}


class ShaderEffect(Effect):
    """Common parts: sidecar metadata, gain/resolution params, compile errors, reload."""

    kind = "shader"
    base_params = {
        "gain": Param(1.0, 0.0, 3.0, help="brightness of this layer"),
        "resolution": Param(0.5, 0.25, 1.0, help="render scale (shaders can be heavy)"),
    }

    def __init__(self, gl: GL, path: Path, **values):
        self.path = Path(path)
        self.meta = _sidecar(self.path)
        self.error: str | None = self.meta.get("_error")
        self.unsupported: list[str] = []
        self.params = {**self.base_params, **self._declared_params()}
        super().__init__(gl, **values)
        for k in ("gain", "resolution"):
            if k in self.meta and k not in values:
                self.values[k] = float(self.meta[k])
        self.name = self.meta.get("title") or self.path.stem
        self.mtime = self._mtime()
        self.blend = gl.program(BLEND_FS)
        self.targets: dict[str, list[Target]] = {}
        self.frame_index = 0
        self.t0 = time.monotonic()
        try:
            self._build()
        except Exception as e:
            self.error = str(e).strip()

    def _declared_params(self) -> dict:
        out = {}
        for k, v in self.meta.get("params", {}).items():
            if isinstance(v, dict):
                out[k] = Param(float(v.get("default", 0.5)), float(v.get("min", 0.0)), float(v.get("max", 1.0)),
                               v.get("kind", "float"), v.get("help", ""))
        return out

    def _mtime(self) -> float:
        files = [self.path, self.path.with_suffix(".toml"), *self.extra_files()]
        return max((p.stat().st_mtime for p in files if p.exists()), default=0.0)

    def extra_files(self) -> list[Path]:
        return []

    def changed(self) -> bool:
        return self._mtime() != self.mtime

    def license_line(self) -> str:
        m = self.meta
        bits = [m.get("author"), m.get("license"), m.get("source")]
        return " · ".join(b for b in bits if b) or "no license info (add a .toml sidecar)"

    def _target(self, name: str, size, double: bool = False) -> list[Target]:
        t = self.targets.get(name)
        if t is None or t[0].size != size:
            for old in t or []:
                old.release()
            t = [self.gl.target(size) for _ in range(2 if double else 1)]
            for x in t:
                x.use(clear=(0, 0, 0, 1))
            self.targets[name] = t
        return t

    def sfere_uniforms(self, f: Frame) -> dict:
        a, b = f.audio, f.ball
        # uBall: x, y in 0..1 of the output, radius in units of the output height, visibility
        return dict(uBall=(b.u, b.v, b.r, b.visible), uBallVel=(b.vx, b.vy),
                    uSub=a.sub, uBass=a.bass, uMid=a.mid, uHigh=a.high, uLevel=a.level, uBeat=a.beat,
                    uBeatPhase=a.beat_phase, uBpm=a.bpm, uPrevFrame=f.pipeline.prev)

    def draw(self, f: Frame) -> None:
        if self.error:
            return
        w, h = f.size
        s = float(self["resolution"])
        size = (max(16, int(w * s)), max(16, int(h * s)))
        try:
            out = self.render(f, size)
        except Exception as e:  # runtime GL errors: report, don't crash the show
            self.error = f"runtime: {e}"
            return
        self.frame_index += 1
        f.pipeline.bind(bool(self["feedback"]))
        self.gl.draw_quad(self.blend, src=out, gain=float(self["gain"]))

    def release(self):
        for ts in self.targets.values():
            for t in ts:
                t.release()
        self.targets.clear()


# ----------------------------------------------------------------------------- Shadertoy
SHADERTOY_HEADER = """#version 330
uniform vec3 iResolution;
uniform float iTime;
uniform float iTimeDelta;
uniform float iFrameRate;
uniform int iFrame;
uniform vec4 iMouse;
uniform vec4 iDate;
uniform sampler2D iChannel0;
uniform sampler2D iChannel1;
uniform sampler2D iChannel2;
uniform sampler2D iChannel3;
uniform vec3 iChannelResolution[4];
""" + SFERE_UNIFORMS

SHADERTOY_FOOTER = """
out vec4 sfere_out;
void main() {
    vec4 c = vec4(0.0);
    mainImage(c, gl_FragCoord.xy);
    sfere_out = vec4(max(c.rgb, vec3(0.0)), 1.0);
}
"""

PASS_ORDER = ["buffer_a", "buffer_b", "buffer_c", "buffer_d", "image"]


class ShadertoyEffect(ShaderEffect):
    """A Shadertoy shader: <name>.glsl is the Image pass; <name>.buffer_a.glsl ... are Buffers A-D."""

    def extra_files(self):
        return [self.path.with_name(f"{self.path.stem}.{p}.glsl") for p in PASS_ORDER[:4]]

    def _build(self):
        self.passes: dict[str, moderngl.Program] = {}
        self.channels = self.meta.get("channels", {})
        common = self.path.with_name(f"{self.path.stem}.common.glsl")
        common_src = common.read_text() if common.exists() else ""
        for p in PASS_ORDER:
            f = self.path if p == "image" else self.path.with_name(f"{self.path.stem}.{p}.glsl")
            if not f.exists():
                continue
            code = f.read_text()
            self._check(code)
            extra = "".join(f"uniform float {k};\n" for k in self.meta.get("params", {}))
            src = SHADERTOY_HEADER + extra + common_src + "\n#line 1\n" + code + SHADERTOY_FOOTER
            try:
                self.passes[p] = self.gl.program(src)
            except Exception as e:
                raise RuntimeError(f"{f.name}: {e}") from None

    def _check(self, code: str):
        for pat, what in [(r"samplerCube|textureCube", "cubemap channels"), (r"mainSound", "sound shaders"),
                          (r"\biKeyboard\b|texelFetch\(\s*iChannel\d\s*,\s*ivec2\(\s*\w+\s*,\s*[012]\s*\)", "keyboard input"),
                          (r"\biSampleRate\b", "audio sample rate")]:
            if re.search(pat, code):
                self.unsupported.append(what)

    def _channel(self, pass_name: str, i: int, f: Frame):
        """Channel binding: '<pass>.iChannel<i>' in the sidecar; buffers default to themselves on 0."""
        key = self.channels.get(pass_name, {}).get(f"iChannel{i}") if isinstance(self.channels.get(pass_name), dict) \
            else self.channels.get(f"{pass_name}.iChannel{i}")
        if key is None:
            if pass_name != "image" and i == 0:
                key = pass_name                       # buffers read their own last frame
            elif pass_name == "image" and i == 0:
                key = next((p for p in PASS_ORDER[:4] if p in self.passes), "prev")
            else:
                key = "prev"
        if key == "prev":
            return f.pipeline.prev
        if key in self.targets:
            ts = self.targets[key]
            if len(ts) == 1:
                return ts[0].tex
            written_this_frame = PASS_ORDER.index(key) < PASS_ORDER.index(pass_name)
            # a buffer reading itself gets last frame; a later pass gets what was just written
            return ts[self.frame_index % 2 if written_this_frame else (self.frame_index + 1) % 2].tex
        return f.pipeline.prev

    def render(self, f: Frame, size):
        ctx = self.ctx
        b = f.ball
        t = time.monotonic() - self.t0
        lt = time.localtime()
        mouse = (b.u * size[0], b.v * size[1], b.u * size[0] if b.visible > 0.2 else -1.0, b.v * size[1])
        common = dict(iResolution=(float(size[0]), float(size[1]), 1.0), iTime=t, iTimeDelta=f.dt,
                      iFrameRate=1.0 / max(f.dt, 1e-3), iFrame=self.frame_index, iMouse=mouse,
                      iDate=(lt.tm_year, lt.tm_mon - 1, lt.tm_mday, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec),
                      **self.sfere_uniforms(f),
                      **{k: float(v) for k, v in self.values.items() if k not in self.base_params})
        for p in PASS_ORDER[:4]:
            if p in self.passes:
                self._target(p, size, double=True)
        out = None
        ctx.disable(moderngl.BLEND)
        for p in PASS_ORDER:
            prog = self.passes.get(p)
            if prog is None:
                continue
            if p == "image":
                tgt = self._target("image", size)[0]
            else:
                tgt = self.targets[p][self.frame_index % 2]
            chans = {f"iChannel{i}": self._channel(p, i, f) for i in range(4)}
            res = [(float(c.width), float(c.height), 1.0) for c in chans.values()]
            tgt.use()
            ctx.viewport = (0, 0, *size)
            self.gl.draw_quad(prog, **common, **chans, iChannelResolution=[x for r in res for x in r])
            out = tgt.tex
        return out


# ----------------------------------------------------------------------------- ISF
ISF_HEADER = """#version 330
in vec2 uv;
out vec4 isf_FragColor;
#define gl_FragColor isf_FragColor
#define texture2D texture
#define isf_FragNormCoord uv
#define vv_FragNormCoord uv
uniform int PASSINDEX;
uniform vec2 RENDERSIZE;
uniform float TIME;
uniform float TIMEDELTA;
uniform vec4 DATE;
uniform int FRAMEINDEX;
#define IMG_SIZE(img) vec2(textureSize(img, 0))
#define IMG_NORM_PIXEL(img, c) texture(img, c)
#define IMG_PIXEL(img, c) texture(img, (c) / IMG_SIZE(img))
#define IMG_THIS_NORM_PIXEL(img) texture(img, isf_FragNormCoord)
#define IMG_THIS_PIXEL(img) texture(img, isf_FragNormCoord)
""" + SFERE_UNIFORMS

ISF_TYPES = {"float": "float", "bool": "bool", "long": "int", "color": "vec4", "point2D": "vec2",
             "image": "sampler2D", "event": "bool", "audio": "sampler2D", "audioFFT": "sampler2D"}


def parse_isf(text: str) -> tuple[dict, str]:
    m = re.search(r"/\*\s*(\{.*?\})\s*\*/", text, re.S)
    if not m:
        raise ValueError("no ISF JSON header (/*{ ... }*/)")
    return json.loads(m.group(1)), text


class ISFEffect(ShaderEffect):
    """An ISF (Interactive Shader Format) generator or filter."""

    def _declared_params(self):
        try:
            self.isf, _ = parse_isf(self.path.read_text())
        except Exception as e:
            self.isf = {"INPUTS": []}
            self.error = f"{self.path.name}: {e}"
            return {}
        out = {}
        self.images = []
        for inp in self.isf.get("INPUTS", []):
            n, t = inp["NAME"], inp.get("TYPE", "float")
            if t == "float":
                out[n] = Param(float(inp.get("DEFAULT", 0.5)), float(inp.get("MIN", 0.0)), float(inp.get("MAX", 1.0)),
                               "float", inp.get("LABEL", ""))
            elif t in ("bool", "event"):
                out[n] = Param(float(bool(inp.get("DEFAULT", 0))), 0, 1, "bool", inp.get("LABEL", ""))
            elif t == "long":
                vals = inp.get("VALUES", [inp.get("MIN", 0), inp.get("MAX", 10)])
                out[n] = Param(float(inp.get("DEFAULT", vals[0])), float(min(vals)), float(max(vals)), "int")
            elif t == "color":
                out[n] = Param(tuple(inp.get("DEFAULT", [1, 1, 1, 1])), 0, 1, "color", inp.get("LABEL", ""))
            elif t == "point2D":
                mx = inp.get("MAX", [1, 1])
                out[n] = Param(tuple(inp.get("DEFAULT", [0.5, 0.5])), 0, float(max(mx)), "point", inp.get("LABEL", ""))
            elif t in ("image", "audio", "audioFFT"):
                self.images.append((n, t))
            else:
                self.unsupported.append(f"input type {t}")
        return out

    def _build(self):
        if self.error:
            return
        text = self.path.read_text()
        decl = []
        for inp in self.isf.get("INPUTS", []):
            t = ISF_TYPES.get(inp.get("TYPE", "float"))
            if t:
                decl.append(f"uniform {t} {inp['NAME']};")
        self.isf_passes = self.isf.get("PASSES") or [{}]
        for p in self.isf_passes:
            if p.get("TARGET"):
                decl.append(f"uniform sampler2D {p['TARGET']};")
        if self.path.with_suffix(".vs").exists():
            self.unsupported.append("custom vertex shader (.vs) ignored")
        # Drop the JSON header comment and keep line numbers aligned for error messages.
        body = re.sub(r"/\*\s*\{.*?\}\s*\*/", lambda m: "\n" * m.group(0).count("\n"), text, count=1, flags=re.S)
        src = ISF_HEADER + "\n".join(decl) + "\n#line 1\n" + body
        try:
            self.prog = self.gl.program(src)
        except Exception as e:
            raise RuntimeError(f"{self.path.name}: {e}") from None

    def _pass_size(self, p: dict, size):
        def ev(expr, default):
            if expr is None:
                return default
            e = str(expr).replace("$WIDTH", str(size[0])).replace("$HEIGHT", str(size[1]))
            if not re.fullmatch(r"[\d\s.+\-*/()]+", e):
                return default
            return max(1, int(eval(e)))  # arithmetic only (checked above)
        return ev(p.get("WIDTH"), size[0]), ev(p.get("HEIGHT"), size[1])

    def render(self, f: Frame, size):
        ctx = self.ctx
        lt = time.localtime()
        t = time.monotonic() - self.t0
        uni = dict(TIME=t, TIMEDELTA=f.dt, FRAMEINDEX=self.frame_index,
                   DATE=(lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec),
                   **self.sfere_uniforms(f))
        for k, v in self.values.items():
            if k in self.base_params:
                continue
            kind = self.params[k].kind
            if kind == "bool":
                uni[k] = bool(v)
            elif kind == "int":
                uni[k] = int(v)
            elif kind in ("color", "point"):
                uni[k] = tuple(float(x) for x in v)
            else:
                uni[k] = float(v)
        for n, _t in self.images:  # image inputs: the scene so far (filters work on it)
            uni[n] = f.pipeline.prev
        ctx.disable(moderngl.BLEND)
        out = None
        for i, p in enumerate(self.isf_passes):
            psize = self._pass_size(p, size)
            name = p.get("TARGET")
            persistent = bool(p.get("PERSISTENT"))
            if name:
                ts = self._target(name, psize, double=persistent)
                tgt = ts[self.frame_index % 2] if persistent else ts[0]
            else:
                tgt = self._target("__out", psize)[0]
            for q in self.isf_passes:  # targets readable by this pass: last written
                qn = q.get("TARGET")
                if qn and qn in self.targets:
                    qts = self.targets[qn]
                    uni[qn] = qts[(self.frame_index + 1) % 2].tex if len(qts) == 2 and qn == name else \
                        qts[self.frame_index % 2 if len(qts) == 2 else 0].tex
            tgt.use()
            ctx.viewport = (0, 0, *psize)
            self.gl.draw_quad(self.prog, PASSINDEX=i, RENDERSIZE=(float(psize[0]), float(psize[1])), **uni)
            out = tgt.tex
        return out


# ----------------------------------------------------------------------------- discovery
def discover(root: Path = EFFECTS_DIR) -> dict[str, tuple[type, Path]]:
    found = {}
    for p in sorted((root / "shadertoy").glob("*.glsl")):
        if p.stem.count(".") == 0:  # skip name.buffer_a.glsl / name.common.glsl
            found[f"shadertoy:{p.stem}"] = (ShadertoyEffect, p)
    for p in sorted((root / "isf").glob("*.fs")):
        found[f"isf:{p.stem}"] = (ISFEffect, p)
    return found
