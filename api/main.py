"""
IDR Navigation System — FastAPI Backend & Live Telemetry Server
==============================================================
SIH 2026 Interactive Navigation Command Server:
 - Real-time scenario playback from actual IO-VNBD dataset
 - Dual-stream position comparison:
     1. Ground Truth GPS reference
     2. Conventional INS baseline (drifting)
     3. AI-Powered IDR (constrained < 10% drift)
 - Interactive GNSS blackout triggers ("Simulate Tunnel", "Simulate Urban Canyon")
 - WebSocket telemetry stream for live Leaflet web dashboard
"""

import os
import sys
import json
import time
import math
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

import pandas as pd
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from idr_engine import IDREngine, SensorPacket, NavigationResult
from navigation.ins import StrapdownINS, geodetic_to_enu

RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"
STATIC_DIR   = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Intelligent Dead Reckoning Navigation API",
    version="1.0.0",
    description="SIH 2026 Smartphone Dead Reckoning Engine with AI Drift Compensation"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_col(row: Any, *patterns: str, default: float = 0.0) -> float:
    """Robust case-insensitive and encoding-resilient column value extractor."""
    for p in patterns:
        p_lower = p.lower()
        for k in row.keys():
            if p_lower in str(k).lower():
                val = row[k]
                try:
                    if pd.notna(val):
                        return float(val)
                except (ValueError, TypeError):
                    pass
    return default


