from __future__ import annotations

import base64
import json
import logging
import math
import os
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

try:
    from arduino.app_bricks.video_objectdetection import VideoObjectDetection
    from arduino.app_peripherals.camera import BaseCamera
    from arduino.app_utils.image import draw_bounding_boxes, get_image_bytes
except Exception as import_error:  # pragma: no cover - depends on App Lab brick runtime
    BaseCamera = None
    VideoObjectDetection = None
    draw_bounding_boxes = None
    get_image_bytes = None
    VIDEO_OBJECT_DETECTION_IMPORT_ERROR = import_error
else:
    VIDEO_OBJECT_DETECTION_IMPORT_ERROR = None


APP_DIR = Path(__file__).resolve().parent.parent
CAMERA_DEVICE = 0
CAMERA_FRAME_INTERVAL_SECONDS = 0.04
MJPEG_PORT = 7001
VISION_FRAME_INTERVAL_SECONDS = 0.06
OPENCV_STEP_COOLDOWN_SECONDS = 3.0
OPENCV_SEARCH_BURST_STEPS = 3
ENABLE_GESTURE_BRICK = os.environ.get("UNO_Q_ENABLE_GESTURE_BRICK", "1").strip().lower() not in {"0", "false", "no"}
HAND_ACTION_COOLDOWN_SECONDS = 1.0
HAND_STABLE_GESTURES_REQUIRED = 3
BLUE_HSV_LOWER = (98, 105, 55)
BLUE_HSV_UPPER = (135, 255, 255)
BLUE_MIN_MASK_AREA = 380
BLUE_MIN_RADIUS = 12
BLUE_MAX_RADIUS_RATIO = 0.32
BLUE_MIN_CIRCULARITY = 0.72
BLUE_MIN_FILL_RATIO = 0.50
BLUE_MAX_FILL_RATIO = 1.18
BLUE_CENTER_DEADZONE_RATIO = 0.24
BLUE_STABLE_DETECTIONS_REQUIRED = 2

HAND_GESTURE_MODE = "hand_gesture"
CONTROL_MODES = {"manual", "opencv", HAND_GESTURE_MODE}
CONTROL_MODE_ALIASES = {
    "hand": HAND_GESTURE_MODE,
    "hand_gesture": HAND_GESTURE_MODE,
    "handgesture": HAND_GESTURE_MODE,
    "mediapipe": HAND_GESTURE_MODE,
}
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
GESTURE_LABEL_TO_MOTION = {
    "open_palm": "stand",
    "open_hand": "stand",
    "palm": "stand",
    "five": "stand",
    "stop": "stand",
    "stand": "stand",
    "fist": "initial",
    "closed_fist": "initial",
    "closed_hand": "initial",
    "initial": "initial",
    "one": "forward",
    "one_finger": "forward",
    "index": "forward",
    "point_up": "forward",
    "up": "forward",
    "forward": "forward",
    "thumbs_up": "forward",
    "two": "backward",
    "two_fingers": "backward",
    "peace": "backward",
    "v_sign": "backward",
    "down": "backward",
    "backward": "backward",
    "left": "turn_left",
    "point_left": "turn_left",
    "turn_left": "turn_left",
    "right": "turn_right",
    "point_right": "turn_right",
    "turn_right": "turn_right",
    "three": "greeting",
    "three_fingers": "greeting",
    "wave": "greeting",
    "greeting": "greeting",
}


def normalize_mode_name(mode: str) -> str:
    normalized = mode.strip().lower().replace("-", "_").replace(" ", "_")
    return CONTROL_MODE_ALIASES.get(normalized, normalized)

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


