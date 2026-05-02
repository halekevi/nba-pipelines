"""One-off reconcile pre-analysis props vs on-disk step8/step6 (run from repo root)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def find_player_rows(df: pd.DataFrame, player_col: str, first: str, last: str) -> pd.DataFrame:
    p = df[player_col].astype(str)
    m = p.str.contains(re.escape(first), case=False, na=False) & p.str.contains(
        re.escape(last), case=False, na=False
    )
    return df.loc[m].copy()


def load_nba() -> pd.DataFrame:
    p = ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx"
    if not p.exists():
        p = ROOT / "Sports" / "NBA" / "step8_all_direction_clean.xlsx"
    return pd.read_excel(p, sheet_name="ALL", engine="openpyxl")


def load_mlb() -> pd.DataFrame:
    for p in (
        ROOT / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
        ROOT / "Sports" / "MLB" / "outputs" / "step8_mlb_direction_clean.xlsx",
    ):
        if p.exists():
            return pd.read_excel(p, sheet_name="ALL", engine="openpyxl")
    return pd.DataFrame()


def load_cbb() -> pd.DataFrame:
    p = ROOT / "Sports" / "CBB" / "step6_ranked_cbb.xlsx"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_excel(p, sheet_name="ALL", engine="openpyxl")


def prop_match(row_prop: str, want: str) -> bool:
    a, b = norm(row_prop), norm(want)
    if not b:
        return True
    keys = [x for x in re.split(r"[^a-z0-9]+", b) if len(x) > 2]
    return all(k in a for k in keys) if keys else True


def main() -> None:
    nba = load_nba()
    mlb = load_mlb()
    cbb = load_cbb()

    # (name, prop keywords, sport)
    tier_list = [
        ("Bryce Harper", "total bases", "MLB"),
        ("Kawhi Leonard", "points", "NBA"),
        ("Devin Booker", "points", "NBA"),
        ("Domantas Sabonis", "rebounds", "NBA"),
        ("Stephen Curry", "3", "NBA"),
        ("Jayson Tatum", "points", "NBA"),
        ("Giannis Antetokounmpo", "pra", "NBA"),
        ("James Harden", "assists", "NBA"),
        ("Paolo Banchero", "points", "NBA"),
        ("Luka Doncic", "points", "NBA"),
        ("Anthony Davis", "rebounds", "NBA"),
        ("Kevin Durant", "points", "NBA"),
        ("Kyle Schwarber", "total bases", "MLB"),
        ("Shai Gilgeous-Alexander", "points", "NBA"),
        ("Donovan Mitchell", "points", "NBA"),
    ]

    okc = []
    cle = []

    print("=== NBA blowout games rows (OKC, CLE stars) ===")
    if len(nba):
        for nm in ["Shai Gilgeous-Alexander", "Jalen Williams", "Chet Holmgren"]:
            sp = nba[nba["Player"].astype(str).str.contains(nm.split()[-1], case=False, na=False)]
            if len(sp):
                print(nm, len(sp))
        for nm in ["Donovan Mitchell", "Darius Garland", "Evan Mobley"]:
            sp = nba[nba["Player"].astype(str).str.contains(nm.split()[-1], case=False, na=False)]
            if len(sp):
                print(nm, len(sp))

    print("\n=== RECONCILE TIER LIST ===")
    results = []
    for full, pkw, sport in tier_list:
        parts = full.split()
        first, last = parts[0], parts[-1]
        if sport == "NBA":
            sub = find_player_rows(nba, "Player", first, last)
            if pkw:
                sub = sub[sub["Prop"].astype(str).apply(lambda x: prop_match(x, pkw))]
            if len(sub) == 0:
                results.append((full, pkw, None))
                continue
            # prefer OVER for goblin/demon
            r = sub.sort_values("Rank Score", ascending=False).iloc[0]
            results.append((full, pkw, r))
        elif sport == "MLB":
            if mlb.empty:
                results.append((full, pkw, None))
                continue
            sub = find_player_rows(mlb, "Player", first, last)
            if pkw:
                sub = sub[sub["Prop"].astype(str).apply(lambda x: prop_match(x, pkw))]
            if len(sub) == 0:
                results.append((full, pkw, None))
                continue
            r = sub.sort_values("Rank Score", ascending=False).iloc[0]
            results.append((full, pkw, r))
        else:
            results.append((full, pkw, None))

    for full, pkw, r in results:
        if r is None:
            print(f"MISS {full} | {pkw}")
            continue
        print(
            f"OK {full} | {pkw} | {r.get('Player')} | {r.get('Prop')} | {r.get('Direction')} | "
            f"PT={r.get('Pick Type')} | ml={r.get('ML Prob')} | l5={r.get('Hit Rate (5g)')} | "
            f"edge={r.get('Edge')} | score={r.get('Rank Score')}"
        )

    print("\n=== COORS PHI batters (pipeline) ===")
    if len(mlb):
        phi = mlb[
            mlb["Team"].astype(str).str.upper().eq("PHI")
            | mlb["Opp"].astype(str).str.upper().eq("PHI")
        ]
        # Coors = COL home
        col = phi[
            phi["Team"].astype(str).str.upper().eq("COL")
            | phi["Opp"].astype(str).str.upper().eq("COL")
        ]
        hit = col[col["Player Type"].astype(str).str.lower().eq("batter")]
        print("PHI@COL batter rows", len(hit))
        if len(hit):
            show = hit[
                [
                    "Player",
                    "Prop",
                    "Direction",
                    "Pick Type",
                    "Tier",
                    "ML Prob",
                    "Hit Rate (5g)",
                    "Edge",
                    "Rank Score",
                ]
            ].sort_values("Rank Score", ascending=False)
            print(show.head(25).to_string())

    print("\n=== COORS pitcher K props (fade) ===")
    if len(mlb):
        pit = mlb[mlb["Player Type"].astype(str).str.lower().eq("pitcher")]
        colp = pit[
            pit["Team"].astype(str).str.upper().isin(["COL", "PHI"])
            & pit["Opp"].astype(str).str.upper().isin(["COL", "PHI", "PHILADELPHIA"])
        ]
        # broader: any game with COL
        col_game = pit[
            pit["Team"].astype(str).str.upper().eq("COL")
            | pit["Opp"].astype(str).str.upper().eq("COL")
        ]
        kprop = col_game[
            col_game["Prop"].astype(str).str.lower().str.contains("strikeout|k ", na=False)
        ]
        print("Pitcher K props COL game", len(kprop))
        if len(kprop):
            print(
                kprop[["Player", "Team", "Opp", "Prop", "Direction", "ML Prob"]]
                .head(15)
                .to_string()
            )


if __name__ == "__main__":
    main()
