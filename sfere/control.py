"""Remote control of the running app (used by the MCP server in sfere/mcp.py).

A Unix socket at $XDG_RUNTIME_DIR/sfere/control.sock takes one JSON command per
connection and answers with one JSON reply. Commands run on the main thread, between
frames, through the same calls the control window's buttons use, so an agent sees and
changes exactly what the user does (and scenes are built in the right GL context).

Layers are numbered from 1 at the bottom of the stack, as in the Scenes tab.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from pathlib import Path

import numpy as np


def socket_path() -> Path:
    if os.environ.get("SFERE_CONTROL_SOCKET"):       # tests, or a second sfere
        return Path(os.environ["SFERE_CONTROL_SOCKET"])
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/sfere-{os.getuid()}"
    return Path(base) / "sfere" / "control.sock"


def send_control(msg: dict, timeout: float = 20.0) -> dict | None:
    """Send a command to the running sfere; its reply, or None if sfere isn't running."""
    path = socket_path()
    if not path.exists():
        return None
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall(json.dumps(msg).encode() + b"\n")
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
    except OSError:
        return None
    finally:
        s.close()
    try:
        return json.loads(b"".join(chunks).decode() or "{}")
    except ValueError:
        return {"ok": False, "message": "unreadable reply"}


class ControlServer:
    """Accepts commands on a socket thread; poll() runs them on the main thread."""

    def __init__(self, app):
        self.app = app
        self.commands = Commands(app)
        self.path = socket_path()
        self.error = ""
        self._queue: queue.Queue = queue.Queue()
        self._sock = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.exists():
                if send_control({"cmd": "ping"}, timeout=1.0) is not None:
                    self.error = "another sfere is running and has the control socket"
                    return
                self.path.unlink()
            self._sock = socket.socket(socket.AF_UNIX)
            self._sock.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self._sock.listen(4)
        except OSError as e:
            self.error = f"control socket: {e}"
            self._sock = None
            return
        threading.Thread(target=self._accept, name="control", daemon=True).start()

    def _accept(self):
        while self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(10)
            data = b""
            try:
                while not data.endswith(b"\n"):
                    b = conn.recv(65536)
                    if not b:
                        break
                    data += b
                msg = json.loads(data.decode() or "{}")
            except (OSError, ValueError) as e:
                reply = {"ok": False, "message": f"bad request: {e}"}
            else:
                if msg.get("cmd") == "ping":
                    reply = {"ok": True}
                else:
                    done = threading.Event()
                    box = {}
                    self._queue.put((msg, box, done))
                    if not done.wait(30):
                        box["reply"] = {"ok": False, "message": "sfere didn't answer in time (busy or stalled)"}
                    reply = box["reply"]
            try:
                conn.sendall(json.dumps(reply, default=_plain).encode())
            except OSError:
                pass

    def poll(self):
        """Run queued commands (main thread, between frames)."""
        while True:
            try:
                msg, box, done = self._queue.get_nowait()
            except queue.Empty:
                return
            box["reply"] = self.commands.run(msg)
            done.set()

    def close(self):
        sock, self._sock = self._sock, None
        if sock:
            sock.close()
            try:
                self.path.unlink()
            except OSError:
                pass


def _plain(v):
    if isinstance(v, (np.generic,)):
        return v.item()
    if isinstance(v, (tuple, set, np.ndarray)):
        return list(v)
    if isinstance(v, Path):
        return str(v)
    return str(v)


class CommandError(Exception):
    pass


