"""
Intelligent Dead Reckoning (IDR) Core Navigation Engine
======================================================
SIH 2026 Core Positioning Architecture.
Unifies:
 - Strapdown INS Mechanization
 - 15-State Error-State Extended Kalman Filter (ES-EKF)
 - AI Kinematic Speed Estimation
 - Non-Holonomic Constraints (NHC)
 - Zero-Velocity Update (ZUPT)
 - Autonomous GNSS Outage Detection & Smooth Recovery

Target: Position drift < 10% of distance traveled during complete GNSS blackouts.
"""

import math
import time
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict

from navigation.ins import StrapdownINS, geodetic_to_enu, enu_to_geodetic
from navigation.ekf import ErrorStateEKF
from navigation.nhc import NonHolonomicConstraints
from ai.speed_estimator import AISpeedEstimator
from ai.correction_model import DriftCorrectionModel


@dataclass
class SensorPacket:
    """Standardized multi-sensor input packet."""
    timestamp_ms: float
    accel: List[float]               # [ax, ay, az] in m/s^2
    gyro: List[float]                # [gx, gy, gz] in rad/s
    mag: Optional[List[float]] = None # [mx, my, mz] in microtesla
    orientation: Optional[List[float]] = None # [yaw, pitch, roll] in degrees
    gravity: Optional[List[float]] = None     # [gx, gy, gz] in m/s^2
    gnss_lat: Optional[float] = None # WGS84 Latitude in degrees
    gnss_lon: Optional[float] = None # WGS84 Longitude in degrees
    gnss_alt: Optional[float] = None # Altitude in meters
    gnss_speed_kmh: Optional[float] = None # Speed in km/h
    gnss_bearing_deg: Optional[float] = None # Course over ground in degrees
    gnss_accuracy_m: Optional[float] = 3.0 # Horizontal accuracy in meters
    gnss_satellites: Optional[int] = 8 # Visible satellite count


@dataclass
class NavigationResult:
    """Standardized navigation state output packet."""
    timestamp_ms: float
    lat: float
    lon: float
    alt: float
    speed_mps: float
    speed_kmh: float
    heading_deg: float
    roll_deg: float
    pitch_deg: float
    nav_mode: str          # "GNSS_AIDED", "IDR_OUTAGE", "IDR_ZUPT", "RECOVERY"
    gnss_outage: bool
    confidence_score: float # 0.0 to 1.0
    drift_error_m: float
    drift_percentage: float # e.g. 3.4% (< 10% target)
    distance_traveled_m: float
    outage_distance_m: float
    outage_duration_s: float
    pos_uncertainty_m: float


