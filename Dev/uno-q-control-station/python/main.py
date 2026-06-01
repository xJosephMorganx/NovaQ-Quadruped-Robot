from __future__ import annotations

import base64
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
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
CAMERA_FRAME_INTERVAL_SECONDS = 0.04
MJPEG_PORT = 7001
VISION_FRAME_INTERVAL_SECONDS = 0.06
OPENCV_STEP_COOLDOWN_SECONDS = 0.65
OPENCV_STAND_SETTLE_SECONDS = 0.25
BLUE_HSV_LOWER = (98, 105, 55)
BLUE_HSV_UPPER = (135, 255, 255)
BLUE_MIN_MASK_AREA = 380
BLUE_MIN_RADIUS = 12
BLUE_MAX_RADIUS_RATIO = 0.32
BLUE_MIN_CIRCULARITY = 0.72
BLUE_MIN_FILL_RATIO = 0.50
BLUE_MAX_FILL_RATIO = 1.18
BLUE_CENTER_DEADZONE_RATIO = 0.14

CONTROL_MODES = {"manual", "opencv", "mediapipe"}
MOTION_MODES = {"initial", "stand", "greeting", "forward", "backward", "turn_left", "turn_right"}
CONTINUOUS_MOTION_MODES = {"forward", "backward", "turn_left", "turn_right"}
ONE_SHOT_MOTION_MODES = {"initial", "stand", "greeting"}
MODE_TO_LEGACY_MOTION = {
    "initial": "initial",
    "stand": "stand",
    "greeting": "greeting",
    "forward": "forward_step",
    "backward": "backward_step",
    "turn_left": "turn_left_step",
    "turn_right": "turn_right_step",
}
LEGACY_MOTION_TO_MODE = {legacy: mode for mode, legacy in MODE_TO_LEGACY_MOTION.items()}

POSE_TABLE = [
    {"name": "Shoulder_FL", "channel": 0, "initial": 170, "stand": 280, "greeting": "235-300"},
    {"name": "Leg_FL", "channel": 1, "initial": 513, "stand": 110, "greeting": 430},
    {"name": "Shoulder_FR", "channel": 2, "initial": 376, "stand": 265, "greeting": 295},
    {"name": "Leg_FR", "channel": 3, "initial": 90, "stand": 500},
    {"name": "Shoulder_BL", "channel": 4, "initial": 412, "stand": 295},
    {"name": "Leg_BL", "channel": 5, "initial": 75, "stand": 495},
    {"name": "Shoulder_BR", "channel": 6, "initial": 175, "stand": 280},
    {"name": "Leg_BR", "channel": 7, "initial": 525, "stand": 110, "greeting": 180},
]

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("uno-q-control-station")

last_frame: bytes | None = None
last_vision_frame: bytes | None = None
last_cv_frame: Any = None
camera_status = "Starting camera"
camera_lock = threading.Lock()


def parse_request_data(request: Any) -> dict[str, Any]:
    """Accept WebUI request bodies, dictionaries, and query strings."""
    if isinstance(request, dict):
        return dict(request)

    for attribute_name in ("json", "body", "data", "form"):
        data = getattr(request, attribute_name, None)
        if callable(data):
            try:
                data = data()
            except Exception:
                data = None
        if isinstance(data, dict):
            return dict(data)
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="ignore")
        if isinstance(data, str) and data.strip():
            try:
                parsed_json = json.loads(data)
            except json.JSONDecodeError:
                parsed_json = None
            if isinstance(parsed_json, dict):
                return parsed_json

    args = getattr(request, "args", None)
    if isinstance(args, dict):
        return dict(args)

    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        return dict(query_params)

    path = getattr(request, "path", "")
    query = getattr(request, "query_string", "")
    if isinstance(query, bytes):
        query = query.decode("utf-8", errors="ignore")
    if not query and isinstance(path, str) and "?" in path:
        query = path.split("?", 1)[1]
    if query:
        return {key: values[-1] for key, values in parse_qs(str(query)).items() if values}

    return {}


def get_host_placeholder() -> str:
    # The browser replaces this value with window.location.hostname.
    return "__HOST__"


