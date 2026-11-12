"""Recording: the output (with the music), the camera input, and the music as a WAV file.

A session writes into one folder, files named by start time:
    sfere-<stamp>-output.mp4    what the output window shows, with the music
    sfere-<stamp>-camera.mp4    the camera as the tracker saw it (mirrored like the Camera tab)
    sfere-<stamp>-audio.wav     the music sfere analysed
The camera video and the WAV replay a session: `sfere --video ...camera.mp4 --audio-file ...audio.wav`.

Video is written to Matroska while recording (it survives a crash), then remuxed to MP4 with
the sound when the recording stops. Frames carry the wall-clock time they arrived, so a
dropped or late frame doesn't shift the rest.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .pipe import FramePipe

RENDER_NODE = "/dev/dri/renderD128"
VAAPI = (["-vaapi_device", RENDER_NODE], ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "20"])
X264 = ([], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
_encoder: tuple[list[str], list[str]] | None = None


def video_encoder() -> tuple[list[str], list[str]]:
    """ffmpeg (global, output) arguments for H.264: the GPU (VAAPI) if a test encode works,
    else x264 on the CPU."""
    global _encoder
    if _encoder is None:
        _encoder = X264
        if os.path.exists(RENDER_NODE) and shutil.which("ffmpeg"):
            test = ["ffmpeg", "-v", "error", *VAAPI[0], "-f", "lavfi",
                    "-i", "testsrc2=size=320x240:rate=30:duration=0.2", *VAAPI[1], "-f", "null", "-"]
            try:
                if subprocess.run(test, capture_output=True, timeout=10).returncode == 0:
                    _encoder = VAAPI
            except (OSError, subprocess.SubprocessError):
                pass
    return _encoder


def encoder_name() -> str:
    return "GPU (VAAPI)" if video_encoder() is VAAPI else "CPU (x264)"


def ffmpeg_video(path: Path, size: tuple[int, int], fps: float, pix_fmt: str) -> list[str]:
    # the GPU encoder refuses very small frames
    glob, out = video_encoder() if size[0] >= 256 and size[1] >= 128 else X264
    return ["ffmpeg", "-v", "error", "-y", *glob,
            "-use_wallclock_as_timestamps", "1", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{size[0]}x{size[1]}",
            "-thread_queue_size", "64", "-i", "-",
            "-fps_mode", "cfr", "-r", f"{fps:g}", *out, str(path)]


class AudioTap:
    """Writes the analysed mix (mono, 48 kHz) to a WAV file from the audio mixer thread."""

    def __init__(self, path: Path, sr: int):
        self.file = sf.SoundFile(str(path), "w", samplerate=sr, channels=1, subtype="PCM_16")
        self.lock = threading.Lock()
        self.frames = 0

    def __call__(self, mix: np.ndarray) -> None:
        with self.lock:
            if self.file is not None:
                self.file.write(np.clip(mix, -1.0, 1.0))
                self.frames += len(mix)

    def close(self) -> None:
        with self.lock:
            if self.file is not None:
                self.file.close()
                self.file = None


class Recording:
    """One recording session. push_output() from the render loop, push_camera() from the
    camera thread; both return at once."""

    def __init__(self, folder: Path, audio_engine, output_size: tuple[int, int] | None, fps: float,
                 camera_size: tuple[int, int] | None, camera_fps: float):
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.base = folder / f"sfere-{stamp}"
        self.started = time.monotonic()
        self.fps = fps
        self.audio_engine = audio_engine
        self.files: list[Path] = []
        self.messages: list[str] = []
        self.wav = Path(f"{self.base}-audio.wav")
        self.audio = AudioTap(self.wav, audio_engine.sr)
        audio_engine.taps.append(self.audio)
        self.output = self.camera = None
        self._next_output = 0.0
        if output_size:
            self.output_size = output_size
            self.output = FramePipe(ffmpeg_video(Path(f"{self.base}-output.mkv"), output_size, fps, "rgb24"),
                                    "record:output")
        if camera_size:
            self.camera_size = camera_size
            self.camera = FramePipe(ffmpeg_video(Path(f"{self.base}-camera.mkv"), camera_size, camera_fps, "bgr24"),
                                    "record:camera", queue=6)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def wants_output_frame(self, now: float) -> bool:
        """Output frames are taken at the recording's rate, whatever the display's."""
        if self.output is None or now < self._next_output:
            return False
        self._next_output = max(self._next_output + 1.0 / self.fps, now - 0.5 / self.fps)
        return True

    def push_output(self, rgb: bytes) -> None:
        if self.output is not None:
            self.output.push(rgb)

    def push_camera(self, bgr: np.ndarray) -> None:
        if self.camera is not None and bgr.shape[1::-1] == self.camera_size:
            self.camera.push(np.ascontiguousarray(bgr).tobytes())

    def status(self) -> str:
        parts = []
        for pipe in (self.output, self.camera):
            if pipe is not None:
                state = pipe.error or (f"{pipe.written} frames" + (f", {pipe.dropped} dropped" if pipe.dropped else ""))
                parts.append(f"{pipe.name.split(':')[1]}: {state}")
        return "   ".join(parts)

    def stop(self) -> list[Path]:
        """Finish the files (this takes a moment: encoders flush, the MP4s get their sound)."""
        try:
            self.audio_engine.taps.remove(self.audio)
        except ValueError:
            pass
        self.audio.close()
        for pipe, kind in ((self.output, "output"), (self.camera, "camera")):
            if pipe is None:
                continue
            code = pipe.close()
            mkv = Path(f"{self.base}-{kind}.mkv")
            mp4 = Path(f"{self.base}-{kind}.mp4")
            if code != 0 or not mkv.exists():
                self.messages.append(f"{kind} recording failed: {pipe.error or pipe.last_log() or code}")
                if mkv.exists():
                    self.files.append(mkv)
                continue
            # output: video + the music; camera: video only (the WAV replays alongside it)
            audio_in = ["-i", str(self.wav), "-c:a", "aac", "-b:a", "192k", "-shortest"] if kind == "output" else []
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mkv), *audio_in[:2],
                   "-map", "0:v", *(["-map", "1:a"] if audio_in else []), "-c:v", "copy", *audio_in[2:],
                   "-movflags", "+faststart", str(mp4)]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode == 0:
                mkv.unlink()
                self.files.append(mp4)
            else:
                self.messages.append(f"{kind}: couldn't add the sound ({r.stderr.decode(errors='replace')[-200:]}); "
                                     f"kept {mkv.name}")
                self.files.append(mkv)
        if self.audio.frames:
            self.files.append(self.wav)
        else:
            self.wav.unlink(missing_ok=True)
        return self.files
