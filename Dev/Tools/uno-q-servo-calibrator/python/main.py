from __future__ import annotations

import base64
import json
import logging
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import cv2

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI


APP_DIR = Path(__file__).resolve().parent.parent
CALIBRATION_FILE = Path(__file__).resolve().parent / "calibration_values.json"

PCA9685_CHANNEL_MIN = 0
PCA9685_CHANNEL_MAX = 7
SAFE_PULSE_MIN = 50
SAFE_PULSE_MAX = 600
DEFAULT_PULSE = 300
CAMERA_DEVICE = 0
CAMERA_FRAME_INTERVAL_SECONDS = 0.08
MJPEG_PORT = 7001

SERVO_TABLE = [
    {"name": "Shoulder_FL", "channel": 0, "min": 70, "max": 495},
    {"name": "Leg_FL", "channel": 1, "min": 70, "max": 495},
    {"name": "Shoulder_FR", "channel": 2, "min": 70, "max": 500},
    {"name": "Leg_FR", "channel": 3, "min": 70, "max": 500},
    {"name": "Shoulder_BL", "channel": 4, "min": 70, "max": 500},
    {"name": "Leg_BL", "channel": 5, "min": 70, "max": 500},
    {"name": "Shoulder_BR", "channel": 6, "min": 70, "max": 495},
    {"name": "Leg_BR", "channel": 7, "min": 70, "max": 495},
]

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("uno-q-servo-calibrator")


def default_calibration() -> dict[str, dict[str, Any]]:
    return {
        str(servo["channel"]): {
            "name": servo["name"],
            "channel": servo["channel"],
            "min": servo["min"],
            "max": servo["max"],
            "pulse": DEFAULT_PULSE,
        }
        for servo in SERVO_TABLE
    }


calibration_values = default_calibration()
current_channel = 0
last_result = "Ready"
last_frame: bytes | None = None
camera_status = "Starting camera"
camera_lock = threading.Lock()


def load_calibration() -> None:
    global calibration_values
    if not CALIBRATION_FILE.exists():
        return

    try:
        saved_values = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Could not load calibration file: %s", error)
        return

    merged_values = default_calibration()
    for channel, servo in merged_values.items():
        saved_servo = saved_values.get(channel, {})
        pulse = saved_servo.get("pulse", servo["pulse"])
        servo["pulse"] = clamp_int(pulse, SAFE_PULSE_MIN, SAFE_PULSE_MAX)

    calibration_values = merged_values


