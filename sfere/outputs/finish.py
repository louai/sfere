"""Finishing a recording: add the sound to the MP4s and, for the stacked layout, combine them.

This can take a while (stacking encodes the video again), so it runs as its own process,
started when a recording stops: quitting sfere never waits for it, and it completes even
after sfere has gone. It reads a job (JSON) on stdin and prints the result (JSON) on stdout.
If sfere quit in the meantime, it says so with a desktop notification instead.

    python -m sfere.outputs.finish < job.json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .recorder import encoder_for


def add_sound(base: str, kind: str, wav: Path | None) -> tuple[Path, str | None]:
    """<base>-<kind>.mkv -> .mp4 (output: with the music). Returns (file kept, problem)."""
    mkv, mp4 = Path(f"{base}-{kind}.mkv"), Path(f"{base}-{kind}.mp4")
    audio = ["-i", str(wav)] if kind == "output" and wav else []
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mkv), *audio, "-map", "0:v", *(["-map", "1:a"] if audio else []),
           "-c:v", "copy", *(["-c:a", "aac", "-b:a", "192k", "-shortest"] if audio else []),
           "-movflags", "+faststart", str(mp4)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        mp4.unlink(missing_ok=True)
        return mkv, f"{kind}: couldn't add the sound ({r.stderr.decode(errors='replace')[-200:]}); kept {mkv.name}"
    mkv.unlink()
    return mp4, None


def stack(base: str, output_size, camera_size) -> tuple[Path | None, str | None]:
    """One video: the output on top, the camera below scaled to the same width, with the music."""
    out, cam = Path(f"{base}-output.mp4"), Path(f"{base}-camera.mp4")
    w = output_size[0]
    cam_h = int(camera_size[1] * w / camera_size[0] / 2 + 0.5) * 2
    glob, enc = encoder_for((w, output_size[1] + cam_h))
    vf = enc[enc.index("-vf") + 1] if "-vf" in enc else ""
    codec = [a for i, a in enumerate(enc) if a != "-vf" and (i == 0 or enc[i - 1] != "-vf")]
    graph = f"[1:v]scale={w}:{cam_h}[c];[0:v][c]vstack=inputs=2:shortest=1" + (f",{vf}" if vf else "") + "[v]"
    stacked = Path(f"{base}-stacked.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", *glob, "-i", str(out), "-i", str(cam), "-filter_complex", graph,
           "-map", "[v]", "-map", "0:a?", *codec, "-c:a", "copy", "-movflags", "+faststart", str(stacked)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        stacked.unlink(missing_ok=True)
        return None, f"Kept separate files: stacking failed ({r.stderr.decode(errors='replace')[-200:]})"
    return stacked, None


def finish(job: dict) -> dict:
    base, wav = job["base"], Path(f"{job['base']}-audio.wav")
    files, messages = [Path(f) for f in job.get("files", [])], list(job.get("messages", []))
    has_wav = bool(job.get("audio_frames")) and wav.exists()
    for kind in job.get("videos", []):
        f, problem = add_sound(base, kind, wav if has_wav else None)
        files.append(f)
        if problem:
            messages.append(problem)
    if has_wav:
        files.append(wav)
    else:
        wav.unlink(missing_ok=True)
    out, cam = Path(f"{base}-output.mp4"), Path(f"{base}-camera.mp4")
    if job.get("layout") == "stacked":
        if out in files and cam in files:
            stacked, problem = stack(base, job["output_size"], job["camera_size"])
            if stacked:
                for f in (out, cam, wav):
                    f.unlink(missing_ok=True)
                files = [f for f in files if f not in (out, cam, wav)] + [stacked]
            else:
                messages.append(problem)
        else:
            messages.append("Kept separate files: one of the videos is missing, so they weren't stacked.")
    return {"files": [str(f) for f in files], "messages": messages}


def main() -> None:
    job = json.loads(sys.stdin.read())
    result = finish(job)
    try:
        print(json.dumps(result), flush=True)
    except OSError:          # sfere is gone (its end of the pipe closed)
        pass
    if job.get("app_pid") and os.getppid() != job["app_pid"] and shutil.which("notify-send"):
        folder = Path(job["base"]).parent
        body = "\n".join([f"Saved {Path(f).name}" for f in result["files"]] + result["messages"]) or "No files"
        subprocess.run(["notify-send", "--app-name=sfere", "-i", "sfere", f"Recording finished in {folder}", body])


if __name__ == "__main__":
    main()
