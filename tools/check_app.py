"""Drives the real app (windows, GL, UI) through the M0/M2 checks and saves screenshots.

  .venv/bin/python tools/check_app.py [--monitor DP-3] [--out out/check]

Checks: fullscreen on a chosen monitor and back, live resize of the output window,
every scene renders (not black) after switching to it, every control tab draws, frame rate. It briefly takes over the
chosen monitor.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sfere.app import App  # noqa: E402

SAMPLE_B = str(Path.home() / "Videos/Camera/Recording from 2026-11-11 22-08-06.462397.mp4")
MUSIC = str(Path(__file__).resolve().parents[1] / "sample music v2.wav")


def run_for(app, seconds):
    end = time.monotonic() + seconds
    n0 = 0
    t0 = time.monotonic()
    while time.monotonic() < end:
        if not app.frame():
            raise SystemExit("window closed")
        n0 += 1
    return n0 / (time.monotonic() - t0)


def shot_output(app, path):
    tex = app.last_tex
    img = np.frombuffer(tex.read(), np.uint8).reshape(tex.height, tex.width, 4)[::-1, :, :3]
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


def shot_control(app, path):
    app._ui_shot = False
    app.frame()
    cv2.imwrite(str(path), cv2.cvtColor(app._ui_shot, cv2.COLOR_RGB2BGR))
    app._ui_shot = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", default="DP-3")
    ap.add_argument("--out", default="out/check")
    ap.add_argument("--touch", default="off", choices=["auto", "on", "off"],
                    help="touch mode; auto/on run through XWayland when a touch screen is connected")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(video=SAMPLE_B, audio_file=MUSIC, audio=None, mute=True, scene=None, x11=False, touch=a.touch,
                              capture=None, capture_every=2.0, duration=None, no_save=True)
    app = App(args)
    app.start_clock()
    results = []
    g = app.glfw
    try:
        fps = run_for(app, 3)
        results.append(f"window: {fps:.1f} fps, render {app.scenes.size}, framebuffer {g.get_framebuffer_size(app.out_win)}")
        names = [app.monitor_name(m) for m in app.monitors()]
        results.append(f"monitors: {[app.monitor_label(m) for m in app.monitors()]}")
        if a.monitor in names:
            app.set_fullscreen(a.monitor)
            fps = run_for(app, 3)
            mon = g.get_window_monitor(app.out_win)
            fb = g.get_framebuffer_size(app.out_win)
            mode = g.get_video_mode(next(m for m in app.monitors() if app.monitor_name(m) == a.monitor))
            ok = bool(mon) and app.monitor_name(mon) == a.monitor and tuple(fb) == (mode.size.width, mode.size.height)
            results.append(f"fullscreen on {a.monitor}: {'OK' if ok else 'FAIL'} framebuffer {fb}, render "
                           f"{app.scenes.size}, {fps:.1f} fps")
            shot_output(app, out / "fullscreen.png")
            app.set_fullscreen(None)
            run_for(app, 1.5)
            mon = g.get_window_monitor(app.out_win)
            results.append(f"back to window: {'OK' if not mon else 'FAIL'} size {g.get_window_size(app.out_win)}")
        else:
            results.append(f"monitor {a.monitor} not connected: fullscreen skipped")
        g.set_window_size(app.out_win, 900, 500)
        run_for(app, 1.5)
        fb = g.get_framebuffer_size(app.out_win)
        results.append(f"resize to 900x500: framebuffer {fb}, render {app.scenes.size} "
                       f"{'OK' if tuple(app.scenes.size) == tuple(fb) else 'FAIL'}")
        g.set_window_size(app.out_win, 1280, 720)
        run_for(app, 1.0)
        for i, name in enumerate(app.scenes.names):
            # switched between frames, like a key or a button in the control window, with a crossfade
            app.select_scene(i)
            fps = run_for(app, app.scenes.fade_time + 1.0)
            shot_output(app, out / f"scene_{i}_{name.replace(' ', '_')}.png")
            rgb = np.frombuffer(app.last_tex.read(), np.uint8).reshape(-1, 4)[:, :3]
            # a scene built in the wrong GL context draws nothing at all
            drawn = rgb.max() > 0 or not app.last_ball.visible
            results.append(f"scene {name}: {'OK' if drawn else 'FAIL (black)'} {fps:.1f} fps, scene "
                           f"{app.scenes.last_frame_ms:.2f} ms, brightest {rgb.max()}, errors {app.scenes.current.errors}")
        for tab in ("Camera", "Audio", "Scenes", "Output", "Debug", "Help"):
            app.panels.show_tab(tab)
            run_for(app, 0.3)
            shot_control(app, out / f"control_{tab}.png")
            results.append(f"tab {tab}: drawn, current={app.panels.current_tab}")
    finally:
        app.close()
    print("\n".join(results))


if __name__ == "__main__":
    main()
