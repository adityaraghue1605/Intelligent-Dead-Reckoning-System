"""
IO-VNBD Post-Download Verification & Readiness Report
======================================================
Run AFTER lfs_download.py completes.
Reads actual CSV files to verify:
 - Real data vs LFS pointer stubs
 - Column names, row counts, timestamp format
 - Sampling frequencies (IMU vs GNSS)
 - Units, missing values, GNSS outages

Usage:
    python data/verify_dataset.py
    python data/verify_dataset.py --max-files 5

Output: prints Dataset Readiness Report + saves docs/dataset_readiness_verified.json
"""

import sys
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"
DOCS_DIR     = PROJECT_ROOT / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

LFS_POINTER_MAX_SIZE = 200  # bytes — pointer stubs are 131-134 bytes


def is_lfs_pointer(path: Path) -> bool:
    """Return True if file is still a Git LFS pointer stub."""
    try:
        if path.stat().st_size > LFS_POINTER_MAX_SIZE:
            return False
        content = path.read_text(encoding="utf-8", errors="ignore")
        return "version https://git-lfs.github.com/spec/v1" in content
    except Exception:
        return False


def safe_import_pandas():
    try:
        import pandas as pd
        import numpy as np
        return pd, np
    except ImportError:
        print("[ERR] pandas/numpy not available. Activate the .venv first:")
        print("      .venv\\Scripts\\activate")
        sys.exit(1)


def read_csv_safe(path: Path, pd):
    """Try multiple CSV reading strategies."""
    strategies = [
        dict(sep=",",  header=0, skipinitialspace=True, encoding="latin1"),
        dict(sep=",",  header=0, skipinitialspace=True, encoding="utf-8", encoding_errors="replace"),
        dict(sep="\t", header=0, skipinitialspace=True, encoding="latin1"),
        dict(sep=";",  header=0, skipinitialspace=True, encoding="latin1"),
        dict(sep=",",  header=None, skipinitialspace=True, encoding="latin1"),
    ]
    for kw in strategies:
        try:
            df = pd.read_csv(path, nrows=5000, **kw)
            if df.shape[1] >= 3 and df.shape[0] >= 5:
                df.columns = [str(c).strip() for c in df.columns]
                return df
        except Exception:
            continue
    return None


def compute_freq(ts_series, pd, np) -> Dict[str, Any]:
    """Compute sampling frequency stats from a timestamp column."""
    ts = pd.to_numeric(ts_series.dropna(), errors="coerce").dropna()
    if len(ts) < 5:
        return {"error": "insufficient data"}

    median_val = float(ts.median())
    # Auto-detect unit
    if median_val > 1e15:
        ts_sec = ts / 1e6
        unit = "microseconds"
    elif median_val > 1e12:
        ts_sec = ts / 1e3
        unit = "milliseconds (Unix)"
    elif median_val > 1e9:
        ts_sec = ts
        unit = "unix_seconds"
    elif median_val > 1e4:
        ts_sec = ts / 1e3
        unit = "elapsed_ms -> seconds"
    else:
        ts_sec = ts
        unit = "elapsed_seconds"

    diffs = ts_sec.sort_values().diff().dropna()
    pos   = diffs[diffs > 0]
    if pos.empty:
        return {"error": "no positive diffs"}

    med_dt  = float(pos.median())
    hz      = round(1.0 / med_dt, 3) if med_dt > 0 else 0
    dur     = round(float(ts_sec.max() - ts_sec.min()), 2)
    n_gaps  = int((diffs > 3 * med_dt).sum())
    n_dups  = int((diffs == 0).sum())

    return {
        "n_samples":        len(ts),
        "duration_sec":     dur,
        "ts_unit":          unit,
        "median_dt_sec":    round(med_dt, 6),
        "freq_hz":          hz,
        "n_gaps":           n_gaps,
        "n_duplicate_ts":   n_dups,
        "max_gap_sec":      round(float(diffs[diffs > 3*med_dt].max()), 2) if n_gaps else 0,
    }