@dataclass
class MotionSnapshot:
    app_mode: str
    desired_motion: str
    current_motion: str
    generation: int
    busy: bool
    last_result: str
    last_error: str | None
    bridge_function: str


class MotionController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._generation = 0
        self._app_mode = "manual"
        self._desired_motion = "stand"
        self._current_motion = "stand"
        self._busy = False
        self._last_result = "Ready"
        self._last_error: str | None = None
        self._bridge_function = "run_mode_step"
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def set_mode(self, mode: str) -> dict[str, Any]:
        normalized_mode = mode.strip().lower().replace("-", "_")
        if normalized_mode in CONTROL_MODES:
            with self._lock:
                self._generation += 1
                self._app_mode = normalized_mode
                self._desired_motion = "stand"
                generation = self._generation
                self._last_result = f"{normalized_mode} mode selected"
                self._last_error = None
            self._wake_event.set()
            return {"ok": True, "acceptedMode": normalized_mode, "generation": generation, "state": self.snapshot_dict()}

        if normalized_mode not in MOTION_MODES:
            allowed = sorted(CONTROL_MODES | MOTION_MODES)
            return {"ok": False, "error": f"Mode must be one of: {', '.join(allowed)}", "state": self.snapshot_dict()}

        with self._lock:
            self._generation += 1
            self._app_mode = "manual"
            self._desired_motion = normalized_mode
            generation = self._generation
            self._last_result = f"Desired motion set to {normalized_mode}"
            self._last_error = None
        self._wake_event.set()
        return {"ok": True, "acceptedMode": normalized_mode, "generation": generation, "state": self.snapshot_dict()}

    def set_autonomous_motion(self, owner_mode: str, motion_mode: str, reason: str) -> None:
        normalized_owner = owner_mode.strip().lower().replace("-", "_")
        normalized_motion = motion_mode.strip().lower().replace("-", "_")
        if normalized_owner not in {"opencv", "mediapipe"} or normalized_motion not in MOTION_MODES:
            return

        with self._lock:
            if self._app_mode != normalized_owner:
                return
            if self._desired_motion == normalized_motion:
                return

            self._generation += 1
            self._desired_motion = normalized_motion
            self._last_result = reason
            self._last_error = None
        self._wake_event.set()

    def run_legacy_motion(self, motion: str) -> dict[str, Any]:
        normalized_motion = motion.strip().lower().replace("-", "_")
        mode = LEGACY_MOTION_TO_MODE.get(normalized_motion, normalized_motion)
        return self.set_mode(mode)

    def snapshot(self) -> MotionSnapshot:
        with self._lock:
            return MotionSnapshot(
                app_mode=self._app_mode,
                desired_motion=self._desired_motion,
                current_motion=self._current_motion,
                generation=self._generation,
                busy=self._busy,
                last_result=self._last_result,
                last_error=self._last_error,
                bridge_function=self._bridge_function,
            )

    def snapshot_dict(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "appMode": snapshot.app_mode,
            "desiredMotion": snapshot.desired_motion,
            "currentMotion": snapshot.current_motion,
            "generation": snapshot.generation,
            "busy": snapshot.busy,
            "lastResult": snapshot.last_result,
            "lastError": snapshot.last_error,
            "bridgeFunction": snapshot.bridge_function,
        }

    def _run(self) -> None:
        while True:
            snapshot = self.snapshot()

            if snapshot.app_mode != "manual":
                if snapshot.desired_motion in CONTINUOUS_MOTION_MODES:
                    self._execute_step(snapshot.desired_motion, snapshot.generation)
                    continue

                if snapshot.current_motion != snapshot.desired_motion:
                    self._execute_step(snapshot.desired_motion, snapshot.generation)
                    continue

                self._wake_event.wait(0.15)
                self._wake_event.clear()
                continue

            if snapshot.desired_motion in CONTINUOUS_MOTION_MODES:
                self._execute_step(snapshot.desired_motion, snapshot.generation)
                continue

            if snapshot.current_motion != snapshot.desired_motion:
                self._execute_step(snapshot.desired_motion, snapshot.generation)
                continue

            self._wake_event.wait(0.15)
            self._wake_event.clear()

    def _execute_step(self, motion_mode: str, generation: int) -> None:
        with self._lock:
            self._busy = True
            self._current_motion = motion_mode
            self._last_result = f"Running {motion_mode} (gen {generation})"
            self._last_error = None

        try:
            bridge_result = self._call_motion_step(motion_mode)
            with self._lock:
                if generation == self._generation:
                    self._last_result = f"{motion_mode} step complete: {bridge_result}"
                    if motion_mode in ONE_SHOT_MOTION_MODES:
                        self._current_motion = motion_mode
                else:
                    self._last_result = f"Ignored stale {motion_mode} response from gen {generation}"
        except Exception as error:
            logger.exception("Bridge motion step failed")
            with self._lock:
                if generation == self._generation:
                    self._last_error = str(error)
                    self._last_result = f"Bridge error: {error}"
        finally:
            with self._lock:
                self._busy = False

    def _call_motion_step(self, motion_mode: str) -> Any:
        try:
            result = Bridge.call("run_mode_step", motion_mode)
            with self._lock:
                self._bridge_function = "run_mode_step"
            return result
        except Exception as error:
            if "run_mode_step" not in str(error):
                raise

        legacy_motion = MODE_TO_LEGACY_MOTION[motion_mode]
        result = Bridge.call("run_motion", legacy_motion)
        with self._lock:
            self._bridge_function = "run_motion"
        return result