# Global Simulation & Engine State
class SimulationManager:
    def __init__(self):
        self.engine = IDREngine()
        self.ins_baseline: Optional[StrapdownINS] = None
        self.is_running = False
        self.current_scenario = "S-A1.csv"
        self.df_data: Optional[pd.DataFrame] = None
        self.current_step = 0
        self.playback_speed = 1.0
        self.active_websockets: List[WebSocket] = []

        # Route preview & bounds
        self.route_preview: List[Dict[str, float]] = []
        self.route_bounds: Dict[str, float] = {}

        # Outage simulation state (tracked by DATASET TIME in milliseconds)
        self.forced_blackout = False
        self.was_blackout = False
        self.blackout_end_ts_ms = -1.0
        self.blackout_start_ts_ms = -1.0
        self.blackout_start_dist_m = 0.0
        self.blackout_active_duration_s = 0.0
        self.blackout_distance_travelled_m = 0.0
        self.current_ai_drift_m = 0.0
        self.current_ins_drift_m = 0.0
        self.last_completed_outage: Optional[Dict[str, Any]] = None

        # Heading & orientation tracking
        self.mounting_yaw_offset = 0.0
        self.last_valid_heading = 0.0

        # Ins baseline tracking
        self.ins_baseline_lat = 0.0
        self.ins_baseline_lon = 0.0

    def load_scenario(self, filename: str) -> bool:
        csv_matches = list(RAW_DATA_DIR.rglob(filename))
        if not csv_matches:
            # Fallback search by stem or any available CSV
            all_csvs = list(RAW_DATA_DIR.rglob("*.csv"))
            if not all_csvs:
                return False
            stem_matches = [f for f in all_csvs if f.stem.lower() == filename.lower().replace(".csv", "")]
            csv_matches = stem_matches if stem_matches else [all_csvs[0]]

        path = csv_matches[0]
        self.current_scenario = path.name
        print(f"[INFO] Loading dataset scenario: {path}")
        df = pd.read_csv(path, encoding="latin1")
        df.columns = [str(c).strip() for c in df.columns]
        self.df_data = df
        self.current_step = 0

        # Extract full route preview
        lat_cols = [c for c in df.columns if 'lat' in c.lower()]
        lon_cols = [c for c in df.columns if 'lon' in c.lower()]
        if lat_cols and lon_cols:
            lat_s = df[lat_cols[0]]
            lon_s = df[lon_cols[0]]
            self.route_bounds = {
                "min_lat": float(lat_s.min()), "max_lat": float(lat_s.max()),
                "min_lon": float(lon_s.min()), "max_lon": float(lon_s.max()),
            }
            stride = max(1, len(df) // 600)
            self.route_preview = [
                {"lat": float(lat_s.iloc[i]), "lon": float(lon_s.iloc[i])}
                for i in range(0, len(df), stride)
            ]
        else:
            self.route_preview = []
            self.route_bounds = {}

        self.reset_engines()
        return True

    def reset_engines(self):
        self.current_step = 0
        self.forced_blackout = False
        self.was_blackout = False
        self.blackout_end_ts_ms = -1.0
        self.blackout_start_ts_ms = -1.0
        self.blackout_start_dist_m = 0.0
        self.blackout_active_duration_s = 0.0
        self.blackout_distance_travelled_m = 0.0
        self.current_ai_drift_m = 0.0
        self.current_ins_drift_m = 0.0
        self.last_completed_outage = None
        self.mounting_yaw_offset = 0.0

        self.engine = IDREngine()

        if self.df_data is not None and len(self.df_data) > 0:
            row0 = self.df_data.iloc[0]
            lat = get_col(row0, "gps latitude", default=52.43163)
            lon = get_col(row0, "gps longitude", default=-1.525745)
            alt = get_col(row0, "gps altitude", default=100.0)
            speed = get_col(row0, "gps speed", default=0.0) / 3.6
            course = get_col(row0, "gps orientation", default=0.0)
            self.last_valid_heading = course

            self.engine.initialize(lat, lon, alt, course, speed)
            self.ins_baseline = StrapdownINS(lat, lon, alt)
            self.ins_baseline.initialize_state(lat, lon, alt, course, speed)
            self.ins_baseline_lat = lat
            self.ins_baseline_lon = lon

    def trigger_blackout(self, duration_seconds: Optional[int] = None, active: bool = True):
        """Trigger or toggle blackout with exact duration tracked by DATASET TIME."""
        if not active:
            if self.forced_blackout:
                self._finalize_outage_benchmark()
            self.forced_blackout = False
            self.blackout_end_ts_ms = -1.0
            return

        self.forced_blackout = True
        current_ts_ms = 0.0
        if self.df_data is not None and self.current_step < len(self.df_data):
            current_ts_ms = get_col(self.df_data.iloc[self.current_step], "time since start", "time", default=0.0)

        if duration_seconds is not None and duration_seconds > 0:
            self.blackout_end_ts_ms = current_ts_ms + (duration_seconds * 1000.0)
        else:
            self.blackout_end_ts_ms = -1.0

    def _finalize_outage_benchmark(self):
        """Record final frozen benchmark stats for the completed outage."""
        ai_pct = (100.0 * self.current_ai_drift_m / self.blackout_distance_travelled_m) if self.blackout_distance_travelled_m > 5.0 else 0.0
        ins_pct = (100.0 * self.current_ins_drift_m / self.blackout_distance_travelled_m) if self.blackout_distance_travelled_m > 5.0 else 0.0
        self.last_completed_outage = {
            "outage_duration_s": round(self.blackout_active_duration_s, 1),
            "distance_travelled_m": round(self.blackout_distance_travelled_m, 1),
            "ai_error_m": round(self.current_ai_drift_m, 2),
            "ai_drift_pct": round(ai_pct, 2),
            "ins_error_m": round(self.current_ins_drift_m, 2),
            "ins_drift_pct": round(ins_pct, 2),
            "target_pass": ai_pct < 10.0
        }

    def step(self) -> Optional[Dict[str, Any]]:
        if self.df_data is None or self.current_step >= len(self.df_data):
            return None

        row = self.df_data.iloc[self.current_step]
        self.current_step += 1

        # Extract values via robust get_col helper
        ts_ms = get_col(row, "time since start", "time", default=self.current_step * 500.0)
        ax = get_col(row, "accelerometer x", default=0.0)
        ay = get_col(row, "accelerometer y", default=0.0)
        az = get_col(row, "accelerometer z", default=9.80665)
        gx = get_col(row, "gyroscope yaw", default=0.0)
        gy = get_col(row, "gyroscope pitch", default=0.0)
        gz = get_col(row, "gyroscope roll", default=0.0)
        phone_yaw = get_col(row, "orientation (yaw)", default=0.0)
        pitch = get_col(row, "orientation (pitch)", default=0.0)
        roll = get_col(row, "orientation (roll", default=0.0)

        gt_lat = get_col(row, "gps latitude", default=self.engine.ref_lat or 52.43163)
        gt_lon = get_col(row, "gps longitude", default=self.engine.ref_lon or -1.525745)
        gt_speed = get_col(row, "gps speed", default=0.0)
        gt_bearing = get_col(row, "gps orientation", default=0.0)

        # Check timed blackout against DATASET TIME (ms)
        if self.blackout_end_ts_ms > 0 and ts_ms >= self.blackout_end_ts_ms:
            self._finalize_outage_benchmark()
            self.forced_blackout = False
            self.blackout_end_ts_ms = -1.0

        # Verified Heading Provenance (valid when moving > 0.4 m/s = 1.4 km/h)
        v_mps = gt_speed / 3.6
        if not self.forced_blackout and v_mps > 0.4:
            # During GNSS aiding, align phone yaw to vehicle motion course
            self.mounting_yaw_offset = (gt_bearing - phone_yaw) % 360.0
            current_heading = gt_bearing
            self.last_valid_heading = current_heading
        elif self.forced_blackout:
            # During blackout, propagate vehicle heading using smartphone gyro/orientation + mounting offset
            current_heading = (phone_yaw + self.mounting_yaw_offset) % 360.0
            self.last_valid_heading = current_heading
        else:
            current_heading = self.last_valid_heading

        # Outage transition detection
        if self.forced_blackout and not self.was_blackout:
            # Outage just started
            self.blackout_start_ts_ms = ts_ms
            self.blackout_start_dist_m = self.engine.total_distance_m
            self.blackout_distance_travelled_m = 0.0
            self.blackout_active_duration_s = 0.0

            # Phase 5 Requirement: Dead Reckoning must start exactly from last valid GNSS state before outage
            if self.engine.ekf is not None:
                self.engine.ekf.ins.pos_enu = geodetic_to_enu(
                    gt_lat, gt_lon, 0.0, self.engine.ref_lat, self.engine.ref_lon, self.engine.ref_alt
                )
            if self.ins_baseline is not None:
                self.ins_baseline_lat = gt_lat
                self.ins_baseline_lon = gt_lon
                self.ins_baseline.initialize_state(gt_lat, gt_lon, 100.0, current_heading, v_mps)
        elif self.forced_blackout and self.was_blackout:
            self.blackout_active_duration_s = max(0.0, (ts_ms - self.blackout_start_ts_ms) / 1000.0)
            self.blackout_distance_travelled_m = max(0.0, self.engine.total_distance_m - self.blackout_start_dist_m)
        self.was_blackout = self.forced_blackout

        self.engine.trigger_simulated_blackout(self.forced_blackout)

        # Build packet: If in blackout, GNSS is STRICTLY None (not fed to filter)
        feed_gnss_lat = None if self.forced_blackout else gt_lat
        feed_gnss_lon = None if self.forced_blackout else gt_lon
        feed_gnss_spd = None if self.forced_blackout else gt_speed
        feed_gnss_brg = None if self.forced_blackout else gt_bearing

        packet = SensorPacket(
            timestamp_ms=ts_ms,
            accel=[ax, ay, az],
            gyro=[gx, gy, gz],
            orientation=[current_heading, pitch, roll],
            gnss_lat=feed_gnss_lat,
            gnss_lon=feed_gnss_lon,
            gnss_speed_kmh=feed_gnss_spd,
            gnss_bearing_deg=feed_gnss_brg
        )

        # 1. Run IDR Engine (fusing IMU, AI Speed, NHC, ZUPT)
        idr_res = self.engine.process_sensor_packet(packet)

        # 2. Run Conventional INS Baseline
        dt = 0.5
        if self.ins_baseline is not None:
            ins_out = self.ins_baseline.step(np.array([ax, ay, az]), dt)
            if self.forced_blackout:
                self.ins_baseline_lat = ins_out["lat"]
                self.ins_baseline_lon = ins_out["lon"]
            else:
                self.ins_baseline_lat = gt_lat
                self.ins_baseline_lon = gt_lon
                self.ins_baseline.initialize_state(gt_lat, gt_lon, 100.0, current_heading, v_mps)

        # 3. Dynamic Drift Calculation against Ground Truth
        if self.forced_blackout:
            enu_gt = geodetic_to_enu(gt_lat, gt_lon, 0.0, self.engine.ref_lat, self.engine.ref_lon, 0.0)
            enu_idr = geodetic_to_enu(idr_res.lat, idr_res.lon, 0.0, self.engine.ref_lat, self.engine.ref_lon, 0.0)
            enu_ins = geodetic_to_enu(self.ins_baseline_lat, self.ins_baseline_lon, 0.0, self.engine.ref_lat, self.engine.ref_lon, 0.0)
            self.current_ai_drift_m = float(math.sqrt((enu_gt[0] - enu_idr[0])**2 + (enu_gt[1] - enu_idr[1])**2))
            self.current_ins_drift_m = float(math.sqrt((enu_gt[0] - enu_ins[0])**2 + (enu_gt[1] - enu_ins[1])**2))
        else:
            self.current_ai_drift_m = 0.0
            self.current_ins_drift_m = 0.0

        ai_drift_pct = (100.0 * self.current_ai_drift_m / self.blackout_distance_travelled_m) if self.blackout_distance_travelled_m > 5.0 else 0.0
        ins_drift_pct = (100.0 * self.current_ins_drift_m / self.blackout_distance_travelled_m) if self.blackout_distance_travelled_m > 5.0 else 0.0

        return {
            "step": self.current_step,
            "total_steps": len(self.df_data),
            "timestamp_ms": ts_ms,
            "gt": {
                "lat": gt_lat,
                "lon": gt_lon,
                "speed_kmh": round(gt_speed, 1),
                "bearing_deg": round(gt_bearing, 1)
            },
            "idr": {
                "lat": idr_res.lat,
                "lon": idr_res.lon,
                "speed_kmh": round(idr_res.speed_kmh, 1),
                "heading_deg": round(current_heading, 1),
                "nav_mode": "IDR_OUTAGE" if self.forced_blackout else idr_res.nav_mode,
                "gnss_outage": self.forced_blackout,
                "drift_error_m": round(self.current_ai_drift_m, 2),
                "drift_percentage": round(ai_drift_pct, 2),
                "confidence_score": round(idr_res.confidence_score, 2),
                "outage_duration_s": round(self.blackout_active_duration_s, 1),
                "outage_distance_m": round(self.blackout_distance_travelled_m, 1),
                "total_dist_m": round(self.engine.total_distance_m, 1)
            },
            "ins_baseline": {
                "lat": self.ins_baseline_lat,
                "lon": self.ins_baseline_lon,
                "drift_error_m": round(self.current_ins_drift_m, 2),
                "drift_percentage": round(ins_drift_pct, 2)
            },
            "outage": {
                "active": self.forced_blackout,
                "duration_s": round(self.blackout_active_duration_s, 1),
                "distance_m": round(self.blackout_distance_travelled_m, 1),
                "ai_drift_m": round(self.current_ai_drift_m, 2),
                "ai_drift_pct": round(ai_drift_pct, 2),
                "ins_drift_m": round(self.current_ins_drift_m, 2),
                "ins_drift_pct": round(ins_drift_pct, 2),
                "target_pass": ai_drift_pct < 10.0,
                "last_completed": self.last_completed_outage
            },
            "telemetry": {
                "acc_x": round(ax, 3), "acc_y": round(ay, 3), "acc_z": round(az, 3),
                "gyro_yaw": round(gx, 4), "gyro_pitch": round(gy, 4), "gyro_roll": round(gz, 4)
            }
        }


sim = SimulationManager()


# Background playback task
async def simulation_loop():
    while True:
        if sim.is_running and sim.df_data is not None:
            data = sim.step()
            if data is not None and sim.active_websockets:
                msg = json.dumps(data)
                for ws in list(sim.active_websockets):
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        sim.active_websockets.remove(ws)
            elif data is None:
                sim.is_running = False
        await asyncio.sleep(0.1 / sim.playback_speed)


@app.on_event("startup")
async def startup_event():
    # Load initial scenario
    sim.load_scenario("S-A1.csv")
    asyncio.create_task(simulation_loop())


# REST Endpoints
@app.get("/api/v1/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Intelligent Dead Reckoning Navigation API",
        "scenario": sim.current_scenario,
        "is_running": sim.is_running
    }


@app.get("/api/v1/scenarios")
def get_scenarios():
    csv_files = list(RAW_DATA_DIR.rglob("*.csv"))
    scenarios = [
        {"name": f.name, "path": str(f.relative_to(RAW_DATA_DIR)), "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(csv_files, key=lambda x: x.name)
    ]
    return {"scenarios": scenarios, "current": sim.current_scenario}


@app.get("/api/v1/scenarios/route")
def get_scenario_route(name: Optional[str] = None):
    if name and name != sim.current_scenario:
        sim.load_scenario(name)
    return {
        "scenario": sim.current_scenario,
        "total_points": len(sim.df_data) if sim.df_data is not None else 0,
        "route": sim.route_preview,
        "bounds": sim.route_bounds
    }


class LoadScenarioRequest(BaseModel):
    scenario_name: str

@app.post("/api/v1/scenarios/load")
def load_scenario_api(req: LoadScenarioRequest):
    ok = sim.load_scenario(req.scenario_name)
    return {
        "status": "ok" if ok else "not_found",
        "scenario": sim.current_scenario,
        "total_points": len(sim.df_data) if sim.df_data is not None else 0,
        "route": sim.route_preview,
        "bounds": sim.route_bounds
    }


@app.post("/api/v1/simulation/play")
def play_simulation():
    sim.is_running = True
    return {"status": "running"}


@app.post("/api/v1/simulation/pause")
def pause_simulation():
    sim.is_running = False
    return {"status": "paused"}


@app.post("/api/v1/simulation/reset")
def reset_simulation():
    sim.is_running = False
    sim.reset_engines()
    return {"status": "reset", "step": 0}


class BlackoutRequest(BaseModel):
    active: bool
    duration_seconds: Optional[int] = None

@app.post("/api/v1/simulation/blackout")
def set_blackout(req: BlackoutRequest):
    sim.trigger_blackout(duration_seconds=req.duration_seconds, active=req.active)
    return {
        "blackout_active": sim.forced_blackout,
        "duration_seconds": req.duration_seconds,
        "end_ts_ms": sim.blackout_end_ts_ms
    }


@app.get("/api/v1/status")
def get_status():
    return {
        "scenario": sim.current_scenario,
        "is_running": sim.is_running,
        "current_step": sim.current_step,
        "total_steps": len(sim.df_data) if sim.df_data is not None else 0,
        "forced_blackout": sim.forced_blackout
    }


# WebSocket stream for UI
@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    sim.active_websockets.append(websocket)
    try:
        while True:
            # Handle client commands if any
            data = await websocket.receive_text()
            cmd = json.loads(data)
            action = cmd.get("action")
            if action == "play":
                sim.is_running = True
            elif action == "pause":
                sim.is_running = False
            elif action == "reset":
                sim.reset_engines()
            elif action == "blackout":
                sim.forced_blackout = cmd.get("active", True)
                dur = cmd.get("duration", 0)
                if dur > 0:
                    sim.blackout_end_step = sim.current_step + dur * 2
                else:
                    sim.blackout_end_step = -1
    except WebSocketDisconnect:
        if websocket in sim.active_websockets:
            sim.active_websockets.remove(websocket)


# Mount static frontend
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")