if BaseCamera is not None:
    class DirectOpenCVCamera(BaseCamera):
        def __init__(self, device: int = CAMERA_DEVICE, resolution: tuple[int, int] = (640, 480), fps: int = 10) -> None:
            super().__init__(resolution=resolution, fps=fps, auto_reconnect=True)
            self.device = device
            self.name = f"/dev/video{device}"
            self._capture: Any = None

        def _open_camera(self) -> None:
            self._close_camera()
            capture = cv2.VideoCapture(self.device)
            if not capture.isOpened():
                raise RuntimeError(f"Failed to open /dev/video{self.device}")

            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            capture.set(cv2.CAP_PROP_FPS, self.fps)

            success, frame = capture.read()
            if not success or frame is None:
                capture.release()
                raise RuntimeError(f"Read test failed for /dev/video{self.device}")

            self._capture = capture
            self._publish_frame(frame)
            self._set_status("connected", {"camera_name": self.name, "camera_path": self.name})

        def _close_camera(self) -> None:
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            self._set_status("disconnected", {"camera_name": self.name, "camera_path": self.name})

        def _read_frame(self) -> Any | None:
            if self._capture is None:
                self._open_camera()
            if self._capture is None:
                return None

            success, frame = self._capture.read()
            if not success or frame is None:
                self._close_camera()
                return None

            self._publish_frame(frame)
            return frame

        def _publish_frame(self, frame: Any) -> None:
            global camera_status, last_cv_frame, last_frame
            success, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if success:
                with camera_lock:
                    last_frame = jpeg.tobytes()
                    last_cv_frame = frame.copy()
                    camera_status = "Camera connected via gesture brick"
else:
    DirectOpenCVCamera = None


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
    executed_generation: int
    busy: bool
    last_result: str
    last_error: str | None
    bridge_function: str


class MotionController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._generation = 0
        self._executed_generation = 0
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
        normalized_mode = normalize_mode_name(mode)
        if normalized_mode in CONTROL_MODES:
            with self._lock:
                self._generation += 1
                self._app_mode = normalized_mode
                if normalized_mode == "manual":
                    self._desired_motion = "stand"
                else:
                    self._desired_motion = self._current_motion
                    self._executed_generation = self._generation
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

    def set_autonomous_motion(self, owner_mode: str, motion_mode: str, reason: str, force: bool = False) -> None:
        normalized_owner = normalize_mode_name(owner_mode)
        normalized_motion = motion_mode.strip().lower().replace("-", "_")
        if normalized_owner not in {"opencv", HAND_GESTURE_MODE} or normalized_motion not in MOTION_MODES:
            return

        with self._lock:
            if self._app_mode != normalized_owner:
                return
            if self._desired_motion == normalized_motion and not force:
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
                executed_generation=self._executed_generation,
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
            "executedGeneration": snapshot.executed_generation,
            "busy": snapshot.busy,
            "lastResult": snapshot.last_result,
            "lastError": snapshot.last_error,
            "bridgeFunction": snapshot.bridge_function,
        }

    def _run(self) -> None:
        while True:
            snapshot = self.snapshot()

            if snapshot.app_mode != "manual":
                if snapshot.generation != snapshot.executed_generation or snapshot.current_motion != snapshot.desired_motion:
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
                    self._executed_generation = generation
                    if motion_mode in ONE_SHOT_MOTION_MODES:
                        self._current_motion = motion_mode
                else:
                    self._last_result = f"Ignored stale {motion_mode} response from gen {generation}"
                    self._executed_generation = max(self._executed_generation, generation)
        except Exception as error:
            logger.exception("Bridge motion step failed")
            with self._lock:
                if generation == self._generation:
                    self._last_error = str(error)
                    self._last_result = f"Bridge error: {error}"
        finally:
            with self._lock:
                self._executed_generation = max(self._executed_generation, generation)
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
    "handGesture": "Idle: hand gesture controller ready",
    "mediapipe": "Idle: hand gesture controller ready",
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
    "hand": {
        "detected": False,
        "gesture": "none",
        "motion": "hold",
        "confidence": 0,
    },
}
vision_lock = threading.Lock()
opencv_state = {
    "last_motion_at": 0.0,
    "last_detection_key": None,
    "stable_detection_count": 0,
    "search_burst_remaining": 0,
}
opencv_lock = threading.Lock()
hand_state = {
    "last_gesture": None,
    "stable_gesture_count": 0,
    "last_motion_at": 0.0,
}
hand_lock = threading.Lock()


