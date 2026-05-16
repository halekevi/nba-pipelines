"""Aggregate hit rates by pick type across every graded slate JSON.

Reads every mobile/www/graded_props_*.json in this repo, drops invalid
Goblin/Demon UNDER rows (per scripts/graded_stratification_report.py), keeps
only HIT/MISS (drops VOID/PUSH/missing), and emits:

  outputs/winners_by_pick_type/by_pick_type.csv
  outputs/winners_by_pick_type/by_sport_pick_type.csv
  outputs/winners_by_pick_type/by_pipeline_prop_pick.csv
  outputs/winners_by_pick_type/top_consistent_winners.csv

A console summary is also printed.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
GRADED_DIR = REPO / "mobile" / "www"
OUT_DIR = REPO / "outputs" / "winners_by_pick_type"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DATE_RE = re.compile(r"graded_props_(\d{4}-\d{2}-\d{2})\.json$")


def _load_all() -> pd.DataFrame:
    rows: list[dict] = []
    files = sorted(GRADED_DIR.glob("graded_props_*.json"))
    for f in files:
        m = DATE_RE.search(f.name)
        date = m.group(1) if m else ""
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[skip] {f.name}: {exc}")
            continue
        props = payload.get("props") if isinstance(payload, dict) else None
        if not isinstance(props, list):
            continue
        for r in props:
            if not isinstance(r, dict):
                continue
            r2 = dict(r)
            r2["_date"] = date
            rows.append(r2)
    df = pd.DataFrame(rows)
    print(f"Loaded {len(df):,} graded prop rows across {len(files)} slate files.")
    return df


def _pick_type_base(s: pd.Series) -> pd.Series:
    v = s.astype(str).str.strip().str.lower()
    out = pd.Series("Standard", index=s.index, dtype=str)
    out.loc[v.eq("goblin")] = "Goblin"
    out.loc[v.eq("demon")] = "Demon"
    out.loc[v.isin(["", "nan", "none", "null", "-", "—", "–", "(missing)"])] = "Standard"
    return out


def _pick_group(base: pd.Series, direction: pd.Series) -> pd.Series:
    d = direction.astype(str).str.strip().str.upper()
    out = base.copy()
    std = base.eq("Standard")
    out.loc[std & d.eq("OVER")] = "Standard OVER"
    out.loc[std & d.eq("UNDER")] = "Standard UNDER"
    out.loc[std & ~d.isin(["OVER", "UNDER"])] = "Standard (no dir)"
    return out


def _wilson_low(hits: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - spread) / denom)


def main() -> None:
    df = _load_all()
    if df.empty:
        print("No graded data found.")
        return

    # Normalize result -> binary (HIT=1, MISS=0; drop void/push/missing).
    res = df.get("result", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
    df = df.assign(_result=res)
    df = df[df["_result"].isin(["HIT", "MISS"])].copy()
    df["is_hit"] = (df["_result"] == "HIT").astype(int)

    # Drop Goblin/Demon UNDER (invalid market side per repo convention).
    pt = df["pick_type"].astype(str).str.strip().str.lower()
    direction = df["direction"].astype(str).str.strip().str.upper()
    bad = pt.isin(["goblin", "demon"]) & direction.eq("UNDER")
    df = df.loc[~bad].copy()

    df["pick_base"] = _pick_type_base(df["pick_type"])
    df["pick_group"] = _pick_group(df["pick_base"], df["direction"])
    df["sport_u"] = df["sport"].astype(str).str.upper().str.strip()
    df["prop_u"] = df["prop"].astype(str).str.strip()

    # Grading-quality flag: per (sport, prop), what fraction of decided rows
    # have actual_value == "0.0" or "". Stat-pull bugs (e.g. NHL shots_on_goal
    # always 0.0) produce 100% UNDER-hits and 0% OVER-hits. We flag groups
    # where this share is dominant so they can be excluded from "trusted" views.
    av = df["actual_value"].astype(str).str.strip()
    df["_actual_missing_or_zero"] = av.isin(["0.0", "0", "0.00", ""]).astype(int)
    grade_quality = (
        df.groupby(["sport_u", "prop_u"], dropna=False)["_actual_missing_or_zero"]
        .mean()
        .reset_index()
        .rename(columns={"_actual_missing_or_zero": "pct_actual_zero_or_blank"})
    )
    df = df.merge(grade_quality, on=["sport_u", "prop_u"], how="left")
    df["grade_trusted"] = (df["pct_actual_zero_or_blank"] < 0.85).astype(int)

    print(f"Decided rows after filters: {len(df):,}")
    date_min = df["_date"].min()
    date_max = df["_date"].max()
    print(f"Date span: {date_min} -> {date_max}")
    print(f"Sports: {sorted(df['sport_u'].unique())}")

    # ----- Aggregate: by pick group (overall) -----
    g_pick = (
        df.groupby("pick_group", dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        .reset_index()
    )
    g_pick["hit_rate"] = g_pick["hits"] / g_pick["n"]
    g_pick["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(g_pick["hits"], g_pick["n"])
    ]
    g_pick = g_pick.sort_values("hit_rate", ascending=False)
    g_pick.to_csv(OUT_DIR / "by_pick_type.csv", index=False)

    # ----- Aggregate: by sport x pick group -----
    g_sp = (
        df.groupby(["sport_u", "pick_group"], dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        .reset_index()
    )
    g_sp["hit_rate"] = g_sp["hits"] / g_sp["n"]
    g_sp["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(g_sp["hits"], g_sp["n"])
    ]
    g_sp = g_sp.sort_values(["sport_u", "hit_rate"], ascending=[True, False])
    g_sp.to_csv(OUT_DIR / "by_sport_pick_type.csv", index=False)

    # ----- Aggregate: by sport x prop x pick group -----
    g_full = (
        df.groupby(["sport_u", "prop_u", "pick_group"], dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        .reset_index()
    )
    g_full["hit_rate"] = g_full["hits"] / g_full["n"]
    g_full["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(g_full["hits"], g_full["n"])
    ]

    # Add per-day consistency: fraction of days where group hit_rate >= threshold.
    daily = (
        df.groupby(["sport_u", "prop_u", "pick_group", "_date"], dropna=False)
        .agg(n_d=("is_hit", "size"), hr_d=("is_hit", "mean"))
        .reset_index()
    )
    daily_winning = daily[daily["n_d"] >= 5].copy()
    daily_winning["winning_day"] = (daily_winning["hr_d"] >= 0.55).astype(int)
    consistency = (
        daily_winning.groupby(["sport_u", "prop_u", "pick_group"], dropna=False)
        .agg(
            n_days=("winning_day", "size"),
            winning_days=("winning_day", "sum"),
            mean_daily_hr=("hr_d", "mean"),
            std_daily_hr=("hr_d", "std"),
        )
        .reset_index()
    )
    consistency["pct_winning_days"] = consistency["winning_days"] / consistency["n_days"].clip(lower=1)
    g_full = g_full.merge(
        consistency,
        how="left",
        on=["sport_u", "prop_u", "pick_group"],
    )
    g_full = g_full.sort_values(
        ["sport_u", "pick_group", "wilson_low"], ascending=[True, True, False]
    )
    g_full.to_csv(OUT_DIR / "by_pipeline_prop_pick.csv", index=False)

    # Carry grading-quality share into the per-(sport, prop, pick) table.
    g_full = g_full.merge(grade_quality, on=["sport_u", "prop_u"], how="left")

    # ----- Top consistent winners (overall) -----
    eligible = g_full[(g_full["n"] >= 100) & (g_full["pick_group"] != "Standard (no dir)")].copy()
    eligible["score"] = (
        0.55 * eligible["wilson_low"]
        + 0.30 * eligible["pct_winning_days"].fillna(0.0)
        + 0.15 * (1.0 - eligible["std_daily_hr"].fillna(0.5).clip(0, 1))
    )
    top = eligible.sort_values("score", ascending=False).head(60)
    top.to_csv(OUT_DIR / "top_consistent_winners.csv", index=False)

    # Trusted view: drop (sport, prop) groups where actual_value is
    # dominantly 0.0/blank (broken stat ingest like NHL shots_on_goal).
    trusted = eligible[eligible["pct_actual_zero_or_blank"] < 0.85].copy()
    trusted_top = trusted.sort_values("score", ascending=False).head(40)
    trusted_top.to_csv(OUT_DIR / "top_consistent_winners_trusted.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))

    print("\n=== HIT RATES BY PICK TYPE (all sports combined) ===")
    print(g_pick.to_string(index=False))

    print("\n=== HIT RATES BY SPORT x PICK TYPE (n>=200) ===")
    big = g_sp[g_sp["n"] >= 200].copy()
    print(big.to_string(index=False))

    print("\n=== TOP 30 RAW MOST CONSISTENT WINNING PROP/PICK COMBOS (n>=100) ===")
    cols = [
        "sport_u", "prop_u", "pick_group", "n", "hits",
        "hit_rate", "wilson_low", "n_days", "pct_winning_days", "mean_daily_hr",
        "pct_actual_zero_or_blank",
    ]
    cols = [c for c in cols if c in top.columns]
    print(top[cols].head(30).to_string(index=False))

    print("\n=== TOP 30 TRUSTED CONSISTENT WINNERS (drops broken-grade groups, n>=100) ===")
    print(trusted_top[cols].head(30).to_string(index=False))

    suspect = (
        grade_quality[grade_quality["pct_actual_zero_or_blank"] >= 0.85]
        .sort_values(["sport_u", "pct_actual_zero_or_blank"], ascending=[True, False])
    )
    suspect.to_csv(OUT_DIR / "suspect_grading_groups.csv", index=False)
    print(f"\n=== {len(suspect)} (sport, prop) groups flagged with broken stat ingest (>=85% actual=0/blank) ===")
    print(suspect.head(20).to_string(index=False))

    print(f"\nWrote: {OUT_DIR}")


if __name__ == "__main__":
    main()
