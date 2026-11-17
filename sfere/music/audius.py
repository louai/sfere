"""Audius: search tracks by genre and BPM range, and stream them (https://docs.audius.org).

The public API needs no account: every call carries app_name=sfere. Search filters on the
server by genre and BPM range; only tracks anyone may stream (not gated, not unlisted, not
deleted) with a known BPM and a DJ-friendly length are kept.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

API = "https://api.audius.co"
APP = "sfere"
MIN_S, MAX_S = 120, 12 * 60


class AudiusError(Exception):
    pass


def _get(url: str, timeout: float = 15.0, tries: int = 3) -> dict:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sfere", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise AudiusError(f"Audius said {e.code} for {url.split('?')[0]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise AudiusError(f"can't reach Audius: {e}") from e
    raise AudiusError("can't reach Audius")


class Audius:
    def __init__(self, base: str | None = None):
        self._base = base

    @property
    def base(self) -> str:
        if self._base is None:
            try:   # the API names a host to use; api.audius.co itself is one
                self._base = (_get(API, timeout=8.0, tries=1).get("data") or [API])[0]
            except AudiusError:
                self._base = API
        return self._base.rstrip("/")

    def url(self, path: str, **params) -> str:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["app_name"] = APP
        return f"{self.base}/v1{path}?{urllib.parse.urlencode(params)}"

    def search(self, genre: str, bpm_min: float, bpm_max: float, limit: int = 50, offset: int = 0,
               query: str = "") -> list[dict]:
        data = _get(self.url("/tracks/search", query=query, genre=genre, bpm_min=f"{bpm_min:.1f}",
                             bpm_max=f"{bpm_max:.1f}", limit=limit, offset=offset)).get("data") or []
        return [t for t in data if usable(t)]

    def stream_url(self, track_id: str) -> str:
        return self.url(f"/tracks/{track_id}/stream")


def usable(t: dict) -> bool:
    return (bool(t.get("is_streamable", True)) and not t.get("is_stream_gated") and not t.get("is_unlisted")
            and not t.get("is_delete") and bool(t.get("bpm")) and MIN_S <= float(t.get("duration") or 0) <= MAX_S)


def describe(t: dict) -> dict:
    """The fields sfere keeps about a track (shown in the Music tab and the set list)."""
    user = t.get("user") or {}
    handle = user.get("handle") or ""
    return {"id": t["id"], "title": t.get("title", "?"), "artist": user.get("name") or handle or "?",
            "bpm": float(t["bpm"]), "key": t.get("musical_key") or "", "genre": t.get("genre") or "",
            "duration": float(t.get("duration") or 0), "plays": int(t.get("play_count") or 0),
            "favorites": int(t.get("favorite_count") or 0), "license": t.get("license") or "",
            "link": f"https://audius.co{t['permalink']}" if t.get("permalink") else f"https://audius.co/{handle}"}
