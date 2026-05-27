const state = {
  poseTable: [],
  lastMotion: "none",
  mjpegMode: false,
  cameraTimer: null,
  motionInFlight: false,
  forwardGaitActive: false,
  forwardGaitStopping: false,
  backwardGaitActive: false,
  backwardGaitStopping: false,
};

const elements = {
  connectionStatus: document.querySelector("#connectionStatus"),
  lastMotion: document.querySelector("#lastMotion"),
  lastResult: document.querySelector("#lastResult"),
  cameraStatus: document.querySelector("#cameraStatus"),
  cameraImage: document.querySelector("#cameraImage"),
  cameraFallback: document.querySelector("#cameraFallback"),
  toggleStreamButton: document.querySelector("#toggleStreamButton"),
  focusModeButton: document.querySelector("#focusModeButton"),
  hudConnection: document.querySelector("#hudConnection"),
  hudMotion: document.querySelector("#hudMotion"),
  hudResult: document.querySelector("#hudResult"),
  poseRows: document.querySelector("#poseRows"),
  motionButtons: document.querySelectorAll("[data-motion]"),
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

  if (!response.ok) {
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

function setButtonsDisabled(disabled) {
  elements.motionButtons.forEach((button) => {
    button.disabled = disabled;
  });
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
  elements.lastMotion.textContent = `Motion: ${state.lastMotion}`;
  elements.hudMotion.textContent = `Motion: ${state.lastMotion}`;
}

function setResult(text) {
  elements.lastResult.textContent = text;
  elements.hudResult.textContent = text;
}

const motionKeyBindings = {
  ArrowUp: "stand",
  ArrowDown: "initial",
  ArrowRight: "greeting",
};

const reservedKeyBindings = {
  a: "Left turn",
  d: "Right turn",
  PageDown: "OpenCV blue ball detection",
  F4: "MediaPipe hand controller",
  " ": "Manual mode",
};

async function loadState() {
  try {
    const data = await fetchJson("/api/state");
    state.poseTable = data.poseTable || [];
    state.lastMotion = data.lastMotion || "none";
    setResult(data.lastResult || "Ready");
    elements.cameraStatus.textContent = data.camera?.status || "Camera status unknown";
    renderPoseTable();
    renderMotion();
    setConnectionStatus("Connected", "status-success");
  } catch (error) {
    setResult(`Connection failed: ${error.message}`);
    setConnectionStatus("Disconnected", "status-danger");
  }
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForMotionSlot() {
  while (state.motionInFlight) {
    await wait(30);
  }
}

async function runMotion(motion) {
  if (state.motionInFlight) {
    await waitForMotionSlot();
  }

  state.motionInFlight = true;
  setButtonsDisabled(true);
  setResult(`Running ${motion}...`);
  setConnectionStatus("Sending", "status-warning");

  try {
    const data = await fetchJson(`/api/motion/${encodeURIComponent(motion)}`);

    if (!data.ok) {
      const error = new Error(data.error || "Motion command failed");
      error.status = 400;
      throw error;
    }

    state.lastMotion = data.motion;
    setResult(data.lastResult || `Motion ${motion} executed`);
    renderMotion();
    setConnectionStatus("Connected", "status-success");
  } catch (error) {
    setResult(error.message);
    setConnectionStatus(error.status ? "Request Error" : "Bridge Error", "status-danger");
  } finally {
    state.motionInFlight = false;
    setButtonsDisabled(false);
  }
}

async function startForwardGait() {
  if (state.forwardGaitActive) {
    return;
  }

  state.forwardGaitActive = true;
  state.forwardGaitStopping = false;
  setResult("Forward gait active");

  while (state.forwardGaitActive) {
    await runMotion("forward-step");
  }

  if (state.forwardGaitStopping) {
    state.forwardGaitStopping = false;
    await runMotion("stand");
  }
}

function stopForwardGait() {
  if (!state.forwardGaitActive) {
    return;
  }

  state.forwardGaitActive = false;
  state.forwardGaitStopping = true;
  setResult("Stopping forward gait...");
}

async function startBackwardGait() {
  if (state.backwardGaitActive) {
    return;
  }

  state.backwardGaitActive = true;
  state.backwardGaitStopping = false;
  setResult("Backward gait active");

  while (state.backwardGaitActive) {
    await runMotion("backward-step");
  }

  if (state.backwardGaitStopping) {
    state.backwardGaitStopping = false;
    await runMotion("stand");
  }
}

function stopBackwardGait() {
  if (!state.backwardGaitActive) {
    return;
  }

  state.backwardGaitActive = false;
  state.backwardGaitStopping = true;
  setResult("Stopping backward gait...");
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

elements.motionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    runMotion(button.dataset.motion);
  });
});

window.addEventListener("keydown", (event) => {
  if (shouldIgnoreKeyboardEvent(event)) {
    return;
  }

  const motion = motionKeyBindings[event.key];
  if (motion) {
    event.preventDefault();
    runMotion(motion);
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    toggleFocusMode();
    return;
  }

  if (event.key.toLowerCase() === "w") {
    event.preventDefault();
    startForwardGait();
    return;
  }

  if (event.key.toLowerCase() === "s") {
    event.preventDefault();
    startBackwardGait();
    return;
  }

  const reservedAction = reservedKeyBindings[event.key] || reservedKeyBindings[event.key.toLowerCase()];
  if (reservedAction) {
    event.preventDefault();
    setResult(`${reservedAction} reserved for a future motion`);
  }
});

window.addEventListener("keyup", (event) => {
  if (shouldIgnoreKeyboardEvent(event)) {
    return;
  }

  if (event.key.toLowerCase() === "w") {
    event.preventDefault();
    stopForwardGait();
  }

  if (event.key.toLowerCase() === "s") {
    event.preventDefault();
    stopBackwardGait();
  }
});

elements.toggleStreamButton.addEventListener("click", toggleStreamMode);
elements.focusModeButton.addEventListener("click", toggleFocusMode);

loadState();
startCameraRefresh();
