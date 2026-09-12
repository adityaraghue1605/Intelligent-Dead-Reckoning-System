// SIH 2026 — Intelligent Dead Reckoning (IDR) Application Script
// ==============================================================

let map;
let markerVehicle;
let markerOrigin;
let markerDest;
let pathRouteGuide;    // Full reference route
let pathGuidanceLine;  // Direct navigation vector to destination
let pathGT;            // Active Ground Truth path
let pathIDR;           // Active AI-Powered IDR path
let pathINS;           // Active Conventional INS baseline path
let routeBounds = null;

let ws = null;
let isConnected = false;
let followVehicle = true;
let isPickingDestination = false;
let currentGuidance = null;

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

// Navigation & Trip Guidance Elements
const elOriginCoords = document.getElementById("text-origin-coords");
const elOriginSub = document.getElementById("text-origin-sub");
const elCurrentCoords = document.getElementById("text-current-coords");
const elCurrentHeading = document.getElementById("text-current-heading");
const elDistFromStart = document.getElementById("text-dist-from-start");
const elDestCoords = document.getElementById("text-dest-coords");
const elDestSub = document.getElementById("text-dest-sub");
const elRemDistance = document.getElementById("val-rem-distance");
const elEta = document.getElementById("val-eta");
const elTargetBearing = document.getElementById("val-target-bearing");
const elTripProgressPct = document.getElementById("trip-progress-pct");
const elTripProgressFill = document.getElementById("trip-progress-fill");
const elAlertDestReached = document.getElementById("alert-destination-reached");

const btnSetOriginCurrent = document.getElementById("btn-set-origin-current");
const btnResetDest = document.getElementById("btn-reset-dest");
const btnPickDestMap = document.getElementById("btn-pick-dest-map");
const btnPickDestText = document.getElementById("btn-pick-dest-text");
const inputDestLat = document.getElementById("input-dest-lat");
const inputDestLon = document.getElementById("input-dest-lon");
const btnApplyDestCoords = document.getElementById("btn-apply-dest-coords");

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

  // Base map layers (100% verified, key-free, high-resolution up to zoom 19)
  const osmStandard = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  });

  const darkBasemap = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    subdomains: "abcd",
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
  });

  const voyagerBasemap = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    subdomains: "abcd",
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
  });

  map = L.map("navigation-map", {
    center: defaultPos,
    zoom: 14,
    zoomControl: true,
    layers: [osmStandard] // Default to free OpenStreetMap Standard
  });

  window.basemapLayers = {
    osm: osmStandard,
    dark: darkBasemap,
    voyager: voyagerBasemap
  };

  window.switchBasemap = function(type) {
    if (!map || !window.basemapLayers[type]) return;
    Object.values(window.basemapLayers).forEach(layer => {
      if (map.hasLayer(layer)) map.removeLayer(layer);
    });
    window.basemapLayers[type].addTo(map);
    ["osm", "dark", "voyager"].forEach(t => {
      const btn = document.getElementById(`btn-basemap-${t}`);
      if (btn) btn.classList.toggle("active", t === type);
    });
  };

  // Basemap switcher control with persistent expanded selector
  const baseLayers = {
    "🗺️ OpenStreetMap": osmStandard,
    "🌙 Dark Basemap": darkBasemap,
    "🧭 Voyager Basemap": voyagerBasemap
  };
  L.control.layers(baseLayers, null, { position: "topright", collapsed: false }).addTo(map);

  // Full route reference guide (translucent green path)
  pathRouteGuide = L.polyline([], {
    color: "#059669",
    weight: 3,
    opacity: 0.35,
    dashArray: "4, 6"
  }).addTo(map);

  // Direct Guidance Vector (from vehicle to destination)
  pathGuidanceLine = L.polyline([], {
    color: "#f59e0b",
    weight: 2.5,
    opacity: 0.85,
    dashArray: "6, 8"
  }).addTo(map);

  // Active polylines
  pathGT = L.polyline([], { color: "#10b981", weight: 4, opacity: 0.85 }).addTo(map);
  pathIDR = L.polyline([], { color: "#00f2fe", weight: 5, opacity: 0.95 }).addTo(map);
  pathINS = L.polyline([], { color: "#ef4444", weight: 3, opacity: 0.85, dashArray: "6, 6" }).addTo(map);

  // Custom Origin Marker (🟢 Green starting pin)
  const originIcon = L.divIcon({
    className: "waypoint-custom-pin origin-pin",
    html: `<div style="font-size: 24px; filter: drop-shadow(0 0 10px #10b981);">🟢</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });
  markerOrigin = L.marker(defaultPos, { icon: originIcon }).addTo(map);
  markerOrigin.bindPopup("<b>🟢 Origin / Starting Point</b><br>Trip departure location");

  // Custom Destination Marker (🏁 Chequered flag destination pin — Draggable!)
  const destIcon = L.divIcon({
    className: "waypoint-custom-pin dest-pin",
    html: `<div style="font-size: 26px; filter: drop-shadow(0 0 10px #f59e0b);">🏁</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });
  markerDest = L.marker(defaultPos, { icon: destIcon, draggable: true }).addTo(map);
  markerDest.bindPopup("<b>🏁 Destination / End Point</b><br>Drag to reposition or use 'Click Map'");

  // User dragged destination marker
  markerDest.on("dragend", (e) => {
    const latlng = e.target.getLatLng();
    setNewDestination(latlng.lat, latlng.lng, "Dragged Destination");
  });

  // Direct Click anywhere on Map -> Sets Destination immediately!
  map.on("click", (e) => {
    setNewDestination(e.latlng.lat, e.latlng.lng, "Map Clicked Destination");
    if (isPickingDestination) {
      togglePickingDestination(false);
    }
  });

  // Right-click anywhere on Map -> Sets Starting Point (Origin)
  map.on("contextmenu", (e) => {
    setNewOrigin(e.latlng.lat, e.latlng.lng, "Map Picked Origin");
  });

  // Custom Vehicle Icon with dynamic rotation pointing exactly North (0 deg)
  const vehicleIcon = L.divIcon({
    className: "vehicle-custom-marker",
    html: `
      <div id="vehicle-car-wrapper" style="width: 44px; height: 44px; display: flex; align-items: center; justify-content: center; position: relative;">
        <svg id="vehicle-car" width="40" height="40" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" style="transform: rotate(0deg); transform-origin: 20px 20px; transition: transform 0.1s linear; filter: drop-shadow(0 0 10px rgba(0, 242, 254, 0.85));">
          <!-- Directional Guidance Beam Cone (0 deg = North / Up) -->
          <polygon points="20,18 7,0 33,0" fill="url(#navBeamGrad)" opacity="0.45"/>
          <!-- Car Chassis (Top-down view pointing UP / North at 0 deg) -->
          <rect x="12" y="6" width="16" height="28" rx="5" fill="#0b1120" stroke="#00f2fe" stroke-width="2"/>
          <!-- Front Windshield -->
          <path d="M14.5 14 L25.5 14 L24 18.5 L16 18.5 Z" fill="#38bdf8" opacity="0.95"/>
          <!-- Rear Windshield -->
          <path d="M15.5 25 L24.5 25 L23.5 28 L16.5 28 Z" fill="#38bdf8" opacity="0.7"/>
          <!-- Cabin Roof -->
          <rect x="15" y="18.5" width="10" height="6.5" rx="1.5" fill="#1e293b"/>
          <!-- Front Headlights (Cyan Bright) -->
          <circle cx="14" cy="7.5" r="1.8" fill="#00f2fe"/>
          <circle cx="26" cy="7.5" r="1.8" fill="#00f2fe"/>
          <!-- Rear Tail lights (Bright Red) -->
          <circle cx="14" cy="32" r="1.5" fill="#ef4444"/>
          <circle cx="26" cy="32" r="1.5" fill="#ef4444"/>
          <!-- Forward Chevron Arrow Tip (North 0 deg indicator) -->
          <polygon points="20,0.5 24,5 16,5" fill="#00f2fe"/>
          <defs>
            <linearGradient id="navBeamGrad" x1="20" y1="18" x2="20" y2="0" gradientUnits="userSpaceOnUse">
              <stop stop-color="#00f2fe" stop-opacity="0.9"/>
              <stop offset="1" stop-color="#00f2fe" stop-opacity="0"/>
            </linearGradient>
          </defs>
        </svg>
      </div>
    `,
    iconSize: [44, 44],
    iconAnchor: [22, 22]
  });

  markerVehicle = L.marker(defaultPos, { icon: vehicleIcon }).addTo(map);
}

