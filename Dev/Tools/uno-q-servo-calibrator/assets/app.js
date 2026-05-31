const SAFE_PULSE_MIN = 50;
const SAFE_PULSE_MAX = 600;

const state = {
  servos: [],
  currentChannel: 0,
  mjpegMode: false,
  cameraTimer: null,
};

const elements = {
  channelSelect: document.querySelector("#channelSelect"),
  servoName: document.querySelector("#servoName"),
  rangeBadge: document.querySelector("#rangeBadge"),
  pulseInput: document.querySelector("#pulseInput"),
  sendButton: document.querySelector("#sendButton"),
  saveButton: document.querySelector("#saveButton"),
  exportButton: document.querySelector("#exportButton"),
  toggleStreamButton: document.querySelector("#toggleStreamButton"),
  currentPulse: document.querySelector("#currentPulse"),
  referenceMin: document.querySelector("#referenceMin"),
  referenceMax: document.querySelector("#referenceMax"),
  lastResult: document.querySelector("#lastResult"),
  connectionStatus: document.querySelector("#connectionStatus"),
  cameraStatus: document.querySelector("#cameraStatus"),
  cameraImage: document.querySelector("#cameraImage"),
  cameraFallback: document.querySelector("#cameraFallback"),
  calibrationRows: document.querySelector("#calibrationRows"),
};

function apiUrl(path) {
  const url = new URL(path, window.location.origin);
  return url;
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
  if (!response.ok) {
    let detail = "";
    try {
      const errorBody = await response.json();
      detail = errorBody.detail ? `: ${JSON.stringify(errorBody.detail)}` : "";
    } catch (_error) {
      detail = "";
    }
    const error = new Error(`HTTP ${response.status}${detail}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function selectedServo() {
  return state.servos.find((servo) => servo.channel === state.currentChannel);
}

function clampPulse(value) {
  const numericValue = Number.parseInt(value, 10);
  if (Number.isNaN(numericValue)) {
    return 300;
  }
  return Math.max(SAFE_PULSE_MIN, Math.min(SAFE_PULSE_MAX, numericValue));
}

function setConnectionStatus(text, className) {
  elements.connectionStatus.textContent = text;
  elements.connectionStatus.className = `status-pill ${className}`;
}

function renderChannelOptions() {
  elements.channelSelect.innerHTML = "";
  state.servos.forEach((servo) => {
    const option = document.createElement("option");
    option.value = String(servo.channel);
    option.textContent = `${servo.channel} - ${servo.name}`;
    elements.channelSelect.append(option);
  });
}

function renderServoDetails() {
  const servo = selectedServo();
  if (!servo) {
    return;
  }

  elements.channelSelect.value = String(servo.channel);
  elements.servoName.textContent = servo.name;
  elements.rangeBadge.textContent = `${servo.min}-${servo.max}`;
  elements.pulseInput.value = String(servo.pulse);
  elements.currentPulse.textContent = String(servo.pulse);
  elements.referenceMin.textContent = String(servo.min);
  elements.referenceMax.textContent = String(servo.max);
}

function renderTable() {
  elements.calibrationRows.innerHTML = "";
  state.servos.forEach((servo) => {
    const row = document.createElement("tr");
    if (servo.channel === state.currentChannel) {
      row.classList.add("active-row");
    }

    row.innerHTML = `
      <td>${servo.name}</td>
      <td>${servo.channel}</td>
      <td>${servo.min}</td>
      <td>${servo.max}</td>
      <td>${servo.pulse}</td>
    `;
    elements.calibrationRows.append(row);
  });
}

function renderAll() {
  renderServoDetails();
  renderTable();
}

async function loadState() {
  try {
    const data = await fetchJson("/api/state");
    state.servos = data.servos;
    state.currentChannel = data.currentChannel;
    elements.lastResult.textContent = data.lastResult || "Ready";
    elements.cameraStatus.textContent = data.camera?.status || "Camera status unknown";
    renderChannelOptions();
    renderAll();
    setConnectionStatus("Connected", "status-success");
  } catch (error) {
    elements.lastResult.textContent = `Connection failed: ${error.message}`;
    setConnectionStatus("Disconnected", "status-danger");
  }
}

async function setPulse(channel, pulse) {
  const clampedPulse = clampPulse(pulse);
  elements.pulseInput.value = String(clampedPulse);

  try {
    const data = await fetchJson("/api/set-pulse", {
      method: "POST",
      body: JSON.stringify({
        channel,
        pulse: clampedPulse,
      }),
    });

    if (!data.ok) {
      throw new Error(data.error || "Servo command failed");
    }

    const servo = state.servos.find((item) => item.channel === data.channel);
    if (servo) {
      servo.pulse = data.pulse;
    }
    state.currentChannel = data.channel;
    elements.lastResult.textContent = data.lastResult;
    setConnectionStatus("Connected", "status-success");
    renderAll();
  } catch (error) {
    elements.lastResult.textContent = error.message;
    setConnectionStatus(error.status ? "Request Error" : "Bridge Error", "status-danger");
  }
}

async function saveCalibration() {
  try {
    const data = await fetchJson("/api/save-calibration", {
      method: "POST",
      body: JSON.stringify({ save: true }),
    });
    if (!data.ok) {
      throw new Error(data.error || "Save failed");
    }
    elements.lastResult.textContent = "Calibration saved";
    setConnectionStatus("Saved", "status-success");
  } catch (error) {
    elements.lastResult.textContent = error.message;
    setConnectionStatus("Save Failed", "status-danger");
  }
}

function exportCalibration() {
  const calibration = {};
  state.servos.forEach((servo) => {
    calibration[servo.channel] = servo;
  });

  const blob = new Blob([JSON.stringify(calibration, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "uno-q-servo-calibration.json";
  link.click();
  URL.revokeObjectURL(url);
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
    const streamUrl = `http://${window.location.hostname}:7001/video-feed`;
    elements.cameraImage.src = streamUrl;
    elements.cameraFallback.classList.add("hidden");
    elements.cameraStatus.textContent = "MJPEG stream mode";
    elements.toggleStreamButton.textContent = "Snapshots";
    return;
  }

  elements.toggleStreamButton.textContent = "MJPEG";
  startCameraRefresh();
}

elements.channelSelect.addEventListener("change", () => {
  state.currentChannel = Number.parseInt(elements.channelSelect.value, 10);
  renderAll();
});

elements.sendButton.addEventListener("click", () => {
  setPulse(state.currentChannel, elements.pulseInput.value);
});

elements.pulseInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    setPulse(state.currentChannel, elements.pulseInput.value);
  }
});

document.querySelectorAll("[data-step]").forEach((button) => {
  button.addEventListener("click", () => {
    const step = Number.parseInt(button.dataset.step, 10);
    const nextPulse = clampPulse(elements.pulseInput.value) + step;
    setPulse(state.currentChannel, nextPulse);
  });
});

elements.saveButton.addEventListener("click", saveCalibration);
elements.exportButton.addEventListener("click", exportCalibration);
elements.toggleStreamButton.addEventListener("click", toggleStreamMode);

loadState();
startCameraRefresh();
