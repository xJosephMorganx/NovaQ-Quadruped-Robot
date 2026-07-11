const state = {
  poseTable: [],
  mjpegMode: false,
  cameraTimer: null,
  stateTimer: null,
  commandId: 0,
  latestAcceptedGeneration: 0,
  appMode: "manual",
  desiredMotion: "stand",
  currentMotion: "none",
  busy: false,
  pressedMovementKeys: new Map(),
  activeMovementMode: null,
};

const elements = {
  connectionStatus: document.querySelector("#connectionStatus"),
  appMode: document.querySelector("#appMode"),
  desiredMotion: document.querySelector("#desiredMotion"),
  currentMotion: document.querySelector("#currentMotion"),
  lastResult: document.querySelector("#lastResult"),
  cameraStatus: document.querySelector("#cameraStatus"),
  cameraImage: document.querySelector("#cameraImage"),
  cameraFallback: document.querySelector("#cameraFallback"),
  toggleStreamButton: document.querySelector("#toggleStreamButton"),
  focusModeButton: document.querySelector("#focusModeButton"),
  hudConnection: document.querySelector("#hudConnection"),
  hudMode: document.querySelector("#hudMode"),
  hudMotion: document.querySelector("#hudMotion"),
  hudResult: document.querySelector("#hudResult"),
  opencvStatus: document.querySelector("#opencvStatus"),
  blueBallStatus: document.querySelector("#blueBallStatus"),
  mediapipeStatus: document.querySelector("#mediapipeStatus"),
  poseRows: document.querySelector("#poseRows"),
  modeButtons: document.querySelectorAll("[data-mode]"),
};

const movementKeyToMode = {
  w: "forward",
  s: "backward",
  a: "turn_left",
  d: "turn_right",
};

const poseKeyToMode = {
  ArrowUp: "stand",
  ArrowDown: "initial",
  ArrowRight: "greeting",
  ArrowLeft: "tail_wag",
};

const reservedKeyBindings = {
  PageDown: "opencv",
  F4: "hand_gesture",
  " ": "manual",
};

const modeLabels = {
  hand_gesture: "hand gesture",
};

function apiUrl(path) {
  return new URL(path, window.location.origin);
}

async function fetchJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
  }

  if (!response.ok || body?.ok === false) {
    const detail = body?.error ? `: ${body.error}` : "";
    const error = new Error(`HTTP ${response.status}${detail}`);
    error.status = response.status;
    throw error;
  }

  return body;
}

function setConnectionStatus(text, className) {
  elements.connectionStatus.textContent = text;
  elements.connectionStatus.className = `status-pill ${className}`;
  elements.hudConnection.textContent = text;
}

function renderPoseTable() {
  elements.poseRows.innerHTML = "";
  state.poseTable.forEach((servo) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${servo.name}</td>
      <td>${servo.channel}</td>
      <td>${servo.initial}</td>
      <td>${servo.stand}</td>
      <td>${servo.greeting || ""}</td>
    `;
    elements.poseRows.append(row);
  });
}

function renderMotion() {
  const appModeLabel = modeLabels[state.appMode] || state.appMode;
  elements.appMode.textContent = `Mode: ${appModeLabel}`;
  elements.desiredMotion.textContent = `Desired: ${state.desiredMotion}`;
  elements.currentMotion.textContent = `Current: ${state.currentMotion}`;
  elements.hudMode.textContent = `Mode: ${appModeLabel}`;
  elements.hudMotion.textContent = `Motion: ${state.desiredMotion}`;

  elements.modeButtons.forEach((button) => {
    const buttonMode = button.dataset.mode;
    button.classList.toggle("is-active", buttonMode === state.appMode || buttonMode === state.desiredMotion);
  });
}

function setResult(text) {
  elements.lastResult.textContent = text;
  elements.hudResult.textContent = text;
}

function applyMotionState(motionState) {
  if (!motionState) {
    return;
  }

  state.appMode = motionState.appMode || state.appMode;
  state.desiredMotion = motionState.desiredMotion || state.desiredMotion;
  state.currentMotion = motionState.currentMotion || state.currentMotion;
  state.busy = Boolean(motionState.busy);
  state.latestAcceptedGeneration = motionState.generation || state.latestAcceptedGeneration;
  setResult(motionState.lastResult || "Ready");
  renderMotion();
}

async function loadState() {
  try {
    const data = await fetchJson("/api/state");
    state.poseTable = data.poseTable || [];
    elements.cameraStatus.textContent = data.camera?.status || "Camera status unknown";
    elements.opencvStatus.textContent = data.vision?.opencv || "Reserved";
    elements.blueBallStatus.textContent = formatBlueBallStatus(data.vision?.blueBall);
    elements.mediapipeStatus.textContent = formatHandStatus(data.vision?.hand, data.vision?.handGesture || data.vision?.mediapipe);
    applyMotionState(data.motion);
    renderPoseTable();
    setConnectionStatus("Connected", "status-success");
  } catch (error) {
    setResult(`Connection failed: ${error.message}`);
    setConnectionStatus("Disconnected", "status-danger");
  }
}

function formatBlueBallStatus(blueBall) {
  if (!blueBall) {
    return "Idle";
  }
  if (!blueBall.detected) {
    const reason = blueBall.rejectReason ? ` (${blueBall.rejectReason})` : "";
    return `${blueBall.action || "search"} -> ${blueBall.motion || "turn_right"}${reason}`;
  }
  const offset = Number(blueBall.offset || 0).toFixed(2);
  const circularity = Number(blueBall.circularity || 0).toFixed(2);
  const fill = Number(blueBall.fillRatio || 0).toFixed(2);
  return `x ${blueBall.x}, r ${blueBall.radius}, off ${offset}, c ${circularity}, f ${fill} -> ${blueBall.motion}`;
}

function formatHandStatus(hand, fallback = "Reserved") {
  if (!hand) {
    return fallback;
  }
  if (!hand.detected) {
    return `${hand.gesture || "none"} -> ${hand.motion || "hold"}`;
  }
  const confidence = Number(hand.confidence || 0).toFixed(2);
  const box = Array.isArray(hand.boundingBox) ? `, box ${hand.boundingBox.join(",")}` : "";
  const fingers = Number.isFinite(Number(hand.fingerCount)) ? `, fingers ${hand.fingerCount}` : "";
  return `${hand.gesture} -> ${hand.motion}${fingers}, c ${confidence}${box}`;
}

async function setMode(mode, source = "ui") {
  const commandId = ++state.commandId;
  setConnectionStatus("Sending", "status-warning");
  setResult(`set_mode("${mode}") from ${source}`);

  try {
    const modePath = mode.replaceAll("_", "-");
    const data = await fetchJson(`/api/mode/${encodeURIComponent(modePath)}?commandId=${commandId}`);

    if (commandId !== state.commandId && data.generation < state.latestAcceptedGeneration) {
      return;
    }

    applyMotionState(data.state);
    setConnectionStatus("Connected", "status-success");
  } catch (error) {
    setResult(error.message);
    setConnectionStatus(error.status ? "Request Error" : "Bridge Error", "status-danger");
  }
}

async function refreshCameraFrame() {
  if (state.mjpegMode) {
    return;
  }

  try {
    const data = await fetchJson("/api/camera-frame");
    elements.cameraStatus.textContent = data.status || "Camera status unknown";
    if (!data.ok || !data.available) {
      elements.cameraFallback.classList.remove("hidden");
      return;
    }

    elements.cameraImage.src = `data:${data.mimeType};base64,${data.data}`;
    elements.cameraFallback.classList.add("hidden");
  } catch (error) {
    elements.cameraStatus.textContent = `Camera refresh failed: ${error.message}`;
    elements.cameraFallback.classList.remove("hidden");
  }
}

function startCameraRefresh() {
  if (state.cameraTimer) {
    window.clearInterval(state.cameraTimer);
  }
  state.cameraTimer = window.setInterval(refreshCameraFrame, 250);
  refreshCameraFrame();
}

function startStateRefresh() {
  if (state.stateTimer) {
    window.clearInterval(state.stateTimer);
  }
  state.stateTimer = window.setInterval(loadState, 750);
}

function toggleStreamMode() {
  state.mjpegMode = !state.mjpegMode;
  if (state.mjpegMode) {
    elements.cameraImage.src = `http://${window.location.hostname}:7001/video-feed`;
    elements.cameraFallback.classList.add("hidden");
    elements.cameraStatus.textContent = "MJPEG stream mode";
    elements.toggleStreamButton.textContent = "Snapshots";
    return;
  }

  elements.toggleStreamButton.textContent = "MJPEG";
  startCameraRefresh();
}