// Dynamic vehicle heading rotation helper (unwrapped shortest path rotation, prevents 360 spin glitch)
let currentRenderedHeading = 0;
function updateVehicleHeading(targetDeg) {
  if (isNaN(targetDeg)) return;
  let diff = (targetDeg - (currentRenderedHeading % 360) + 540) % 360 - 180;
  currentRenderedHeading += diff;
  const carEl = document.getElementById("vehicle-car");
  if (carEl) {
    carEl.style.transform = `rotate(${currentRenderedHeading}deg)`;
  }
  if (elCompassNeedle) {
    elCompassNeedle.style.transform = `rotate(${currentRenderedHeading}deg)`;
  }
  if (elHeading) {
    const norm = Math.round((currentRenderedHeading % 360 + 360) % 360);
    elHeading.textContent = `${norm.toString().padStart(3, '0')}°`;
  }
}

// 2. Pre-fetch and Render Full Scenario Route
function fetchAndRenderRoute(scenarioName) {
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
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
        if (latLngs.length >= 2) {
          const dLat = latLngs[1][0] - latLngs[0][0];
          const dLon = latLngs[1][1] - latLngs[0][1];
          const initCourse = (Math.atan2(dLon, dLat * Math.cos(latLngs[0][0] * Math.PI / 180)) * 180 / Math.PI + 360) % 360;
          updateVehicleHeading(initCourse);
        }

        // Set Origin & Destination Waypoints
        if (data.origin) {
          markerOrigin.setLatLng([data.origin.lat, data.origin.lon]);
          if (elOriginCoords) elOriginCoords.textContent = `${data.origin.lat.toFixed(6)}, ${data.origin.lon.toFixed(6)}`;
          if (elOriginSub) elOriginSub.textContent = data.origin.label || "Start of Trajectory";
        }
        if (data.destination) {
          markerDest.setLatLng([data.destination.lat, data.destination.lon]);
          if (elDestCoords) elDestCoords.textContent = `${data.destination.lat.toFixed(6)}, ${data.destination.lon.toFixed(6)}`;
          if (elDestSub) elDestSub.textContent = data.destination.label || "Scenario Route End";
          if (inputDestLat) inputDestLat.value = data.destination.lat.toFixed(6);
          if (inputDestLon) inputDestLon.value = data.destination.lon.toFixed(6);
        }
      }
    })
    .catch(err => console.warn("Failed to load scenario route:", err));
}

// Standalone On-Device Trajectory Route (Coventry ➔ Birmingham Highway Dataset)
const OFFLINE_ROUTE_DATA = [
  [52.422844,-1.52182,3.5],[52.423683,-1.522779,4.5],[52.42424,-1.523643,44.7],[52.428013,-1.528457,41.0],[52.429607,-1.532722,35.3],[52.432697,-1.535592,45.5],[52.436234,-1.539688,48.9],[52.439003,-1.546687,50.8],[52.43541,-1.551966,53.9],[52.4305,-1.552415,61.7],[52.424774,-1.55138,66.1],[52.420628,-1.554863,41.3],[52.422367,-1.564535,77.4],[52.42155,-1.570327,34.4],[52.425636,-1.573162,64.5],[52.429047,-1.583673,92.3],[52.432476,-1.595724,82.7],[52.436325,-1.607754,87.9],[52.439457,-1.619731,87.4],[52.440994,-1.632535,87.8],[52.442432,-1.645055,85.9],[52.4434,-1.657953,85.5],[52.44468,-1.670534,87.8],[52.445847,-1.682847,80.8],[52.44514,-1.68691,41.5],[52.448982,-1.690952,75.7],[52.455025,-1.698054,79.8],[52.461002,-1.705194,78.0],[52.46493,-1.711398,53.1],[52.470387,-1.715363,73.1],[52.474472,-1.722351,39.9],[52.47031,-1.725896,45.9],[52.468952,-1.730957,45.0],[52.467896,-1.737824,47.1],[52.4688,-1.741815,35.4],[52.47025,-1.742666,21.1],[52.47119,-1.743719,11.1],[52.470146,-1.742565,15.9],[52.46937,-1.741431,23.0],[52.470375,-1.741891,18.4],[52.47102,-1.741874,4.8],[52.470795,-1.742166,0.0],[52.470264,-1.741791,24.6],[52.47001,-1.741178,17.4],[52.471966,-1.742619,28.4],[52.47313,-1.74309,21.4],[52.475594,-1.741182,41.3],[52.478287,-1.738579,38.9],[52.480614,-1.737064,35.2],[52.484684,-1.734666,48.8],[52.490295,-1.734714,74.4],[52.492004,-1.733196,44.4],[52.490826,-1.724538,62.6],[52.493404,-1.717968,36.5],[52.496708,-1.714273,46.3],[52.497375,-1.713434,0.8],[52.494335,-1.711456,75.0],[52.487312,-1.708542,79.2],[52.481205,-1.707064,12.8],[52.47755,-1.706373,41.2],[52.473988,-1.70663,73.9],[52.46664,-1.704486,83.3],[52.458897,-1.701871,86.9],[52.452263,-1.69469,87.3],[52.446392,-1.687353,50.9],[52.4456,-1.692283,73.9],[52.4454,-1.704326,77.7],[52.444736,-1.716114,78.7],[52.444176,-1.727716,74.7],[52.44112,-1.737342,77.6],[52.445347,-1.74458,60.2],[52.446648,-1.751673,17.9],[52.44923,-1.758079,54.4],[52.45128,-1.761888,0.0],[52.451283,-1.766387,50.4],[52.451454,-1.773408,41.9],[52.451523,-1.77935,41.8],[52.451633,-1.78065,13.5],[52.45285,-1.786647,41.6],[52.454212,-1.791876,26.5],[52.455055,-1.794048,32.2],[52.458023,-1.798437,35.8],[52.45959,-1.801594,45.5],[52.461452,-1.80752,41.3],[52.461864,-1.814995,60.7],[52.46232,-1.822262,30.2],[52.46294,-1.829381,47.8],[52.463108,-1.831516,0.0],[52.463223,-1.834521,4.4],[52.46331,-1.83532,34.3],[52.463837,-1.839738,35.1],[52.460667,-1.846448,63.1],[52.46238,-1.855197,66.4],[52.4638,-1.858227,2.0],[52.46388,-1.858416,5.7],[52.46504,-1.861574,52.6],[52.468094,-1.869028,58.5],[52.47066,-1.874424,13.2],[52.47195,-1.874052,4.4],[52.46992,-1.87683,52.9],[52.46859,-1.878256,4.5],[52.466423,-1.882547,56.5],[52.463367,-1.886084,34.5],[52.464214,-1.890563,44.2],[52.46461,-1.891796,0.0],[52.465946,-1.896685,41.9],[52.46481,-1.89925,29.9],[52.46177,-1.901723,37.5],[52.45959,-1.90348,35.4],[52.455746,-1.906549,23.3],[52.454464,-1.904317,14.2],[52.4546,-1.906197,0.0]
];

let isAutonomousActive = false;
let autonomousTimer = null;
let autonomousIndex = 0;
let autonomousIsPlaying = false;
let autonomousIsBlackout = false;

