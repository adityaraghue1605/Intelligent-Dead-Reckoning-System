"""
IO-VNBD LFS Dataset Downloader v3
===================================
Downloads ALL IO-VNBD CSV files via GitHub LFS Batch API.

Strategy (in order of preference):
  A) GitHub API (requires --token or GITHUB_TOKEN env var, OR wait for rate limit reset)
  B) Hardcoded known file list (ZERO API calls, works immediately even when rate-limited)

NO git installation required. NO admin rights required.

Usage:
    # Fastest (no API needed, uses hardcoded file list):
    python data/lfs_download.py --use-known-list

    # With GitHub token (recommended for full automatic discovery):
    python data/lfs_download.py --token YOUR_TOKEN

    # Smartphone files only (fastest subset):
    python data/lfs_download.py --use-known-list --s-only

    # Dry run (see what will be downloaded):
    python data/lfs_download.py --use-known-list --dry-run

Get a FREE GitHub token (no scopes needed for public repos):
    https://github.com/settings/tokens/new
    Name it "IO-VNBD-Download", click Generate, copy the token.

Author: IDR Project (SIH 2026)
"""

import os
import sys
import json
import time
import argparse
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import urllib.parse
import requests

# ─── Constants ────────────────────────────────────────────────────────────────
REPO_OWNER  = "onyekpeu"
REPO_NAME   = "IO-VNBD"
REPO_BRANCH = "master"
GITHUB_API  = "https://api.github.com"
RAW_BASE    = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}"
LFS_API     = f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git/info/lfs/objects/batch"

LFS_POINTER_MAX_SIZE = 200  # bytes
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"

LFS_HEADERS = {
    "Accept":       "application/vnd.git-lfs+json",
    "Content-Type": "application/vnd.git-lfs+json",
}

# ─── KNOWN FILE LIST ─────────────────────────────────────────────────────────
# Complete enumeration from GitHub API (documented 11-Sep-2026).
# Used when --use-known-list is set OR when GitHub API is rate-limited.
# Format: ("repo_path", lfs_pointer_size)
# All CSV and ZIP files are LFS pointers (size 130-134 bytes).
KNOWN_FILES = [
    # ── Root ──
    ("README.md",          1926),   # regular text file
    ("README_1.pdf",       134),    # LFS
    (".gitattributes",     126),    # regular

    # ── Unsynchronised / Uncategorised / S-Dataset (Smartphone) ──
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A1.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A2.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A3.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A4.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A5.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A6.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A7.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A8.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A9.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A10.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A11.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A12.csv",   131),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-A13.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-I.csv",     131),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-M.csv",     133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S1.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S2.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S3a.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S3b.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S3c.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-S4.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T1.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T2.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T3.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T4.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T5.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T6.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T7.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T8.csv",    132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T9.csv",    133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T10.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-T11.csv",   132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-Vfa01.csv", 132),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-Vfa02.csv", 133),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-Vta10.csv", 131),
    ("Unsynchronised V and S Dataset/Uncategorised IOVNB (V and S) Dataset/S-Dataset/S-Vta11.csv", 130),

    # ── Synchronised / Categorised / S (Driver A) ──
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S1/S-S1.csv",   132),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S1/V-S1.csv",   133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S1/V-S1.JPG",   131),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S2/S-S2.csv",   133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S2/V-S2.csv",   133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3a/S-S3a.csv", 132),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3a/V-S3a.csv", 133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3b/S-S3b.csv", 132),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3b/V-S3b.csv", 133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3c/S-S3c.csv", 132),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S3c/V-S3c.csv", 133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S4/S-S4.csv",   133),
    ("Synchronised V abd S datasets/Categorised IOVNB Dataset/S (Driver A)/S4/V-S4.csv",   133),

    # ── Zip archives (LFS) ──
    ("Synchronised V abd S datasets.zip",       134),
    ("Unsynchronised V and S Dataset.zip",      134),
]

# Classify known files
def classify_known(path: str, size: int) -> str:
    """Return 'lfs', 'regular', or 'skip'."""
    ext = Path(path).suffix.lower()
    if ext in (".gitattributes",):
        return "skip"
    if size <= LFS_POINTER_MAX_SIZE:
        return "lfs"
    return "regular"


