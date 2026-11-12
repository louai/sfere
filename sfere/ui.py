"""Control-window panels (Dear ImGui): camera & calibration, audio sources, scenes, output, debug, help.

Touch mode (sfere/touch.py) scales every control up, scrolls when a finger is dragged up or
down, and has buttons for everything the keyboard shortcuts do.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .audio.sources import DEFAULT_MONITOR, FileSource, list_sources
from .camera import camera_controls_from_settings
from .fx.builtin import BUILTIN
from .fx.shaders import ShaderEffect
from .render.post import DEFAULT_POST
from .scenes import BIND_SOURCES, Missing
from .touch import touch_mode, wants_xwayland

TABS = ["Camera", "Audio", "Scenes", "Output", "Debug", "Help"]
GUIDE = Path(__file__).resolve().parents[1] / "docs" / "guide.md"
ACCENT = (0.35, 0.25, 0.7, 1)
RECORDING = (0.75, 0.15, 0.15, 1)
# style sizes scaled with the display and touch mode, from ImGui's defaults
SIZES = ("window_padding", "frame_padding", "item_spacing", "item_inner_spacing", "cell_padding", "indent_spacing",
         "scrollbar_size", "grab_min_size", "separator_text_padding", "frame_rounding", "grab_rounding",
         "tab_rounding", "scrollbar_rounding")
TOUCH_SIZES = 1.9   # padding and spacing: a button about 9 mm tall on a 13" panel
TOUCH_FONT = 1.35


class Panels:
    def __init__(self, app):
        self.app = app
        self.imgui = app.imgui
        self.current_tab = None
        self._force = ("Scenes", 2)     # (tab, frames left): ImGui needs the request on consecutive frames
        self.show_mask = True
        self.drag_start = None
        self.sources_cache = []
        self.sources_at = 0.0
        self.calib_msg = ""
        self.file_path = ""
        self.layer_search = ""
        self.ranges = app.engine.controls.ranges() if app.engine.controls else {}
        self.touch = app.touch
        style = self.imgui.get_style()
        self._base = {k: getattr(style, k) for k in SIZES}
        self._base = {k: (self.imgui.ImVec2(v.x, v.y) if hasattr(v, "x") else v) for k, v in self._base.items()}
        self._base_drag = self.imgui.get_io().mouse_drag_threshold
        self._style_key = None
        self.dpi = self.scale = 1.0
        self._gesture = None          # touch: None, "control", or the key of the window being scrolled
        self._pressed_value = None    # touch: (slider label, value before the press)
        self._guide = None
        self.renaming = None          # the new name being typed, while renaming a scene
        self.rename_msg = ""
        self.drag_button = 0

    # ---------------------------------------------------------------- style and touch
    def _apply_style(self):
        """Scale the UI for the display and for fingers. Under XWayland the window is in device
        pixels, so the monitor's scale is applied here; on Wayland the compositor does it."""
        im, glfw, win = self.imgui, self.app.glfw, self.app.ctl_win
        ww, _ = glfw.get_window_size(win)
        fw, _ = glfw.get_framebuffer_size(win)
        cs = glfw.get_window_content_scale(win)[0]
        dpi = max(1.0, round(cs * ww / fw, 2)) if ww > 0 and fw > 0 else self.dpi
        if (dpi, self.touch) == self._style_key:
            return
        self._style_key = (dpi, self.touch)
        k = dpi * (TOUCH_SIZES if self.touch else 1.0)
        style = im.get_style()
        for name, v in self._base.items():
            setattr(style, name, im.ImVec2(v.x * k, v.y * k) if hasattr(v, "x") else v * k)
        style.touch_extra_padding = im.ImVec2(4 * dpi, 4 * dpi) if self.touch else im.ImVec2(0, 0)
        style.font_scale_dpi = dpi
        style.font_scale_main = TOUCH_FONT if self.touch else 1.0
        im.get_io().mouse_drag_threshold = self._base_drag * k
        self.dpi, self.scale = dpi, k

    def px(self, v: float) -> float:
        """A fixed widget size, scaled like the rest of the UI."""
        return v * self.scale

    def _touch_scroll(self, key: str):
        """Touch mode: a finger dragged mostly up or down scrolls this window, even when it
        started on a button or slider (which is then cancelled); a sideways drag goes to the
        control under it. The scroll bar moves with the finger, as if dragging it. Call before
        ending the window."""
        im = self.imgui
        if not self.touch:
            return
        if not im.is_mouse_down(0):
            self._gesture = self._pressed_value = None
            return
        if self._gesture is None and im.is_mouse_dragging(0) \
                and im.is_window_hovered(im.HoveredFlags_.allow_when_blocked_by_active_item):
            d = im.get_mouse_drag_delta(0)
            self._gesture = key if abs(d.y) > 1.5 * abs(d.x) else "control"
            if self._gesture == key and im.is_any_item_active():
                im.internal.clear_active_id()
        if self._gesture == key:
            im.set_scroll_y(im.get_scroll_y() + im.get_io().mouse_delta.y)

    def _slider(self, label, v, lo, hi, fmt=None):
        """slider_float / slider_int. In touch mode a slider jumps to the finger, so if that
        press turns into a scroll, the value it had before comes back."""
        im = self.imgui
        if isinstance(v, int) and isinstance(lo, int):
            ch, nv = im.slider_int(label, v, lo, hi, fmt or "%d")
        else:
            ch, nv = im.slider_float(label, float(v), float(lo), float(hi), fmt or "%.3f")
        if self.touch:
            if im.is_item_activated():
                self._pressed_value = (label, v)
            elif self._pressed_value and self._pressed_value[0] == label and self._gesture not in (None, "control"):
                ch, nv = True, self._pressed_value[1]
                self._pressed_value = None
        return ch, nv

    def _flow_begin(self):
        im = self.imgui
        self._flow_right = im.get_cursor_screen_pos().x + im.get_content_region_avail().x
        self._flow_first = True

    def _button(self, label: str, on: bool = False) -> bool:
        """A button in a row that wraps (after _flow_begin); highlighted when `on`."""
        im = self.imgui
        st = im.get_style()
        w = im.calc_text_size(label, True).x + 2 * st.frame_padding.x
        if not self._flow_first and im.get_item_rect_max().x + st.item_spacing.x + w <= self._flow_right:
            im.same_line()
        self._flow_first = False
        if on:
            im.push_style_color(im.Col_.button, ACCENT)
        pressed = im.button(label)
        if on:
            im.pop_style_color()
        return pressed

    # ---------------------------------------------------------------- layout
    def draw(self):
        im, app = self.imgui, self.app
        self._apply_style()
        io = im.get_io()
        im.set_next_window_pos((0, 0))
        im.set_next_window_size((io.display_size.x, io.display_size.y))
        flags = im.WindowFlags_.no_title_bar | im.WindowFlags_.no_resize | im.WindowFlags_.no_move | im.WindowFlags_.no_collapse
        im.begin("sfere", None, flags)
        a, b = app.last_audio, app.last_ball
        im.text(f"{app.fps:5.1f} fps   frame {app.frame_ms:4.1f} ms   scene: {app.scenes.current.name}   "
                f"ball: {b.state}   tempo: {a.bpm:.1f} BPM{' (locked)' if a.locked else ''}")
        im.same_line()
        im.progress_bar(float(a.beat), (self.px(60), 0), "beat")
        # preview of the output, then the show controls: everything the keys do, as buttons
        avail = im.get_content_region_avail()
        pw = min(420.0 * self.dpi, avail.x * 0.42)
        ph = pw * app.last_tex.height / app.last_tex.width
        im.image(im.ImTextureRef(app.preview_id(app.last_tex)), (pw, ph), (0, 1), (1, 0))
        im.same_line()
        im.begin_group()
        self._flow_begin()
        fs = app.fullscreen_monitor
        if self._button("Leave fullscreen (Esc)" if fs else "Fullscreen (F)", bool(fs)):
            app.toggle_fullscreen()
        if len(app.monitors()) > 1 and self._button("Next display (Shift+F)"):
            app.next_monitor()
        if self._button("Black out (B)", app.scenes.blackout_target > 0.5):
            app.toggle_blackout()
        if self._button("Freeze (Space)", app.freeze):
            app.freeze = not app.freeze
        if self._button("Tap tempo (T)"):
            app.tap_tempo()
        if self._button("Mirror (M)", app.engine.mirror):
            app.engine.mirror = not app.engine.mirror
        cyc = app.cycle
        label = f"Cycle: next in {cyc.remaining(time.monotonic()):.0f} s (A)###cycle" if cyc.enabled \
            else "Cycle scenes (A)###cycle"
        if self._button(label, cyc.enabled):
            app.toggle_cycle()
        rec = app.recording
        label = f"Stop recording {_clock(rec.elapsed)} (R)###record" if rec else "Record (R)###record"
        if rec:
            im.push_style_color(im.Col_.button, RECORDING)
        if self._button(label):
            app.toggle_recording()
        if rec:
            im.pop_style_color()
        im.spacing()
        self._scene_combo()
        for msg in app.scenes.messages[-4:]:
            im.text_colored((1, 0.7, 0.3, 1), msg[:110])
        im.end_group()
        if im.begin_tab_bar("tabs"):
            force, left = self._force or (None, 0)
            for name in TABS:
                flags = im.TabItemFlags_.set_selected if force == name else 0
                opened, _ = im.begin_tab_item(name, None, flags)
                if opened:
                    self.current_tab = name
                    im.begin_child("##body")    # only the tab's contents scroll
                    getattr(self, f"tab_{name.lower()}")()
                    self._touch_scroll("body")
                    im.end_child()
                    im.end_tab_item()
            self._force = (force, left - 1) if left > 1 else None
            im.end_tab_bar()
        im.end()

    def show_tab(self, name: str):
        self._force = (name, 2)

    # ---------------------------------------------------------------- camera
    def tab_camera(self):
        im, app, eng = self.imgui, self.app, self.app.engine
        cfg = eng.tracker.config
        cam = app.settings["camera"]
        im.text(f"Source: {eng.capture.source}   {eng.capture.status}   {eng.capture.fps:.1f} fps   "
                f"tracking {eng.tracker.last_ms:.2f} ms   profile: {eng.tracker.active_profile}")
        w = min(640.0 * self.dpi, im.get_content_region_avail().x)
        h = w * 9 / 16
        p0 = im.get_cursor_screen_pos()
        # a button the size of the view, so a drag on it draws a zone instead of scrolling
        im.invisible_button("##camview", (w, h), im.ButtonFlags_.mouse_button_left | im.ButtonFlags_.mouse_button_right)
        activated = im.is_item_activated()
        mp = im.get_mouse_pos()
        nx, ny = (mp.x - p0.x) / w, (mp.y - p0.y) / h
        dl = im.get_window_draw_list()
        dl.add_image(im.ImTextureRef(app.cam_tex_id), p0, im.ImVec2(p0.x + w, p0.y + h))
        # exclusion zones (drawn in displayed = mirrored coordinates)
        for r in cfg.exclusion:
            x0, y0, x1, y1 = r
            if eng.mirror:
                x0, x1 = 1 - x1, 1 - x0
            dl.add_rect((p0.x + x0 * w, p0.y + y0 * h), (p0.x + x1 * w, p0.y + y1 * h),
                        im.get_color_u32((1, 0.3, 0.3, 1)), 0.0, 2.0)
        # tap or click: calibrate on the ball; drag (either button): exclusion zone
        if activated:
            self.drag_start = (nx, ny)
            self.drag_button = 0 if im.is_mouse_down(0) else 1
            self._gesture = "control"
        if self.drag_start is not None:
            sx, sy = self.drag_start
            io, btn = im.get_io(), self.drag_button
            moved = io.mouse_drag_max_distance_sqr[btn] > io.mouse_drag_threshold ** 2
            if moved:
                dl.add_rect((p0.x + sx * w, p0.y + sy * h), (mp.x, mp.y), im.get_color_u32((1, 1, 0, 1)), 0.0, 1.0)
            if im.is_mouse_released(btn):
                if moved:
                    x0, x1 = sorted((min(max(sx, 0), 1), min(max(nx, 0), 1)))
                    y0, y1 = sorted((min(max(sy, 0), 1), min(max(ny, 0), 1)))
                    if eng.mirror:
                        x0, x1 = 1 - x1, 1 - x0
                    if x1 - x0 > 0.01 and y1 - y0 > 0.01:
                        cfg.exclusion = cfg.exclusion + [[round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]]
                elif btn == 0:
                    res = eng.calibrate_at(sx, sy)
                    self.calib_msg = (f"Calibrated: hue {res['hue']:.0f}°, saturation ≥ {res['min_saturation']:.2f}, "
                                      f"brightness ≥ {res['min_value']:.2f}") if res else "Nothing bright there"
                self.drag_start = None
        im.text_disabled("Tap or click the ball to calibrate its color.  Drag a box over a reflection to exclude it.")
        if self.calib_msg:
            im.text(self.calib_msg)
        _, self.show_mask = im.checkbox("Show detection mask (green)", self.show_mask)
        im.same_line()
        changed, eng.mirror = im.checkbox("Mirror", eng.mirror)
        profiles = ["auto", "dark", "lit"]
        ch, i = im.combo("Tracking profile", profiles.index(cfg.profile), profiles)
        if ch:
            cfg.profile = profiles[i]
            if cfg.profile != "auto":
                eng.tracker.active_profile = cfg.profile
        _, cfg.hue = self._slider("Ball hue (°)", cfg.hue, 0.0, 360.0, "%.0f")
        _, cfg.hue_tolerance = self._slider("Hue tolerance", cfg.hue_tolerance, 3.0, 60.0, "%.0f")
        _, cfg.min_saturation = self._slider("Min saturation", cfg.min_saturation, 0.0, 1.0)
        _, cfg.min_value = self._slider("Min brightness (dark stage)", cfg.min_value, 0.0, 1.0)
        if cfg.exclusion:
            im.text(f"Exclusion zones: {len(cfg.exclusion)}")
            im.same_line()
            if im.button("Clear zones"):
                cfg.exclusion = []
        if eng.controls and eng.controls.available:
            im.separator_text("Camera (V4L2) — restored when sfere exits")
            for key, label in (("exposure", "exposure_time_absolute"), ("gain", "gain")):
                lo, hi = self.ranges.get(label, (0, 255))
                cur = cam.get(key)
                if cur is None:
                    cur = eng.controls.get([label]).get(label, lo)
                ch, v = self._slider(f"{key.capitalize()}", int(cur), int(lo), int(min(hi, 600 if key == "exposure" else hi)))
                if ch:
                    cam[key] = v
                    eng.controls.set({label: v})
            if im.button("Apply dark-stage preset"):
                eng.controls.set(camera_controls_from_settings(cam))

    # ---------------------------------------------------------------- audio
    def tab_audio(self):
        im, eng = self.imgui, self.app.engine.audio
        if time.monotonic() - self.sources_at > 3.0:
            self.sources_cache = list_sources()
            self.sources_at = time.monotonic()
        a = self.app.last_audio
        im.text(f"Tempo {a.bpm:.1f} BPM {'(locked)' if a.locked else '(listening…)'}   beats {a.beat_count}")
        for label, v in (("sub", a.sub), ("bass", a.bass), ("mid", a.mid), ("high", a.high)):
            im.progress_bar(float(v), (self.px(140), 0), label)
            im.same_line()
        im.new_line()
        im.plot_histogram("##spectrum", a.spectrum, graph_size=(min(self.px(560), im.get_content_region_avail().x), self.px(60)),
                          scale_min=0, scale_max=1)
        for group, kind in (("Audio from other apps (system output monitors)", "system"), ("Inputs (mics, line-in)", "input")):
            im.separator_text(group)
            for s in [x for x in self.sources_cache if x.kind == kind]:
                on = s.id in eng.sources
                ch, want = im.checkbox(f"{s.name}##{s.id}", on)
                if ch:
                    if want:
                        eng.enable(s.id, 1.0, s.name, kind)
                    else:
                        eng.disable(s.id)
                if on and s.id in eng.sources:
                    src = eng.sources[s.id]
                    im.same_line()
                    im.progress_bar(max(0.0, (src.peak_db + 60) / 60), (self.px(90), 0), f"{src.peak_db:.0f} dB")
                    im.same_line()
                    im.set_next_item_width(self.px(120))
                    _, src.gain = self._slider(f"gain##{s.id}", src.gain, 0.0, 4.0, "%.2f")
                    if not src.status.startswith("running"):
                        im.same_line()
                        im.text_colored((1, 0.6, 0.3, 1), src.status)
        im.separator_text("File player (development)")
        _, self.file_path = im.input_text("Audio file", self.file_path)
        im.same_line()
        if im.button("Play"):
            eng.play_file(self.file_path)
        files = [s for s in eng.sources.values() if isinstance(s, FileSource)]
        for fsrc in files:
            im.text(f"{fsrc.name}: {fsrc.status}")
            im.same_line()
            if im.button("Stop##file"):
                eng.disable(fsrc.id)
        im.separator_text("Timing")
        an = self.app.engine.audio.analyser
        ch, ms = self._slider("Latency offset (ms)", an.latency_s * 1000, -200.0, 200.0, "%.0f")
        if ch:
            an.latency_s = ms / 1000
            self.app.settings["audio"]["latency_ms"] = ms
        if im.button("Tap tempo (T)"):
            self.app.tap_tempo()

    # ---------------------------------------------------------------- scenes
    def _scene_combo(self):
        """The scene dropdown: numbers are the keys that select a scene."""
        im, mgr = self.imgui, self.app.scenes
        labels = [f"{i + 1}  {n}" if i < 9 else f"    {n}" for i, n in enumerate(mgr.names)]
        im.set_next_item_width(min(self.px(300), im.get_content_region_avail().x * 0.7))
        if im.begin_combo("Scene", labels[mgr.index], im.ComboFlags_.height_large):
            for i, label in enumerate(labels):
                if im.selectable(label, i == mgr.index)[0]:
                    self.app.select_scene(i)
            self._touch_scroll("scenes")
            im.end_combo()

    def _bind_button(self, scene, target: str, name: str, lo: float, hi: float, value: float):
        """♪ before a setting: drive it from the music or the ball's motion. Highlighted when
        bound. Returns the binding, if any."""
        im = self.imgui
        b = scene.binding(target)
        popup = f"bind##{target}"
        if b:
            im.push_style_color(im.Col_.button, ACCENT)
        if im.button(f"♪##{target}"):
            im.open_popup(popup)
        if b:
            im.pop_style_color()
        if im.is_item_hovered():
            im.set_tooltip("Drive this setting from the music or the ball")
        im.same_line()
        if im.begin_popup(popup):
            im.text(f"Drive {name} from")
            sources = [src for src, _ in BIND_SOURCES]
            labels = ["Nothing: set by hand"] + [label for _, label in BIND_SOURCES]
            cur = sources.index(b.source) + 1 if b and b.source in sources else 0
            ch, i = im.combo(f"##source{target}", cur, labels)
            if ch:
                if i == 0:
                    scene.set_binding(target, None)
                else:      # a new binding starts at the current value and reaches halfway to the top
                    start = b.lo if b else value
                    end = b.hi if b else min(hi, value + (hi - lo) * 0.5)
                    scene.set_binding(target, sources[i - 1], start, end, b.smooth if b else 0.15)
                b = scene.binding(target)
            if b:
                _, b.lo = self._slider(f"When quiet / still##{target}", b.lo, float(lo), float(hi))
                _, b.hi = self._slider(f"When loud / fast##{target}", b.hi, float(lo), float(hi))
                _, b.smooth = self._slider(f"Smoothing (s)##{target}", b.smooth, 0.0, 1.0, "%.2f")
                im.text_disabled(f"Now {b.value or 0:.2f}")
            if im.button("Done"):
                im.close_current_popup()
            im.end_popup()
        return b

    def _bind_spacer(self):
        """Keeps settings that can't be bound lined up with those that can."""
        im = self.imgui
        st = im.get_style()
        im.dummy((im.calc_text_size("♪").x + 2 * st.frame_padding.x, im.get_frame_height()))
        im.same_line()

    def tab_scenes(self):
        im, app, mgr = self.imgui, self.app, self.app.scenes
        self._scene_library()
        scene = mgr.current
        im.separator_text("Look")
        _, mgr.fade_time = self._slider("Crossfade (s)", mgr.fade_time, 0.1, 8.0)
        self._cycle_settings()
        im.text("Palette")
        for ci, c in enumerate(scene.palette):
            im.same_line()
            ch, nc = im.color_edit3(f"##palette{ci}", list(c[:3]), im.ColorEditFlags_.no_inputs)
            if ch:
                scene.palette[ci] = tuple(nc)
        im.text_disabled("Effects take their colors from the palette.")
        ranges = {"decay": (0, 0.995), "zoom": (0.97, 1.03), "rotate": (-1, 1), "hue_shift": (-1, 1),
                  "kaleido": (0, 12), "kaleido_spin": (-1, 1), "exposure": (0.2, 3), "bloom": (0, 3)}
        for k in ("decay", "bloom", "bloom_threshold", "exposure", "vignette", "zoom", "rotate", "hue_shift",
                  "kaleido", "kaleido_spin"):
            lo, hi = ranges.get(k, (0, 1))
            v = float(scene.post.get(k, DEFAULT_POST[k]))
            b = self._bind_button(scene, f"post.{k}", k, lo, hi, v)
            if b:
                im.text(f"{k}: {v:.2f}  ← {dict(BIND_SOURCES).get(b.source, b.source)}")
            else:
                _, scene.post[k] = self._slider(f"{k}##post", v, float(lo), float(hi))
        self._layers(mgr, scene)
        self._add_layer(mgr)

    def _cycle_settings(self):
        im, app = self.imgui, self.app
        cfg, cyc = app.settings["cycle"], app.cycle
        im.separator_text("Cycle")
        ch, on = im.checkbox("Cycle through the scenes (A)", cyc.enabled)
        if ch:
            cfg["enabled"] = on
        if cyc.enabled:
            im.same_line()
            im.text_disabled(f"next scene in {cyc.remaining(time.monotonic()):.0f} s")
        steps = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600]
        cur = int(cfg.get("seconds", 30))
        i = min(range(len(steps)), key=lambda k: abs(steps[k] - cur))
        ch, i = self._slider("Every##cycle", i, 0, len(steps) - 1,
                             f"{steps[i] // 60} min {steps[i] % 60:02d} s" if steps[i] >= 60 else f"{steps[i]} s")
        if ch:
            cfg["seconds"] = steps[i]
        orders = ["in order", "shuffle"]
        ch, k = im.combo("Order##cycle", orders.index(cfg.get("order", "in order")) if cfg.get("order") in orders else 0,
                         ["In list order", "Shuffle (never the same scene twice in a row)"])
        if ch:
            cfg["order"] = orders[k]
        _, cfg["on_beat"] = im.checkbox("Change on the beat (when the tempo is locked)", bool(cfg.get("on_beat", True)))
        im.text_disabled("Changing scene by hand starts the countdown again.")

    def _scene_library(self):
        im, app, mgr = self.imgui, self.app, self.app.scenes
        im.separator_text("Scene")
        if self.renaming is not None:
            im.set_next_item_width(self.px(260))
            enter, self.renaming = im.input_text("New name", self.renaming, im.InputTextFlags_.enter_returns_true)
            im.same_line()
            if im.button("Save name") or enter:
                if mgr.rename(self.renaming):
                    self.renaming = None
                    app.settings["scene"] = mgr.current.name
                    app.save_scenes()
                else:
                    self.rename_msg = "That name is taken or empty."
            im.same_line()
            if im.button("Cancel"):
                self.renaming = None
            if self.renaming is not None and self.rename_msg:
                im.text_colored((1, 0.6, 0.3, 1), self.rename_msg)
            return
        i, n = mgr.index, len(mgr.names)
        self._flow_begin()
        if self._button("New"):
            mgr.new_scene()
            self._library_changed()
        if self._button("Duplicate"):
            mgr.new_scene(copy_current=True)
            self._library_changed()
        if self._button("Rename"):
            self.renaming, self.rename_msg = mgr.current.name, ""
        im.begin_disabled(i == 0)
        if self._button("Move earlier"):
            mgr.move(-1)
            self._library_changed()
        im.end_disabled()
        im.begin_disabled(i == n - 1)
        if self._button("Move later"):
            mgr.move(1)
            self._library_changed()
        im.end_disabled()
        if mgr.preset_of_current() and self._button("Reset to preset"):
            im.open_popup("Reset scene?")
        im.begin_disabled(n < 2)
        if self._button("Delete"):
            im.open_popup("Delete scene?")
        im.end_disabled()
        im.text_disabled(f"Scene {i + 1} of {n}.  The first nine are on the number keys.")
        flags = im.WindowFlags_.always_auto_resize
        if im.begin_popup_modal("Delete scene?", None, flags)[0]:
            im.text(f"Delete {mgr.current.name}? This can't be undone.")
            if im.button("Delete scene"):
                mgr.delete()
                self._library_changed()
                im.close_current_popup()
            im.same_line()
            if im.button("Keep it"):
                im.close_current_popup()
            im.end_popup()
        if im.begin_popup_modal("Reset scene?", None, flags)[0]:
            im.text(f"Put {mgr.current.name} back to the built-in {mgr.preset_of_current()}?\n"
                    "Its layers, settings and bindings are replaced.")
            if im.button("Reset"):
                mgr.reset_to_preset()
                self._library_changed()
                im.close_current_popup()
            im.same_line()
            if im.button("Cancel##reset"):
                im.close_current_popup()
            im.end_popup()

    def _library_changed(self):
        self.app.settings["scene"] = self.app.scenes.current.name
        self.app.save_scenes()

    def _layers(self, mgr, scene):
        im = self.imgui
        im.separator_text("Layers")
        im.text_disabled("Top layer first. Filters process the layers below them.")
        action, n = None, len(scene.layers)
        for li in reversed(range(n)):
            eff = scene.layers[li]
            key = getattr(eff, "key", eff.name)
            label = f"{li + 1}. {key}{'   (off)' if not eff.enabled else ''}"
            if not im.collapsing_header(f"{label}###layer{li}"):
                continue
            im.push_id(f"layer{li}")
            if not isinstance(eff, Missing):
                _, eff.enabled = im.checkbox("On", eff.enabled)
                im.same_line()
            im.begin_disabled(li == n - 1)
            if im.button("Up"):
                action = (mgr.move_layer, li, 1)
            im.end_disabled()
            im.same_line()
            im.begin_disabled(li == 0)
            if im.button("Down"):
                action = (mgr.move_layer, li, -1)
            im.end_disabled()
            im.same_line()
            if im.button("Duplicate"):
                action = (mgr.duplicate_layer, li)
            im.same_line()
            if im.button("Reset"):
                action = (mgr.reset_layer, li)
            if im.is_item_hovered():
                im.set_tooltip("Default settings, no bindings")
            im.same_line()
            if im.button("Remove"):
                action = (mgr.remove_layer, li)
            im.pop_id()
            if isinstance(eff, Missing):
                im.text_colored((1, 0.6, 0.3, 1), f"{key} isn't available. The layer keeps its settings "
                                "and works again once the effect is back.")
                continue
            if isinstance(eff, ShaderEffect):
                im.text_disabled(eff.license_line())
                if eff.error:
                    im.text_colored((1, 0.4, 0.4, 1), eff.error[:400])
                if eff.unsupported:
                    im.text_colored((1, 0.7, 0.3, 1), "Unsupported: " + ", ".join(eff.unsupported))
            self._params(scene, li, eff)
        if action:
            fn, *args = action
            fn(*args)

    def _params(self, scene, li, eff):
        im = self.imgui
        for k, p in eff.params.items():
            v = eff.values[k]
            tag = f"{k}##{li}"
            if p.kind == "float":
                b = self._bind_button(scene, f"{li}:{k}", k, p.lo, p.hi, float(v))
                if b:
                    im.text(f"{k}: {float(v):.2f}  ← {dict(BIND_SOURCES).get(b.source, b.source)}")
                else:
                    _, eff.values[k] = self._slider(tag, float(v), float(p.lo), float(p.hi))
            else:
                self._bind_spacer()
                if p.kind == "bool":
                    _, nv = im.checkbox(tag, bool(v))
                    eff.values[k] = float(nv)
                elif p.kind == "int":
                    _, eff.values[k] = self._slider(tag, int(v), int(p.lo), int(p.hi))
                elif p.kind == "point":
                    im.text(f"{k}: follows the ball")
                elif p.kind == "color":
                    _, c = im.color_edit3(tag, list(v[:3]))
                    eff.values[k] = (*c, 1.0)
                else:
                    _, eff.values[k] = self._slider(tag, float(v), float(p.lo), float(p.hi))
            if p.help and im.is_item_hovered():
                im.set_tooltip(p.help)

    def _add_layer(self, mgr):
        im = self.imgui
        im.separator_text("Add a layer")
        _, self.layer_search = im.input_text("Search##addlayer", self.layer_search)
        q = self.layer_search.lower()
        keys = [k for k in sorted(BUILTIN) + sorted(mgr.shader_index) if q in k.lower()]
        im.text_disabled(f"{len(keys)} effects. Tap or click one to add it on top.")
        if im.begin_list_box("##addlist", (-1, self.px(200))):
            for k in keys:
                if im.selectable(k, False)[0]:
                    mgr.add_layer(k)
            self._touch_scroll("addlist")
            im.end_list_box()

    # ---------------------------------------------------------------- output
    def tab_output(self):
        im, app = self.imgui, self.app
        mons = app.monitors()
        im.text(f"Output window: {'fullscreen on ' + app.fullscreen_monitor if app.fullscreen_monitor else 'window'}"
                f"   render {app.scenes.size[0]}×{app.scenes.size[1]}")
        for m in mons:
            name = app.monitor_name(m)
            if im.button(f"Fullscreen on {app.monitor_label(m)}"):
                app.set_fullscreen(name)
        if app.fullscreen_monitor and im.button("Back to a window (Esc)"):
            app.set_fullscreen(None)
        out = app.settings["output"]
        display = app.display_fps()
        rates = [0] + [r for r in (24, 25, 30, 48, 50, 60, 72, 90, 120, 144, 165, 240) if r < display]
        labels = [f"Match display ({display:g} Hz)"] + [f"{r} fps" for r in rates[1:]]
        cur = int(out.get("fps_limit") or 0)
        ch, i = im.combo("Frame rate", rates.index(cur) if cur in rates else 0, labels)
        if ch:
            out["fps_limit"] = rates[i]
        im.same_line()
        im.text_disabled(f"now {app.fps:.1f} fps")
        if im.is_item_hovered():
            im.set_tooltip("Lower rates save GPU and power. Trails and motion look the same at any rate;\n"
                           "some downloaded shaders with feedback move slower at lower rates.")
        _, out["start_on_external"] = im.checkbox("Open the output on an external display (at start and when one is plugged in)",
                                                  bool(out.get("start_on_external", True)))
        ch, out["render_scale"] = self._slider("Render scale", float(out.get("render_scale", 1.0)), 0.5, 1.0)
        if ch:
            app._resize_at = time.monotonic()
        modes = ["fit", "fill", "stretch"]
        ch, i = im.combo("Aspect (camera → output)", modes.index(app.scenes.aspect_mode), modes)
        if ch:
            app.scenes.aspect_mode = modes[i]
        fx, fy = app.scenes.flip
        _, a = im.checkbox("Flip horizontally (rear projection)", fx < 0)
        _, b = im.checkbox("Flip vertically", fy < 0)
        app.scenes.flip = (-1.0 if a else 1.0, -1.0 if b else 1.0)
        bo = app.scenes.blackout_target > 0.5
        ch, bo = im.checkbox("Black out (B)", bo)
        if ch:
            app.scenes.blackout_target = 1.0 if bo else 0.0
        _, app.debug = im.checkbox("Debug marker on output (D)", app.debug)
        self._recording_settings()
        self._virtual_camera_settings()
        self._touch_settings()

    def _recording_settings(self):
        im, app = self.imgui, self.app
        cfg = app.settings["recording"]
        rec = app.recording
        im.separator_text("Recording")
        im.begin_disabled(rec is not None)
        _, cfg["output"] = im.checkbox("The output, with the music", bool(cfg.get("output", True)))
        _, cfg["camera"] = im.checkbox("The camera, and the music as a WAV file", bool(cfg.get("camera", True)))
        if im.is_item_hovered():
            im.set_tooltip("Replay a recorded session with --video <camera file> --audio-file <WAV file>")
        layouts = ["separate", "stacked"]
        both = bool(cfg.get("output", True)) and bool(cfg.get("camera", True))
        im.begin_disabled(not both)
        cur = cfg.get("layout", "separate")
        ch, i = im.combo("Files", layouts.index(cur) if cur in layouts else 0,
                         ["Separate videos (the camera and the WAV replay a session)",
                          "One video: the output on top, the camera below"])
        if ch:
            cfg["layout"] = layouts[i]
        im.end_disabled()
        if im.is_item_hovered(im.HoveredFlags_.allow_when_disabled):
            im.set_tooltip("Stacking needs both the output and the camera. It is made when the recording stops.")
        heights = [720, 1080, 1440, 0]
        w, h = app.scenes.size
        labels = [f"{x}p" for x in heights[:-1]] + [f"Full render ({w}×{h})"]
        cur = int(cfg.get("height", 1080))
        ch, i = im.combo("Output size", heights.index(cur) if cur in heights else 1, labels)
        if ch:
            cfg["height"] = heights[i]
        rates = [24, 25, 30, 50, 60]
        cur = int(cfg.get("fps", 30))
        ch, i = im.combo("Output frame rate", rates.index(cur) if cur in rates else 2, [f"{r} fps" for r in rates])
        if ch:
            cfg["fps"] = rates[i]
        _, cfg["folder"] = im.input_text("Folder", cfg.get("folder", "~/Videos/sfere"))
        im.end_disabled()
        im.same_line()
        if im.button("Open folder"):
            folder = Path(cfg.get("folder") or "~/Videos/sfere").expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rec:
            im.push_style_color(im.Col_.button, RECORDING)
            if im.button(f"Stop recording ({_clock(rec.elapsed)})"):
                app.stop_recording()
            im.pop_style_color()
            im.same_line()
            im.text_disabled(rec.status())
        elif im.button("Start recording (R)"):
            app.start_recording()
        if any(t.is_alive() for t in app.finishing):
            im.text_disabled("Finishing the last recording…")
        for line in app.record_log[-4:]:
            (im.text_disabled if line.startswith("Saved") else lambda t: im.text_colored((1, 0.6, 0.3, 1), t))(line)

    def _virtual_camera_settings(self):
        im, app = self.imgui, self.app
        from .outputs.vcam import NAME, setup_hint
        cfg = app.settings["virtual_camera"]
        im.separator_text("Virtual camera")
        ch, on = im.checkbox(f"Send the output to other apps as a camera named “{NAME}”", bool(cfg.get("enabled")))
        if ch:
            app.set_virtual_camera(on)
        sizes = [(640, 360), (1280, 720), (1920, 1080)]
        cur = (int(cfg.get("width", 1280)), int(cfg.get("height", 720)))
        ch, i = im.combo("Camera size", sizes.index(cur) if cur in sizes else 1, [f"{w}×{h}" for w, h in sizes])
        if ch:
            cfg["width"], cfg["height"] = sizes[i]
            if app.vcam:
                app.set_virtual_camera(True)
        if app.vcam_error:
            im.text_colored((1, 0.6, 0.3, 1), app.vcam_error)
        elif app.vcam:
            state = "running" if app.vcam.alive else f"stopped: {app.vcam.pipe.error or app.vcam.pipe.last_log()}"
            im.text_disabled(f"{app.vcam.where}, {app.vcam.size[0]}×{app.vcam.size[1]} at {app.vcam.fps} fps: {state}")
        if (app.vcam is None or app.vcam.backend == "pipewire") and (cfg.get("enabled") or app.vcam_error):
            im.text_disabled("OBS 30+ (Video Capture Device, PipeWire), Firefox and Chrome see PipeWire cameras.\n"
                             + setup_hint())

    def _touch_settings(self):
        im, app = self.imgui, self.app
        im.separator_text("Control window")
        modes = ["auto", "on", "off"]
        labels = ["Automatic (on when a touch screen is connected)", "On", "Off"]
        cur = app.settings["ui"].get("touch", "auto")
        ch, i = im.combo("Touch-friendly controls", modes.index(cur) if cur in modes else 0, labels)
        if ch:
            app.settings["ui"]["touch"] = modes[i]
            self.touch = app.touch = touch_mode(modes[i], app.touchscreen)
        im.text_disabled(f"Touch screen: {'connected' if app.touchscreen else 'none found'}.   "
                         f"Display: {'XWayland' if app.x11 else 'Wayland'}.")
        if not app.args.x11 and wants_xwayland(app.settings["ui"]["touch"], app.touchscreen) != app.x11:
            im.text_colored((1, 0.7, 0.3, 1), "Restart sfere to apply: touches reach sfere's windows only through XWayland.")

    # ---------------------------------------------------------------- debug
    def tab_debug(self):
        im, app = self.imgui, self.app
        b, a, eng = app.last_ball, app.last_audio, app.engine
        im.text(f"Ball: {b.state}  x {b.x:.3f} y {b.y:.3f} r {b.r:.3f}  speed {b.speed:.2f} accel {b.accel:.2f} "
                f"curvature {b.curvature:.2f}")
        im.text(f"Camera {eng.capture.fps:.1f} fps   tracker {eng.tracker.last_ms:.2f} ms   "
                f"render {app.scenes.last_frame_ms:.2f} ms   frame {app.frame_ms:.2f} ms   {app.fps:.1f} fps")
        im.text(f"Audio: bpm {a.bpm:.2f} locked {a.locked} phase {a.beat_phase:.2f} bar {a.bar} beats {a.beat_count}")
        an = eng.audio.analyser
        im.text(f"Onsets {len(an.onset_times)}  kicks {len(an.kick_times)}  tempo confidence {an.tempo_conf:.2f}")
        for m in app.scenes.messages[-10:]:
            im.text_wrapped(m)

    # ---------------------------------------------------------------- help
    def tab_help(self):
        """docs/guide.md, rendered: headings, paragraphs, lists, tables and code blocks."""
        im = self.imgui
        if self._guide is None:
            try:
                self._guide = parse_markdown(GUIDE.read_text())
            except OSError as e:
                self._guide = [("p", f"The guide is missing ({e}). It lives in docs/guide.md.")]
        base = im.get_style().font_size_base
        for kind, *rest in self._guide:
            if kind == "h":
                level, text = rest
                if level > 1:
                    im.spacing()
                im.push_font(None, base * {1: 1.7, 2: 1.35}.get(level, 1.12))
                (im.text if level < 3 else lambda t: im.text_colored((0.75, 0.65, 1, 1), t))(text)
                im.pop_font()
                if level == 2:
                    im.separator()
            elif kind == "p":
                im.text_wrapped(rest[0])
            elif kind == "li":
                depth, text = rest
                w = im.get_style().indent_spacing * depth
                if w:       # indent(0) would indent by the default amount
                    im.indent(w)
                if not re.match(r"\d+\. ", text):
                    im.bullet()
                im.text_wrapped(text)
                if w:
                    im.unindent(w)
            elif kind == "code":
                for line in rest[0]:
                    im.text_colored((0.6, 0.85, 0.6, 1), "  " + line)
            elif kind == "table":
                rows = rest[0]
                flags = im.TableFlags_.row_bg | im.TableFlags_.borders_inner_h | im.TableFlags_.sizing_stretch_prop
                if rows and im.begin_table(f"##t{id(rows)}", len(rows[0]), flags):
                    for r, row in enumerate(rows):
                        im.table_next_row()
                        for c in range(len(rows[0])):
                            im.table_next_column()
                            cell = row[c] if c < len(row) else ""
                            (im.text_disabled if r == 0 else im.text_wrapped)(cell)
                    im.end_table()
            elif kind == "gap":
                im.spacing()


