"""
IO-VNBD Dataset Inspector
===========================
Performs deep inspection of the IO-VNBD dataset raw files.
Reports:
  - Complete folder/file structure
  - Sensor fields & column names
  - Sampling frequencies (computed from timestamps)
  - IMU vs GNSS frequency comparison
  - Timestamp format and synchronization
  - Units & coordinate frames
  - Missing values, duplicates, outliers
  - Dataset duration and sequence count

IMPORTANT: This script NEVER modifies raw files.
           All analysis is read-only.

Usage:
    python preprocessing/dataset_inspector.py --data-dir data/IO-VNBD/raw/
"""

import os
import sys
import json
import warnings
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, List, Tuple, Any

warnings.filterwarnings("ignore", category=pd.errors.ParserWarning)

# ── Project root ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"
DOCS_DIR     = PROJECT_ROOT / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# ── Sensor keyword mapping ──────────────────────────────────────────────────────
SENSOR_KEYWORDS = {
    "accelerometer": ["accel", "acc", "linear_accel", "linear_acceleration"],
    "gyroscope":     ["gyro", "rotation", "angular"],
    "magnetometer":  ["magnet", "mag", "compass"],
    "orientation":   ["orient", "quaternion", "euler", "attitude", "rotation_vec"],
    "gnss":          ["gps", "gnss", "location", "position", "lat", "lon", "latitude"],
    "speed":         ["speed", "velocity", "odometer", "wheel"],
    "barometer":     ["baro", "pressure", "altitude"],
    "ground_truth":  ["ground_truth", "reference", "gt", "truth", "rtk", "imu_gt"],
}

# ── Units reference (from IO-VNBD paper) ─────────────────────────────────────
KNOWN_UNITS = {
    "acc":   "m/s²",
    "gyro":  "rad/s",
    "mag":   "µT (microtesla)",
    "gps":   "degrees (lat/lon), m (altitude), m/s (speed)",
    "speed": "m/s",
    "time":  "Unix epoch seconds or ms",
}


# ══════════════════════════════════════════════════════════════════════════════
# Helper: safe CSV reader
# ══════════════════════════════════════════════════════════════════════════════

def try_read_csv(path: Path, nrows: int = 5000) -> Optional[pd.DataFrame]:
    """Attempt multiple CSV reading strategies. Returns None on failure."""
    strategies = [
        dict(sep=",",  header=0),
        dict(sep="\t", header=0),
        dict(sep=" ",  header=0, skipinitialspace=True),
        dict(sep=",",  header=None),
        dict(sep="\t", header=None),
    ]
    for kwargs in strategies:
        try:
            df = pd.read_csv(path, nrows=nrows, **kwargs)
            if df.shape[1] >= 2:    # must have at least 2 columns
                return df
        except Exception:
            continue
    return None


def try_read_txt(path: Path) -> Optional[str]:
    """Read text file content."""
    for enc in ["utf-8", "latin-1", "cp1252"]:
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Sampling Frequency Analyser
# ══════════════════════════════════════════════════════════════════════════════

