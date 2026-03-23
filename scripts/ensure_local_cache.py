import os
from pathlib import Path


def _is_onedrive_path(path: str) -> bool:
    return "onedrive" in path.lower()


def ensure_local_cache(repo_root: str | None = None) -> str:
    if repo_root is None:
        repo_root = str(Path(__file__).resolve().parents[1])

    # Optional explicit override for advanced deployments.
    explicit_cache_dir = os.getenv("PROPORACLE_CACHE_DIR", "").strip()

    if explicit_cache_dir:
        cache_dir = explicit_cache_dir
    elif os.name == "nt" and _is_onedrive_path(repo_root):
        # Keep SQLite DBs outside OneDrive to avoid lock/disk-I/O errors.
        local_appdata = os.getenv("LOCALAPPDATA", "").strip()
        if local_appdata:
            cache_dir = os.path.join(local_appdata, "PropORACLE", "cache")
        else:
            cache_dir = os.path.join(repo_root, "data", "cache")
    else:
        cache_dir = os.path.join(repo_root, "data", "cache")

    os.makedirs(cache_dir, exist_ok=True)

    logs_dir = os.path.join(repo_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    outputs_dir = os.path.join(repo_root, "outputs", "synthetic")
    os.makedirs(outputs_dir, exist_ok=True)

    return cache_dir


if __name__ == "__main__":
    path = ensure_local_cache()
    print(f"Local cache ready: {path}")
