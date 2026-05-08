import os
import json
import requests
from pathlib import Path

def push_file(filename, payload, url, token):
    print(f"Pushing {filename} to {url}...")
    headers = {
        "X-Mobile-Token": token,
        "Content-Type": "application/json"
    }
    data = {
        "filename": filename,
        "payload": payload
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            print(f"Successfully pushed {filename}")
            return True
        else:
            print(f"Failed to push {filename}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error pushing {filename}: {e}")
        return False

def main():
    ROOT_DIR = Path(__file__).resolve().parent.parent
    TEMPLATES_DIR = ROOT_DIR / "ui_runner" / "templates"

    token = os.environ.get("PROPORACLE_MOBILE_TOKEN", "").strip()
    upload_url = os.environ.get("PROPORACLE_MOBILE_UPLOAD_URL", "").strip()

    if not token:
        print("Error: PROPORACLE_MOBILE_TOKEN environment variable not set.")
        return
    if not upload_url:
        print("Error: PROPORACLE_MOBILE_UPLOAD_URL environment variable not set (e.g., https://your-app.up.railway.app/api/mobile/upload-data).")
        return

    # Files to push
    files_to_push = [
        "slate_latest.json",
        "tickets_latest.json",
        "pipeline_status.json"
    ]

    # Also push dated graded props for the last 3 days
    for gp in TEMPLATES_DIR.glob("graded_props_*.json"):
        files_to_push.append(gp.name)

    success_count = 0
    for filename in files_to_push:
        file_path = TEMPLATES_DIR / filename
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if push_file(filename, payload, upload_url, token):
                    success_count += 1
            except Exception as e:
                print(f"Error reading {filename}: {e}")
        else:
            print(f"Skipping {filename}, file not found.")

    print(f"Mobile data push complete. Success: {success_count}/{len(files_to_push)}")

if __name__ == "__main__":
    main()
