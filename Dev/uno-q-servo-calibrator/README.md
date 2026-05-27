# UNO Q Servo Calibrator

Web-based calibration tool for the quadruped robot servos using Arduino UNO Q,
Arduino App Lab, PCA9685, and MG996R servos.

## Overview

This app runs with the Arduino UNO Q split architecture:

```text
Browser over Wi-Fi
  -> Arduino App Lab WebUI on UNO Q Linux
  -> Python Bridge call
  -> UNO Q microcontroller sketch
  -> PCA9685 over I2C
  -> MG996R servos
```

The USB camera is read from the Linux side with OpenCV. Servo commands do not use
USB serial; they use the UNO Q Bridge API.

![Servo calibration UI placeholder](docs/servo-calibrator-ui.png)
![Robot camera feed placeholder](docs/camera-feed.png)

## Project Structure

```text
uno-q-servo-calibrator/
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

## Default Servo Table

| Articulation | Channel | Pulse Min 0 deg | Pulse Max 180 deg |
| --- | ---: | ---: | ---: |
| Shoulder_FL | 0 | 70 | 495 |
| Leg_FL | 1 | 70 | 495 |
| Shoulder_FR | 2 | 70 | 500 |
| Leg_FR | 3 | 70 | 500 |
| Shoulder_BL | 4 | 70 | 500 |
| Leg_BL | 5 | 70 | 500 |
| Shoulder_BR | 6 | 70 | 495 |
| Leg_BR | 7 | 70 | 495 |

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

The UI also has a snapshot-based camera mode through WebUI, so calibration still
works if the extra MJPEG port is not exposed.

## Calibration Workflow

1. Select a PCA9685 channel from `0` to `7`.
2. Enter a pulse value or use the `-5`, `-1`, `+1`, and `+5` buttons.
3. Press `Send` or press `Enter` in the pulse input.
4. Watch the servo position and camera feed.
5. Press `Save` when the current working values are correct.
6. Use `Export JSON` to download the values from the browser.

The app does not move any servo automatically at startup. A servo only moves after
you send a command from the UI.

## Generated Calibration File

Saved values are written on the UNO Q Linux side to:

```text
python/calibration_values.json
```

This file is generated at runtime and can later be copied into the walking or
vision-control code as the robot's calibrated servo reference.

## Notes

- Safe pulse commands are clamped between `50` and `600`.
- The camera defaults to `/dev/video0`.
- If the camera is disconnected, the UI shows a camera unavailable state and the
  servo calibrator remains usable.
- Use App Lab Monitor logs instead of USB serial logs.
