"""
AI Residual Drift & Heading Correction Layer
============================================
Compensates for sensor non-linearities, temperature drift, and heading divergence:
 - Centripetal acceleration cross-check (a_lat ≈ v * yaw_rate)
 - Online gyro bias correction during straight driving segments
 - Road slope & pitch compensation
 - Confidence & drift uncertainty estimator
"""

import math
import numpy as np
from typing import Dict, Any, Tuple, Optional


class DriftCorrectionModel:
    """
    Online drift and heading error compensation model.
    Monitors kinematic consistency between linear accelerations and rotational rates.
    """

    def __init__(self):
        self.gyro_bias_est = np.zeros(3, dtype=np.float64)
        self.straight_samples = 0
        self.gyro_window = []

    def evaluate_consistency(self, speed_mps: float, gyro_yaw_rad_s: float,
                             acc_lat_mps2: float) -> Tuple[float, float]:
        """
        Evaluate kinematic consistency:
        In a turn, centripetal acceleration is a_c = v * omega_yaw.
        Discrepancies indicate gyro drift or accelerometer bias.
        Returns: (centripetal_residual, confidence_score [0..1])
        """
        if speed_mps < 0.5:
            # Low speed: centripetal forces are negligible
            return 0.0, 1.0

        expected_a_lat = speed_mps * gyro_yaw_rad_s
        residual = abs(acc_lat_mps2 - expected_a_lat)

        # Confidence decays as residual increases
        confidence = float(math.exp(-0.5 * (residual / 1.5)**2))
        return residual, confidence

    def update_gyro_drift(self, gyro_rates: np.ndarray, speed_mps: float,
                          is_straight_road: bool) -> np.ndarray:
        """
        Estimate residual gyro bias when vehicle is moving straight at cruising speed.
        When moving straight, true yaw rate is 0. Any non-zero mean is gyro bias.
        """
        if speed_mps > 3.0 and is_straight_road:
            self.straight_samples += 1
            alpha = min(0.05, 1.0 / (self.straight_samples + 10))
            self.gyro_bias_est = (1.0 - alpha) * self.gyro_bias_est + alpha * gyro_rates
        return self.gyro_bias_est.copy()

    def estimate_drift_percentage(self, outage_duration_sec: float,
                                   distance_travelled_m: float,
                                   pos_uncertainty_m: float) -> float:
        """
        Compute real-time drift percentage:
        drift_pct = (estimated_position_error / max(10m, distance_travelled)) * 100
        """
        if distance_travelled_m < 10.0:
            return 0.0

        drift_pct = (pos_uncertainty_m / distance_travelled_m) * 100.0
        return float(np.clip(drift_pct, 0.0, 100.0))
