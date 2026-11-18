"""A DJ set driven by the ball: Audius tracks near the movement's tempo, mixed by sfere.

The session owns a worker thread (network, decoding, stretching; nothing here may block the
render loop or the audio callback) and the mixer (dj.Engine), which plays through a
StreamSource so sfere's analyser hears exactly the set.

The worker, twice a second:
- keeps a pool of candidate tracks around the target tempo (Audius search by genre and BPM);
- plans the next tracks (picker.choose) — the set's playlist, shown in the Music tab;
- with nothing playing, prepares and starts the first track;
- about two minutes before the playing track's mix point, prepares the next one at the
  playing tempo and schedules the transition on that bar;
- when the movement's tempo has moved away from the playing tempo, re-renders the rest of
  the playing track a step closer (at most 2 % per 8 bars) and splices it in on a bar; the
  next track is then prepared at the new tempo.

Audio files live in a session folder that is deleted when the set stops (Audius allows
session caching only). The set list is saved to ~/Music/sfere as .m3u and .json.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from . import genres as genres_mod
from .audius import Audius, AudiusError, describe
from .dj import Deck, Engine
from .picker import choose, stretch_needed
from .prepare import SR, PrepareError, prepare, rerender

SETLISTS = Path.home() / "Music" / "sfere"
PREPARE_AHEAD = 120.0      # seconds before a mix point to start preparing the next track
MIN_GRID_CONFIDENCE = 0.25


class Session:
    def __init__(self, audio_engine, motion, cfg: dict, play_audio: bool = True, audius: Audius | None = None,
                 genre: dict | None = None):
        self.audio_engine, self.motion, self.cfg = audio_engine, motion, cfg
        self.genre = genre or genres_mod.DEFAULTS[1]
        self._genre_changed = 0.0
        self.play_audio = play_audio
        self.audius = audius or Audius()
        self.engine = Engine(volume=float(cfg.get("volume", 1.0)))
        self.engine.on_change = self._on_change
        self.status = "starting"
        self.error = ""
        self.pool: dict[str, dict] = {}
        self.pool_center = 0.0
        self.bad: set[str] = set()
        self.planned: list[dict] = []
        self.history: list[dict] = []          # the set list: tracks as they started
        self.next_deck: Deck | None = None
        self.preparing = ""
        self.debug = ""
        self._last_splice = 0.0
        self._stop = threading.Event()
        self._skip = threading.Event()
        self.folder = Path(tempfile.mkdtemp(prefix="sfere-music-", dir=_runtime_dir()))
        self.source = None
        self._muted: list = []
        self._thread = threading.Thread(target=self._run, name="music", daemon=True)

    # ----------------------------------------------------------------- public (main thread)
    def start(self) -> "Session":
        from ..audio.sources import StreamSource
        # the set replaces what sfere listens to: other apps' audio would include the set twice
        for sid, src in list(self.audio_engine.sources.items()):
            if src.kind in ("system", "file"):
                src.muted = True
                self._muted.append(src)
        self.source = self.audio_engine.add(StreamSource("dj", "DJ set", self.engine.render,
                                                         play_audio=self.play_audio))
        self._thread.start()
        return self

    def set_genre(self, genre: dict) -> None:
        """Change the genre mid-set: the pool is rebuilt once edits have paused for a moment."""
        self.genre = genre
        self._genre_changed = time.monotonic()

    def skip(self) -> None:
        self._skip.set()

    def stop(self) -> Path | None:
        """Stop now; the worker and the temp folder are cleaned up in the background."""
        self._stop.set()
        if self.source:
            self.audio_engine.disable(self.source.id)
        for src in self._muted:
            src.muted = False
        saved = self._save_setlist()
        threading.Thread(target=self._cleanup, daemon=True).start()
        return saved

    @property
    def playing(self) -> dict | None:
        d = self.engine.deck
        return d.prep.meta if d else None

    @property
    def tempo(self) -> float:
        d = self.engine.deck
        return d.tempo if d else 0.0

    def queue(self) -> list[dict]:
        """Playing, next (prepared), then the planned picks: the set's playlist."""
        out = []
        if self.playing:
            out.append({**self.playing, "state": "playing"})
        if self.next_deck:
            out.append({**self.next_deck.prep.meta, "state": "next"})
        out += [{**t, "state": "planned"} for t in self.planned]
        return out

    # ----------------------------------------------------------------- worker
    def _run(self):
        while not self._stop.is_set():
            try:
                self._step()
                self.error = ""
            except (AudiusError, PrepareError, OSError) as e:
                self.error = str(e)
            except Exception as e:      # keep the set alive; report
                self.error = f"{type(e).__name__}: {e}"
            self._stop.wait(0.5)

    @property
    def target(self) -> float:
        return float(self.motion.target_bpm)

    @property
    def max_stretch(self) -> float:
        return float(self.cfg.get("max_stretch", 0.08))

    @property
    def mix_bars(self) -> int:
        return int(self.cfg.get("mix_bars", 16))

    def _step(self):
        target = self.target
        self._refresh_pool(target)
        deck = self.engine.deck
        if deck and self.engine.mix is None and self.engine.clock >= deck.end:
            self.engine.stop_deck()               # it played out with nothing ready: start afresh
            deck = None
        tempo = deck.tempo if deck else target
        taken = {h["id"] for h in self.history} | self.bad
        if self.next_deck:
            taken.add(self.next_deck.prep.meta["id"])
        prev = self.next_deck.prep.meta if self.next_deck else self.playing
        self.planned = choose(list(self.pool.values()), target, prev, taken, n=3, max_stretch=self.max_stretch)
        if deck is None:
            self.status = "finding the first track"
            first = self._prepare_first(target)
            if first:
                start = self.engine.clock + int(0.3 * SR)
                d = Deck.load(first, start)
                self.engine.play(d)
                self._started(d)
            return
        clock = self.engine.clock
        mix_at = deck.clock_of(deck.prep.mix_out)
        if self._skip.is_set() and self.engine.mix is None:
            self._skip.clear()
            mix_at = deck.clock_of(deck.next_bar(clock, ahead_s=1.0))
            if self.next_deck is None:
                self.next_deck = self._prepare_next(deck)
            if self.next_deck:
                self._schedule(deck, self.next_deck, mix_at, bars=4)
            return
        self.status = "mixing" if self.engine.mix else "playing"
        # follow the movement: nudge the playing tempo toward the target, bar by bar
        if (self.cfg.get("follow_movement", True) and self.engine.mix is None
                and stretch_needed(tempo, target) > 0.005 and time.monotonic() - self._last_splice > 8 * 4 * 60 / tempo):
            if self._nudge(deck, target, mix_at):
                return
            mix_at = deck.clock_of(deck.prep.mix_out)
        if self.next_deck is None and (mix_at - clock) / SR < PREPARE_AHEAD:
            self.next_deck = self._prepare_next(deck)
        nd = self.next_deck
        if nd and self.engine.mix is None:
            if abs(nd.tempo - deck.tempo) > 0.05:          # the tempo moved since it was prepared
                self.next_deck = self._prepare_next(deck)
                return
            if nd.start != mix_at:
                self._schedule(deck, nd, mix_at, self.mix_bars)

    def _refresh_pool(self, target: float):
        if self._genre_changed:
            if time.monotonic() - self._genre_changed < 1.5:
                return                           # still being edited
            self._genre_changed, self.pool = 0.0, {}
        if self.pool and abs(target / self.pool_center - 1) < 0.02 and len(self.pool) >= 20:
            return
        g, m = self.genre, self.max_stretch
        # around the target, plus its other octaves when the genre's tracks may be tagged there
        ranges = [(target * (1 - m), target * (1 + m))]
        if g.get("octaves"):
            ranges += [(lo / 2, hi / 2) for lo, hi in ranges] + [(lo * 2, hi * 2) for lo, hi in ranges]
        found = {}
        offsets = (0, 50) if len(self.pool) < 20 else (0,)
        # every keyword at the target's own range first; other octaves and later pages after
        plan = genres_mod.search_plan(g)
        calls = [(a, q, *ranges[0], 0) for a, q in plan]
        calls += [(a, q, lo, hi, 0) for lo, hi in ranges[1:] for a, q in plan]
        calls += [(a, q, *ranges[0], off) for off in offsets[1:] for a, q in plan]
        for audius_genre, query, lo, hi, offset in calls[:24]:      # a refresh stays a few seconds
            for t in self.audius.search(audius_genre or "", lo, hi, limit=50, offset=offset, query=query):
                if genres_mod.keep(g, t):
                    meta = describe(t)
                    meta["bpm"] = genres_mod.in_octave(g, meta["bpm"], m)
                    found[t["id"]] = meta
        if found:
            self.pool.update(found)
        self.pool_center = target

    def _prepare(self, meta: dict, tempo: float):
        self.preparing = f"{meta['artist']} – {meta['title']}"
        try:
            p = prepare(meta, self.audius.stream_url(meta["id"]), self.folder, tempo, self.mix_bars)
        except PrepareError as e:
            self.bad.add(meta["id"])
            self.error = f"skipped {self.preparing}: {e}"
            return None
        finally:
            self.preparing = ""
        if p.grid.confidence < MIN_GRID_CONFIDENCE:     # its beats can't be placed well enough to mix
            self.bad.add(meta["id"])
            return None
        return p

    def _prepare_first(self, target: float):
        for meta in self.planned:
            p = self._prepare(meta, target)
            if p:
                return p
        return None

    def _prepare_next(self, deck: Deck) -> Deck | None:
        for meta in self.planned:
            if stretch_needed(meta["bpm"], deck.tempo) > self.max_stretch:
                continue
            p = self._prepare(meta, deck.tempo)
            if p:
                return Deck.load(p, start=0)
        return None

    def _schedule(self, out: Deck, inn: Deck, at: int, bars: int):
        if at - self.engine.clock < int(0.2 * SR):
            return
        inn.start = at
        self.engine.schedule(out, inn, at, bars)

    def _nudge(self, deck: Deck, target: float, mix_at: int) -> bool:
        """Re-render the rest of the playing track a step nearer the target tempo. False if
        it's too near the next mix (the next pick follows the target instead)."""
        step = max(-0.02, min(0.02, target / deck.tempo - 1.0))
        new_tempo = deck.tempo * (1 + step)
        # far enough ahead that re-rendering (about 2 s per 2 minutes left) finishes before it
        left = (deck.end - self.engine.clock) / SR
        orig = deck.next_bar(self.engine.clock, ahead_s=4.0 + 0.03 * left)
        at = deck.clock_of(orig)
        self.debug = f"nudge: at {(at - self.engine.clock) / SR:.1f}s ahead, mix in {(mix_at - self.engine.clock) / SR:.1f}s"
        if at > mix_at - int(self.mix_bars * 4 * 60 / deck.tempo * SR):
            return False
        pcm = rerender(deck.prep, orig, new_tempo)
        ratio = new_tempo / deck.prep.grid.bpm
        if not self.engine.splice(deck, at, orig, ratio, pcm):
            return False
        self._last_splice = time.monotonic()
        if self.next_deck:                       # it was prepared at the old tempo
            self.engine.cancel_mix()
            self.next_deck = None
        return True

    # ----------------------------------------------------------------- bookkeeping
    def _on_change(self, deck: Deck):
        """A transition finished (audio thread): the incoming track is now the one playing."""
        self.next_deck = None
        self._started(deck)

    def _started(self, deck: Deck):
        self.history.append({**deck.prep.meta, "started": time.strftime("%H:%M:%S"),
                             "tempo": round(deck.tempo, 2)})

    def _save_setlist(self) -> Path | None:
        if not self.history:
            return None
        SETLISTS.mkdir(parents=True, exist_ok=True)
        base = SETLISTS / f"sfere-set-{time.strftime('%Y%m%d-%H%M%S')}"
        lines = ["#EXTM3U"]
        for t in self.history:
            lines += [f"#EXTINF:{int(t['duration'])},{t['artist']} - {t['title']}", t["link"]]
        Path(f"{base}.m3u").write_text("\n".join(lines) + "\n")
        Path(f"{base}.json").write_text(json.dumps({"genre": self.genre.get("name"), "tracks": self.history}, indent=1))
        return Path(f"{base}.m3u")

    def _cleanup(self):
        self._thread.join(timeout=60)
        shutil.rmtree(self.folder, ignore_errors=True)


def _runtime_dir() -> str | None:
    import os
    d = os.environ.get("XDG_RUNTIME_DIR")
    return d if d and os.path.isdir(d) else None
