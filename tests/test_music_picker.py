"""Picking the next tracks: tempo first, then key, popularity and variety."""
from __future__ import annotations

import random

from sfere.music.picker import camelot, choose, key_fit


def test_camelot_reads_audius_key_names():
    assert camelot("A minor") == (8, "A") and camelot("C major") == (8, "B")
    assert camelot("D flat minor") == (12, "A") and camelot("F sharp minor") == (11, "A")
    assert camelot("A flat major") == (4, "B") and camelot("Ebm") == (2, "A")
    assert camelot("") is None and camelot("nonsense") is None


def test_key_fit():
    assert key_fit("A minor", "C major") == 1.0          # relative keys
    assert key_fit("A minor", "E minor") == 1.0          # one step round the wheel
    assert key_fit("A minor", "B minor") == 0.5          # two steps
    assert key_fit("A minor", "D sharp minor") == 0.0    # opposite side
    assert key_fit("", "A minor") == 0.5


def T(id, bpm, key="", artist="x", plays=0):
    return {"id": id, "bpm": bpm, "key": key, "artist": artist, "plays": plays}


def test_choose_prefers_tempo_then_key_and_never_repeats():
    pool = [T("far", 150), T("close-clash", 128.5, "D sharp minor", "b"), T("close-blend", 129, "E minor", "c"),
            T("exact", 128, "B flat minor", "d"), T("played", 128, "A minor", "e")]
    prev = {"id": "now", "bpm": 128, "key": "A minor", "artist": "z"}
    picks = choose(pool, 128, prev, played={"played"}, n=3, rng=random.Random(0))
    ids = [p["id"] for p in picks]
    assert "far" not in ids and "played" not in ids          # 17 % stretch is too much; no repeats
    assert ids[0] == "close-blend"                           # in key beats a closer tempo that clashes
    assert len(ids) == 3


def test_choose_follows_a_new_target():
    pool = [T(f"t{b}", b) for b in (120, 124, 128, 132, 136, 140)]
    assert choose(pool, 124, None, set(), n=1, rng=random.Random(1))[0]["id"] == "t124"
    assert choose(pool, 138, None, set(), n=1, rng=random.Random(1))[0]["id"] in ("t136", "t140")
    assert choose(pool, 200, None, set(), n=1) == []           # nothing within reach