motion_controller = MotionController()

vision_status = {
    "opencv": "Idle: blue ball tracking ready",
    "mediapipe": "Idle: hand controller reserved",
    "blueBall": {
        "detected": False,
        "action": "idle",
        "motion": "stand",
        "x": None,
        "y": None,
        "radius": None,
        "offset": None,
        "maskArea": 0,
    },
}
vision_lock = threading.Lock()


def state_response() -> dict[str, Any]:
    with camera_lock:
        frame_available = last_frame is not None
        current_camera_status = camera_status

    with vision_lock:
        current_vision_status = {
            "opencv": vision_status["opencv"],
            "mediapipe": vision_status["mediapipe"],
            "blueBall": dict(vision_status["blueBall"]),
        }

    return {
        "ok": True,
        "controlModes": sorted(CONTROL_MODES),
        "motionModes": sorted(MOTION_MODES),
        "poseTable": POSE_TABLE,
        "motion": motion_controller.snapshot_dict(),
        "camera": {
            "available": frame_available,
            "status": current_camera_status,
            "snapshotEndpoint": "/api/camera-frame",
            "mjpegEndpoint": f"http://{get_host_placeholder()}:{MJPEG_PORT}/video-feed",
        },
        "vision": current_vision_status,
    }


def mode_response(request: Any = None) -> dict[str, Any]:
    request_data = parse_request_data(request)
    mode = str(request_data.get("mode", "")).strip().lower()
    if not mode:
        return {"ok": False, "error": "Missing mode", "state": motion_controller.snapshot_dict()}
    return motion_controller.set_mode(mode)


def read_motion_from_request(request: Any) -> str:
    request_data = parse_request_data(request)
    return str(request_data.get("motion", "")).strip().lower()


def motion_response(request: Any = None) -> dict[str, Any]:
    motion = read_motion_from_request(request)
    if not motion:
        return {"ok": False, "error": "Missing motion", "state": motion_controller.snapshot_dict()}
    return motion_controller.run_legacy_motion(motion)


def mode_path_response(mode: str) -> dict[str, Any]:
    return motion_controller.set_mode(mode)


def camera_frame_response() -> dict[str, Any]:
    snapshot = motion_controller.snapshot()
    with camera_lock:
        frame = last_vision_frame if snapshot.app_mode == "opencv" and last_vision_frame is not None else last_frame
        if frame is None:
            return {"ok": False, "available": False, "status": camera_status}
        encoded_frame = base64.b64encode(frame).decode("ascii")
        return {
            "ok": True,
            "available": True,
            "status": camera_status,
            "mimeType": "image/jpeg",
            "data": encoded_frame,
        }


