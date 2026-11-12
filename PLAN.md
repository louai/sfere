# sfere — plan

sfere takes a live webcam feed of someone dancing with a glowing contact-juggling ball, and the music they're dancing to. It tracks the ball and draws rave- or museum-style visuals driven by the ball's motion and by the music. The camera is used only for tracking: everything in the output is rendered graphics.

**Platform: a native Python desktop app** (§1.5). It has to capture **audio inputs (mics, line-in) and audio played by other applications**, which a browser can't do on Linux. It also needs dependable manual camera exposure and reliable fullscreen on a second monitor under Wayland.

---

## Status (2026-11-11): M0–M4 built and tested

| Milestone | Result |
|---|---|
| M0 | Control window (ImGui) and output window share one GL context. Fullscreen on DP-3 at 3440×1440, 60 fps, and back to a window; live resize works. Webcam runs at a steady 30.0 fps 720p, and its V4L2 settings are restored exactly on exit. (`tools/check_app.py`) |
| M1 | Sample B: 367/367 visible frames tracked, 0 false detections, lost/found at both hidden stretches, 1.1 px jitter. Sample A: 98.4% tracked, 0 reflection lock-ons, 1.1 px jitter. About 1.2 ms per frame offline, about 2–6 ms inside the app. (`tests/test_tracker.py`) |
| M2 | Feedback, bloom and composite pipeline; orb, trail and light painting; render once, shown in both windows; render scale. About 60 fps on every scene. |
| M3 | 123.0 BPM from the file, with 100% of kicks within 50 ms of the grid, also at −20 dB. 123.0 BPM within 4.4–4.6 s from **system audio while `pw-play` plays the track, mixed with the C920 mic**. (`tests/test_analyser.py`, `SFERE_LIVE=1 tests/test_live_audio.py`) |
| M4 | 10 built-in effects; 7 presets with crossfades; Shadertoy loader (multipass) and ISF loader (generators and filters, controls built from the JSON header); ball and audio uniforms; bindings; hot reload; license display; broken shaders reported without stopping the show. (`tests/test_render.py`, `tests/test_units.py`) |

**Changes from the plan, found while building:**
- **ImGui on Wayland.** imgui-bundle's native module links two X11-only GLFW functions that the Wayland GLFW build lacks, so sfere compiles a tiny stub library for them at first start (`sfere/compat.py`; needs `cc`). It also renders ImGui with its own moderngl backend rather than PyOpenGL. `moderngl-window` turned out not to be needed.
- **One moderngl context per window**, attached through `render/gl.py:attach_current_context`: `moderngl.create_context()` can't attach to a second EGL context.
- **Two layers per scene.** Effects draw into a *direct* layer by default; only those with `feedback` on enter the light-painting loop. Feeding everything back washed out any scene with a backdrop or a full-screen shader (brightness × 1/(1 − decay)).
- **Kicks.** Kick detection also requires a 1.5–8 kHz beater click at the same moment (or a very strong hit). Without it, off-beat bass notes counted as kicks.
- **Correction to §1.2:** the v2 sample's *strong* kick lands every second beat, with lighter hits in between; it isn't a strict four-on-the-floor. The tempo (123) and grid placement were unaffected.
- **Tempo is refined to sub-frame precision** (parabolic peak fits at 1–4 beats). At 48 kHz, plain interpolation had biased it to 122.25.
- **The kaleidoscope folds around the ball**, not the screen center, so the ball is always in the image.
- **This laptop's digital mic delivers a constant −1.0 signal**: muted or misconfigured at the system level. sfere detects such a stuck source, reports it, and mixes silence in its place.
- **Not done yet (M5):** NDI and virtual-camera outputs, OSC, Identify, borderless mode, and the test with a live performer in a dim room.

---

## 1. Research findings

### 1.1 Sample footage

There are two samples. **Sample B, the low-exposure one, is the target setup**: build for it first. Sample A shows what happens in a normally lit room and is kept as the fallback case.

#### Sample B: low exposure (`~/Videos/Camera/Recording from 2026-11-11 22-08-06.462397.mp4`) — target setup

| Property | Value |
|---|---|
| Resolution | 1920×1080, H.264 |
| Frame rate | variable, about 24 fps on average (436 frames in 18.2 s); see §1.4 for why |
| Audio | MP3 track |
| Scene | everything is black (pixel value 0) except the ball |

**How the ball looks on camera.** It's a flat, saturated red with no white core: on average RGB (150, 1, 18), ranging from (110, 0, 0) to (185, 3, 32). Fingers in front of it show as dark notches or stripes that can split it into several pieces.

**Tracker test** (`tools/probe_dark.py`): mark pixels with red > 60, red > 2× green and red > 1.5× blue, then take the average position of all marked pixels.

| Result | Value |
|---|---|
| Frames where the ball is visible | 368 of 437 |
| Of those, frames tracked | **all 368** |
| Missed frames | 69, in two stretches (frames 146–190 and 413–436, about 2 s and 1 s). Both are completely black, so the ball is hidden or out of view, not missed. |
| Large jumps | 1, where the ball reappears after being hidden, which is correct |
| Ball size (at 320 px width) | 49 px at median-small distances, 134 px typical, up to about 24,800 px (about 43% of the frame) when held right up to the camera |

