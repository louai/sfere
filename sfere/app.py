"""The sfere application: output window + control window, main loop, hotkeys (PLAN.md §2, §5.3, §5.4)."""
from __future__ import annotations

import contextlib
import time
from pathlib import Path

import cv2
import moderngl
import numpy as np

from . import settings as settings_mod
from .compat import load_gui
from .engine import Engine
from .render.gl import GL, attach_current_context
from .scenes import PRESETS, SceneManager
from .touch import touch_mode, touchscreen_present, wants_xwayland

OUTPUT_FS = """#version 330
uniform sampler2D src;
uniform vec2 scale;      // letterbox: fraction of the window the image covers
uniform int debug;
uniform vec3 ball;       // u, v, visible
uniform float beat;
in vec2 uv;
out vec4 color;
void main() {
    vec2 q = (uv - 0.5) / scale + 0.5;
    if (q.x < 0.0 || q.x > 1.0 || q.y < 0.0 || q.y > 1.0) { color = vec4(0, 0, 0, 1); return; }
    vec3 c = texture(src, q).rgb;
    if (debug == 1) {
        vec2 d = abs(q - ball.xy) * vec2(textureSize(src, 0));
        if (ball.z > 0.0 && ((d.x < 1.5 && d.y < 18.0) || (d.y < 1.5 && d.x < 18.0))) c = vec3(0, 1, 0);
        if (q.x < 0.03 && q.y > 0.95) c = mix(c, vec3(1, 1, 0), beat);
    }
    color = vec4(c, 1.0);
}
"""


