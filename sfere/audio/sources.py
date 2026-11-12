"""Audio sources: other applications' audio (PipeWire/PulseAudio monitors), inputs (mics,
line-in) and a file player for development, mixed with a gain per source (PLAN.md §4.1).

Every source runs in its own thread and writes into its own FIFO; the mixer thread sums
whatever all live sources have delivered and feeds the analyser. A source that stops
delivering (unplugged) is ignored by the mixer and reconnects on its own.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .analyser import Analyser

SR = 48000
BLOCK = 480                     # 10 ms capture blocks
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"
STALE_S = 0.15


@dataclass
class SourceInfo:
    id: str
    name: str
    kind: str  # "system" | "input" | "file"


def list_sources() -> list[SourceInfo]:
    import soundcard as sc

    out = [SourceInfo(DEFAULT_MONITOR, "Default output (follows device changes)", "system")]
    try:
        for m in sc.all_microphones(include_loopback=True):
            out.append(SourceInfo(m.id, m.name, "system" if m.isloopback else "input"))
    except Exception:
        pass
    return out


class _Fifo:
    def __init__(self, seconds: float = 1.0):
        self.buf = np.zeros(int(SR * seconds), np.float32)
        self.n = 0
        self.lock = threading.Lock()
        self.last_push = 0.0

    def push(self, x: np.ndarray) -> None:
        with self.lock:
            x = x[-len(self.buf):]
            if self.n + len(x) > len(self.buf):  # overflow: drop the oldest
                drop = self.n + len(x) - len(self.buf)
                self.buf[:self.n - drop] = self.buf[drop:self.n]
                self.n -= drop
            self.buf[self.n:self.n + len(x)] = x
            self.n += len(x)
            self.last_push = time.monotonic()

    def take(self, k: int) -> np.ndarray:
        with self.lock:
            k = min(k, self.n)
            out = self.buf[:k].copy()
            self.buf[:self.n - k] = self.buf[k:self.n]
            self.n -= k
            return out

    def clear(self) -> None:
        with self.lock:
            self.n = 0


class Source:
    kind = "input"

    def __init__(self, id: str, name: str = "", gain: float = 1.0):
        self.id, self.name, self.gain = id, name or id, gain
        self.fifo = _Fifo()
        self.peak_db = -120.0
        self.status = "starting"
        self.muted = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"audio:{id}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self, join: bool = True):
        self._stop.set()
        if join and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.5)   # close the recorder before the interpreter exits

    def _meter(self, x: np.ndarray):
        p = float(np.abs(x).max()) if len(x) else 0.0
        db = 20 * np.log10(p + 1e-9)
        self.peak_db = max(db, self.peak_db - 1.5)  # falls ~150 dB/s at 100 blocks/s

    def live(self) -> bool:
        return time.monotonic() - self.fifo.last_push < STALE_S

    _dc = 0.0
    _flat_blocks = 0

    def _dc_block(self, x: np.ndarray) -> np.ndarray:
        """Removes a DC offset some inputs carry (running mean over ~0.2 s, vectorized)."""
        self._dc += 0.05 * (float(x.mean()) - self._dc) if len(x) else 0.0
        return x - np.float32(self._dc)

    def _stuck_check(self, x: np.ndarray) -> bool:
        """A signal pinned at one value (e.g. a muted digital mic reading -1.0) is no audio."""
        if len(x) and float(x.std()) < 1e-5 and abs(float(x.mean())) > 0.01:
            self._flat_blocks += 1
        else:
            self._flat_blocks = 0
            if self.status.startswith("no signal"):
                self.status = "running"
        if self._flat_blocks > 50:  # 0.5 s
            self.status = f"no signal (stuck at {float(x.mean()):+.2f}; check mute/levels)"
            return True
        return False


class CaptureSource(Source):
    """A PulseAudio/PipeWire source: a microphone, line-in, or an output's monitor."""

    def __init__(self, id: str, name: str = "", gain: float = 1.0, kind: str = "input"):
        super().__init__(id, name, gain)
        self.kind = kind
        self.resolved = None

    def _resolve(self):
        import soundcard as sc

        if self.id == DEFAULT_MONITOR:
            spk = sc.default_speaker()
            for m in sc.all_microphones(include_loopback=True):
                if m.isloopback and m.id == spk.id + ".monitor":
                    return m
            return next(m for m in sc.all_microphones(include_loopback=True)
                        if m.isloopback and m.id.startswith(spk.id))
        return sc.get_microphone(self.id, include_loopback=True)

    def _run(self):
        import soundcard as sc  # noqa: F401

        while not self._stop.is_set():
            try:
                mic = self._resolve()
                self.resolved = mic.id
                self.name = self.name if self.id != DEFAULT_MONITOR else f"Default output → {mic.name}"
                ch = min(2, mic.channels) if isinstance(mic.channels, int) else 1
                with mic.recorder(samplerate=SR, channels=ch, blocksize=BLOCK) as rec:
                    self.status = "running"
                    last_check = time.monotonic()
                    while not self._stop.is_set():
                        data = rec.record(numframes=BLOCK)
                        x = data.mean(axis=1).astype(np.float32) if data.ndim == 2 else data.astype(np.float32)
                        self._meter(x)
                        if self._stuck_check(x):
                            x = np.zeros_like(x)  # keep the clock running, contribute silence
                        self.fifo.push(self._dc_block(x))
                        # the default output moved (e.g. to HDMI): reopen on the new monitor
                        if self.id == DEFAULT_MONITOR and time.monotonic() - last_check > 2.0:
                            last_check = time.monotonic()
                            if self._resolve().id != self.resolved:
                                break
            except Exception as e:  # unplugged, server restart...
                self.status = f"disconnected ({type(e).__name__})"
                self.peak_db = -120.0
                self._stop.wait(2.0)


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    X = np.fft.rfft(x, axis=0)
    keep = n_out // 2 + 1
    Y = np.zeros((keep,) + X.shape[1:], X.dtype)
    m = min(keep, X.shape[0])
    Y[:m] = X[:m]
    return (np.fft.irfft(Y, n_out, axis=0) * (n_out / len(x))).astype(np.float32)


