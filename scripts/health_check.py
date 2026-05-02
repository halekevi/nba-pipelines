import os
import sys
import socket
import json
import subprocess
from pathlib import Path

def check_flask_server(port=5173):
    """Checks if the Flask server is listening on the given port using socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        result = s.connect_ex(('127.0.0.1', port))
        if result == 0:
            # Try to get PID using netstat if on Windows
            try:
                output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode()
                for line in output.strip().split('\n'):
                    if 'LISTENING' in line:
                        pid = line.strip().split()[-1]
                        return True, pid
            except:
                return True, "Unknown"
    return False, None

def check_capacitor_config(mobile_dir, expected_url):
    """Checks if capacitor.config.json has the correct URL."""
    config_path = Path(mobile_dir) / "android" / "app" / "src" / "main" / "assets" / "capacitor.config.json"
    if not config_path.exists():
        return False, "capacitor.config.json not found"

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            actual_url = config.get("server", {}).get("url")
            if actual_url == expected_url:
                return True, actual_url
            else:
                return False, f"URL mismatch: expected {expected_url}, got {actual_url}"
    except Exception as e:
        return False, str(e)

def check_paths(root_dir):
    """Checks if critical paths exist under the repo root."""
    paths_to_check = [
        "ui_runner/app.py",
        "mobile/capacitor.config.js",
        "ui_runner/templates/tickets_latest.json",
        "ui_runner/templates/slate_latest.json"
    ]
    results = []
    for p in paths_to_check:
        full_path = Path(root_dir) / p
        exists = full_path.exists()
        results.append((p, exists))
    return results


def _repo_root() -> Path:
    env = (os.environ.get("PROPORACLE_REPO_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    root = str(_repo_root())
    mobile = os.path.join(root, "mobile")
    # Physical device on LAN: set PROPORACLE_HEALTH_URL=http://<PC_LAN_IP>:5173
    # Android Studio emulator: use http://10.0.2.2:5173 (host loopback)
    target_url = os.environ.get("PROPORACLE_HEALTH_URL", "http://10.0.0.207:5173")

    print("--- PropORACLE Health Check ---")

    # 1. Server Check
    is_up, pid = check_flask_server()
    if is_up:
        print(f"[OK] Flask Server is RUNNING on port 5173 (PID: {pid})")
    else:
        print("[FAIL] Flask Server is NOT RUNNING on port 5173")

    # 2. Capacitor Check
    cap_ok, cap_msg = check_capacitor_config(mobile, target_url)
    if cap_ok:
        print(f"[OK] Capacitor Config URL: {cap_msg}")
    else:
        print(f"[FAIL] Capacitor Config: {cap_msg}")

    # 3. Path Check
    path_results = check_paths(root)
    for p, exists in path_results:
        status = "[OK]" if exists else "[FAIL]"
        print(f"{status} Path: {p}")

    # 4. Repo root (informational; pipelines write under outputs/, data/, etc. relative to this tree)
    print(f"[INFO] Repo root: {root}")

    if any(not r[1] for r in path_results) or not is_up or not cap_ok:
        print("\nHealth check FAILED. Please address the issues above.")
        sys.exit(1)
    else:
        print("\nHealth check PASSED. Ready for mobile testing.")
        sys.exit(0)
