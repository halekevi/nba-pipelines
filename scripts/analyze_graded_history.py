#!/usr/bin/env python3
"""
Full backdated graded-prop analysis across all sports.

Reads mobile/www/graded_props_*.json (read-only), prints slice/edge/streak/player
reports, and writes data/graded_analysis_latest.json for the ticket builder.

Usage:
  py -3.14 scripts/analyze_graded_history.py
  py -3.14 scripts/analyze_graded_history.py --sport NBA
  py -3.14 scripts/analyze_graded_history.py --min-n 50
  py -3.14 scripts/analyze_graded_history.py --days 30
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from scipy.stats import pearsonr
except ImportError:
    pearsonr = None  # type: ignore[misc, assignment]

_REPO = Path(__file__).resolve().parent.parent
_GRADED_DIR = _REPO / "mobile" / "www"
_OUT_JSON = _REPO / "data" / "graded_analysis_latest.json"
_RETRAIN_CSV = _REPO / "data" / "retrain_dataset.csv"

HR_STRONG = 0.60
HR_OK = 0.50
HR_WEAK = 0.35

EDGE_BUCKETS = [
    ("<1.0", lambda e: e < 1.0),
    ("1.0-2.0", lambda e: (e >= 1.0) & (e < 2.0)),
    ("2.0-3.0", lambda e: (e >= 2.0) & (e < 3.0)),
    ("3.0-4.0", lambda e: (e >= 3.0) & (e < 4.0)),
    (">=4.0", lambda e: e >= 4.0),
]

STREAK_ORDER = ("HOT", "WARM", "NEUTRAL", "COLD")


def _norm_sport(s: object) -> str:
    u = str(s or "").strip().upper()
    if u in ("SOC", "FOOTBALL"):
        return "SOCCER"
    return u or "UNKNOWN"


def _norm_pick(s: object) -> str:
    raw = str(s or "").strip()
    if raw in ("", "—", "–", "-", "NaN", "nan", "None", "none"):
        return "standard"
    return raw.casefold()


def _norm_dir(s: object) -> str:
    d = str(s or "").strip().upper()
    if d in ("OVER", "O"):
        return "OVER"
    if d in ("UNDER", "U"):
        return "UNDER"
    return d


def _parse_hit(result: object) -> int | None:
    if result is None or (isinstance(result, float) and np.isnan(result)):
        return None
    t = str(result).strip().upper()
    if t in ("HIT", "WIN", "W", "1", "TRUE"):
        return 1
    if t in ("MISS", "LOSS", "L", "0", "FALSE"):
        return 0
    if t in ("VOID", "PUSH", "DNP", "CANCELLED", "CANCELED"):
        return None
    try:
        v = int(float(result))
        if v in (0, 1):
            return v
    except (TypeError, ValueError):
        pass
    return None


def _as_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y")


def _hr_flag(hr: float) -> str:
    if hr >= HR_STRONG:
        return "STRONG"
    if hr >= HR_OK:
        return "OK"
    if hr >= HR_WEAK:
        return "WEAK"
    return "AVOID"


def _hr_emoji(flag: str) -> str:
    return {"STRONG": "🟢", "OK": "🟡", "WEAK": "🔴", "AVOID": "⛔"}.get(flag, "")


def _norm_prop_type(prop: object) -> str:
    s = str(prop or "").strip()
    if not s:
        return "Other"
    sl = s.casefold()
    rules = [
        (r"point", "Points"),
        (r"rebound", "Rebounds"),
        (r"assist", "Assists"),
        (r"steal", "Steals"),
        (r"block", "Blocks"),
        (r"3-?pt|three", "3-PT Made"),
        (r"turnover", "Turnovers"),
        (r"goal", "Goals"),
        (r"shot", "Shots"),
        (r"save", "Saves"),
        (r"strikeout|k\'?s", "Strikeouts"),
        (r"hit", "Hits"),
        (r"run", "Runs"),
        (r"rbi", "RBIs"),
        (r"walk", "Walks"),
        (r"ace|serve", "Aces"),
        (r"double", "Double Faults"),
        (r"pass", "Passing"),
        (r"receiv", "Receiving"),
        (r"rush", "Rushing"),
    ]
    for pat, label in rules:
        if re.search(pat, sl):
            return label
    return s[:40]


def _join_key(row: pd.Series) -> str:
    return "|".join(
        [
            str(row.get("file_date", ""))[:10],
            _norm_sport(row.get("sport")),
            str(row.get("player", "")).casefold().strip(),
            _norm_prop_type(row.get("prop_type")),
            str(row.get("line", "")),
            _norm_pick(row.get("pick_type")),
            _norm_dir(row.get("direction")),
        ]
    )


def load_graded_json(
    *,
    sport: str | None = None,
    days: int | None = None,
    min_date: str | None = None,
    include_suspect: bool = False,
) -> pd.DataFrame:
    paths = sorted(
        p
        for p in _GRADED_DIR.glob("graded_props_*.json")
        if ".bak_" not in p.name
    )
    if days and days > 0:
        paths = paths[-days:]
    min_d = str(min_date or "").strip()[:10]
    rows: list[dict[str, Any]] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_date = str(raw.get("date") or path.stem.replace("graded_props_", ""))[:10]
        if min_d and len(min_d) == 10 and file_date < min_d:
            continue
        chunk = raw.get("props", raw.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            sp = _norm_sport(r.get("sport"))
            if sport and sp != _norm_sport(sport):
                continue
            if not include_suspect and r.get("grading_suspect"):
                continue
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            edge = pd.to_numeric(r.get("edge"), errors="coerce")
            rows.append(
                {
                    "player": str(r.get("player", "")).strip(),
                    "sport": sp,
                    "prop_type": _norm_prop_type(r.get("prop")),
                    "prop_raw": str(r.get("prop", "")),
                    "pick_type": _norm_pick(r.get("pick_type")),
                    "direction": _norm_dir(r.get("direction") or r.get("over_under")),
                    "tier": str(r.get("tier", "")).strip().upper() or "—",
                    "edge": edge,
                    "abs_edge": float(abs(edge)) if pd.notna(edge) else np.nan,
                    "ml_prob": pd.to_numeric(r.get("ml_prob"), errors="coerce"),
                    "blended_score": pd.to_numeric(r.get("blended_score"), errors="coerce"),
                    "composite_hit_rate": pd.to_numeric(
                        r.get("composite_hit_rate") or r.get("hit_rate"), errors="coerce"
                    ),
                    "line": pd.to_numeric(r.get("line"), errors="coerce"),
                    "projection": pd.to_numeric(r.get("projection"), errors="coerce"),
                    "actual_value": pd.to_numeric(r.get("actual_value"), errors="coerce"),
                    "hit": int(hit),
                    "graded_at": file_date,
                    "file_date": file_date,
                    "on_ticket": _as_bool(r.get("on_ticket")),
                    "on_shadow_ticket": _as_bool(r.get("on_shadow_ticket")),
                    "ticket_id": r.get("ticket_id"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["_key"] = df.apply(_join_key, axis=1)
    df = df.drop_duplicates(subset=["_key"], keep="last").drop(columns=["_key"])
    df["graded_at"] = pd.to_datetime(df["graded_at"], errors="coerce")
    return df


def enrich_from_retrain(df: pd.DataFrame) -> pd.DataFrame:
    """Left-merge blended_score from retrain_dataset.csv when present."""
    if df.empty or not _RETRAIN_CSV.is_file():
        return df
    usecols = [
        "file_date",
        "sport",
        "player",
        "prop",
        "line",
        "pick_type",
        "direction",
        "blended_score",
        "result_binary",
    ]
    parts: list[pd.DataFrame] = []
    try:
        for chunk in pd.read_csv(
            _RETRAIN_CSV,
            usecols=usecols,
            chunksize=150_000,
            encoding="utf-8",
            encoding_errors="replace",
        ):
            chunk = chunk[chunk["result_binary"].notna()].copy()
            if chunk.empty:
                continue
            chunk["sport"] = chunk["sport"].map(_norm_sport)
            chunk["pick_type"] = chunk["pick_type"].map(_norm_pick)
            chunk["direction"] = chunk["direction"].map(_norm_dir)
            chunk["prop_type"] = chunk["prop"].map(_norm_prop_type)
            chunk["line"] = pd.to_numeric(chunk["line"], errors="coerce")
            chunk["player_key"] = chunk["player"].astype(str).str.casefold().str.strip()
            parts.append(
                chunk[
                    [
                        "file_date",
                        "sport",
                        "player_key",
                        "prop_type",
                        "line",
                        "pick_type",
                        "direction",
                        "blended_score",
                    ]
                ]
            )
    except Exception as exc:
        print(f"[warn] retrain merge skipped: {exc}")
        return df
    if not parts:
        return df
    rt = pd.concat(parts, ignore_index=True)
    rt = rt.drop_duplicates(
        subset=["file_date", "sport", "player_key", "prop_type", "line", "pick_type", "direction"],
        keep="last",
    )
    out = df.copy()
    out["player_key"] = out["player"].astype(str).str.casefold().str.strip()
    merged = out.merge(
        rt,
        on=["file_date", "sport", "player_key", "prop_type", "line", "pick_type", "direction"],
        how="left",
        suffixes=("", "_rt"),
    )
    if "blended_score_rt" in merged.columns:
        bs = pd.to_numeric(merged["blended_score"], errors="coerce")
        merged["blended_score"] = bs.fillna(pd.to_numeric(merged["blended_score_rt"], errors="coerce"))
        merged = merged.drop(columns=["blended_score_rt"], errors="ignore")
    merged = merged.drop(columns=["player_key"], errors="ignore")
    n = int(merged["blended_score"].notna().sum())
    print(f"[enrich] blended_score from retrain: {n:,}/{len(merged):,} ({100 * n / len(merged):.1f}%)")
    return merged


def compute_rolling_l10(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill l10_over_pct / l10_under_pct from prior graded actuals vs line."""
    out = df.copy()
    out["stat_over"] = np.where(
        out["actual_value"].notna() & out["line"].notna(),
        (out["actual_value"] > out["line"]).astype(float),
        np.nan,
    )
    out = out.sort_values(["player", "prop_type", "file_date", "graded_at"])
    gcols = ["player", "prop_type"]
    over_rates: list[float] = []
    under_rates: list[float] = []
    for _, grp in out.groupby(gcols, sort=False):
        prev = grp["stat_over"].shift(1)
        roll = prev.rolling(10, min_periods=3).mean()
        over_rates.extend(roll.tolist())
        under_rates.extend((1.0 - roll).tolist())
    out["l10_over_pct"] = over_rates
    out["l10_under_pct"] = under_rates
    cov = int(out["l10_over_pct"].notna().sum())
    print(f"[l10] rolling L10 from graded history: {cov:,}/{len(out):,} ({100 * cov / len(out):.1f}%)")
    return out