def classify_columns(cols: List[str]) -> Dict[str, List[str]]:
    """Classify column names into sensor groups."""
    groups: Dict[str, List[str]] = {
        "timestamp":      [],
        "accelerometer":  [],
        "linear_accel":   [],
        "gravity":        [],
        "gyroscope":      [],
        "magnetometer":   [],
        "orientation":    [],
        "gnss":           [],
        "speed":          [],
        "unknown":        [],
    }
    keywords = {
        "timestamp":     ["time", "timestamp", "ts", "epoch", "since", "ms"],
        "accelerometer": ["accelerometer", "acc", "accel"],
        "linear_accel":  ["linear acceleration", "linear_accel", "linearacc"],
        "gravity":       ["gravity", "grav"],
        "gyroscope":     ["gyroscope", "gyro", "gyr", "rotation rate", "angular"],
        "magnetometer":  ["magnetic", "magnet", "magnetometer", "mag"],
        "orientation":   ["orientation", "azimuth", "pitch", "roll", "heading", "bearing"],
        "gnss":          ["latitude", "longitude", "altitude", "location", "gps", "gnss",
                          "lat", "lon", "lng", "position"],
        "speed":         ["speed", "velocity"],
    }
    for col in cols:
        col_l = col.lower()
        matched = False
        for grp, kws in keywords.items():
            if any(k in col_l for k in kws):
                groups[grp].append(col)
                matched = True
                break
        if not matched:
            groups["unknown"].append(col)
    return {k: v for k, v in groups.items() if v}


def check_gnss_outages(df, gnss_cols: List[str], ts_col: Optional[str], pd) -> Dict:
    """Detect GNSS outage periods in the data."""
    if not gnss_cols:
        return {"error": "no GNSS columns found"}

    lat_cols = [c for c in gnss_cols if "lat" in c.lower()]
    lon_cols = [c for c in gnss_cols if "lon" in c.lower() or "lng" in c.lower()]
    acc_cols = [c for c in gnss_cols if "acc" in c.lower() and "lat" not in c.lower()]

    info = {}
    if lat_cols:
        lat_col = lat_cols[0]
        lat_vals = pd.to_numeric(df[lat_col], errors="coerce")
        missing_gnss = int(lat_vals.isna().sum())
        zero_gnss    = int((lat_vals == 0).sum())
        info["gnss_lat_missing"]  = missing_gnss
        info["gnss_lat_zero"]     = zero_gnss
        info["gnss_lat_range"]    = [round(float(lat_vals.min()), 4),
                                     round(float(lat_vals.max()), 4)]
        # Detect repeated values (GNSS not updating = possible outage indicator)
        if len(lat_vals) > 10:
            consecutive_same = int((lat_vals.diff() == 0).sum())
            info["gnss_repeated_rows"] = consecutive_same

    if acc_cols:
        acc_col  = acc_cols[0]
        acc_vals = pd.to_numeric(df[acc_col], errors="coerce")
        info["gnss_high_accuracy_rows"] = int((acc_vals > 50).sum())

    return info


