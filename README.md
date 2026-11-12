# sfere

sfere tracks a glowing contact-juggling ball with a webcam and renders music-reactive visuals around it. It listens to audio from other applications and to inputs such as microphones and line-in. The camera is used only for tracking; the output is entirely rendered graphics. **[The guide](docs/guide.md)** explains setup, every control, touch screens and shaders; the same text is in the control window's Help tab. See [PLAN.md](PLAN.md) for the design, the research and the current status.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m sfere                     # webcam + audio from other apps
.venv/bin/python -m sfere --video sample.mp4 --audio-file "sample music v2.wav"   # with recordings
```

To add sfere to the app drawer and get a `sfere` command for the terminal, run `tools/install_desktop.sh` once. `sfere --help` lists the options.

The first start compiles a tiny compatibility library for Dear ImGui on Wayland, so it needs a C compiler (`cc`).

Two windows open. If an external display is connected, the output goes fullscreen on it (and on one plugged in later); turn this off in the Output tab.
- **control:** calibration, audio sources, scenes, output settings;
- **output:** the visuals.

Click or tap the ball in the Camera tab to calibrate its color. Drag a box in the same view to exclude a reflection.

With a touch screen connected, the control window switches to large controls and finger scrolling, and sfere runs through XWayland so touches reach it (`--touch off` or the Output tab turns this off). Settings are saved in `~/.config/sfere/settings.toml`.

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

Subfolders work too. `effects/isf/vidvox/` holds 197 shaders from Vidvox's MIT-licensed ISF collection; most are filters that process the layers below them. To add any effect to the current scene, use **Add a layer** in the Scenes tab. The Scenes tab also creates, duplicates, renames, reorders and deletes scenes, edits layers, and binds settings to the music; scenes are saved in `~/.config/sfere/scenes.json`.

An optional `pack.toml` in a folder sets defaults for every shader in it. An optional `name.toml` next to a shader records its `title`, `author`, `license` and `source`, and can set `gain`, `resolution`, `[params]` and `[channels]`.

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
