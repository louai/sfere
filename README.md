# sfere

sfere tracks a glowing contact-juggling ball with a webcam and renders music-reactive visuals around it. It listens to audio from other applications and to inputs such as microphones and line-in. The camera is used only for tracking; the output is entirely rendered graphics. See [PLAN.md](PLAN.md) for the design, the research and the current status.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m sfere                     # webcam + audio from other apps
.venv/bin/python -m sfere --video sample.mp4 --audio-file "sample music v2.wav"   # with recordings
```

The first start compiles a tiny compatibility library for Dear ImGui on Wayland, so it needs a C compiler (`cc`).

Two windows open:
- **control:** calibration, audio sources, scenes, output settings;
- **output:** the visuals.

Click the ball in the Camera tab to calibrate its color. Right-drag in the same view to exclude a reflection. Settings are saved in `~/.config/sfere/settings.toml`.

| Key | Action |
|---|---|
| `1`–`9` | switch scene (with a crossfade) |
| `F` | toggle fullscreen |
| `Shift+F` | move fullscreen to the next monitor |
| `Esc` | leave fullscreen |
| `B` | black out |
| `T` | tap tempo |
| `M` | mirror |
| `D` | debug marker on the output |
| `Space` | freeze |
| `Ctrl+Q` | quit |

## Shaders

Drop shader files in and they load live; saving a file reloads it.
- `effects/shadertoy/*.glsl`: the Image pass. Optional `name.buffer_a.glsl`…`buffer_d.glsl` files add Buffers A–D.
- `effects/isf/*.fs`: ISF shaders. sfere builds controls from their JSON header.

An optional `name.toml` next to a shader records its `title`, `author`, `license` and `source`, and can set `gain`, `resolution`, `[params]` and `[channels]`.

Every shader also receives sfere's uniforms:
- `uBall` (x, y, radius, visible);
- `uBallVel`;
- `uSub`, `uBass`, `uMid`, `uHigh`, `uLevel`;
- `uBeat`, `uBeatPhase`, `uBpm`;
- `uPrevFrame`.

`iMouse` and ISF `point2D` inputs follow the ball. Check each shader's license before using it at paid performances: most Shadertoy shaders are non-commercial (CC BY-NC-SA).

## Tests

```sh
.venv/bin/python -m pytest                              # tracker, analyser, rendering, shaders, units
SFERE_LIVE=1 .venv/bin/python -m pytest tests/test_live_audio.py -s   # plays audio, listens to system output
.venv/bin/python tools/check_app.py --monitor DP-3      # real windows: fullscreen, resize, scenes, tabs
```