**What this means for the design:**
- With low exposure, a simple color threshold works; the core+halo scoring becomes a *lit-room profile*.
- Because fingers split the ball into pieces, the tracker should **merge nearby pieces** (dilate, or group blobs within about 1 ball radius) before taking the center, rather than picking the single largest piece.
- The ball's size on screen varies by about 500×. Effects should scale with ball size, and an extreme close-up should be treated as its own moment, such as a "fill the screen" burst.
- The ball genuinely disappears for 1–2 s at a time, so the lost/found states and the `found` / `lost` events matter. Re-appearing is a good effect trigger.
- The camera image is never shown in the output (§2), so the black scene costs nothing visually: the camera only has to make the ball easy to find.

#### Sample A: lit room (`~/Videos/Camera/Recording from 2026-11-11 21-38-10.703597.mp4`) — fallback case

| Property | Value |
|---|---|
| Resolution | 1920×1080, H.264 |
| Frame rate | variable, about 22 fps on average (494 frames in 22.1 s) |
| Audio | MP3 track |
| Scene | dim room, pale green wall, warm light, window on the right edge, person in dark clothes |

**How the ball looks on camera.** The ball glows red, but its center is so bright that the camera records it as pure white. Around that white core is a saturated red halo. The core plus the halo is the ball's most distinctive feature.

**Things that look like the ball, or hide it:**
1. **Skin under warm light.** The face and hands pass a loose "red" color test. A strict test (red > 170, saturation > 0.55, red > 2× green) rejects them.
2. **Reflection in the window glass.** On the right edge of the frame, the glass reflects a real red copy of the ball. Color alone can't tell it from the ball, so it has to be handled by the tracker (see §3.2).
3. **Motion blur.** During fast moves the ball becomes a red smear with little or no white core.
4. **Ball held up high.** In some poses it records as almost entirely white with very little red halo, which the halo-based detector misses.
5. **Hands covering it.** Hands hide it briefly during holds and transfers.
6. **Changing size.** Its size on screen varies with distance, from about 6 to about 60 px across at 320 px frame width.
7. **Ball absent.** Toward the end of the clip, the person walks toward the camera and the ball isn't visible.

**Tracker test.** `tools/probe_tracker.py` uses NumPy only: it downscales each frame to 320×180, scores pixels by red-halo density plus a bonus for a white core next to red, and takes the highest-scoring spot.

| Result | Value |
|---|---|
| Frames with a detection | 408 of 494 |
| Frames missed | 86, of which about 50 are where the ball has left the scene (frames 422–470) |
| Jumps over 60 px between consecutive frames | 1 |

When the ball is in view, it's found in about 92% of frames, even with no motion model at all. The misses come from blur, the all-white high poses, and occlusion. The remaining problems are fixable in software: the white-only fallback, a motion model, the reflection mask, and computing a proper blob center instead of the brightest point. This confirms a color/brightness tracker is enough, and **no ML model is needed**.

### 1.2 Sample music (`sfere/sample music v2.wav`)

| Property | Value |
|---|---|
| Format | WAV, 44.1 kHz, stereo, 16-bit, 10.6 s |
| Loudness | full level: peak −0.7 dBFS, RMS −11.5 dBFS |
| Where the power is | 20–60 Hz: 42%; 60–250 Hz: 55%; everything above 250 Hz: about 3%. Spectral centroid about 98 Hz, so very sub-heavy. |
| Tempo | **123 BPM**, a strong, unambiguous peak (autocorrelation 0.89; half and double tempo show no competing peak) |
| Rhythm | four-on-the-floor: 23 kick onsets in 10.6 s, one per beat (beat period 0.488 s). Hi-hats and mids fall on both beats and off-beats, so the full mix has about 4.5 onsets per second. |

Tested with `tools/probe_audio.py`, which uses the same FFT size (2048) and hop (512) as the planned analyser.

**What this means for the design:**
- **Line the beat grid up against the kick (30–120 Hz), not the full mix.** Fitting it to all onsets at once placed the grid exactly half a beat off, on the off-beat hats and claps. Measured against that grid, 15 of 23 kicks fell on the off-beat. The tempo comes from the full mix; *where the beats fall* comes from the low band.
- **Estimate tempo from the onset envelope's autocorrelation, not from intervals between onsets.** An earlier, busier sample (v1, 136 BPM with eighth/sixteenth-note rhythms) made interval-based estimation report 304 BPM. Keep an octave rule: prefer the strongest peak in 90–160 BPM over its half or double.
- **Keep automatic gain and separate per-band normalization.** v2 is mastered loud, but v1 peaked at −21 dBFS, and a microphone in a room will vary just as much. With about 3% of power above 250 Hz, a fixed-scale `mid` or `high` band would sit near 0. Each band is normalized against its own recent range (§4).
- **Map events to effects by drum role:**
  - kick → `beat` → ripples and big bursts
  - off-beat hats → `subdivision` → sparkles and twinkle
  - sub-bass level (`sub` band, 20–60 Hz, added because this track lives there) → orb size, glow, screen pulse
