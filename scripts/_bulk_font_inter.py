"""One-off bulk replace: Share Tech Mono stack -> Inter across ui_runner + scripts (April 2026)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = [
    ROOT / "ui_runner" / "templates",
    ROOT / "ui_runner" / "static",
    ROOT / "ui_runner" / "docs",
    ROOT / "scripts",
]
EXTS = {".html", ".htm", ".css", ".py", ".js", ".md"}

REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"'Share Tech Mono'\s*,\s*monospace"), "'Inter',sans-serif"),
    (re.compile(r'"Share Tech Mono"\s*,\s*monospace'), '"Inter",sans-serif'),
    (re.compile(r"'Share Tech Mono'\s*,\s*ui-monospace\s*,\s*monospace"), "'Inter',ui-sans-serif,sans-serif"),
    (re.compile(r'"Share Tech Mono"\s*,\s*ui-monospace\s*,\s*monospace'), '"Inter",ui-sans-serif,sans-serif'),
]

OLD_INTER = "family=Inter:wght@600;700;800"
NEW_INTER = "family=Inter:wght@400;500;600;700;800"


def patch_content(raw: str) -> tuple[str, int]:
    n = 0
    s = raw
    for pat, repl in REPLACEMENTS:
        s, c = pat.subn(repl, s)
        n += c
    c2 = s.count(OLD_INTER)
    if c2:
        s = s.replace(OLD_INTER, NEW_INTER)
        n += c2
    return s, n


def main() -> None:
    total_files = 0
    total_changes = 0
    for base in DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in EXTS:
                continue
            if "node_modules" in path.parts or ".git" in path.parts:
                continue
            if path.name == "_bulk_font_inter.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new_text, n = patch_content(text)
            if n == 0:
                continue
            path.write_text(new_text, encoding="utf-8", newline="\n")
            total_files += 1
            total_changes += n
            print(f"{path.relative_to(ROOT)} ({n})")
    print(f"\nDone: {total_files} files, ~{total_changes} replacements.")


if __name__ == "__main__":
    main()
