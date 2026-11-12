"""Dear ImGui (imgui-bundle) on GLFW, rendered with moderngl.

imgui-bundle's own Python renderer needs PyOpenGL; this one uses the control window's
moderngl context instead, so there is one GL wrapper in the process. It follows
ImGui 1.92's dynamic-texture protocol (RendererHasTextures).
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess

import moderngl
import numpy as np

VS = """#version 330
uniform mat4 proj;
in vec2 pos;
in vec2 uv_in;
in vec4 col_in;
out vec2 uv;
out vec4 col;
void main() { uv = uv_in; col = col_in; gl_Position = proj * vec4(pos, 0.0, 1.0); }
"""
FS = """#version 330
uniform sampler2D tex;
in vec2 uv;
in vec4 col;
out vec4 color;
void main() { color = col * texture(tex, uv); }
"""


def _system_font() -> str | None:
    """A sans-serif font file with the dashes, arrows and math signs sfere's labels use, or None."""
    if not shutil.which("fc-match"):
        return None
    try:
        path = subprocess.run(["fc-match", "-f", "%{file}", "sans-serif:style=Regular:charset=2014 2190 2192 2265 2026 266a"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return path if path.endswith((".ttf", ".otf")) else None


class ImguiGlfw:
    def __init__(self, glfw, imgui, window, ctx: moderngl.Context):
        self.glfw, self.imgui, self.window, self.ctx = glfw, imgui, window, ctx
        imgui.create_context()
        self.io = imgui.get_io()
        self.io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
        self.io.set_ini_filename("")
        # the scalable build of the default font stays sharp at touch and HiDPI sizes; a system
        # font fills in the characters it lacks (—, ≥, …)
        self.io.fonts.add_font_default_vector()
        fallback = _system_font()
        if fallback:
            cfg = imgui.ImFontConfig()
            cfg.merge_mode = True
            self.io.fonts.add_font_from_file_ttf(fallback, 0.0, cfg)
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        pio = imgui.get_platform_io()
        mx = ctx.info["GL_MAX_TEXTURE_SIZE"]
        pio.renderer_texture_max_width = mx
        pio.renderer_texture_max_height = mx
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.vbo = ctx.buffer(reserve=1 << 20, dynamic=True)
        self.ibo = ctx.buffer(reserve=1 << 18, dynamic=True)
        self.vao = ctx.vertex_array(self.prog, [(self.vbo, "2f 2f 4f1", "pos", "uv_in", "col_in")],
                                    index_buffer=self.ibo, index_element_size=imgui.INDEX_SIZE)
        self.textures: dict[int, moderngl.Texture] = {}
        self.key_callback_chain = None
        self._time = None
        self._keymap = self._make_keymap()
        glfw.set_key_callback(window, self._on_key)
        glfw.set_char_callback(window, lambda w, c: self.io.add_input_character(c) if 0 < c < 0x10000 else None)
        glfw.set_cursor_pos_callback(window, lambda w, x, y: self.io.add_mouse_pos_event(x, y))
        glfw.set_mouse_button_callback(window, lambda w, b, a, m: self.io.add_mouse_button_event(b, a == glfw.PRESS))
        glfw.set_scroll_callback(window, lambda w, x, y: self.io.add_mouse_wheel_event(x, y))
        glfw.set_cursor_enter_callback(window, lambda w, entered: None if entered else self.io.add_mouse_pos_event(-1, -1))
        pio.platform_get_clipboard_text_fn = lambda _c: (glfw.get_clipboard_string(window) or b"").decode()
        pio.platform_set_clipboard_text_fn = lambda _c, text: glfw.set_clipboard_string(window, text)

    # ------------------------------------------------------------------ input
    def _make_keymap(self):
        g, K = self.glfw, self.imgui.Key
        m = {g.KEY_TAB: K.tab, g.KEY_LEFT: K.left_arrow, g.KEY_RIGHT: K.right_arrow, g.KEY_UP: K.up_arrow,
             g.KEY_DOWN: K.down_arrow, g.KEY_PAGE_UP: K.page_up, g.KEY_PAGE_DOWN: K.page_down, g.KEY_HOME: K.home,
             g.KEY_END: K.end, g.KEY_INSERT: K.insert, g.KEY_DELETE: K.delete, g.KEY_BACKSPACE: K.backspace,
             g.KEY_SPACE: K.space, g.KEY_ENTER: K.enter, g.KEY_ESCAPE: K.escape, g.KEY_KP_ENTER: K.keypad_enter,
             g.KEY_LEFT_CONTROL: K.left_ctrl, g.KEY_RIGHT_CONTROL: K.right_ctrl, g.KEY_LEFT_SHIFT: K.left_shift,
             g.KEY_RIGHT_SHIFT: K.right_shift, g.KEY_LEFT_ALT: K.left_alt, g.KEY_RIGHT_ALT: K.right_alt,
             g.KEY_LEFT_SUPER: K.left_super, g.KEY_RIGHT_SUPER: K.right_super, g.KEY_MINUS: K.minus,
             g.KEY_PERIOD: K.period, g.KEY_COMMA: K.comma, g.KEY_SLASH: K.slash}
        for i in range(26):
            m[g.KEY_A + i] = getattr(K, chr(ord("a") + i))
        for i in range(10):
            m[g.KEY_0 + i] = getattr(K, f"_{i}")
        for i in range(12):
            m[g.KEY_F1 + i] = getattr(K, f"f{i + 1}")
        return m

    def _on_key(self, window, key, scancode, action, mods):
        K = self.imgui.Key
        down = action != self.glfw.RELEASE
        k = self._keymap.get(key)
        if k is not None:
            self.io.add_key_event(k, down)
        self.io.add_key_event(K.mod_ctrl, bool(mods & self.glfw.MOD_CONTROL))
        self.io.add_key_event(K.mod_shift, bool(mods & self.glfw.MOD_SHIFT))
        self.io.add_key_event(K.mod_alt, bool(mods & self.glfw.MOD_ALT))
        self.io.add_key_event(K.mod_super, bool(mods & self.glfw.MOD_SUPER))
        if self.key_callback_chain and not self.io.want_text_input:
            self.key_callback_chain(window, key, scancode, action, mods)

    def new_frame(self):
        g, imgui = self.glfw, self.imgui
        w, h = g.get_window_size(self.window)
        fw, fh = g.get_framebuffer_size(self.window)
        self.io.display_size = imgui.ImVec2(w, h)
        if w > 0 and h > 0:
            self.io.display_framebuffer_scale = imgui.ImVec2(fw / w, fh / h)
        t = g.get_time()
        self.io.delta_time = max(1e-4, (t - self._time) if self._time else 1 / 60)
        self._time = t
        imgui.new_frame()

    # ------------------------------------------------------------------ textures
    def register(self, tex: moderngl.Texture) -> int:
        """Use an existing moderngl texture in imgui.image(); returns its id."""
        self.textures[tex.glo] = tex
        return tex.glo

    def _update_textures(self):
        imgui = self.imgui
        S = imgui.ImTextureStatus
        for t in imgui.get_platform_io().textures:
            if t.status == S.ok:
                continue
            if t.status == S.want_create:
                px = np.asarray(t.get_pixels_array(), np.uint8)
                tex = self.ctx.texture((t.width, t.height), 4, px.tobytes())
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self.textures[tex.glo] = tex
                t.set_tex_id(tex.glo)
                t.status = S.ok
            elif t.status == S.want_updates:
                tex = self.textures[t.tex_id]
                full = np.asarray(t.get_pixels_array(), np.uint8).reshape(t.height, t.width, 4)
                for r in t.updates:
                    sub = np.ascontiguousarray(full[r.y:r.y + r.h, r.x:r.x + r.w])
                    tex.write(sub.tobytes(), viewport=(r.x, r.y, r.w, r.h))
                t.status = S.ok
            elif t.status == S.want_destroy:
                tex = self.textures.pop(t.tex_id, None)
                if tex:
                    tex.release()
                t.set_tex_id(0)
                t.status = S.destroyed

    # ------------------------------------------------------------------ draw
    def render(self):
        imgui, ctx = self.imgui, self.ctx
        imgui.render()
        dd = imgui.get_draw_data()
        self._update_textures()
        dw, dh = self.io.display_size.x, self.io.display_size.y
        sx, sy = self.io.display_framebuffer_scale.x, self.io.display_framebuffer_scale.y
        fbw, fbh = int(dw * sx), int(dh * sy)
        if fbw <= 0 or fbh <= 0:
            return
        dd.scale_clip_rects(self.io.display_framebuffer_scale)
        ctx.screen.use()
        ctx.viewport = (0, 0, fbw, fbh)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        proj = np.array([[2 / dw, 0, 0, 0], [0, -2 / dh, 0, 0], [0, 0, -1, 0], [-1, 1, 0, 1]], "f4")
        self.prog["proj"].write(proj.tobytes())
        self.prog["tex"].value = 0
        vs, isz = imgui.VERTEX_SIZE, imgui.INDEX_SIZE
        for cl in dd.cmd_lists:
            vbytes = cl.vtx_buffer.size() * vs
            ibytes = cl.idx_buffer.size() * isz
            if vbytes > self.vbo.size:
                self.vbo.orphan(vbytes * 2)
            if ibytes > self.ibo.size:
                self.ibo.orphan(ibytes * 2)
            self.vbo.write(ctypes.string_at(cl.vtx_buffer.data_address(), vbytes))
            self.ibo.write(ctypes.string_at(cl.idx_buffer.data_address(), ibytes))
            for cmd in cl.cmd_buffer:
                tex = self.textures.get(cmd.tex_ref.get_tex_id())
                if tex is None:
                    continue
                tex.use(0)
                x, y, z, w = cmd.clip_rect
                ctx.scissor = (int(x), int(fbh - w), int(z - x), int(w - y))
                self.vao.render(moderngl.TRIANGLES, vertices=cmd.elem_count, first=cmd.idx_offset)
        ctx.scissor = None
        ctx.disable(moderngl.BLEND)
