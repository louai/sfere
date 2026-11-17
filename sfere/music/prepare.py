"""Getting a track ready to mix: download, decode, find its beats, stretch it to the set's tempo.

- The audio is fetched into the session folder only (Audius allows session caching, not a
  library) and decoded with ffmpeg to 48 kHz stereo.
- The beat grid comes from an onset envelope (spectral flux), seeded with the tempo Audius
  gives: autocorrelation picks the right octave and refines the period, a comb filter finds
  the phase, every beat is then snapped to its nearest onset and a straight line fitted
  through them (so the grid holds for the whole track), and the bar's first beat is the
  phase with the most low-end (kick and bass).
- Stretching keeps the pitch: ffmpeg's rubberband filter, from any beat to the end, so the
  rest of a playing track can be re-rendered at a new tempo.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
HOP = 256                       # onset envelope resolution: 5.3 ms
FRAME = 1024


class PrepareError(Exception):
    pass


def download(url: str, dest: Path, timeout: float = 30.0) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "sfere"})
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 16)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise PrepareError(f"download failed: {e}") from e
    tmp.replace(dest)
    return dest


def _ffmpeg(args: list[str], data: bytes | None = None) -> bytes:
    if not shutil.which("ffmpeg"):
        raise PrepareError("ffmpeg isn't installed")
    r = subprocess.run(["ffmpeg", "-v", "error", *args], input=data, capture_output=True)
    if r.returncode != 0:
        raise PrepareError(r.stderr.decode(errors="replace")[-300:] or "ffmpeg failed")
    return r.stdout


def decode(path: Path, start: float = 0.0) -> np.ndarray:
    """(n, 2) float32 at 48 kHz, from `start` seconds."""
    raw = _ffmpeg([*(["-ss", f"{start:.4f}"] if start > 0 else []), "-i", str(path),
                   "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"])
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def stretch(pcm: np.ndarray, ratio: float) -> np.ndarray:
    """Play `ratio` times as fast without changing pitch (1.02 = 2 % faster, shorter)."""
    if abs(ratio - 1.0) < 1e-4:
        return np.ascontiguousarray(pcm, np.float32)
    raw = _ffmpeg(["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                   "-af", f"rubberband=tempo={ratio:.6f}:pitchq=quality", "-f", "f32le", "-"],
                  np.ascontiguousarray(pcm, np.float32).tobytes())
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


# ----------------------------------------------------------------------------------- beats
def onset_envelope(mono: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(spectral flux, low-band energy) per hop."""
    n = 1 + max(0, (len(mono) - FRAME) // HOP)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = mono[np.minimum(idx, len(mono) - 1)] * np.hanning(FRAME)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    logm = np.log1p(10.0 * mag)
    flux = np.maximum(0.0, np.diff(logm, axis=0, prepend=logm[:1])).sum(axis=1)
    low_bins = int(150 * FRAME / SR) + 1                 # below ~150 Hz: kick and bass
    low = (mag[:, 1:low_bins] ** 2).sum(axis=1)
    flux -= np.convolve(flux, np.ones(32) / 32, "same")  # remove the slow loudness swell
    return np.maximum(flux, 0.0), low


@dataclass
class Grid:
    """Beat k is at `first + k * period` seconds (k may be negative); bars start where
    k % 4 == downbeat."""
    period: float
    first: float
    downbeat: int = 0
    confidence: float = 0.0

    @property
    def bpm(self) -> float:
        return 60.0 / self.period

    def beat(self, k: float) -> float:
        return self.first + k * self.period

    def bar_starts(self, duration: float) -> np.ndarray:
        k0 = self.downbeat - 4 * int(np.ceil(self.first / (4 * self.period)))
        ks = np.arange(k0, int((duration - self.first) / self.period) + 1, 4)
        t = self.first + ks * self.period
        return t[(t >= 0) & (t < duration)]


def _pulse(env: np.ndarray, t: np.ndarray, bpm: float) -> complex:
    """How strongly (and at what phase) the envelope repeats at this tempo, over the whole track."""
    return complex(np.dot(env, np.exp(-2j * np.pi * (bpm / 60.0) * t)))


def _sharpness(env: np.ndarray, t: np.ndarray, bpm: float, harmonics: int = 4) -> float:
    """How sharply the envelope repeats at this tempo: the pulse and its harmonics (a kick is a
    narrow spike, so its repetition shows at 2, 3, 4 times the rate too)."""
    return sum(abs(_pulse(env, t, h * bpm)) / h for h in range(1, harmonics + 1))


def beat_grid(pcm: np.ndarray, bpm_hint: float, sr: int = SR) -> Grid:
    """The track's tempo and beats, assuming (as dance tracks are) a steady tempo. Everything
    is read from the kick's body (onsets below ~110 Hz):
    - tempo: within ±1.5 % of Audius's tag (whose octave is kept: the set's tempo is counted
      in it), where the kicks repeat most sharply over the whole track;
    - beats and bars: the kick onsets folded over one bar; the strongest kick in the bar is
      its first beat."""
    mono = pcm.mean(axis=1) if pcm.ndim == 2 else pcm
    n = 1 + max(0, (len(mono) - FRAME) // HOP)
    fps = sr / HOP
    if n < fps * 8:
        raise PrepareError("track too short to find its beats")
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    sub = np.empty(n, np.float32)
    win = np.hanning(FRAME)
    for a in range(0, n, 4096):                       # in blocks: a whole track at once is ~1 GB
        spec = np.abs(np.fft.rfft(mono[np.minimum(idx[a:a + 4096], len(mono) - 1)] * win, axis=1))
        sub[a:a + 4096] = (spec[:, 1:3] ** 2).sum(axis=1)       # ~47 and ~94 Hz bins
    kick = np.maximum(0.0, np.diff(sub, prepend=sub[:1]))
    kick /= np.percentile(kick, 99.5) or 1.0
    t = np.arange(n) / fps + FRAME / (2 * sr)            # frames are centred
    k0 = kick - kick.mean()
    bpm = float(bpm_hint)
    for span, steps in ((0.015, 121), (0.0005, 41)):
        cands = bpm * (1 + np.linspace(-span, span, steps))
        score = np.array([_sharpness(k0, t, b) for b in cands])
        i = int(np.argmax(score))
        bpm = float(cands[i])
        if 0 < i < steps - 1:
            a, b_, c = score[i - 1], score[i], score[i + 1]
            den = a - 2 * b_ + c
            if den:
                bpm += 0.5 * (a - c) / den * (cands[1] - cands[0])
    period = 60.0 / bpm
    bar = 4 * period
    bins = 64                                          # 16 per beat
    ph = ((t / bar) % 1.0) * bins
    hist = np.bincount(ph.astype(int) % bins, weights=kick, minlength=bins)
    hist = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))          # a little smoothing
    j = int(np.argmax(hist))
    a, b_, c = hist[(j - 1) % bins], hist[j], hist[(j + 1) % bins]
    den = a - 2 * b_ + c
    peak = (j + 0.5 + (0.5 * (a - c) / den if den else 0.0)) / bins * bar   # bar start, mod bar
    first = peak - period * np.floor(peak / period)    # the earliest beat at or after 0
    downbeat = int(round((peak - first) / period)) % 4
    peakiness = float(b_ / (hist.mean() or 1.0))
    return Grid(period=period, first=float(first), downbeat=downbeat, confidence=min(1.0, peakiness / 8))