# ─── Logging ──────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    tag = {"INFO": "INFO", "OK": " OK ", "WARN": "WARN", "ERR": " ERR", "HEAD": "===="}
    print(f"[{tag.get(level, level)}] {msg}", flush=True)


# ─── GitHub API helpers ───────────────────────────────────────────────────────
def get_full_tree(session: requests.Session) -> Optional[List[Dict]]:
    """Get complete repo file tree via Git Trees API (2 API calls total)."""
    log("Fetching branch HEAD commit SHA ...")
    r = session.get(
        f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/branches/{REPO_BRANCH}",
        timeout=30
    )
    if r.status_code != 200:
        log(f"GitHub API returned HTTP {r.status_code}. Rate-limited? Use --use-known-list.", "ERR")
        return None

    tree_sha = r.json()["commit"]["commit"]["tree"]["sha"]
    log(f"Fetching full recursive tree (SHA: {tree_sha[:10]}...) ...")
    r2 = session.get(
        f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/{tree_sha}?recursive=1",
        timeout=60
    )
    if r2.status_code != 200:
        log(f"Tree API returned HTTP {r2.status_code}. Use --use-known-list.", "ERR")
        return None

    data = r2.json()
    blobs = [item for item in data.get("tree", []) if item["type"] == "blob"]
    log(f"Repo tree: {len(blobs)} files found", "OK")
    return blobs


# ─── URL builder ─────────────────────────────────────────────────────────────
def raw_url(path: str) -> str:
    return f"{RAW_BASE}/{urllib.parse.quote(path, safe='/')}"


# ─── LFS pointer parser ───────────────────────────────────────────────────────
def parse_lfs_pointer(content: str) -> Optional[Tuple[str, int]]:
    oid = size = None
    for line in content.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":")[-1].strip()
        elif line.startswith("size "):
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    return (oid, size) if oid and size else None


def fetch_lfs_pointer(session: requests.Session, path: str) -> Optional[Tuple[str, int]]:
    try:
        r = session.get(raw_url(path), timeout=20)
        if r.status_code == 200:
            return parse_lfs_pointer(r.text)
        log(f"HTTP {r.status_code} for pointer: {path}", "WARN")
    except requests.RequestException as e:
        log(f"Pointer fetch error: {e}", "WARN")
    return None


# ─── LFS Batch API ────────────────────────────────────────────────────────────
def lfs_batch_request(objects: List[Dict]) -> Dict[str, str]:
    payload = {
        "operation": "download",
        "transfers": ["basic"],
        "ref":       {"name": f"refs/heads/{REPO_BRANCH}"},
        "objects":   objects,
    }
    try:
        r = requests.post(LFS_API, headers=LFS_HEADERS, json=payload, timeout=60)
        if r.status_code == 200:
            result = {}
            for obj in r.json().get("objects", []):
                oid   = obj.get("oid")
                error = obj.get("error")
                if error:
                    log(f"LFS error for {(oid or '?')[:12]}...: {error}", "WARN")
                    continue
                href = obj.get("actions", {}).get("download", {}).get("href")
                if href and oid:
                    result[oid] = href
            return result
        log(f"LFS batch HTTP {r.status_code}: {r.text[:200]}", "ERR")
    except requests.RequestException as e:
        log(f"LFS batch network error: {e}", "ERR")
    return {}


