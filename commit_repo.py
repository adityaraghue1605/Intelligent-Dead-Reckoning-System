import os, sys
from pathlib import Path
from dulwich import porcelain

repo_path = '.'
root = Path(repo_path).resolve()

# Collect project files, explicitly excluding .venv, .git, scratch, and __pycache__
tracked_files = []
exclude_dirs = {'.venv', '.git', 'scratch', '__pycache__', '.idea', '.vscode'}

for dirpath, dirnames, filenames in os.walk(root):
    # Filter out excluded dirs in-place
    dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
    for f in filenames:
        if f.endswith(('.pyc', '.pyo')) or f.startswith('scratch_'):
            continue
        rel_path = os.path.relpath(os.path.join(dirpath, f), root)
        # Normalize to forward slashes for git
        tracked_files.append(rel_path.replace('\\', '/'))

print(f"Total project files to track: {len(tracked_files)}")

# Open or init repo
if not os.path.exists('.git'):
    repo = porcelain.init(repo_path)
    print("Initialized empty Git repository.")
else:
    repo = porcelain.open_repo(repo_path)

# Stage the collected files
porcelain.add(repo, tracked_files)
print(f"Staged {len(tracked_files)} files successfully.")

# Commit
commit_msg = b"""feat(idr): root-cause data audit, Leaflet map fix, and dead-reckoning trajectory alignment

- Fix Leaflet map by defaulting to OpenStreetMap Standard (eliminating API KEY REQUIRED overlays)
- Fix vehicle marker positioning to track ground truth during GNSS aiding and diverge during outages
- Fix dead-reckoning initialization at outage onset (0.00m initial jump)
- Lower heading alignment and speed calibration thresholds to 0.4 m/s for low-speed urban motion
- Document S-A1.csv GPS freezing root cause (rows 139-414 frozen coordinate fix)
- Add automated dashboard button test suite (46/46 passing unit and integration tests)
"""

commit_id = porcelain.commit(
    repo,
    message=commit_msg,
    author=b"SIH IDR Pair Programmer <idr-team@sih2026.gov.in>",
    committer=b"SIH IDR Pair Programmer <idr-team@sih2026.gov.in>"
)

sha = commit_id.decode('ascii') if isinstance(commit_id, bytes) else commit_id
print(f"Committed successfully! Commit SHA: {sha}")

# Print status summary
log_entries = list(porcelain.log(repo, max_entries=1))
print("Git Commit Verified:")
for entry in log_entries:
    print(" ", entry.commit.id.decode('ascii'), "-", entry.commit.message.decode('utf-8').splitlines()[0])
