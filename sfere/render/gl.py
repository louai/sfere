"""Small moderngl helpers: cached programs, fullscreen quads, float render targets."""
from __future__ import annotations

import moderngl
import numpy as np

QUAD_VS = """#version 330
in vec2 in_pos;
out vec2 uv;
void main() { uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""


def attach_current_context() -> moderngl.Context:
    """A moderngl Context for the GL context current on this thread (one per GLFW window).

    moderngl.create_context() only attaches this way for its first, global context;
    later calls go through glcontext's X11/GLX detection, which can't see the EGL
    contexts GLFW makes on Wayland. This repeats what moderngl.init_context() does,
    without touching moderngl's global default.
    """
    from collections import deque

    from _moderngl import DefaultLoader
    from moderngl import mgl

    ctx = moderngl.Context.__new__(moderngl.Context)
    ctx.mglo, ctx.version_code = mgl.create_context(context=DefaultLoader())
    ctx._info = None
    ctx._extensions = None
    ctx.extra = None
    ctx._gc_mode = None
    ctx._objects = deque()
    ctx._screen = ctx.detect_framebuffer(0)
    ctx.fbo = ctx.detect_framebuffer()
    ctx.mglo.fbo = ctx.fbo.mglo
    return ctx


class GL:
    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        self.quad_vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4").tobytes())
        self._programs: dict[tuple, moderngl.Program] = {}
        self._quads: dict[int, moderngl.VertexArray] = {}

    def program(self, fs: str, vs: str = QUAD_VS, **kw) -> moderngl.Program:
        key = (vs, fs, tuple(sorted(kw.items())))
        p = self._programs.get(key)
        if p is None:
            p = self.ctx.program(vertex_shader=vs, fragment_shader=fs, **kw)
            self._programs[key] = p
        return p

    def quad(self, prog: moderngl.Program) -> moderngl.VertexArray:
        vao = self._quads.get(prog.glo)
        if vao is None:
            vao = self.ctx.vertex_array(prog, [(self.quad_vbo, "2f", "in_pos")])
            self._quads[prog.glo] = vao
        return vao

    def draw_quad(self, prog: moderngl.Program, **uniforms) -> None:
        set_uniforms(prog, **uniforms)
        self.quad(prog).render(moderngl.TRIANGLE_STRIP)

    def target(self, size: tuple[int, int], dtype: str = "f2", components: int = 4) -> "Target":
        return Target(self.ctx, size, dtype, components)


class Target:
    """A texture plus a framebuffer drawing into it."""

    def __init__(self, ctx: moderngl.Context, size, dtype="f2", components=4):
        self.size = (max(1, int(size[0])), max(1, int(size[1])))
        self.tex = ctx.texture(self.size, components, dtype=dtype)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tex.repeat_x = self.tex.repeat_y = False
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])

    def use(self, clear: tuple | None = None):
        self.fbo.use()
        if clear is not None:
            self.fbo.clear(*clear)

    def release(self):
        self.fbo.release()
        self.tex.release()


def set_uniforms(prog: moderngl.Program, **uniforms) -> None:
    """Sets only the uniforms the program actually uses (unused ones are optimized out).

    A moderngl Texture value is bound to the next free texture unit."""
    unit = 0
    for name, value in uniforms.items():
        if name not in prog:
            continue
        if isinstance(value, (moderngl.Texture,)):
            value.use(location=unit)
            prog[name].value = unit
            unit += 1
        else:
            try:
                prog[name].value = value
            except Exception:
                prog[name].write(np.asarray(value, "f4").tobytes())
