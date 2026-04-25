import os
import shutil
from pathlib import Path

def generate_bundle():
    # Define paths
    ROOT_DIR = Path(__file__).resolve().parent.parent
    STATIC_DIR = ROOT_DIR / "ui_runner" / "static"
    TEMPLATES_DIR = ROOT_DIR / "ui_runner" / "templates"
    MOBILE_WWW_DIR = ROOT_DIR / "mobile" / "www"

    # Ensure mobile/www exists and is clean
    if MOBILE_WWW_DIR.exists():
        shutil.rmtree(MOBILE_WWW_DIR)
    MOBILE_WWW_DIR.mkdir(parents=True, exist_ok=True)

    # Copy static assets
    print(f"Copying static assets from {STATIC_DIR} to {MOBILE_WWW_DIR / 'static'}...")
    shutil.copytree(STATIC_DIR, MOBILE_WWW_DIR / "static")

    # Copy index.html from templates to mobile/www root
    INDEX_SRC = TEMPLATES_DIR / "index.html"
    INDEX_DEST = MOBILE_WWW_DIR / "index.html"

    if INDEX_SRC.exists():
        print(f"Copying {INDEX_SRC} to {INDEX_DEST}...")
        shutil.copy2(INDEX_SRC, INDEX_DEST)

        # Read index.html and update asset paths for local relative loading
        content = INDEX_DEST.read_text(encoding="utf-8")

        # Replace absolute-style static paths with relative ones
        # e.g., /static/style.css -> static/style.css
        # Also handle potential leading slashes in other asset links
        content = content.replace('href="/static/', 'href="static/')
        content = content.replace('src="/static/', 'src="static/')

        INDEX_DEST.write_text(content, encoding="utf-8")
    else:
        print(f"ERROR: {INDEX_SRC} not found!")

    # Copy other essential templates if needed (e.g., ticket templates)
    # For a static bundle, we mainly need the entry point and assets.
    # If the app relies on dynamic template loading, we'd need a different strategy or
    # ensure those are also bundled and paths are relative.

    print("Mobile bundle generation complete.")

if __name__ == "__main__":
    generate_bundle()