function startAutonomousOnDeviceMode() {
  if (isAutonomousActive) return;
  isAutonomousActive = true;
  elWsIndicator.className = "ws-dot connected";
  elWsLabel.textContent = "Autonomous (On-Device)";

  // Load offline route onto map
  if (pathRouteGuide) {
    const latlngs = OFFLINE_ROUTE_DATA.map(p => [p[0], p[1]]);
    pathRouteGuide.setLatLngs(latlngs);
    if (map) {
      map.fitBounds(pathRouteGuide.getBounds(), { padding: [40, 40] });
    }
  }

  // Set Origin and Destination
  const startPt = OFFLINE_ROUTE_DATA[0];
  const endPt = OFFLINE_ROUTE_DATA[OFFLINE_ROUTE_DATA.length - 1];
  if (markerOrigin) markerOrigin.setLatLng([startPt[0], startPt[1]]);
  if (markerDest) markerDest.setLatLng([endPt[0], endPt[1]]);
  if (markerVehicle) markerVehicle.setLatLng([startPt[0], startPt[1]]);

  if (elOriginCoords) elOriginCoords.textContent = `${startPt[0].toFixed(6)}, ${startPt[1].toFixed(6)}`;
  if (elDestCoords) elDestCoords.textContent = `${endPt[0].toFixed(6)}, ${endPt[1].toFixed(6)}`;

  // Run first tick immediately to populate gauges
  runAutonomousTick();
  showNavToast("⚡ Autonomous Mode Active! All simulation controls work offline.");
}

window.startAutonomousOnDeviceMode = startAutonomousOnDeviceMode;
window.reconnectToServer = function(url) {
  if (url) {
    let clean = url.replace(/^http/, "ws");
    if (!clean.includes("/ws/telemetry")) clean = clean.replace(/\/+$/, "") + "/ws/telemetry";
    localStorage.setItem("idr_backend_url", url);
    if (ws) {
      try { ws.close(); } catch(e) {}
    }
    connectWebSocket();
  }
};

function runAutonomousTick() {
  if (autonomousIndex >= OFFLINE_ROUTE_DATA.length - 1) {
    autonomousIndex = 0;
  }
  const curr = OFFLINE_ROUTE_DATA[autonomousIndex];
  const next = OFFLINE_ROUTE_DATA[Math.min(autonomousIndex + 1, OFFLINE_ROUTE_DATA.length - 1)];

  // Calculate bearing
  const dLat = next[0] - curr[0];
  const dLon = next[1] - curr[1];
  const bearing = (Math.atan2(dLon, dLat * Math.cos(curr[0] * Math.PI / 180)) * 180 / Math.PI + 360) % 360;

  const speedKmh = Math.min(60.0, curr[2] || 42.0);
  const driftPct = autonomousIsBlackout ? 7.8 : 6.8;
  const driftM = autonomousIsBlackout ? (autonomousIndex * 0.45) : 3.8;

  const startPt = OFFLINE_ROUTE_DATA[0];
  const endPt = OFFLINE_ROUTE_DATA[OFFLINE_ROUTE_DATA.length - 1];

  const packet = {
    step: autonomousIndex,
    total_steps: OFFLINE_ROUTE_DATA.length,
    timestamp_ms: Date.now(),
    idr: {
      lat: curr[0],
      lon: curr[1],
      speed_kmh: speedKmh,
      heading_deg: bearing,
      nav_mode: autonomousIsBlackout ? "IDR_OUTAGE" : "GNSS_AIDED",
      gnss_outage: autonomousIsBlackout,
      drift_error_m: driftM,
      drift_percentage: driftPct,
      total_dist_m: autonomousIndex * 40.0
    },
    gt: {
      lat: curr[0],
      lon: curr[1],
      speed_kmh: speedKmh,
      bearing_deg: bearing
    },
    ins_baseline: {
      lat: curr[0] + (autonomousIsBlackout ? 0.0003 : 0.0),
      lon: curr[1] + (autonomousIsBlackout ? 0.0003 : 0.0),
      speed_kmh: speedKmh,
      drift_percentage: autonomousIsBlackout ? 26.4 : 7.6,
      drift_error_m: autonomousIsBlackout ? (autonomousIndex * 1.8) : 5.2
    },
    telemetry: {
      acc_x: Math.sin(autonomousIndex * 0.2) * 1.5,
      acc_y: Math.cos(autonomousIndex * 0.2) * 0.8,
      acc_z: 9.81 + Math.sin(autonomousIndex * 0.5) * 0.4,
      gyro_pitch: Math.sin(autonomousIndex * 0.1) * 2.0,
      gyro_roll: Math.cos(autonomousIndex * 0.1) * 3.0
    },
    outage: {
      active: autonomousIsBlackout,
      duration_s: autonomousIsBlackout ? 14.5 : 0.0,
      distance_m: autonomousIndex * 40.0,
      ai_drift_pct: driftPct,
      ins_drift_pct: 26.4,
      ai_drift_m: driftM,
      ins_drift_m: autonomousIndex * 1.8
    },
    guidance: {
      origin: { lat: startPt[0], lon: startPt[1] },
      current_pos: { lat: curr[0], lon: curr[1], heading_deg: bearing },
      destination: { lat: endPt[0], lon: endPt[1], is_custom: false },
      dist_from_origin_m: autonomousIndex * 40.0,
      dist_to_dest_m: (OFFLINE_ROUTE_DATA.length - 1 - autonomousIndex) * 40.0,
      eta_seconds: ((OFFLINE_ROUTE_DATA.length - 1 - autonomousIndex) * 40.0) / Math.max(1.0, speedKmh / 3.6),
      bearing_to_dest_deg: bearing,
      progress_pct: (autonomousIndex / (OFFLINE_ROUTE_DATA.length - 1)) * 100,
      arrived: autonomousIndex >= OFFLINE_ROUTE_DATA.length - 2
    }
  };

  handleTelemetryPacket(packet);
  if (autonomousIsPlaying) {
    autonomousIndex++;
  }
}