def camera_capture_loop() -> None:
    global last_frame, last_vision_frame, last_cv_frame, camera_status

    while True:
        capture = cv2.VideoCapture(CAMERA_DEVICE)
        if not capture.isOpened():
            with camera_lock:
                camera_status = f"Camera unavailable on /dev/video{CAMERA_DEVICE}"
                last_frame = None
                last_vision_frame = None
                last_cv_frame = None
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
                    last_vision_frame = None
                    last_cv_frame = None
                break

            success, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if success:
                with camera_lock:
                    last_frame = jpeg.tobytes()
                    last_cv_frame = frame.copy()
                    camera_status = "Camera connected"

            time.sleep(CAMERA_FRAME_INTERVAL_SECONDS)

        capture.release()
        time.sleep(1)


def detect_blue_ball(frame: Any) -> dict[str, Any]:
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BLUE_HSV_LOWER, BLUE_HSV_UPPER)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask_area = int(cv2.countNonZero(mask))

    detection = {
        "detected": False,
        "x": None,
        "y": None,
        "radius": None,
        "offset": None,
        "maskArea": mask_area,
        "frameWidth": width,
        "frameHeight": height,
        "mask": mask,
    }
    if mask_area < BLUE_MIN_MASK_AREA:
        return detection

    blurred = cv2.GaussianBlur(mask, (9, 9), 2)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=40,
        param1=80,
        param2=14,
        minRadius=BLUE_MIN_RADIUS,
        maxRadius=0,
    )

    if circles is not None:
        circle = max(circles[0], key=lambda candidate: candidate[2])
        x, y, radius = int(round(circle[0])), int(round(circle[1])), int(round(circle[2]))
    else:
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return detection
        contour = max(contours, key=cv2.contourArea)
        contour_area = cv2.contourArea(contour)
        if contour_area < BLUE_MIN_MASK_AREA:
            return detection
        (x_float, y_float), radius_float = cv2.minEnclosingCircle(contour)
        x, y, radius = int(round(x_float)), int(round(y_float)), int(round(radius_float))

    if radius < BLUE_MIN_RADIUS:
        return detection

    offset = (x - (width / 2)) / (width / 2)
    detection.update(
        {
            "detected": True,
            "x": x,
            "y": y,
            "radius": radius,
            "offset": round(offset, 3),
        }
    )
    return detection


def choose_blue_ball_motion(detection: dict[str, Any]) -> tuple[str, str]:
    if not detection["detected"]:
        return "search", "turn_right"

    offset = float(detection["offset"])
    if offset < -BLUE_CENTER_DEADZONE_RATIO:
        return "correct_left", "turn_left"
    if offset > BLUE_CENTER_DEADZONE_RATIO:
        return "correct_right", "turn_right"
    return "centered_advance", "forward"


def encode_vision_frame(frame: Any, detection: dict[str, Any], action: str, motion: str) -> bytes | None:
    annotated = frame.copy()
    height, width = annotated.shape[:2]
    center_x = width // 2
    deadzone_px = int(width * BLUE_CENTER_DEADZONE_RATIO)
    left_limit = center_x - deadzone_px
    right_limit = center_x + deadzone_px

    cv2.line(annotated, (center_x, 0), (center_x, height), (255, 255, 255), 1)
    cv2.line(annotated, (left_limit, 0), (left_limit, height), (0, 255, 255), 1)
    cv2.line(annotated, (right_limit, 0), (right_limit, height), (0, 255, 255), 1)

    if detection["detected"]:
        center = (int(detection["x"]), int(detection["y"]))
        radius = int(detection["radius"])
        cv2.circle(annotated, center, radius, (255, 0, 0), 3)
        cv2.circle(annotated, center, 3, (0, 255, 255), -1)

    label = f"OpenCV blue ball: {action} -> {motion}"
    cv2.rectangle(annotated, (8, height - 42), (min(width - 8, 470), height - 8), (0, 0, 0), -1)
    cv2.putText(annotated, label, (18, height - 19), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)

    success, jpeg = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not success:
        return None
    return jpeg.tobytes()


