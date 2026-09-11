"""
Training Pipeline for AI Speed Estimator
========================================
Extracts sliding window features from real IO-VNBD CSV trajectories:
 - Uses authentic smartphone accelerometer & gyroscope streams
 - Uses real GPS Speed (km/h) as training supervision
 - Evaluates test metrics: R^2, Mean Absolute Error (MAE), RMSE
 - Exports trained model to models/speed_estimator.pkl
"""

import sys
import math
from typing import Tuple
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ai.speed_estimator import SpeedFeatureExtractor, AISpeedEstimator

RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"
MODELS_DIR   = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_dataset_features(csv_path: Path, window_size: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """Load real IO-VNBD CSV and extract (X, y) windows."""
    df = pd.read_csv(csv_path, encoding="latin1")
    df.columns = [str(c).strip() for c in df.columns]

    # Find columns
    time_col = [c for c in df.columns if "TIME" in c.upper()][0]
    speed_col = [c for c in df.columns if "SPEED" in c.upper()][0]
    ax_col = [c for c in df.columns if "ACCELEROMETER" in c.upper() and "X" in c.upper()][0]
    ay_col = [c for c in df.columns if "ACCELEROMETER" in c.upper() and "Y" in c.upper()][0]
    az_col = [c for c in df.columns if "ACCELEROMETER" in c.upper() and "Z" in c.upper()][0]
    gx_col = [c for c in df.columns if "GYROSCOPE" in c.upper() and ("YAW" in c.upper() or "Z" in c.upper())][0]
    gy_col = [c for c in df.columns if "GYROSCOPE" in c.upper() and ("PITCH" in c.upper() or "Y" in c.upper())][0]
    gz_col = [c for c in df.columns if "GYROSCOPE" in c.upper() and ("ROLL" in c.upper() or "X" in c.upper())][0]

    acc_df = df[[ax_col, ay_col, az_col]].apply(pd.to_numeric, errors='coerce').fillna(0.0)
    gyro_df = df[[gx_col, gy_col, gz_col]].apply(pd.to_numeric, errors='coerce').fillna(0.0)
    speed_series = pd.to_numeric(df[speed_col], errors='coerce').fillna(0.0)

    acc_data  = acc_df.values.astype(np.float32)
    gyro_data = gyro_df.values.astype(np.float32)
    speed_mps = (speed_series.values / 3.6).astype(np.float32)

    extractor = SpeedFeatureExtractor(window_size=window_size)
    X, y = [], []

    N = len(df)
    step = 2  # 50% window overlap
    for i in range(0, N - window_size + 1, step):
        acc_win = acc_data[i:i+window_size]
        gyro_win = gyro_data[i:i+window_size]
        feats = extractor.extract_from_window(acc_win, gyro_win)
        target_speed = float(np.median(speed_mps[i:i+window_size]))
        X.append(feats)
        y.append(target_speed)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def main():
    print("=" * 65)
    print("AI Speed Estimator Training on Real IO-VNBD Dataset")
    print("=" * 65)

    csv_files = list(RAW_DATA_DIR.rglob("*.csv"))
    print(f"Found {len(csv_files)} real CSV datasets in {RAW_DATA_DIR}")

    if not csv_files:
        print("[ERR] No CSV files found. Please ensure IO-VNBD dataset is downloaded.")
        return

    # Select primary training trajectories
    train_files = [f for f in csv_files if any(k in f.name for k in ["S-A1.csv", "S-A2.csv", "S-A3.csv", "S-A4.csv"])]
    if not train_files:
        train_files = csv_files[:3]

    print(f"Loading features from {len(train_files)} training files: {[f.name for f in train_files]}")

    X_all, y_all = [], []
    for f in train_files:
        print(f"  Processing {f.name} ...")
        X, y = load_dataset_features(f)
        X_all.append(X)
        y_all.append(y)

    X_train = np.vstack(X_all)
    y_train = np.concatenate(y_all)
    print(f"Total training samples: {len(X_train)} windows (feature dimension: {X_train.shape[1]})")
    print(f"Speed range: {np.min(y_train):.1f} to {np.max(y_train):.1f} m/s (mean: {np.mean(y_train):.1f} m/s)")

    # Train-test split
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

    X_tr, X_val, y_tr, y_val = train_test_split(X_train, y_train, test_size=0.2, random_state=42)

    print("\nTraining Random Forest Regressor ...")
    estimator = AISpeedEstimator()
    estimator.fit(X_tr, y_tr)

    # Evaluate
    X_val_scaled = estimator.scaler.transform(X_val)
    y_pred = estimator.model.predict(X_val_scaled)
    r2 = r2_score(y_val, y_pred)
    mae = mean_absolute_error(y_val, y_pred)
    rmse = math.sqrt(mean_squared_error(y_val, y_pred))

    print("\n" + "=" * 40)
    print("VALIDATION METRICS (Real IO-VNBD Test Set)")
    print("=" * 40)
    print(f"  R^2 Score            : {r2:.4f}")
    print(f"  Mean Absolute Error  : {mae:.3f} m/s ({mae*3.6:.2f} km/h)")
    print(f"  Root Mean Sq Error   : {rmse:.3f} m/s ({rmse*3.6:.2f} km/h)")
    print("=" * 40)

    # Save trained model
    out_path = MODELS_DIR / "speed_estimator.pkl"
    estimator.save(str(out_path))
    print(f"\n[OK] Model successfully saved to: {out_path}")


if __name__ == "__main__":
    main()
