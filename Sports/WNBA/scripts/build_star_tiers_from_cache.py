#!/usr/bin/env python3
"""Rebuild wnba_star_tiers.csv from ESPN cache + manual tier-1 overrides."""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd

_DATA = Path(__file__).resolve().parents[1] / "data"
_CACHE = Path(__file__).resolve().parents[1] / "wnba_espn_cache.csv"
_OUT = _DATA / "wnba_star_tiers.csv"

TIER1 = {
    "a ja wilson",
    "breanna stewart",
    "sabrina ionescu",
    "kelsey plum",
    "napheesa collier",
    "alyssa thomas",
    "jonquel jones",
    "jewell loyd",
    "dewanna bonner",
    "skylar diggins smith",
    "brittney griner",
    "dearica hamby",
    "kahleah copper",
    "jackie young",
    "chelsea gray",
}
TEAM_MAP = {
    "LV": "LVA",
    "LA": "LAS",
    "PHO": "PHX",
    "CONN": "CON",
    "WSH": "WSH",
    "NY": "NYL",
    "GS": "GSV",
    "GSW": "GSV",
}


def norm(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s).strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def main() -> None:
    existing = pd.read_csv(_OUT, encoding="utf-8-sig") if _OUT.exists() else pd.DataFrame()
    manual: dict[str, tuple[int, str, str]] = {}
    if not existing.empty:
        for r in existing.itertuples(index=False):
            manual[norm(r.player_name)] = (
                int(r.star_tier),
                str(r.team_abbreviation),
                str(getattr(r, "notes", "")),
            )

    df = pd.read_csv(_CACHE, low_memory=False, encoding="utf-8-sig")
    df["PLAYER_NORM"] = df["PLAYER_NAME"].map(norm)
    df["TEAM"] = df["TEAM"].astype(str).str.upper().map(lambda x: TEAM_MAP.get(x, x))
    agg = (
        df.groupby(["PLAYER_NAME", "PLAYER_NORM", "TEAM"], as_index=False)
        .agg(avg_min=("MIN", "mean"), games=("MIN", "count"))
        .sort_values(["avg_min", "games"], ascending=[False, False])
    )

    rows: list[dict] = []
    seen: set[str] = set()
    for r in agg.itertuples():
        if r.PLAYER_NORM in seen:
            continue
        seen.add(r.PLAYER_NORM)
        if r.PLAYER_NORM in manual:
            tier, team, note = manual[r.PLAYER_NORM]
        elif r.PLAYER_NORM in TIER1:
            tier, team, note = 1, r.TEAM, "Franchise star"
        elif r.avg_min >= 14:
            tier, team, note = 2, r.TEAM, "Reliable starter"
        else:
            tier, team, note = 3, r.TEAM, "Role player"
        rows.append(
            {
                "player_name": r.PLAYER_NAME,
                "star_tier": tier,
                "team_abbreviation": team,
                "notes": note,
            }
        )

    for pnorm, (tier, team, note) in manual.items():
        if pnorm in seen:
            continue
        name = existing.loc[existing["player_name"].map(norm) == pnorm, "player_name"].iloc[0]
        rows.append(
            {
                "player_name": name,
                "star_tier": tier,
                "team_abbreviation": team,
                "notes": note or {1: "Franchise star", 2: "Reliable starter", 3: "Role player"}[tier],
            }
        )
        seen.add(pnorm)

    out = pd.DataFrame(rows).drop_duplicates(subset=["player_name"]).sort_values(
        ["star_tier", "player_name"]
    )
    out.to_csv(_OUT, index=False, encoding="utf-8-sig")
    print(
        f"Wrote {_OUT} — {len(out)} players "
        f"(T1={(out.star_tier == 1).sum()}, T2={(out.star_tier == 2).sum()}, T3={(out.star_tier == 3).sum()})"
    )


if __name__ == "__main__":
    main()
