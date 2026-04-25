#!/usr/bin/env python3
"""
Utility to pre-render _site_nav.html into static HTML.
The mobile bundle generator strips Jinja2 tags, so we must bake the "active" state
into the HTML using regex before delivery.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "ui_runner" / "templates"

def render_static_nav(active_tab: str = "home") -> str:
    """
    Render _site_nav.html with the specified tab active (static HTML; no Flask Jinja).
    Supported tabs: "home", "grades", "tickets", "help", etc. (whatever matches _site_nav.html logic).
    """
    path = TEMPLATES_DIR / "_site_nav.html"
    if not path.is_file():
        # Fallback if template is missing
        return (
            f'<nav class="snav glass-card" role="navigation" aria-label="Main">'
            f'<a class="snav-brand" href="/">PropOracle</a>'
            f'<ul class="snav-links nav-links">'
            f'<li><a href="/grades" class="{"active" if active_tab == "grades" else ""}">Grades</a></li>'
            f'<li><a href="/tickets" class="{"active" if active_tab == "tickets" else ""}">Tickets</a></li>'
            f'</ul></nav>'
        )

    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    # Strip leading Jinja comments if present
    if lines and lines[0].lstrip().startswith("{#"):
        lines = lines[1:]
    raw = "\n".join(lines).lstrip()

    # Strip common set tags used in _site_nav.html
    raw = re.sub(
        r"\{%\s*set\s+_na\s*=\s*nav_active\|default\('home'\)\s*%\}\s*\n?",
        "",
        raw,
        count=1,
    )
    raw = re.sub(
        r"\{%\s*set\s+_pill\s*=\s*nav_pill_suffix\|default\('LIVE'\)\s*%\}\s*\n?",
        "",
        raw,
        count=1,
    )

    # Static replacements
    raw = raw.replace("{{ _pill }}", "LIVE")

    # Handle active class replacement: {{ 'active' if _na == 'tab_name' else '' }}
    def _active_repl(m: re.Match[str]) -> str:
        tab_name = m.group(1)
        return "active" if tab_name == active_tab else ""

    raw = re.sub(
        r"\{\{\s*'active'\s*if\s*_na\s*==\s*'(\w+)'\s*else\s*''\s*\}\}",
        _active_repl,
        raw,
    )

    return raw.strip()

if __name__ == "__main__":
    # Test render
    print(render_static_nav("tickets"))
