"""
IO-VNBD Data Loader
====================
Read-only data loader for the IO-VNBD dataset.
Returns synchronized, typed pandas DataFrames.
Raw files are NEVER modified.

Usage:
    from preprocessing.data_loader import IOVNBDLoader
    loader = IOVNBDLoader("data/IO-VNBD/raw/")
    sequences = loader.discover_sequences()
    data = loader.load_sequence(sequences[0])
"""

import os
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass, field
import warnings

warnings.filterwarnings("ignore")

# ── Project root ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"


# ══════════════════════════════════════════════════════════════════════════════
# Data Containers
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SensorData:
    """Container for a single sensor stream."""
    name: str                      # sensor name (e.g., "accelerometer")
    data: pd.DataFrame             # time-indexed DataFrame
    fs_hz: float                   # estimated sampling frequency
    ts_col: str                    # timestamp column name
    units: str = ""
    coordinate_frame: str = ""
    source_file: str = ""
    n_samples: int = 0
    duration_sec: float = 0.0
    n_gaps: int = 0
    quality_flags: Dict = field(default_factory=dict)


@dataclass
class SequenceData:
    """Container for a complete sequence (all sensors for one drive)."""
    sequence_id: str
    source_dir: str
    accelerometer:  Optional[SensorData] = None
    gyroscope:      Optional[SensorData] = None
    magnetometer:   Optional[SensorData] = None
    orientation:    Optional[SensorData] = None
    gnss:           Optional[SensorData] = None
    speed:          Optional[SensorData] = None
    ground_truth:   Optional[SensorData] = None
    other:          Dict[str, SensorData] = field(default_factory=dict)
    metadata:       Dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"Sequence: {self.sequence_id}"]
        for attr in ["accelerometer", "gyroscope", "magnetometer",
                     "orientation", "gnss", "speed", "ground_truth"]:
            sd: Optional[SensorData] = getattr(self, attr)
            if sd is not None:
                lines.append(f"  {attr:15s}: {sd.n_samples:6d} samples @ {sd.fs_hz:.1f} Hz | "
                             f"duration={sd.duration_sec:.1f}s | gaps={sd.n_gaps}")
            else:
                lines.append(f"  {attr:15s}: NOT AVAILABLE")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Timestamp Utilities
# ══════════════════════════════════════════════════════════════════════════════

def normalize_timestamp(ts: pd.Series) -> Tuple[pd.Series, str]:
    """
    Normalize timestamps to seconds (float64).
    Returns (normalized_series, detected_unit_string).
    """
    ts_num = pd.to_numeric(ts, errors="coerce")
    median = ts_num.median()

    if pd.isna(median):
        return ts_num, "unknown"

    if median > 1e15:
        return ts_num / 1e6, "microseconds→seconds"
    elif median > 1e12:
        return ts_num / 1e3, "milliseconds→seconds"
    elif median > 1e9:
        return ts_num, "unix_seconds"
    elif median > 1e6:
        return ts_num / 1e3, "elapsed_ms→seconds"
    else:
        return ts_num, "elapsed_seconds"


def detect_sampling_frequency(ts_sec: pd.Series) -> Tuple[float, int, float]:
    """
    Returns (median_hz, n_gaps, duration_sec).
    A gap is any dt > 3× median dt.
    """
    ts_sorted = ts_sec.dropna().sort_values().reset_index(drop=True)
    if len(ts_sorted) < 2:
        return 0.0, 0, 0.0

    diffs = ts_sorted.diff().dropna()
    pos_diffs = diffs[diffs > 0]
    if pos_diffs.empty:
        return 0.0, 0, 0.0

    median_dt = float(pos_diffs.median())
    duration  = float(ts_sorted.iloc[-1] - ts_sorted.iloc[0])
    hz        = 1.0 / median_dt if median_dt > 0 else 0.0
    n_gaps    = int((diffs > 3 * median_dt).sum())

    return round(hz, 3), n_gaps, round(duration, 3)


