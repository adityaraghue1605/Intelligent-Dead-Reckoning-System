// SIH 2026 — Intelligent Dead Reckoning (IDR) Application Script
// ==============================================================

let map;
let markerVehicle;
let pathRouteGuide; // Full reference route
let pathGT;         // Active Ground Truth path
let pathIDR;        // Active AI-Powered IDR path
let pathINS;        // Active Conventional INS baseline path
let routeBounds = null;

let ws = null;
let isConnected = false;
let followVehicle = false;

// Waveform buffers
const waveHistoryLength = 60;
const waveAccelX = [];
const waveAccelY = [];
const waveAccelZ = [];

// DOM Elements
const elModeBadge = document.getElementById("nav-mode-badge");
const elModeText = document.getElementById("mode-text");
const elDriftPctVal = document.getElementById("drift-pct-val");
const elDriftBadge = document.getElementById("drift-target-badge");
const elWsIndicator = document.getElementById("ws-indicator");
const elWsLabel = document.getElementById("ws-label");

const elSpeed = document.getElementById("val-speed");
const elGtSpeed = document.getElementById("val-gt-speed");
const elHeading = document.getElementById("val-heading");
const elPitch = document.getElementById("val-pitch");
const elRoll = document.getElementById("val-roll");
const elCompassNeedle = document.getElementById("compass-needle");

const elIdrDrift = document.getElementById("val-idr-drift");
const elIdrPct = document.getElementById("val-idr-pct");
const elInsDrift = document.getElementById("val-ins-drift");
const elInsPct = document.getElementById("val-ins-pct");
const elOutageDist = document.getElementById("val-outage-dist");
const elOutageDur = document.getElementById("val-outage-dur");
const elBenchmarkVerdict = document.getElementById("val-benchmark-verdict");
const elBenchmarkTag = document.getElementById("benchmark-status-tag");

const elCurrentStep = document.getElementById("current-step");
const elTotalSteps = document.getElementById("total-steps");
const elProgressBar = document.getElementById("progress-bar");
const elElapsedTime = document.getElementById("elapsed-time");
const elBlackoutBanner = document.getElementById("blackout-banner");
const elBannerDrift = document.getElementById("banner-drift-val");
const elScenarioSelect = document.getElementById("scenario-select");

const canvasWave = document.getElementById("accel-waveform");
const ctxWave = canvasWave.getContext("2d");

