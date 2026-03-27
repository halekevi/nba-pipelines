#!/usr/bin/env python3
"""
setup_prizepicks_profile.py

Copies your real Chrome profile into a Playwright-safe location so
step1 scripts can reuse your existing PrizePicks login without any
DataDome challenges or manual interaction.

Run once:
    py -3.14 setup_prizepicks_profile.py

Re-run if you ever get logged out of PrizePicks in your normal Chrome.
"""

import os
import shutil
import sys
from pathlib import Path

CHROME_PROFILE = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data"
PP_PROFILE_DIR = Path.home() / ".pp_browser_profile"

COPY_TARGETS = [
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Network",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Local State",
]


def copy_profile():
    if not CHROME_PROFILE.exists():
        print(f"❌ Chrome profile not found at: {CHROME_PROFILE}")
        sys.exit(1)

    print(f"📁 Chrome source:   {CHROME_PROFILE}")
    print(f"📁 PP profile dest: {PP_PROFILE_DIR}")
    print()
    print("⚠️  Chrome must be fully closed (not just minimized).")
    input("   Close Chrome now, then press Enter to continue... ")
    print()

    (PP_PROFILE_DIR / "Default").mkdir(parents=True, exist_ok=True)

    copied = 0
    for target in COPY_TARGETS:
        src  = CHROME_PROFILE / target
        dest = PP_PROFILE_DIR / target
        if not src.exists():
            print(f"  ⚠️  Skipping (not found): {target}")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            print(f"  ✅ {target}")
            copied += 1
        except PermissionError:
            print(f"  ❌ Locked — is Chrome still running? : {target}")
        except Exception as e:
            print(f"  ⚠️  {target}: {e}")

    print(f"\n✅ Done — {copied} items copied to {PP_PROFILE_DIR}")


def verify_profile():
    cookie_db = PP_PROFILE_DIR / "Default/Cookies"
    if not cookie_db.exists():
        print("⚠️  Cookies file missing from copied profile.")
        return
    try:
        import sqlite3
        con  = sqlite3.connect(str(cookie_db))
        rows = con.execute(
            "SELECT name FROM cookies WHERE host_key LIKE '%prizepicks%'"
        ).fetchall()
        con.close()
        if rows:
            print(f"✅ PrizePicks cookies found: {[r[0] for r in rows]}")
            print("   step1 scripts will use this session automatically.")
        else:
            print("⚠️  No PrizePicks cookies in profile.")
            print("   Log into PrizePicks in Chrome first, then re-run this script.")
    except Exception as e:
        print(f"⚠️  Could not verify: {e}")


if __name__ == "__main__":
    copy_profile()
    print()
    verify_profile()