def save_calibration_to_disk() -> None:
    CALIBRATION_FILE.write_text(
        json.dumps(calibration_values, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        raise ValueError("Value must be an integer") from None

    return max(minimum, min(maximum, numeric_value))


def parse_request_data(request: Any) -> dict[str, Any]:
    """Accept WebUI request bodies or plain dictionaries."""
    if isinstance(request, dict):
        return dict(request)

    data = getattr(request, "json", None)
    if callable(data):
        try:
            parsed_json = data()
        except Exception:
            parsed_json = None
        if isinstance(parsed_json, dict):
            return parsed_json
    elif isinstance(data, dict):
        return data

    args = getattr(request, "args", None)
    if isinstance(args, dict):
        return dict(args)

    return {}


def state_response() -> dict[str, Any]:
    with camera_lock:
        frame_available = last_frame is not None
        current_camera_status = camera_status

    return {
        "ok": True,
        "servos": list(calibration_values.values()),
        "currentChannel": current_channel,
        "lastResult": last_result,
        "camera": {
            "available": frame_available,
            "status": current_camera_status,
            "snapshotEndpoint": "/api/camera-frame",
            "mjpegEndpoint": f"http://{get_host_placeholder()}:{MJPEG_PORT}/video-feed",
        },
    }


def get_host_placeholder() -> str:
    # The browser replaces this value with window.location.hostname.
    return "__HOST__"


def set_pulse(data: dict[str, Any]) -> dict[str, Any]:
    global current_channel, last_result

    try:
        channel = clamp_int(data.get("channel"), PCA9685_CHANNEL_MIN, PCA9685_CHANNEL_MAX)
        pulse = clamp_int(data.get("pulse"), SAFE_PULSE_MIN, SAFE_PULSE_MAX)
    except ValueError as error:
        last_result = str(error)
        return {"ok": False, "error": str(error)}

    try:
        bridge_result = Bridge.call("set_servo_pulse", channel, pulse)
    except Exception as error:
        last_result = f"Bridge error: {error}"
        logger.exception("Bridge call failed")
        return {"ok": False, "error": last_result}

    current_channel = channel
    calibration_values[str(channel)]["pulse"] = pulse
    last_result = f"Channel {channel} set to {pulse}"

    return {
        "ok": True,
        "channel": channel,
        "pulse": pulse,
        "bridgeResult": bridge_result,
        "lastResult": last_result,
    }


def save_calibration() -> dict[str, Any]:
    global last_result
    try:
        save_calibration_to_disk()
    except OSError as error:
        last_result = f"Save failed: {error}"
        return {"ok": False, "error": last_result}

    last_result = "Calibration saved"
    return {
        "ok": True,
        "path": str(CALIBRATION_FILE),
        "calibration": calibration_values,
    }


def calibration_response() -> dict[str, Any]:
    return {"ok": True, "calibration": calibration_values}


def camera_frame_response() -> dict[str, Any]:
    with camera_lock:
        if last_frame is None:
            return {"ok": False, "available": False, "status": camera_status}
        encoded_frame = base64.b64encode(last_frame).decode("ascii")
        return {
            "ok": True,
            "available": True,
            "status": camera_status,
            "mimeType": "image/jpeg",
            "data": encoded_frame,
        }


def camera_capture_loop() -> None:
    global last_frame, camera_status

    while True:
        capture = cv2.VideoCapture(CAMERA_DEVICE)
        if not capture.isOpened():
            with camera_lock:
                camera_status = f"Camera unavailable on /dev/video{CAMERA_DEVICE}"
                last_frame = None
            time.sleep(2)
            continue

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        with camera_lock:
            camera_status = "Camera connected"

        while capture.isOpened():
            success, frame = capture.read()
            if not success:
                with camera_lock:
                    camera_status = "Camera frame read failed"
                    last_frame = None
                break

            success, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if success:
                with camera_lock:
                    last_frame = jpeg.tobytes()
                    camera_status = "Camera connected"

            time.sleep(CAMERA_FRAME_INTERVAL_SECONDS)

        capture.release()
        time.sleep(1)


class MjpegHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path != "/video-feed":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        while True:
            with camera_lock:
                frame = last_frame
            if frame is None:
                time.sleep(0.25)
                continue

            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(CAMERA_FRAME_INTERVAL_SECONDS)
            except (BrokenPipeError, ConnectionResetError):
                break

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def start_mjpeg_server() -> None:
    try:
        server = ThreadingHTTPServer(("0.0.0.0", MJPEG_PORT), MjpegHandler)
    except OSError as error:
        logger.warning("Could not start MJPEG server on port %s: %s", MJPEG_PORT, error)
        return

    logger.info("MJPEG camera stream available on port %s", MJPEG_PORT)
    server.serve_forever()


load_calibration()

threading.Thread(target=camera_capture_loop, daemon=True).start()
threading.Thread(target=start_mjpeg_server, daemon=True).start()

web_ui = WebUI()
web_ui.expose_api("GET", "/api/state", state_response)
web_ui.expose_api("POST", "/api/set-pulse", set_pulse)
web_ui.expose_api("POST", "/api/save-calibration", save_calibration)
web_ui.expose_api("GET", "/api/calibration", calibration_response)
web_ui.expose_api("GET", "/api/camera-frame", camera_frame_response)

App.run()