def inspect_csv(path: Path, pd, np) -> Dict[str, Any]:
    """Full inspection of one CSV file."""
    result = {
        "file":     path.name,
        "path":     str(path.relative_to(RAW_DATA_DIR)),
        "size_kb":  round(path.stat().st_size / 1024, 1),
        "is_lfs_pointer": is_lfs_pointer(path),
    }

    if result["is_lfs_pointer"]:
        result["status"] = "LFS_POINTER_STUB — download not complete"
        return result

    df = read_csv_safe(path, pd)
    if df is None:
        result["status"] = "FAILED to parse CSV"
        return result

    cols  = list(df.columns)
    n_row = len(df)
    groups = classify_columns(cols)

    result.update({
        "status":       "OK",
        "n_rows":       n_row,
        "n_cols":       len(cols),
        "columns":      cols,
        "sensor_groups": {k: v for k, v in groups.items()},
    })

    # Timestamp analysis
    ts_col = None
    for c in cols:
        if any(kw in c.lower() for kw in ["time", "ts", "since", "epoch", "ms"]):
            ts_col = c
            break
    if ts_col is None and groups.get("timestamp"):
        ts_col = groups["timestamp"][0]
    if ts_col is None:
        # Try first numeric monotonic column
        for c in cols:
            vals = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(vals) > 5 and vals.is_monotonic_increasing:
                ts_col = c
                break

    result["timestamp_column"] = ts_col
    if ts_col:
        freq_info = compute_freq(df[ts_col], pd, np)
        result["timestamp_analysis"] = freq_info
    else:
        result["timestamp_analysis"] = {"error": "no timestamp column found"}

    # Per-sensor-group frequency if separate timestamp cols exist
    sensor_freqs = {}
    is_s_file = "S-" in path.name or path.name.startswith("S")
    is_v_file = "V-" in path.name or path.name.startswith("V")

    # Sample data preview
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    sample = {}
    for c in numeric_cols[:8]:
        vals = df[c].dropna()
        if len(vals) > 0:
            sample[c] = {
                "min":  round(float(vals.min()), 4),
                "max":  round(float(vals.max()), 4),
                "mean": round(float(vals.mean()), 4),
                "null%": round(float(df[c].isna().mean()) * 100, 1),
            }
    result["sample_stats"] = sample

    # Missing value summary
    null_pct = (df.isnull().sum() / n_row * 100).round(1).to_dict()
    result["null_pct_per_column"] = {k: float(v) for k, v in null_pct.items() if v > 0}

    # GNSS outage check
    gnss_cols = groups.get("gnss", [])
    if gnss_cols:
        result["gnss_outage_check"] = check_gnss_outages(df, gnss_cols, ts_col, pd)

    return result