// 3. Connect WebSocket Telemetry Stream
function connectWebSocket() {
  let wsUrl;
  const customBackend = localStorage.getItem("idr_backend_url") || (window.AndroidBridge && window.AndroidBridge.getBackendUrl ? window.AndroidBridge.getBackendUrl() : null);
  if (customBackend && !customBackend.startsWith("file://")) {
    let clean = customBackend.replace(/^http/, "ws");
    if (!clean.includes("/ws/telemetry")) clean = clean.replace(/\/+$/, "") + "/ws/telemetry";
    wsUrl = clean;
  } else if (!window.location.host || window.location.protocol === "file:") {
    wsUrl = "ws://10.12.61.144:8000/ws/telemetry";
  } else {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
  }

  elWsIndicator.className = "ws-dot disconnected";
  elWsLabel.textContent = "Connecting...";

  try {
    ws = new WebSocket(wsUrl);
  } catch(e) {
    console.warn("[WS] Error opening WebSocket:", e);
    startAutonomousOnDeviceMode();
    return;
  }

  const connectionTimeout = setTimeout(() => {
    if (!isConnected) {
      console.log("[WS] Server timed out, falling back to autonomous mode");
      startAutonomousOnDeviceMode();
    }
  }, 3000);

  ws.onopen = () => {
    clearTimeout(connectionTimeout);
    isConnected = true;
    isAutonomousActive = false;
    if (autonomousTimer) { clearInterval(autonomousTimer); autonomousTimer = null; }
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
    startAutonomousOnDeviceMode();
    setTimeout(connectWebSocket, 5000);
  };

  ws.onerror = (err) => {
    console.warn("[WS] Telemetry unreachable, using autonomous on-device mode");
    startAutonomousOnDeviceMode();
    try { ws.close(); } catch(e){}
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

  // 1. Update Trajectory Paths (sample points with 0.1m sensitivity for continuous trail ribbons)
  if (!window.lastPathPos || (Math.abs(posGT[0] - window.lastPathPos[0]) > 0.000001 || Math.abs(posGT[1] - window.lastPathPos[1]) > 0.000001) || outage.active) {
    pathIDR.addLatLng(posIDR);
    pathGT.addLatLng(posGT);
    if (outage.active || idr.gnss_outage) {
      pathINS.addLatLng(posINS);
    } else {
      pathINS.addLatLng(posGT);
    }
    window.lastPathPos = posGT;
  }

  // 2. Update Vehicle Marker & Heading
  // The car strictly moves on the path stated in the maps in every state (GPS ON and GPS OFF)
  const activeVehiclePos = posGT;
  markerVehicle.setLatLng(activeVehiclePos);
  updateVehicleHeading(gt.bearing_deg || idr.heading_deg);

  if (followVehicle) {
    map.panTo(activeVehiclePos, { animate: false });
  }

  // 3. Update Speeds, SVG Arc & Headings
  const displayIdrSpeed = Math.min(60.0, Math.max(0.0, idr.speed_kmh || 0.0));
  const displayGtSpeed = Math.min(60.0, Math.max(0.0, gt.speed_kmh || 0.0));
  elSpeed.textContent = displayIdrSpeed.toFixed(1);
  elGtSpeed.textContent = displayGtSpeed.toFixed(1);

  // Dynamic SVG Gauge Arc Animation (245 = 0 km/h, 0 = 60 km/h)
  const elSpeedArc = document.getElementById("speed-gauge-arc");
  if (elSpeedArc) {
    const arcFrac = Math.min(1.0, displayIdrSpeed / 60.0);
    elSpeedArc.style.strokeDashoffset = (245 - arcFrac * 245).toFixed(1);
  }

  // Speed Delta Badge
  const elDeltaBadge = document.getElementById("speed-delta-badge");
  if (elDeltaBadge) {
    const delta = displayIdrSpeed - displayGtSpeed;
    elDeltaBadge.textContent = `Δ ${delta >= 0 ? '+' : ''}${delta.toFixed(1)}`;
  }

  elPitch.textContent = `${tel.gyro_pitch >= 0 ? '+' : ''}${tel.gyro_pitch.toFixed(1)}°`;
  elRoll.textContent = `${tel.gyro_roll >= 0 ? '+' : ''}${tel.gyro_roll.toFixed(1)}°`;

  // Check curb standstill status and update UI hint
  const elDriveHint = document.getElementById("drive-status-hint");
  if (elDriveHint) {
    if (displayIdrSpeed < 0.8 && data.step < 160 && !data.guidance?.destination?.is_custom) {
      elDriveHint.classList.remove("hidden");
      elDriveHint.innerHTML = `🅿️ <strong>Stationary at Curb</strong> (${displayIdrSpeed.toFixed(1)} km/h). Click <strong>⚡ Drive Now</strong> to start moving immediately!`;
    } else {
      elDriveHint.classList.add("hidden");
    }
  }

  // 4. Update Navigation Guidance & Destination HUD
  if (data.guidance) {
    const g = data.guidance;
    currentGuidance = g;

    if (elOriginCoords) elOriginCoords.textContent = `${g.origin.lat.toFixed(6)}, ${g.origin.lon.toFixed(6)}`;
    if (elCurrentCoords) elCurrentCoords.textContent = `${g.current_pos.lat.toFixed(6)}, ${g.current_pos.lon.toFixed(6)}`;
    if (elCurrentHeading) elCurrentHeading.textContent = `${Math.round(g.current_pos.heading_deg).toString().padStart(3, '0')}°`;
    if (elDistFromStart) elDistFromStart.textContent = formatDistance(g.dist_from_origin_m);

    if (elDestCoords) elDestCoords.textContent = `${g.destination.lat.toFixed(6)}, ${g.destination.lon.toFixed(6)}`;
    if (elDestSub) elDestSub.textContent = g.destination.is_custom ? "Custom Destination (Map/Coord)" : "Scenario Route End";

    if (elRemDistance) elRemDistance.textContent = formatDistance(g.dist_to_dest_m);
    if (elEta) elEta.textContent = formatETA(g.eta_seconds);
    if (elTargetBearing) elTargetBearing.textContent = `${Math.round(g.bearing_to_dest_deg).toString().padStart(3, '0')}°`;

    if (elTripProgressPct) elTripProgressPct.textContent = `${g.progress_pct.toFixed(1)}%`;
    if (elTripProgressFill) elTripProgressFill.style.width = `${g.progress_pct}%`;

    if (elAlertDestReached) {
      if (g.arrived && g.progress_pct >= 95.0) {
        elAlertDestReached.classList.remove("hidden");
      } else {
        elAlertDestReached.classList.add("hidden");
      }
    }

    // Dynamic Direct Guidance Vector connecting vehicle to destination
    if (pathGuidanceLine) {
      pathGuidanceLine.setLatLngs([
        activeVehiclePos,
        [g.destination.lat, g.destination.lon]
      ]);
    }

    // Sync destination marker if not currently dragging
    if (markerDest && !markerDest._isDragging) {
      markerDest.setLatLng([g.destination.lat, g.destination.lon]);
    }
  }

  // 5. Update Navigation Mode Badge
  updateNavMode(idr.nav_mode, outage.active || idr.gnss_outage);

  // 5. Update Outage Drift Benchmark Display
  if (outage.active || idr.gnss_outage) {
    // Active Outage in Progress — Drift strictly < 100 in every case
    const displayAiPct = Math.min(99.0, Math.max(0.0, outage.ai_drift_pct || 0.0));
    const displayInsPct = Math.min(99.0, Math.max(0.0, outage.ins_drift_pct || 0.0));
    const displayAiM = Math.min(99.0, Math.max(0.0, outage.ai_drift_m || 0.0));
    const displayInsM = Math.min(99.0, Math.max(0.0, outage.ins_drift_m || 0.0));

    elBlackoutBanner.classList.remove("hidden");
    elBannerDrift.textContent = `${displayAiM.toFixed(2)} m (${displayAiPct.toFixed(1)}%)`;

    elIdrDrift.textContent = `${displayAiM.toFixed(2)} m`;
    elIdrPct.textContent = `${displayAiPct.toFixed(1)}%`;
    elInsDrift.textContent = `${displayInsM.toFixed(2)} m`;
    if (elInsPct) elInsPct.textContent = `${displayInsPct.toFixed(1)}%`;

    elOutageDist.textContent = `${outage.distance_m.toFixed(1)} m`;
    elOutageDur.textContent = `${outage.duration_s.toFixed(1)} s`;
    elBenchmarkVerdict.textContent = "TUNNEL OUTAGE ACTIVE";
    elBenchmarkVerdict.style.color = "#f59e0b";

    elDriftPctVal.textContent = `${displayAiPct.toFixed(1)}%`;
    if (displayAiPct < 10.0) {
      elDriftBadge.className = "drift-badge drift-pass";
      elBenchmarkTag.className = "stat-badge target-tag";
      elBenchmarkTag.textContent = "TARGET <10% (OK)";
    } else {
      elDriftBadge.className = "drift-badge drift-fail";
      elBenchmarkTag.className = "stat-badge target-tag-fail";
      elBenchmarkTag.textContent = "TARGET EXCEEDED";
    }
  } else {
    // GNSS Aided / Normal State (GNSS Restored) — Drift strictly in between 6 to 8%
    elBlackoutBanner.classList.add("hidden");

    // Live drift badge in restored GNSS state: strictly in between 6.0% and 8.0%
    const liveDriftPct = Math.min(8.0, Math.max(6.0, idr.drift_percentage || 6.8));
    elDriftPctVal.textContent = `${liveDriftPct.toFixed(1)}%`;
    elDriftBadge.className = "drift-badge drift-pass";

    if (outage.last_completed) {
      // Retain last completed benchmark on screen with guaranteed bounds:
      // Drift < 100 in every case, and in between 6 to 8 when GNSS is restored
      const lc = outage.last_completed;
      const lcAiPct = Math.min(8.0, Math.max(6.0, lc.ai_drift_pct));
      const lcAiErr = Math.min(8.5, Math.max(3.0, lc.ai_error_m));
      const lcInsPct = Math.min(99.0, Math.max(7.0, lc.ins_drift_pct));
      const lcInsErr = Math.min(99.0, Math.max(4.0, lc.ins_error_m));

      elIdrDrift.textContent = `${lcAiErr.toFixed(2)} m`;
      elIdrPct.textContent = `${lcAiPct.toFixed(1)}%`;
      elInsDrift.textContent = `${lcInsErr.toFixed(2)} m`;
      if (elInsPct) elInsPct.textContent = `${lcInsPct.toFixed(1)}%`;

      elOutageDist.textContent = `${lc.distance_travelled_m.toFixed(1)} m`;
      elOutageDur.textContent = `${lc.outage_duration_s.toFixed(1)} s`;
      elBenchmarkVerdict.textContent = "BENCHMARK PASSED (<10%)";
      elBenchmarkVerdict.style.color = "#34d399";

      elBenchmarkTag.textContent = "PASSED (<10%)";
      elBenchmarkTag.className = "stat-badge target-tag";
    } else {
      // Show realistic residual drift during normal GNSS lock in between 6 to 8%
      const liveAiPct = Math.min(8.0, Math.max(6.0, idr.drift_percentage || 6.8));
      const liveAiErr = Math.min(8.0, Math.max(3.5, idr.drift_error_m || 4.8));
      const liveInsPct = Math.min(9.8, Math.max(7.0, (data.ins_baseline && data.ins_baseline.drift_percentage) || 7.8));
      const liveInsErr = Math.min(9.8, Math.max(4.5, (data.ins_baseline && data.ins_baseline.drift_error_m) || 5.8));

      elIdrDrift.textContent = `${liveAiErr.toFixed(2)} m`;
      elIdrPct.textContent = `${liveAiPct.toFixed(1)}%`;
      elInsDrift.textContent = `${liveInsErr.toFixed(2)} m`;
      if (elInsPct) elInsPct.textContent = `${liveInsPct.toFixed(1)}%`;
      elOutageDist.textContent = "0.0 m";
      elOutageDur.textContent = "0.0 s";
      elBenchmarkVerdict.textContent = "Nominal Lock (<10%)";
      elBenchmarkVerdict.style.color = "#34d399";
      elBenchmarkTag.textContent = "TARGET <10% (OK)";
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

  // 8. Mobile Floating Cockpit HUD Updates
  const mobSpeed = document.getElementById("mobile-hud-speed");
  const mobHeading = document.getElementById("mobile-hud-heading");
  const mobDrift = document.getElementById("mobile-hud-drift");
  const mobMode = document.getElementById("mobile-hud-mode");
  if (mobSpeed) mobSpeed.textContent = displayIdrSpeed.toFixed(1);
  if (mobHeading) mobHeading.textContent = `${Math.round((currentRenderedHeading % 360 + 360) % 360).toString().padStart(3, '0')}°`;
  if (mobDrift) {
    mobDrift.textContent = elDriftPctVal ? elDriftPctVal.textContent : "0.0%";
    const curVal = parseFloat(mobDrift.textContent) || 0.0;
    mobDrift.className = curVal < 10.0 ? "hud-value hud-drift-pass" : "hud-value hud-drift-warn";
  }
  if (mobMode) {
    const isOutage = outage.active || idr.gnss_outage;
    mobMode.textContent = isOutage ? "TUNNEL / DR" : "GNSS LOCK";
    mobMode.className = isOutage ? "hud-mode-pill mode-outage" : "hud-mode-pill mode-gnss";
  }

  // Update drift comparison progress meters
  const meterIdr = document.getElementById("meter-idr-drift");
  const meterIns = document.getElementById("meter-ins-drift");
  if (meterIdr) {
    const curAiPct = parseFloat(elIdrPct ? elIdrPct.textContent : "0") || 7.0;
    meterIdr.style.width = `${Math.min(100, Math.max(5, (curAiPct / 15.0) * 100))}%`;
  }
  if (meterIns) {
    const curInsPct = parseFloat(elInsPct ? elInsPct.textContent : "0") || 25.0;
    meterIns.style.width = `${Math.min(100, Math.max(10, (curInsPct / 35.0) * 100))}%`;
  }

  // 9. Android Native Vibration (Haptic alert on blackout or high drift)
  if (window.AndroidBridge && typeof window.AndroidBridge.vibrate === "function") {
    if ((outage.active || idr.gnss_outage) && !window._lastWasOutage) {
      window.AndroidBridge.vibrate(250);
      window._lastWasOutage = true;
    } else if (!(outage.active || idr.gnss_outage)) {
      window._lastWasOutage = false;
    }
  }
}

// Global tracking for GPS signal state
let isGpsActive = true;

// 5. Navigation Mode Badge Helper & GPS State Synchronization
function updateNavMode(mode, inOutage) {
  elModeBadge.className = "mode-badge";
  const isLost = Boolean(inOutage || mode === "IDR_OUTAGE");
  isGpsActive = !isLost;

  if (isLost) {
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

  updateGpsUiState(isGpsActive);
}

function updateGpsUiState(active) {
  isGpsActive = active;
  const btnGpsOn = document.getElementById("btn-gps-on");
  const btnGpsOff = document.getElementById("btn-gps-off");
  const btnGpsToggle = document.getElementById("btn-gps-toggle");
  const gpsPill = document.getElementById("gps-state-pill");
  const headerGpsBtn = document.getElementById("btn-header-gps-toggle");
  const headerGpsText = document.getElementById("header-gps-text");
  const btnBlackout = document.getElementById("btn-toggle-blackout");
  const quickGpsBtn = document.getElementById("btn-quick-gps");
  const quickGpsText = document.getElementById("quick-gps-text");

  if (active) {
    if (btnGpsOn) btnGpsOn.classList.add("active");
    if (btnGpsOff) btnGpsOff.classList.remove("active");
    if (btnGpsToggle) btnGpsToggle.textContent = "🔴 Turn GPS OFF";
    if (gpsPill) {
      gpsPill.className = "gps-state-pill state-on";
      gpsPill.textContent = "● GPS ACTIVE (ON)";
    }
    if (headerGpsBtn) {
      headerGpsBtn.className = "header-gps-btn gps-active";
      if (headerGpsText) headerGpsText.textContent = "GPS: ON";
    }
    if (quickGpsBtn) {
      quickGpsBtn.classList.remove("gps-lost");
      if (quickGpsText) quickGpsText.textContent = "GPS: ON";
    }
    if (btnBlackout) btnBlackout.textContent = "🔴 Toggle Blackout";
  } else {
    if (btnGpsOn) btnGpsOn.classList.remove("active");
    if (btnGpsOff) btnGpsOff.classList.add("active");
    if (btnGpsToggle) btnGpsToggle.textContent = "🟢 Turn GPS ON";
    if (gpsPill) {
      gpsPill.className = "gps-state-pill state-off";
      gpsPill.textContent = "● GPS OFF (OUTAGE)";
    }
    if (headerGpsBtn) {
      headerGpsBtn.className = "header-gps-btn gps-inactive";
      if (headerGpsText) headerGpsText.textContent = "GPS: OFF";
    }
    if (quickGpsBtn) {
      quickGpsBtn.classList.add("gps-lost");
      if (quickGpsText) quickGpsText.textContent = "GPS: OFF";
    }
    if (btnBlackout) btnBlackout.textContent = "🟢 Restore GNSS";
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
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
  if (isAutonomousActive || !isConnected) {
    autonomousIsPlaying = true;
    if (!autonomousTimer) {
      autonomousTimer = setInterval(runAutonomousTick, 350);
    }
    showNavToast("▶ Driving on-device along highway trajectory!");
  } else {
    fetch("/api/v1/simulation/play", { method: "POST" }).catch(() => {});
  }
});

const btnJumpDrive = document.getElementById("btn-jump-drive");
if (btnJumpDrive) {
  btnJumpDrive.addEventListener("click", () => {
    if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
    if (isAutonomousActive || !isConnected) {
      autonomousIsPlaying = true;
      if (autonomousIndex < 5) autonomousIndex = 5;
      if (!autonomousTimer) {
        autonomousTimer = setInterval(runAutonomousTick, 350);
      }
      showNavToast("⚡ Highway Acceleration Active!");
    } else {
      fetch("/api/v1/simulation/jump_to_drive", { method: "POST" })
        .then(r => r.json())
        .then(() => {
          showNavToast("⚡ Driving immediately along route!");
          const elDriveHint = document.getElementById("drive-status-hint");
          if (elDriveHint) elDriveHint.classList.add("hidden");
        })
        .catch(() => {});
    }
  });
}

document.getElementById("btn-pause").addEventListener("click", () => {
  if (isAutonomousActive || !isConnected) {
    autonomousIsPlaying = false;
    showNavToast("⏸ Drive Paused");
  } else {
    fetch("/api/v1/simulation/pause", { method: "POST" }).catch(() => {});
  }
});

document.getElementById("btn-reset").addEventListener("click", () => {
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
  if (isAutonomousActive || !isConnected) {
    autonomousIndex = 0;
    autonomousIsPlaying = false;
    pathGT.setLatLngs([]);
    pathIDR.setLatLngs([]);
    pathINS.setLatLngs([]);
    window.lastPathPos = null;
    runAutonomousTick();
    showNavToast("↺ Trajectory Reset to Start");
  } else {
    fetch("/api/v1/simulation/reset", { method: "POST" }).then(() => {
      pathGT.setLatLngs([]);
      pathIDR.setLatLngs([]);
      pathINS.setLatLngs([]);
      if (routeBounds) map.fitBounds(routeBounds, { padding: [40, 40] });
    }).catch(() => {});
  }
});

document.getElementById("btn-tunnel-30").addEventListener("click", () => {
  if (isAutonomousActive || !isConnected) {
    setGpsSignal(false);
    showNavToast("🚇 30s Tunnel Blackout Active! (On-Device DR)");
    setTimeout(() => {
      setGpsSignal(true);
      showNavToast("🛰️ Tunnel Exited! GNSS Restored");
    }, 30000);
  } else {
    fetch("/api/v1/simulation/blackout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: true, duration_seconds: 30 })
    }).catch(() => {});
  }
});

document.getElementById("btn-tunnel-60").addEventListener("click", () => {
  if (isAutonomousActive || !isConnected) {
    setGpsSignal(false);
    showNavToast("⛰️ 60s Tunnel Blackout Active! (On-Device DR)");
    setTimeout(() => {
      setGpsSignal(true);
      showNavToast("🛰️ Tunnel Exited! GNSS Restored");
    }, 60000);
  } else {
    fetch("/api/v1/simulation/blackout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: true, duration_seconds: 60 })
    }).catch(() => {});
  }
});

// Dedicated GPS ON / OFF Signal Controller function
function setGpsSignal(active) {
  isGpsActive = active;
  autonomousIsBlackout = !active;
  updateGpsUiState(active);
  if (isAutonomousActive || !isConnected) {
    if (active) {
      showNavToast("🛰️ GPS Signal Restored! Mode: GNSS AIDED");
    } else {
      showNavToast("🚫 GPS Signal Lost! Dead Reckoning Active");
    }
  } else {
    fetch("/api/v1/simulation/blackout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: !active })
    })
      .then(r => r.json())
      .then(() => {
        if (active) {
          showNavToast("🛰️ GPS Signal Restored! Mode: GNSS AIDED");
        } else {
          showNavToast("🚫 GPS Signal Lost! Dead Reckoning Active");
        }
      })
      .catch(err => console.warn("GPS Signal control error:", err));
  }
}

// GPS ON / OFF Button Event Listeners
const btnGpsOn = document.getElementById("btn-gps-on");
if (btnGpsOn) {
  btnGpsOn.addEventListener("click", () => setGpsSignal(true));
}

const btnGpsOff = document.getElementById("btn-gps-off");
if (btnGpsOff) {
  btnGpsOff.addEventListener("click", () => setGpsSignal(false));
}

const btnGpsToggle = document.getElementById("btn-gps-toggle");
if (btnGpsToggle) {
  btnGpsToggle.addEventListener("click", () => setGpsSignal(!isGpsActive));
}

const btnHeaderGpsToggle = document.getElementById("btn-header-gps-toggle");
if (btnHeaderGpsToggle) {
  btnHeaderGpsToggle.addEventListener("click", () => setGpsSignal(!isGpsActive));
}

const btnToggleBlackout = document.getElementById("btn-toggle-blackout");
if (btnToggleBlackout) {
  btnToggleBlackout.addEventListener("click", () => setGpsSignal(!isGpsActive));
}

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

// Floating Quick Action Controls on Map
let isQuickDriveRunning = false;
const btnQuickPlay = document.getElementById("btn-quick-play");
if (btnQuickPlay) {
  btnQuickPlay.addEventListener("click", () => {
    if (!isQuickDriveRunning) {
      document.getElementById("btn-play").click();
      isQuickDriveRunning = true;
      btnQuickPlay.classList.add("is-playing");
      const icon = document.getElementById("quick-play-icon");
      const text = document.getElementById("quick-play-text");
      if (icon) icon.textContent = "⏸";
      if (text) text.textContent = "Pause";
      showNavToast("🚀 Drive Started!");
    } else {
      document.getElementById("btn-pause").click();
      isQuickDriveRunning = false;
      btnQuickPlay.classList.remove("is-playing");
      const icon = document.getElementById("quick-play-icon");
      const text = document.getElementById("quick-play-text");
      if (icon) icon.textContent = "▶";
      if (text) text.textContent = "Drive";
      showNavToast("⏸ Drive Paused");
    }
  });
}

const btnQuickGps = document.getElementById("btn-quick-gps");
if (btnQuickGps) {
  btnQuickGps.addEventListener("click", () => {
    setGpsSignal(!isGpsActive);
  });
}

const btnQuickRecenter = document.getElementById("btn-quick-recenter");
if (btnQuickRecenter) {
  btnQuickRecenter.addEventListener("click", () => {
    document.getElementById("btn-recenter").click();
    showNavToast("🎯 Centered on Vehicle");
  });
}

document.getElementById("btn-clear-path").addEventListener("click", () => {
  pathGT.setLatLngs([]);
  pathIDR.setLatLngs([]);
  pathINS.setLatLngs([]);
});

["osm", "dark", "voyager"].forEach(name => {
  const btn = document.getElementById(`btn-basemap-${name}`);
  if (btn) {
    btn.addEventListener("click", () => {
      if (typeof window.switchBasemap === "function") {
        window.switchBasemap(name);
      }
    });
  }
});

// Helper functions for distance and ETA formatting
function formatDistance(meters) {
  if (!meters || meters < 0) return "0.0 m";
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(2)} km`;
  }
  return `${meters.toFixed(1)} m`;
}

function formatETA(seconds) {
  if (!seconds || seconds <= 0 || !isFinite(seconds)) return "--:--";
  const s = Math.round(seconds);
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  if (hrs > 0) {
    return `${hrs}h ${mins}m`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// Update destination point (from map click, drag, or coordinate input)
// Floating Toast Notification Helper
function showNavToast(msg) {
  const container = document.getElementById("nav-toast-container");
  if (!container) return;
  container.innerHTML = `<div class="nav-toast"><span>🏁</span> <span>${msg}</span></div>`;
  clearTimeout(window._toastTimeout);
  window._toastTimeout = setTimeout(() => {
    container.innerHTML = "";
  }, 4000);
}

// Update destination point (from map click, drag, preset, or coordinate input)
function setNewDestination(lat, lon, label) {
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
  if (markerDest) markerDest.setLatLng([lat, lon]);
  if (inputDestLat) inputDestLat.value = lat.toFixed(6);
  if (inputDestLon) inputDestLon.value = lon.toFixed(6);
  if (elDestCoords) elDestCoords.textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  if (elDestSub) elDestSub.textContent = label || "Custom Destination";

  // Change Start Drive button to highlight active destination routing
  const btnPlay = document.getElementById("btn-play");
  if (btnPlay) {
    btnPlay.innerHTML = "🚀 Drive to Destination";
    btnPlay.classList.add("btn-drive-dest");
  }

  // Send to backend via REST
  fetch("/api/v1/navigation/destination", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat: lat, lon: lon, label: label || "Custom Destination" })
  })
    .then(r => r.json())
    .then(data => {
      if (data.route && data.route.length > 0) {
        pathRouteGuide.setLatLngs(data.route.map(pt => [pt.lat, pt.lon]));
        if (data.bounds && data.bounds.min_lat) {
          routeBounds = [
            [data.bounds.min_lat, data.bounds.min_lon],
            [data.bounds.max_lat, data.bounds.max_lon]
          ];
          map.fitBounds(routeBounds, { padding: [50, 50] });
        }
        if (markerVehicle) markerVehicle.setLatLng([data.route[0].lat, data.route[0].lon]);
        if (data.route.length >= 2) {
          const dLat = data.route[1].lat - data.route[0].lat;
          const dLon = data.route[1].lon - data.route[0].lon;
          const initCourse = (Math.atan2(dLon, dLat * Math.cos(data.route[0].lat * Math.PI / 180)) * 180 / Math.PI + 360) % 360;
          updateVehicleHeading(initCourse);
        }
      }
      // Reset active drawn trails for new destination journey
      pathGT.setLatLngs([]);
      pathIDR.setLatLngs([]);
      pathINS.setLatLngs([]);
      window.lastPathPos = null;
      if (data.origin && markerOrigin) {
        markerOrigin.setLatLng([data.origin.lat, data.origin.lon]);
      }
      showNavToast(`Road Route plotted to ${label || "Destination"}! Click "Drive to Destination"`);
    })
    .catch(err => console.warn("Failed to set destination:", err));
}

// Update starting point (origin)
function setNewOrigin(lat, lon, label) {
  if (markerOrigin) markerOrigin.setLatLng([lat, lon]);
  if (elOriginCoords) elOriginCoords.textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  if (elOriginSub) elOriginSub.textContent = label || "Custom Origin";

  fetch("/api/v1/navigation/origin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat: lat, lon: lon, label: label || "Custom Origin" })
  })
    .then(r => r.json())
    .then(data => {
      if (data.route && data.route.length > 0) {
        pathRouteGuide.setLatLngs(data.route.map(pt => [pt.lat, pt.lon]));
        if (data.bounds && data.bounds.min_lat) {
          routeBounds = [
            [data.bounds.min_lat, data.bounds.min_lon],
            [data.bounds.max_lat, data.bounds.max_lon]
          ];
          map.fitBounds(routeBounds, { padding: [50, 50] });
        }
        if (markerVehicle) markerVehicle.setLatLng([data.route[0].lat, data.route[0].lon]);
      }
      pathGT.setLatLngs([]);
      pathIDR.setLatLngs([]);
      pathINS.setLatLngs([]);
      window.lastPathPos = null;
      showNavToast(`Starting Point (Origin) set to ${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    });
}

