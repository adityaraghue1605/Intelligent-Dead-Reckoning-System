"""
15-State Error-State Extended Kalman Filter (ES-EKF)
===================================================
Fuses:
 - Inertial strapdown mechanization (INS prediction)
 - GNSS position & velocity updates (when satellite lock is available)
 - AI-inferred vehicle forward speed (during GNSS outages)
 - Non-Holonomic Constraints (NHC: zero lateral & vertical body velocity)
 - Zero-Velocity Updates (ZUPT: stationary detection)

States:
  delta_x = [delta_p (3), delta_v (3), psi (3), b_acc (3), b_gyro (3)]^T  (15x1)
"""

import math
import numpy as np
from typing import Tuple, Dict, Any, Optional

from navigation.ins import StrapdownINS, geodetic_to_enu, enu_to_geodetic


def skew_symmetric(v: np.ndarray) -> np.ndarray:
    """Return 3x3 skew symmetric matrix of vector v."""
    return np.array([
        [ 0.0,  -v[2],  v[1]],
        [ v[2],  0.0,  -v[0]],
        [-v[1],  v[0],  0.0]
    ], dtype=np.float64)


class ErrorStateEKF:
    """
    15-state Error-State Extended Kalman Filter for GNSS/INS Integration.
    Maintains covariance matrix P (15x15) and nominal navigation states.
    """

    def __init__(self, ref_lat_deg: float, ref_lon_deg: float, ref_alt_m: float = 0.0):
        self.ins = StrapdownINS(ref_lat_deg, ref_lon_deg, ref_alt_m)

        # 15-state error vector (reset to 0 after every feedback correction)
        self.dx = np.zeros(15, dtype=np.float64)

        # Covariance matrix P (15x15)
        self.P = np.eye(15, dtype=np.float64)
        # Initial uncertainties:
        self.P[0:3, 0:3] *= 4.0**2       # Position std: 4 m
        self.P[3:6, 3:6] *= 0.5**2       # Velocity std: 0.5 m/s
        self.P[6:9, 6:9] *= math.radians(3.0)**2  # Attitude std: 3 degrees
        self.P[9:12, 9:12] *= 0.05**2    # Accel bias std: 0.05 m/s^2
        self.P[12:15, 12:15] *= 0.005**2 # Gyro bias std: 0.005 rad/s

        # Sensor bias estimates
        self.accel_bias = np.zeros(3, dtype=np.float64)
        self.gyro_bias  = np.zeros(3, dtype=np.float64)

        # Process noise spectral densities Q
        self.sigma_acc_noise = 0.2     # m/s^2 / sqrt(Hz)
        self.sigma_gyro_noise = 0.01   # rad/s / sqrt(Hz)
        self.sigma_acc_bias_walk = 0.001 # m/s^3 / sqrt(Hz)
        self.sigma_gyro_bias_walk = 0.0001 # rad/s^2 / sqrt(Hz)

        # Navigation mode
        # "GNSS_AIDED", "IDR_OUTAGE", "IDR_ZUPT", "RECOVERY"
        self.nav_mode = "INITIALIZING"

    def predict(self, accel_raw: np.ndarray, dt: float,
                gyro_raw: Optional[np.ndarray] = None,
                gravity_body: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        Prediction Step:
         1. Correct raw sensors with current bias estimates
         2. Advance INS nominal state
         3. Propagate error-state covariance matrix P = F * P * F^T + Q
        """
        # Bias correction
        acc_corr = accel_raw - self.accel_bias
        gyro_corr = (gyro_raw - self.gyro_bias) if gyro_raw is not None else None

        # INS mechanization step
        nav_state = self.ins.step(acc_corr, dt, gyro_corr, gravity_body)

        # Construct continuous-time Error Dynamics Matrix F (15x15)
        F = np.zeros((15, 15), dtype=np.float64)

        # dp_dot = dv
        F[0:3, 3:6] = np.eye(3)

        # dv_dot = -skew(f_n)*psi + R_b^n * da_bias
        f_n = self.ins.R_b_n @ acc_corr
        F[3:6, 6:9] = -skew_symmetric(f_n)
        F[3:6, 9:12] = self.ins.R_b_n

        # dpsi_dot = -R_b^n * dg_bias
        F[6:9, 12:15] = -self.ins.R_b_n

        # Discrete-time state transition matrix Phi = I + F*dt
        Phi = np.eye(15, dtype=np.float64) + F * dt

        # Discrete Process Noise Covariance Q_k
        Q_k = np.zeros((15, 15), dtype=np.float64)
        Q_k[3:6, 3:6]     = (self.sigma_acc_noise**2 * dt) * np.eye(3)
        Q_k[6:9, 6:9]     = (self.sigma_gyro_noise**2 * dt) * np.eye(3)
        Q_k[9:12, 9:12]   = (self.sigma_acc_bias_walk**2 * dt) * np.eye(3)
        Q_k[12:15, 12:15] = (self.sigma_gyro_bias_walk**2 * dt) * np.eye(3)

        # Propagate covariance
        self.P = Phi @ self.P @ Phi.T + Q_k
        # Ensure symmetric positive-definite
        self.P = 0.5 * (self.P + self.P.T)

        return nav_state

    def update_gnss(self, lat_deg: float, lon_deg: float, alt_m: float,
                    speed_mps: Optional[float] = None,
                    course_deg: Optional[float] = None,
                    accuracy_m: float = 3.0):
        """
        Measurement Update: Full GNSS position and horizontal velocity.
        """
        self.nav_mode = "GNSS_AIDED"

        # Observed position in ENU
        pos_obs = geodetic_to_enu(lat_deg, lon_deg, alt_m,
                                  self.ins.ref_lat, self.ins.ref_lon, self.ins.ref_alt)
        pos_res = pos_obs - self.ins.pos_enu

        # Position measurement matrix (3x15)
        H_pos = np.zeros((3, 15), dtype=np.float64)
        H_pos[0:3, 0:3] = np.eye(3)

        R_pos = np.eye(3, dtype=np.float64) * (max(1.0, accuracy_m)**2)

        # Perform Kalman measurement update on position
        self._kalman_update(H_pos, pos_res, R_pos)

        # Optional velocity update if speed and course available
        if speed_mps is not None and speed_mps >= 0.2 and course_deg is not None:
            course_rad = math.radians((450.0 - course_deg) % 360.0)
            v_east = speed_mps * math.cos(course_rad)
            v_north = speed_mps * math.sin(course_rad)
            vel_obs = np.array([v_east, v_north, 0.0], dtype=np.float64)
            vel_res = vel_obs - self.ins.vel_enu

            H_vel = np.zeros((2, 15), dtype=np.float64)
            H_vel[0:2, 3:5] = np.eye(2)
            R_vel = np.eye(2, dtype=np.float64) * (0.5**2)

            self._kalman_update(H_vel, vel_res[:2], R_vel)

    def update_ai_speed_and_nhc(self, ai_forward_speed_mps: float,
                                speed_sigma: float = 1.0,
                                nhc_sigma: float = 0.15):
        """
        Measurement Update during GNSS Outage:
         1. Forward velocity = AI predicted speed (v_y_body ≈ ai_speed)
         2. Lateral velocity = 0 m/s (NHC constraint)
         3. Vertical velocity = 0 m/s (Road plane constraint)
        """
        self.nav_mode = "IDR_OUTAGE"

        # Current body velocity
        v_body_curr = self.ins.R_b_n.T @ self.ins.vel_enu

        # Forward axis is body X (column 0 of R_b^n)
        target_v_body = np.array([max(0.0, ai_forward_speed_mps), 0.0, 0.0], dtype=np.float64)
        res_v_body = target_v_body - v_body_curr

        # Measurement matrix: v_body = (R_b^n)^T * v_enu
        H_3d = np.zeros((3, 15), dtype=np.float64)
        H_3d[0:3, 3:6] = self.ins.R_b_n.T

        R_cov = np.diag([speed_sigma**2, nhc_sigma**2, nhc_sigma**2])
        self._kalman_update(H_3d, res_v_body, R_cov)

    def update_zupt(self, zupt_sigma: float = 0.05):
        """Zero-Velocity Update when vehicle is detected stationary."""
        self.nav_mode = "IDR_ZUPT"
        res_vel = np.zeros(3, dtype=np.float64) - self.ins.vel_enu

        H_zupt = np.zeros((3, 15), dtype=np.float64)
        H_zupt[0:3, 3:6] = np.eye(3)
        R_zupt = np.eye(3, dtype=np.float64) * (zupt_sigma**2)

        self._kalman_update(H_zupt, res_vel, R_zupt)

    def _kalman_update(self, H: np.ndarray, residual: np.ndarray, R_cov: np.ndarray):
        """Standard Kalman gain, state error correction, and covariance update."""
        S = H @ self.P @ H.T + R_cov
        K = self.P @ H.T @ np.linalg.inv(S)

        # Error state correction
        delta_x = K @ residual

        # Feedback to nominal INS states
        self.ins.pos_enu += delta_x[0:3]
        self.ins.vel_enu += delta_x[3:6]

        # Small angle attitude correction: R_new = (I - skew(psi)) * R_old
        psi = delta_x[6:9]
        d_R = np.eye(3) - skew_symmetric(psi)
        self.ins.R_b_n = d_R @ self.ins.R_b_n

        # Update biases
        self.accel_bias += delta_x[9:12]
        self.gyro_bias  += delta_x[12:15]

        # Joseph form covariance update for numerical stability:
        # P = (I - K*H)*P*(I - K*H)^T + K*R*K^T
        I_KH = np.eye(15, dtype=np.float64) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R_cov @ K.T
        self.P = 0.5 * (self.P + self.P.T)

    def get_nav_solution(self) -> Dict[str, Any]:
        """Return full navigation solution packet."""
        lat, lon, alt = enu_to_geodetic(
            self.ins.pos_enu[0], self.ins.pos_enu[1], self.ins.pos_enu[2],
            self.ins.ref_lat, self.ins.ref_lon, self.ins.ref_alt
        )
        roll, pitch, yaw = self.ins.R_b_n_to_euler if hasattr(self.ins, 'R_b_n_to_euler') else (0.0, 0.0, 0.0)
        # Compute compass heading from yaw
        R = self.ins.R_b_n
        pitch_rad = math.asin(np.clip(-R[2, 0], -1.0, 1.0))
        yaw_rad = math.atan2(R[1, 0], R[0, 0]) if abs(math.cos(pitch_rad)) > 1e-4 else 0.0
        compass_heading = (450.0 - math.degrees(yaw_rad)) % 360.0

        speed_mps = float(np.linalg.norm(self.ins.vel_enu[:2]))
        pos_std_m = float(math.sqrt(max(0.0, self.P[0, 0] + self.P[1, 1])))

        return {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "pos_enu": self.ins.pos_enu.copy(),
            "vel_enu": self.ins.vel_enu.copy(),
            "speed_mps": speed_mps,
            "speed_kmh": speed_mps * 3.6,
            "heading_deg": compass_heading,
            "nav_mode": self.nav_mode,
            "pos_uncertainty_m": pos_std_m,
            "total_dist_m": self.ins.total_distance_m
        }