def main():
    parser = argparse.ArgumentParser(description="Verify IO-VNBD dataset after download.")
    parser.add_argument("--data-dir",   default=str(RAW_DATA_DIR))
    parser.add_argument("--max-files",  type=int, default=None,
                        help="Max CSV files to inspect (default: all)")
    parser.add_argument("--s-only",     action="store_true",
                        help="Inspect only smartphone (S-*.csv) files")
    parser.add_argument("--v-only",     action="store_true",
                        help="Inspect only vehicle (V-*.csv) files")
    args = parser.parse_args()

    pd, np = safe_import_pandas()

    raw_dir = Path(args.data_dir)
    if not raw_dir.exists():
        print(f"[ERR] Directory not found: {raw_dir}")
        sys.exit(1)

    # Find all CSVs
    all_csvs = sorted(raw_dir.rglob("*.csv"))
    if args.s_only:
        all_csvs = [f for f in all_csvs if f.name.startswith("S-") or "/S-" in str(f)]
    if args.v_only:
        all_csvs = [f for f in all_csvs if f.name.startswith("V-") or "/V-" in str(f)]
    if args.max_files:
        all_csvs = all_csvs[:args.max_files]

    print("=" * 70)
    print("IO-VNBD Dataset Readiness Verification Report")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"Raw data dir : {raw_dir}")
    print(f"CSV files    : {len(all_csvs)}")
    print()

    if not all_csvs:
        print("[ERR] No CSV files found in raw directory!")
        print("      Run: python data/lfs_download.py")
        sys.exit(1)

    # Check LFS pointers vs real data
    pointer_count = sum(1 for f in all_csvs if is_lfs_pointer(f))
    real_count    = len(all_csvs) - pointer_count
    print(f"Real CSV files (downloaded) : {real_count}")
    print(f"LFS pointer stubs remaining : {pointer_count}")
    print()

    if pointer_count > 0:
        print("[!!] WARNING: Some files are still LFS pointer stubs.")
        print("     Re-run: python data/lfs_download.py")
        print()

    # Inspect a representative sample
    s_files = [f for f in all_csvs if f.name.startswith("S-")]
    v_files = [f for f in all_csvs if f.name.startswith("V-")]
    inspect_targets = []

    # Pick the first real S file and first real V file
    for f in s_files:
        if not is_lfs_pointer(f):
            inspect_targets.append(("smartphone", f))
            break
    for f in v_files:
        if not is_lfs_pointer(f):
            inspect_targets.append(("vehicle", f))
            break

    # Also inspect any additional files up to max_files
    for f in all_csvs:
        if not is_lfs_pointer(f) and f not in [t[1] for t in inspect_targets]:
            inspect_targets.append(("other", f))
        if len(inspect_targets) >= (args.max_files or 4):
            break

    reports = []
    for label, fpath in inspect_targets:
        print(f"--- Inspecting [{label}]: {fpath.name} ---")
        r = inspect_csv(fpath, pd, np)
        reports.append(r)

        print(f"  Status       : {r.get('status','?')}")
        print(f"  Size         : {r.get('size_kb','?')} KB")
        if r.get("status") == "OK":
            print(f"  Rows         : {r.get('n_rows','?'):,}")
            print(f"  Columns ({r.get('n_cols','?')}): {', '.join(r.get('columns',[])[:8])}...")
            ts_info = r.get("timestamp_analysis", {})
            if "error" not in ts_info:
                print(f"  Timestamp    : col='{r.get('timestamp_column')}' "
                      f"unit={ts_info.get('ts_unit','?')}")
                print(f"  Freq (IMU)   : {ts_info.get('freq_hz','?')} Hz")
                print(f"  Duration     : {ts_info.get('duration_sec','?')} seconds")
                print(f"  Gaps         : {ts_info.get('n_gaps','?')}")
            sensor_grp = r.get("sensor_groups", {})
            for grp, cols in sensor_grp.items():
                print(f"  {grp:20s}: {cols}")
            gnss_chk = r.get("gnss_outage_check", {})
            if gnss_chk and "error" not in gnss_chk:
                print(f"  GNSS missing : {gnss_chk.get('gnss_lat_missing','?')} rows")
                print(f"  GNSS zeros   : {gnss_chk.get('gnss_lat_zero','?')} rows")
                print(f"  GNSS repeated: {gnss_chk.get('gnss_repeated_rows','?')} rows (1Hz pattern)")
                lat_range = gnss_chk.get("gnss_lat_range", [])
                if lat_range:
                    print(f"  Lat range    : {lat_range[0]} to {lat_range[1]}")
            nulls = r.get("null_pct_per_column", {})
            if nulls:
                print(f"  Null cols    : { {k:f'{v}%' for k,v in list(nulls.items())[:4]} }")
        print()

    # Final summary
    real_dfs = [r for r in reports if r.get("status") == "OK"]
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total CSV files found : {len(all_csvs)}")
    print(f"Real data (downloaded): {real_count}")
    print(f"LFS stubs remaining   : {pointer_count}")
    print(f"Successfully inspected: {len(real_dfs)}")

    if real_dfs:
        first = real_dfs[0]
        ts_info = first.get("timestamp_analysis", {})
        print()
        print("Verified Sensor Configuration (from actual data):")
        print(f"  IMU sampling rate : {ts_info.get('freq_hz','?')} Hz")
        print(f"  Timestamp unit    : {ts_info.get('ts_unit','?')}")
        print(f"  Columns           : {first.get('n_cols','?')}")
        grps = first.get("sensor_groups", {})
        for g, c in grps.items():
            print(f"  {g:20s}: {c}")

    warning_status = "RESOLVED" if real_count > 0 and pointer_count == 0 else \
                     "PARTIAL" if real_count > 0 else "NOT RESOLVED"
    print()
    print(f"Dataset Warning Status: {warning_status}")
    if warning_status != "RESOLVED":
        print("  Action required: python data/lfs_download.py")
    print("=" * 70)

    # Save JSON report
    report_path = DOCS_DIR / "dataset_readiness_verified.json"
    output = {
        "generated_at":     datetime.now().isoformat(),
        "raw_dir":          str(raw_dir),
        "total_csv_files":  len(all_csvs),
        "real_files":       real_count,
        "lfs_stubs":        pointer_count,
        "warning_status":   warning_status,
        "file_reports":     reports,
    }
    with open(report_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nJSON report saved: {report_path}")


if __name__ == "__main__":
    main()
