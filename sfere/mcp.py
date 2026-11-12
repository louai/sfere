"""sfere's MCP server: lets an agent drive the running sfere.

Run as `sfere mcp` (stdio transport). Register it with an MCP client such as Claude Code:
    claude mcp add --scope user sfere -- ~/.local/bin/sfere mcp
It needs no extra packages: it speaks MCP's JSON-RPC over stdin/stdout and forwards each
tool to the running app through its control socket (sfere/control.py), so the agent sees
and changes the same scenes, layers and outputs as the person at the control window.
"""
from __future__ import annotations

import json
import sys

from .control import send_control

PROTOCOL = "2025-06-18"
SOURCES = ("audio.sub, audio.bass, audio.mid, audio.high, audio.level, audio.beat, audio.beat_phase, "
           "ball.speed, ball.accel, ball.curvature")
SCENE = {"description": "A scene's name (or a unique part of it) or its number from 1.",
         "type": ["string", "integer"]}
LAYER = {"description": "Layer number in the current scene: 1 is the bottom (drawn first), the highest is on top.",
         "type": "integer"}


def _obj(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


TOOLS = [
    ("status", "What sfere is doing now: current scene and the list of scenes, frame rate, the ball (tracking/lost, "
     "position, speed), the music (tempo, lock, band levels, audio sources), camera, output (fullscreen display, "
     "blackout, freeze), recording, virtual camera, and recent messages such as shader errors.", _obj(), "status"),
    ("list_scenes", "Every scene in the library in order (the first nine are on the number keys), with its layers "
     "and whether it's a built-in that can be reset.", _obj(), "list_scenes"),
    ("get_scene", "The current scene live: crossfade, palette, post settings, and each layer (numbered from 1 at the "
     "bottom) with its effect, on/off and every parameter's value, range, default and binding. With scene: another "
     "scene's saved spec.", _obj({"scene": SCENE}), "get_scene"),
    ("list_effects", "Effects that can be added as layers: built-in effects (with a one-line description) and "
     "Shadertoy/ISF shaders (keys like 'isf:vidvox/Bad TV'). Filters process the layers below them.",
     _obj({"query": {"type": "string", "description": "Part of the effect key."}}), "list_effects"),
    ("screenshot", "Save what the output shows right now as a PNG and return its path (look at it to judge a change).",
     _obj({"path": {"type": "string", "description": "Where to save; default ~/.cache/sfere/output-<time>.png"},
           "width": {"type": "integer", "default": 1280, "description": "Scale down to this width."}}),
     "screenshot"),
    ("select_scene", "Switch the output to a scene, with the crossfade (or at once).",
     _obj({"scene": SCENE, "immediate": {"type": "boolean", "default": False}}, ["scene"]), "select_scene"),
    ("new_scene", "Create a scene after the current one and switch to it: blank (backdrop and orb) or a copy of the "
     "current scene.", _obj({"copy_current": {"type": "boolean", "default": False},
                             "name": {"type": "string", "description": "Its name (must be unique)."}}), "new_scene"),
    ("rename_scene", "Rename the current scene.", _obj({"name": {"type": "string"}}, ["name"]), "rename_scene"),
    ("delete_scene", "Delete the current scene (can't be undone; needs confirm=true) and crossfade to its neighbour.",
     _obj({"confirm": {"type": "boolean"}}, ["confirm"]), "delete_scene"),
    ("move_scene", "Move the current scene earlier (-1) or later (+1) in the list, which changes its number key.",
     _obj({"delta": {"type": "integer"}}, ["delta"]), "move_scene"),
    ("reset_scene", "Put a built-in scene back the way sfere ships it (replaces its layers; needs confirm=true).",
     _obj({"confirm": {"type": "boolean"}}, ["confirm"]), "reset_scene"),
    ("set_look", "Change the current scene's post settings (decay, bloom, bloom_threshold, exposure, vignette, zoom, "
     "rotate, hue_shift, kaleido, kaleido_spin), its palette (hex colors the effects take their colors from) or "
     "the crossfade time.", _obj({"post": {"type": "object", "description": "{setting: value}"},
                                  "palette": {"type": "array", "items": {"type": "string"}},
                                  "crossfade_s": {"type": "number"}}), "set_look"),
    ("set_layer", "Change a layer's parameters (names and ranges from get_scene; values are clamped to the range; "
     "colors as hex) and/or turn it on or off.",
     _obj({"layer": LAYER, "values": {"type": "object", "description": "{param: value}"},
           "enabled": {"type": "boolean"}}, ["layer"]), "set_layer"),
    ("add_layer", "Add an effect on top of the current scene (keys from list_effects).",
     _obj({"effect": {"type": "string"}}, ["effect"]), "add_layer"),
    ("layer_tool", "Remove a layer, move it up (towards the top) or down, duplicate it, or reset it to default "
     "settings without bindings.",
     _obj({"layer": LAYER, "action": {"type": "string", "enum": ["remove", "up", "down", "duplicate", "reset"]}},
          ["layer", "action"]), "layer_tool"),
    ("bind", "Make a number setting follow the music or the ball: between at_rest (quiet / still) and at_full "
     "(loud / fast), with smoothing in seconds. target is 'post.<setting>' or '<layer>.<param>' (e.g. '3.size'). "
     f"source is one of: {SOURCES}; null stops the binding.",
     _obj({"target": {"type": "string"}, "source": {"type": ["string", "null"]},
           "at_rest": {"type": "number"}, "at_full": {"type": "number"},
           "smoothing_s": {"type": "number", "default": 0.15}}, ["target"]), "bind"),
    ("show", "Show controls: blackout, freeze the output, mirror the camera, the debug marker, fullscreen on a "
     "display (name from status), back to a window, or one tap of tap tempo.",
     _obj({"blackout": {"type": "boolean"}, "freeze": {"type": "boolean"}, "mirror": {"type": "boolean"},
           "debug_marker": {"type": "boolean"}, "fullscreen": {"type": "string"}, "window": {"type": "boolean"},
           "tap_tempo": {"type": "boolean"}}), "show"),
    ("record", "Start or stop recording, or read its status. Recordings go to ~/Videos/sfere: the output with the "
     "music, the camera, and the music as WAV. Settings (output, camera, height 720/1080/1440/0 for full, fps, "
     "folder) can change while not recording. Files are finished in the background after stop; status lists them.",
     _obj({"action": {"type": "string", "enum": ["start", "stop", "status"], "default": "status"},
           "output": {"type": "boolean"}, "camera": {"type": "boolean"}, "height": {"type": "integer"},
           "fps": {"type": "integer"}, "folder": {"type": "string"}}), "record"),
    ("virtual_camera", "Turn the virtual camera named 'sfere' on or off, or change its size; returns where other apps "
     "find it.", _obj({"on": {"type": "boolean"}, "width": {"type": "integer"}, "height": {"type": "integer"}}),
     "virtual_camera"),
    ("audio", "List the audio sources (other apps' output, mics, line-ins) and which are on; turn one on or off; "
     "set an active source's gain (0–4).",
     _obj({"enable": {"type": "string"}, "disable": {"type": "string"}, "source": {"type": "string"},
           "gain": {"type": "number"}}), "audio"),
]
COMMANDS = {name: cmd for name, _, _, cmd in TOOLS}
NOT_RUNNING = ("sfere isn't running (or its control socket is off). Ask the user to start it: "
               "type `sfere` in a terminal or open it from the app drawer.")


def call(name: str, args: dict) -> tuple[dict, bool]:
    cmd = COMMANDS.get(name)
    if cmd is None:
        return {"message": f"Unknown tool {name}"}, True
    reply = send_control({"cmd": cmd, **args}, timeout=40)
    if reply is None:
        return {"message": NOT_RUNNING}, True
    return reply, not reply.get("ok", True)


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        ver = (req.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "sfere", "version": "1.0"},
                  "instructions": "Drives the user's running sfere: music-reactive visuals around a tracked "
                                  "juggling ball, shown live on a projector. Read status/get_scene before "
                                  "changing things, keep changes gentle during a show, and use screenshot to "
                                  "see the result. Layers are numbered from 1 at the bottom."}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}
    elif method == "tools/call":
        params = req.get("params") or {}
        out, err = call(params.get("name"), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}], "isError": bool(err)}
    elif method == "ping":
        result = {}
    elif rid is None:               # a notification (initialized, cancelled, …): no reply
        return None
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                reply = handle(req)
            except Exception as e:   # never die mid-session
                reply = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