class Commands:
    def __init__(self, app):
        self.app = app

    def run(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn is None:
            return {"ok": False, "message": f"Unknown command {cmd!r}"}
        try:
            out = fn(**{k: v for k, v in msg.items() if k != "cmd"})
        except CommandError as e:
            return {"ok": False, "message": str(e)}
        except TypeError as e:
            return {"ok": False, "message": f"bad arguments: {e}"}
        except Exception as e:  # report to the agent instead of losing the show
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}
        out = out or {}
        out.setdefault("ok", True)
        return out

    # ------------------------------------------------------------------ helpers
    @property
    def scenes(self):
        return self.app.scenes

    def _scene_index(self, scene) -> int:
        names = self.scenes.names
        if isinstance(scene, int) or (isinstance(scene, str) and scene.isdigit()):
            i = int(scene) - 1
            if 0 <= i < len(names):
                return i
            raise CommandError(f"there are {len(names)} scenes; numbers go from 1")
        for i, n in enumerate(names):
            if n.lower() == str(scene).lower():
                return i
        matches = [i for i, n in enumerate(names) if str(scene).lower() in n.lower()]
        if len(matches) == 1:
            return matches[0]
        raise CommandError(f"no scene {scene!r}; scenes: {', '.join(names)}")

    def _layer(self, layer) -> int:
        n = len(self.scenes.current.layers)
        try:
            i = int(layer) - 1
        except (TypeError, ValueError):
            raise CommandError("layer is a number from 1 (bottom) to the top")
        if not 0 <= i < n:
            raise CommandError(f"the scene has {n} layers, numbered 1 (bottom) to {n} (top)")
        return i

    def _changed(self):
        self.app.settings["scene"] = self.scenes.current.name
        self.app.save_scenes()

    # ------------------------------------------------------------------ reading
    def cmd_status(self):
        app, a, b = self.app, self.app.last_audio, self.app.last_ball
        rec = app.recording
        return {
            "scene": self.scenes.current.name,
            "scenes": self.scenes.names,
            "fps": round(app.fps, 1),
            "ball": {"state": b.state, "visible": round(float(b.visible), 2), "x": round(b.x, 3), "y": round(b.y, 3),
                     "speed": round(float(b.speed), 2)},
            "audio": {"bpm": round(a.bpm, 1), "tempo_locked": a.locked, "sub": round(a.sub, 2), "bass": round(a.bass, 2),
                      "mid": round(a.mid, 2), "high": round(a.high, 2), "level": round(a.level, 2),
                      "sources": {sid: {"name": s.name, "status": s.status, "gain": s.gain, "peak_db": round(s.peak_db, 1)}
                                  for sid, s in app.engine.audio.sources.items()}},
            "camera": {"source": app.engine.capture.source, "status": app.engine.capture.status,
                       "fps": round(app.engine.capture.fps, 1)},
            "output": {"fullscreen_on": app.fullscreen_monitor, "displays": [app.monitor_name(m) for m in app.monitors()],
                       "render_size": list(self.scenes.size), "blackout": self.scenes.blackout_target > 0.5,
                       "freeze": app.freeze, "mirror": app.engine.mirror},
            "recording": ({"elapsed_s": round(rec.elapsed, 1), "status": rec.status()} if rec else None),
            "recording_log": app.record_log[-5:],
            "virtual_camera": ({"where": app.vcam.where, "running": app.vcam.alive} if app.vcam
                               else {"error": app.vcam_error} if app.vcam_error else None),
            "messages": self.scenes.messages[-8:],
        }

    def cmd_list_scenes(self):
        return {"scenes": [{"number": i + 1, "name": s["name"], "key": str(i + 1) if i < 9 else None,
                            "layers": [l["effect"] for l in s.get("layers", [])],
                            "built_in": s.get("preset"), "current": s["name"] == self.scenes.current.name}
                           for i, s in enumerate(self.scenes.presets)]}

    def cmd_get_scene(self, scene=None):
        """The current scene live (values, ranges, bindings), or another scene's saved spec."""
        mgr = self.scenes
        if scene is not None and mgr.names[self._scene_index(scene)] != mgr.current.name:
            return {"spec": mgr.presets[self._scene_index(scene)], "current": False}
        s = mgr.current
        bound = {b.target: {"source": b.source, "at_rest": b.lo, "at_full": b.hi, "smoothing_s": b.smooth}
                 for b in s.bindings}
        layers = []
        for i, eff in enumerate(s.layers):
            params = {}
            for k, p in eff.params.items():
                v = eff.values.get(k)
                params[k] = {"value": _plain(v) if not isinstance(v, (int, float)) else round(float(v), 4),
                             "kind": p.kind, "min": p.lo, "max": p.hi, "default": _plain(p.default)
                             if not isinstance(p.default, (int, float)) else p.default}
                if p.help:
                    params[k]["help"] = p.help
                if f"{i}:{k}" in bound:
                    params[k]["bound"] = bound[f"{i}:{k}"]
            layers.append({"layer": i + 1, "effect": getattr(eff, "key", eff.name), "enabled": eff.enabled,
                           "params": params, **({"missing": True} if type(eff).__name__ == "Missing" else {}),
                           **({"error": eff.error} if getattr(eff, "error", None) else {})})
        from .render.post import DEFAULT_POST
        return {"current": True, "name": s.name, "number": mgr.index + 1, "crossfade_s": mgr.fade_time,
                "palette": [_hex(c) for c in s.palette],
                "post": {k: {"value": round(float(s.post.get(k, d)), 4), **({"bound": bound[f"post.{k}"]}
                                                                          if f"post.{k}" in bound else {})}
                         for k, d in DEFAULT_POST.items()},
                "layers": layers, "layer_order": "layer 1 is drawn first (bottom); the last is on top"}

    def cmd_list_effects(self, query: str = ""):
        from .fx.builtin import BUILTIN
        q = query.lower()
        out = [{"effect": k, "kind": "built-in", "about": (BUILTIN[k].__doc__ or "").strip().split("\n")[0]}
               for k in sorted(BUILTIN) if q in k.lower()]
        out += [{"effect": k, "kind": k.split(":")[0]} for k in sorted(self.scenes.shader_index) if q in k.lower()]
        return {"count": len(out), "effects": out[:200]}

    def cmd_screenshot(self, path: str | None = None, width: int = 1280):
        """Save what the output shows as a PNG; returns the file path."""
        import cv2
        tex = self.app.last_tex
        img = np.frombuffer(tex.read(), np.uint8).reshape(tex.height, tex.width, 4)[::-1, :, :3]
        if width and tex.width > width:
            img = cv2.resize(img, (int(width), int(tex.height * width / tex.width)), interpolation=cv2.INTER_AREA)
        out = Path(path).expanduser() if path else Path.home() / ".cache" / "sfere" / f"output-{time.strftime('%H%M%S')}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2BGR))
        return {"path": str(out), "size": [img.shape[1], img.shape[0]], "scene": self.scenes.current.name}

    # ------------------------------------------------------------------ scenes
    def cmd_select_scene(self, scene, immediate: bool = False):
        i = self._scene_index(scene)
        if immediate:
            self.scenes.select(i, immediate=True)
            self.app.settings["scene"] = self.scenes.names[i]
        else:
            self.app.select_scene(i)
        return {"scene": self.scenes.current.name}

    def cmd_new_scene(self, copy_current: bool = False, name: str | None = None):
        self.scenes.new_scene(copy_current=bool(copy_current))
        if name and not self.scenes.rename(name):
            self._changed()
            return {"ok": False, "message": f"created {self.scenes.current.name}, but {name!r} is taken",
                    "scene": self.scenes.current.name}
        self._changed()
        return {"scene": self.scenes.current.name}

    def cmd_rename_scene(self, name: str):
        if not self.scenes.rename(name):
            raise CommandError(f"{name!r} is empty or another scene has it")
        self._changed()
        return {"scene": self.scenes.current.name}

    def cmd_delete_scene(self, confirm: bool = False):
        if not confirm:
            raise CommandError(f"deleting {self.scenes.current.name} can't be undone; repeat with confirm=true")
        name = self.scenes.current.name
        if not self.scenes.delete():
            raise CommandError("the last scene can't be deleted")
        self._changed()
        return {"deleted": name, "scene": self.scenes.current.name}

    def cmd_move_scene(self, delta: int):
        self.scenes.move(int(delta))
        self._changed()
        return {"scenes": self.scenes.names}

    def cmd_reset_scene(self, confirm: bool = False):
        if not self.scenes.preset_of_current():
            raise CommandError(f"{self.scenes.current.name} isn't a built-in scene")
        if not confirm:
            raise CommandError("this replaces the scene's layers and settings; repeat with confirm=true")
        self.scenes.reset_to_preset()
        self._changed()
        return self.cmd_get_scene()

    def cmd_set_look(self, post: dict | None = None, palette: list | None = None, crossfade_s: float | None = None):
        from .render.post import DEFAULT_POST
        from .scenes import hexrgb
        s = self.scenes.current
        for k, v in (post or {}).items():
            if k not in DEFAULT_POST:
                raise CommandError(f"no post setting {k!r}; they are {', '.join(DEFAULT_POST)}")
            s.post[k] = float(v)
        if palette is not None:
            s.palette = [hexrgb(c) for c in palette] or s.palette
        if crossfade_s is not None:
            self.scenes.fade_time = max(0.1, min(8.0, float(crossfade_s)))
        self.scenes.sync()
        return {"post": {k: s.post.get(k, d) for k, d in DEFAULT_POST.items()}, "palette": [_hex(c) for c in s.palette]}

    def cmd_cycle(self, on: bool | None = None, seconds: float | None = None, order: str | None = None,
                  on_beat: bool | None = None):
        cfg = self.app.settings["cycle"]
        if order not in (None, "in order", "shuffle"):
            raise CommandError("order is 'in order' or 'shuffle'")
        if seconds is not None:
            if float(seconds) < 1:
                raise CommandError("seconds must be at least 1")
            cfg["seconds"] = float(seconds)
        for k, v in (("enabled", on), ("order", order), ("on_beat", on_beat)):
            if v is not None:
                cfg[k] = v
        cyc = self.app.cycle
        return {"on": cyc.enabled, "seconds": cyc.seconds, "order": cfg.get("order", "in order"),
                "on_beat": bool(cfg.get("on_beat", True)),
                "next_scene_in_s": round(cyc.remaining(time.monotonic()), 1) if cyc.enabled else None}

    # ------------------------------------------------------------------ layers
    def cmd_set_layer(self, layer, values: dict | None = None, enabled: bool | None = None):
        i = self._layer(layer)
        eff = self.scenes.current.layers[i]
        changed = {}
        for k, v in (values or {}).items():
            p = eff.params.get(k)
            if p is None:
                raise CommandError(f"layer {i + 1} ({getattr(eff, 'key', eff.name)}) has no {k!r}; "
                                   f"it has {', '.join(eff.params)}")
            if p.kind == "bool":
                v = float(bool(v))
            elif p.kind == "color":
                from .scenes import hexrgb
                v = (*hexrgb(v), 1.0) if isinstance(v, str) else tuple(float(x) for x in v)
            elif p.kind == "point":
                raise CommandError(f"{k} follows the ball")
            else:
                v = min(max(float(v), p.lo), p.hi)
                v = int(round(v)) if p.kind == "int" else v
            eff.values[k] = v
            changed[k] = _plain(v) if isinstance(v, tuple) else v
        if enabled is not None:
            eff.enabled = bool(enabled)
        self.scenes.sync()
        return {"layer": i + 1, "effect": getattr(eff, "key", eff.name), "enabled": eff.enabled, "set": changed}

    def cmd_add_layer(self, effect: str):
        if not self.scenes.add_layer(effect):
            raise CommandError(f"couldn't add {effect!r}: {(self.scenes.messages or ['no such effect'])[-1]}")
        self.app.save_scenes()
        return {"layer": len(self.scenes.current.layers), "effect": effect}

    def cmd_layer_tool(self, layer, action: str):
        i = self._layer(layer)
        mgr = self.scenes
        n = len(mgr.current.layers)
        if action == "remove":
            mgr.remove_layer(i)
        elif action == "up":
            if i == n - 1:
                raise CommandError("it's already the top layer")
            mgr.move_layer(i, 1)
        elif action == "down":
            if i == 0:
                raise CommandError("it's already the bottom layer")
            mgr.move_layer(i, -1)
        elif action == "duplicate":
            mgr.duplicate_layer(i)
        elif action == "reset":
            mgr.reset_layer(i)
        else:
            raise CommandError("action is one of remove, up, down, duplicate, reset")
        self.app.save_scenes()
        return {"layers": [getattr(e, "key", e.name) for e in mgr.current.layers]}

    def cmd_bind(self, target: str, source: str | None = None, at_rest: float | None = None,
                 at_full: float | None = None, smoothing_s: float = 0.15):
        """target: 'post.<setting>' or '<layer>.<param>' (layer from 1)."""
        from .scenes import BIND_SOURCES
        s = self.scenes.current
        if target.startswith("post."):
            from .render.post import DEFAULT_POST
            key = target[5:]
            if key not in DEFAULT_POST:
                raise CommandError(f"no post setting {key!r}")
            internal, value = target, float(s.post.get(key, DEFAULT_POST[key]))
            lo, hi = at_rest if at_rest is not None else value, at_full if at_full is not None else value * 1.5 + 0.1
        else:
            layer, _, param = target.partition(".")
            i = self._layer(layer)
            eff = s.layers[i]
            p = eff.params.get(param)
            if p is None or p.kind != "float":
                raise CommandError(f"layer {i + 1} has no number setting {param!r}")
            internal, value = f"{i}:{param}", float(eff.values[param])
            lo = at_rest if at_rest is not None else value
            hi = at_full if at_full is not None else min(p.hi, value + (p.hi - p.lo) * 0.5)
        sources = [k for k, _ in BIND_SOURCES]
        if source and source not in sources:
            raise CommandError(f"source is one of {', '.join(sources)} (or null to stop)")
        s.set_binding(internal, source, float(lo), float(hi), float(smoothing_s))
        self.scenes.sync()
        return {"target": target, "source": source, "at_rest": lo, "at_full": hi} if source else {"target": target,
                                                                                                   "source": None}

    # ------------------------------------------------------------------ show controls
    def cmd_show(self, blackout: bool | None = None, freeze: bool | None = None, mirror: bool | None = None,
                 debug_marker: bool | None = None, fullscreen: str | None = None, window: bool | None = None,
                 tap_tempo: bool | None = None):
        app = self.app
        if blackout is not None:
            app.scenes.blackout_target = 1.0 if blackout else 0.0
        if freeze is not None:
            app.freeze = bool(freeze)
        if mirror is not None:
            app.engine.mirror = bool(mirror)
        if debug_marker is not None:
            app.debug = bool(debug_marker)
        if fullscreen:
            names = [app.monitor_name(m) for m in app.monitors()]
            if fullscreen not in names:
                raise CommandError(f"no display {fullscreen!r}; displays: {', '.join(names)}")
            app.set_fullscreen(fullscreen)
        if window:
            app.set_fullscreen(None)
        if tap_tempo:
            app.tap_tempo()
        return {"blackout": app.scenes.blackout_target > 0.5, "freeze": app.freeze, "mirror": app.engine.mirror,
                "fullscreen_on": app.fullscreen_monitor}

    def cmd_record(self, action: str = "status", output: bool | None = None, camera: bool | None = None,
                   height: int | None = None, fps: int | None = None, folder: str | None = None,
                   layout: str | None = None):
        app, cfg = self.app, self.app.settings["recording"]
        if layout not in (None, "separate", "stacked"):
            raise CommandError("layout is 'separate' or 'stacked'")
        for k, v in (("output", output), ("camera", camera), ("height", height), ("fps", fps), ("folder", folder),
                     ("layout", layout)):
            if v is not None:
                if app.recording:
                    raise CommandError("stop the recording before changing its settings")
                cfg[k] = v
        if action == "start" and not app.recording and not app.start_recording():
            raise CommandError(app.record_log[-1] if app.record_log else "couldn't start recording")
        if action == "stop":
            app.stop_recording()
        rec = app.recording
        return {"recording": bool(rec), "elapsed_s": round(rec.elapsed, 1) if rec else None,
                "status": rec.status() if rec else None, "settings": dict(cfg), "log": app.record_log[-5:],
                "finishing": any(t.is_alive() for t in app.finishing)}

    def cmd_virtual_camera(self, on: bool | None = None, width: int | None = None, height: int | None = None):
        app, cfg = self.app, self.app.settings["virtual_camera"]
        if width and height:
            cfg["width"], cfg["height"] = int(width), int(height)
            if app.vcam:
                app.set_virtual_camera(True)
        if on is not None:
            app.set_virtual_camera(bool(on))
        from .outputs.vcam import setup_hint
        out = {"on": app.vcam is not None, "error": app.vcam_error or None}
        if app.vcam:
            out.update(where=app.vcam.where, backend=app.vcam.backend, running=app.vcam.alive,
                       size=list(app.vcam.size), fps=app.vcam.fps)
        if not app.vcam or app.vcam.backend == "pipewire":
            out["note"] = ("A PipeWire camera works in OBS 30+, Firefox and Chrome. " + setup_hint())
        return out

    def cmd_audio(self, enable: str | None = None, disable: str | None = None, gain: float | None = None,
                  source: str | None = None):
        from .audio.sources import list_sources
        eng = self.app.engine.audio
        if enable:
            info = next((s for s in list_sources() if s.id == enable), None)
            if info is None:
                raise CommandError(f"no audio source {enable!r}")
            eng.enable(info.id, 1.0, info.name, info.kind)
        if disable:
            eng.disable(disable)
        if gain is not None and source:
            if source not in eng.sources:
                raise CommandError(f"{source!r} isn't on")
            eng.sources[source].gain = max(0.0, min(4.0, float(gain)))
        return {"available": [{"id": s.id, "name": s.name, "kind": s.kind, "on": s.id in eng.sources}
                              for s in list_sources()],
                "on": {sid: {"gain": s.gain, "status": s.status, "peak_db": round(s.peak_db, 1)}
                       for sid, s in eng.sources.items()}}


def _hex(c) -> str:
    from .scenes import to_hex
    return to_hex(c)
