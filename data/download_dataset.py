"""
IO-VNBD Dataset Downloader
===========================
Downloads/clones the IO-VNBD dataset from the official GitHub repository.
Raw files are placed in data/IO-VNBD/raw/ and NEVER modified.

Usage:
    python data/download_dataset.py [--subset smartphone] [--help]

Repository: https://github.com/onyekpeu/IO-VNBD
Paper: IO-VNBD: Inertial and Odometry benchmark dataset for ground vehicle
       positioning, Data in Brief, 2021.
"""

import os
import sys
import subprocess
import argparse
import hashlib
import json
import requests
from pathlib import Path
from datetime import datetime

# ── Project root (2 levels up from this script) ────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "IO-VNBD" / "raw"

REPO_URL = "https://github.com/onyekpeu/IO-VNBD.git"
REPO_API  = "https://api.github.com/repos/onyekpeu/IO-VNBD"

# Files that are too large for git LFS — must be downloaded separately
ZENODO_DOI = "10.5281/zenodo.3828480"   # Check if correct from paper
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_DOI.split('.')[-1]}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def clone_repo(dest: Path) -> bool:
    """Clone the IO-VNBD GitHub repository (structure + small files)."""
    if (dest / ".git").exists():
        print(f"[INFO] Repo already cloned at: {dest}")
        print("[INFO] Pulling latest changes ...")
        result = subprocess.run(
            ["git", "pull"],
            cwd=dest, capture_output=True, text=True
        )
        print(result.stdout)
        return True

    print(f"[INFO] Cloning {REPO_URL} → {dest}")
    result = subprocess.run(
        ["git", "clone", "--depth=1", REPO_URL, str(dest)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] git clone failed:\n{result.stderr}")
        return False
    print(result.stdout)
    print("[OK] Repository cloned successfully.")
    return True


def inspect_repo_structure(repo_dir: Path) -> dict:
    """Walk the cloned repo and return a tree structure."""
    tree = {}
    for item in sorted(repo_dir.rglob("*")):
        if ".git" in item.parts:
            continue
        rel = item.relative_to(repo_dir)
        is_file = item.is_file()
        size = item.stat().st_size if is_file else None
        tree[str(rel)] = {
            "type": "file" if is_file else "dir",
            "size_bytes": size,
            "extension": item.suffix.lower() if is_file else None,
        }
    return tree


def get_repo_api_info() -> dict:
    """Fetch repository metadata from GitHub API."""
    try:
        r = requests.get(REPO_API, timeout=10,
                         headers={"Accept": "application/vnd.github.v3+json"})
        if r.status_code == 200:
            data = r.json()
            return {
                "name": data.get("name"),
                "description": data.get("description"),
                "size_kb": data.get("size"),
                "default_branch": data.get("default_branch"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "license": data.get("license", {}).get("name") if data.get("license") else None,
            }
    except Exception as e:
        print(f"[WARN] Could not fetch GitHub API info: {e}")
    return {}


def check_git_lfs() -> bool:
    """Check if git-lfs is installed."""
    try:
        result = subprocess.run(
            ["git", "lfs", "version"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"[OK] git-lfs: {result.stdout.strip()}")
            return True
    except FileNotFoundError:
        pass
    print("[WARN] git-lfs not found. Large files may not download automatically.")
    print("       Install from: https://git-lfs.github.com/")
    return False


def save_download_manifest(repo_dir: Path, tree: dict, api_info: dict):
    """Save download metadata for reproducibility."""
    manifest = {
        "downloaded_at": datetime.now().isoformat(),
        "repo_url": REPO_URL,
        "api_info": api_info,
        "file_tree": tree,
        "total_files": sum(1 for v in tree.values() if v["type"] == "file"),
        "total_dirs": sum(1 for v in tree.values() if v["type"] == "dir"),
    }
    manifest_path = RAW_DATA_DIR / "download_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"[OK] Manifest saved: {manifest_path}")
    return manifest


def print_file_tree(tree: dict, max_entries: int = 80):
    """Pretty-print the repository file tree."""
    print("\n" + "=" * 60)
    print("IO-VNBD Repository File Tree")
    print("=" * 60)
    entries = list(tree.items())
    for path_str, info in entries[:max_entries]:
        depth = path_str.count(os.sep)
        indent = "  " * depth
        icon = "📁" if info["type"] == "dir" else "📄"
        size_str = ""
        if info["size_bytes"] is not None:
            sz = info["size_bytes"]
            if sz > 1024 * 1024:
                size_str = f"  [{sz/(1024*1024):.1f} MB]"
            elif sz > 1024:
                size_str = f"  [{sz/1024:.1f} KB]"
            else:
                size_str = f"  [{sz} B]"
        print(f"{indent}{icon} {Path(path_str).name}{size_str}")

    if len(entries) > max_entries:
        print(f"  ... and {len(entries) - max_entries} more entries")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Download IO-VNBD dataset for IDR project."
    )
    parser.add_argument(
        "--dest", type=str,
        default=str(RAW_DATA_DIR),
        help="Destination directory for raw data (default: data/IO-VNBD/raw/)"
    )
    parser.add_argument(
        "--inspect-only", action="store_true",
        help="Only inspect already-downloaded files, no cloning"
    )
    args = parser.parse_args()

    dest = Path(args.dest)
    ensure_dir(dest)

    print("=" * 60)
    print("IO-VNBD Dataset Downloader")
    print("SIH 2026 — Intelligent Dead Reckoning System")
    print("=" * 60)
    print(f"Destination: {dest}")

    # ── Check git-lfs ────────────────────────────────────────────
    has_lfs = check_git_lfs()

    # ── Get GitHub API info ──────────────────────────────────────
    print("\n[INFO] Fetching repository metadata ...")
    api_info = get_repo_api_info()
    if api_info:
        print(f"  Repo   : {api_info.get('name')}")
        print(f"  Desc   : {api_info.get('description')}")
        print(f"  Size   : {api_info.get('size_kb', 'N/A')} KB (GitHub reported)")
        print(f"  Updated: {api_info.get('updated_at')}")

    # ── Clone or skip ────────────────────────────────────────────
    if not args.inspect_only:
        success = clone_repo(dest)
        if not success:
            print("\n[MANUAL DOWNLOAD INSTRUCTIONS]")
            print("=" * 60)
            print("git clone is not available or failed.")
            print("Please download the dataset manually:")
            print(f"  1. Visit: {REPO_URL}")
            print("  2. Click 'Code' → 'Download ZIP'")
            print(f"  3. Extract to: {dest}")
            print("  OR:")
            print(f"  git clone {REPO_URL} {dest}")
            print("=" * 60)
            sys.exit(1)

    # ── Inspect structure ────────────────────────────────────────
    print("\n[INFO] Inspecting repository structure ...")
    if not dest.exists() or not any(dest.iterdir()):
        print(f"[WARN] Directory empty: {dest}")
        print("       Run without --inspect-only to download first.")
        sys.exit(1)

    tree = inspect_repo_structure(dest)
    print_file_tree(tree)

    # ── Save manifest ─────────────────────────────────────────────
    manifest = save_download_manifest(dest, tree, api_info)
    print(f"\n[SUMMARY]")
    print(f"  Total files : {manifest['total_files']}")
    print(f"  Total dirs  : {manifest['total_dirs']}")
    print(f"  Location    : {dest}")
    print(f"\n[DONE] Dataset download/inspection complete.")
    print("       Next step: Run the dataset inspector script.")


if __name__ == "__main__":
    main()
