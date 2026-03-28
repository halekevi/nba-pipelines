"""
Build 3–6 leg parlays from combined slate Full Sheet.

By default only includes legs whose Game Time parses and is **strictly after** now
(US sportsbook-style times in the sheet are interpreted in --tz, default America/New_York).

Examples:
  python scripts/parlay_from_combined_slate.py --date 2026-03-28
  python scripts/parlay_from_combined_slate.py --combined outputs/2026-03-28/combined_slate_tickets_2026-03-28.xlsx
  python scripts/parlay_from_combined_slate.py --date 2026-03-28 --now 2026-03-28T17:00:00-04:00
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_RE_SLATE_DIR = re.compile(r"^(\d{4})-\d{2}-\d{2}$")

import pandas as pd

RS = "Rank Score"
ROOT = Path(__file__).resolve().parents[1]

# "03/28 10:00PM" / "03/28 6:30PM" (single-digit hour)
_RE_MDY_HM = re.compile(
    r"^(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\s*(AM|PM)\s*$",
    re.IGNORECASE,
)


def parse_game_time(
    raw: object,
    *,
    infer_year: int,
    tz: ZoneInfo,
) -> datetime | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        return dt
    except ValueError:
        pass

    m = _RE_MDY_HM.match(s)
    if m:
        mo, d, h, mi, ap = m.groups()
        h_i = int(h)
        mi_i = int(mi)
        ap_u = ap.upper()
        if ap_u == "PM" and h_i != 12:
            h_i += 12
        if ap_u == "AM" and h_i == 12:
            h_i = 0
        try:
            return datetime(infer_year, int(mo), int(d), h_i, mi_i, tzinfo=tz)
        except ValueError:
            return None

    try:
        ts = pd.to_datetime(s, utc=True)
        return ts.to_pydatetime().astimezone(tz)
    except Exception:
        return None


def default_combined_path(d: date) -> Path:
    ds = d.isoformat()
    return ROOT / "outputs" / ds / f"combined_slate_tickets_{ds}.xlsx"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Parlays from combined slate (optional: upcoming games only).")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="Slate folder date")
    ap.add_argument("--combined", type=Path, help="Path to combined_slate_tickets_*.xlsx")
    ap.add_argument("--tz", default="America/New_York", help="Timezone for sheet times (default US Eastern)")
    ap.add_argument(
        "--now",
        metavar="ISO",
        help="Override current moment, e.g. 2026-03-28T17:00:00-04:00 (otherwise real clock)",
    )
    ap.add_argument(
        "--include-no-time",
        action="store_true",
        help="Keep rows with missing/unparseable Game Time (default: drop when filtering upcoming)",
    )
    ap.add_argument(
        "--all-games",
        action="store_true",
        help="Do not filter by start time (include every row like the old script)",
    )
    args = ap.parse_args()

    if args.combined:
        comb = args.combined if args.combined.is_absolute() else ROOT / args.combined
    elif args.date:
        comb = default_combined_path(date.fromisoformat(args.date))
    else:
        ap.error("Provide --date YYYY-MM-DD or --combined path/to.xlsx")

    year_i = date.today().year
    for part in comb.parts:
        m = _RE_SLATE_DIR.match(part)
        if m:
            year_i = int(m.group(1))
            break

    tz = ZoneInfo(args.tz)
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
    else:
        now = datetime.now(tz)

    df = pd.read_excel(comb, sheet_name="Full Slate")
    df = df.rename(columns=lambda x: str(x).strip())
    df = df.dropna(subset=[RS])
    df = df.sort_values(RS, ascending=False)

    def gkey(r: pd.Series) -> tuple:
        gt = r.get("Game Time", "")
        if pd.isna(gt):
            gt = ""
        return (str(r.get("Sport", "")), str(r.get("Team", "")), str(r.get("Opp", "")), str(gt))

    if not args.all_games:
        times: list[datetime | None] = []
        for _, r in df.iterrows():
            times.append(parse_game_time(r.get("Game Time"), infer_year=year_i, tz=tz))
        df = df.copy()
        df["_kickoff"] = times
        if args.include_no_time:
            mask = df["_kickoff"].isna() | (df["_kickoff"] > now)
        else:
            mask = df["_kickoff"].notna() & (df["_kickoff"] > now)
        df = df.loc[mask].drop(columns=["_kickoff"])

    seen_prop: set[tuple] = set()
    rows: list[pd.Series] = []
    for _, r in df.iterrows():
        pid = (
            str(r.get("Player", "")),
            str(r.get("Prop", "")),
            r.get("Line"),
            str(r.get("Dir", "")),
        )
        if pid in seen_prop:
            continue
        seen_prop.add(pid)
        rows.append(r)

    def leg_str(r: pd.Series) -> str:
        hr = r.get("Hit Rate", "")
        if pd.isna(hr):
            hr = ""
        gt = r.get("Game Time", "")
        if pd.isna(gt):
            gt = ""
        return (
            f"{r['Sport']} | {r['Player']} ({r.get('Team', '')}) {r.get('Dir', '')} "
            f"{r.get('Line', '')} {r.get('Prop', '')} "
            f"[{r.get('Pick Type', '')}] Tier {r.get('Tier', '')} "
            f"RS={float(r[RS]):.2f} HR={hr} | {gt}"
        )

    used_games: set[tuple] = set()
    used_players: set[str] = set()
    picked: list[pd.Series] = []
    for r in rows:
        if len(picked) >= 14:
            break
        pl = str(r.get("Player", ""))
        g = gkey(r)
        if not pl or pl in used_players or g in used_games:
            continue
        used_games.add(g)
        used_players.add(pl)
        picked.append(r)
    legs = [leg_str(r) for r in picked]

    used_sports: set[str] = set()
    used_g2: set[tuple] = set()
    used_p2: set[str] = set()
    cross: list[pd.Series] = []
    for r in rows:
        sp = str(r.get("Sport", "")).strip()
        if sp in used_sports:
            continue
        pl = str(r.get("Player", ""))
        g = gkey(r)
        if not pl or pl in used_p2 or g in used_g2:
            continue
        used_sports.add(sp)
        used_g2.add(g)
        used_p2.add(pl)
        cross.append(r)
    cross_legs = [leg_str(r) for r in cross]

    print("Source:", comb)
    print(f"Now ({args.tz}): {now.isoformat()}")
    if args.all_games:
        print("Filter: none (--all-games)\n")
    else:
        print(
            "Filter: Game Time parsed and kickoff > now"
            + ("; unparseable/missing times excluded" if not args.include_no_time else "; unparseable/missing allowed if --include-no-time")
            + "\n"
        )

    for n in (3, 4, 5, 6):
        print(f"=== {n}-LEG (best RS, 1 leg / game) ===")
        if len(legs) < n:
            print(f"  Only {len(legs)} legs available after filters.\n")
            continue
        for i, L in enumerate(legs[:n], 1):
            print(f"  {i}. {L}")
        print()

    print("=== CROSS-SPORT (first top leg per sport, 1 game each) ===")
    for n in (3, 4, 5, 6):
        print(f"--- {n}-LEG ---")
        if len(cross_legs) < n:
            print(f"  Only {len(cross_legs)} sports/legs available.\n")
            continue
        for i, L in enumerate(cross_legs[:n], 1):
            print(f"  {i}. {L}")
        print()


if __name__ == "__main__":
    main()