class IDREngine:
    """
    Complete Smartphone-Based Intelligent Dead Reckoning Engine.
    Handles continuous sensor fusion, autonomous blackout detection, and recovery.
    """

    def __init__(self, model_path: Optional[str] = "models/speed_estimator.pkl"):
        self.ekf: Optional[ErrorStateEKF] = None
        self.nhc = NonHolonomicConstraints()
        self.ai_speed = AISpeedEstimator(model_path=model_path)
        self.drift_model = DriftCorrectionModel()

        # Origin reference
        self.ref_lat: Optional[float] = None
        self.ref_lon: Optional[float] = None
        self.ref_alt: float = 0.0
        self.is_initialized = False

        # State tracking
        self.last_ts_ms: Optional[float] = None
        self.total_distance_m = 0.0

        # Outage management
        self.forced_blackout = False      # For demo/simulation
        self.in_outage = False
        self.outage_start_ts_ms: Optional[float] = None
        self.outage_start_dist_m: float = 0.0
        self.outage_distance_m: float = 0.0
        self.outage_duration_s: float = 0.0

        # Reference ground truth position during simulated outages (for benchmark tracking)
        self.last_gnss_lat: Optional[float] = None
        self.last_gnss_lon: Optional[float] = None

        # Cumulative drift tracking
        self.current_drift_m = 0.0

        # Online speed scale factor calibration
        self.speed_scale_factor = 1.0
        self.last_valid_speed_mps = 0.0

    def trigger_simulated_blackout(self, active: bool = True):
        """Enable or disable simulated GNSS blackout (e.g. Tunnel / Canyon demo)."""
        self.forced_blackout = active

    def initialize(self, lat: float, lon: float, alt: float = 0.0,
                   heading_deg: float = 0.0, speed_mps: float = 0.0):
        """Initialize reference frame and EKF state."""
        self.ref_lat = lat
        self.ref_lon = lon
        self.ref_alt = alt

        self.ekf = ErrorStateEKF(lat, lon, alt)
        self.ekf.ins.initialize_state(lat, lon, alt, heading_deg, speed_mps)
        self.is_initialized = True
        self.total_distance_m = 0.0

    def process_sensor_packet(self, packet: SensorPacket) -> NavigationResult:
        """Process one incoming synchronized sensor packet and advance navigation solution."""
        # Auto-initialize on first valid GNSS fix
        if not self.is_initialized:
            init_lat = packet.gnss_lat if packet.gnss_lat is not None else 52.43163
            init_lon = packet.gnss_lon if packet.gnss_lon is not None else -1.525745
            init_alt = packet.gnss_alt if packet.gnss_alt is not None else 100.0
            init_heading = packet.gnss_bearing_deg if packet.gnss_bearing_deg is not None else 0.0
            init_speed = (packet.gnss_speed_kmh / 3.6) if packet.gnss_speed_kmh is not None else 0.0
            self.initialize(init_lat, init_lon, init_alt, init_heading, init_speed)
            self.last_ts_ms = packet.timestamp_ms

        # Compute delta time dt
        if self.last_ts_ms is not None:
            dt = (packet.timestamp_ms - self.last_ts_ms) / 1000.0
            if dt <= 0.001 or dt > 2.0:
                dt = 0.5
        else:
            dt = 0.5
        self.last_ts_ms = packet.timestamp_ms

        acc_np = np.array(packet.accel, dtype=np.float64)
        gyro_np = np.array(packet.gyro, dtype=np.float64)
        grav_np = np.array(packet.gravity, dtype=np.float64) if packet.gravity else None

        # Update AI speed estimator buffer
        self.ai_speed.push_imu_sample(acc_np, gyro_np)

        # 1. Update fused attitude if phone orientation available
        if packet.orientation is not None:
            yaw_deg, pitch_deg, roll_deg = packet.orientation
            self.ekf.ins.update_attitude_from_orientation(yaw_deg, pitch_deg, roll_deg)

        pos_prev = self.ekf.ins.pos_enu.copy()

        # 2. INS Prediction step
        nav_state = self.ekf.predict(acc_np, dt, gyro_np, grav_np)
        if not self.in_outage:
            self.total_distance_m = self.ekf.ins.total_distance_m

        # 3. Assess GNSS availability & quality
        has_gnss_fix = (
            packet.gnss_lat is not None and
            packet.gnss_lon is not None and
            not self.forced_blackout and
            (packet.gnss_accuracy_m is None or packet.gnss_accuracy_m < 30.0) and
            (packet.gnss_satellites is None or packet.gnss_satellites >= 4)
        )

        # Outage state machine
        if not has_gnss_fix:
            if not self.in_outage:
                # Entering new blackout
                self.in_outage = True
                self.outage_start_ts_ms = packet.timestamp_ms
                self.outage_start_dist_m = self.total_distance_m
                self.outage_distance_m = 0.0
                self.outage_duration_s = 0.0
            else:
                self.outage_distance_m = self.total_distance_m - self.outage_start_dist_m
                self.outage_duration_s = (packet.timestamp_ms - self.outage_start_ts_ms) / 1000.0

            # 4. In Outage: Apply ZUPT or AI-Speed + NHC
            raw_pred_speed = self.ai_speed.predict_speed()
            raw_ai_speed = max(0.0, raw_pred_speed * self.speed_scale_factor)

            # Blend inertial continuity from entry speed with AI speed model
            decay_weight = float(np.exp(-self.outage_duration_s / 35.0)) if self.last_valid_speed_mps > 1.0 else 0.0
            pred_fwd_speed = decay_weight * self.last_valid_speed_mps + (1.0 - decay_weight) * raw_ai_speed

            acc_norm = float(np.linalg.norm(acc_np))
            gyro_norm = float(np.linalg.norm(gyro_np))
            is_stopped = self.nhc.detect_zero_velocity(acc_np, gyro_np) and (raw_ai_speed < 3.5)
            
            # Rate-of-change continuity constraint on forward speed (prevents impossible instantaneous drops/spikes)
            if not hasattr(self, 'dr_fwd_speed_mps') or self.dr_fwd_speed_mps is None:
                self.dr_fwd_speed_mps = self.last_valid_speed_mps if self.last_valid_speed_mps > 0.5 else pred_fwd_speed

            if is_stopped:
                max_stop_decel = 5.0 * dt
                self.dr_fwd_speed_mps = max(0.0, self.dr_fwd_speed_mps - max_stop_decel)
                if self.dr_fwd_speed_mps < 0.3:
                    self.dr_fwd_speed_mps = 0.0
                pred_fwd_speed = self.dr_fwd_speed_mps
                self.last_valid_speed_mps = pred_fwd_speed
            else:
                max_decel = 4.5 * dt
                max_accel = 3.5 * dt
                pred_fwd_speed = float(np.clip(pred_fwd_speed, self.dr_fwd_speed_mps - max_decel, self.dr_fwd_speed_mps + max_accel))
                self.dr_fwd_speed_mps = pred_fwd_speed

            if is_stopped and pred_fwd_speed == 0.0:
                self.ekf.update_zupt(zupt_sigma=0.02)
            else:
                self.ekf.update_ai_speed_and_nhc(
                    ai_forward_speed_mps=pred_fwd_speed,
                    speed_sigma=0.5,
                    nhc_sigma=0.08
                )

            # High-fidelity Kinematic Dead Reckoning projection along vehicle heading
            # Eliminates runaway unconstrained accelerometer double-integration tilt drift
            sol_temp = self.ekf.get_nav_solution()
            hdg_rad = math.radians(sol_temp["heading_deg"])
            dr_step_dist = pred_fwd_speed * dt
            d_east = dr_step_dist * math.sin(hdg_rad)
            d_north = dr_step_dist * math.cos(hdg_rad)

            self.ekf.ins.pos_enu[0] = pos_prev[0] + d_east
            self.ekf.ins.pos_enu[1] = pos_prev[1] + d_north
            self.ekf.ins.vel_enu = np.array([
                pred_fwd_speed * math.sin(hdg_rad),
                pred_fwd_speed * math.cos(hdg_rad),
                0.0
            ], dtype=np.float64)

            self.total_distance_m += dr_step_dist
            self.ekf.ins.total_distance_m = self.total_distance_m
            self.outage_distance_m = self.total_distance_m - self.outage_start_dist_m
            self.outage_duration_s = (packet.timestamp_ms - self.outage_start_ts_ms) / 1000.0
        else:
            # GNSS available
            if self.in_outage:
                # Exiting outage -> Recovery mode
                self.in_outage = False
                self.ekf.nav_mode = "RECOVERY"

            speed_mps = (packet.gnss_speed_kmh / 3.6) if packet.gnss_speed_kmh is not None else None
            alt_m = packet.gnss_alt if packet.gnss_alt is not None else 0.0
            acc_m = packet.gnss_accuracy_m if packet.gnss_accuracy_m is not None else 3.0

            # Online speed calibration against true GNSS (valid down to 0.4 m/s = 1.4 km/h)
            if speed_mps is not None and speed_mps > 0.4:
                self.last_valid_speed_mps = speed_mps
                self.dr_fwd_speed_mps = speed_mps
                pred_ai = self.ai_speed.predict_speed()
                if pred_ai > 0.5:
                    self.speed_scale_factor = float(np.clip(speed_mps / pred_ai, 0.2, 5.0))

            self.ekf.update_gnss(
                lat_deg=packet.gnss_lat,
                lon_deg=packet.gnss_lon,
                alt_m=alt_m,
                speed_mps=speed_mps,
                course_deg=packet.gnss_bearing_deg,
                accuracy_m=acc_m
            )
            self.last_gnss_lat = packet.gnss_lat
            self.last_gnss_lon = packet.gnss_lon

        # 5. Extract latest fused navigation state
        sol = self.ekf.get_nav_solution()

        # 6. Compute Drift Metrics against reference if in simulated outage
        if self.in_outage and packet.gnss_lat is not None and packet.gnss_lon is not None:
            # Compare dead reckoning position against underlying ground truth GPS
            gt_enu = geodetic_to_enu(packet.gnss_lat, packet.gnss_lon, 0.0,
                                     self.ref_lat, self.ref_lon, self.ref_alt)
            est_enu = sol["pos_enu"]
            self.current_drift_m = float(math.sqrt((gt_enu[0] - est_enu[0])**2 + (gt_enu[1] - est_enu[1])**2))
        elif not self.in_outage:
            self.current_drift_m = 0.0

        drift_pct = 0.0
        if self.outage_distance_m > 10.0:
            drift_pct = (self.current_drift_m / self.outage_distance_m) * 100.0

        confidence = 1.0 if not self.in_outage else max(0.5, 1.0 - (sol["pos_uncertainty_m"] / 50.0))

        return NavigationResult(
            timestamp_ms=packet.timestamp_ms,
            lat=sol["lat"],
            lon=sol["lon"],
            alt=sol["alt"],
            speed_mps=sol["speed_mps"],
            speed_kmh=sol["speed_kmh"],
            heading_deg=sol["heading_deg"],
            roll_deg=nav_state["roll_deg"],
            pitch_deg=nav_state["pitch_deg"],
            nav_mode=sol["nav_mode"],
            gnss_outage=self.in_outage,
            confidence_score=confidence,
            drift_error_m=self.current_drift_m,
            drift_percentage=drift_pct,
            distance_traveled_m=self.total_distance_m,
            outage_distance_m=self.outage_distance_m,
            outage_duration_s=self.outage_duration_s,
            pos_uncertainty_m=sol["pos_uncertainty_m"]
        )
