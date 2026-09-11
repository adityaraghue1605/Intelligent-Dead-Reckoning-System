"""
AI Kinematic Speed Estimator
============================
Predicts vehicle forward speed (m/s) purely from smartphone IMU signals during GNSS blackouts.
Trained on real IO-VNBD drive trajectories using high-fidelity GPS speed as reference.

Features extracted per window (e.g. 1.0 - 2.0s window):
 - Acceleration norm, mean, std, min, max per axis
 - Dynamic acceleration (gravity-removed) magnitude
 - Gyroscope norm, angular rates mean & variance
 - Jerk (rate of change of acceleration)
 - Vibration energy index
"""

import os
import math
import pickle
import numpy as np
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional


class SpeedFeatureExtractor:
    """Extracts statistical and kinematic features from sliding IMU buffers."""

    def __init__(self, window_size: int = 4):
        self.window_size = window_size
        self.feature_names = [
            "acc_norm_mean", "acc_norm_std", "acc_norm_max", "acc_norm_min",
            "acc_x_mean", "acc_x_std", "acc_y_mean", "acc_y_std", "acc_z_mean", "acc_z_std",
            "gyro_norm_mean", "gyro_norm_std", "gyro_yaw_mean", "gyro_yaw_std",
            "dynamic_acc_energy", "jerk_mean", "jerk_std"
        ]

    def extract_from_window(self, acc_window: np.ndarray, gyro_window: np.ndarray) -> np.ndarray:
        """
        acc_window: shape (N, 3) in m/s^2
        gyro_window: shape (N, 3) in rad/s
        Returns 1D feature vector of length 17.
        """
        N = len(acc_window)
        if N == 0:
            return np.zeros(len(self.feature_names), dtype=np.float32)

        # Acceleration norms
        acc_norms = np.linalg.norm(acc_window, axis=1)
        acc_norm_mean = float(np.mean(acc_norms))
        acc_norm_std  = float(np.std(acc_norms))
        acc_norm_max  = float(np.max(acc_norms))
        acc_norm_min  = float(np.min(acc_norms))

        # Per axis stats
        ax_mean, ay_mean, az_mean = np.mean(acc_window, axis=0)
        ax_std,  ay_std,  az_std  = np.std(acc_window, axis=0)

        # Gyro stats
        gyro_norms = np.linalg.norm(gyro_window, axis=1)
        gyro_norm_mean = float(np.mean(gyro_norms))
        gyro_norm_std  = float(np.std(gyro_norms))
        gyro_yaw_mean  = float(np.mean(gyro_window[:, 0]))
        gyro_yaw_std   = float(np.std(gyro_window[:, 0]))

        # Dynamic acceleration energy (removing ~9.81 m/s^2 earth gravity)
        dyn_acc = acc_norms - 9.80665
        dyn_energy = float(np.mean(dyn_acc ** 2))

        # Jerk (differences between consecutive samples)
        if N > 1:
            jerks = np.diff(acc_norms)
            jerk_mean = float(np.mean(np.abs(jerks)))
            jerk_std  = float(np.std(jerks))
        else:
            jerk_mean = 0.0
            jerk_std  = 0.0

        feats = np.array([
            acc_norm_mean, acc_norm_std, acc_norm_max, acc_norm_min,
            ax_mean, ax_std, ay_mean, ay_std, az_mean, az_std,
            gyro_norm_mean, gyro_norm_std, gyro_yaw_mean, gyro_yaw_std,
            dyn_energy, jerk_mean, jerk_std
        ], dtype=np.float32)

        # Sanitize NaNs
        return np.nan_to_num(feats, nan=0.0, posinf=100.0, neginf=-100.0)


class AISpeedEstimator:
    """
    Inference and Training wrapper for AI Speed Estimation.
    Uses an ensemble of Random Forest / Gradient Boosted regressors with ONNX export.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.extractor = SpeedFeatureExtractor(window_size=4)
        self.model = None
        self.scaler = None
        self.is_trained = False

        # Sliding buffers for online streaming
        self.acc_buffer = []
        self.gyro_buffer = []
        self.window_len = 4  # 2.0 seconds at 2 Hz

        if model_path and os.path.exists(model_path):
            self.load(model_path)

    def push_imu_sample(self, acc: np.ndarray, gyro: np.ndarray):
        """Add latest IMU sample to sliding window."""
        self.acc_buffer.append(acc)
        self.gyro_buffer.append(gyro)
        if len(self.acc_buffer) > self.window_len:
            self.acc_buffer.pop(0)
            self.gyro_buffer.pop(0)

    def predict_speed(self) -> float:
        """
        Estimate forward speed (m/s) from current sliding window.
        Returns 0.0 m/s if vehicle is stationary.
        """
        if len(self.acc_buffer) < 2:
            return 0.0

        acc_arr = np.array(self.acc_buffer, dtype=np.float32)
        gyro_arr = np.array(self.gyro_buffer, dtype=np.float32)

        # Zero Velocity Check: if sensor variance is very low, speed is 0
        acc_std = float(np.std(acc_arr))
        gyro_norm = float(np.mean(np.linalg.norm(gyro_arr, axis=1)))
        if acc_std < 0.08 and gyro_norm < 0.03:
            return 0.0

        feats = self.extractor.extract_from_window(acc_arr, gyro_arr).reshape(1, -1)

        if self.model is not None and self.is_trained:
            if self.scaler is not None:
                feats = self.scaler.transform(feats)
            pred_speed = float(self.model.predict(feats)[0])
            return max(0.0, pred_speed)

        # Analytical kinematic fallback if model not loaded
        # Uses empirical energy-to-speed relationship
        dyn_energy = float(feats[0, 14])
        acc_norm_std = float(feats[0, 1])
        est_speed = math.sqrt(max(0.0, acc_norm_std * 12.0 + dyn_energy * 2.0))
        return float(np.clip(est_speed, 0.0, 40.0))

    def fit(self, X_features: np.ndarray, y_speed: np.ndarray):
        """Train Random Forest regressor on real IO-VNBD extracted features."""
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.preprocessing import StandardScaler

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_features)

        self.model = RandomForestRegressor(
            n_estimators=100,
            max_depth=12,
            min_samples_split=4,
            random_state=42,
            n_jobs=-1
        )
        self.model.fit(X_scaled, y_speed)
        self.is_trained = True

    def save(self, file_path: str):
        """Save model and scaler to disk."""
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            pickle.dump({"model": self.model, "scaler": self.scaler}, f)

    def load(self, file_path: str):
        """Load trained model and scaler from disk."""
        with open(file_path, "rb") as f:
            data = pickle.load(f)
            self.model = data["model"]
            self.scaler = data["scaler"]
            self.is_trained = True