def state_response() -> dict[str, Any]:
    with camera_lock:
        frame_available = last_frame is not None
        current_camera_status = camera_status

    with vision_lock:
        current_vision_status = {
            "opencv": vision_status["opencv"],
            "handGesture": vision_status["handGesture"],
            "mediapipe": vision_status["handGesture"],
            "blueBall": dict(vision_status["blueBall"]),
            "hand": dict(vision_status["hand"]),
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
        frame = (
            last_vision_frame
            if snapshot.app_mode in {"opencv", HAND_GESTURE_MODE} and last_vision_frame is not None
            else last_frame
        )
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
        "circleArea": 0,
        "circularity": 0,
        "fillRatio": 0,
        "rejectReason": "no_blue_mask",
        "frameWidth": width,
        "frameHeight": height,
        "mask": mask,
    }
    if mask_area < BLUE_MIN_MASK_AREA:
        detection["rejectReason"] = "mask_too_small"
        return detection

    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    max_radius = int(min(width, height) * BLUE_MAX_RADIUS_RATIO)

    for contour in contours:
        contour_area = cv2.contourArea(contour)
        if contour_area < BLUE_MIN_MASK_AREA:
            continue

        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue

        (x_float, y_float), radius_float = cv2.minEnclosingCircle(contour)
        radius = int(round(radius_float))
        if radius < BLUE_MIN_RADIUS:
            continue
        if radius > max_radius:
            continue

        circle_area = math.pi * radius_float * radius_float
        if circle_area <= 0:
            continue

        circularity = (4 * math.pi * contour_area) / (perimeter * perimeter)
        fill_ratio = contour_area / circle_area
        if circularity < BLUE_MIN_CIRCULARITY:
            continue
        if fill_ratio < BLUE_MIN_FILL_RATIO or fill_ratio > BLUE_MAX_FILL_RATIO:
            continue

        x, y = int(round(x_float)), int(round(y_float))
        candidates.append(
            {
                "x": x,
                "y": y,
                "radius": radius,
                "contourArea": int(contour_area),
                "circleArea": int(circle_area),
                "circularity": round(circularity, 3),
                "fillRatio": round(fill_ratio, 3),
            }
        )

    if not candidates:
        detection["rejectReason"] = "no_circular_candidate"
        return detection

    candidate = max(candidates, key=lambda item: item["contourArea"])
    x = candidate["x"]
    y = candidate["y"]
    radius = candidate["radius"]
    offset = (x - (width / 2)) / (width / 2)
    detection.update(
        {
            "detected": True,
            "x": x,
            "y": y,
            "radius": radius,
            "offset": round(offset, 3),
            "circleArea": candidate["circleArea"],
            "circularity": candidate["circularity"],
            "fillRatio": candidate["fillRatio"],
            "rejectReason": None,
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


def build_detection_key(detection: dict[str, Any], action: str) -> str:
    if not detection["detected"]:
        return "missing"
    x_bucket = int(int(detection["x"]) / 32)
    y_bucket = int(int(detection["y"]) / 32)
    radius_bucket = int(int(detection["radius"]) / 12)
    return f"{action}:{x_bucket}:{y_bucket}:{radius_bucket}"


def is_detection_stable(detection: dict[str, Any], action: str) -> bool:
    if not detection["detected"]:
        with opencv_lock:
            opencv_state["last_detection_key"] = None
            opencv_state["stable_detection_count"] = 0
        return False

    detection_key = build_detection_key(detection, action)
    with opencv_lock:
        if opencv_state["last_detection_key"] == detection_key:
            opencv_state["stable_detection_count"] += 1
        else:
            opencv_state["last_detection_key"] = detection_key
            opencv_state["stable_detection_count"] = 1
        return opencv_state["stable_detection_count"] >= BLUE_STABLE_DETECTIONS_REQUIRED


def reset_search_burst() -> None:
    with opencv_lock:
        opencv_state["search_burst_remaining"] = 0


def maybe_issue_opencv_motion(action: str, motion: str, detection: dict[str, Any]) -> tuple[str, str]:
    now = time.monotonic()
    with opencv_lock:
        elapsed = now - float(opencv_state["last_motion_at"])
        if elapsed < OPENCV_STEP_COOLDOWN_SECONDS:
            return "settling", "hold"

    if action == "search":
        with opencv_lock:
            if opencv_state["search_burst_remaining"] <= 0:
                opencv_state["search_burst_remaining"] = OPENCV_SEARCH_BURST_STEPS
            opencv_state["search_burst_remaining"] -= 1
            burst_remaining = opencv_state["search_burst_remaining"]

        motion_controller.set_autonomous_motion("opencv", motion, f"OpenCV search burst: {motion}", force=True)
        with opencv_lock:
            opencv_state["last_motion_at"] = now
        if burst_remaining > 0:
            return f"search_burst_{burst_remaining + 1}", motion
        return "search_pause", motion

    reset_search_burst()

    if detection["detected"] and not is_detection_stable(detection, action):
        return "confirming", "hold"

    motion_controller.set_autonomous_motion("opencv", motion, f"OpenCV blue ball {action}: {motion}", force=True)
    with opencv_lock:
        opencv_state["last_motion_at"] = now
    return action, motion


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


def normalize_gesture_label(label: str) -> str:
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def best_gesture_detection(detections: dict[str, Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for label, entries in detections.items():
        normalized_label = normalize_gesture_label(str(label))
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            entries = [{"confidence": entries}]

        for entry in entries:
            if not isinstance(entry, dict):
                entry = {"confidence": entry}
            confidence = float(entry.get("confidence", 0) or 0)
            candidate = {
                "label": str(label),
                "normalizedLabel": normalized_label,
                "confidence": confidence,
                "motion": GESTURE_LABEL_TO_MOTION.get(normalized_label, "hold"),
                "boundingBox": entry.get("bounding_box_xyxy"),
            }
            if best is None or candidate["confidence"] > best["confidence"]:
                best = candidate
    return best


def gesture_detection_frame(frame: bytes | None, detections: dict[str, Any]) -> bytes | None:
    if frame is None:
        return None
    if draw_bounding_boxes is None or get_image_bytes is None:
        return frame
    try:
        return get_image_bytes(draw_bounding_boxes(frame, detections))
    except Exception:
        logger.exception("Failed to annotate gesture detection frame")
        return frame


def is_gesture_stable(label: str) -> bool:
    with hand_lock:
        if hand_state["last_gesture"] == label:
            hand_state["stable_gesture_count"] += 1
        else:
            hand_state["last_gesture"] = label
            hand_state["stable_gesture_count"] = 1
        return hand_state["stable_gesture_count"] >= HAND_STABLE_GESTURES_REQUIRED


def maybe_issue_gesture_brick_motion(gesture: dict[str, Any]) -> tuple[str, str]:
    label = gesture["normalizedLabel"]
    motion = gesture["motion"]
    if motion == "hold":
        return label, "hold"

    if not is_gesture_stable(label):
        return "confirming", "hold"

    now = time.monotonic()
    with hand_lock:
        elapsed = now - float(hand_state["last_motion_at"])
        if elapsed < HAND_ACTION_COOLDOWN_SECONDS:
            return "cooldown", "hold"
        hand_state["last_motion_at"] = now

    motion_controller.set_autonomous_motion(HAND_GESTURE_MODE, motion, f"Gesture brick {label}: {motion}", force=True)
    return label, motion


def on_gesture_detections(detections: dict[str, Any], frame: bytes | None = None) -> None:
    global camera_status, last_vision_frame

    encoded_frame = gesture_detection_frame(frame, detections)
    if encoded_frame is not None:
        with camera_lock:
            last_vision_frame = encoded_frame
            if motion_controller.snapshot().app_mode == HAND_GESTURE_MODE:
                camera_status = "Gesture detection brick preview active"

    best_detection = best_gesture_detection(detections)
    if best_detection is None:
        with vision_lock:
            vision_status["handGesture"] = "Gesture brick active: no hand gesture"
            vision_status["mediapipe"] = vision_status["handGesture"]
            vision_status["hand"] = {
                "detected": False,
                "gesture": "none",
                "motion": "hold",
                "confidence": 0,
            }
        return

    snapshot = motion_controller.snapshot()
    gesture_label = best_detection["normalizedLabel"]
    motion = best_detection["motion"]
    action = gesture_label
    if snapshot.app_mode != HAND_GESTURE_MODE:
        motion = "hold"
    else:
        action, motion = maybe_issue_gesture_brick_motion(best_detection)

    with vision_lock:
        vision_status["handGesture"] = f"Gesture brick {action}: {motion}"
        vision_status["mediapipe"] = vision_status["handGesture"]
        vision_status["hand"] = {
            "detected": True,
            "gesture": gesture_label,
            "motion": motion,
            "confidence": round(float(best_detection["confidence"]), 3),
            "boundingBox": best_detection["boundingBox"],
        }


def setup_gesture_detector() -> Any:
    if not ENABLE_GESTURE_BRICK:
        with vision_lock:
            vision_status["handGesture"] = "Gesture brick disabled"
            vision_status["mediapipe"] = vision_status["handGesture"]
        return None

    if VideoObjectDetection is None or DirectOpenCVCamera is None:
        error = f"Gesture brick unavailable: {VIDEO_OBJECT_DETECTION_IMPORT_ERROR}"
        logger.warning(error)
        with vision_lock:
            vision_status["handGesture"] = error
            vision_status["mediapipe"] = error
        return None

    try:
        camera = DirectOpenCVCamera(device=CAMERA_DEVICE, resolution=(640, 480), fps=10)
        detector = VideoObjectDetection(camera=camera, confidence=0.45, debounce_sec=0.25, camera_preview=True)
        detector.on_detect_all(on_gesture_detections)
        with vision_lock:
            vision_status["handGesture"] = "Gesture brick ready: waiting for hand"
            vision_status["mediapipe"] = vision_status["handGesture"]
        return detector
    except Exception as error:
        logger.exception("Failed to initialize gesture detection brick")
        with vision_lock:
            vision_status["handGesture"] = f"Gesture brick init failed: {error}"
            vision_status["mediapipe"] = vision_status["handGesture"]
        return None


def vision_worker_loop() -> None:
    global last_vision_frame

    while True:
        snapshot = motion_controller.snapshot()
        if snapshot.app_mode not in {"opencv", HAND_GESTURE_MODE}:
            with camera_lock:
                last_vision_frame = None
            time.sleep(0.2)
            continue

        if snapshot.app_mode == HAND_GESTURE_MODE:
            with vision_lock:
                if ENABLE_GESTURE_BRICK:
                    if vision_status["handGesture"].startswith("Idle"):
                        vision_status["handGesture"] = "Gesture brick active: waiting for hand"
                else:
                    vision_status["handGesture"] = "Gesture brick disabled"
                    vision_status["mediapipe"] = vision_status["handGesture"]
            time.sleep(0.2)
            continue

        with camera_lock:
            frame = last_cv_frame.copy() if last_cv_frame is not None else None

        if frame is None:
            with vision_lock:
                if snapshot.app_mode == "opencv":
                    vision_status["opencv"] = "Active: waiting for camera frame"
                    vision_status["blueBall"] = {
                        "detected": False,
                        "action": "waiting_for_frame",
                        "motion": "hold",
                        "x": None,
                        "y": None,
                        "radius": None,
                        "offset": None,
                        "maskArea": 0,
                    }
            time.sleep(VISION_FRAME_INTERVAL_SECONDS)
            continue

        if snapshot.app_mode == "opencv":
            detection = detect_blue_ball(frame)
            planned_action, planned_motion = choose_blue_ball_motion(detection)
            action, motion = maybe_issue_opencv_motion(planned_action, planned_motion, detection)
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
                    "circularity": detection["circularity"],
                    "fillRatio": detection["fillRatio"],
                    "rejectReason": detection["rejectReason"],
                }

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
                frame = (
                    last_vision_frame
                    if snapshot.app_mode in {"opencv", HAND_GESTURE_MODE} and last_vision_frame is not None
                    else last_frame
                )
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


gesture_detector = setup_gesture_detector()
if not ENABLE_GESTURE_BRICK or gesture_detector is None:
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
web_ui.expose_api("GET", "/api/mode/hand-gesture", lambda: mode_path_response(HAND_GESTURE_MODE))
web_ui.expose_api("GET", "/api/mode/mediapipe", lambda: mode_path_response(HAND_GESTURE_MODE))
web_ui.expose_api("GET", "/api/motion", motion_response)
web_ui.expose_api("POST", "/api/motion", motion_response)
web_ui.expose_api("GET", "/api/camera-frame", camera_frame_response)

App.run()