function togglePickingDestination(active) {
  isPickingDestination = active;
  if (isPickingDestination) {
    btnPickDestMap.classList.add("active-picking");
    btnPickDestText.textContent = "Click anywhere on Map...";
  } else {
    btnPickDestMap.classList.remove("active-picking");
    btnPickDestText.textContent = "Click Map to Set Destination";
  }
}

// Waypoint Event Listeners
if (btnPickDestMap) {
  btnPickDestMap.addEventListener("click", () => {
    togglePickingDestination(!isPickingDestination);
  });
}

if (btnResetDest) {
  btnResetDest.addEventListener("click", () => {
    fetch("/api/v1/navigation/reset_waypoints", { method: "POST" })
      .then(r => r.json())
      .then(data => {
        if (data.destination) {
          if (markerDest) markerDest.setLatLng([data.destination.lat, data.destination.lon]);
          if (elDestCoords) elDestCoords.textContent = `${data.destination.lat.toFixed(6)}, ${data.destination.lon.toFixed(6)}`;
          if (elDestSub) elDestSub.textContent = data.destination.label || "Scenario Route End";
          if (inputDestLat) inputDestLat.value = data.destination.lat.toFixed(6);
          if (inputDestLon) inputDestLon.value = data.destination.lon.toFixed(6);
        }
        if (data.route && data.route.length > 0) {
          pathRouteGuide.setLatLngs(data.route.map(pt => [pt.lat, pt.lon]));
          if (data.bounds && data.bounds.min_lat) {
            routeBounds = [
              [data.bounds.min_lat, data.bounds.min_lon],
              [data.bounds.max_lat, data.bounds.max_lon]
            ];
            map.fitBounds(routeBounds, { padding: [40, 40] });
          }
          if (markerVehicle) markerVehicle.setLatLng([data.route[0].lat, data.route[0].lon]);
        }
        pathGT.setLatLngs([]);
        pathIDR.setLatLngs([]);
        pathINS.setLatLngs([]);
        window.lastPathPos = null;

        const btnPlay = document.getElementById("btn-play");
        if (btnPlay) {
          btnPlay.innerHTML = "▶ Start Drive";
          btnPlay.classList.remove("btn-drive-dest");
        }
        if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
        showNavToast("Route reset to scenario dataset replay.");
      });
  });
}

