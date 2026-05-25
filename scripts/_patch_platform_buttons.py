"""One-off: add static PrizePicks/Underdog buttons after Standard in slate toolbars."""
import re
from pathlib import Path

SNIPPET = """        <button class="slate-filter-btn" id="sfb-{sport}-pp" type="button" title="PrizePicks lines only" onclick="togglePlatformFilter('{sport}','prizepicks',this)">PrizePicks</button>
        <button class="slate-filter-btn" id="sfb-{sport}-ud" type="button" title="Underdog lines only" onclick="togglePlatformFilter('{sport}','underdog',this)">Underdog</button>
"""

PAT = re.compile(
    r'(<button class="slate-filter-btn" id="sfb-([\w]+)-standard" '
    r'onclick="togglePickFilter\(\'([\w]+)\',\'Standard\',this\)">Standard</button>\s*\n)'
    r'(\s*<span class="slate-tier-group")',
    re.MULTILINE,
)


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if re.search(r'id="sfb-nba-pp"', text):
        print(f"{path}: already has static PP/UD buttons")
        return

    def repl(m: re.Match) -> str:
        sport = m.group(2)
        return m.group(1) + SNIPPET.format(sport=sport) + m.group(4)

    new, n = PAT.subn(repl, text)
    if not n:
        print(f"{path}: no toolbar matches")
        return
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"{path}: patched {n} toolbars")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    patch(root / "ui_runner" / "templates" / "index.html")
    patch(root / "mobile" / "www" / "index.html")