function toggleFocusMode() {
  document.body.classList.toggle("focus-mode");
  const focusEnabled = document.body.classList.contains("focus-mode");
  elements.focusModeButton.textContent = focusEnabled ? "Dashboard" : "Focus View";
}

function shouldIgnoreKeyboardEvent(event) {
  const tagName = event.target?.tagName?.toLowerCase();
  return tagName === "input" || tagName === "select" || tagName === "textarea" || event.repeat;
}

function chooseActiveMovementMode() {
  const pressedEntries = [...state.pressedMovementKeys.entries()];
  if (!pressedEntries.length) {
    return null;
  }
  pressedEntries.sort((left, right) => right[1] - left[1]);
  return movementKeyToMode[pressedEntries[0][0]];
}

function updateMovementMode(source) {
  const nextMovementMode = chooseActiveMovementMode();
  if (nextMovementMode === state.activeMovementMode) {
    return;
  }

  state.activeMovementMode = nextMovementMode;
  setMode(nextMovementMode || "stand", source);
}

elements.modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.mode, "button");
  });
});

window.addEventListener("keydown", (event) => {
  if (shouldIgnoreKeyboardEvent(event)) {
    return;
  }

  const poseMode = poseKeyToMode[event.key];
  if (poseMode) {
    event.preventDefault();
    state.pressedMovementKeys.clear();
    state.activeMovementMode = null;
    setMode(poseMode, "keyboard");
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    toggleFocusMode();
    return;
  }

  const reservedMode = reservedKeyBindings[event.key] || reservedKeyBindings[event.key.toLowerCase()];
  if (reservedMode) {
    event.preventDefault();
    setMode(reservedMode, "keyboard");
    return;
  }

  const key = event.key.toLowerCase();
  if (movementKeyToMode[key]) {
    event.preventDefault();
    state.pressedMovementKeys.set(key, performance.now());
    updateMovementMode("keyboard");
  }
});

window.addEventListener("keyup", (event) => {
  if (shouldIgnoreKeyboardEvent(event)) {
    return;
  }

  const key = event.key.toLowerCase();
  if (!movementKeyToMode[key]) {
    return;
  }

  event.preventDefault();
  state.pressedMovementKeys.delete(key);
  updateMovementMode("keyboard");
});

window.addEventListener("blur", () => {
  if (!state.pressedMovementKeys.size) {
    return;
  }
  state.pressedMovementKeys.clear();
  state.activeMovementMode = null;
  setMode("stand", "window-blur");
});

elements.toggleStreamButton.addEventListener("click", toggleStreamMode);
elements.focusModeButton.addEventListener("click", toggleFocusMode);

loadState();
startStateRefresh();
startCameraRefresh();