def _inline(s: str) -> str:
    """Markdown inline markup to plain text: links keep their text, ** and ` go."""
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    return s.replace("**", "").replace("`", "")


def parse_markdown(text: str) -> list[tuple]:
    """The subset of Markdown docs/guide.md uses, as blocks for the Help tab:
    ("h", level, text), ("p", text), ("li", depth, text), ("code", lines), ("table", rows), ("gap",).
    A numbered item keeps its number in the text and is drawn without a bullet."""
    blocks, para, lines = [], [], text.splitlines()

    def flush():
        if para:
            blocks.append(("p", _inline(" ".join(para))))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(("code", code))
        elif stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [_inline(c.strip()) for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        elif m := re.match(r"(#{1,6})\s+(.*)", stripped):
            flush()
            blocks.append(("h", len(m.group(1)), _inline(m.group(2))))
        elif m := re.match(r"(\s*)([-*]|\d+\.)\s+(.*)", line):
            flush()
            number = m.group(2) + " " if m.group(2)[0].isdigit() else ""
            blocks.append(("li", len(m.group(1)) // 2, number + _inline(m.group(3))))
        elif not stripped:
            flush()
            if blocks and blocks[-1][0] == "li":
                blocks.append(("gap",))
        elif blocks and blocks[-1][0] == "li" and not para and line.startswith("  "):
            depth, prev = blocks[-1][1], blocks[-1][2]       # a list item continued on the next line
            blocks[-1] = ("li", depth, prev + " " + _inline(stripped))
        else:
            para.append(stripped)
        i += 1
    flush()
    return blocks


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m:02d}:{s:02d}"
