# sfere guide

sfere follows a glowing contact-juggling ball with a webcam and draws visuals around it that react to the music. The camera is used only to find the ball: the output shows rendered graphics, never the room or the performer.

This guide covers setting up for a show, every part of the control window, touch screens, and adding your own shaders. The same text is in the **Help** tab of the control window.

## What you need

- A webcam that can see the whole performance area. A Logitech C920 at 720p and 30 fps works well.
- A ball with a light inside it, ideally a saturated color (red, green, blue or purple).
- A dim room. sfere works in a lit room too, but a dark stage gives the cleanest tracking.
- Music, from any of these:
  - another app on the same computer (a music player, a DJ app, a browser tab);
  - a microphone or line-in;
  - several of these mixed together.
- A projector or second display for the output. Without one, the output stays in a window.

## Starting sfere

Open **sfere** from the app drawer, or run it from a terminal:

```
.venv/bin/python -m sfere
```

Two windows open:

- **sfere — output**: the visuals. If an external display is connected, it goes fullscreen there by default.
- **sfere — control**: everything you adjust during setup and the show.

sfere remembers your calibration, audio sources, scene edits and output settings in `~/.config/sfere/settings.toml` and restores them at the next start.

## The control window

The top of the control window is always visible:

- **Status line**: frame rate, the current scene, whether the ball is found, and the tempo. The yellow bar flashes on every beat.
- **Output preview**: exactly what the output window shows.
- **Show buttons**: Fullscreen, Black out, Freeze, Tap tempo and Mirror. Each button lists its keyboard shortcut. A highlighted button means that mode is on.
- **Scene buttons**: one per scene. Tap or click one to crossfade to it.
- **Messages**: shader errors and other warnings, in orange.

Below that, six tabs hold the settings: Camera, Audio, Scenes, Output, Debug and Help.

## 1. Camera: calibrate the ball

The Camera tab shows the webcam image with the detected ball pixels painted green.

1. Hold the lit ball where the camera can see it.
2. Tap or click the ball in the camera image. sfere reads its color and sets the hue, saturation and brightness thresholds. The line under the image confirms the values, or says "Nothing bright there" if you missed.
3. If a lamp, sign or reflection also turns green, drag a box over it to add an **exclusion zone** (a right-drag works too). The ball is never tracked inside a zone. **Clear zones** removes them all.
4. Move the ball around the whole performance area and check that the green covers it everywhere.

Other settings:

- **Mirror**: flips the camera image left to right, so moving the ball to your right moves it right on screen.
- **Tracking profile**:
  - **dark** suits a dim stage;
  - **lit** suits a lit room: it also asks for the ball's bright white core, which rules out red clothes and furniture;
  - **auto** picks between them.
- **Ball hue, Hue tolerance, Min saturation, Min brightness**: the thresholds that calibration sets. Fine-tune them by hand if needed.
- **Exposure and Gain**: the webcam's own settings, for cameras that allow it. A short exposure keeps the ball sharp when it moves fast. sfere puts the camera back the way it found it when it exits.

## 2. Audio: choose the music

- **Audio from other apps** lists the computer's outputs. Tick one to hear whatever plays through it. The default output is ticked at first start.
- **Inputs** lists microphones and line-ins.

You can tick several sources; they are mixed. Each one has a level meter and a **gain** slider. If a source stops working (unplugged, muted at the system level), its status appears next to it in orange and sfere carries on with the others.

The bars at the top show the energy in four bands: sub, bass, mid and high. Under them, the spectrum.

**Tempo** locks within a few seconds of steady music with a kick drum. If it can't lock (a track without drums, a live band), tap **Tap tempo** in time with the music, at least three taps.

**Latency offset** shifts the beat earlier or later. Use it when the visuals hit consistently before or after the music, for example with a microphone across a large room.

The **File player** plays an audio file through sfere for testing at home.

## 3. Scenes: pick and edit the look