def vision_worker_loop() -> None:
    global last_vision_frame

    while True:
        snapshot = motion_controller.snapshot()
        if snapshot.app_mode not in {"opencv", "mediapipe"}:
            with camera_lock:
                last_vision_frame = None
            time.sleep(0.2)
            continue

        with camera_lock:
            frame = last_cv_frame.copy() if last_cv_frame is not None else None

        if frame is None:
            motion_controller.set_autonomous_motion(snapshot.app_mode, "stand", f"{snapshot.app_mode}: waiting for camera frame")
            with vision_lock:
                if snapshot.app_mode == "opencv":
                    vision_status["opencv"] = "Active: waiting for camera frame"
                    vision_status["blueBall"] = {
                        "detected": False,
                        "action": "waiting_for_frame",
                        "motion": "stand",
                        "x": None,
                        "y": None,
                        "radius": None,
                        "offset": None,
                        "maskArea": 0,
                    }
                if snapshot.app_mode == "mediapipe":
                    vision_status["mediapipe"] = "Active: waiting for camera frame"
            time.sleep(VISION_FRAME_INTERVAL_SECONDS)
            continue

        if snapshot.app_mode == "opencv":
            detection = detect_blue_ball(frame)
            action, motion = choose_blue_ball_motion(detection)
            motion_controller.set_autonomous_motion("opencv", motion, f"OpenCV blue ball {action}: {motion}")
            encoded_frame = encode_vision_frame(frame, detection, action, motion)
            if encoded_frame is not None:
                with camera_lock:
                    last_vision_frame = encoded_frame

            with vision_lock:
                vision_status["opencv"] = (
                    f"Blue ball {action}: {motion}, offset {detection['offset']}"
                    if detection["detected"]
                    else "Blue ball not detected: searching right"
                )
                vision_status["blueBall"] = {
                    "detected": detection["detected"],
                    "action": action,
                    "motion": motion,
                    "x": detection["x"],
                    "y": detection["y"],
                    "radius": detection["radius"],
                    "offset": detection["offset"],
                    "maskArea": detection["maskArea"],
                }

        if snapshot.app_mode == "mediapipe":
            with vision_lock:
                vision_status["mediapipe"] = "Active: waiting for hand controller implementation"

        time.sleep(VISION_FRAME_INTERVAL_SECONDS)


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
            snapshot = motion_controller.snapshot()
            with camera_lock:
                frame = last_vision_frame if snapshot.app_mode == "opencv" and last_vision_frame is not None else last_frame
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
threading.Thread(target=vision_worker_loop, daemon=True).start()
threading.Thread(target=start_mjpeg_server, daemon=True).start()

web_ui = WebUI()
web_ui.expose_api("GET", "/api/state", state_response)
web_ui.expose_api("GET", "/api/mode", mode_response)
web_ui.expose_api("POST", "/api/mode", mode_response)
web_ui.expose_api("GET", "/api/mode/initial", lambda: mode_path_response("initial"))
web_ui.expose_api("GET", "/api/mode/stand", lambda: mode_path_response("stand"))
web_ui.expose_api("GET", "/api/mode/greeting", lambda: mode_path_response("greeting"))
web_ui.expose_api("GET", "/api/mode/forward", lambda: mode_path_response("forward"))
web_ui.expose_api("GET", "/api/mode/backward", lambda: mode_path_response("backward"))
web_ui.expose_api("GET", "/api/mode/turn-left", lambda: mode_path_response("turn_left"))
web_ui.expose_api("GET", "/api/mode/turn-right", lambda: mode_path_response("turn_right"))
web_ui.expose_api("GET", "/api/mode/manual", lambda: mode_path_response("manual"))
web_ui.expose_api("GET", "/api/mode/opencv", lambda: mode_path_response("opencv"))
web_ui.expose_api("GET", "/api/mode/mediapipe", lambda: mode_path_response("mediapipe"))
web_ui.expose_api("GET", "/api/motion", motion_response)
web_ui.expose_api("POST", "/api/motion", motion_response)
web_ui.expose_api("GET", "/api/camera-frame", camera_frame_response)

App.run()