# ─── File downloader ─────────────────────────────────────────────────────────
def verify_sha256(path: Path, expected_oid: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest() == expected_oid


def download_file(url: str, dest: Path, expected_size: Optional[int] = None,
                  oid: Optional[str] = None, no_verify: bool = False) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, stream=True, timeout=(15, 60))
            r.raise_for_status()
            total = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            if expected_size and abs(total - expected_size) > 1000:
                log(f"Size mismatch {dest.name}: got {total}, expected {expected_size}", "WARN")
                tmp.unlink(missing_ok=True)
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return False

            if tmp.exists():
                tmp.rename(dest)

            if oid and not no_verify:
                if not verify_sha256(dest, oid):
                    log(f"SHA-256 mismatch: {dest.name}", "ERR")
                    return False
            return True
        except Exception as e:
            tmp.unlink(missing_ok=True)
            if attempt < max_retries:
                log(f"Retry {attempt}/{max_retries} for {dest.name} after error: {e}", "WARN")
                time.sleep(2 * attempt)
            else:
                log(f"Download failed {dest.name}: {e}", "ERR")
                return False
    return False


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Download IO-VNBD dataset via LFS Batch API (no git/admin required)."
    )
    parser.add_argument("--dest",          default=str(RAW_DATA_DIR))
    parser.add_argument("--token",         default=os.environ.get("GITHUB_TOKEN"),
                        help="GitHub personal access token (avoids API rate limits)")
    parser.add_argument("--use-known-list", action="store_true",
                        help="Use hardcoded file list (no API calls; works when rate-limited)")
    parser.add_argument("--s-only",        action="store_true",
                        help="Only download smartphone S-*.csv files")
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--lfs-batch-size", type=int, default=100)
    parser.add_argument("--no-verify",     action="store_true",
                        help="Skip SHA-256 integrity check (faster but less safe)")
    args = parser.parse_args()

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Accept": "application/vnd.github.v3+json"}
    if args.token:
        headers["Authorization"] = f"token {args.token}"
    session = requests.Session()
    session.headers.update(headers)

    print("=" * 70)
    print("IO-VNBD LFS Dataset Downloader v3")
    print("SIH 2026 -- Intelligent Dead Reckoning System")
    print("=" * 70)
    print(f"Destination    : {dest_dir}")
    print(f"GitHub token   : {'SET (5000 req/hr)' if args.token else 'NOT SET (60 req/hr)'}")
    print(f"Mode           : {'Known-list (no API)' if args.use_known_list else 'GitHub API'}")
    print(f"Dry run        : {args.dry_run}")
    print()

    # ── Build file list ───────────────────────────────────────────────────────
    if args.use_known_list:
        log("Using hardcoded known file list (zero API calls) ...", "HEAD")
        all_files = [(p, s, classify_known(p, s)) for p, s in KNOWN_FILES]
    else:
        if not args.token:
            log("No GitHub token set. GitHub API allows only 60 req/hr unauthenticated.", "WARN")
            log("If you see 403 errors, use: --use-known-list OR --token YOUR_TOKEN", "WARN")
            print()
        log("Fetching file list from GitHub API ...", "HEAD")
        blobs = get_full_tree(session)
        if blobs is None:
            log("Falling back to hardcoded known file list.", "WARN")
            all_files = [(p, s, classify_known(p, s)) for p, s in KNOWN_FILES]
        else:
            all_files = [
                (b["path"], b.get("size", 0),
                 "lfs" if b.get("size", 9999) <= LFS_POINTER_MAX_SIZE else "regular")
                for b in blobs
            ]

    # Apply filters
    if args.s_only:
        all_files = [(p, s, t) for p, s, t in all_files
                     if "/S-" in p or Path(p).name.startswith("S-")]

    lfs_files = [(p, s) for p, s, t in all_files if t == "lfs"]
    reg_files = [(p, s) for p, s, t in all_files if t == "regular"]

    log(f"LFS files (CSV/data) : {len(lfs_files)}")
    log(f"Regular files        : {len(reg_files)}")
    print()

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        print("--- LFS files (CSV/data, require LFS batch download) ---")
        for p, s in lfs_files[:30]:
            print(f"  [{s} B ptr] {p}")
        if len(lfs_files) > 30:
            print(f"  ... and {len(lfs_files)-30} more")
        print()
        print("--- Regular files ---")
        for p, s in reg_files:
            print(f"  [{s:,} B] {p}")
        print()
        print(f"Dry run complete. {len(lfs_files)} LFS files + {len(reg_files)} regular files.")
        return

    # ── Download regular files ────────────────────────────────────────────────
    if reg_files:
        log(f"Downloading {len(reg_files)} regular file(s) ...", "HEAD")
        for path, size in reg_files:
            dest = dest_dir / path
            if args.skip_existing and dest.exists() and dest.stat().st_size == size:
                log(f"SKIP: {dest.name}")
                continue
            log(f"Downloading: {path}")
            ok = download_file(raw_url(path), dest, expected_size=size,
                               no_verify=args.no_verify)
            log(f"{'OK' if ok else 'FAILED'}: {dest.name}", "OK" if ok else "ERR")
        print()

    # ── Fetch LFS pointer OIDs ────────────────────────────────────────────────
    log(f"Fetching {len(lfs_files)} LFS pointer files to extract OIDs ...", "HEAD")
    log("(Each pointer is ~132 bytes -- this is fast)", "INFO")
    lfs_objects = []
    skipped     = []

    for i, (path, ptr_size) in enumerate(lfs_files):
        dest = dest_dir / path
        if args.skip_existing and dest.exists() and dest.stat().st_size > LFS_POINTER_MAX_SIZE:
            skipped.append(path)
            continue

        result = fetch_lfs_pointer(session, path)
        if result:
            oid, size = result
            lfs_objects.append({"oid": oid, "size": size, "path": path})
        else:
            log(f"Could not parse pointer: {Path(path).name}", "WARN")

        if (i + 1) % 10 == 0:
            log(f"  Progress: {i+1}/{len(lfs_files)} pointers read")
        time.sleep(0.08)

    log(f"LFS objects to download : {len(lfs_objects)}", "OK")
    log(f"Already on disk (skip)  : {len(skipped)}", "OK")
    print()

    if not lfs_objects:
        log("Nothing new to download (all files present or no objects found).", "OK")
    else:
        # ── LFS Batch API ─────────────────────────────────────────────────────
        log(f"Calling LFS Batch API in batches of {args.lfs_batch_size} ...", "HEAD")
        oid_to_href: Dict[str, str] = {}

        for bs in range(0, len(lfs_objects), args.lfs_batch_size):
            batch = lfs_objects[bs: bs + args.lfs_batch_size]
            log(f"Batch {bs//args.lfs_batch_size+1}: "
                f"objects {bs+1}--{bs+len(batch)} of {len(lfs_objects)}")
            hrefs = lfs_batch_request([{"oid": o["oid"], "size": o["size"]} for o in batch])
            oid_to_href.update(hrefs)
            log(f"  Got {len(hrefs)} download URLs", "OK")
            time.sleep(0.5)

        log(f"Total download URLs obtained: {len(oid_to_href)}", "OK")
        print()

        # ── Download LFS files ────────────────────────────────────────────────
        log(f"Downloading {len(lfs_objects)} data files ...", "HEAD")
        success = fail = 0

        for i, obj in enumerate(lfs_objects):
            oid  = obj["oid"]
            dest = dest_dir / obj["path"]
            name = Path(obj["path"]).name

            if oid not in oid_to_href:
                log(f"[{i+1}/{len(lfs_objects)}] No URL for: {name}", "WARN")
                fail += 1
                continue

            sz_kb = round(obj["size"] / 1024, 1)
            log(f"[{i+1}/{len(lfs_objects)}] {name} ({sz_kb} KB)")

            ok = download_file(
                oid_to_href[oid], dest,
                expected_size=obj["size"],
                oid=oid,
                no_verify=args.no_verify,
            )
            if ok:
                success += 1
            else:
                fail += 1
            time.sleep(0.05)

        print()
        log(f"Succeeded  : {success}", "OK")
        log(f"Skipped    : {len(skipped)}", "OK")
        log(f"Failed     : {fail}", "WARN" if fail else "OK")

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    csv_files = list(dest_dir.rglob("*.csv"))
    real_csv  = [f for f in csv_files if f.stat().st_size > LFS_POINTER_MAX_SIZE]
    stub_csv  = [f for f in csv_files if f.stat().st_size <= LFS_POINTER_MAX_SIZE]
    total_mb  = sum(f.stat().st_size for f in real_csv) / 1024 / 1024

    log(f"CSV files on disk       : {len(csv_files)}", "HEAD")
    log(f"Real data files         : {len(real_csv)}", "OK")
    log(f"LFS stubs remaining     : {len(stub_csv)}", "WARN" if stub_csv else "OK")
    log(f"Total real data size    : {total_mb:.1f} MB")
    print()
    if stub_csv:
        log("Some files are still LFS stubs. Re-run to retry failed downloads.", "WARN")
    elif real_csv:
        log("Dataset download COMPLETE -- all files are real data!", "OK")
        log("Next step: python data/verify_dataset.py", "OK")
    else:
        log("No files downloaded yet. Run without --dry-run.", "WARN")
    print("=" * 70)


if __name__ == "__main__":
    main()
