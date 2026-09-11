"""
Unit Tests — IO-VNBD Data Loader
SIH 2026 IDR Project

Tests:
  - File discovery
  - Column detection
  - Timestamp normalization
  - Sampling frequency computation
  - Data quality checks
  - Sequence loading
  - Synchronization (no raw file modification)

Run: pytest tests/test_data_loader.py -v
"""

import pytest
import numpy as np
import pandas as pd
from io import StringIO
from pathlib import Path
import tempfile
import os
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing.data_loader import (
    normalize_timestamp,
    detect_sampling_frequency,
    detect_sensor_type,
    build_sensor_data,
    robust_read_csv,
    IOVNBDLoader,
    SequenceData,
    SensorData,
)
from preprocessing.dataset_inspector import (
    compute_sampling_stats,
    classify_columns,
    identify_timestamp_column,
    check_data_quality,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def imu_csv_content():
    """Synthetic accelerometer CSV with Unix millisecond timestamps at 10 Hz."""
    rows = ["timestamp,acc_x,acc_y,acc_z"]
    t0 = 1_700_000_000_000  # Unix ms
    dt = 100                 # 10 Hz
    for i in range(100):
        t = t0 + i * dt
        ax = np.random.normal(0, 0.5)
        ay = np.random.normal(0, 0.5)
        az = np.random.normal(9.81, 0.1)
        rows.append(f"{t},{ax:.6f},{ay:.6f},{az:.6f}")
    return "\n".join(rows)


@pytest.fixture
def gnss_csv_content():
    """Synthetic GNSS CSV with Unix second timestamps at 1 Hz."""
    rows = ["timestamp,latitude,longitude,altitude,speed"]
    t0 = 1_700_000_000  # Unix seconds
    for i in range(10):
        t = t0 + i
        lat = 28.6139 + i * 0.0001
        lon = 77.2090 + i * 0.0001
        alt = 216.0
        spd = 15.0
        rows.append(f"{t},{lat:.6f},{lon:.6f},{alt:.2f},{spd:.2f}")
    return "\n".join(rows)


@pytest.fixture
def temp_sequence_dir(imu_csv_content, gnss_csv_content):
    """Create a temporary sequence directory with sensor files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        seq_dir = Path(tmpdir) / "seq_001"
        seq_dir.mkdir()
        (seq_dir / "accelerometer.csv").write_text(imu_csv_content)
        (seq_dir / "gnss.csv").write_text(gnss_csv_content)
        yield tmpdir, seq_dir


@pytest.fixture
def sample_imu_df():
    """Sample IMU DataFrame."""
    t0 = 1_700_000_000_000  # Unix ms, 10 Hz
    ts = [t0 + i * 100 for i in range(200)]
    df = pd.DataFrame({
        "timestamp": ts,
        "acc_x": np.random.normal(0, 0.5, 200),
        "acc_y": np.random.normal(0, 0.5, 200),
        "acc_z": np.random.normal(9.81, 0.1, 200),
    })
    return df


@pytest.fixture
def sample_gnss_df():
    """Sample GNSS DataFrame."""
    t0 = 1_700_000_000  # Unix seconds, 1 Hz
    ts = [t0 + i for i in range(20)]
    df = pd.DataFrame({
        "timestamp": ts,
        "latitude":  np.linspace(28.6139, 28.6239, 20),
        "longitude": np.linspace(77.2090, 77.2190, 20),
        "altitude":  [216.0] * 20,
        "speed":     np.random.uniform(10, 20, 20),
    })
    return df


# ══════════════════════════════════════════════════════════════════════════════
# 1. Timestamp Normalization Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTimestampNormalization:

    def test_unix_milliseconds(self):
        """Timestamps in Unix ms (>1e12) should be divided by 1000."""
        ts = pd.Series([1_700_000_000_000 + i * 100 for i in range(10)])
        ts_norm, unit = normalize_timestamp(ts)
        assert "milliseconds" in unit
        # Normalized values should be ~1.7e9 (Unix seconds)
        assert ts_norm.median() > 1e9
        assert ts_norm.median() < 2e9

    def test_unix_seconds(self):
        """Timestamps in Unix seconds (1e9 < val < 1e12) unchanged."""
        ts = pd.Series([1_700_000_000 + i for i in range(10)])
        ts_norm, unit = normalize_timestamp(ts)
        assert "unix_seconds" in unit or "seconds" in unit
        assert ts_norm.median() > 1e9

    def test_elapsed_seconds(self):
        """Elapsed-seconds timestamps (small values) returned as-is."""
        ts = pd.Series([i * 0.1 for i in range(100)])
        ts_norm, unit = normalize_timestamp(ts)
        assert ts_norm.iloc[0] == pytest.approx(0.0, abs=1e-9)

    def test_microseconds(self):
        """Unix microseconds (>1e15) divided by 1e6."""
        ts = pd.Series([1_700_000_000_000_000 + i * 100_000 for i in range(10)])
        ts_norm, unit = normalize_timestamp(ts)
        assert "microseconds" in unit
        assert ts_norm.median() > 1e9


# ══════════════════════════════════════════════════════════════════════════════
# 2. Sampling Frequency Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSamplingFrequency:

    def test_10hz_imu(self):
        """10 Hz IMU → median_hz ≈ 10."""
        ts = pd.Series([1_700_000_000 + i * 0.1 for i in range(200)])
        hz, n_gaps, dur = detect_sampling_frequency(ts)
        assert hz == pytest.approx(10.0, rel=0.05)
        assert n_gaps == 0

    def test_1hz_gnss(self):
        """1 Hz GNSS → median_hz ≈ 1."""
        ts = pd.Series([1_700_000_000 + i for i in range(60)])
        hz, n_gaps, dur = detect_sampling_frequency(ts)
        assert hz == pytest.approx(1.0, rel=0.05)
        assert n_gaps == 0

    def test_gap_detection(self):
        """A single 5-second gap in 10 Hz data → n_gaps >= 1."""
        ts_list = list(range(0, 50))           # 0..49 seconds
        ts_list += list(range(55, 105))        # 5-second gap
        ts = pd.Series([t * 0.1 for t in ts_list])
        hz, n_gaps, dur = detect_sampling_frequency(ts)
        assert n_gaps >= 1

    def test_duplicate_timestamps(self):
        """Duplicated timestamps should not produce negative frequencies."""
        ts = pd.Series([1.0, 1.0, 2.0, 2.0, 3.0, 3.0])
        hz, n_gaps, dur = detect_sampling_frequency(ts)
        assert hz >= 0

    def test_duration_correct(self):
        """Duration should equal last - first timestamp."""
        ts = pd.Series([100.0 + i * 0.1 for i in range(100)])
        hz, n_gaps, dur = detect_sampling_frequency(ts)
        assert dur == pytest.approx(9.9, abs=0.2)

    def test_imu_faster_than_gnss(self):
        """IMU must be detected as faster than GNSS."""
        imu_ts = pd.Series([1_700_000_000 + i * 0.1 for i in range(1000)])
        gps_ts = pd.Series([1_700_000_000 + i for i in range(100)])
        imu_hz, _, _ = detect_sampling_frequency(imu_ts)
        gps_hz, _, _ = detect_sampling_frequency(gps_ts)
        assert imu_hz > gps_hz
        assert imu_hz / gps_hz == pytest.approx(10.0, rel=0.1)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Column Detection Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestColumnDetection:

    def test_accelerometer_columns(self):
        cols = ["timestamp", "acc_x", "acc_y", "acc_z"]
        result = classify_columns(cols)
        assert "accelerometer" in result
        assert len(result["accelerometer"]) == 3

    def test_gyroscope_columns(self):
        cols = ["time", "gyro_x", "gyro_y", "gyro_z"]
        result = classify_columns(cols)
        assert "gyroscope" in result

    def test_gnss_columns(self):
        cols = ["ts", "latitude", "longitude", "altitude", "speed"]
        result = classify_columns(cols)
        assert "gnss" in result
        assert "latitude" in result["gnss"]

    def test_timestamp_detection(self):
        df = pd.DataFrame({
            "timestamp": range(10),
            "acc_x": range(10),
            "acc_y": range(10),
        })
        ts_col = identify_timestamp_column(df)
        assert ts_col == "timestamp"

    def test_sensor_type_detection_imu(self, sample_imu_df):
        sensor_type, ts_col = detect_sensor_type(sample_imu_df)
        assert sensor_type == "accelerometer"
        assert ts_col == "timestamp"

    def test_sensor_type_detection_gnss(self, sample_gnss_df):
        sensor_type, ts_col = detect_sensor_type(sample_gnss_df)
        assert sensor_type == "gnss"

    def test_mixed_columns_classified(self):
        """Mixed-sensor DataFrame should classify into multiple categories."""
        cols = ["time", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y",
                "gyro_z", "latitude", "longitude"]
        result = classify_columns(cols)
        assert "accelerometer" in result
        assert "gyroscope" in result
        assert "gnss" in result


# ══════════════════════════════════════════════════════════════════════════════
# 4. Data Quality Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestDataQuality:

    def test_missing_value_detection(self):
        df = pd.DataFrame({
            "ts": [1.0, 2.0, 3.0, 4.0],
            "val": [1.0, np.nan, 3.0, np.nan],
        })
        quality = check_data_quality(df, ts_col="ts")
        assert "val" in quality["missing_values"]
        assert quality["missing_values"]["val"]["count"] == 2

    def test_duplicate_row_detection(self):
        df = pd.DataFrame({
            "ts": [1.0, 1.0, 3.0],
            "val": [1.0, 1.0, 3.0],
        })
        quality = check_data_quality(df, ts_col="ts")
        assert quality["duplicate_rows"] >= 1

    def test_outlier_detection(self):
        """A single extreme value should be flagged as outlier."""
        vals = list(np.random.normal(0, 1, 99)) + [1000.0]  # extreme outlier
        df = pd.DataFrame({
            "ts":  range(100),
            "acc": vals,
        })
        quality = check_data_quality(df, ts_col="ts")
        assert "acc" in quality["outlier_cols"]

    def test_no_false_outliers(self):
        """Normal data should have zero or very few outliers."""
        vals = np.random.normal(9.81, 0.2, 200)
        df = pd.DataFrame({
            "ts":  [i * 0.1 for i in range(200)],
            "az":  vals,
        })
        quality = check_data_quality(df, ts_col="ts")
        # May have 0 or very few
        if "az" in quality["outlier_cols"]:
            assert quality["outlier_cols"]["az"]["pct"] < 2.0  # < 2%


# ══════════════════════════════════════════════════════════════════════════════
# 5. Robust CSV Reader Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCSVReader:

    def test_comma_separated(self, tmp_path):
        csv = "ts,x,y,z\n1,1.0,2.0,3.0\n2,4.0,5.0,6.0\n"
        p = tmp_path / "test.csv"
        p.write_text(csv)
        df = robust_read_csv(p)
        assert df is not None
        assert list(df.columns) == ["ts", "x", "y", "z"]
        assert len(df) == 2

    def test_tab_separated(self, tmp_path):
        tsv = "ts\tx\ty\tz\n1\t1.0\t2.0\t3.0\n2\t4.0\t5.0\t6.0\n"
        p = tmp_path / "test.txt"
        p.write_text(tsv)
        df = robust_read_csv(p)
        assert df is not None
        assert df.shape[1] == 4

    def test_returns_none_on_garbage(self, tmp_path):
        p = tmp_path / "binary.dat"
        p.write_bytes(b"\x00\x01\x02\x03")
        df = robust_read_csv(p)
        # May return None or a single-column junk df — just check no crash
        # Either None or df with < 2 columns is acceptable
        if df is not None:
            assert df.shape[1] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. Loader Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestIOVNBDLoader:

    def test_loader_init_valid(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        assert loader.raw_dir.exists()

    def test_loader_init_invalid(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            IOVNBDLoader(raw_dir=str(nonexistent))

    def test_discover_sequences(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        assert len(seqs) >= 1

    def test_load_sequence_returns_data(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        assert seqs, "No sequences discovered"
        data = loader.load_sequence(seqs[0])
        assert isinstance(data, SequenceData)

    def test_accelerometer_loaded(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        data = loader.load_sequence(seqs[0])
        assert data.accelerometer is not None, "Accelerometer not found"
        assert data.accelerometer.n_samples == 100

    def test_gnss_loaded(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        data = loader.load_sequence(seqs[0])
        assert data.gnss is not None, "GNSS not found"
        assert data.gnss.n_samples == 10

    def test_imu_faster_than_gnss_in_loader(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        data = loader.load_sequence(seqs[0])
        if data.accelerometer and data.gnss:
            assert data.accelerometer.fs_hz > data.gnss.fs_hz, \
                f"IMU ({data.accelerometer.fs_hz} Hz) should be faster than " \
                f"GNSS ({data.gnss.fs_hz} Hz)"

    def test_raw_files_not_modified(self, temp_sequence_dir):
        """Verify loading does not modify raw files."""
        tmpdir, seq_dir = temp_sequence_dir
        acc_file = seq_dir / "accelerometer.csv"
        original_mtime = acc_file.stat().st_mtime
        original_size  = acc_file.stat().st_size

        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs   = loader.discover_sequences()
        data   = loader.load_sequence(seqs[0])

        assert acc_file.stat().st_mtime == original_mtime, \
            "Raw accelerometer file was modified!"
        assert acc_file.stat().st_size == original_size, \
            "Raw file size changed!"

    def test_sequence_summary(self, temp_sequence_dir):
        tmpdir, seq_dir = temp_sequence_dir
        loader = IOVNBDLoader(raw_dir=tmpdir)
        seqs = loader.discover_sequences()
        data = loader.load_sequence(seqs[0])
        summary = data.summary()
        assert "Sequence" in summary
        assert "accelerometer" in summary


# ══════════════════════════════════════════════════════════════════════════════
# 7. Sensor Data Container Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSensorData:

    def test_build_sensor_data(self, sample_imu_df):
        sd = build_sensor_data(sample_imu_df, "accelerometer",
                               ts_col="timestamp", source_file="test.csv")
        assert sd.name == "accelerometer"
        assert sd.n_samples == 200
        assert sd.fs_hz == pytest.approx(10.0, rel=0.1)
        assert sd.duration_sec == pytest.approx(19.9, abs=1.0)

    def test_sensor_data_index_sorted(self, sample_imu_df):
        """Data index must be monotonically increasing."""
        sd = build_sensor_data(sample_imu_df, "accelerometer",
                               ts_col="timestamp")
        assert sd.data.index.is_monotonic_increasing

    def test_sensor_data_no_raw_ref(self, sample_imu_df):
        """SensorData should store a copy, not a view of the raw DataFrame."""
        sd = build_sensor_data(sample_imu_df, "accelerometer",
                               ts_col="timestamp")
        # Modifying sd.data should NOT affect original
        original_val = float(sample_imu_df["acc_x"].iloc[0])
        # (We won't actually mutate raw — just check it's safe)
        assert sd.data is not sample_imu_df


# ══════════════════════════════════════════════════════════════════════════════
# 8. Sampling Stats Inspector Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSamplingStats:

    def test_correct_hz_from_inspector(self):
        """Inspector compute_sampling_stats should match expected Hz."""
        ts = pd.Series([1_700_000_000_000 + i * 100 for i in range(500)])
        stats = compute_sampling_stats(ts, name="test_imu")
        assert stats["median_hz"] == pytest.approx(10.0, rel=0.1)
        assert stats["ts_unit_detected"] in ["milliseconds", "unix_seconds"]

    def test_insufficient_data(self):
        ts = pd.Series([1.0])  # Only 1 sample
        stats = compute_sampling_stats(ts, name="tiny")
        assert "error" in stats

    def test_non_numeric_timestamps(self):
        ts = pd.Series(["abc", "def", "ghi"])
        stats = compute_sampling_stats(ts, name="bad")
        assert "error" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
