"""sfere — run with `python -m sfere` (or the `sfere` script).

  sfere                                   webcam + audio from other apps (default output monitor)
  sfere --video sample.mp4 --audio-file music.wav   development with recordings
  sfere --audio @DEFAULT_MONITOR@ --audio alsa_input...   choose audio sources (repeatable)
  sfere --touch off                       mouse-sized controls on a touch-screen laptop
  sfere --capture out/run --duration 20   save output/control screenshots every few seconds, then exit
"""
from __future__ import annotations

import argparse


def main(argv=None):
    p = argparse.ArgumentParser(prog="sfere", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", help="use a video file instead of the webcam (loops)")
    p.add_argument("--audio-file", help="play and analyse this file instead of live audio")
    p.add_argument("--audio", action="append", help="live audio source id (repeatable); default: saved sources")
    p.add_argument("--mute", action="store_true", help="analyse --audio-file without playing it")
    p.add_argument("--scene", help="start with this scene")
    p.add_argument("--x11", action="store_true", help="run through XWayland (exact window placement)")
    p.add_argument("--touch", choices=["auto", "on", "off"],
                   help="touch-friendly controls; auto (default) = when a touch screen is connected")
    p.add_argument("--capture", help="directory for periodic output/control screenshots (testing)")
    p.add_argument("--capture-every", type=float, default=2.0)
    p.add_argument("--duration", type=float, help="exit after this many seconds")
    p.add_argument("--no-save", action="store_true", help="don't write settings on exit")
    args = p.parse_args(argv)
    from .app import App
    App(args).run()


if __name__ == "__main__":
    main()
