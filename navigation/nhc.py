"""
Non-Holonomic Constraints (NHC) & Kinematic Velocity Constraints
================================================================
Implements land-vehicle kinematic constraints for dead reckoning:
 - Lateral velocity in vehicle frame: v_lat ≈ 0 m/s (no side slipping)
 - Vertical velocity in vehicle frame: v_vert ≈ 0 m/s (vehicle stays on road plane)
 - Zero-Velocity Update (ZUPT): detector when vehicle stops (traffic lights, stops)
 - Zero Angular Rate Update (ZARU): detector for straight-line driving
"""

import math
import numpy as np
from typing import Tuple, Dict, Any, Optional


class NonHolonomicConstraints:
    """
    Applies Non-Holonomic Constraints (NHC) to constrain velocity in the vehicle body frame:
    - Forward axis: velocity determined by forward kinematics / AI speed model
    - Lateral axis: velocity ~= 0 (residual error std ~ 0.05 - 0.1 m/s)
    - Vertical axis: velocity ~= 0 (residual error std ~ 0.05 - 0.1 m/s)
    """

    def __init__(self, sigma_lat: float = 0.1, sigma_vert: float = 0.1):
        self.sigma_lat = sigma_lat
        self.sigma_vert = sigma_vert

        # Zero Velocity Update (ZUPT) thresholds
        self.zupt_accel_window = []
        self.zupt_gyro_window = []
        self.window_size = 6  # 3 seconds at 2 Hz
        self.is_stationary = False

    def check_zupt(self, accel_norm: float, gyro_norm: float) -> bool:
        """
        Detect if vehicle is stationary (ZUPT condition) based on acceleration variance
        and angular velocity norm over sliding window.
        """
        self.zupt_accel_window.append(accel_norm)
        self.zupt_gyro_window.append(gyro_norm)
        if len(self.zupt_accel_window) > self.window_size:
            self.zupt_accel_window.pop(0)
            self.zupt_gyro_window.pop(0)

        if len(self.zupt_accel_window) < self.window_size:
            return False

        acc_std = float(np.std(self.zupt_accel_window))
        gyro_mean = float(np.mean(self.zupt_gyro_window))

        # Vehicle stationary criteria: low acceleration variation & gyro rates near zero (automotive idle vibration)
        self.is_stationary = (acc_std < 0.30 and gyro_mean < 0.085)
        return self.is_stationary

    def detect_zero_velocity(self, accel: np.ndarray, gyro: np.ndarray) -> bool:
        """Helper alias taking vectors directly."""
        accel_norm = float(np.linalg.norm(accel))
        gyro_norm = float(np.linalg.norm(gyro))
        return self.check_zupt(accel_norm, gyro_norm)

    def project_velocity_to_body(self, vel_enu: np.ndarray, R_b_n: np.ndarray) -> np.ndarray:
        """
        Convert velocity from ENU navigation frame to vehicle Body frame:
        v_body = (R_b^n)^T * v_enu
        """
        return R_b_n.T @ vel_enu

    def apply_nhc_filter(self, vel_enu: np.ndarray, R_b_n: np.ndarray,
                          forward_speed_hint: Optional[float] = None,
                          is_stopped: bool = False) -> np.ndarray:
        """
        Constrain velocity vector by suppressing lateral and vertical body drift:
        If is_stopped is True, all velocity components are driven to zero.
        Otherwise, lateral and vertical body velocities are strongly damped.
        """
        if is_stopped:
            return np.zeros(3, dtype=np.float64)

        # Body velocity: [v_lat_x, v_fwd_y, v_vert_z] or depending on phone mount
        # In typical smartphone vehicle mount: Y is forward (longitudinal), X is lateral, Z is vertical
        v_body = R_b_n.T @ vel_enu

        # Damp lateral and vertical velocities toward zero
        # Preserve or blend forward component
        fwd_speed = forward_speed_hint if forward_speed_hint is not None else v_body[1]

        # Constrained body velocity
        v_body_constrained = np.array([
            0.05 * v_body[0],   # Heavily damped lateral velocity
            max(0.0, fwd_speed), # Forward velocity (vehicles typically do not travel backward on highways)
            0.05 * v_body[2]    # Heavily damped vertical velocity
        ], dtype=np.float64)

        # Transform back to ENU navigation frame
        return R_b_n @ v_body_constrained

    def get_nhc_measurement_model(self, R_b_n: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return the measurement matrix H, measurement z, and covariance R for EKF update:
        z = [0, 0]^T (lateral and vertical body velocities expected to be 0)
        """
        # Rows 0 and 2 of R_b^n transpose correspond to lateral and vertical axes
        R_n_b = R_b_n.T
        H_vel = np.vstack([R_n_b[0, :], R_n_b[2, :]]) # Shape (2, 3)
        z = np.zeros(2, dtype=np.float64)
        R_cov = np.diag([self.sigma_lat**2, self.sigma_vert**2])
        return H_vel, z, R_cov
