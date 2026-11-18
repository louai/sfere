"""Genres for DJ sets, editable in the Music tab and saved in settings ([music] genres).

A genre is how sfere finds its music on Audius and the tempo range the movement is moved into:
- name, and a BPM range;
- `audius`: Audius genres to search (or, with keywords, to keep); empty means any;
- `keywords`: words searched for (titles, tags, descriptions). Audius has no genre for much
  of the world's dance music (SWANA, South Asian), so those are found by keyword;
- `exclude`: Audius genres never taken (keyword searches also turn up, say, metal);
- `avoid`: words that rule a track out when its title or tags have them ("type beat":
  instrumentals made for rappers, not dance tracks);
- `octaves`: also take tracks tagged at half or double the range (bhangra and dabke are
  often tagged in half time), counted at the range's tempo.
"""
from __future__ import annotations

import copy
import re
import unicodedata

# Audius's genre names (search filter values)
AUDIUS_GENRES = [
    "Electronic", "House", "Deep House", "Tech House", "Techno", "Trance", "Progressive House", "Afro House",
    "Drum & Bass", "Jungle", "Dubstep", "Trap", "Electro", "Disco", "Future House", "Future Bass", "Tropical House",
    "Moombahton", "Jersey Club", "Hardstyle", "Glitch Hop", "Downtempo", "Lo-Fi", "Hyperpop", "Dancehall",
    "Hip-Hop/Rap", "R&B/Soul", "Pop", "World", "Latin", "Reggae", "Funk", "Alternative", "Rock", "Experimental",
    "Folk", "Acoustic", "Jazz", "Blues", "Soundtrack", "Ambient", "Classical", "Devotional", "Country", "Metal",
    "Punk", "Vaporwave", "Spoken Word", "Comedy", "Kids", "Podcasts", "Audiobooks",
]
AVOID = ["type beat", "free for profit", "freestyle beat"]
NOT_DANCE = ["Metal", "Punk", "Rock", "Blues", "Spoken Word", "Podcasts", "Audiobooks", "Comedy", "Kids", "Classical", "Ambient",
             "Country", "Devotional", "Acoustic", "Soundtrack"]

DEFAULTS = [
    {"key": "house", "name": "House / tech house", "bpm": [118, 128],
     "audius": ["House", "Tech House", "Deep House"], "keywords": [], "exclude": [], "avoid": [], "octaves": False},
    {"key": "techno", "name": "Techno", "bpm": [125, 140],
     "audius": ["Techno"], "keywords": [], "exclude": [], "avoid": [], "octaves": False},
    {"key": "trance", "name": "Trance / progressive", "bpm": [128, 140],
     "audius": ["Trance", "Progressive House"], "keywords": [], "exclude": [], "avoid": [], "octaves": False},
    {"key": "dnb", "name": "Drum & bass", "bpm": [165, 178],
     "audius": ["Drum & Bass", "Jungle"], "keywords": [], "exclude": [], "avoid": [], "octaves": False},
    # Southwest Asian & North African: Arabic / oriental house, Persian and Turkish club music
    {"key": "swana_house", "name": "SWANA house (Arabic, oriental, Persian, Turkish)", "bpm": [118, 126],
     "audius": ["Electronic", "House", "Deep House", "Tech House", "Afro House", "Downtempo"],
     "keywords": ["arabic house", "oriental house", "arabic", "egyptian", "persian", "turkish", "maghreb"],
     "exclude": NOT_DANCE, "avoid": AVOID, "octaves": False},
    # dabke (Levant), shaabi and mahraganat (Egypt), raï (Maghreb), khaleeji (Gulf)
    {"key": "swana_dance", "name": "SWANA dance (dabke, shaabi, raï, mahraganat, khaleeji)", "bpm": [95, 135],
     "audius": [], "keywords": ["dabke", "shaabi", "chaabi", "mahraganat", "raï", "khaleeji", "arabic", "arab",
                               "egyptian", "algerian", "maghreb", "middle eastern"],
     "exclude": NOT_DANCE, "avoid": AVOID, "octaves": True},
    {"key": "bhangra", "name": "Bhangra (dhol, Punjabi)", "bpm": [90, 110],
     "audius": [], "keywords": ["bhangra", "dhol", "punjabi", "giddha"], "exclude": NOT_DANCE, "avoid": AVOID, "octaves": True},
    {"key": "desi_club", "name": "Desi club (Bollywood, Punjabi, desi house)", "bpm": [118, 130],
     "audius": [], "keywords": ["bollywood", "desi", "punjabi", "bhangra", "hindi", "indian", "pakistani", "tamil"],
     "exclude": NOT_DANCE, "avoid": AVOID, "octaves": True},
]