# ══════════════════════════════════════════════════════════════════════════════
# CSV Reader
# ══════════════════════════════════════════════════════════════════════════════

def robust_read_csv(path: Path) -> Optional[pd.DataFrame]:
    """Read CSV with multiple fallback strategies."""
    strategies = [
        dict(sep=",",  header=0,    skipinitialspace=True, encoding="latin1"),
        dict(sep=",",  header=0,    skipinitialspace=True, encoding="utf-8", encoding_errors="replace"),
        dict(sep="\t", header=0,    skipinitialspace=True, encoding="latin1"),
        dict(sep=" ",  header=0,    skipinitialspace=True, encoding="latin1"),
        dict(sep=",",  header=None, skipinitialspace=True, encoding="latin1"),
        dict(sep="\t", header=None, skipinitialspace=True, encoding="latin1"),
    ]
    for kwargs in strategies:
        try:
            df = pd.read_csv(path, **kwargs)
            if df.shape[1] >= 2 and df.shape[0] >= 2:
                # Clean column names
                df.columns = [str(c).strip() for c in df.columns]
                return df
        except Exception:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Sensor Column Detection
# ══════════════════════════════════════════════════════════════════════════════

SENSOR_COL_PATTERNS: Dict[str, List[str]] = {
    "accelerometer": [
        "accelerometer x", "accelerometer y", "accelerometer z", "accelerometer",
        "acc_x", "acc_y", "acc_z", "accel_x", "accel_y", "accel_z",
        "linear_accel_x", "linear_accel_y", "linear_accel_z",
        "ax", "ay", "az", "x_acc", "y_acc", "z_acc",
    ],
    "gyroscope": [
        "gyroscope yaw", "gyroscope pitch", "gyroscope roll", "gyroscope",
        "gyro_x", "gyro_y", "gyro_z", "gyr_x", "gyr_y", "gyr_z",
        "rot_x", "rot_y", "rot_z", "ang_vel_x", "ang_vel_y", "ang_vel_z",
        "wx", "wy", "wz",
    ],
    "magnetometer": [
        "magnetic field x", "magnetic field y", "magnetic field z", "magnetic field",
        "mag_x", "mag_y", "mag_z", "magnet_x", "magnet_y", "magnet_z",
        "mx", "my", "mz",
    ],
    "orientation": [
        "orientation (yaw)", "orientation (pitch)", "orientation (roll", "orientation",
        "roll", "pitch", "yaw", "qw", "qx", "qy", "qz",
        "q0", "q1", "q2", "q3", "heading", "azimuth",
    ],
    "gnss": [
        "gps latitude", "gps longitude", "gps altitude", "gps accuracy",
        "latitude", "longitude", "altitude",
        "lat", "lon", "lng", "alt",
        "gps_lat", "gps_lon", "gnss_lat", "gnss_lon",
    ],
    "speed": [
        "gps speed", "speed", "velocity", "vel_x", "vel_y", "vel_z",
        "v_x", "v_y", "forward_vel", "wheel_speed", "odometer",
    ],
    "ground_truth": [
        "gps latitude", "gps longitude", "gps altitude",
        "gt_lat", "gt_lon", "gt_alt", "ref_lat", "ref_lon",
        "truth_x", "truth_y", "truth_z", "rtk_lat", "rtk_lon",
        "ground_truth_lat", "ground_truth_lon",
    ],
    "timestamp": [
        "time since start", "timestamp", "time", "ts", "epoch",
        "time_ms", "time_sec", "t", "unix_time",
    ],
}


def match_columns(df_cols: List[str],
                  patterns: List[str]) -> List[str]:
    """Find columns matching any pattern (case-insensitive partial match)."""
    matched = []
    cols_lower = {c.lower(): c for c in df_cols}
    for pat in patterns:
        for col_l, col_orig in cols_lower.items():
            if pat in col_l and col_orig not in matched:
                matched.append(col_orig)
    return matched