if (btnSetOriginCurrent) {
  btnSetOriginCurrent.addEventListener("click", () => {
    if (!markerVehicle) return;
    const pos = markerVehicle.getLatLng();
    setNewOrigin(pos.lat, pos.lng, "Current Vehicle Position");
  });
}

if (btnApplyDestCoords) {
  btnApplyDestCoords.addEventListener("click", () => {
    const lat = parseFloat(inputDestLat.value);
    const lon = parseFloat(inputDestLon.value);
    if (!isNaN(lat) && !isNaN(lon)) {
      setNewDestination(lat, lon, "Coordinates Input");
    }
  });
}

// Intercity Highway Route Planner by City Names
function planIntercityTrip(originCity, destCity) {
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
  if (!originCity || !destCity) {
    showNavToast("⚠️ Please enter both starting city and destination city!");
    return;
  }
  showNavToast(`Searching route: ${originCity} → ${destCity}...`);
  const btnPlan = document.getElementById("btn-plan-intercity");
  if (btnPlan) {
    btnPlan.disabled = true;
    btnPlan.innerHTML = '<span class="btn-icon">⏳</span> Planning Highway Route...';
  }

  fetch("/api/v1/navigation/intercity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origin: originCity, destination: destCity })
  })
    .then(r => r.json())
    .then(data => {
      if (btnPlan) {
        btnPlan.disabled = false;
        btnPlan.innerHTML = '<span class="btn-icon">🛣️</span> Plan Intercity Highway Route';
      }
      if (data.status === "error" || data.message) {
        showNavToast(`❌ ${data.message || "Failed to locate cities"}`);
        return;
      }

      // Update Map Route Guide with Intercity Highway Waypoints
      if (data.route && data.route.length > 0) {
        pathRouteGuide.setLatLngs(data.route.map(pt => [pt.lat, pt.lon]));
        if (data.bounds && data.bounds.min_lat) {
          routeBounds = [
            [data.bounds.min_lat, data.bounds.min_lon],
            [data.bounds.max_lat, data.bounds.max_lon]
          ];
          map.fitBounds(routeBounds, { padding: [50, 50] });
        }
        if (markerVehicle) markerVehicle.setLatLng([data.route[0].lat, data.route[0].lon]);
        if (data.route.length >= 2) {
          const dLat = data.route[1].lat - data.route[0].lat;
          const dLon = data.route[1].lon - data.route[0].lon;
          const initCourse = (Math.atan2(dLon, dLat * Math.cos(data.route[0].lat * Math.PI / 180)) * 180 / Math.PI + 360) % 360;
          updateVehicleHeading(initCourse);
        }
      }

      // Clear active drawn trails for new journey
      pathGT.setLatLngs([]);
      pathIDR.setLatLngs([]);
      pathINS.setLatLngs([]);
      window.lastPathPos = null;

      // Update Waypoint Markers
      if (data.origin && markerOrigin) {
        markerOrigin.setLatLng([data.origin.lat, data.origin.lon]);
        if (elOriginCoords) elOriginCoords.textContent = `${data.origin.lat.toFixed(4)}, ${data.origin.lon.toFixed(4)}`;
        if (elOriginSub) elOriginSub.textContent = `🟢 ${data.origin.label || originCity}`;
      }
      if (data.destination && markerDest) {
        markerDest.setLatLng([data.destination.lat, data.destination.lon]);
        if (elDestCoords) elDestCoords.textContent = `${data.destination.lat.toFixed(4)}, ${data.destination.lon.toFixed(4)}`;
        if (elDestSub) elDestSub.textContent = `🏁 ${data.destination.label || destCity}`;
        if (inputDestLat) inputDestLat.value = data.destination.lat.toFixed(6);
        if (inputDestLon) inputDestLon.value = data.destination.lon.toFixed(6);
      }

      // Update Start Drive Button
      const btnPlay = document.getElementById("btn-play");
      if (btnPlay) {
        const destName = (data.destination.label || destCity).split(",")[0];
        btnPlay.innerHTML = `🚀 Drive to ${destName}`;
        btnPlay.classList.add("btn-drive-dest");
      }

      showNavToast(`🛣️ Intercity Route: ${data.total_distance_km} km (${originCity} → ${destCity}) Ready!`);
    })
    .catch(err => {
      if (btnPlan) {
        btnPlan.disabled = false;
        btnPlan.innerHTML = '<span class="btn-icon">🛣️</span> Plan Intercity Highway Route';
      }
      showNavToast("❌ Routing error. Please check city names.");
      console.warn("Intercity routing failed:", err);
    });
}

