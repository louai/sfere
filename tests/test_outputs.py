"""Recording and virtual-camera plumbing: the frame pipe, GPU readback, a short recording."""
from __future__ import annotations

import shutil
import subprocess
import time

import moderngl
import numpy as np
import pytest

from sfere.outputs.pipe import FramePipe


def test_frame_pipe_writes_and_drops_instead_of_blocking(tmp_path):
    out = tmp_path / "raw"
    pipe = FramePipe(["sh", "-c", f"sleep 0.3; cat > '{out}'"], "test", queue=2)
    t0 = time.monotonic()
    for i in range(50):
        pipe.push(bytes([i]) * 1000)
    assert time.monotonic() - t0 < 0.2          # never waits for the slow consumer
    assert pipe.close() == 0
    data = out.read_bytes()
    assert pipe.dropped > 0 and len(data) == pipe.written * 1000
    assert data[-1] == 49                       # the newest frame always gets through
    with pytest.raises(RuntimeError):
        FramePipe(["no-such-encoder-xyz"], "missing")


def test_grabber_reads_frames_top_row_first(tmp_path):
    from sfere.outputs.grab import FrameGrabber
    from sfere.render.gl import GL
    ctx = moderngl.create_standalone_context(require=330)
    gl = GL(ctx)
    src = ctx.texture((64, 32), 4, dtype="f1")
    img = np.zeros((32, 64, 4), np.uint8)
    img[16:, :, 0] = 255       # GL rows go bottom-up: rows 16.. are the TOP half of the picture
    src.write(img.tobytes())
    g = FrameGrabber(gl, (33, 17))              # odd sizes become even
    assert g.size == (34, 18)
    assert g.grab(src) is None                  # the first frame comes back one grab later
    frame = np.frombuffer(g.grab(src), np.uint8).reshape(18, 34, 3)
    assert frame[2, 17, 0] > 200 and frame[-3, 17, 0] < 50     # red on top
    g.release()
    ctx.release()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_short_recording_has_video_and_sound(tmp_path):
    from sfere.outputs.recorder import Recording

    class Audio:
        sr = 48000
        taps: list = []

    audio = Audio()
    rec = Recording(tmp_path, audio, (64, 36), 30.0, (32, 18), 30.0)
    assert audio.taps
    for i in range(20):
        audio.taps[0](np.sin(np.linspace(0, 40, 2400)).astype(np.float32) * 0.3)
        rec.push_output(bytes([i * 10 % 256]) * (64 * 36 * 3))
        rec.push_camera(np.full((18, 32, 3), i, np.uint8))
        time.sleep(1 / 30)
    files = rec.stop()
    assert not audio.taps and not rec.messages, rec.messages
    names = sorted(f.name.split("-")[-1] for f in files)
    assert names == ["audio.wav", "camera.mp4", "output.mp4"]
    out = next(f for f in files if f.name.endswith("output.mp4"))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert "video,64" in probe and any(p.startswith("audio") for p in probe)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("out_size,cam_size", [((64, 36), (32, 18)), ((640, 360), (1280, 720))])
def test_stacked_recording_is_one_video(tmp_path, out_size, cam_size):
    from sfere.outputs.recorder import Recording

    class Audio:
        sr = 48000
        taps: list = []

    audio = Audio()
    rec = Recording(tmp_path, audio, out_size, 30.0, cam_size, 30.0, layout="stacked")
    for i in range(20):
        audio.taps[0](np.zeros(1600, np.float32) + 0.1)
        rec.push_output(bytes([200]) * (out_size[0] * out_size[1] * 3))           # bright output on top
        rec.push_camera(np.zeros((cam_size[1], cam_size[0], 3), np.uint8))        # dark camera below
        time.sleep(1 / 30)
    files = rec.stop()
    assert not rec.messages, rec.messages
    assert [f.name.split("-")[-1] for f in files] == ["stacked.mp4"]
    assert sorted(p.name.split("-")[-1] for p in tmp_path.iterdir()) == ["stacked.mp4"]
    w, h = out_size
    cam_h = int(cam_size[1] * w / cam_size[0] / 2 + 0.5) * 2
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "csv=p=0", str(files[0])], capture_output=True, text=True).stdout.split()
    assert f"video,{w},{h + cam_h}" in probe and any(p.startswith("audio") for p in probe)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(files[0]), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"], capture_output=True).stdout
    img = np.frombuffer(frame, np.uint8).reshape(h + cam_h, w)
    assert img[: h // 2].mean() > 150 and img[h + cam_h // 2:].mean() < 40
