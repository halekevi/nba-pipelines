#!/usr/bin/env python3
"""
Read-only grader analysis: matrices, player consistency, optional slices.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ensure_local_cache import ensure_local_cache  # noqa: E402

ensure_local_cache(str(REPO_ROOT))

import build_player_consistency as bpc  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "cache" / "player_consistency.db"


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_col(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}


def _col(nc: dict[str, str], *names: str) -> str | None:
    for n in names:
        k = n.strip().lower().replace(" ", "_")
        if k in nc:
            return nc[k]
    return None


def _parse_result(val: Any) -> int | None:
    return bpc._parse_result(val)


def _parse_tier(val: Any) -> str:
    return bpc._parse_tier(val)


def _parse_date(val: Any) -> pd.Timestamp | None:
    return bpc._parse_date(val)


def load_graded_dataframe(
    sport: str | None,
    since: pd.Timestamp | None,
    days: int | None,
    _min_count: int,
    include_synthetic: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    end_d = pd.Timestamp(_today_str())
    start_d = None
    if days is not None:
        start_d = end_d - timedelta(days=int(days))
    if since is not None:
        start_d = since if start_d is None else max(since, start_d)

    for sp, pattern in bpc.GRADED_GLOBS:
        if sport is not None and sp.upper() != sport.upper():
            continue
        out_dir = REPO_ROOT / "outputs"
        if not out_dir.is_dir():
            continue
        for path in out_dir.rglob(pattern):
            df = bpc._read_graded_frame(path)
            if df is None or df.empty:
                continue
            nc = _norm_col(df)
            c_player = _col(nc, "player", "player_name", "Player")
            c_prop = _col(nc, "prop_type", "stat_type", "Prop Type", "prop type")
            c_line = _col(nc, "line", "Line")
            c_dir = _col(nc, "direction", "Direction")
            c_res = _col(nc, "result", "Result")
            c_date = _col(nc, "date", "created_at", "Date")
            c_tier = _col(nc, "tier", "Tier")
            c_edge = _col(nc, "edge", "Edge")
            c_min = _col(nc, "minutes", "avg_minutes", "min", "MIN")
            c_team_pace = _col(nc, "team_pace", "def_pace")
            c_def_tier = _col(nc, "def_tier", "opp_def_tier")
            c_home = _col(nc, "home_away", "home/away")
            c_b2b = _col(nc, "back_to_back", "b2b")
            c_goalie_conf = _col(nc, "goalie_confirmed")
            c_pp = _col(nc, "pp_unit", "pp_tier")
            c_comp = _col(nc, "competition")
            c_mp = _col(nc, "minutes_played", "mins_played")
            c_gs = _col(nc, "game_script_mult", "Game Script Mult", "game_script_multiplier")
            if not all([c_player, c_prop, c_line, c_dir, c_res]):
                continue
            for _, r in df.iterrows():
                hit = _parse_result(r.get(c_res))
                if hit is None:
                    continue
                d = _parse_date(r.get(c_date)) if c_date else None
                if start_d is not None and d is not None and d.normalize() < start_d.normalize():
                    continue
                try:
                    line = float(r.get(c_line))
                except (TypeError, ValueError):
                    continue
                direction = str(r.get(c_dir) or "").strip().upper()
                if direction not in ("OVER", "UNDER"):
                    continue
                prop_raw = str(r.get(c_prop) or "").strip()
                pnorm = bpc._normalize_prop_type(prop_raw, sp)
                tier = _parse_tier(r.get(c_tier) if c_tier else "Standard")
                rec = {
                    "sport": sp,
                    "player_name": str(r.get(c_player) or "").strip(),
                    "prop_type": pnorm,
                    "direction": direction,
                    "tier": tier,
                    "line": line,
                    "hit": hit,
                    "date": d,
                }
                if c_edge:
                    try:
                        rec["edge"] = float(r.get(c_edge))
                    except (TypeError, ValueError):
                        rec["edge"] = None
                else:
                    rec["edge"] = None
                if c_min:
                    rec["minutes"] = pd.to_numeric(r.get(c_min), errors="coerce")
                if c_team_pace:
                    rec["pace_raw"] = r.get(c_team_pace)
                if c_def_tier:
                    rec["def_tier_raw"] = r.get(c_def_tier)
                if c_home:
                    rec["home_away"] = str(r.get(c_home) or "").strip()
                if c_b2b:
                    rec["b2b"] = str(r.get(c_b2b) or "").strip()
                if c_goalie_conf:
                    rec["goalie_confirmed"] = str(r.get(c_goalie_conf) or "").strip()
                if c_pp:
                    rec["pp_unit"] = str(r.get(c_pp) or "").strip()
                if c_comp:
                    rec["competition"] = str(r.get(c_comp) or "").strip()
                if c_mp:
                    rec["minutes_played"] = pd.to_numeric(r.get(c_mp), errors="coerce")
                if c_gs:
                    rec["game_script_mult"] = pd.to_numeric(r.get(c_gs), errors="coerce")
                rows.append(rec)

    if include_synthetic:
        since_sql = since.strftime("%Y-%m-%d") if since is not None else None
        syn_df = bpc.load_synthetic_from_db(str(bpc.SYNTHETIC_GRADED_DB), sport, since_sql)
        if not syn_df.empty:
            for _, r in syn_df.iterrows():
                sp = str(r.get("sport") or "").strip()
                if not sp:
                    continue
                if sport is not None and sp.upper() != sport.upper():
                    continue
                hit = _parse_result(r.get("result"))
                if hit is None:
                    continue
                d = _parse_date(r.get("game_date"))
                if start_d is not None and d is not None and d.normalize() < start_d.normalize():
                    continue
                try:
                    line = float(r.get("line"))
                except (TypeError, ValueError):
                    continue
                direction = str(r.get("direction") or "").strip().upper()
                if direction not in ("OVER", "UNDER"):
                    continue
                prop_raw = str(r.get("prop_type") or "").strip()
                pnorm = bpc._normalize_prop_type(prop_raw, sp)
                tier = _parse_tier(r.get("tier") if pd.notna(r.get("tier")) else "Standard")
                rows.append(
                    {
                        "sport": sp,
                        "player_name": str(r.get("player_name") or "").strip(),
                        "prop_type": pnorm,
                        "direction": direction,
                        "tier": tier,
                        "line": line,
                        "hit": hit,
                        "date": d,
                        "edge": None,
                    }
                )

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out = out.drop_duplicates(
        subset=["player_name", "sport", "prop_type", "direction", "line", "date"],
        keep="last",
    )
    return out


def _expected_band(tier: str, direction: str) -> tuple[float, float, float] | None:
    t = tier.strip()
    d = direction.upper()
    if t == "Goblin" and d == "OVER":
        return 0.56, 0.65, 0.605
    if t == "Standard" and d == "OVER":
        return 0.50, 0.55, 0.525
    if t == "Standard" and d == "UNDER":
        return 0.48, 0.53, 0.505
    if t == "Demon" and d == "OVER":
        return 0.40, 0.48, 0.44
    return None


def _matrix_flag(hit_rate: float, lo: float, hi: float, n: int) -> str:
    if n < 10:
        return "INSUFFICIENT"
    if n < 30:
        return "LOW SAMPLE"
    if hit_rate > hi:
        return "ABOVE_RANGE ↑"
    if hit_rate < lo:
        return "BELOW_RANGE ⚠"
    return "ON_TARGET ✓"


def section_matrix(df: pd.DataFrame, sport: str | None, min_count: int, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2A — prop_type × direction × tier matrix ===\n\n")
    if df.empty:
        sink.write("(no graded rows in window)\n")
        return
    work = df if not sport else df[df["sport"].str.upper() == sport.upper()]
    sports = sorted(work["sport"].unique()) if not sport else [sport]
    for sp in sports:
        sub = work[work["sport"] == sp]
        if sub.empty:
            continue
        sink.write(f"--- {sp} ---\n")
        groups = sub.groupby(["prop_type", "direction", "tier"], dropna=False)
        rows_out: list[tuple] = []
        for key, g in groups:
            prop_t, direc, tier = key
            band = _expected_band(str(tier), str(direc))
            n = len(g)
            hits = int(g["hit"].sum())
            hr = hits / n if n else 0.0
            if band:
                lo, hi, mid = band
                dev = hr - mid
                flag = _matrix_flag(hr, lo, hi, n)
            else:
                lo = hi = mid = 0.0
                dev = 0.0
                flag = "N/A (tier×dir)"
            rows_out.append((dev, prop_t, direc, tier, n, hits, hr, lo, hi, mid, flag))
        rows_out.sort(key=lambda x: -x[0])
        sink.write(
            f"{'prop_type':<22} {'dir':<6} {'tier':<10} {'n':>5} {'hits':>5} {'hr':>7} "
            f"{'dev':>7} {'flag':<18}\n"
        )
        for _, prop_t, direc, tier, n, hits, hr, lo, hi, mid, flag in rows_out:
            sink.write(
                f"{str(prop_t)[:21]:<22} {str(direc)[:5]:<6} {str(tier)[:9]:<10} "
                f"{n:5d} {hits:5d} {hr:7.3f} {hr - mid:7.3f} {flag:<18}\n"
            )
        sink.write("\n")


def section_players(sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2B — Player consistency (DB) ===\n\n")
    if not DB_PATH.exists():
        sink.write(f"(no DB at {DB_PATH})\n")
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        q = "SELECT * FROM player_consistency"
        params: tuple = ()
        if sport:
            q += " WHERE sport = ?"
            params = (sport,)
        cur = conn.execute(q, params)
        cols = [d[0] for d in cur.description]
        recs = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
    if not recs:
        sink.write("(no player_consistency rows)\n")
        return

    base_spec: list[tuple[str, str]] = [
        ("player", "player_name"),
        ("prop", "prop_type"),
        ("dir", "direction"),
        ("bucket", "line_bucket"),
        ("hit_rate", "hit_rate"),
        ("count", "decided_count"),
        ("grade", "grade"),
        ("trending", "trending"),
    ]

    def _w(title: str, subset: list[dict], extra: list[str]) -> None:
        sink.write(f"{title}\n")
        headers = [h for h, _ in base_spec] + extra
        keys = [k for _, k in base_spec] + extra
        sink.write(" | ".join(headers) + "\n")
        for r in subset[:200]:
            line = " | ".join(str(r.get(c, "")) for c in keys)
            sink.write(line + "\n")
        if len(subset) > 200:
            sink.write(f"... ({len(subset) - 200} more rows)\n")
        sink.write("\n")

    sa = [r for r in recs if r.get("grade") in ("S", "A")]
    df_cells = [r for r in recs if r.get("grade") in ("D", "F")]
    down = [
        r
        for r in recs
        if r.get("grade") in ("S", "A", "B") and r.get("trending") == "DOWN"
    ]
    up = [
        r
        for r in recs
        if r.get("grade") in ("C", "D") and r.get("trending") == "UP"
    ]

    _w("GRADE S/A PLAYERS (reliable — use these):", sa, [])
    _w(
        "GRADE D/F PLAYERS (avoid/blacklist):",
        df_cells,
        ["games_since_F"],
    )
    _w(
        "TRENDING DOWN (S/A/B + DOWN):",
        down,
        ["last_5_hit_rate", "last_20_hit_rate"],
    )
    _w(
        "TRENDING UP (C/D + UP):",
        up,
        ["last_5_hit_rate", "last_20_hit_rate"],
    )


def _minutes_bucket(val: float, sport: str) -> str | None:
    if pd.isna(val):
        return None
    v = float(val)
    if sport in ("NBA", "CBB"):
        if v < 20:
            return "<20"
        if v < 28:
            return "20-28"
        if v < 34:
            return "28-34"
        return "34+"
    if sport == "NHL":
        if v < 14:
            return "<14"
        if v < 18:
            return "14-18"
        if v < 22:
            return "18-22"
        return "22+"
    if sport == "Soccer":
        if v < 60:
            return "<60"
        if v < 75:
            return "60-75"
        return "75-90"
    return None


def section_minutes(df: pd.DataFrame, sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2C — Minutes / TOI / playing time ===\n\n")
    if "minutes" not in df.columns:
        sink.write("(no minutes / avg_minutes column in graded data)\n")
        return
    work = df.dropna(subset=["minutes"])
    if work.empty:
        sink.write("(no numeric minutes)\n")
        return
    sports = [sport] if sport else sorted(work["sport"].unique())
    for sp in sports:
        sub = work[work["sport"] == sp]
        if sub.empty:
            continue
        sub = sub.copy()
        sub["min_bucket"] = sub["minutes"].apply(lambda x: _minutes_bucket(x, sp))
        sub = sub.dropna(subset=["min_bucket"])
        if sub.empty:
            continue
        sink.write(f"--- {sp} ---\n")
        g = sub.groupby(["min_bucket", "prop_type"])
        for (bucket, prop), gg in g:
            n = len(gg)
            if n < 5:
                continue
            hr = gg["hit"].mean()
            sink.write(f"  {bucket:8} | {str(prop)[:20]:20} | n={n:4} | hr={hr:.3f}\n")
        sink.write("\n")


def _pace_bucket(val: Any) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        s = str(val).strip().lower()
        if "slow" in s:
            return "slow"
        if "fast" in s:
            return "fast"
        if "med" in s or "mid" in s:
            return "medium"
        return None
    # numeric: assume higher = faster
    if x < 0.33:
        return "slow"
    if x > 0.66:
        return "fast"
    return "medium"


def section_pace(df: pd.DataFrame, sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2D — Game pace ===\n\n")
    if "pace_raw" not in df.columns:
        sink.write("(no team_pace / def_pace column)\n")
        return
    scoring = {"Points", "PRA", "Pts+Asts", "Pts+Rebs", "Goals", "Assists"}
    work = df[df["prop_type"].isin(scoring)].copy()
    if sport:
        work = work[work["sport"] == sport]
    work["pace_b"] = work["pace_raw"].apply(_pace_bucket)
    work = work.dropna(subset=["pace_b"])
    if work.empty:
        sink.write("(no pace buckets)\n")
        return
    for sp in sorted(work["sport"].unique()):
        sink.write(f"--- {sp} ---\n")
        sub = work[work["sport"] == sp]
        for pb in ["slow", "medium", "fast"]:
            ss = sub[sub["pace_b"] == pb]
            if ss.empty:
                continue
            over = ss[ss["direction"] == "OVER"]
            if len(over) >= 5:
                sink.write(
                    f"  pace={pb}: Points-like OVER n={len(over)} hr={over['hit'].mean():.3f}\n"
                )
        sink.write("\n")


def section_defense(df: pd.DataFrame, sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2E — Defensive matchup quality ===\n\n")
    if "def_tier_raw" not in df.columns:
        sink.write("(no def_tier / opp_def_tier column)\n")
        return
    work = df.copy()
    if sport:
        work = work[work["sport"] == sport]
    work["def_tier"] = work["def_tier_raw"].astype(str).str.strip().str.upper()
    if work.empty:
        return
    for sp in sorted(work["sport"].unique()):
        sink.write(f"--- {sp} ---\n")
        sub = work[work["sport"] == sp]
        for prop in sorted(sub["prop_type"].unique())[:25]:
            pp = sub[sub["prop_type"] == prop]
            sink.write(f"  {prop}:\n")
            for dt in sorted(pp["def_tier"].unique()):
                gg = pp[pp["def_tier"] == dt]
                if len(gg) < 3:
                    continue
                sink.write(f"    {dt}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
            for direc in ("OVER", "UNDER"):
                sink.write(f"    {direc} by tier:\n")
                for dt in sorted(pp["def_tier"].unique()):
                    gg = pp[(pp["def_tier"] == dt) & (pp["direction"] == direc)]
                    if len(gg) < 3:
                        continue
                    sink.write(f"      {dt}: hr={gg['hit'].mean():.3f} (n={len(gg)})\n")
        sink.write("\n")


def _edge_bucket(e: float | None) -> str | None:
    if e is None or (isinstance(e, float) and pd.isna(e)):
        return None
    x = float(e)
    if x < -0.10:
        return "< -0.10"
    if x < 0:
        return "-0.10:0"
    if x < 0.05:
        return "0:0.05"
    if x < 0.10:
        return "0.05:0.10"
    if x < 0.15:
        return "0.10:0.15"
    if x < 0.20:
        return "0.15:0.20"
    return "> 0.20"


def section_edge(df: pd.DataFrame, sport: str | None, min_count: int, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2F — Edge calibration ===\n\n")
    if "edge" not in df.columns or df["edge"].isna().all():
        sink.write("(no edge column)\n")
        return
    work = df.dropna(subset=["edge"]).copy()
    if sport:
        work = work[work["sport"] == sport]
    work["edge_b"] = work["edge"].apply(_edge_bucket)
    work = work.dropna(subset=["edge_b"])
    order = ["< -0.10", "-0.10:0", "0:0.05", "0.05:0.10", "0.10:0.15", "0.15:0.20", "> 0.20"]
    hrs: list[tuple[str, int, float]] = []
    for b in order:
        gg = work[work["edge_b"] == b]
        if gg.empty:
            continue
        hrs.append((b, len(gg), gg["hit"].mean()))
    for b, n, hr in hrs:
        sink.write(f"  {b:14} | n={n:5} | hr={hr:.3f}\n")
    if len(hrs) >= 2:
        mono = all(hrs[i][2] <= hrs[i + 1][2] + 1e-9 for i in range(len(hrs) - 1))
        sink.write(f"  Monotonic (non-decreasing HR): {'YES' if mono else 'NO — review calibration'}\n")
    sink.write("\n")


def section_intangibles(df: pd.DataFrame, sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2G — Sport-specific intangibles ===\n\n")
    if df.empty:
        return
    work = df if not sport else df[df["sport"] == sport]
    if "home_away" in work.columns:
        sink.write("Home vs away (where column present):\n")
        for sp in sorted(work["sport"].unique()):
            if sp not in ("NBA", "CBB"):
                continue
            sub = work[work["sport"] == sp]
            for ha in ["H", "HOME", "Home", "A", "AWAY", "Away"]:
                pass
            for prop in sorted(sub["prop_type"].unique())[:12]:
                pp = sub[sub["prop_type"] == prop]
                sink.write(f"  [{sp}] {prop}:\n")
                for label in sorted(pp["home_away"].astype(str).unique())[:6]:
                    gg = pp[pp["home_away"].astype(str) == label]
                    if len(gg) < 5:
                        continue
                    sink.write(f"    {label}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
        sink.write("\n")
    else:
        sink.write("(no home_away column)\n")

    if "b2b" in work.columns:
        sink.write("B2B vs rested (NBA/CBB):\n")
        for sp in ("NBA", "CBB"):
            sub = work[work["sport"] == sp]
            if sub.empty:
                continue
            for prop in sorted(sub["prop_type"].unique())[:8]:
                pp = sub[sub["prop_type"] == prop]
                sink.write(f"  [{sp}] {prop} by b2b flag:\n")
                for v in sorted(pp["b2b"].astype(str).unique())[:5]:
                    gg = pp[pp["b2b"].astype(str) == v]
                    if len(gg) < 3:
                        continue
                    sink.write(f"    {v}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
        sink.write("\n")
    else:
        sink.write("(no back_to_back column)\n")

    nhl = work[work["sport"] == "NHL"]
    if not nhl.empty and "goalie_confirmed" in nhl.columns:
        sink.write("NHL goalie_confirmed (Shots/Goals):\n")
        for prop in ("Shots", "Goals"):
            pp = nhl[nhl["prop_type"] == prop]
            if pp.empty:
                continue
            for gc in sorted(pp["goalie_confirmed"].astype(str).unique())[:6]:
                gg = pp[pp["goalie_confirmed"].astype(str) == gc]
                if len(gg) < 3:
                    continue
                sink.write(f"  {prop} / {gc}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
        sink.write("\n")
    if not nhl.empty and "pp_unit" in nhl.columns:
        sink.write("NHL pp_unit:\n")
        pp = nhl
        for pu in sorted(pp["pp_unit"].astype(str).unique())[:8]:
            gg = pp[pp["pp_unit"].astype(str) == pu]
            if len(gg) < 5:
                continue
            sink.write(f"  {pu}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
        sink.write("\n")

    soc = work[work["sport"] == "Soccer"]
    if not soc.empty and "competition" in soc.columns:
        sink.write("Soccer competition:\n")
        for prop in sorted(soc["prop_type"].unique())[:10]:
            pp = soc[soc["prop_type"] == prop]
            sink.write(f"  {prop}:\n")
            for comp in sorted(pp["competition"].astype(str).unique())[:6]:
                gg = pp[pp["competition"].astype(str) == comp]
                if len(gg) < 3:
                    continue
                sink.write(f"    {comp}: n={len(gg)} hr={gg['hit'].mean():.3f}\n")
        sink.write("\n")
    if not soc.empty and "minutes_played" in soc.columns:
        sink.write("Soccer minutes_played < 60 vs >= 60:\n")
        for prop in sorted(soc["prop_type"].unique())[:8]:
            pp = soc[soc["prop_type"] == prop]
            low = pp[pp["minutes_played"] < 60]
            hi = pp[pp["minutes_played"] >= 60]
            if len(low) >= 3 and len(hi) >= 3:
                sink.write(
                    f"  {prop}: <60 hr={low['hit'].mean():.3f} (n={len(low)}) | "
                    f">=60 hr={hi['hit'].mean():.3f} (n={len(hi)})\n"
                )
        sink.write("\n")


def section_game_script(df: pd.DataFrame, sport: str | None, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2I — Game Script Risk Validation ===\n\n")
    if df.empty:
        sink.write("(no graded rows in window)\n\n")
        return
    if "game_script_mult" not in df.columns or df["game_script_mult"].notna().sum() == 0:
        sink.write(
            "Run pipeline with game script enabled to populate this analysis "
            "(graded files need a game_script_mult column).\n\n"
        )
        return

    work = df if not sport else df[df["sport"].str.upper() == sport.upper()]
    sports = sorted(work["sport"].unique()) if not sport else [sport]
    for sp in sports:
        sub = work[work["sport"] == sp].copy()
        sub = sub.dropna(subset=["game_script_mult"])
        if sub.empty:
            sink.write(f"=== Game Script Risk Validation — {sp} ===\n(no game_script_mult values)\n\n")
            continue
        baseline = float(sub["hit"].mean()) if len(sub) else 0.0

        def _bucket(m: float) -> str:
            if m >= 1.03:
                return "Favorable"
            if m >= 0.97:
                return "Neutral"
            if m >= 0.90:
                return "Caution"
            return "High Risk"

        def _range_label(bucket: str) -> str:
            return {
                "Favorable": ">= 1.03",
                "Neutral": "0.97-1.02",
                "Caution": "0.90-0.96",
                "High Risk": "< 0.90",
            }[bucket]

        sub["gs_bucket"] = sub["game_script_mult"].astype(float).map(_bucket)
        sink.write(f"=== Game Script Risk Validation — {sp} ===\n")
        sink.write(f"Sport baseline hit rate (all rows with mult): {baseline:.1%}\n")
        sink.write(
            f"{'Risk Level':<14} | {'Mult Range':<11} | {'Count':>5} | "
            f"{'Hit Rate':>9} | {'Expected':<20}\n"
        )
        sink.write("-" * 72 + "\n")
        order = ["Favorable", "Neutral", "Caution", "High Risk"]
        fav_hr = None
        hi_hr = None
        for b in order:
            g = sub[sub["gs_bucket"] == b]
            n = len(g)
            if n == 0:
                sink.write(f"{b:<14} | {_range_label(b):<11} | {n:5d} | {'n/a':>9} | —\n")
                continue
            hr = float(g["hit"].mean())
            if b == "Favorable":
                fav_hr = hr
            if b == "High Risk":
                hi_hr = hr
            if b == "Favorable":
                exp = f"> {baseline:.0%} (if model helps)"
            elif b == "Neutral":
                exp = f"~{baseline:.0%}"
            elif b == "Caution":
                exp = f"< {baseline:.0%}"
            else:
                exp = f"< {baseline:.0%}"
            sink.write(
                f"{b:<14} | {_range_label(b):<11} | {n:5d} | {hr:8.1%} | {exp:<20}\n"
            )
        sink.write("\n")
        if fav_hr is not None and hi_hr is not None and fav_hr > hi_hr:
            sink.write(
                "Key check: high-risk bucket hit rate is below favorable bucket — "
                "consistent with game script penalty working.\n\n"
            )
        elif fav_hr is not None and hi_hr is not None:
            sink.write(
                "Key check: high-risk bucket is not clearly below favorable — "
                "multipliers may need recalibration.\n\n"
            )


def section_recalibrate(df: pd.DataFrame, sport: str | None, min_count: int, sink: TextIO) -> None:
    sink.write("\n=== SECTION 2H — Recalibration recommendations ===\n\n")
    if df.empty:
        sink.write("(no data)\n")
        return
    work = df if not sport else df[df["sport"] == sport]
    sink.write("_PROP_WEIGHTS updates (|dev| > 4% and n >= 30):\n")
    sink.write("sport | prop_type | dir | tier | current | recommended\n")
    # current unknown without parsing step7 — emit placeholder 1.00
    for (sp, prop, direc, tier), g in work.groupby(["sport", "prop_type", "direction", "tier"]):
        band = _expected_band(str(tier), str(direc))
        n = len(g)
        if n < 30 or not band:
            continue
        lo, hi, mid = band
        hr = g["hit"].mean()
        dev = hr - mid
        if abs(dev) <= 0.04:
            continue
        rec = round(1.0 + dev * 2.0, 3)
        sink.write(f"{sp} | {prop} | {direc} | {tier} | 1.000 | {rec}\n")
    sink.write("\n_PROP_HR_PRIOR updates:\n")
    sink.write("sport | prop_type | direction | current | actual | recommended\n")
    for (sp, prop, direc), g in work.groupby(["sport", "prop_type", "direction"]):
        n = len(g)
        if n < 30:
            continue
        hr = g["hit"].mean()
        sink.write(f"{sp} | {prop} | {direc} | 0.500 | {hr:.3f} | {hr:.3f}\n")
    sink.write("\nCELLS TO BLOCK (hit_rate < 40%, n >= 30):\n")
    for (sp, prop, direc, tier), g in work.groupby(["sport", "prop_type", "direction", "tier"]):
        n = len(g)
        if n < 30:
            continue
        hr = g["hit"].mean()
        if hr < 0.40:
            sink.write(f"  BLOCK {sp} | {prop} | {direc} | {tier} | hr={hr:.3f} n={n}\n")
    sink.write("\nSTRONG CELLS (hit_rate > 60%, n >= 30):\n")
    for (sp, prop, direc, tier), g in work.groupby(["sport", "prop_type", "direction", "tier"]):
        n = len(g)
        if n < 30:
            continue
        hr = g["hit"].mean()
        if hr > 0.60:
            sink.write(f"  STRONG {sp} | {prop} | {direc} | {tier} | hr={hr:.3f} n={n}\n")
    sink.write("\n")


def run_report(args: argparse.Namespace) -> str:
    since = pd.to_datetime(args.since, errors="coerce") if args.since else None
    if since is not None and pd.isna(since):
        since = None

    df = load_graded_dataframe(
        sport=args.sport,
        since=since,
        days=args.days,
        _min_count=args.min_count,
        include_synthetic=args.include_synthetic,
    )

    buf = io.StringIO()
    sink = buf

    section = (args.section or "all").lower()
    if section in ("all", "matrix"):
        section_matrix(df, args.sport, args.min_count, sink)
    if section in ("all", "players"):
        section_players(args.sport, sink)
    if section in ("all", "minutes"):
        section_minutes(df, args.sport, sink)
    if section in ("all", "pace"):
        section_pace(df, args.sport, sink)
    if section in ("all", "defense"):
        section_defense(df, args.sport, sink)
    if section in ("all", "edge"):
        section_edge(df, args.sport, args.min_count, sink)
    if section in ("all", "intangibles"):
        section_intangibles(df, args.sport, sink)
    if section in ("all", "recalibrate", "2h"):
        section_recalibrate(df, args.sport, args.min_count, sink)
    if section in ("all", "game_script", "2i", "game_script_risk"):
        section_game_script(df, args.sport, sink)

    text = buf.getvalue()
    out_path = Path(args.output) if args.output else REPO_ROOT / "outputs" / f"grader_analysis_{_today_str()}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return str(out_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--section", default="all")
    ap.add_argument("--min-count", type=int, dest="min_count", default=30)
    ap.add_argument("--output", default=None, help="Report path (default outputs/grader_analysis_<today>.txt)")
    ap.add_argument(
        "--include-synthetic",
        action="store_true",
        default=False,
        help="Include synthetic_graded.db rows (default: real graded workbooks only).",
    )
    args = ap.parse_args()

    if args.sport == "":
        args.sport = None

    path = run_report(args)
    report = Path(path).read_text(encoding="utf-8")
    sys.stdout.write(report)
    print(f"\n[Saved report to {path}]", file=sys.stderr)


if __name__ == "__main__":
    main()