def compute_sampling_stats(timestamps: pd.Series, name: str = "") -> Dict[str, Any]:
    """
    Compute sampling frequency statistics from a timestamp column.
    Handles: Unix seconds, Unix milliseconds, Unix microseconds,
             elapsed seconds/ms.
    """
    ts = timestamps.dropna().reset_index(drop=True)
    if len(ts) < 2:
        return {"error": "insufficient data"}

    ts_num = pd.to_numeric(ts, errors="coerce").dropna()
    if ts_num.empty:
        return {"error": "non-numeric timestamps"}

    # Detect unit: if values > 1e12 → ms, if > 1e15 → µs, else → seconds
    median_val = ts_num.median()
    if median_val > 1e15:
        ts_sec = ts_num / 1e6
        ts_unit = "microseconds"
    elif median_val > 1e12:
        ts_sec = ts_num / 1e3
        ts_unit = "milliseconds"
    elif median_val > 1e9:
        ts_sec = ts_num
        ts_unit = "unix_seconds"
    else:
        # Likely elapsed time in seconds or ms
        if ts_num.max() < 1e6:
            ts_sec = ts_num
            ts_unit = "elapsed_seconds"
        else:
            ts_sec = ts_num / 1e3
            ts_unit = "elapsed_ms"

    diffs = ts_sec.diff().dropna()
    diffs_pos = diffs[diffs > 0]

    if diffs_pos.empty:
        return {"error": "no positive time differences"}

    median_dt  = float(diffs_pos.median())
    mean_dt    = float(diffs_pos.mean())
    std_dt     = float(diffs_pos.std())
    min_dt     = float(diffs_pos.min())
    max_dt     = float(diffs_pos.max())

    median_hz  = 1.0 / median_dt if median_dt > 0 else 0
    duration   = float(ts_sec.max() - ts_sec.min())
    n_samples  = len(ts_sec)

    # Gaps: dt > 3× median
    gap_threshold = 3 * median_dt
    gaps = diffs[diffs > gap_threshold]
    n_duplicates = int((diffs == 0).sum())

    return {
        "sensor_name":      name,
        "n_samples":        n_samples,
        "duration_sec":     round(duration, 3),
        "ts_unit_detected": ts_unit,
        "median_dt_sec":    round(median_dt,  6),
        "mean_dt_sec":      round(mean_dt,    6),
        "std_dt_sec":       round(std_dt,     6),
        "min_dt_sec":       round(min_dt,     6),
        "max_dt_sec":       round(max_dt,     6),
        "median_hz":        round(median_hz,  3),
        "n_gaps":           int(len(gaps)),
        "n_duplicates":     n_duplicates,
        "gap_threshold_sec":round(gap_threshold, 6),
        "max_gap_sec":      round(float(gaps.max()), 3) if not gaps.empty else 0.0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Column Classifier
# ══════════════════════════════════════════════════════════════════════════════

def classify_columns(cols: List[str]) -> Dict[str, List[str]]:
    """Guess sensor type from column names."""
    classification = defaultdict(list)
    for col in cols:
        col_l = col.lower()
        matched = False
        for sensor, keywords in SENSOR_KEYWORDS.items():
            if any(kw in col_l for kw in keywords):
                classification[sensor].append(col)
                matched = True
        if not matched:
            if any(t in col_l for t in ["time", "timestamp", "ts", "epoch", "sec", "ms"]):
                classification["timestamp"].append(col)
            else:
                classification["unknown"].append(col)
    return dict(classification)


def identify_timestamp_column(df: pd.DataFrame) -> Optional[str]:
    """Heuristically identify the timestamp column."""
    for col in df.columns:
        col_l = str(col).lower()
        if any(t in col_l for t in ["time", "timestamp", "ts", "epoch"]):
            return col
    # Fallback: first numeric column that looks monotonic
    for col in df.columns:
        try:
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(vals) > 10 and vals.is_monotonic_increasing:
                return col
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Data Quality Checker
# ══════════════════════════════════════════════════════════════════════════════

def check_data_quality(df: pd.DataFrame, ts_col: Optional[str]) -> Dict[str, Any]:
    """Run data-quality checks on a DataFrame."""
    total_rows = len(df)
    quality = {
        "total_rows":       total_rows,
        "total_cols":       len(df.columns),
        "missing_values":   {},
        "outlier_cols":     {},
        "duplicate_rows":   int(df.duplicated().sum()),
        "all_null_cols":    [],
        "constant_cols":    [],
    }

    for col in df.columns:
        n_null = int(df[col].isna().sum())
        if n_null > 0:
            quality["missing_values"][col] = {
                "count": n_null,
                "pct":   round(100 * n_null / total_rows, 2)
            }
        if n_null == total_rows:
            quality["all_null_cols"].append(col)

        try:
            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(numeric) > 10:
                if numeric.std() == 0:
                    quality["constant_cols"].append(col)
                else:
                    # IQR outlier detection
                    q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
                    iqr = q3 - q1
                    if iqr > 0:
                        lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                        n_outliers = int(((numeric < lo) | (numeric > hi)).sum())
                        if n_outliers > 0:
                            quality["outlier_cols"][col] = {
                                "n_outliers": n_outliers,
                                "pct": round(100 * n_outliers / len(numeric), 2),
                                "lower_bound": round(float(lo), 4),
                                "upper_bound": round(float(hi), 4),
                            }
        except Exception:
            pass

    return quality


def compute_column_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute per-column statistics."""
    stats = {}
    for col in df.columns:
        try:
            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(numeric) > 0:
                stats[col] = {
                    "dtype":  str(df[col].dtype),
                    "count":  int(len(numeric)),
                    "mean":   round(float(numeric.mean()), 6),
                    "std":    round(float(numeric.std()),  6),
                    "min":    round(float(numeric.min()),  6),
                    "max":    round(float(numeric.max()),  6),
                    "p25":    round(float(numeric.quantile(0.25)), 6),
                    "p75":    round(float(numeric.quantile(0.75)), 6),
                }
            else:
                stats[col] = {"dtype": str(df[col].dtype), "non_numeric": True}
        except Exception as e:
            stats[col] = {"error": str(e)}
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# File Inspector
# ══════════════════════════════════════════════════════════════════════════════

def inspect_file(path: Path) -> Dict[str, Any]:
    """Full inspection of a single data file."""
    result = {
        "file":       str(path.name),
        "path":       str(path),
        "size_bytes": path.stat().st_size,
        "extension":  path.suffix.lower(),
    }

    # Read README/text files
    if path.suffix.lower() in [".md", ".txt", ".rst"]:
        content = try_read_txt(path)
        result["type"]    = "text"
        result["content"] = content[:3000] if content else "unreadable"
        return result

    # Skip binary/non-tabular
    if path.suffix.lower() in [".jpg", ".png", ".pdf", ".zip", ".mat"]:
        result["type"] = "binary"
        return result

    # Try reading as tabular data
    if path.suffix.lower() in [".csv", ".txt", ".tsv", ".dat", ".log"]:
        df = try_read_csv(path)
        if df is None:
            result["type"]  = "unreadable"
            result["error"] = "could not parse as CSV/TSV"
            return result

        result["type"]    = "tabular"
        result["columns"] = list(df.columns)
        result["n_rows_sampled"] = len(df)
        result["column_types"]  = {c: str(df[c].dtype) for c in df.columns}

        # Classify columns
        result["column_classification"] = classify_columns([str(c) for c in df.columns])

        # Find timestamp column
        ts_col = identify_timestamp_column(df)
        result["timestamp_column"] = ts_col

        # Sampling stats
        if ts_col is not None:
            result["sampling_stats"] = compute_sampling_stats(
                df[ts_col], name=path.stem
            )

        # Column statistics
        result["column_stats"] = compute_column_stats(df)

        # Data quality
        result["data_quality"] = check_data_quality(df, ts_col)

        # Sample rows
        result["sample_rows"] = df.head(3).to_dict(orient="records")

        return result

    result["type"] = "unknown_format"
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Repository Walker
# ══════════════════════════════════════════════════════════════════════════════

def walk_dataset(raw_dir: Path) -> Dict[str, Any]:
    """Walk the entire dataset directory and inspect all files."""
    report = {
        "inspection_time": datetime.now().isoformat(),
        "raw_dir": str(raw_dir),
        "directories": [],
        "files": [],
        "sensor_summary": defaultdict(list),
    }

    all_files = []
    for root, dirs, files in os.walk(raw_dir):
        # Skip hidden / git directories
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        root_path = Path(root)
        rel_root  = root_path.relative_to(raw_dir)
        if str(rel_root) != ".":
            report["directories"].append(str(rel_root))

        for fname in sorted(files):
            fpath = root_path / fname
            if fname.startswith("."):
                continue
            all_files.append(fpath)

    print(f"\n[INFO] Found {len(all_files)} files to inspect.")

    for i, fpath in enumerate(all_files):
        rel_path = fpath.relative_to(raw_dir)
        print(f"  [{i+1:3d}/{len(all_files)}] Inspecting: {rel_path} "
              f"({fpath.stat().st_size / 1024:.1f} KB)", end="\r")

        file_info = inspect_file(fpath)
        file_info["relative_path"] = str(rel_path)
        report["files"].append(file_info)

        # Aggregate sensor types
        if file_info.get("type") == "tabular":
            for sensor_type, cols in file_info.get("column_classification", {}).items():
                if sensor_type not in ("timestamp", "unknown") and cols:
                    report["sensor_summary"][sensor_type].append({
                        "file":    str(rel_path),
                        "columns": cols,
                        "hz":      file_info.get("sampling_stats", {}).get("median_hz"),
                        "n_rows":  file_info.get("n_rows_sampled"),
                    })

    report["sensor_summary"] = dict(report["sensor_summary"])
    print(f"\n[OK] Inspection complete. {len(all_files)} files processed.")
    return report


# ══════════════════════════════════════════════════════════════════════════════
# Frequency Summary
# ══════════════════════════════════════════════════════════════════════════════

def build_frequency_summary(report: Dict) -> Dict[str, Any]:
    """Extract and compare sensor sampling frequencies."""
    freq_summary = {}
    for finfo in report["files"]:
        if finfo.get("type") != "tabular":
            continue
        ss = finfo.get("sampling_stats", {})
        if "median_hz" not in ss:
            continue

        col_class = finfo.get("column_classification", {})

        # Determine primary sensor category
        sensor_types = [k for k in col_class if k not in ("timestamp", "unknown")]
        if not sensor_types:
            continue

        rel_path = finfo.get("relative_path", finfo["file"])
        freq_summary[rel_path] = {
            "sensor_types":  sensor_types,
            "median_hz":     ss["median_hz"],
            "n_samples":     ss["n_samples"],
            "duration_sec":  ss["duration_sec"],
            "n_gaps":        ss["n_gaps"],
            "n_duplicates":  ss["n_duplicates"],
            "ts_unit":       ss["ts_unit_detected"],
        }

    return freq_summary


# ══════════════════════════════════════════════════════════════════════════════
# Report Generator
# ══════════════════════════════════════════════════════════════════════════════

def generate_markdown_report(report: Dict, freq_summary: Dict,
                              out_path: Path) -> str:
    """Generate a human-readable Markdown dataset report."""
    lines = []
    lines.append("# IO-VNBD Dataset Inspection Report")
    lines.append(f"\n**Generated:** {report['inspection_time']}")
    lines.append(f"\n**Raw Data Directory:** `{report['raw_dir']}`")
    lines.append("\n---\n")

    # ── 1. Dataset Overview ─────────────────────────────────────
    lines.append("## 1. Dataset Overview")
    n_files = len(report["files"])
    n_tabular = sum(1 for f in report["files"] if f.get("type") == "tabular")
    n_text    = sum(1 for f in report["files"] if f.get("type") == "text")
    total_mb  = sum(
        f.get("size_bytes", 0) for f in report["files"]
    ) / (1024 * 1024)
    lines.append(f"\n| Item | Value |")
    lines.append(f"|------|-------|")
    lines.append(f"| Total files | {n_files} |")
    lines.append(f"| Tabular (CSV/TXT) files | {n_tabular} |")
    lines.append(f"| Text/README files | {n_text} |")
    lines.append(f"| Total size | {total_mb:.1f} MB |")
    lines.append(f"| Directories | {len(report['directories'])} |")

    # ── 2. Folder Structure ──────────────────────────────────────
    lines.append("\n## 2. Folder Structure")
    lines.append("\n```")
    lines.append("data/IO-VNBD/raw/")
    for d in report["directories"]:
        depth = d.count(os.sep)
        lines.append(f"{'  ' * depth}├── {Path(d).name}/")
    for finfo in report["files"]:
        rel = finfo.get("relative_path", finfo["file"])
        depth = rel.count(os.sep)
        size_kb = finfo.get("size_bytes", 0) / 1024
        lines.append(f"{'  ' * depth}├── {Path(rel).name}  [{size_kb:.1f} KB]")
    lines.append("```")

    # ── 3. Sensor Summary ────────────────────────────────────────
    lines.append("\n## 3. Available Sensors")
    sensor_summary = report.get("sensor_summary", {})
    if sensor_summary:
        for sensor, entries in sensor_summary.items():
            lines.append(f"\n### {sensor.upper()}")
            for e in entries:
                lines.append(f"- **File:** `{e['file']}`  "
                             f"| **Columns:** {e['columns']}  "
                             f"| **Hz:** {e.get('hz', 'N/A')}  "
                             f"| **Rows:** {e.get('n_rows', 'N/A')}")
    else:
        lines.append("\n_No sensor data found. Check if dataset was downloaded._")

    # ── 4. Sampling Frequencies ──────────────────────────────────
    lines.append("\n## 4. Sampling Frequencies")
    lines.append("\n| File | Sensor Types | Median Hz | Samples | Duration (s) | Gaps | Timestamp Unit |")
    lines.append("|------|-------------|-----------|---------|--------------|------|----------------|")
    for fpath, info in sorted(freq_summary.items(), key=lambda x: x[1]["median_hz"], reverse=True):
        fname = Path(fpath).name
        sensors = ", ".join(info["sensor_types"])
        hz = info["median_hz"]
        n  = info["n_samples"]
        dur = info["duration_sec"]
        gaps = info["n_gaps"]
        ts_unit = info["ts_unit"]
        lines.append(f"| `{fname}` | {sensors} | **{hz} Hz** | {n} | {dur} | {gaps} | {ts_unit} |")

    # ── 5. IMU vs GNSS Frequency Comparison ─────────────────────
    lines.append("\n## 5. IMU vs GNSS Frequency Comparison")
    imu_entries  = [(k, v) for k, v in freq_summary.items()
                    if any(s in v["sensor_types"] for s in ["accelerometer", "gyroscope"])]
    gnss_entries = [(k, v) for k, v in freq_summary.items()
                    if "gnss" in v["sensor_types"]]

    if imu_entries:
        avg_imu_hz = np.mean([v["median_hz"] for _, v in imu_entries])
        lines.append(f"\n- **IMU (Accel/Gyro) frequency:** ~{avg_imu_hz:.1f} Hz")
    else:
        lines.append("\n- **IMU frequency:** Not detected in sampled files")

    if gnss_entries:
        avg_gnss_hz = np.mean([v["median_hz"] for _, v in gnss_entries])
        lines.append(f"- **GNSS frequency:** ~{avg_gnss_hz:.1f} Hz")
    else:
        lines.append("- **GNSS frequency:** Not detected in sampled files")

    if imu_entries and gnss_entries:
        ratio = avg_imu_hz / avg_gnss_hz if avg_gnss_hz > 0 else "N/A"
        lines.append(f"- **Ratio (IMU/GNSS):** {ratio:.1f}×")
        lines.append(f"- **Interpolation required:** YES — IMU is {ratio:.0f}× faster than GNSS")
        lines.append("- **Recommendation:** Use IMU timestamps as primary; interpolate GNSS to IMU grid using linear interpolation.")

    # ── 6. Per-File Detail ───────────────────────────────────────
    lines.append("\n## 6. Per-File Details")
    for finfo in report["files"]:
        rel = finfo.get("relative_path", finfo["file"])
        lines.append(f"\n### `{rel}`")
        lines.append(f"- **Type:** {finfo.get('type', 'unknown')}")
        lines.append(f"- **Size:** {finfo.get('size_bytes', 0)/1024:.1f} KB")

        if finfo.get("type") == "tabular":
            lines.append(f"- **Columns:** {finfo.get('columns', [])}")
            lines.append(f"- **Rows (sampled):** {finfo.get('n_rows_sampled', 'N/A')}")

            ss = finfo.get("sampling_stats", {})
            if "median_hz" in ss:
                lines.append(f"- **Sampling:** {ss['median_hz']} Hz  "
                             f"| dt={ss['median_dt_sec']}s  "
                             f"| gaps={ss['n_gaps']}  "
                             f"| duplicates={ss['n_duplicates']}")

            dq = finfo.get("data_quality", {})
            missing = dq.get("missing_values", {})
            if missing:
                lines.append(f"- **Missing values:** {missing}")
            outliers = dq.get("outlier_cols", {})
            if outliers:
                lines.append(f"- **Outliers detected:** {list(outliers.keys())}")

            lines.append(f"- **Column classification:** {finfo.get('column_classification', {})}")

        elif finfo.get("type") == "text":
            content = finfo.get("content", "")
            if content:
                lines.append("\n```")
                lines.append(content[:1500])
                lines.append("```")

    # ── 7. Recommendations ──────────────────────────────────────
    lines.append("\n## 7. Recommendations for IDR Baseline")
    lines.append("""
Based on the inspection:

1. **First sequence to use:** Select the smartphone dataset file(s) with:
   - Highest sample count
   - Fewest gaps
   - Complete accel + gyro + GNSS coverage

2. **Resampling strategy:**
   - Keep IMU at native frequency (expected ~10 Hz)
   - Forward-fill GNSS (expected ~1 Hz) to IMU timestamps
   - Mark interpolated GNSS samples with a `gnss_interpolated=True` flag

3. **Coordinate frame:**
   - Smartphone IMU: device frame (X=right, Y=forward, Z=up typically)
   - GNSS: WGS-84 (lat/lon/alt)
   - Ground truth: reported in WGS-84 or local ENU

4. **Do NOT modify raw files.** All processing happens in `data/IO-VNBD/processed/`.
""")

    md_content = "\n".join(lines)
    out_path.write_text(md_content, encoding="utf-8")
    print(f"\n[OK] Markdown report saved: {out_path}")
    return md_content


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="IO-VNBD Dataset Inspector — SIH 2026 IDR Project"
    )
    parser.add_argument(
        "--data-dir", type=str,
        default=str(RAW_DATA_DIR),
        help="Path to raw dataset directory"
    )
    parser.add_argument(
        "--out-json", type=str,
        default=str(DOCS_DIR / "dataset_inspection.json"),
        help="Output JSON report path"
    )
    parser.add_argument(
        "--out-md", type=str,
        default=str(DOCS_DIR / "dataset_report.md"),
        help="Output Markdown report path"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"[ERROR] Data directory not found: {data_dir}")
        print("        Run: python data/download_dataset.py first")
        sys.exit(1)

    if not any(data_dir.iterdir()):
        print(f"[ERROR] Data directory is empty: {data_dir}")
        print("        Dataset has not been downloaded yet.")
        sys.exit(1)

    print("=" * 60)
    print("IO-VNBD Dataset Inspector")
    print("SIH 2026 — Intelligent Dead Reckoning")
    print("=" * 60)
    print(f"Data directory: {data_dir}")

    # Walk and inspect
    report = walk_dataset(data_dir)

    # Frequency summary
    freq_summary = build_frequency_summary(report)

    # Save JSON report
    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"[OK] JSON report saved: {json_path}")

    # Save Markdown report
    md_path = Path(args.out_md)
    generate_markdown_report(report, freq_summary, md_path)

    # Console summary
    print("\n" + "=" * 60)
    print("INSPECTION SUMMARY")
    print("=" * 60)
    print(f"  Files inspected : {len(report['files'])}")
    print(f"  Sensor types    : {list(report.get('sensor_summary', {}).keys())}")
    print()
    print("SAMPLING FREQUENCIES:")
    for fpath, info in sorted(freq_summary.items(),
                               key=lambda x: x[1]["median_hz"], reverse=True):
        print(f"  {Path(fpath).name:40s} → {info['median_hz']:6.1f} Hz  "
              f"({', '.join(info['sensor_types'])})")

    print()
    print(f"  Reports saved to: {DOCS_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