def all_genres(settings: dict) -> list[dict]:
    """The user's genres (saved), or sfere's."""
    g = settings.get("music", {}).get("genres")
    return g if isinstance(g, list) and g else copy.deepcopy(DEFAULTS)


def find(settings: dict, key: str) -> dict:
    gs = all_genres(settings)
    return next((g for g in gs if g.get("key") == key), gs[0])


def save(settings: dict, genres: list[dict]) -> None:
    settings.setdefault("music", {})["genres"] = genres


def clean(g: dict) -> dict:
    """A genre with sensible fields (from the editor or an agent)."""
    lo, hi = sorted(float(x) for x in (g.get("bpm") or [120, 130])[:2])
    lo, hi = max(50.0, lo), min(220.0, max(hi, lo + 2))
    words = g.get("keywords") or []
    if isinstance(words, str):
        words = words.split(",")
    return {"key": g.get("key") or new_key(g.get("name", "genre"), []), "name": (g.get("name") or "New genre").strip(),
            "bpm": [round(lo), round(hi)],
            "audius": [x for x in (g.get("audius") or []) if x in AUDIUS_GENRES],
            "keywords": [w.strip() for w in words if w.strip()],
            "exclude": [x for x in (g.get("exclude") or []) if x in AUDIUS_GENRES],
            "avoid": [w.strip() for w in (g.get("avoid") or []) if w.strip()],
            "octaves": bool(g.get("octaves", False))}


def new_key(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "genre"
    key, i = base, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


def search_plan(g: dict) -> list[tuple[str | None, str]]:
    """(Audius genre filter, query) searches that find this genre's tracks: each keyword
    (results then kept by `keep`), or each Audius genre when there are no keywords."""
    if g.get("keywords"):
        return [(None, w) for w in g["keywords"][:12]]
    return [(a, "") for a in (g.get("audius") or [None])[:10]]


def bpm_ranges(g: dict, margin: float) -> list[tuple[float, float]]:
    """Tag ranges to search: the genre's range with room to stretch, and half and double
    of it when tracks may be tagged in another octave."""
    lo, hi = g["bpm"][0] * (1 - margin), g["bpm"][1] * (1 + margin)
    out = [(lo, hi)]
    if g.get("octaves"):
        out += [(lo / 2, hi / 2), (lo * 2, min(hi * 2, 250.0))]
    return out


def _plain(text: str) -> str:
    """Lower case without accents: raï = rai, Bollywood = bollywood."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "").lower() if not unicodedata.combining(c))


def _mentions_in(track: dict, words: list[str], fields) -> bool:
    text = _plain(" ".join(str(track.get(k) or "") for k in fields))
    return any(re.search(rf"(?<![a-z0-9]){re.escape(_plain(w))}(?![a-z0-9])", text) for w in words)


def mentions(track: dict, words: list[str]) -> bool:
    """Whether a keyword appears as a whole word in the track's title, tags, description or
    artist. Audius's search also matches word beginnings ("desi" finds "desire", "rai" finds
    "rain"), so its results are checked again here."""
    user = track.get("user") or {}
    text = _plain(" ".join(str(track.get(k) or "") for k in ("title", "tags", "description"))
                  + " " + str(user.get("name") or "") + " " + str(user.get("handle") or ""))
    return any(re.search(rf"(?<![a-z0-9]){re.escape(_plain(w))}(?![a-z0-9])", text) for w in words)


def keep(g: dict, track: dict) -> bool:
    """Whether a search result belongs: not an excluded genre; with keywords, mentioning one
    of them, and in the listed Audius genres (when any are listed)."""
    genre = track.get("genre") or ""
    if genre in (g.get("exclude") or []):
        return False
    if g.get("avoid") and _mentions_in(track, g["avoid"], ("title", "tags")):
        return False
    if g.get("keywords"):
        if not mentions(track, g["keywords"]):
            return False
        if g.get("audius"):
            return genre in g["audius"]
    return True


def in_octave(g: dict, bpm: float, margin: float) -> float:
    """A track's tempo counted in the genre's octave (only moved when `octaves` is on)."""
    lo, hi = g["bpm"][0] * (1 - margin), g["bpm"][1] * (1 + margin)
    if lo <= bpm <= hi or not g.get("octaves"):
        return bpm
    for m in (2.0, 0.5):
        if lo <= bpm * m <= hi:
            return bpm * m
    return bpm
