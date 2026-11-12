"""Read rendered frames back from the GPU for recording and the virtual camera.

The output texture is drawn into a target of the wanted size (letterboxed, flipped so rows
come out top-first), and read into a pixel buffer. The read is collected one grab later,
so the GPU copy never makes the render loop wait.
"""
from __future__ import annotations

import moderngl

from ..render.gl import GL

GRAB_FS = """#version 330
uniform sampler2D src;
uniform vec2 scale;      // fraction of the frame the image covers (letterbox)
in vec2 uv;
out vec4 color;
void main() {
    vec2 q = (vec2(uv.x, 1.0 - uv.y) - 0.5) / scale + 0.5;   // top row first in memory
    if (q.x < 0.0 || q.x > 1.0 || q.y < 0.0 || q.y > 1.0) { color = vec4(0, 0, 0, 1); return; }
    color = vec4(texture(src, q).rgb, 1.0);
}
"""


def even(x: float) -> int:
    """Encoders want even sizes for 4:2:0 color."""
    return max(2, int(x / 2 + 0.5) * 2)


class FrameGrabber:
    """Frames of one size, as RGB bytes (width × height × 3, top row first)."""

    def __init__(self, gl: GL, size: tuple[int, int]):
        self.gl = gl
        self.size = (even(size[0]), even(size[1]))
        self.target = gl.target(self.size, dtype="f1", components=3)
        self.prog = gl.program(GRAB_FS)
        n = self.size[0] * self.size[1] * 3
        self.pbos = [gl.ctx.buffer(reserve=n), gl.ctx.buffer(reserve=n)]
        self.i = 0
        self.pending = False

    def grab(self, tex: moderngl.Texture) -> bytes | None:
        """Start reading this frame; return the previous one (None on the first call)."""
        ctx = self.gl.ctx
        w, h = self.size
        img_a, frame_a = tex.width / tex.height, w / h
        scale = (img_a / frame_a, 1.0) if img_a < frame_a else (1.0, frame_a / img_a)
        self.target.use()
        ctx.viewport = (0, 0, w, h)
        ctx.disable(moderngl.BLEND)
        self.gl.draw_quad(self.prog, src=tex, scale=scale)
        prev = self.pbos[1 - self.i].read() if self.pending else None
        self.target.tex.read_into(self.pbos[self.i], alignment=1)
        self.i, self.pending = 1 - self.i, True
        return prev

    def release(self):
        self.target.release()
        for b in self.pbos:
            b.release()