A scene is a stack of effect layers plus post-processing. sfere comes with seven:

| Scene | Look |
|---|---|
| 1 Rave | Ribbon trail, sparkles on onsets, rings and shockwaves on beats |
| 2 Museum | The path drawn as a slow constellation of stars |
| 3 Cosmic | Satellites orbiting the ball, the spectrum as a halo, a kaleidoscope |
| 4 Ember Storm | A comet tail of embers that rises with the sub-bass |
| 5 Light Painting | Long-exposure trails that build up over time |
| 6 Neon Shaders | Downloaded-style shaders: hexagons, a plasma orb, a radial burst |
| 7 Ink Tunnel | A tunnel and multipass ink trails |

Switch scenes with the scene buttons at the top or the keys 1 to 9. **Crossfade** sets how long the change takes.

The Scenes tab edits the current scene:

- **Post** controls the whole image:
  - **decay**: how long trails linger;
  - **bloom**: glow around bright areas;
  - **exposure**: overall brightness;
  - **zoom, rotate**: make the feedback trails drift;
  - **kaleido**: kaleidoscope mirrors, folded around the ball.
- Each **layer** opens to show its settings. A setting marked "← bound" follows the music or the ball automatically.
- **Remove layer** deletes a layer from the scene.
- **Add a layer** lists every built-in effect and shader (over 200). Type in **Search** to filter, then tap or click one to add it on top. Filter shaders, like blurs and color effects, process the layers below them.
- **Reset layers to the preset** appears once you have edited a scene, and undoes the edits.

Edited scenes are saved when sfere exits.

## 4. Output: the projector

- **Fullscreen on ...**: one button per display. **Back to a window** returns to a normal window.
- **Frame rate**: match the display, or a lower rate to save power and heat.
- **Go fullscreen on an external display at start and when one is plugged in**: on by default. Turn it off to keep the output in a window.
- **Render scale**: renders at a fraction of the display's resolution. Lower it if the frame rate drops.
- **Aspect**: how the camera's view maps onto the output. **fit** keeps the whole view, **fill** crops it to fill the screen, **stretch** fills it without keeping the shape.
- **Flip horizontally**: for rear projection. **Flip vertically**: for a projector mounted upside down.
- **Black out**: fades the output to black. It fades back in when you turn it off.
- **Debug marker**: a green cross on the output where sfere places the ball, and a beat flash in the corner.
- **Control window**: touch-screen settings; see the next section.

## Using a touch screen

sfere notices a connected touch screen and switches the control window to touch mode:

- Every button, slider, checkbox and tab gets bigger and easier to hit, with a larger font.
- Drag a finger up or down anywhere in a tab to scroll it, including over buttons and sliders. A sideways drag moves the slider under your finger instead. If a scroll starts on a slider, that slider goes back to its previous value.
- Drag a finger up or down the **Add a layer** list to scroll it; tap an effect to add it.
- The show controls have buttons at the top of the window, so you don't need the keyboard shortcuts. The debug marker is in the Output tab.
- In the Camera tab, tap the ball to calibrate, and drag a box to exclude a reflection.

To make touches reach its windows, sfere runs through XWayland when a touch screen is connected. GLFW, the library sfere uses for windows, ignores touches on native Wayland windows.

Change this in **Output → Control window → Touch-friendly controls**:

- **Automatic**: touch mode when a touch screen is connected (the default).
- **On**: the large controls even without a touch screen.
- **Off**: normal-size controls and native Wayland, even with a touch screen. Fingers then do nothing in sfere's windows.

A change of display backend takes effect the next time sfere starts. For one start, use `--touch on` or `--touch off` on the command line.

Text fields (the shader search, the file player) still need a keyboard. GNOME's on-screen keyboard doesn't open for XWayland windows by itself, so turn it on in **Settings → Accessibility → Typing** if you need it.

## Keyboard shortcuts

Keys work when either sfere window has focus, unless you are typing in a text field.