def detect_sensor_type(df: pd.DataFrame) -> Tuple[str, str]:
    """
    Detect the primary sensor type of a DataFrame.
    Returns (sensor_type, timestamp_column).
    """
    cols = list(df.columns)
    ts_col = None

    # Find timestamp column first
    ts_matches = match_columns(cols, SENSOR_COL_PATTERNS["timestamp"])
    if ts_matches:
        ts_col = ts_matches[0]

    # Check each sensor type
    best_sensor = "unknown"
    best_count  = 0
    for sensor, patterns in SENSOR_COL_PATTERNS.items():
        if sensor == "timestamp":
            continue
        matches = match_columns(cols, patterns)
        if len(matches) > best_count:
            best_count  = len(matches)
            best_sensor = sensor

    # Fallback: find ts_col from monotonic column
    if ts_col is None:
        for col in cols:
            try:
                vals = pd.to_numeric(df[col], errors="coerce").dropna()
                if len(vals) > 5 and vals.is_monotonic_increasing:
                    ts_col = col
                    break
            except Exception:
                pass

    return best_sensor, (ts_col or cols[0])


def build_sensor_data(df: pd.DataFrame, name: str,
                      ts_col: str, source_file: str = "") -> SensorData:
    """Build a SensorData object from a DataFrame."""
    ts_raw = df[ts_col]
    ts_sec, ts_unit = normalize_timestamp(ts_raw)
    fs_hz, n_gaps, duration = detect_sampling_frequency(ts_sec)

    # Set index to normalized time
    df_indexed = df.copy()
    df_indexed["_ts_sec"] = ts_sec.values
    df_indexed = df_indexed.set_index("_ts_sec").sort_index()

    return SensorData(
        name=name,
        data=df_indexed,
        fs_hz=fs_hz,
        ts_col=ts_col,
        source_file=source_file,
        n_samples=len(df_indexed),
        duration_sec=duration,
        n_gaps=n_gaps,
        units="",   # Will be filled by loader from metadata/docs
        coordinate_frame="device_frame",  # Default; refined later
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main Loader
# ══════════════════════════════════════════════════════════════════════════════

class IOVNBDLoader:
    """
    Read-only loader for the IO-VNBD dataset.
    Discovers sequences, loads sensor files, and returns SequenceData.
    """

    SENSOR_FILE_HINTS: Dict[str, List[str]] = {
        "accelerometer": ["accel", "accelerometer", "linear_accel", "imu_acc"],
        "gyroscope":     ["gyro", "gyroscope", "angular", "imu_gyro"],
        "magnetometer":  ["mag", "magnet", "compass"],
        "orientation":   ["orient", "quaternion", "euler", "attitude"],
        "gnss":          ["gps", "gnss", "location", "position"],
        "speed":         ["speed", "velocity", "odometer", "wheel"],
        "ground_truth":  ["ground_truth", "gt", "reference", "rtk", "truth"],
    }

    def __init__(self, raw_dir: Optional[str] = None):
        self.raw_dir = Path(raw_dir) if raw_dir else RAW_DATA_DIR
        if not self.raw_dir.exists():
            raise FileNotFoundError(f"Raw data directory not found: {self.raw_dir}")

    def discover_sequences(self) -> List[str]:
        """
        Discover all sequences (drive sessions) in the dataset.
        Returns list of sequence IDs (directory names or unique identifiers).
        """
        sequences = []

        # Strategy 1: Each subdirectory is a sequence
        subdirs = [d for d in sorted(self.raw_dir.iterdir())
                   if d.is_dir() and not d.name.startswith(".")]
        if subdirs:
            for d in subdirs:
                data_files = list(d.glob("*.csv")) + list(d.glob("*.txt"))
                if data_files:
                    sequences.append(str(d.relative_to(self.raw_dir)))
            if sequences:
                return sequences

        # Strategy 2: Flat structure — all CSV files are sequences
        csv_files = sorted(self.raw_dir.glob("*.csv"))
        if csv_files:
            return [f.stem for f in csv_files]

        # Strategy 3: Look two levels deep
        for child in sorted(self.raw_dir.iterdir()):
            if child.is_dir():
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir():
                        data_files = list(grandchild.glob("*.csv")) + \
                                     list(grandchild.glob("*.txt"))
                        if data_files:
                            sequences.append(
                                str(grandchild.relative_to(self.raw_dir))
                            )
        if sequences:
            return sequences

        # Strategy 4: Recursive scan for all CSV files (IO-VNBD dataset format)
        all_csvs = sorted(self.raw_dir.rglob("*.csv"))
        if all_csvs:
            return sorted(list({f.stem for f in all_csvs if not f.name.startswith(".")}))

        return sequences

    def _find_sensor_file(self, seq_dir: Path,
                          sensor: str) -> Optional[Path]:
        """Find the file for a given sensor type in a sequence directory."""
        hints = self.SENSOR_FILE_HINTS.get(sensor, [sensor])
        candidates = []

        for ext in ["*.csv", "*.txt", "*.tsv", "*.dat"]:
            for f in seq_dir.glob(ext):
                name_l = f.stem.lower()
                if any(h in name_l for h in hints):
                    candidates.append(f)

        if candidates:
            # Prefer exact name matches
            for f in candidates:
                if sensor in f.stem.lower():
                    return f
            return candidates[0]

        return None

    def _load_sensor(self, seq_dir: Path,
                     sensor_name: str) -> Optional[SensorData]:
        """Load a single sensor from its file."""
        fpath = self._find_sensor_file(seq_dir, sensor_name)
        if fpath is None:
            return None

        df = robust_read_csv(fpath)
        if df is None or df.empty:
            return None

        # Detect sensor type and timestamp column
        detected_sensor, ts_col = detect_sensor_type(df)
        return build_sensor_data(df, sensor_name, ts_col,
                                 source_file=str(fpath))

    def _try_combined_file(self, seq_dir: Path) -> Optional[pd.DataFrame]:
        """Some sequences have a single combined CSV."""
        combined_hints = ["combined", "all", "data", "sensor", "log", "recording"]
        for ext in ["*.csv", "*.txt"]:
            for f in sorted(seq_dir.glob(ext)):
                if any(h in f.stem.lower() for h in combined_hints):
                    df = robust_read_csv(f)
                    if df is not None and df.shape[1] >= 5:
                        return df
        return None

    def load_sequence(self, sequence_id: str) -> SequenceData:
        """
        Load all sensor data for a given sequence.
        Returns a SequenceData object.
        """
        seq = SequenceData(
            sequence_id=sequence_id,
            source_dir=str(self.raw_dir),
        )

        # Check if sequence_id matches a CSV file directly or in subfolders
        csv_matches = list(self.raw_dir.rglob(f"{sequence_id}.csv")) + \
                      list(self.raw_dir.rglob(sequence_id))
        if csv_matches and csv_matches[0].is_file():
            target_csv = csv_matches[0]
            df = robust_read_csv(target_csv)
            if df is not None:
                # Find timestamp column
                ts_cols = [c for c in df.columns if any(k in c.lower() for k in ["time", "timestamp", "since", "epoch"])]
                ts_col = ts_cols[0] if ts_cols else df.columns[0]

                # Decompose combined columns into typed SensorData instances
                for sensor_type, patterns in SENSOR_COL_PATTERNS.items():
                    if sensor_type == "timestamp":
                        continue
                    matching_cols = match_columns(list(df.columns), patterns)
                    matching_cols = [c for c in matching_cols if c != ts_col]
                    if matching_cols:
                        sub_df = df[[ts_col] + matching_cols].copy()
                        sd = build_sensor_data(sub_df, sensor_type, ts_col, source_file=str(target_csv))
                        setattr(seq, sensor_type, sd)

                seq.metadata = {
                    "sequence_id": sequence_id,
                    "source_file": str(target_csv),
                    "sensors_found": [s for s in ["accelerometer","gyroscope","magnetometer","orientation","gnss","speed","ground_truth"]
                                      if getattr(seq, s) is not None],
                    "total_rows": len(df),
                }
                return seq

        seq_path_rel = Path(sequence_id)
        seq_dir = self.raw_dir / seq_path_rel
        if not seq_dir.exists():
            seq_dir = self.raw_dir

        seq.source_dir = str(seq_dir)

        # Try per-sensor files
        for sensor in ["accelerometer", "gyroscope", "magnetometer",
                        "orientation", "gnss", "speed", "ground_truth"]:
            sd = self._load_sensor(seq_dir, sensor)
            setattr(seq, sensor, sd)

        # Try combined file if individual files failed
        if (seq.accelerometer is None and seq.gyroscope is None
                and seq.gnss is None):
            combined = self._try_combined_file(seq_dir)
            if combined is not None:
                detected, ts_col = detect_sensor_type(combined)
                sd = build_sensor_data(combined, "combined", ts_col,
                                       source_file=str(seq_dir))
                seq.other["combined"] = sd

        # Populate metadata
        seq.metadata = {
            "sequence_id":   sequence_id,
            "source_dir":    str(seq_dir),
            "sensors_found": [s for s in ["accelerometer","gyroscope",
                                          "magnetometer","orientation",
                                          "gnss","speed","ground_truth"]
                               if getattr(seq, s) is not None],
        }

        return seq

    def load_all_sequences(self) -> List[SequenceData]:
        """Load all discovered sequences."""
        seq_ids = self.discover_sequences()
        results = []
        for sid in seq_ids:
            try:
                results.append(self.load_sequence(sid))
            except Exception as e:
                print(f"[WARN] Failed to load sequence {sid}: {e}")
        return results

    def get_synchronized_dataframe(self, seq: SequenceData,
                                   resample: bool = False) -> pd.DataFrame:
        """
        Return a merged DataFrame with all available sensors.
        Uses IMU timestamps as the primary index.
        GNSS is forward-filled (not interpolated) to IMU grid.

        NOTE: This does NOT modify raw files. Returns new DataFrame only.
        """
        frames = {}

        # Primary: IMU (accelerometer)
        primary = seq.accelerometer or seq.gyroscope
        if primary is None and seq.other:
            primary = list(seq.other.values())[0]
        if primary is None:
            raise ValueError(f"No IMU data in sequence: {seq.sequence_id}")

        primary_ts = primary.data.index

        def resample_to_primary(sd: SensorData, method: str = "ffill") -> pd.DataFrame:
            """Align a sensor to the primary IMU timestamps."""
            # Merge on nearest timestamp
            df_resampled = (
                sd.data.reindex(
                    primary_ts.union(sd.data.index)
                )
                .sort_index()
            )
            if method == "ffill":
                df_resampled = df_resampled.ffill()
            df_resampled = df_resampled.reindex(primary_ts)
            return df_resampled

        # Build merged frame column by column
        merged = primary.data.copy()
        merged.columns = [f"imu_{c}" if c != primary.ts_col else c
                          for c in merged.columns]

        if seq.gyroscope:
            gdf = resample_to_primary(seq.gyroscope)
            gdf.columns = [f"gyro_{c}" for c in gdf.columns]
            merged = merged.join(gdf, how="left", rsuffix="_gyro")

        if seq.gnss:
            # GNSS at lower rate — forward fill with interpolated flag
            gps_df = resample_to_primary(seq.gnss, method="ffill")
            gps_df.columns = [f"gnss_{c}" for c in gps_df.columns]
            merged = merged.join(gps_df, how="left", rsuffix="_gnss")

        if seq.ground_truth:
            gt_df = resample_to_primary(seq.ground_truth, method="ffill")
            gt_df.columns = [f"gt_{c}" for c in gt_df.columns]
            merged = merged.join(gt_df, how="left", rsuffix="_gt")

        return merged
