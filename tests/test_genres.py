"""Editable genres: cleaning, search plans, keeping results, octaves, and the session using them."""
from __future__ import annotations

from sfere.motion_tempo import MotionTempo
from sfere.music import genres


def test_defaults_include_swana_and_south_asian_dance_music():
    keys = {g["key"] for g in genres.DEFAULTS}
    assert {"house", "techno", "trance", "dnb", "swana_house", "swana_dance", "bhangra", "desi_club"} <= keys
    for g in genres.DEFAULTS:
        assert genres.clean(g) == g, g["key"]                 # the defaults are already clean
        assert set(g["audius"]) <= set(genres.AUDIUS_GENRES) and set(g["exclude"]) <= set(genres.AUDIUS_GENRES)


def test_clean_and_new_keys():
    g = genres.clean({"name": " Mahraganat ", "bpm": [135, 110], "keywords": "mahraganat, , shaabi ",
                      "audius": ["Electronic", "Not A Genre"], "octaves": 1})
    assert g["name"] == "Mahraganat" and g["bpm"] == [110, 135] and g["keywords"] == ["mahraganat", "shaabi"]
    assert g["audius"] == ["Electronic"] and g["octaves"] is True and g["key"] == "mahraganat"
    assert genres.new_key("Desi Club", ["desi_club"]) == "desi_club_2"


def test_search_plan_keep_and_octaves():
    bh = genres.find({"music": {}}, "bhangra")
    assert genres.search_plan(bh) == [(None, w) for w in ("bhangra", "dhol", "punjabi", "giddha")]
    assert genres.search_plan(genres.find({"music": {}}, "techno")) == [("Techno", "")]
    assert not genres.keep(bh, {"genre": "Metal", "title": "Bhangra"})
    assert genres.keep(bh, {"genre": "Hip-Hop/Rap", "title": "Bombay to Punjab", "tags": "punjabi,desi"})
    assert not genres.keep(bh, {"genre": "Hip-Hop/Rap", "title": "Brooklyn hiphop"})       # no keyword at all
    sh = genres.find({"music": {}}, "swana_house")
    assert genres.keep(sh, {"genre": "Deep House", "title": "Arabic sunset"})
    assert not genres.keep(sh, {"genre": "Hip-Hop/Rap", "title": "Arabic sunset"})
    # Audius's search matches word beginnings; sfere checks whole words, accents aside
    desi = genres.find({"music": {}}, "desi_club")
    assert not genres.keep(desi, {"genre": "Pop", "title": "Freed From Desire"})
    sw = genres.find({"music": {}}, "swana_dance")
    assert not genres.keep(sw, {"genre": "Deep House", "title": "Feathers in the Rain"})
    assert genres.keep(sw, {"genre": "World", "title": "Rai Bladi", "user": {"name": "Cheb X"}})
    assert not genres.keep(sw, {"genre": "Hip-Hop/Rap", "title": "Arabic Rap Type Beat - Sahara"})
    assert genres.in_octave(bh, 50.0, 0.08) == 100.0 and genres.in_octave(bh, 100.0, 0.08) == 100.0
    assert genres.in_octave(genres.find({"music": {}}, "techno"), 65.0, 0.08) == 65.0     # octaves off
    assert genres.bpm_ranges(bh, 0.0)[1] == (45.0, 55.0)


def test_saved_genres_replace_the_defaults_and_motion_uses_their_range():
    mine = [genres.clean({"key": "dabke", "name": "Dabke", "bpm": [100, 120], "keywords": ["dabke"]})]
    settings = {"music": {}}
    genres.save(settings, mine)
    assert genres.all_genres(settings) == mine and genres.find(settings, "missing")["key"] == "dabke"
    mt = MotionTempo(mine[0])
    assert mt.range == (100.0, 120.0) and mt.target_bpm == 110
    mt.set_genre(genres.find({"music": {}}, "dnb"))
    assert mt.range == (165.0, 178.0) and 165 <= mt.target_bpm <= 178
