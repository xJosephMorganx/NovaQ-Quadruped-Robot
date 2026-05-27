from __future__ import annotations

import base64
import logging
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cv2

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI


APP_DIR = Path(__file__).resolve().parent.parent
CAMERA_DEVICE = 0
CAMERA_FRAME_INTERVAL_SECONDS = 0.08
MJPEG_PORT = 7001
ALLOWED_MOTIONS = {"backward_step", "forward_step", "greeting", "initial", "stand"}
TRANSIENT_MOTIONS = {"backward_step", "forward_step", "greeting"}

POSE_TABLE = [
    {"name": "Shoulder_FL", "channel": 0, "initial": 165, "stand": 280, "greeting": "235-300"},
    {"name": "Leg_FL", "channel": 1, "initial": 505, "stand": 110, "greeting": 430},
    {"name": "Shoulder_FR", "channel": 2, "initial": 390, "stand": 265, "greeting": 295},
    {"name": "Leg_FR", "channel": 3, "initial": 90, "stand": 500},
    {"name": "Shoulder_BL", "channel": 4, "initial": 415, "stand": 285},
    {"name": "Leg_BL", "channel": 5, "initial": 75, "stand": 495},
    {"name": "Shoulder_BR", "channel": 6, "initial": 170, "stand": 280},
    {"name": "Leg_BR", "channel": 7, "initial": 525, "stand": 110, "greeting": 180},
]

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("uno-q-keyboard-motion")

last_result = "Ready"
last_motion = "none"
last_frame: bytes | None = None
camera_status = "Starting camera"
camera_lock = threading.Lock()


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

    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        return dict(query_params)

    return {}


def read_motion_from_request(request: Any) -> str:
    request_data = parse_request_data(request)
    motion = str(request_data.get("motion", "")).strip().lower()
    if motion:
        return motion

    path = getattr(request, "path", "")
    query = getattr(request, "query_string", "")
    if isinstance(query, bytes):
        query = query.decode("utf-8", errors="ignore")
    if not query and isinstance(path, str) and "?" in path:
        query = path.split("?", 1)[1]

    values = parse_qs(str(query))
    return values.get("motion", [""])[0].strip().lower()


def state_response() -> dict[str, Any]:
    with camera_lock:
        frame_available = last_frame is not None
        current_camera_status = camera_status

    return {
        "ok": True,
        "motions": sorted(ALLOWED_MOTIONS),
        "poseTable": POSE_TABLE,
        "lastMotion": last_motion,
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


def run_named_motion(motion: str) -> dict[str, Any]:
    global last_motion, last_result

    if motion not in ALLOWED_MOTIONS:
        last_result = f"Rejected motion: {motion or 'missing'}"
        return {
            "ok": False,
            "error": f"Motion must be one of: {', '.join(sorted(ALLOWED_MOTIONS))}",
            "lastResult": last_result,
        }

    try:
        bridge_result = Bridge.call("run_motion", motion)
    except Exception as error:
        last_result = f"Bridge error: {error}"
        logger.exception("Bridge call failed")
        return {"ok": False, "error": last_result, "lastResult": last_result}

    last_motion = motion
    if motion in TRANSIENT_MOTIONS:
        last_motion = "stand"
    last_result = f"Motion {motion} executed"

    return {
        "ok": True,
        "motion": motion,
        "bridgeResult": bridge_result,
        "lastResult": last_result,
    }


def motion_response(request: Any = None) -> dict[str, Any]:
    return run_named_motion(read_motion_from_request(request))


def initial_motion_response() -> dict[str, Any]:
    return run_named_motion("initial")


def stand_motion_response() -> dict[str, Any]:
    return run_named_motion("stand")


def greeting_motion_response() -> dict[str, Any]:
    return run_named_motion("greeting")


def forward_step_response() -> dict[str, Any]:
    return run_named_motion("forward_step")


def backward_step_response() -> dict[str, Any]:
    return run_named_motion("backward_step")


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


threading.Thread(target=camera_capture_loop, daemon=True).start()
threading.Thread(target=start_mjpeg_server, daemon=True).start()

web_ui = WebUI()
web_ui.expose_api("GET", "/api/state", state_response)
web_ui.expose_api("GET", "/api/motion/initial", initial_motion_response)
web_ui.expose_api("GET", "/api/motion/stand", stand_motion_response)
web_ui.expose_api("GET", "/api/motion/greeting", greeting_motion_response)
web_ui.expose_api("GET", "/api/motion/forward-step", forward_step_response)
web_ui.expose_api("GET", "/api/motion/backward-step", backward_step_response)
web_ui.expose_api("GET", "/api/motion", motion_response)
web_ui.expose_api("POST", "/api/motion", motion_response)
web_ui.expose_api("GET", "/api/camera-frame", camera_frame_response)

App.run()
