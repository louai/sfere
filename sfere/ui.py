"""Control-window panels (Dear ImGui): camera & calibration, audio sources, scenes, output, debug."""
from __future__ import annotations

import time

from .audio.sources import DEFAULT_MONITOR, FileSource, list_sources
from .camera import camera_controls_from_settings
from .fx.shaders import ShaderEffect

TABS = ["Camera", "Audio", "Scenes", "Output", "Debug"]


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
        self.ranges = app.engine.controls.ranges() if app.engine.controls else {}

    # ---------------------------------------------------------------- layout
    def draw(self):
        im, app = self.imgui, self.app
        io = im.get_io()
        im.set_next_window_pos((0, 0))
        im.set_next_window_size((io.display_size.x, io.display_size.y))
        flags = im.WindowFlags_.no_title_bar | im.WindowFlags_.no_resize | im.WindowFlags_.no_move | im.WindowFlags_.no_collapse
        im.begin("sfere", None, flags)
        a, b = app.last_audio, app.last_ball
        im.text(f"{app.fps:5.1f} fps   frame {app.frame_ms:4.1f} ms   scene: {app.scenes.current.name}   "
                f"ball: {b.state}   tempo: {a.bpm:.1f} BPM{' (locked)' if a.locked else ''}")
        im.same_line()
        im.progress_bar(float(a.beat), (60, 0), "beat")
        # preview of the output, top right
        avail = im.get_content_region_avail()
        pw = min(420.0, avail.x * 0.42)
        ph = pw * app.last_tex.height / app.last_tex.width
        im.image(im.ImTextureRef(app.preview_id(app.last_tex)), (pw, ph), (0, 1), (1, 0))
        im.same_line()
        im.begin_group()
        im.text("Output preview")
        im.text_disabled("Keys: 1-9 scenes  F fullscreen  Shift+F next monitor\n"
                         "D debug  B black out  T tap tempo  M mirror  Space freeze")
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
                    getattr(self, f"tab_{name.lower()}")()
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
        w = 640
        p0 = im.get_cursor_screen_pos()
        im.image(im.ImTextureRef(app.cam_tex_id), (w, w * 9 / 16))
        hovered = im.is_item_hovered()
        mp = im.get_mouse_pos()
        nx, ny = (mp.x - p0.x) / w, (mp.y - p0.y) / (w * 9 / 16)
        dl = im.get_window_draw_list()
        # exclusion zones (drawn in displayed = mirrored coordinates)
        for r in cfg.exclusion:
            x0, y0, x1, y1 = r
            if eng.mirror:
                x0, x1 = 1 - x1, 1 - x0
            dl.add_rect((p0.x + x0 * w, p0.y + y0 * w * 9 / 16), (p0.x + x1 * w, p0.y + y1 * w * 9 / 16),
                        im.get_color_u32((1, 0.3, 0.3, 1)), 0.0, 2.0)
        if hovered and im.is_mouse_clicked(0):
            res = eng.calibrate_at(nx, ny)
            self.calib_msg = (f"Calibrated: hue {res['hue']:.0f}°, saturation ≥ {res['min_saturation']:.2f}, "
                              f"brightness ≥ {res['min_value']:.2f}") if res else "Nothing bright there"
        if hovered and im.is_mouse_clicked(1):
            self.drag_start = (nx, ny)
        if self.drag_start is not None:
            sx, sy = self.drag_start
            dl.add_rect((p0.x + sx * w, p0.y + sy * w * 9 / 16), (mp.x, mp.y), im.get_color_u32((1, 1, 0, 1)), 0.0, 1.0)
            if im.is_mouse_released(1):
                x0, x1 = sorted((min(max(sx, 0), 1), min(max(nx, 0), 1)))
                y0, y1 = sorted((min(max(sy, 0), 1), min(max(ny, 0), 1)))
                if eng.mirror:
                    x0, x1 = 1 - x1, 1 - x0
                if x1 - x0 > 0.01 and y1 - y0 > 0.01:
                    cfg.exclusion = cfg.exclusion + [[round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]]
                self.drag_start = None
        im.text_disabled("Left-click the ball to calibrate its color.  Right-drag to add an exclusion zone.")
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
        _, cfg.hue = im.slider_float("Ball hue (°)", cfg.hue, 0, 360, "%.0f")
        _, cfg.hue_tolerance = im.slider_float("Hue tolerance", cfg.hue_tolerance, 3, 60, "%.0f")
        _, cfg.min_saturation = im.slider_float("Min saturation", cfg.min_saturation, 0, 1)
        _, cfg.min_value = im.slider_float("Min brightness (dark stage)", cfg.min_value, 0, 1)
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
                ch, v = im.slider_int(f"{key.capitalize()}", int(cur), lo, min(hi, 600 if key == "exposure" else hi))
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
            im.progress_bar(float(v), (140, 0), label)
            im.same_line()
        im.new_line()
        im.plot_histogram("##spectrum", a.spectrum, graph_size=(560, 60), scale_min=0, scale_max=1)
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
                    im.progress_bar(max(0.0, (src.peak_db + 60) / 60), (90, 0), f"{src.peak_db:.0f} dB")
                    im.same_line()
                    im.set_next_item_width(120)
                    _, src.gain = im.slider_float(f"gain##{s.id}", src.gain, 0, 4, "%.2f")
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
        ch, ms = im.slider_float("Latency offset (ms)", an.latency_s * 1000, -200, 200, "%.0f")
        if ch:
            an.latency_s = ms / 1000
            self.app.settings["audio"]["latency_ms"] = ms
        if im.button("Tap tempo (T)"):
            self.app.tap_tempo()

    # ---------------------------------------------------------------- scenes
    def tab_scenes(self):
        im, mgr = self.imgui, self.app.scenes
        for i, name in enumerate(mgr.names):
            if i:
                im.same_line()
            sel = mgr.current.name == name
            if sel:
                im.push_style_color(im.Col_.button, (0.35, 0.25, 0.7, 1))
            if im.button(f"{i + 1} {name}"):
                mgr.select(i)
                self.app.settings["scene"] = name
            if sel:
                im.pop_style_color()
        _, mgr.fade_time = im.slider_float("Crossfade (s)", mgr.fade_time, 0.1, 8.0)
        scene = mgr.current
        im.separator_text("Post")
        for k in ("decay", "bloom", "bloom_threshold", "exposure", "vignette", "zoom", "rotate", "hue_shift",
                  "kaleido", "kaleido_spin"):
            from .render.post import DEFAULT_POST
            lo, hi = {"decay": (0, 0.995), "zoom": (0.97, 1.03), "rotate": (-1, 1), "hue_shift": (-1, 1),
                      "kaleido": (0, 12), "kaleido_spin": (-1, 1), "exposure": (0.2, 3), "bloom": (0, 3)}.get(k, (0, 1))
            _, scene.post[k] = im.slider_float(f"{k}##post", float(scene.post.get(k, DEFAULT_POST[k])), lo, hi)
        for li, eff in enumerate(scene.layers):
            label = f"{li + 1}. {getattr(eff, 'key', eff.name)}"
            if im.collapsing_header(f"{label}##layer{li}"):
                if isinstance(eff, ShaderEffect):
                    im.text_disabled(eff.license_line())
                    if eff.error:
                        im.text_colored((1, 0.4, 0.4, 1), eff.error[:400])
                    if eff.unsupported:
                        im.text_colored((1, 0.7, 0.3, 1), "Unsupported: " + ", ".join(eff.unsupported))
                bound = {b.target.split(":", 1)[1] for b in scene.bindings if b.target.startswith(f"{li}:")}
                bound |= {b.target.split(".", 1)[1] for b in scene.bindings if b.target.startswith(eff.name + ".")}
                for k, p in eff.params.items():
                    v = eff.values[k]
                    tag = f"{k}##{li}"
                    if k in bound:
                        im.text(f"{k}: {v:.2f}  ← bound")
                    elif p.kind == "bool":
                        _, nv = im.checkbox(tag, bool(v))
                        eff.values[k] = float(nv)
                    elif p.kind == "int":
                        _, eff.values[k] = im.slider_int(tag, int(v), int(p.lo), int(p.hi))
                    elif p.kind == "point":
                        im.text(f"{k}: follows the ball")
                    elif p.kind == "color":
                        _, c = im.color_edit3(tag, list(v[:3]))
                        eff.values[k] = (*c, 1.0)
                    else:
                        _, eff.values[k] = im.slider_float(tag, float(v), float(p.lo), float(p.hi))
                    if p.help and im.is_item_hovered():
                        im.set_tooltip(p.help)
        if scene.bindings:
            im.separator_text("Bindings")
            for b in scene.bindings:
                im.text(f"{b.target} ← {b.source}  ({b.lo:g} … {b.hi:g})  now {b.value or 0:.2f}")

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
        ch, out["render_scale"] = im.slider_float("Render scale", float(out.get("render_scale", 1.0)), 0.5, 1.0)
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