class App:
    def __init__(self, args):
        self.args = args
        self.settings = settings_mod.load()
        if args.scene:
            self.settings["scene"] = args.scene
        touch = getattr(args, "touch", None) or self.settings["ui"].get("touch", "auto")
        self.touchscreen = touchscreen_present()
        self.touch = touch_mode(touch, self.touchscreen)
        self.x11 = args.x11 or wants_xwayland(touch, self.touchscreen)
        self.glfw, self.imgui = load_gui(force_x11=self.x11)
        glfw = self.glfw
        if not glfw.init():
            raise SystemExit("GLFW could not initialise a display")
        for hint, v in ((glfw.CONTEXT_VERSION_MAJOR, 3), (glfw.CONTEXT_VERSION_MINOR, 3),
                        (glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE), (glfw.OPENGL_FORWARD_COMPAT, True)):
            glfw.window_hint(hint, v)
        glfw.window_hint(glfw.SCALE_TO_MONITOR, True)
        # GLFW minimizes a fullscreen window when it loses focus, so clicking the control
        # window would hide the show
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        # matches sfere.desktop, so the shell groups both windows under the app's icon
        glfw.window_hint_string(glfw.WAYLAND_APP_ID, "sfere")
        glfw.window_hint_string(glfw.X11_CLASS_NAME, "sfere")
        glfw.window_hint_string(glfw.X11_INSTANCE_NAME, "sfere")
        self.out_win = glfw.create_window(1280, 720, "sfere — output", None, None)
        if not self.out_win:
            raise SystemExit("Could not open the output window")
        self.ctl_win = glfw.create_window(1180, 860, "sfere — control", None, self.out_win)
        glfw.make_context_current(self.out_win)
        self.ctx = attach_current_context()
        glfw.swap_interval(0)    # Wayland composites tear-free; pacing is ours
        self.gl = GL(self.ctx)
        self.fullscreen_monitor = None
        self.p_out = self.gl.program(OUTPUT_FS)
        self.render_size = self._native_render_size()
        edited = self.settings.get("scene_layers", {})
        presets = [{**p, "layers": edited[p["name"]]} if p["name"] in edited else p for p in PRESETS]
        self.scenes = SceneManager(self.gl, self.render_size, presets)
        self.scenes.gl_current = self.output_context
        out = self.settings["output"]
        self.scenes.aspect_mode = out.get("aspect", "fit")
        self.scenes.flip = (-1.0 if out.get("flip_x") else 1.0, -1.0 if out.get("flip_y") else 1.0)
        if self.settings.get("scene") in self.scenes.names:
            self.scenes.select(self.settings["scene"], immediate=True)
        glfw.make_context_current(self.ctl_win)
        self.ctx_ui = attach_current_context()
        glfw.swap_interval(0)
        from .ui_backend import ImguiGlfw
        self.ui = ImguiGlfw(glfw, self.imgui, self.ctl_win, self.ctx_ui)
        self.ui.key_callback_chain = self._on_key
        glfw.set_key_callback(self.out_win, self._on_key)
        glfw.set_framebuffer_size_callback(self.out_win, lambda w, a, b: setattr(self, "_resize_at", time.monotonic()))
        self._resize_at = None
        self.cam_tex = self.ctx_ui.texture((320, 180), 3)
        self.cam_tex_id = self.ui.register(self.cam_tex)
        self._preview = {}       # glo -> external texture in the UI context
        self.debug = False
        self.freeze = False
        self.windowed_size = (1280, 720)
        self.last_tex = None
        self.taps: list[float] = []
        self._ui_shot = None
        self.frame_ms = 0.0
        self.fps = 0.0
        self._deadline = 0.0
        self.engine = Engine(self.settings, video=args.video, audio_file=args.audio_file,
                             audio_sources=args.audio, play_audio=not args.mute)
        self.scenes.cam_aspect = self.settings["camera"]["width"] / self.settings["camera"]["height"]
        from . import ui
        self.panels = ui.Panels(self)
        self._monitors_changed = False
        glfw.set_monitor_callback(lambda m, event: setattr(self, "_monitors_changed", True))
        # Applied once the window is on screen: asked for earlier, GNOME puts it fullscreen
        # on the display it first appeared on, whatever monitor was requested.
        names = [self.monitor_name(m) for m in self.monitors()]
        self._start_fullscreen = None
        if out.get("mode") == "fullscreen" and out.get("monitor") in names:
            self._start_fullscreen = out["monitor"]
        elif out.get("start_on_external", True) and self.external_monitors():
            self._start_fullscreen = self.preferred_monitor()
        self._frame_count = 0

    @contextlib.contextmanager
    def output_context(self):
        """Make the output window's GL context current, then put back whichever was."""
        g = self.glfw
        prev = g.get_current_context()
        g.make_context_current(self.out_win)
        try:
            yield
        finally:
            if prev:
                g.make_context_current(prev)

    # ------------------------------------------------------------------ windows
    def monitors(self) -> list:
        return list(self.glfw.get_monitors() or [])

    def monitor_name(self, m) -> str:
        n = self.glfw.get_monitor_name(m)
        return n.decode() if isinstance(n, bytes) else str(n)

    def monitor_label(self, m) -> str:
        mode = self.glfw.get_video_mode(m)
        return f"{self.monitor_name(m)}  {mode.size.width}×{mode.size.height} @{mode.refresh_rate} Hz"

    def set_fullscreen(self, name: str | None) -> None:
        glfw = self.glfw
        if name is None:
            if self.fullscreen_monitor is not None:
                w, h = self.windowed_size
                glfw.set_window_monitor(self.out_win, None, 100, 100, w, h, 0)
            self.fullscreen_monitor = None
            self.settings["output"]["mode"] = "window"
            return
        mon = next((m for m in self.monitors() if self.monitor_name(m) == name), None)
        if mon is None:
            return
        if self.fullscreen_monitor is None:
            self.windowed_size = tuple(glfw.get_window_size(self.out_win))
        mode = glfw.get_video_mode(mon)
        glfw.set_window_monitor(self.out_win, mon, 0, 0, mode.size.width, mode.size.height, mode.refresh_rate)
        self.fullscreen_monitor = name
        self.settings["output"].update(mode="fullscreen", monitor=name)

    def external_monitors(self) -> list[str]:
        """Connected displays other than a laptop's built-in panel. GLFW's monitor order is the
        compositor's, so it says nothing about which is external; the connector name does."""
        return [n for n in map(self.monitor_name, self.monitors()) if not n.startswith(("eDP", "LVDS", "DSI"))]

    def preferred_monitor(self) -> str | None:
        """Where the output goes fullscreen by default: an external display (projector) whenever
        one is connected, the saved one if there are several; the built-in panel only when it's
        the only screen."""
        names = [self.monitor_name(m) for m in self.monitors()]
        want = self.settings["output"].get("monitor")
        pool = self.external_monitors() or names
        return want if want in pool else (pool[0] if pool else None)

    def _handle_monitor_change(self) -> None:
        """Hot-plug: a newly connected external display takes the output; if the display the
        output was fullscreen on goes away, fall back to a window."""
        self._monitors_changed = False
        names = [self.monitor_name(m) for m in self.monitors()]
        if self.fullscreen_monitor and self.fullscreen_monitor not in names:
            self.set_fullscreen(None)
        if not self.fullscreen_monitor and self.settings["output"].get("start_on_external", True) \
                and self.external_monitors():
            self.set_fullscreen(self.preferred_monitor())

    def next_monitor(self) -> None:
        mons = [self.monitor_name(m) for m in self.monitors()]
        if not mons:
            return
        i = (mons.index(self.fullscreen_monitor) + 1) % len(mons) if self.fullscreen_monitor in mons else 0
        self.set_fullscreen(mons[i])

    def display_fps(self) -> float:
        mon = self.glfw.get_window_monitor(self.out_win)
        if not mon:
            mons = self.monitors()
            mon = mons[0] if mons else None
        return float(self.glfw.get_video_mode(mon).refresh_rate) if mon else 60.0

    def target_fps(self) -> float:
        """The output frame rate: the display's refresh rate, or the Output tab's limit below it
        (frames faster than the display are never shown)."""
        limit = float(self.settings["output"].get("fps_limit") or 0)
        display = self.display_fps()
        return min(limit, display) if limit > 0 else display

    def _native_render_size(self) -> tuple[int, int]:
        out = self.settings["output"]
        if out.get("fixed_resolution"):
            return int(out["render_width"]), int(out["render_height"])
        fw, fh = self.glfw.get_framebuffer_size(self.out_win)
        s = float(out.get("render_scale", 1.0))
        if self.x11 and self.fullscreen_monitor:
            # XWayland fullscreen is in device pixels; render at the display's scaled resolution
            # like a Wayland fullscreen window, so a 2x display doesn't cost four times the GPU time
            s /= max(1.0, self.glfw.get_window_content_scale(self.out_win)[0])
        return max(64, int(fw * s)), max(64, int(fh * s))

    # ------------------------------------------------------------------ hotkeys
    def _on_key(self, window, key, scancode, action, mods):
        g = self.glfw
        if action != g.PRESS:
            return
        shift = bool(mods & g.MOD_SHIFT)
        if key == g.KEY_F and shift:
            self.next_monitor()
        elif key == g.KEY_F:
            self.toggle_fullscreen()
        elif key == g.KEY_ESCAPE and self.fullscreen_monitor:
            self.set_fullscreen(None)
        elif key == g.KEY_Q and mods & g.MOD_CONTROL:
            g.set_window_should_close(self.out_win, True)
        elif key == g.KEY_D:
            self.debug = not self.debug
        elif key == g.KEY_C:
            self.panels.show_tab("Camera")
        elif key == g.KEY_M:
            self.engine.mirror = not self.engine.mirror
        elif key == g.KEY_B:
            self.toggle_blackout()
        elif key == g.KEY_T:
            self.tap_tempo()
        elif key == g.KEY_SPACE:
            self.freeze = not self.freeze
        elif g.KEY_1 <= key <= g.KEY_9:
            self.select_scene(key - g.KEY_1)

    # ------------------------------------------------------------------ actions (keys and toolbar)
    def toggle_fullscreen(self):
        self.set_fullscreen(None if self.fullscreen_monitor else self.preferred_monitor())

    def toggle_blackout(self):
        self.scenes.blackout_target = 0.0 if self.scenes.blackout_target > 0.5 else 1.0

    def select_scene(self, i: int):
        if 0 <= i < len(self.scenes.names):
            self.scenes.select(i)
            self.settings["scene"] = self.scenes.names[i]

    def tap_tempo(self):
        now = time.monotonic()
        self.taps = [t for t in self.taps if now - t < 3.0] + [now]
        if len(self.taps) >= 3:
            bpm = 60.0 / float(np.median(np.diff(self.taps)))
            a = self.engine.audio.analyser
            a.bpm, a.period, a.locked = bpm, 60.0 / bpm, True
            a.last_beat = a.audio_time - (now - self.taps[-1])

    # ------------------------------------------------------------------ frame
    def preview_id(self, tex: moderngl.Texture) -> int:
        """The output texture as seen from the UI context (shared GL objects)."""
        ext = self._preview.get(tex.glo)
        if ext is None or ext.size != tex.size:
            ext = self.ctx_ui.external_texture(tex.glo, tex.size, 4, 0, "f1")
            self._preview = {tex.glo: ext}
            self.ui.register(ext)
        return ext.glo

    def render_output(self, t: float, dt: float):
        glfw = self.glfw
        glfw.make_context_current(self.out_win)
        if self._resize_at and time.monotonic() - self._resize_at > 0.15:
            self._resize_at = None
            size = self._native_render_size()
            if size != self.scenes.size:
                self.scenes.resize(size)
        ball = self.engine.ball(t)
        audio = self.engine.audio.state(t)
        self.last_ball, self.last_audio = ball, audio
        if not self.freeze or self.last_tex is None:
            self.last_tex = self.scenes.render(t - self.t0, dt, ball, audio)
        fw, fh = glfw.get_framebuffer_size(self.out_win)
        self.ctx.screen.use()
        self.ctx.viewport = (0, 0, fw, fh)
        self.ctx.disable(moderngl.BLEND)
        rw, rh = self.last_tex.size
        win_a, img_a = fw / max(fh, 1), rw / rh
        scale = (img_a / win_a, 1.0) if img_a < win_a else (1.0, win_a / img_a)
        self.gl.draw_quad(self.p_out, src=self.last_tex, scale=scale, debug=int(self.debug),
                          ball=(self._ball_uv[0], self._ball_uv[1], ball.visible), beat=float(audio.beat))
        glfw.swap_buffers(self.out_win)

    @property
    def _ball_uv(self):
        from .scenes import map_ball
        b = self.last_ball
        sb = map_ball(b, self.scenes.size[0] / self.scenes.size[1], self.scenes.cam_aspect, self.scenes.aspect_mode)
        u, v = sb.u, sb.v
        if self.scenes.flip[0] < 0:
            u = 1 - u
        if self.scenes.flip[1] < 0:
            v = 1 - v
        return u, v

    def render_control(self):
        glfw = self.glfw
        glfw.make_context_current(self.ctl_win)
        if self.engine.preview is not None:
            img = self.engine.preview
            if self.panels.show_mask and self.engine.preview_mask is not None:
                img = img.copy()
                img[self.engine.preview_mask > 0] = (0, 255, 0)
            if self.engine.mirror:
                img = img[:, ::-1]
            self.cam_tex.write(np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).tobytes())
        self.ui.new_frame()
        self.panels.draw()
        fw, fh = glfw.get_framebuffer_size(self.ctl_win)
        self.ctx_ui.screen.use()
        self.ctx_ui.viewport = (0, 0, fw, fh)
        self.ctx_ui.clear(0.08, 0.08, 0.1)
        self.ui.render()
        if self._ui_shot is not None:   # read before the swap: the back buffer is undefined after it
            self._ui_shot = np.frombuffer(self.ctx_ui.screen.read(viewport=(0, 0, fw, fh), components=3),
                                          np.uint8).reshape(fh, fw, 3)[::-1].copy()
        glfw.swap_buffers(self.ctl_win)

    def start_clock(self):
        self.t0 = self._last = self._fps_t = time.monotonic()
        self._frames = 0

    def frame(self) -> bool:
        """One iteration: events, output, control, pacing. False when a window was closed."""
        glfw = self.glfw
        if glfw.window_should_close(self.out_win) or glfw.window_should_close(self.ctl_win):
            return False
        glfw.poll_events()
        if self._monitors_changed:
            self._handle_monitor_change()
        self._frame_count += 1
        if self._start_fullscreen and self._frame_count == 3:
            self.set_fullscreen(self._start_fullscreen)
        now = time.monotonic()
        dt = min(0.1, max(1e-4, now - self._last))
        self._last = now
        t_start = time.perf_counter()
        self.render_output(now, dt)
        self.render_control()
        self.frame_ms = (time.perf_counter() - t_start) * 1000
        self._frames += 1
        if now - self._fps_t >= 1.0:
            self.fps = self._frames / (now - self._fps_t)
            self._frames, self._fps_t = 0, now
        # Pace by deadlines so the average rate is exact; after a slow frame, start over from now
        # rather than rushing to catch up.
        self._deadline = max(self._deadline + 1.0 / self.target_fps(), now)
        wait = self._deadline - time.monotonic()
        if wait > 0.001:
            time.sleep(wait - 0.0005)
        return True

    def run(self):
        self.start_clock()
        capture_dir = Path(self.args.capture) if self.args.capture else None
        self._ui_shot = False if capture_dir else None
        if capture_dir:
            capture_dir.mkdir(parents=True, exist_ok=True)
        shots, next_shot = 0, self.t0 + 1.0
        try:
            while self.frame():
                now = time.monotonic()
                if capture_dir and now >= next_shot:
                    self._capture(capture_dir, shots)
                    shots += 1
                    next_shot = now + self.args.capture_every
                if self.args.duration and now - self.t0 > self.args.duration:
                    break
        finally:
            self.close()

    def _capture(self, d: Path, i: int):
        tex = self.last_tex
        img = np.frombuffer(tex.read(), np.uint8).reshape(tex.height, tex.width, 4)[::-1, :, :3]
        cv2.imwrite(str(d / f"output_{i:03d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        if isinstance(self._ui_shot, np.ndarray):
            cv2.imwrite(str(d / f"control_{i:03d}.png"), cv2.cvtColor(self._ui_shot, cv2.COLOR_RGB2BGR))
        self._ui_shot = False   # ask render_control for a fresh one
        a, b = self.last_audio, self.last_ball
        with open(d / "log.txt", "a") as f:
            f.write(f"{i} t={time.monotonic() - self.t0:.1f} fps={self.fps:.1f} frame_ms={self.frame_ms:.2f} "
                    f"scene_ms={self.scenes.last_frame_ms:.2f} track_ms={self.engine.tracker.last_ms:.2f} "
                    f"cam_fps={self.engine.capture.fps:.1f} ball={b.state}({b.x:.2f},{b.y:.2f}) "
                    f"bpm={a.bpm:.1f} locked={a.locked} beats={a.beat_count} render={self.scenes.size} "
                    f"scene={self.scenes.current.name} fullscreen={self.fullscreen_monitor}\n")

    def close(self):
        try:
            s = self.settings
            cfg = self.engine.tracker.config
            s["tracking"].update(hue=cfg.hue, hue_tolerance=cfg.hue_tolerance, min_saturation=cfg.min_saturation,
                                 min_value=cfg.min_value, exclusion=[list(r) for r in cfg.exclusion],
                                 profile=cfg.profile)
            if not self.args.audio_file and self.args.audio is None:
                s["audio"]["sources"] = [k for k, v in self.engine.audio.sources.items() if v.kind != "file"]
                s["audio"]["gains"] = {k: v.gain for k, v in self.engine.audio.sources.items() if v.kind != "file"}
            if self.args.video is None:
                s["camera"]["mirror"] = self.engine.mirror
            s["scene_layers"] = {p["name"]: p["layers"] for p, orig in zip(self.scenes.presets, PRESETS)
                                 if p["layers"] != orig["layers"]}
            s["output"]["aspect"] = self.scenes.aspect_mode
            s["output"]["flip_x"], s["output"]["flip_y"] = self.scenes.flip[0] < 0, self.scenes.flip[1] < 0
            if not self.args.no_save:
                settings_mod.save(s)
        finally:
            self.engine.close()
            self.glfw.terminate()
