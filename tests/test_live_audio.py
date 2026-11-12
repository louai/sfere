"""Live M3 check (opt-in, makes sound): another app plays the sample, sfere listens to system audio.

    SFERE_LIVE=1 .venv/bin/python -m pytest tests/test_live_audio.py -s
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sfere.audio.sources import DEFAULT_MONITOR, AudioEngine

MUSIC = Path(__file__).resolve().parents[1] / "sample music v2.wav"
pytestmark = pytest.mark.skipif(os.environ.get("SFERE_LIVE") != "1" or not shutil.which("pw-play"),
                                reason="live audio test: set SFERE_LIVE=1")


def test_system_audio_plus_mic_tempo():
    eng = AudioEngine()
    sysa = eng.enable(DEFAULT_MONITOR, kind="system")
    mic = eng.enable("alsa_input.usb-046d_HD_Pro_Webcam_C920_113BEA3F-02.analog-stereo", kind="input")
    time.sleep(0.8)
    player = subprocess.Popen(["pw-play", "--volume=0.3", str(MUSIC)])
    t0, lock = time.monotonic(), None
    try:
        while player.poll() is None and time.monotonic() - t0 < 12:
            time.sleep(0.2)
            st = eng.state(time.monotonic())
            if st.locked and lock is None and abs(st.bpm - 123) <= 1:
                lock = time.monotonic() - t0
    finally:
        player.kill()
        eng.close()
    print(f"\nsystem audio: {sysa.status} peak {sysa.peak_db:.1f} dB | mic: {mic.status} | "
          f"bpm {st.bpm:.2f}, within ±1 of 123 after {lock}s, beats {st.beat_count}")
    assert sysa.status == "running" and sysa.peak_db > -60
    assert lock is not None and lock <= 6.5   # 6 s of music after ~0.5 s player start-up
    assert abs(st.bpm - 123) <= 1