- **The tempo takes about 4–6 s of audio to settle.** Until then, beat-driven effects fall back to kick onsets, and tap-tempo can seed the estimate.

### 1.3 Audio capture on this machine (tested)

**Sound system:** PipeWire 1.6.2, with the PulseAudio compatibility layer. Every output device has a **monitor** source: a recordable copy of everything being played through that output, from any application.

| Source | Kind | Captured? |
|---|---|---|
| `…HiFi__Headphones__sink.monitor` (`@DEFAULT_MONITOR@`) | **audio from other apps**, through the default output | yes. While `sample music v2.wav` played through another app (`pw-play`), sfere-style capture got peak −4.2 dBFS, with 96% of power below 250 Hz, matching the file's own analysis. |
| `…HiFi__HDMI1–4__sink.monitor` | audio from other apps, through HDMI/DisplayPort outputs (e.g. the projector) | available |
| `alsa_input.usb-046d_HD_Pro_Webcam_C920…` (current default input) | C920 webcam microphone | yes (room noise, peak −54 dBFS) |
| `…HiFi__Mic1__source` | laptop microphone | yes (peaked at 0 dBFS on open, a start-up pop or a gain that's too high; check the level in testing) |
| `…HiFi__Headset__source` | headset / line-in jack | available |

Test method: `parec --device=<source> --format=float32le --rate=48000 --latency-msec=20`. Native rate is 48 kHz.

**What this means for the design:**
- **Other apps' audio:** capture the default output's monitor (`@DEFAULT_MONITOR@`). It follows you if you switch output, for example to the projector's HDMI audio. Any specific output can also be picked.
- **Audio inputs:** any mic or line-in source by name.
- **Both at once:** sfere opens several sources and **mixes them, with a gain per source**, for example system audio plus a microphone.
- **Latency:** about 20 ms buffers are fine for visuals.

### 1.4 Camera on this machine (tested)

Logitech **HD Pro Webcam C920** at `/dev/video0`. Its V4L2 controls, as currently set:

| Control | Now | Range | Plan |
|---|---|---|---|
| `auto_exposure` | **1 (Manual)** | 0–3 | keep manual; this is how sample B got its black background |
| `exposure_time_absolute` | 38 | 3–2047 | calibration slider; lower means darker and less blur |
| `exposure_dynamic_framerate` | **1 (on)** | bool | **set to 0**. When on, the camera may drop its frame rate to lengthen exposure, which explains the variable ~22–24 fps in both samples. Off gives a steady 30 fps. |
| `gain` | 158 | 0–255 | lower it; high gain adds sensor noise. With a self-lit ball, little gain is needed. |
| `white_balance_automatic` | 1 | bool | set to 0 so the ball's hue stays stable |
| `focus_automatic_continuous` | 1 | bool | set to 0 with a fixed focus, to stop focus hunting in the dark |
| `power_line_frequency` | 2 (60 Hz) | 0–2 | keep (it reduces flicker under mains-powered lights) |

The C920 tops out at 30 fps; MJPG covers every resolution. sfere sets all of these at start-up and restores your previous values on exit.

### 1.5 Platform: why Python, and with what

A browser version was considered first and rejected, for these reasons:

| Need | Browser on Linux | Python (chosen) |
|---|---|---|
| **Audio from other apps** | not possible (only another Chrome tab's audio) | the PipeWire/PulseAudio monitor sources (§1.3) |
| **Several audio sources mixed** | awkward | straightforward |
| **Manual exposure, white balance, focus** | camera-dependent, often missing | V4L2 controls directly (§1.4) |
| **Fullscreen on monitor 2 under Wayland** | the page can't choose the screen; needs drag + `F`, or X11 mode | GLFW can request fullscreen on a chosen monitor, which Wayland allows |
| **OSC out to TouchDesigner / Resolume** | needs a bridge process | `python-osc` |
| **GPU** | WebGL2, no compute shaders | OpenGL 4.3+ with compute shaders (particles, fluids) |
| **Offline tests** | separate harness | the same tracker and analyser code runs on the sample files in `pytest` |

**Libraries:**

| Part | Library | Notes |
|---|---|---|
| Camera | `opencv-python` (`cv2.VideoCapture(0, cv2.CAP_V4L2)`, MJPG 1280×720@30) + `v4l2-ctl` for controls | also reads the sample videos for development |
| Tracking / analysis | `numpy` (already installed) | tested above |
| Audio capture | **`soundcard`**: lists microphones *including* monitor sources (`include_loopback=True`) through libpulse; fallback: a `parec` subprocess (the tested method) | |
| Audio file playback (dev) | `soundfile` + `sounddevice` | playback and analysis read the same buffer, so they're exactly in sync |
| Windows + GL context | `glfw` (GLFW 3.4, native Wayland and X11) | monitor list, fullscreen on a chosen monitor |
| Rendering | `moderngl` + `moderngl-window` | shaders, framebuffers, instancing, compute; moderngl-window adds resource loading, shader hot-reload and ImGui integration helpers. Python never loops over particles or pixels. |
| Borrowed effects | built-in loaders for **Shadertoy** and **ISF** shaders (§5.5) | thousands of existing GLSL effects, after a license check |
| Simulation *(reserve)* | `taichi` | GPU fluids, smoke and large particle systems for the stretch goals; its wheels lag behind Python releases, so it may need a 3.12 venv |
| Video out to other apps | `ndi-python` (NDI), `pyvirtualcam` + `v4l2loopback` (virtual webcam) | §5.6 |
| Onset/tempo cross-check *(optional)* | `aubio` | a second opinion for the analyser in tests and in the debug overlay |
| Control UI | `imgui-bundle` (Dear ImGui) | sliders, pickers, camera preview with the detection overlay |
| OSC | `python-osc` | |
| Settings | `tomllib` (standard library) + a small writer | `~/.config/sfere/settings.toml` |

**Environment:** Python 3.14 with a project venv (`python3 -m venv .venv`; `venv` and `ensurepip` work, `pip` isn't installed system-wide). Wheels for 3.14 are the first thing to check in M0 (§8).

---

## 2. Architecture

```
 camera thread                         audio thread(s)
 ─────────────                         ───────────────
 /dev/video0 or video file             system-audio monitor ─┐
   │ MJPG 1280×720 @30                 mic / line-in ────────┼─▶ mixer (gain per source)
   ▼                                   file player (dev) ────┘        │ 48 kHz, ~20 ms blocks
 downscale 320×180 → Tracker                                          ▼
   │ observations {x, y, r, conf, t}                         Analyser (FFT 2048, hop 512)
   ▼                                                          bands · onsets · tempo · beat grid
 BallFilter (Kalman + lost/found + motion events)                     │
   │                                                                  │
   └──────────────▶ latest BallState ◀── shared, lock-protected ──▶ latest AudioState
                                   │
 main thread (GLFW requires it)    ▼
 ───────────────────────────────────────────────────────────────────────────────
 Scene.update(dt, ball, audio, events) → render once into an offscreen texture
   ├──▶ Output window: fullscreen on the chosen monitor, or a resizable window (vsync on)
   └──▶ Control window (ImGui): preview of that same texture, camera + mask view,
        panels for calibration / audio sources / scenes / output (vsync off)
```

- **The camera feeds only the tracker.** Camera pixels never reach the renderer or the output. The renderer receives only `BallState` and `AudioState`, which are numbers. As a result:
  - Camera settings can be tuned purely for tracking.
  - The output shows nothing of the room or the performer.
  - The camera image appears only in the control window, for calibration and debugging.
- **Threads:**
  - **Camera thread:** OpenCV capture and NumPy tracking. Both release the GIL during the heavy work.
  - **Audio:** library callback threads feed a ring buffer; the analyser runs on its own thread at about 94 Hz (48000 / 512).
  - **Main thread:** GLFW, ImGui and rendering.

  Threads share only the *latest* state object, swapped under a lock, never queues of frames, so no stage can fall behind and add latency.
- **Render once, show twice.** The two windows share one GL context. The scene is rendered once per frame into a texture. The output window draws it full-size; the control window shows it as an ImGui image. Only the output window waits for vsync, so the control window can't halve the output's frame rate.

### Files

```
sfere/
  pyproject.toml           dependencies, `sfere` entry point
  sfere/__main__.py        start-up: settings, threads, windows, main loop
  sfere/camera.py          capture thread, V4L2 controls (set + restore), video-file source
  sfere/tracker.py         profiles (dark stage / lit room), mask → pieces → merge → observation
  sfere/ballfilter.py      Kalman filter, prediction, lost/found state machine, motion events
  sfere/audio/sources.py   list inputs + monitors, open several, mixer with per-source gain
  sfere/audio/analyser.py  bands, auto gain, onsets, tempo, kick-aligned beat grid
  sfere/render/gl.py       moderngl helpers: framebuffers, ping-pong, fullscreen quad
  sfere/render/post.py     feedback, bloom, composite
  sfere/fx/*.py + *.glsl   one module per effect (shaders hot-reload on save)
  sfere/fx/shadertoy.py    Shadertoy loader: header wrapper, iChannels, multipass buffers
  sfere/fx/isf.py          ISF loader: JSON INPUTS → UI controls, PASSES → framebuffers
  sfere/fx/bindings.py     bind shader inputs to audio bands / ball / motion features
  effects/shadertoy/       drop-in Shadertoy shaders (+ .toml with source, author, license)
  effects/isf/             drop-in ISF shaders (+ .toml with source, author, license)
  sfere/outputs/ndi.py     NDI sender (async GPU readback)
  sfere/outputs/vcam.py    virtual webcam via pyvirtualcam / v4l2loopback
  sfere/scenes.py          presets = stacks of effects + parameters, crossfades
  sfere/windows.py         output + control windows, monitor picker, fullscreen, aspect/flip
  sfere/ui.py              ImGui panels and hotkeys
  sfere/outputs/osc.py     optional OSC output (ball + beat) for TouchDesigner / Resolume
  sfere/settings.py        ~/.config/sfere/settings.toml
  tests/                   pytest: tracker on samples A/B, analyser on sample music
  tools/probe_*.py         the feasibility probes used for this research (exist)
```

---

## 3. Tracking design

### 3.0 Two tracking profiles
- **Dark stage (default):** for low exposure, as in sample B. A plain color threshold, merging of nearby pieces, and the center of the merged group. No core is required.
- **Lit room:** for auto-exposure, as in sample A. The full core+halo scoring below, plus exclusion zones.

Calibration chooses between them automatically. If more than about 98% of the frame is near-black outside the ball, it uses *Dark stage*. You can also choose one in the panel.

### 3.1 Per-frame pipeline (320×180, target under 3 ms)
1. **Downscale.** `cv2.resize` with `INTER_AREA` to 320×180, then convert to HSV with `cv2.cvtColor`.
2. **Classify each pixel** with thresholds derived from the calibrated ball color:
   - *halo*: hue within ±15° of the ball's hue, saturation > 0.55, brightness > 0.6
   - *core*: every color channel > 0.9 (blown-out white)
3. **Find blobs.** Dilate 3×3, then `cv2.connectedComponentsWithStats`. For each blob, record pixel count, centroid, bounding box, core fraction, halo fraction, and fill ratio (how circle-like it is). *Dark stage:* merge pieces within about 1 ball radius of each other into one observation.
4. **Score each blob (lit room):**
   `score = w_area·log(area) + w_core·[core blob touches halo] + w_circ·fillRatio − w_dist·(distance from predicted position / gate) − ∞·[centroid inside an exclusion zone]`
   - White-only blobs are accepted only if they are near the predicted position. This covers the high, all-white poses without locking onto window glare.
   - A long, thin halo blob is accepted as motion blur. Its centroid gives the position and its long axis gives the direction of motion.
5. **Output** the best blob if its score clears the threshold: `{x, y, radius, confidence, t}`. Otherwise output nothing.

### 3.2 Handling reflections and lookalikes
- **Exclusion zones.** In the calibration view, drag rectangles over the window or a mirror. This handles the reflection seen in sample A.
- **Motion gate.** A blob much farther from the predicted position than the ball could plausibly travel in one frame is rejected, unless the track has been lost for more than 0.5 s.
- **Single target.** Assume one ball for version 1; choose the best-scoring blob each frame.

### 3.3 Filtering and prediction
- Constant-velocity **Kalman filter** in camera coordinates, with measurement noise scaled by 1/confidence. It uses the capture timestamps, never a frame count.
- **States:** tracking → coasting (no detection for up to 250 ms; predicted position, visuals fade) → lost (visuals release the ball) → re-acquire (requires 2 consecutive confirmed detections).
- **Render-time extrapolation:** the camera runs at 30 fps while rendering runs at 60 or more. Each render frame extrapolates from the filter state, so trails stay smooth instead of jumping between camera frames.
- **Mirror toggle:** a selfie-style view is the default for a performer watching a screen; a projector behind the performer may not want it.

### 3.4 Calibration UX (control window)
1. Show the camera with an overlay of the detection mask.
2. **Click on the ball** to sample its hue, saturation and brightness and set the thresholds automatically. Sliders allow fine-tuning.
3. Camera controls (§1.4): exposure, gain, white balance and focus, plus a "Dark stage" preset that applies all of them at once.
4. Draw exclusion zones.
5. Saved to `settings.toml`.

### 3.5 Motion features (exposed to effects)
- `speed`, `accel`, `angle`, `curvature`, `radius` (as a rough depth cue), smoothed and normalized to 0–1.
- **Events:**
  - `flick`: sharp acceleration spike
  - `stall`: fast movement followed by a sudden stop, typical of isolations
  - `loop`: about 360° of accumulated turning within 1.5 s
  - `rise` / `drop`: vertical velocity crossing a threshold
  - `found` / `lost`: the ball appears or disappears

---

## 4. Audio design

### 4.1 Sources (Audio panel)
- **The list shows every available source**, grouped:
  - **System audio:** "Default output (follows device changes)" (`@DEFAULT_MONITOR@`), plus each output's monitor by name (Headphones, HDMI 1–4…).
  - **Inputs:** microphones and line-ins (laptop mic, headset jack, C920 mic, USB interfaces).
  - **File (development):** play a WAV or MP3 through the speakers and analyse the same samples.
- **Any number can be enabled at once.** Each has a gain slider, a level meter and a mute button. They're mixed to mono at 48 kHz before analysis. Typical setups:
  - *DJ software or Spotify on this laptop:* System audio only.
  - *Music from an external sound system:* line-in, or a mic near a speaker.
  - *Both, for example a live vocalist over a playback track:* System audio plus mic.
- **Hot-plug:** the list refreshes when devices appear or disappear. A source that vanishes shows as "disconnected" and reconnects automatically when it returns.
- **Feedback safety:** sfere never plays captured audio back out. Only the dev file player produces sound.

### 4.2 Features computed each analysis hop (about 94 per second)
- `sub` (20–60 Hz), `bass` (60–250 Hz), `mid`, `high` levels. Each is auto-adjusted to its own recent loudness range and smoothed with separate attack and release times.
- `onset`: positive spectral flux above an adaptive threshold (median + k·MAD over the last ~1 s).
- `beat`: an onset that lines up with the BPM estimate.
  - **Tempo** comes from autocorrelation of the last ~6 s of the onset envelope, preferring 90–160 BPM over half or double tempo. *Not* from intervals between onsets, which gave 304 BPM on the v1 sample (§1.2).
  - **Beat alignment** is re-fit continuously **against kick-band (30–120 Hz) onsets**; fitting it to the full mix landed it on the off-beats (§1.2).
- `subdivision`: an onset on an eighth or sixteenth note of the grid, for small, fast effects.
- `beatPhase` from 0 to 1 within the beat, and a `bar` counter.

### 4.3 Calibration
- A **latency offset** per source; microphones hear the room late.
- A **tap-tempo** key, which overrides or seeds the tempo estimate.

---

## 5. Rendering and effects

### 5.1 Pipeline
- Each frame draws into a float framebuffer with additive blending.
- **Feedback pass:** the previous frame is blended back in, slightly faded, optionally zoomed, rotated or tinted. This gives long-exposure light-painting persistence for free.
- **Bloom:** bright-pass, a chain of 5 progressively blurred downsampled copies, added back on top.
- **Composite:** then color grading and vignette.
- **Background:** generated only, never camera pixels. Pure black (best for projection) or a generated backdrop per scene, such as a slow gradient, starfield or noise field.
- **The ball itself:** a glowing orb sprite at the tracked position, drawn at the measured radius, with a color that can respond to the music.
- **Particles** are simulated on the GPU with compute shaders. Python never loops over individual particles.

### 5.2 Effect modules
Each module is a Python class with `update(dt, ball, audio, events)` and `draw(ctx)`, its GLSL files, and a `params` schema that the control UI builds sliders from. Shaders reload on save.

| # | Effect | Driven by |
|---|---|---|
| 1 | **Ribbon trail:** smoothed Catmull-Rom curve through the last N positions; width tapers and grows with speed; color shifts over time or follows the spectrum | position, speed |
| 2 | **Ripples:** concentric rings expanding from the ball, fading out | beat, stall |
| 3 | **Sparkle bursts:** glittery particle explosions with gravity, drag and twinkle | onset (size ∝ energy), flick |
| 4 | **Comet embers:** continuous particle stream behind the ball, emission rate ∝ speed | speed, high |
| 5 | **Light painting:** high-persistence feedback so the path lingers for seconds | position |
| 6 | **Kaleidoscope:** N-fold mirror symmetry of the whole layer stack around the screen center | bar / phrase |
| 7 | **Orbiters:** small satellites orbiting the ball; orbit radius tracks bass, spin tracks BPM | bass, beatPhase |
| 8 | **Spectrum halo:** FFT bars arranged radially around the ball | spectrum |
| 9 | **Constellation:** recent positions become stars joined by thin lines (a museum look) | position, beat |
| 10 | **Shockwave distortion:** screen-space ripple that warps the feedback buffer | drop / beat |
| — | *Stretch:* stable-fluids smoke and ink injected at the ball's position (compute shaders) | velocity |

### 5.3 Scenes
- A scene is a named stack of effects with parameters, a color palette and a background mode. Examples: *Rave* = trail + sparkles + ripples + bloom; *Museum* = constellation + light painting with soft colors; *Cosmic* = orbiters + kaleidoscope.
- Number keys 1–9 switch scenes with a 2 s crossfade. Optional auto-advance every 16 or 32 bars.
- **Hotkeys** (work in either window):

  | Key | Action |
  |---|---|
  | `F` | toggle fullscreen for the output window, on its current or chosen monitor |
  | `Shift+F` | move the output fullscreen to the next monitor |
  | `D` | debug overlay (mask, blobs, filter state, audio meters, fps / latency) |
  | `C` | calibration |
  | `M` | mirror |
  | `B` | black out (fade the output to black) |
  | `T` | tap tempo |
  | `Space` | freeze or clear |

### 5.4 Output display: second monitor or resizable window
The **control window** (camera and calibration, audio sources, scenes, preview) stays on the laptop screen. The **output window** shows only the visuals.

**Display modes** (Output panel):

| Mode | What you get | How |
|---|---|---|
| **Window** | a normal window you drag and resize freely; the drawing size follows the window size and pixel density | `glfw.create_window`, framebuffer-size callback |
| **Fullscreen on monitor N** | the output covers a chosen screen at its native mode | `glfw.get_monitors()` (names, sizes, refresh rates) + `glfw.set_window_monitor(win, monitor, …)` |
| **Borderless (windowed fullscreen)** | fills a monitor without changing its video mode; best for quick switching | an undecorated window sized to the monitor's work area |

**Panel controls:**
- **Mode** and **monitor picker**, listing each monitor by name, resolution and refresh rate (on this machine: the laptop's eDP-1 and the external DP-3).
- **Identify:** flashes a large number on each monitor.
- **Render resolution:** native, or fixed (e.g. 1920×1080 for recording), plus a render scale of 50–100% for slower GPUs on 4K projectors.
- **Aspect:** Fill (crop) / Fit (black bars) / Stretch: how the camera's coordinate space, where the ball is tracked, maps onto an output of a different shape.
- **Flip:** mirror horizontally (rear projection) and vertically.
- **Hide cursor** over the output, and **black out**.
- **Remembered:** the chosen monitor and mode are restored at the next start if that monitor is connected.

**Wayland notes** (this machine: GNOME on Wayland, two monitors):
- Wayland doesn't let apps *position* normal windows, so a plain window opens wherever GNOME puts it. It can be dragged.
- Wayland *does* let an app request **fullscreen on a specific monitor**, which `set_window_monitor` uses. This is the main path; verify it in M0.
- Fallback: start GLFW in X11 mode (XWayland, the X11 compatibility layer) with `glfw.init_hint(glfw.PLATFORM, glfw.PLATFORM_X11)`, behind a `--x11` flag. Exact window placement then works.

### 5.5 Borrowed effects: Shadertoy and ISF loaders
Besides the hand-written effects in §5.2, sfere loads existing GLSL shaders as effect layers, so its library can grow without new code.

**Formats:**
| Format | Where they come from | How sfere loads them |
|---|---|---|
| **Shadertoy** (`.glsl` with `mainImage(out vec4, in vec2)`) | shadertoy.com | Wraps the code in a small header providing Shadertoy's built-in variables (`iResolution`, `iTime`, `iTimeDelta`, `iFrame`, `iMouse`, `iChannel0–3`). Multipass "Buffer A–D" shaders become ping-pong framebuffers. |
| **ISF** (`.fs` with a JSON header) | the ISF shader collections (Interactive Shader Format, used by VDMX and other VJ tools) | Reads the JSON `INPUTS` (float, color, bool, point2D, image) and **builds sliders and pickers automatically**. `PASSES` maps to framebuffers. Generators and filters are both supported. |

**Wiring them to sfere's data.** Each loaded shader also receives sfere's own uniforms:
- **Ball:** `uBall` (x, y, radius, visible), `uBallVel`.
- **Audio:** `uSub`, `uBass`, `uMid`, `uHigh`, `uBeat` (1 on a beat, then decays), `uBeatPhase`, `uBpm`.
- **Previous frame:** `uPrevFrame`, so a shader can work as a filter on the layers below it.

`iMouse` / ISF `point2D` inputs can be bound to the **ball position**, so most interactive shaders follow the ball with no edits. In the control UI, any ISF input or Shadertoy constant can be bound to an audio band or a motion feature (for example `glow ← uSub`, `rotation ← speed`), with range and smoothing per binding. Bindings are saved with the scene.

**Folders and reloading:**
- `effects/shadertoy/*.glsl` and `effects/isf/*.fs` are scanned at start and watched. Dropping in or saving a file loads or reloads it live.
- A shader that fails to compile shows its error in the control window and is skipped; the show keeps running.
- **Licenses.** Shadertoy's default license is CC BY-NC-SA 3.0 (attribution, non-commercial, share-alike), and individual authors may set others. ISF collections vary. Each loaded shader keeps its source URL, author and license in a sidecar `.toml`, and the Effects panel shows them. Paid gigs should use only shaders whose license allows it, or hand-written ones.

### 5.6 Video outputs to other applications
Alongside the output window, the final frame can also be sent to other software. Each output is optional and toggled in the Output panel, and they can all run at once.

| Output | Library | What it's for | Notes |
|---|---|---|---|
| **NDI** | `ndi-python` (NDI runtime) | Send sfere's video over the local network to Resolume, OBS, vMix or TouchDesigner on another machine. **This is how sfere feeds Windows/macOS VJ tools from this Linux laptop.** | Frames read back from the GPU asynchronously (pixel-buffer objects, one frame late) to avoid stalling rendering. 1080p60 over gigabit Ethernet is fine. The NDI runtime is free to use but proprietary; install it separately. |
| **Virtual webcam** | `pyvirtualcam` + `v4l2loopback` kernel module | sfere appears as a camera ("sfere") in OBS, browsers, video-call apps, and anything else that takes a webcam. | Not installed on this machine. Install with `sudo apt install v4l2loopback-dkms`. **Secure Boot is on here**, so Ubuntu will ask to enroll a signing key (MOK) on the next reboot, a one-time step. Load it with `exclusive_caps=1 card_label=sfere`. |
| **OSC** (data, not video) | `python-osc` | Send ball position, speed, size, found/lost, beats and bands, so another tool draws its own visuals. | Already planned (§2); listed here with the other outputs. |
| *Recording (stretch)* | `ffmpeg` subprocess | Save the output to a video file, with the analysed audio muxed in. | Reuses the NDI readback path. |

The video outputs use the **render resolution**, not the window size (§5.4). A fixed 1920×1080 keeps NDI and the virtual camera stable while the output window is resized.

---

## 6. Performance and latency targets
- **Rendering:** 60 fps (or the projector's refresh rate) at 1080p on this machine's integrated GPU. Particles are capped at about 50k on compute shaders.
- **Tracking:** ≤ 3 ms per camera frame on the camera thread (OpenCV resize, color conversion and connected components are fast C code).
- **Audio analysis:** about 0.2 ms per hop. Capture buffers of about 20 ms.
- **Motion to photon:** under 80 ms, which is mostly camera latency (a 33 ms frame period plus USB/MJPG decoding). Render-time extrapolation hides about one camera frame.
- **Measured live** in the debug overlay: camera fps, tracking ms, audio callback underruns, render ms, end-to-end latency estimate.

---

## 7. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | venv + dependencies; control window (ImGui) + output window (GL) sharing one context; source picker (webcam or video file, sample B by default); camera controls applied and restored; monitor picker + fullscreen | sample B plays in the control window; the output window goes fullscreen on DP-3 and back to a resizable window; the webcam runs at a steady 30 fps |
| M1 | Tracker (both profiles) + Kalman filter + debug overlay + click-to-calibrate + exclusion zones; `pytest` on the samples | sample B: 100% of frames where the ball is visible are tracked, and lost/found fire at both hidden stretches. Sample A: ≥ 97% tracked, 0 reflection lock-ons. Median jitter < 2 px at 320 px width. |
| M2 | Render pipeline (feedback + bloom) + orb + ribbon trail + light painting; render once, show in both windows; resize handling, render scale | smooth trail at 60 fps following the sample video on the second monitor; resizing the output window live works |
| M3 | Audio: source list (system-audio monitors, inputs, file), several sources mixed with gain + meters, hot-plug; analyser with automatic gain + kick-aligned beats; ripples and sparkle bursts on beats | `sample music v2.wav` gives 123 ± 1 BPM within 6 s **both** from the file player **and** from system audio while another app plays it; ≥ 90% of kick onsets within 50 ms of the grid; still works at −20 dB; system audio + mic can be enabled together |
| M4 | Remaining effects (embers, orbiters, spectrum halo, constellation, kaleidoscope, shockwave) + motion events + scene system + control panels; **Shadertoy + ISF loaders** with automatic controls, ball/audio uniforms, bindings, hot-reload and license display | ≥ 5 distinct presets, switching with crossfades; at least 3 Shadertoy and 3 ISF shaders (license-checked) load, follow the ball and react to `uBeat` without code changes; a broken shader shows an error and doesn't stop the show |
| M5 | Output extras (Identify, aspect/flip, black-out, borderless mode, `--x11`); **NDI and virtual-webcam outputs**; OSC output; saved settings; performance pass; live test with webcam in a dim room | 30-minute live session with no stalls, running from system audio; NDI shows up in OBS/Resolume on another machine; "sfere" appears as a camera in OBS on this one; enabling both costs < 2 ms per frame |
| Stretch | Fluid sim (compute shaders or Taichi); recording the output to a file (an `ffmpeg` pipe); MIDI control; several balls or colors | — |

---

## 8. Risks
- **Python 3.14 is very new.** Prebuilt packages (wheels) for `moderngl`, `imgui-bundle` or `opencv-python` may lag behind. Check first thing in M0; fallback: a venv on Python 3.12/3.13, for example via `uv`.
- **GLFW fullscreen on a chosen monitor under Wayland.** Expected to work, since it's a standard Wayland request, but untested here. Fallback: `--x11` (XWayland).
- **The GIL.** Python runs only one thread's Python code at a time, so three busy threads could stall each other. Mitigated because the heavy work in each thread is NumPy, OpenCV or GL calls, which release it. If tracking still stutters the renderer, move capture and tracking into a separate process with shared memory.
- **System-audio capture includes every sound the computer plays** (notifications, browser tabs). Use a specific output's monitor, or turn notification sounds down during a show.
- **The microphone hears the room late and noisily.** Prefer line-in or system audio; per-source latency offsets cover the rest.
- **Shader licenses.** Most Shadertoy shaders are non-commercial (CC BY-NC-SA). The license sidecars and the Effects panel make this visible; paid performances use permissively licensed or hand-written effects.
- **Shadertoy compatibility.** Shaders that rely on keyboard input, sound-input channels, cubemaps or WebGL-only quirks may need small edits. The loader reports which features it doesn't support rather than failing silently.
- **GPU readback for NDI or the virtual camera** can stall rendering if done synchronously. Use asynchronous pixel-buffer readback, one frame behind.
- **Virtual-camera module under Secure Boot** needs a one-time key enrollment at reboot (`v4l2loopback-dkms`). If that's unwanted, NDI into OBS covers the same uses.
- **NDI runtime** is a separate proprietary download (free to use). OSC remains the zero-install option.
- **Several red objects or lights in a venue.** Exclusion zones, the motion gate, and the white-core requirement (lit room) address this. Calibration can also select a different ball hue.

---

## 9. Open questions (sensible defaults assumed)
1. **Where is the output shown?** Answered: an output window that can be resized or put fullscreen on a chosen monitor (§5.4).
2. **Show the camera image under the effects?** Answered: no. The camera is used only for tracking.
3. **Music source?** Answered: both audio inputs and audio from other applications, alone or mixed (§4.1).
4. **One ball or several?** Default: one in version 1, with several as a stretch goal.
