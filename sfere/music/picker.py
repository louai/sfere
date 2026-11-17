"""Choosing the next tracks of a set: close to the target tempo, in a compatible key, no repeats.

Keys use the Camelot wheel DJs mix by: the same number (relative major/minor) or a
neighbouring number with the same letter blend; two steps away still works; anything else
clashes. Tempo is the strongest factor: a track is only a candidate if stretching it to the
target stays within `max_stretch` (beyond ~8 % stretched audio starts to sound processed).
"""
from __future__ import annotations

import math
import random
import re

NOTES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
# Camelot number for each pitch class: minor keys (A) and major keys (B)
CAMELOT_MINOR = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
CAMELOT_MAJOR = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}


def camelot(key: str) -> tuple[int, str] | None:
    """'D flat minor' / 'F# major' / 'Ebm' -> (12, 'A') etc.; None if unreadable."""
    m = re.match(r"\s*([a-gA-G])\s*(#|b|sharp|flat|♯|♭)?\s*(major|minor|maj|min|m)?\s*$", key or "", re.I)
    if not m:
        return None
    pc = NOTES[m.group(1).lower()]
    acc = (m.group(2) or "").lower()
    pc = (pc + (1 if acc in ("#", "sharp", "♯") else -1 if acc in ("b", "flat", "♭") else 0)) % 12
    minor = (m.group(3) or "").lower() in ("minor", "min", "m")
    return (CAMELOT_MINOR if minor else CAMELOT_MAJOR)[pc], "A" if minor else "B"


def key_fit(a: str, b: str) -> float:
    """1 blends, 0.5 works, 0 clashes; 0.5 when a key is unknown."""
    ca, cb = camelot(a), camelot(b)
    if not ca or not cb:
        return 0.5
    step = min((ca[0] - cb[0]) % 12, (cb[0] - ca[0]) % 12)
    if step == 0 or (step == 1 and ca[1] == cb[1]):
        return 1.0
    if step <= 2:
        return 0.5
    return 0.0


def stretch_needed(bpm: float, target: float) -> float:
    """How far a track must be sped up or slowed down (0.03 = 3 %)."""
    return abs(target / bpm - 1.0)


def score(t: dict, target: float, prev: dict | None, max_stretch: float, rng: random.Random) -> float:
    s = stretch_needed(t["bpm"], target)
    if s > max_stretch:
        return -math.inf
    out = 3.0 * (1.0 - s / max_stretch)
    if prev:
        out += 1.5 * key_fit(t.get("key", ""), prev.get("key", ""))
        if t.get("artist") == prev.get("artist"):
            out -= 2.0
    out += 0.5 * min(1.0, math.log1p(t.get("plays", 0)) / math.log(1e6))
    return out + 0.3 * rng.random()          # a little variety between sets


def choose(pool: list[dict], target: float, prev: dict | None, played: set, n: int = 3,
           max_stretch: float = 0.08, rng: random.Random | None = None) -> list[dict]:
    """The best `n` next tracks, each chosen after the one before it (so keys chain)."""
    rng = rng or random.Random()
    out, taken = [], set(played)
    for _ in range(n):
        best, best_s = None, -math.inf
        for t in pool:
            if t["id"] in taken:
                continue
            s = score(t, target, prev, max_stretch, rng)
            if s > best_s:
                best, best_s = t, s
        if best is None:
            break
        out.append(best)
        taken.add(best["id"])
        prev = best
    return out