def mix_points(pcm: np.ndarray, grid: Grid, mix_bars: int, sr: int = SR) -> tuple[float, float]:
    """(mix in, mix out) in seconds of the original track: start from its first bar; start
    mixing out `mix_bars` before its outro (where the energy falls away), on a phrase."""
    duration = len(pcm) / sr
    bars = grid.bar_starts(duration)
    if len(bars) < mix_bars + 8:
        raise PrepareError("track too short to mix")
    mono = np.abs(pcm.mean(axis=1))
    energy = np.array([mono[int(a * sr):int(b * sr)].mean() for a, b in zip(bars[:-1], bars[1:])])
    loud = np.flatnonzero(energy > 0.5 * np.median(energy))
    last_loud = int(loud[-1]) + 1 if len(loud) else len(bars) - 1
    out_bar = max(mix_bars, last_loud - mix_bars // 2)
    out_bar -= out_bar % 8                                   # phrases are 8 bars
    out_bar = min(max(out_bar, mix_bars), len(bars) - 1 - mix_bars)
    return float(bars[0]), float(bars[out_bar])


@dataclass
class Prepared:
    """A track decoded, gridded and stretched to `tempo` BPM, ready for a deck.
    `pcm` starts at the original's `start` second (a bar), stretched by `ratio`."""
    meta: dict
    file: Path
    grid: Grid                     # in the original's seconds
    mix_in: float                  # original seconds
    mix_out: float
    tempo: float                   # BPM it plays at
    start: float                   # original second the pcm begins at
    pcm: np.ndarray = field(repr=False, default=None)

    @property
    def ratio(self) -> float:
        return self.tempo / self.grid.bpm

    def out_time(self, orig: float) -> float:
        """Seconds into self.pcm at which the original's second `orig` plays."""
        return (orig - self.start) / self.ratio

    def orig_time(self, out: float) -> float:
        return self.start + out * self.ratio


def prepare(meta: dict, url: str, folder: Path, tempo: float, mix_bars: int) -> Prepared:
    path = folder / f"{meta['id']}.mp3"
    if not path.exists():
        download(url, path)
    pcm = decode(path)
    grid = beat_grid(pcm, meta["bpm"])
    mix_in, mix_out = mix_points(pcm, grid, mix_bars)
    p = Prepared(meta=meta, file=path, grid=grid, mix_in=mix_in, mix_out=mix_out, tempo=tempo, start=mix_in)
    p.pcm = stretch(pcm[int(mix_in * SR):], p.ratio)
    return p


def rerender(p: Prepared, from_orig: float, tempo: float) -> np.ndarray:
    """The track from original second `from_orig` (a beat) at a new tempo."""
    pcm = decode(p.file, from_orig)
    return stretch(pcm, tempo / p.grid.bpm)