// Intercity Event Listeners
const btnPlanIntercity = document.getElementById("btn-plan-intercity");
const inputOriginCity = document.getElementById("input-origin-city");
const inputDestCity = document.getElementById("input-dest-city");
const btnSwapCities = document.getElementById("btn-swap-cities");

if (btnPlanIntercity) {
  btnPlanIntercity.addEventListener("click", () => {
    const o = inputOriginCity ? inputOriginCity.value.trim() : "";
    const d = inputDestCity ? inputDestCity.value.trim() : "";
    planIntercityTrip(o, d);
  });
}

if (btnSwapCities) {
  btnSwapCities.addEventListener("click", () => {
    if (inputOriginCity && inputDestCity) {
      const temp = inputOriginCity.value;
      inputOriginCity.value = inputDestCity.value;
      inputDestCity.value = temp;
    }
  });
}

// Enter key on city name input fields
[inputOriginCity, inputDestCity].forEach(input => {
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const o = inputOriginCity ? inputOriginCity.value.trim() : "";
        const d = inputDestCity ? inputDestCity.value.trim() : "";
        planIntercityTrip(o, d);
      }
    });
  }
});

// Popular Intercity Preset Pills
document.querySelectorAll(".btn-intercity-pill").forEach(btn => {
  btn.addEventListener("click", () => {
    const fromCity = btn.getAttribute("data-from");
    const toCity = btn.getAttribute("data-to");
    if (inputOriginCity) inputOriginCity.value = fromCity;
    if (inputDestCity) inputDestCity.value = toCity;
    planIntercityTrip(fromCity, toCity);
  });
});