class FileSource(Source):
    """Plays a file to the speakers and analyses exactly the samples being played."""

    kind = "file"

    def __init__(self, path: str, gain: float = 1.0, loop: bool = True, play_audio: bool = True):
        super().__init__(f"file:{path}", path.split("/")[-1], gain)
        self.path, self.loop, self.play_audio = path, loop, play_audio
        self.position = 0
        self.paused = False

    def _run(self):
        import sounddevice as sd
        import soundfile as sf

        try:
            data, sr = sf.read(self.path, dtype="float32", always_2d=True)
            data = _resample(data, sr, SR)
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            data = data[:, :2]
        except Exception as e:
            self.status = f"error: {e}"
            return
        self.length = len(data)

        def chunk(frames):
            out = np.zeros((frames, 2), np.float32)
            if self.paused:
                return out
            i = 0
            while i < frames:
                take = min(frames - i, len(data) - self.position)
                if take <= 0:
                    if not self.loop:
                        break
                    self.position = 0
                    continue
                out[i:i + take] = data[self.position:self.position + take]
                self.position += take
                i += take
            return out

        if self.play_audio:
            def callback(outdata, frames, _time, _status):
                out = chunk(frames)
                outdata[:] = out
                mono = out.mean(axis=1)
                self._meter(mono)
                self.fifo.push(mono)

            try:
                with sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK, callback=callback):
                    self.status = "playing"
                    while not self._stop.wait(0.1):
                        pass
            except Exception as e:
                self.status = f"error: {e}"
        else:  # silent: paced by the clock (tests, headless)
            self.status = "playing (silent)"
            next_t = time.monotonic()
            while not self._stop.is_set():
                mono = chunk(BLOCK).mean(axis=1)
                self._meter(mono)
                self.fifo.push(mono)
                next_t += BLOCK / SR
                self._stop.wait(max(0.0, next_t - time.monotonic()))


class AudioEngine:
    """Owns the sources, the mixer thread and the analyser."""

    def __init__(self, latency_ms: float = 0.0):
        self.analyser = Analyser(sr=SR)
        self.analyser.latency_s = latency_ms / 1000.0
        self.sources: dict[str, Source] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._mix, name="audio:mixer", daemon=True)
        self._thread.start()

    # -- control ---------------------------------------------------------------------
    def enable(self, id: str, gain: float = 1.0, name: str = "", kind: str = "input") -> Source:
        with self._lock:
            if id in self.sources:
                self.sources[id].gain = gain
                return self.sources[id]
            src = CaptureSource(id, name, gain, kind).start()
            self.sources[id] = src
            return src

    def disable(self, id: str) -> None:
        with self._lock:
            src = self.sources.pop(id, None)
        if src:
            src.stop()

    def play_file(self, path: str, play_audio: bool = True, loop: bool = True, gain: float = 1.0) -> FileSource:
        for id in [k for k, s in self.sources.items() if s.kind == "file"]:
            self.disable(id)
        src = FileSource(path, gain, loop, play_audio).start()
        with self._lock:
            self.sources[src.id] = src
        return src

    def close(self) -> None:
        self._stop.set()
        for s in list(self.sources.values()):
            s.stop(join=False)
        for s in list(self.sources.values()):
            s.stop()
        self._thread.join(timeout=1.0)

    # -- mixer -----------------------------------------------------------------------
    def _mix(self) -> None:
        while not self._stop.wait(0.005):
            with self._lock:
                srcs = list(self.sources.values())
            live = [s for s in srcs if s.live()]
            for s in srcs:
                if not s.live():
                    s.fifo.clear()
            if not live:
                continue
            n = min(s.fifo.n for s in live)
            if n < BLOCK // 2:
                continue
            mix = np.zeros(n, np.float32)
            for s in live:
                x = s.fifo.take(n)
                if not s.muted:
                    mix[:len(x)] += x * s.gain
            self.analyser.feed(mix, wall=time.monotonic())

    def state(self, wall: float | None = None):
        return self.analyser.state(wall)
