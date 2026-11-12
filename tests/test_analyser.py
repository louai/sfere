"""M3 acceptance for the analyser, offline on the sample track (PLAN.md §7)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sfere.audio.analyser import analyse_file

MUSIC = Path(__file__).resolve().parents[1] / "sample music v2.wav"


def load():
    x, sr = sf.read(MUSIC, dtype="float32", always_2d=True)
    return x.mean(axis=1), sr


def check(a, label):
    lock_t = next((t for t, b in a.bpm_track if a.locked and abs(b - 123) <= 1), None)
    final = a.bpm
    beats = np.array(a.beat_times)
    grid_beats = beats[beats > (lock_t or 0)]
    kicks = np.array([k for k in a.kick_times if lock_t is not None and k > lock_t + 0.25])
    near = [np.min(np.abs(grid_beats - k)) <= 0.05 for k in kicks] if len(grid_beats) else []
    frac = float(np.mean(near)) if near else 0.0
    print(f"\n{label}: bpm {final:.2f}, first within ±1 BPM at {lock_t}, "
          f"{len(kicks)} kicks after lock, {frac:.0%} within 50 ms of a grid beat, onsets {len(a.onset_times)}")
    return lock_t, final, frac, len(kicks)


@pytest.mark.skipif(not MUSIC.exists(), reason="sample music not present")
@pytest.mark.parametrize("gain_db", [0, -20])
def test_tempo_and_kick_aligned_grid(gain_db):
    x, sr = load()
    a = analyse_file(x * 10 ** (gain_db / 20), sr)
    lock_t, bpm, frac, nk = check(a, f"{gain_db:+d} dB")
    assert lock_t is not None and lock_t <= 6.0
    assert abs(bpm - 123) <= 1
    assert nk >= 6
    assert frac >= 0.9
    # the estimate never wanders after locking
    after = [b for t, b in a.bpm_track if t > lock_t]
    assert all(abs(b - 123) <= 1 for b in after)


@pytest.mark.skipif(not MUSIC.exists(), reason="sample music not present")
def test_bands_and_spectrum_move():
    x, sr = load()
    a = analyse_file(x, sr)
    st = a.state()
    assert 0 <= st.sub <= 1 and 0 <= st.high <= 1
    assert st.spectrum.shape == (64,) and st.spectrum.max() > 0.3