| Key | Action |
|---|---|
| 1 to 9 | Switch scene, with a crossfade |
| F | Toggle fullscreen |
| Shift+F | Move fullscreen to the next display |
| Esc | Leave fullscreen |
| B | Black out |
| T | Tap tempo |
| M | Mirror the camera |
| D | Debug marker on the output |
| C | Show the Camera tab |
| Space | Freeze the output |
| Ctrl+Q | Quit |

## Adding shaders

Drop shader files into the `effects` folder and they load straight away. Saving a file reloads it, so you can edit a shader while sfere runs.

- `effects/shadertoy/name.glsl`: a Shadertoy shader's Image pass. Add `name.buffer_a.glsl` to `name.buffer_d.glsl` for Buffers A to D.
- `effects/isf/name.fs`: an ISF shader. sfere builds its controls from the JSON header.

Subfolders work too. New shaders appear in **Scenes → Add a layer**.

An optional `name.toml` next to a shader describes it and sets defaults:

```
title = "Plasma orb"
author = "you"
license = "CC BY-NC-SA 3.0"
source = "https://www.shadertoy.com/view/..."
gain = 0.7          # brightness of the layer
resolution = 0.5    # render at half resolution
```

It can also hold `[params]` and `[channels]` tables. A `pack.toml` in a folder sets defaults for every shader in that folder.

Every shader receives sfere's own uniforms:

| Uniform | Meaning |
|---|---|
| uBall | Ball position x, y and radius in output coordinates (0 to 1), and visibility |
| uBallVel | Ball velocity |
| uSub, uBass, uMid, uHigh, uLevel | Band energies and overall level, 0 to 1 |
| uBeat | 1 on a beat, fading to 0 |
| uBeatPhase | Position within the beat, 0 to 1 |
| uBpm | The tempo |
| uPrevFrame | The previous output frame |

Shadertoy's `iMouse` and ISF `point2D` inputs follow the ball, so most shaders react to it without changes. If a shader fails to compile, its error appears in the messages and the show keeps running.

Check each shader's license before a paid performance. Most Shadertoy shaders are non-commercial (CC BY-NC-SA). The license of every shader shows when you open its layer in the Scenes tab. The Vidvox shaders in `effects/isf/vidvox/` are MIT-licensed.

## Command line

| Option | Effect |
|---|---|
| --video FILE | Use a recording instead of the webcam (it loops) |
| --audio-file FILE | Play and analyse an audio file instead of live audio |
| --audio ID | Use this live audio source; repeat for several |
| --mute | Analyse --audio-file without playing it |
| --scene NAME | Start with this scene |
| --touch auto, on or off | Touch mode for this start only |
| --x11 | Run through XWayland |
| --no-save | Don't save settings on exit |
| --capture DIR | Save screenshots every few seconds (testing) |
| --duration S | Quit after this many seconds |

## Troubleshooting

- **The ball isn't found.** Calibrate again in the Camera tab. Make sure the green mask covers the ball in every part of the stage. In a lit room, choose the **lit** profile.
- **Something else is tracked.** Drag an exclusion zone over it, or lower **Hue tolerance**.
- **The tempo is wrong or never locks.** Check the meters in the Audio tab: the right source must be ticked and moving. Raise its gain if the music is quiet. Use **Tap tempo** for music without a steady kick.
- **A microphone shows "stuck" or no signal.** It is muted or misconfigured at the system level. Check it in GNOME Settings → Sound.
- **The output appears on the wrong display.** Press Shift+F, or use the Fullscreen buttons in the Output tab.
- **The frame rate drops.** Lower **Render scale**, choose a lower **Frame rate**, or remove heavy shader layers.
- **Touches do nothing.** Check **Output → Control window**: it should show "Display: XWayland". If not, set Touch-friendly controls to Automatic or On and restart sfere.
- **A shader shows an error.** The message names the line. Fix the file and save it; sfere reloads it at once.