def streak_category(row: pd.Series) -> str | None:
    direction = _norm_dir(row.get("direction"))
    if direction == "OVER":
        rate = row.get("l10_over_pct")
    elif direction == "UNDER":
        rate = row.get("l10_under_pct")
    else:
        return None
    if pd.isna(rate):
        return None
    if rate >= 0.70:
        return "HOT"
    if rate >= 0.60:
        return "WARM"
    if rate >= 0.40:
        return "NEUTRAL"
    return "COLD"


def slice_table(
    df: pd.DataFrame,
    group_cols: list[str],
    *,
    min_n: int,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = (
        df.groupby(group_cols, dropna=False)
        .agg(
            hit_rate=("hit", "mean"),
            n=("hit", "count"),
            mean_edge=("edge", "mean"),
            mean_ml_prob=("ml_prob", "mean"),
            mean_blended=("blended_score", "mean"),
        )
        .reset_index()
    )
    g = g[g["n"] >= min_n].sort_values("hit_rate", ascending=False)
    g["flag"] = g["hit_rate"].map(_hr_flag)
    return g


def print_slice_table(title: str, tbl: pd.DataFrame) -> None:
    print(f"\n=== {title} (min n shown in table) ===")
    if tbl.empty:
        print("(no slices)")
        return
    hdr = f"{'Sport':<8} {'Dir':<6} {'Pick':<10} {'Tier':<5} {'HR':>7} {'N':>7} {'Edge':>6} {'ML':>6} {'Flag':>8}"
    print(hdr)
    print("-" * len(hdr))
    for _, r in tbl.iterrows():
        sp = str(r.get("sport", r.get("Sport", "")))[:8]
        dr = str(r.get("direction", ""))[:6]
        pt = str(r.get("pick_type", ""))[:10]
        ti = str(r.get("tier", ""))[:5]
        hr = f"{100 * r['hit_rate']:.1f}%"
        em = _hr_emoji(str(r["flag"]))
        ml = f"{r['mean_ml_prob']:.2f}" if pd.notna(r.get("mean_ml_prob")) else "—"
        print(
            f"{sp:<8} {dr:<6} {pt:<10} {ti:<5} {hr:>7} {int(r['n']):>7} "
            f"{r.get('mean_edge', float('nan')):>6.2f} {ml:>6} {em} {r['flag']}"
        )


def edge_bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    sub = df[df["abs_edge"].notna()].copy()
    for sport, gsp in sub.groupby("sport"):
        e = gsp["abs_edge"]
        for label, fn in EDGE_BUCKETS:
            mask = fn(e)
            n = int(mask.sum())
            if n == 0:
                continue
            rows.append(
                {
                    "sport": sport,
                    "edge_bucket": label,
                    "hit_rate": float(gsp.loc[mask, "hit"].mean()),
                    "n": n,
                }
            )
    return pd.DataFrame(rows)


def line_bucket(prop_type: str, line: float) -> str | None:
    if pd.isna(line):
        return None
    pt = prop_type.casefold()
    if "point" in pt:
        bounds = [10, 15, 20, 25, 30]
        labels = ["<10", "10-15", "15-20", "20-25", "25-30", ">30"]
    elif "rebound" in pt:
        bounds = [4, 6, 8]
        labels = ["<4", "4-6", "6-8", ">8"]
    elif "assist" in pt:
        bounds = [3, 5, 7]
        labels = ["<3", "3-5", "5-7", ">7"]
    else:
        return None
    for i, b in enumerate(bounds):
        if line < b:
            return labels[i]
    return labels[-1]


def line_bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    tmp = df.copy()
    tmp["line_bucket"] = [
        line_bucket(pt, ln) for pt, ln in zip(tmp["prop_type"], tmp["line"], strict=False)
    ]
    tmp = tmp[tmp["line_bucket"].notna()]
    # percentile buckets for Other prop types
    other = df[~df["prop_type"].str.casefold().str.contains("point|rebound|assist")].copy()
    if not other.empty and other["line"].notna().any():
        for sport, gsp in other.groupby("sport"):
            lines = gsp["line"].dropna()
            if len(lines) < 30:
                continue
            qs = lines.quantile([0.2, 0.4, 0.6, 0.8]).tolist()
            for _, row in gsp.iterrows():
                ln = row["line"]
                if pd.isna(ln):
                    continue
                b = sum(ln >= q for q in qs)
                row = dict(row)
                row["line_bucket"] = f"P{b}-{b+1}"
            # aggregate percentile buckets
            gsp = gsp.copy()
            edges = pd.Series([-np.inf, *qs, np.inf]).drop_duplicates().tolist()
            n_bins = len(edges) - 1
            labels = [f"P{i}-{i+1}" for i in range(n_bins)]
            gsp["line_bucket"] = pd.cut(
                gsp["line"],
                bins=edges,
                labels=labels[:n_bins],
                duplicates="drop",
            ).astype(str)
            for (pt, lb), g in gsp.groupby(["prop_type", "line_bucket"]):
                if g["hit"].count() < 30:
                    continue
                rows.append(
                    {
                        "sport": sport,
                        "prop_type": pt,
                        "line_bucket": str(lb),
                        "hit_rate": float(g["hit"].mean()),
                        "n": int(len(g)),
                    }
                )
    for (sport, pt, lb), g in tmp.groupby(["sport", "prop_type", "line_bucket"]):
        if len(g) < 30:
            continue
        rows.append(
            {
                "sport": sport,
                "prop_type": pt,
                "line_bucket": lb,
                "hit_rate": float(g["hit"].mean()),
                "n": int(len(g)),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(
            subset=["sport", "prop_type", "line_bucket"], keep="first"
        ).sort_values(["sport", "hit_rate"], ascending=[True, False])
    return out


def player_tables(df: pd.DataFrame, *, min_props: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    for (player, sport), g in df.groupby(["player", "sport"]):
        n = len(g)
        if n < min_props:
            continue
        hr = float(g["hit"].mean())
        prop_mode = g["prop_type"].mode()
        primary = str(prop_mode.iloc[0]) if len(prop_mode) else ""
        consistency = 1.0 - float(g["hit"].std(ddof=0)) if n > 1 else 1.0
        rows.append(
            {
                "player": player,
                "sport": sport,
                "hit_rate": hr,
                "n": n,
                "primary_prop_type": primary,
                "consistency": consistency,
            }
        )
    all_p = pd.DataFrame(rows)
    if all_p.empty:
        return all_p, all_p
    top = all_p.sort_values("hit_rate", ascending=False).head(30)
    bottom = all_p.sort_values("hit_rate", ascending=True).head(20)
    return top, bottom


def weekly_trends(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["week"] = tmp["graded_at"].dt.to_period("W-MON").astype(str)
    rows: list[dict] = []
    for week, g in tmp.groupby("week"):
        gob = g[g["pick_type"] == "goblin"]
        nba = g[g["sport"] == "NBA"]
        rows.append(
            {
                "week": week,
                "overall_hr": float(g["hit"].mean()),
                "goblin_hr": float(gob["hit"].mean()) if len(gob) else np.nan,
                "nba_hr": float(nba["hit"].mean()) if len(nba) else np.nan,
                "n": len(g),
            }
        )
    return pd.DataFrame(rows).sort_values("week")


def ascii_sparkline(values: list[float], width: int = 40) -> str:
    if not values:
        return ""
    vmin, vmax = min(values), max(values)
    if vmax - vmin < 1e-9:
        return "▃" * min(len(values), width)
    chars = "▁▂▃▄▅▆▇█"
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][:width]
    out = []
    for v in sampled:
        idx = int((v - vmin) / (vmax - vmin) * (len(chars) - 1))
        out.append(chars[idx])
    return "".join(out)


def correlation_edge_hr(df: pd.DataFrame) -> dict[str, Any]:
    results: dict[str, Any] = {}
    per_sport: dict[str, float] = {}
    for sport, g in df.groupby("sport"):
        sub = g[g["abs_edge"].notna()]
        if len(sub) < 50:
            continue
        r = float(sub["abs_edge"].corr(sub["hit"]))
        per_sport[sport] = r
    results["per_sport"] = per_sport
    if per_sport:
        best = max(per_sport, key=lambda k: abs(per_sport[k]))
        results["strongest_sport"] = best
        results["strongest_r"] = per_sport[best]
    # monotonicity across buckets
    bucket = edge_bucket_table(df)
    if bucket.empty:
        results["monotonic"] = False
        return results
    mono = True
    for sport in bucket["sport"].unique():
        b = bucket[bucket["sport"] == sport].sort_values("edge_bucket")
        if len(b) < 2:
            continue
        hrs = b["hit_rate"].tolist()
        if hrs != sorted(hrs):
            mono = False
            break
    results["monotonic"] = mono
    avg_r = float(np.nanmean(list(per_sport.values()))) if per_sport else 0.0
    results["avg_r"] = avg_r
    if avg_r >= 0.08 and mono:
        results["verdict"] = "YES"
    elif avg_r >= 0.03:
        results["verdict"] = "WEAK"
    else:
        results["verdict"] = "NO"
    return results


def correlation_streak_hr(df: pd.DataFrame) -> dict[str, Any]:
    tmp = df.copy()
    tmp["streak_cat"] = tmp.apply(streak_category, axis=1)
    sub = tmp[tmp["streak_cat"].notna()]
    if sub.empty:
        return {"verdict": "NO", "note": "no L10 data"}
    rows: list[dict] = []
    for (sport, direction, cat), g in sub.groupby(["sport", "direction", "streak_cat"]):
        if len(g) < 30:
            continue
        rows.append(
            {
                "sport": sport,
                "direction": direction,
                "streak_cat": cat,
                "hit_rate": float(g["hit"].mean()),
                "n": len(g),
            }
        )
    tbl = pd.DataFrame(rows)
    hot = tbl[tbl["streak_cat"] == "HOT"]["hit_rate"].mean() if (tbl["streak_cat"] == "HOT").any() else np.nan
    cold = tbl[tbl["streak_cat"] == "COLD"]["hit_rate"].mean() if (tbl["streak_cat"] == "COLD").any() else np.nan
    verdict = "NO"
    if pd.notna(hot) and pd.notna(cold) and hot - cold >= 0.03:
        verdict = "YES" if hot - cold >= 0.06 else "WEAK"
    best_row = None
    if not tbl.empty:
        hot_tbl = tbl[tbl["streak_cat"] == "HOT"].sort_values("hit_rate", ascending=False)
        if not hot_tbl.empty:
            r = hot_tbl.iloc[0]
            best_row = f"{r['sport']} {r['direction']}"
    return {
        "verdict": verdict,
        "hot_hr": hot,
        "cold_hr": cold,
        "table": tbl,
        "best_streak_signal": best_row,
    }


def ml_calibration(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    correlated: list[str] = []
    uncorrelated: list[str] = []
    for sport, g in df.groupby("sport"):
        sub = g[g["ml_prob"].notna()]
        if len(sub) < 100:
            continue
        r = float(sub["ml_prob"].corr(sub["hit"]))
        if abs(r) > 0.10:
            correlated.append(f"{sport}(r={r:.2f})")
        else:
            uncorrelated.append(f"{sport}(r={r:.2f})")
    return correlated, uncorrelated


def ticket_section(df: pd.DataFrame) -> None:
    tagged = df[df["on_ticket"]]
    print("\n=== STEP 9: Ticket analysis ===")
    if "ticket_id" not in df.columns or df["ticket_id"].notna().sum() == 0:
        print("ticket_id not present in graded JSON — ticket-level win rate skipped.")
        if tagged.empty:
            print("on_ticket tagged rows: 0")
            return
        hr = float(tagged["hit"].mean())
        print(f"on_ticket leg hit rate: {100 * hr:.1f}% (n={len(tagged):,} legs)")
        for pick, g in tagged.groupby("pick_type"):
            print(f"  pick_type={pick}: {100 * g['hit'].mean():.1f}% (n={len(g)})")
        return
    # ticket_id path (future)
    print("(ticket_id present — full ticket win rate not yet implemented)")


def export_json(
    path: Path,
    *,
    df: pd.DataFrame,
    top_slices: pd.DataFrame,
    avoid_slices: pd.DataFrame,
    prop_rankings: pd.DataFrame,
    top_players: pd.DataFrame,
    bottom_players: pd.DataFrame,
    edge_corr: dict[str, Any],
    streak_sig: dict[str, Any],
    date_min: str,
    date_max: str,
) -> None:
    def slice_records(tbl: pd.DataFrame, *, priority_start: int = 1) -> list[dict]:
        out: list[dict] = []
        for i, r in tbl.reset_index(drop=True).iterrows():
            out.append(
                {
                    "sport": str(r["sport"]),
                    "pick_type": str(r["pick_type"]),
                    "direction": str(r["direction"]),
                    "tier": str(r["tier"]),
                    "hit_rate": round(float(r["hit_rate"]), 4),
                    "n": int(r["n"]),
                    "mean_edge": round(float(r["mean_edge"]), 3) if pd.notna(r.get("mean_edge")) else None,
                    "priority": priority_start + int(i),
                }
            )
        return out

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_range": {"min": date_min, "max": date_max},
        "total_props": int(len(df)),
        "overall_hit_rate": round(float(df["hit"].mean()), 4) if len(df) else 0.0,
        "top_slices": slice_records(top_slices.head(25)),
        "avoid_slices": slice_records(avoid_slices.head(25), priority_start=1),
        "prop_type_rankings": [
            {
                "prop_type": str(r["prop_type"]),
                "direction": str(r["direction"]),
                "hit_rate": round(float(r["hit_rate"]), 4),
                "n": int(r["n"]),
                "mean_edge": round(float(r["mean_edge"]), 3) if pd.notna(r.get("mean_edge")) else None,
            }
            for _, r in prop_rankings.head(40).iterrows()
        ],
        "player_rankings": {
            "top_30": [
                {
                    "player": str(r["player"]),
                    "sport": str(r["sport"]),
                    "hit_rate": round(float(r["hit_rate"]), 4),
                    "n": int(r["n"]),
                    "primary_prop_type": str(r["primary_prop_type"]),
                    "consistency": round(float(r["consistency"]), 3),
                }
                for _, r in top_players.iterrows()
            ],
            "bottom_20": [
                {
                    "player": str(r["player"]),
                    "sport": str(r["sport"]),
                    "hit_rate": round(float(r["hit_rate"]), 4),
                    "n": int(r["n"]),
                    "primary_prop_type": str(r["primary_prop_type"]),
                }
                for _, r in bottom_players.iterrows()
            ],
        },
        "edge_correlations": edge_corr,
        "streak_signals": {
            k: v
            for k, v in streak_sig.items()
            if k != "table"
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="", help="Filter to one sport (e.g. NBA)")
    ap.add_argument("--min-n", type=int, default=30, help="Minimum slice size to display")
    ap.add_argument("--days", type=int, default=0, help="Only last N daily files (0=all)")
    ap.add_argument(
        "--min-date",
        default="",
        metavar="YYYY-MM-DD",
        help="Only graded_props files on/after this date (e.g. 2026-04-01 for clean NBA1Q)",
    )
    ap.add_argument("--no-retrain", action="store_true", help="Skip retrain CSV blended_score merge")
    ap.add_argument(
        "--include-suspect",
        action="store_true",
        help="Include NBA1Q/NBA1H rows flagged grading_suspect (default: exclude)",
    )
    args = ap.parse_args()

    sport_f = args.sport.strip().upper() if args.sport else None
    days = args.days if args.days > 0 else None

    print("=== STEP 1: Load graded JSON ===")
    min_date = str(args.min_date or "").strip()[:10] or None
    df = load_graded_json(
        sport=sport_f,
        days=days,
        min_date=min_date,
        include_suspect=args.include_suspect,
    )
    if df.empty:
        print("No decided props found.", file=sys.stderr)
        return 1
    if min_date:
        print(f"[filter] min_date >= {min_date}")

    if not args.no_retrain:
        df = enrich_from_retrain(df)
    df = compute_rolling_l10(df)

    print("\nRows by sport:")
    for sp, n in df.groupby("sport").size().sort_values(ascending=False).items():
        print(f"  {sp}: {n:,} rows")

    min_n = max(1, int(args.min_n))
    date_min = str(df["file_date"].min())[:10]
    date_max = str(df["file_date"].max())[:10]
    overall_hr = float(df["hit"].mean())

    # Step 2
    slices = slice_table(
        df,
        ["sport", "direction", "pick_type", "tier"],
        min_n=min_n,
    )
    print_slice_table("STEP 2: sport × direction × pick_type × tier", slices)

    # Step 3
    print("\n=== STEP 3: Hit rate by edge bucket ===")
    eb = edge_bucket_table(df)
    if eb.empty:
        print("(no edge data)")
    else:
        print(f"{'Sport':<10} {'Bucket':<10} {'HR':>8} {'N':>8}")
        for _, r in eb.sort_values(["sport", "edge_bucket"]).iterrows():
            print(f"{r['sport']:<10} {r['edge_bucket']:<10} {100*r['hit_rate']:>7.1f}% {int(r['n']):>8}")

    edge_corr = correlation_edge_hr(df)

    # Step 4
    print("\n=== STEP 4: Hit rate by L10 streak category ===")
    df["streak_cat"] = df.apply(streak_category, axis=1)
    streak_tbl = (
        df[df["streak_cat"].notna()]
        .groupby(["sport", "streak_cat"])
        .agg(hit_rate=("hit", "mean"), n=("hit", "count"))
        .reset_index()
    )
    streak_tbl = streak_tbl[streak_tbl["n"] >= min_n].sort_values(["sport", "hit_rate"], ascending=[True, False])
    if streak_tbl.empty:
        print("(insufficient L10 coverage)")
    else:
        for _, r in streak_tbl.iterrows():
            print(f"  {r['sport']:<8} {r['streak_cat']:<8} HR={100*r['hit_rate']:.1f}%  n={int(r['n'])}")

    streak_sig = correlation_streak_hr(df)

    # Step 5
    print("\n=== STEP 5: Hit rate by line bucket (n>=30) ===")
    lb = line_bucket_table(df)
    if lb.empty:
        print("(no line buckets with enough data)")
    else:
        for _, r in lb.head(40).iterrows():
            print(
                f"  {r['sport']:<8} {r['prop_type']:<16} {r['line_bucket']:<8} "
                f"HR={100*r['hit_rate']:.1f}% n={int(r['n'])}"
            )

    # Step 6
    print("\n=== STEP 6: Top / bottom players (min 20 props) ===")
    top_p, bot_p = player_tables(df)
    for label, tbl in (("TOP 30", top_p), ("BOTTOM 20", bot_p)):
        print(f"\n{label}:")
        if tbl.empty:
            print("  (none)")
            continue
        for _, r in tbl.iterrows():
            print(
                f"  {r['player']:<28} {r['sport']:<6} HR={100*r['hit_rate']:.1f}% "
                f"n={int(r['n'])}  {r['primary_prop_type']}"
            )

    # Step 7
    print("\n=== STEP 7: Prop type × direction ===")
    prop_tbl = slice_table(df, ["prop_type", "direction"], min_n=min_n)
    if not prop_tbl.empty:
        print(f"\n{'PropType':<20} {'Dir':<6} {'HR':>7} {'N':>7} {'Edge':>6}")
        for _, r in prop_tbl.head(30).iterrows():
            print(
                f"{str(r['prop_type']):<20} {str(r['direction']):<6} "
                f"{100*r['hit_rate']:>6.1f}% {int(r['n']):>7} {r.get('mean_edge', float('nan')):>6.2f}"
            )

    # Step 8
    print("\n=== STEP 8: Weekly trends ===")
    wt = weekly_trends(df)
    if not wt.empty:
        print(f"{'Week':<12} {'All HR':>8} {'Goblin':>8} {'NBA':>8} {'N':>8}")
        for _, r in wt.iterrows():
            g = f"{100*r['goblin_hr']:.1f}%" if pd.notna(r["goblin_hr"]) else "—"
            nba = f"{100*r['nba_hr']:.1f}%" if pd.notna(r["nba_hr"]) else "—"
            print(f"{r['week']:<12} {100*r['overall_hr']:>7.1f}% {g:>8} {nba:>8} {int(r['n']):>8}")
        spark = ascii_sparkline(wt["overall_hr"].tolist())
        print(f"\nOverall HR trend: {spark}")

    ticket_section(df)

    # Step 10 report
    report_min = max(50, min_n)
    top_bet = slice_table(df, ["sport", "direction", "pick_type", "tier"], min_n=report_min).head(10)
    avoid = slice_table(df, ["sport", "direction", "pick_type", "tier"], min_n=report_min).tail(10)
    prop_all = slice_table(df, ["prop_type", "direction"], min_n=report_min)
    prop_best = prop_all.head(10)
    prop_worst = prop_all.tail(10)
    ml_good, ml_bad = ml_calibration(df)

    print("\n" + "=" * 60)
    print("  === PROPORACLE GRADED DATA ANALYSIS ===")
    print(f"  Date range: {date_min} to {date_max}")
    print(f"  Total decided props: {len(df):,}")
    print(f"  Overall hit rate: {100 * overall_hr:.1f}%")
    print()
    print(f"  TOP 10 SLICES TO BET (min {report_min} props):")
    for i, r in top_bet.reset_index(drop=True).iterrows():
        print(
            f"  {i+1}. {r['sport']} {r['pick_type']} {r['direction']} {r['tier']}: "
            f"{100*r['hit_rate']:.1f}% ({int(r['n'])} props)"
        )
    print()
    print(f"  BOTTOM 10 SLICES TO AVOID (min {report_min} props):")
    for i, r in avoid.reset_index(drop=True).iterrows():
        print(
            f"  {i+1}. {r['sport']} {r['pick_type']} {r['direction']} {r['tier']}: "
            f"{100*r['hit_rate']:.1f}% ({int(r['n'])} props)"
        )
    print()
    print("  EDGE CORRELATION:")
    print(f"  Does higher edge predict higher HR? {edge_corr.get('verdict', 'NO')}")
    if edge_corr.get("strongest_sport"):
        print(
            f"  Strongest edge signal: {edge_corr['strongest_sport']} "
            f"(r={edge_corr.get('strongest_r', 0):.3f})"
        )
    print()
    print("  STREAK SIGNAL:")
    print(f"  Does HOT streak predict higher HR? {streak_sig.get('verdict', 'NO')}")
    if streak_sig.get("best_streak_signal"):
        print(f"  Best streak signal: {streak_sig['best_streak_signal']}")
    print()
    print("  BEST PROP TYPES:")
    for i, r in prop_best.reset_index(drop=True).iterrows():
        print(f"  {i+1}. {r['prop_type']} {r['direction']}: {100*r['hit_rate']:.1f}% ({int(r['n'])} props)")
    print()
    print("  WORST PROP TYPES TO AVOID:")
    for i, r in prop_worst.reset_index(drop=True).iterrows():
        print(f"  {i+1}. {r['prop_type']} {r['direction']}: {100*r['hit_rate']:.1f}% ({int(r['n'])} props)")
    print()
    print("  MODEL CALIBRATION STATUS:")
    print(f"  Sports where ml_prob correlates with hit (|r|>0.10): {', '.join(ml_good) or 'none'}")
    print(f"  Sports where ml_prob is uncorrelated: {', '.join(ml_bad) or 'none'}")
    print()
    print("  TICKET BUILDER RECOMMENDATIONS:")
    if not top_bet.empty:
        for i, r in top_bet.head(3).reset_index(drop=True).iterrows():
            print(
                f"  Priority {i+1}: {r['sport']} {r['pick_type']} {r['direction']} tier {r['tier']} "
                f"({100*r['hit_rate']:.1f}%, n={int(r['n'])})"
            )
    if not avoid.empty:
        avoid_list = ", ".join(
            f"{r['sport']}/{r['pick_type']}/{r['direction']}/{r['tier']}"
            for _, r in avoid.head(5).iterrows()
        )
        print(f"  Avoid: {avoid_list}")
    print("=" * 60)

    export_json(
        _OUT_JSON,
        df=df,
        top_slices=slice_table(df, ["sport", "direction", "pick_type", "tier"], min_n=report_min),
        avoid_slices=avoid,
        prop_rankings=prop_all,
        top_players=top_p,
        bottom_players=bot_p,
        edge_corr=edge_corr,
        streak_sig=streak_sig,
        date_min=date_min,
        date_max=date_max,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
