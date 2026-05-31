# UNO Q Keyboard Motion

Web app for controlling the quadruped robot with keyboard-triggered poses while
watching a live USB camera feed. It uses Arduino UNO Q, Arduino App Lab,
PCA9685, and MG996R servos.

## Overview

```text
Browser over Wi-Fi
  -> Arduino App Lab WebUI on UNO Q Linux
  -> Python Bridge call
  -> UNO Q microcontroller sketch motion function
  -> PCA9685 over I2C
  -> MG996R servos
```

The USB camera is read from the Linux side with OpenCV. Servo animations live in
the Arduino sketch and Python calls them through the UNO Q Bridge API.

## Project Structure

```text
uno-q-keyboard-motion/
+-- app.yaml
+-- assets/
|   +-- app.js
|   +-- index.html
|   +-- styles.css
+-- python/
|   +-- main.py
|   +-- requirements.txt
+-- sketch/
|   +-- sketch.ino
|   +-- sketch.yaml
+-- README.md
```

## Hardware

| Device | Connection |
| --- | --- |
| PCA9685 | UNO Q I2C bus |
| PCA9685 address | `0x40` |
| Servo signal channels | `0-7` |
| Servo power | External 6 V from buck converter |
| Camera | USB/UVC camera through USB hub |
| Common ground | UNO Q ground and servo power ground connected |

Do not power the MG996R servos from the UNO Q. Use the external 6 V servo supply.

## Keyboard Controls

| Key | Motion | Description |
| --- | --- | --- |
| `Arrow Down` | `initial` | Arms vertical up with a slightly slower stage, then shoulders at 90-degree calibrated pulses |
| `Arrow Up` | `stand` | Shoulders at midpoint, short delay, arms vertical down |
| `Arrow Right` | `greeting` | Short FL arm wave, then returns to stand |
| `W` hold | `forward_step` loop | Gait forward while held, returns to stand on release |
| `S` hold | `backward_step` loop | Gait backward while held, returns to stand on release |
| `A` | future | Left turn |
| `D` | future | Right turn |
| `Page Down` | future | OpenCV blue ball detection |
| `F4` | future | MediaPipe hand controller |
| `Space` | future | Manual mode |
| `Escape` | UI | Toggle Focus View |

The web UI also includes buttons for both motions. It remembers the current pose
and always sends requested motion commands without skipping repeated states.
`Focus View` hides the dashboard and leaves the camera feed fullscreen-style
with status overlaid on top of the video; press `Escape` to toggle it from the
keyboard.

## Calibrated Pulse Targets

| Articulation | Channel | Initial | Stand | Greeting |
| --- | ---: | ---: | ---: | --- |
| Shoulder_FL | 0 | 170 | 280 | waves between `235-300` |
| Leg_FL | 1 | 513 | 110 | lifts to `430` |
| Shoulder_FR | 2 | 410 | 265 | support to `295` |
| Leg_FR | 3 | 90 | 500 |  |
| Shoulder_BL | 4 | 412 | 295 |  |
| Leg_BL | 5 | 75 | 495 |  |
| Shoulder_BR | 6 | 175 | 280 |  |
| Leg_BR | 7 | 525 | 110 | support lift to `180` |

These are direct PCA9685 pulse values from the verified calibration table. They
are not converted from degrees at runtime.

## Motion Behavior

- Motion functions are defined in `sketch/sketch.ino`.
- Python calls `Bridge.call("run_motion", motion)`.
- Supported motion names are `initial`, `stand`, `greeting`, `forward_step`, and
  `backward_step`.
- Servo pulses are clamped to `50-600`.
- Smooth movement uses simultaneous group steps of `5` pulse counts every `15 ms`.
- `initial` moves all arms up together with a slightly slower `22 ms` step delay,
  waits `500 ms`, then moves all shoulders together.
- `stand` moves all shoulders together, waits `500 ms`, then moves all arms down together.
- `greeting` starts from `stand`, moves `Shoulder_FR` a little toward 90 for
  support first, then lifts FL and slightly raises BR. It waves `Shoulder_FL`
  three times in a short mid-range, then lowers FL before returning BR and
  `Shoulder_FR` to `stand`.
- `forward_step` is a frame-based diagonal gait cycle that plants arms at
  vertical down for stability, uses a reduced shoulder range, and moves quickly
  with `5 ms` servo steps and `20 ms` frame pauses. Each diagonal pair
  repositions while lifted, plants, then only that pair pushes; the web app
  repeats it while `W` is held and sends `stand` only when `W` is released.
- `backward_step` uses the same timing and lifts as `forward_step`, with
  shoulder place/push ranges reversed. The web app repeats it while `S` is held
  and sends `stand` only when `S` is released.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/state` | Current app state, pose table, and camera status |
| `GET` | `/api/motion/initial` | Runs `initial` through Bridge |
| `GET` | `/api/motion/stand` | Runs `stand` through Bridge |
| `GET` | `/api/motion/greeting` | Runs `greeting` through Bridge |
| `GET` | `/api/motion/forward-step` | Runs one conservative forward gait step |
| `GET` | `/api/motion/backward-step` | Runs one conservative backward gait step |
| `GET` | `/api/motion?motion=initial` | Manual API compatibility endpoint |
| `POST` | `/api/motion` | Also accepts `initial` or `stand` for manual API tests |
| `GET` | `/api/camera-frame` | Snapshot camera frame as base64 JPEG |
| `GET` | `:7001/video-feed` | Optional MJPEG stream |

Example motion request:

```text
GET /api/motion/initial
```

POST body alternative:

```json
{ "motion": "initial" }
```

## Running in Arduino App Lab

1. Copy or import this folder into Arduino App Lab.
2. Install the sketch dependency `Adafruit PWM Servo Driver Library` if App Lab
   does not install it automatically.
3. Connect the UNO Q and the computer to the same Wi-Fi network.
4. Start the app from App Lab.
5. Open the WebUI at:

```text
http://<UNO_Q_IP>:7000
```

The optional MJPEG stream is available at:

```text
http://<UNO_Q_IP>:7001/video-feed
```

## Modo hotspot / red propia

The UNO Q has a Linux side based on Debian, Wi-Fi, and Arduino Bridge/RPC for
communication between Linux and the microcontroller. Arduino documents this
architecture in the UNO Q hardware page:

https://docs.arduino.cc/hardware/uno-q/

For App Lab network mode, Arduino notes that the board is detected over USB or
when the computer and UNO Q are on the same Wi-Fi network. For Wi-Fi detection,
the first network setup requires connecting through USB first:

https://support.arduino.cc/hc/en-us/articles/23170726082332-If-your-Arduino-UNO-Q-is-not-detected-by-Arduino-App-Lab

Recommended safe workflow:

1. First test this app over USB or over your normal local Wi-Fi.
2. Configure any hotspot/access point manually on the UNO Q Linux side as a
   separate NetworkManager task.
3. Reconnect the computer to that hotspot.
4. Open the app with the UNO Q hotspot IP address.

Do not make this app automatically switch Wi-Fi networks. If the app changes the
network while you are controlling the robot, the browser connection can drop in
the middle of a motion command.

## Hardware Test Checklist

1. Start with servo power disconnected and confirm the app loads.
2. Watch App Lab Monitor logs while pressing `Arrow Down` and `Arrow Up`.
3. Connect servo power with the robot lifted off the table.
4. Press `Arrow Down` and verify the initial pose.
5. Press `Arrow Up` and verify all shoulders move together, then all arms move down
   together after the delay.
6. Keep a physical power switch or battery disconnect within reach during early
   tests.