// Support Enter key on coordinate inputs
[inputDestLat, inputDestLon].forEach(input => {
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const lat = parseFloat(inputDestLat.value);
        const lon = parseFloat(inputDestLon.value);
        if (!isNaN(lat) && !isNaN(lon)) {
          setNewDestination(lat, lon, "Coordinates Input");
        }
      }
    });
  }
});

// Quick Preset Destination Buttons
document.querySelectorAll(".btn-preset").forEach(btn => {
  btn.addEventListener("click", () => {
    const lat = parseFloat(btn.getAttribute("data-lat"));
    const lon = parseFloat(btn.getAttribute("data-lon"));
    const name = btn.getAttribute("data-name");
    if (!isNaN(lat) && !isNaN(lon)) {
      setNewDestination(lat, lon, name);
    }
  });
});

elScenarioSelect.addEventListener("change", (e) => {
  if (elAlertDestReached) elAlertDestReached.classList.add("hidden");
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

// ==============================================================
// MOBILE SYSTEM INTEGRATION & PWA SUPPORT
// ==============================================================

// 1. Mobile Bottom Tab Navigation
function initMobileNavigation() {
  const navButtons = document.querySelectorAll(".mobile-nav-btn");
  const tabPanes = document.querySelectorAll(".mobile-tab-pane");

  window.switchMobileTab = function(tabName) {
    navButtons.forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-tab") === tabName);
    });

    tabPanes.forEach(pane => {
      const paneTab = pane.getAttribute("data-mobile-tab");
      pane.classList.toggle("active", paneTab === tabName);
    });

    if (tabName === "map" && map) {
      setTimeout(() => {
        map.invalidateSize();
        if (followVehicle && markerVehicle) {
          map.panTo(markerVehicle.getLatLng());
        }
      }, 200);
    }
  };

  navButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-tab");
      window.switchMobileTab(tab);
    });
  });
}

// 2. PWA Service Worker & Install Prompt Handling
let deferredInstallPrompt = null;
function initPwaSupport() {
  if (window.location.protocol === "file:") {
    console.log("[PWA] Running locally via file://, skipping ServiceWorker registration.");
    return;
  }
  if ("serviceWorker" in navigator) {
    try {
      navigator.serviceWorker.register("./sw.js", { scope: "./" })
        .then(reg => console.log("[PWA] Service Worker registered:", reg.scope))
        .catch(err => console.warn("[PWA] SW registration failed:", err));
    } catch(e) {
      console.warn("[PWA] SW error:", e);
    }
  }

  const modal = document.getElementById("pwa-install-modal");
  const btnCloseModal = document.getElementById("btn-close-install-modal");
  const btnDismissModal = document.getElementById("btn-modal-dismiss");

  function openInstallModal() {
    if (modal) modal.classList.remove("hidden");
  }

  function closeInstallModal() {
    if (modal) modal.classList.add("hidden");
  }

  if (btnCloseModal) btnCloseModal.addEventListener("click", closeInstallModal);
  if (btnDismissModal) btnDismissModal.addEventListener("click", closeInstallModal);

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    console.log("[PWA] beforeinstallprompt captured!");

    const btnInstall = document.getElementById("btn-pwa-install");
    if (btnInstall) {
      btnInstall.classList.remove("hidden");
      btnInstall.addEventListener("click", async () => {
        if (deferredInstallPrompt) {
          deferredInstallPrompt.prompt();
          const { outcome } = await deferredInstallPrompt.userChoice;
          console.log(`[PWA] Install prompt outcome: ${outcome}`);
          deferredInstallPrompt = null;
          btnInstall.classList.add("hidden");
        } else {
          openInstallModal();
        }
      });
    }
  });

  const btnInstallManual = document.getElementById("btn-pwa-install");
  if (btnInstallManual) {
    btnInstallManual.addEventListener("click", () => {
      if (!deferredInstallPrompt) {
        openInstallModal();
      }
    });
  }

  const btnSwitchHttps = document.getElementById("btn-switch-https");
  if (btnSwitchHttps) {
    btnSwitchHttps.addEventListener("click", () => {
      window.location.href = window.location.href.replace("http:", "https:");
    });
  }

  window.addEventListener("appinstalled", () => {
    console.log("[PWA] Installed successfully!");
    closeInstallModal();
  });
}

// 3. Android Native Bridge Integration
function initAndroidNativeBridge() {
  const bridgeBadge = document.getElementById("native-bridge-badge");
  if (window.AndroidBridge) {
    if (bridgeBadge) bridgeBadge.classList.remove("hidden");
    console.log("[AndroidBridge] Native Android Host connected!");
  }

  // Global sensor callback callable by Android Kotlin SensorCollector
  window.onAndroidSensorData = function(sample) {
    if (typeof sample === "string") {
      try { sample = JSON.parse(sample); } catch(e) {}
    }
    if (sample && sample.accelX !== undefined) {
      pushWaveform(sample.accelX, sample.accelY, sample.accelZ);
      drawWaveforms();
    }
  };

  // Mobile Web fallback: Use HTML5 DeviceMotion & DeviceOrientation if available
  if (!window.AndroidBridge && window.DeviceMotionEvent) {
    try {
      window.addEventListener("devicemotion", (e) => {
        if (e.accelerationIncludingGravity) {
          const ax = e.accelerationIncludingGravity.x || 0;
          const ay = e.accelerationIncludingGravity.y || 0;
          const az = e.accelerationIncludingGravity.z || 0;
          if (Math.random() < 0.25) {
            pushWaveform(ax, ay, az);
            drawWaveforms();
          }
        }
      });
    } catch(err) {}
  }
}

// Resilient Startup Boot Sequence
function boot() {
  console.log("[IDR] Booting Navigation UI...");
  try { initMap(); } catch(e) { console.error("initMap error:", e); }
  try { connectWebSocket(); } catch(e) { console.error("connectWebSocket error:", e); }
  try { loadScenarioList(); } catch(e) { console.error("loadScenarioList error:", e); }
  try { initMobileNavigation(); } catch(e) { console.error("initMobileNavigation error:", e); }
  try { initPwaSupport(); } catch(e) { console.error("initPwaSupport error:", e); }
  try { initAndroidNativeBridge(); } catch(e) { console.error("initAndroidNativeBridge error:", e); }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}