// 1. Initialize Leaflet Map with Geographic Basemaps
function initMap() {
  const defaultPos = [52.43163, -1.525745];

  // Base map layers
  const osmStandard = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  });

  const cartoDark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>'
  });

  const cartoVoyager = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>'
  });

  map = L.map("navigation-map", {
    center: defaultPos,
    zoom: 12,
    zoomControl: true,
    layers: [osmStandard] // Default to free, reliable OpenStreetMap Standard (no API key required)
  });

  // Basemap switcher control
  const baseLayers = {
    "🗺️ OpenStreetMap (Free, No Key)": osmStandard,
    "🌙 Dark Basemap (Carto)": cartoDark,
    "🧭 Voyager Basemap (Carto)": cartoVoyager
  };
  L.control.layers(baseLayers, null, { position: "topright" }).addTo(map);

  // Full route reference guide (translucent green path)
  pathRouteGuide = L.polyline([], {
    color: "#059669",
    weight: 3,
    opacity: 0.35,
    dashArray: "4, 6"
  }).addTo(map);

  // Active polylines
  pathGT = L.polyline([], { color: "#10b981", weight: 4, opacity: 0.85 }).addTo(map);
  pathIDR = L.polyline([], { color: "#00f2fe", weight: 5, opacity: 0.95 }).addTo(map);
  pathINS = L.polyline([], { color: "#ef4444", weight: 3, opacity: 0.85, dashArray: "6, 6" }).addTo(map);

  // Custom Vehicle Icon with dynamic rotation
  const vehicleIcon = L.divIcon({
    className: "vehicle-custom-marker",
    html: `<div id="vehicle-car" style="transform: rotate(0deg); transition: transform 0.15s linear; font-size: 26px; filter: drop-shadow(0 0 8px #00f2fe); text-shadow: 0 0 10px rgba(0,242,254,0.8);">🚘</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });

  markerVehicle = L.marker(defaultPos, { icon: vehicleIcon }).addTo(map);
}

// 2. Pre-fetch and Render Full Scenario Route
function fetchAndRenderRoute(scenarioName) {
  fetch(`/api/v1/scenarios/route?name=${encodeURIComponent(scenarioName)}`)
    .then(r => r.json())
    .then(data => {
      if (data.route && data.route.length > 0) {
        const latLngs = data.route.map(pt => [pt.lat, pt.lon]);
        pathRouteGuide.setLatLngs(latLngs);

        if (data.bounds && data.bounds.min_lat) {
          routeBounds = [
            [data.bounds.min_lat, data.bounds.min_lon],
            [data.bounds.max_lat, data.bounds.max_lon]
          ];
          map.fitBounds(routeBounds, { padding: [40, 40] });
        } else {
          routeBounds = pathRouteGuide.getBounds();
          map.fitBounds(routeBounds, { padding: [40, 40] });
        }

        // Set vehicle to start of route
        markerVehicle.setLatLng(latLngs[0]);
      }
    })
    .catch(err => console.warn("Failed to load scenario route:", err));
}

// 3. Connect WebSocket Telemetry Stream
function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;

  elWsIndicator.className = "ws-dot disconnected";
  elWsLabel.textContent = "Connecting...";

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    isConnected = true;
    elWsIndicator.className = "ws-dot connected";
    elWsLabel.textContent = "Live Stream";
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleTelemetryPacket(data);
    } catch (e) {
      console.error("[WS] Error parsing telemetry:", e);
    }
  };

  ws.onclose = () => {
    isConnected = false;
    elWsIndicator.className = "ws-dot disconnected";
    elWsLabel.textContent = "Reconnecting...";
    setTimeout(connectWebSocket, 2000);
  };

  ws.onerror = (err) => {
    console.error("[WS] Telemetry error:", err);
    ws.close();
  };
}

// 4. Process Live Telemetry Packet
function handleTelemetryPacket(data) {
  const idr = data.idr;
  const gt = data.gt;
  const ins = data.ins_baseline;
  const outage = data.outage || {};
  const tel = data.telemetry;

  const posIDR = [idr.lat, idr.lon];
  const posGT  = [gt.lat, gt.lon];
  const posINS = [ins.lat, ins.lon];

  // 1. Update Trajectory Paths
  pathIDR.addLatLng(posIDR);
  pathGT.addLatLng(posGT);

  if (outage.active || idr.gnss_outage) {
    pathINS.addLatLng(posINS);
  } else {
    // When GNSS is available, keep INS baseline aligned with GT
    pathINS.addLatLng(posGT);
  }

  // 2. Update Vehicle Marker & Heading
  // Follow reference GNSS path during normal aiding, diverge to IDR during blackout
  const isOutage = outage.active || idr.gnss_outage;
  const activeVehiclePos = isOutage ? posIDR : posGT;
  markerVehicle.setLatLng(activeVehiclePos);
  const carEl = document.getElementById("vehicle-car");
  if (carEl) {
    carEl.style.transform = `rotate(${idr.heading_deg}deg)`;
  }

  if (followVehicle) {
    map.panTo(activeVehiclePos, { animate: true, duration: 0.1 });
  }

  // 3. Update Speeds & Headings
  elSpeed.textContent = idr.speed_kmh.toFixed(1);
  elGtSpeed.textContent = gt.speed_kmh.toFixed(1);
  elHeading.textContent = `${Math.round(idr.heading_deg).toString().padStart(3, '0')}°`;
  elPitch.textContent = `${tel.gyro_pitch >= 0 ? '+' : ''}${tel.gyro_pitch.toFixed(1)}°`;
  elRoll.textContent = `${tel.gyro_roll >= 0 ? '+' : ''}${tel.gyro_roll.toFixed(1)}°`;
  elCompassNeedle.style.transform = `rotate(${idr.heading_deg}deg)`;

  // 4. Update Navigation Mode Badge
  updateNavMode(idr.nav_mode, outage.active || idr.gnss_outage);

  // 5. Update Outage Drift Benchmark Display
  if (outage.active || idr.gnss_outage) {
    // Active Outage in Progress
    elBlackoutBanner.classList.remove("hidden");
    elBannerDrift.textContent = `${outage.ai_drift_m.toFixed(2)} m (${outage.ai_drift_pct.toFixed(1)}%)`;

    elIdrDrift.textContent = `${outage.ai_drift_m.toFixed(2)} m`;
    elIdrPct.textContent = `${outage.ai_drift_pct.toFixed(1)}%`;
    elInsDrift.textContent = `${outage.ins_drift_m.toFixed(2)} m`;
    if (elInsPct) elInsPct.textContent = `${outage.ins_drift_pct.toFixed(1)}%`;

    elOutageDist.textContent = `${outage.distance_m.toFixed(1)} m`;
    elOutageDur.textContent = `${outage.duration_s.toFixed(1)} s`;
    elBenchmarkVerdict.textContent = "TUNNEL OUTAGE ACTIVE";
    elBenchmarkVerdict.style.color = "#f59e0b";

    elDriftPctVal.textContent = `${outage.ai_drift_pct.toFixed(1)}%`;
    if (outage.target_pass) {
      elDriftBadge.className = "drift-badge drift-pass";
      elBenchmarkTag.className = "stat-badge target-tag";
      elBenchmarkTag.textContent = "TARGET <10% (OK)";
    } else {
      elDriftBadge.className = "drift-badge drift-fail";
      elBenchmarkTag.className = "stat-badge target-tag-fail";
      elBenchmarkTag.textContent = "TARGET EXCEEDED";
    }
  } else {
    // GNSS Aided / Normal State
    elBlackoutBanner.classList.add("hidden");

    if (outage.last_completed) {
      // Retain last completed benchmark on screen
      const lc = outage.last_completed;
      elIdrDrift.textContent = `${lc.ai_error_m.toFixed(2)} m`;
      elIdrPct.textContent = `${lc.ai_drift_pct.toFixed(1)}%`;
      elInsDrift.textContent = `${lc.ins_error_m.toFixed(2)} m`;
      if (elInsPct) elInsPct.textContent = `${lc.ins_drift_pct.toFixed(1)}%`;

      elOutageDist.textContent = `${lc.distance_travelled_m.toFixed(1)} m`;
      elOutageDur.textContent = `${lc.outage_duration_s.toFixed(1)} s`;
      elBenchmarkVerdict.textContent = lc.target_pass ? "BENCHMARK PASSED (<10%)" : "BENCHMARK FAILED (≥10%)";
      elBenchmarkVerdict.style.color = lc.target_pass ? "#34d399" : "#f87171";

      elDriftPctVal.textContent = `${lc.ai_drift_pct.toFixed(1)}%`;
      elDriftBadge.className = lc.target_pass ? "drift-badge drift-pass" : "drift-badge drift-fail";
      elBenchmarkTag.textContent = lc.target_pass ? "PASSED (<10%)" : "FAILED";
      elBenchmarkTag.className = lc.target_pass ? "stat-badge target-tag" : "stat-badge target-tag-fail";
    } else {
      elIdrDrift.textContent = "0.00 m";
      elIdrPct.textContent = "0.0%";
      elInsDrift.textContent = "0.00 m";
      if (elInsPct) elInsPct.textContent = "0.0%";
      elOutageDist.textContent = "0.0 m";
      elOutageDur.textContent = "0.0 s";
      elBenchmarkVerdict.textContent = "Awaiting Outage";
      elBenchmarkVerdict.style.color = "#94a3b8";
      elDriftPctVal.textContent = "0.0%";
      elDriftBadge.className = "drift-badge drift-pass";
      elBenchmarkTag.textContent = "TARGET <10%";
      elBenchmarkTag.className = "stat-badge target-tag";
    }
  }

  // 6. Progress & Elapsed Time
  elCurrentStep.textContent = data.step;
  elTotalSteps.textContent = data.total_steps;
  const pct = (data.step / data.total_steps) * 100;
  elProgressBar.style.width = `${pct}%`;

  if (data.timestamp_ms) {
    const totalSec = Math.floor(data.timestamp_ms / 1000);
    const mins = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const secs = (totalSec % 60).toString().padStart(2, '0');
    elElapsedTime.textContent = `${mins}:${secs}`;
  }

  // 7. Accelerometer Waveforms
  pushWaveform(tel.acc_x, tel.acc_y, tel.acc_z);
  drawWaveforms();
}

// 5. Navigation Mode Badge Helper
function updateNavMode(mode, inOutage) {
  elModeBadge.className = "mode-badge";
  if (inOutage || mode === "IDR_OUTAGE") {
    elModeBadge.classList.add("mode-outage");
    elModeText.textContent = "IDR / GNSS LOST";
  } else if (mode === "IDR_ZUPT") {
    elModeBadge.classList.add("mode-zupt");
    elModeText.textContent = "ZERO VELOCITY (ZUPT)";
  } else if (mode === "RECOVERY") {
    elModeBadge.classList.add("mode-recovery");
    elModeText.textContent = "GNSS RECOVERY";
  } else {
    elModeBadge.classList.add("mode-gnss");
    elModeText.textContent = "GNSS + INS AIDED";
  }
}

// 6. Waveform Canvas Renderer
function pushWaveform(ax, ay, az) {
  waveAccelX.push(ax);
  waveAccelY.push(ay);
  waveAccelZ.push(az - 9.81);
  if (waveAccelX.length > waveHistoryLength) {
    waveAccelX.shift();
    waveAccelY.shift();
    waveAccelZ.shift();
  }
}

function drawWaveforms() {
  const w = canvasWave.width;
  const h = canvasWave.height;
  ctxWave.clearRect(0, 0, w, h);

  ctxWave.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctxWave.lineWidth = 1;
  ctxWave.beginPath();
  ctxWave.moveTo(0, h / 2);
  ctxWave.lineTo(w, h / 2);
  ctxWave.stroke();

  const drawChannel = (arr, color) => {
    if (arr.length < 2) return;
    ctxWave.strokeStyle = color;
    ctxWave.lineWidth = 1.5;
    ctxWave.beginPath();
    const dx = w / (waveHistoryLength - 1);
    const scale = 10.0;
    for (let i = 0; i < arr.length; i++) {
      const x = i * dx;
      const y = h / 2 - (arr[i] * scale);
      if (i === 0) ctxWave.moveTo(x, y);
      else ctxWave.lineTo(x, y);
    }
    ctxWave.stroke();
  };

  drawChannel(waveAccelX, "#38bdf8");
  drawChannel(waveAccelY, "#34d399");
  drawChannel(waveAccelZ, "#fbbf24");
}

// 7. UI Controls & Event Listeners
document.getElementById("btn-play").addEventListener("click", () => {
  fetch("/api/v1/simulation/play", { method: "POST" });
});

document.getElementById("btn-pause").addEventListener("click", () => {
  fetch("/api/v1/simulation/pause", { method: "POST" });
});

document.getElementById("btn-reset").addEventListener("click", () => {
  fetch("/api/v1/simulation/reset", { method: "POST" }).then(() => {
    pathGT.setLatLngs([]);
    pathIDR.setLatLngs([]);
    pathINS.setLatLngs([]);
    if (routeBounds) map.fitBounds(routeBounds, { padding: [40, 40] });
  });
});

document.getElementById("btn-tunnel-30").addEventListener("click", () => {
  fetch("/api/v1/simulation/blackout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active: true, duration_seconds: 30 })
  });
});

document.getElementById("btn-tunnel-60").addEventListener("click", () => {
  fetch("/api/v1/simulation/blackout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active: true, duration_seconds: 60 })
  });
});

let manualBlackout = false;
document.getElementById("btn-toggle-blackout").addEventListener("click", () => {
  manualBlackout = !manualBlackout;
  fetch("/api/v1/simulation/blackout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active: manualBlackout })
  });
  document.getElementById("btn-toggle-blackout").textContent = manualBlackout ? "🟢 Restore GNSS" : "🔴 Toggle Blackout";
});

document.getElementById("btn-fit-route").addEventListener("click", () => {
  followVehicle = false;
  if (routeBounds) {
    map.fitBounds(routeBounds, { padding: [40, 40] });
  } else if (pathRouteGuide && pathRouteGuide.getLatLngs().length > 0) {
    map.fitBounds(pathRouteGuide.getBounds(), { padding: [40, 40] });
  }
});

document.getElementById("btn-recenter").addEventListener("click", () => {
  followVehicle = true;
  if (markerVehicle) {
    map.panTo(markerVehicle.getLatLng());
  }
});

document.getElementById("btn-clear-path").addEventListener("click", () => {
  pathGT.setLatLngs([]);
  pathIDR.setLatLngs([]);
  pathINS.setLatLngs([]);
});

elScenarioSelect.addEventListener("change", (e) => {
  const scName = e.target.value;
  fetch("/api/v1/scenarios/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario_name: scName })
  }).then(() => {
    pathGT.setLatLngs([]);
    pathIDR.setLatLngs([]);
    pathINS.setLatLngs([]);
    fetchAndRenderRoute(scName);
  });
});

// Populate scenarios from backend
function loadScenarioList() {
  fetch("/api/v1/scenarios")
    .then(r => r.json())
    .then(data => {
      if (data.scenarios && data.scenarios.length > 0) {
        elScenarioSelect.innerHTML = "";
        data.scenarios.forEach(sc => {
          const opt = document.createElement("option");
          opt.value = sc.name;
          opt.textContent = `${sc.name} (${sc.size_kb} KB)`;
          if (sc.name === data.current) opt.selected = true;
          elScenarioSelect.appendChild(opt);
        });
        fetchAndRenderRoute(data.current || "S-A1.csv");
      }
    })
    .catch(err => console.warn("Could not fetch scenario list:", err));
}

// Startup
window.addEventListener("DOMContentLoaded", () => {
  initMap();
  connectWebSocket();
  loadScenarioList();
});
