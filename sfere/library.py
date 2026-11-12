"""The scene library: every scene's spec, saved as JSON next to settings.toml.

A spec is what scenes.Scene is built from: name, palette, post, layers (effect, values,
bind, enabled) and bind (post.* bindings). Built-in scenes carry "preset": <name> so they
can be reset. Before scenes.json existed, settings.toml kept edited layers per preset in
`scene_layers`; those are carried over on first load.
"""
from __future__ import annotations

import copy
import json

from . import settings as settings_mod
from .scenes import PRESETS

DEFAULT_PALETTE = ["#ff2bd6", "#7a2bff", "#2bd9ff", "#2bff88"]


def path():
    return settings_mod.PATH.parent / "scenes.json"


def defaults(legacy_layers: dict | None = None) -> list[dict]:
    out = []
    for p in PRESETS:
        spec = copy.deepcopy(p)
        spec["preset"] = p["name"]
        if legacy_layers and p["name"] in legacy_layers:
            spec["layers"] = copy.deepcopy(legacy_layers[p["name"]])
        out.append(spec)
    return out


def load(settings: dict | None = None) -> list[dict]:
    """The saved library, or the built-in scenes (with any legacy layer edits)."""
    try:
        data = json.loads(path().read_text())
        specs = data.get("scenes") if isinstance(data, dict) else None
        if specs and all(isinstance(s, dict) and s.get("name") for s in specs):
            return specs
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        broken = path().with_suffix(".json.broken")
        path().replace(broken)
    return defaults((settings or {}).get("scene_layers"))


def save(specs: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "scenes": specs}, indent=1))
    tmp.replace(p)


def preset(name: str) -> dict | None:
    """The built-in spec a library scene started from."""
    p = next((p for p in PRESETS if p["name"] == name), None)
    return copy.deepcopy(p) if p else None


def unique_name(name: str, taken) -> str:
    taken = set(taken)
    if name not in taken:
        return name
    i = 2
    while f"{name} {i}" in taken:
        i += 1
    return f"{name} {i}"


def blank(name: str) -> dict:
    return {"name": name, "palette": list(DEFAULT_PALETTE), "post": {},
            "layers": [{"effect": "backdrop", "values": {"brightness": 0.03}}, {"effect": "orb"}]}
