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
from typing import Tuple, Optional
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ai.speed_estimator import SpeedFeatureExtractor, AISpeedEstimator

RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"
MODELS_DIR   = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_paired_dataset_features(s_path: Path, v_path: Optional[Path] = None, window_size: int = 4) -> Tuple[np.ndarray, np.ndarray]:
    """Load authentic IO-VNBD smartphone sensor data and paired vehicle ground truth speed."""
    df_s = pd.read_csv(s_path, encoding="latin1")
    df_s.columns = [str(c).strip() for c in df_s.columns]

    ax_col = [c for c in df_s.columns if "ACCELEROMETER" in c.upper() and "X" in c.upper()][0]
    ay_col = [c for c in df_s.columns if "ACCELEROMETER" in c.upper() and "Y" in c.upper()][0]
    az_col = [c for c in df_s.columns if "ACCELEROMETER" in c.upper() and "Z" in c.upper()][0]
    gx_col = [c for c in df_s.columns if "GYROSCOPE" in c.upper() and ("YAW" in c.upper() or "Z" in c.upper())][0]
    gy_col = [c for c in df_s.columns if "GYROSCOPE" in c.upper() and ("PITCH" in c.upper() or "Y" in c.upper())][0]
    gz_col = [c for c in df_s.columns if "GYROSCOPE" in c.upper() and ("ROLL" in c.upper() or "X" in c.upper())][0]

    acc_data = df_s[[ax_col, ay_col, az_col]].apply(pd.to_numeric, errors='coerce').fillna(0.0).values.astype(np.float32)
    gyro_data = df_s[[gx_col, gy_col, gz_col]].apply(pd.to_numeric, errors='coerce').fillna(0.0).values.astype(np.float32)

    if v_path and v_path.exists():
        df_v = pd.read_csv(v_path, encoding="latin1")
        df_v.columns = [str(c).strip() for c in df_v.columns]
        spd_col = [c for c in df_v.columns if "VELOCITY" in c.upper() or "SPEED" in c.upper()][0]
        # V-*.csv velocity is in km/hr -> convert to m/s
        speed_series = pd.to_numeric(df_v[spd_col], errors='coerce').fillna(0.0)
        speed_mps = (speed_series.values / 3.6).astype(np.float32)
    else:
        spd_col = [c for c in df_s.columns if "SPEED" in c.upper()][0]
        speed_series = pd.to_numeric(df_s[spd_col], errors='coerce').fillna(0.0)
        max_v = float(speed_series.max())
        if max_v < 40.0:
            speed_mps = speed_series.values.astype(np.float32)
        else:
            speed_mps = (speed_series.values / 3.6).astype(np.float32)

    min_len = min(len(acc_data), len(speed_mps))
    acc_data = acc_data[:min_len]
    gyro_data = gyro_data[:min_len]
    speed_mps = speed_mps[:min_len]

    extractor = SpeedFeatureExtractor(window_size=window_size)
    X, y = [], []

    step = 3  # Overlap stride
    for i in range(0, min_len - window_size + 1, step):
        acc_win = acc_data[i:i+window_size]
        gyro_win = gyro_data[i:i+window_size]
        feats = extractor.extract_from_window(acc_win, gyro_win)
        target_speed = float(np.median(speed_mps[i:i+window_size]))
        X.append(feats)
        y.append(target_speed)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def main():
    print("=" * 65)
    print("AI Speed Estimator Training on Real Synchronized IO-VNBD Dataset")
    print("=" * 65)

    pairs = [
        ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S1/S-S1.csv",
         "Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S1/V-S1.csv"),
        ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S2/S-S2.csv",
         "Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S2/V-S2.csv"),
        ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3a/S-S3a.csv",
         "Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3a/V-S3a.csv"),
        ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S4/S-S4.csv",
         "Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S4/V-S4.csv"),
    ]

    X_all, y_all = [], []
    for s_rel, v_rel in pairs:
        s_path = RAW_DATA_DIR / s_rel
        v_path = RAW_DATA_DIR / v_rel
        if s_path.exists() and v_path.exists():
            print(f"  Loading paired dataset: {s_path.name} + {v_path.name} ...")
            X, y = load_paired_dataset_features(s_path, v_path)
            # Sample up to 10,000 windows per scenario for balanced dataset
            if len(X) > 10000:
                idx = np.random.choice(len(X), 10000, replace=False)
                X, y = X[idx], y[idx]
            X_all.append(X)
            y_all.append(y)

    # Also include S-A10.csv (verified high-rate city drive)
    sa10_files = list(RAW_DATA_DIR.rglob("S-A10.csv"))
    if sa10_files:
        print("  Loading city scenario S-A10.csv ...")
        X, y = load_paired_dataset_features(sa10_files[0])
        X_all.append(X)
        y_all.append(y)

    if not X_all:
        print("[ERR] No training datasets found.")
        return

    X_train = np.vstack(X_all)
    y_train = np.concatenate(y_all)
    print(f"Total training windows: {len(X_train)} (feature dim: {X_train.shape[1]})")
    print(f"Target speed distribution: min={np.min(y_train):.2f} m/s, mean={np.mean(y_train):.2f} m/s, max={np.max(y_train):.2f} m/s")
    print(f"Stationary samples (speed < 0.5 m/s): {np.sum(y_train < 0.5)} ({(y_train < 0.5).mean()*100:.1f}%)")

    from sklearn.model_selection import train_test_split
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

    X_tr, X_val, y_tr, y_val = train_test_split(X_train, y_train, test_size=0.15, random_state=42)

    print("\nTraining Ensemble Speed Regressor ...")
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_val_s = scaler.transform(X_val)

    model = ExtraTreesRegressor(
        n_estimators=60,
        max_depth=16,
        min_samples_split=4,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_tr_s, y_tr)

    y_pred = model.predict(X_val_s)
    r2 = r2_score(y_val, y_pred)
    mae = mean_absolute_error(y_val, y_pred)
    rmse = math.sqrt(mean_squared_error(y_val, y_pred))

    print("\n" + "=" * 50)
    print("AI SPEED ESTIMATOR VALIDATION METRICS")
    print("=" * 50)
    print(f"  R^2 Score            : {r2:.4f}")
    print(f"  Mean Absolute Error  : {mae:.3f} m/s ({mae*3.6:.2f} km/h)")
    print(f"  Root Mean Sq Error   : {rmse:.3f} m/s ({rmse*3.6:.2f} km/h)")
    print("=" * 50)

    # Save pickle model
    estimator = AISpeedEstimator()
    estimator.model = model
    estimator.scaler = scaler
    estimator.is_trained = True
    pkl_path = MODELS_DIR / "speed_estimator.pkl"
    estimator.save(str(pkl_path))
    print(f"[OK] Saved Pickle model to: {pkl_path}")

    # Export to ONNX for edge engine deployment
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
        initial_type = [('float_input', FloatTensorType([None, X_train.shape[1]]))]
        
        # Pipeline with scaler + model
        from sklearn.pipeline import Pipeline
        pipe = Pipeline([('scaler', scaler), ('regressor', model)])
        onx = convert_sklearn(pipe, initial_types=initial_type, target_opset=15)
        
        edge_dir = PROJECT_ROOT / "edge_engine"
        edge_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = edge_dir / "speed_estimator.onnx"
        with open(onnx_path, "wb") as f:
            f.write(onx.SerializeToString())
        print(f"[OK] Saved ONNX model to: {onnx_path}")
    except Exception as e:
        print(f"[WARN] ONNX export note: {e}")


if __name__ == "__main__":
    main()
