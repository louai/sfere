"""Scene cycle: move to another scene every so many seconds.

Settings (settings.toml [cycle]): enabled, seconds, order ("in order" or "shuffle"), on_beat.
With on_beat and a locked tempo, a due change waits for the next beat (at most a couple of
seconds), so scenes change on the music. Any other change of scene (a key, the dropdown, an
agent) starts the countdown again.
"""
from __future__ import annotations

import random

MAX_BEAT_WAIT = 2.0     # seconds a due change may wait for a beat


class SceneCycle:
    def __init__(self, cfg: dict, rng: random.Random | None = None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.since = None          # when the current scene started showing
        self.scene = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    @property
    def seconds(self) -> float:
        return max(1.0, float(self.cfg.get("seconds", 30)))

    def remaining(self, now: float) -> float:
        return max(0.0, self.seconds - (now - self.since)) if self.since is not None else self.seconds

    def tick(self, now: float, scene: str, names: list[str], beat: bool = False, locked: bool = False) -> int | None:
        """Call every frame; returns the index of the scene to switch to, or None."""
        if scene != self.scene or self.since is None:     # started, or switched by someone else
            self.scene, self.since = scene, now
            return None
        if not self.enabled or len(names) < 2:
            self.since = now if not self.enabled else self.since
            return None
        late = now - self.since - self.seconds
        if late < 0:
            return None
        if self.cfg.get("on_beat", True) and locked and not beat and late < MAX_BEAT_WAIT:
            return None
        i = names.index(scene) if scene in names else -1
        if self.cfg.get("order") == "shuffle":
            nxt = self.rng.choice([j for j in range(len(names)) if j != i])
        else:
            nxt = (i + 1) % len(names)
        self.scene, self.since = names[nxt], now     # our own change: the countdown starts now
        return nxt
