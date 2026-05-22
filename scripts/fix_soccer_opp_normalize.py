#!/usr/bin/env python3
"""Re-enrich legacy parent step8_soccer xlsx with normalized Opp → defense DB matching."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from difflib import get_close_matches
from pathlib import Path

import pandas as pd

_STRIP_TOKENS = frozenset(
    {
        "fc",
        "sc",
        "afc",
        "cf",
        "sl",
        "sd",
        "ca",
        "cd",
        "ac",
        "as",
        "rc",
        "rcd",
        "ud",
        "ce",
        "cp",
        "fk",
        "bk",
        "rb",
    }
)

_ALIASES: dict[str, str] = {
    "man city": "manchester city",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "psg": "paris saint-germain",
    "paris sg": "paris saint-germain",
    "paris saint germain": "paris saint-germain",
    "atletico": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "fc barcelona": "barcelona",
    "barca": "barcelona",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ac milan": "milan",
    "juve": "juventus",
    "dortmund": "borussia dortmund",
    "bvb": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "bayer 04": "bayer leverkusen",
    "lyon": "olympique lyonnais",
    "marseille": "olympique de marseille",
    "monaco": "as monaco",
    "nice": "ogc nice",
    "roma": "as roma",
    "lazio": "ss lazio",
    "napoli": "ssc napoli",
    "celta": "celta vigo",
    "celta de vigo": "celta vigo",
    "betis": "real betis",
    "sociedad": "real sociedad",
    # Unmatched dry-run (2026-05) — only teams present in soccer_defense_summary
    "boca": "boca juniors",
    "bragantino": "red bull bragantino",
    "cordoba sde": "central cordoba",
    "central cordoba sde": "central cordoba",
    "santa fe": "union",
    "union santa fe": "union",
    "chivas": "guadalajara",
    "cd guadalajara": "guadalajara",
    "earthquakes": "san jose",
    "san jose earthquakes": "san jose",
    "santos laguna": "santos",
    "pride": "orlando pride",
    "reign": "seattle reign",
    "seattle reign fc": "seattle reign",
    "stoke": "stoke city",
    "racing club de lens": "lens",
    "rc lens": "lens",
}


def normalize_opp(raw: str) -> str:
    """Normalize opponent team text for defense DB lookup."""
    s = unicodedata.normalize("NFKD", str(raw or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    s = re.sub(r"[-–—/]+", " ", s)
    s = re.sub(r"[^\w\s']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if s in _ALIASES:
        return _ALIASES[s]

    parts = s.split()
    changed = True
    while parts and changed:
        changed = False
        if parts and parts[0] in _STRIP_TOKENS:
            parts = parts[1:]
            changed = True
        if parts and parts[-1] in _STRIP_TOKENS:
            parts = parts[:-1]
            changed = True
    s = " ".join(parts).strip()
    if s in _ALIASES:
        return _ALIASES[s]

    parts = s.split()
    while parts:
        trial = " ".join(parts).strip()
        if trial in _ALIASES:
            return _ALIASES[trial]
        if not parts:
            break
        parts = parts[:-1]
    return s


def _def_tier_fill_frac(df: pd.DataFrame) -> float:
    for col in ("def_tier", "Def Tier", "DEF_TIER"):
        if col not in df.columns:
            continue
        s = df[col]
        ok = s.notna() & (s.astype(str).str.strip() != "") & (s.astype(str).str.lower() != "nan")
        return float(ok.mean()) if len(df) else 0.0
    return 0.0


def _row_missing_def_tier(row: pd.Series) -> bool:
    for col in ("def_tier", "Def Tier", "DEF_TIER"):
        if col not in row.index:
            continue
        v = row[col]
        if pd.isna(v):
            return True
        if str(v).strip().lower() in ("", "nan"):
            return True
        return False
    return True


def _load_defense_lookup(repo: Path) -> tuple[dict[str, dict], list[str]]:
    rows: list[dict] = []

    db_path = repo / "data" / "soccer_defense_cache.db"
    if db_path.is_file():
        con = sqlite3.connect(db_path)
        try:
            q = "SELECT * FROM defense_ratings"
            df = pd.read_sql_query(q, con)
            rows.extend(df.to_dict("records"))
        except Exception:
            pass
        finally:
            con.close()

    json_path = repo / "data" / "soccer_defense_cache.json"
    if json_path.is_file():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows.extend(payload)
        elif isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list):
                    rows.extend(v)

    if not rows:
        if str(repo / "scripts") not in sys.path:
            sys.path.insert(0, str(repo / "scripts"))
        soc_scripts = repo / "Sports" / "Soccer" / "scripts"
        if str(soc_scripts) not in sys.path:
            sys.path.insert(0, str(soc_scripts))
        try:
            from defense_db import load_defense_from_db  # type: ignore

            d = load_defense_from_db("soccer")
            if isinstance(d, pd.DataFrame) and not d.empty:
                rows.extend(d.to_dict("records"))
        except Exception:
            pass

    if not rows:
        csv_path = repo / "Sports" / "Soccer" / "cache" / "soccer_defense_summary.csv"
        if csv_path.is_file():
            d = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
            rows.extend(d.to_dict("records"))

    if not rows:
        raise SystemExit("No soccer defense data found (db/json/csv)")

    lookup: dict[str, dict] = {}
    for row in rows:
        name = row.get("pp_name") or row.get("team_name") or row.get("team") or row.get("TEAM_NAME")
        norm = normalize_opp(name)
        if not norm or norm in lookup:
            continue
        tier = row.get("DEF_TIER") or row.get("def_tier") or ""
        pace = None
        for pc in ("opp_gf_per_game", "OPP_PPG", "opp_gaa", "goals_conceded_pg", "opp_pace_zscore"):
            if pc in row and row[pc] is not None and str(row[pc]).strip().lower() not in ("", "nan"):
                pace = row[pc]
                break
        lookup[norm] = {"def_tier": tier, "DEF_TIER": tier, "opp_pace": pace}
    return lookup, sorted(lookup.keys())


def _resolve(raw_opp: str, lookup: dict[str, dict], db_keys: list[str]) -> dict | None:
    norm = normalize_opp(raw_opp)
    if norm in lookup:
        return lookup[norm]
    hits = get_close_matches(norm, db_keys, n=1, cutoff=0.72)
    if hits:
        return lookup[hits[0]]
    raw_s = str(raw_opp or "").strip()
    if raw_s and raw_s.lower() not in ("nan", "unknown_opp", "none"):
        print(f"  [Soccer enrich] unmatched: {raw_opp!r}")
    return None


def _ensure_tier_columns_object(df: pd.DataFrame) -> None:
    for col in ("def_tier", "Def Tier", "DEF_TIER"):
        if col in df.columns:
            df[col] = df[col].astype(object)


def _enrich_xlsx(path: Path, lookup: dict[str, dict], db_keys: list[str], dry_run: bool) -> tuple[float, float]:
    df = pd.read_excel(path, engine="openpyxl")
    _ensure_tier_columns_object(df)
    before = _def_tier_fill_frac(df)

    opp_col = None
    for c in ("Opp", "opp_team", "OPP", "Opponent"):
        if c in df.columns:
            opp_col = c
            break
    if not opp_col:
        print(f"  [skip] no Opp column in {path.name}")
        return before, before

    if "def_tier" not in df.columns and "Def Tier" not in df.columns:
        df["def_tier"] = pd.NA
    if "Def Tier" not in df.columns:
        df["Def Tier"] = pd.NA
    _ensure_tier_columns_object(df)

    for idx, row in df.iterrows():
        if not _row_missing_def_tier(row):
            continue
        raw = row.get(opp_col, "")
        rec = _resolve(str(raw), lookup, db_keys)
        if rec is None:
            continue
        tier = rec.get("def_tier") or rec.get("DEF_TIER") or ""
        if "def_tier" in df.columns:
            df.at[idx, "def_tier"] = tier
        if "Def Tier" in df.columns:
            df.at[idx, "Def Tier"] = tier
        if "DEF_TIER" in df.columns:
            df.at[idx, "DEF_TIER"] = tier
        pace = rec.get("opp_pace")
        if pace is not None:
            if "opp_pace_zscore" in df.columns:
                if _row_missing_pace_z(df.loc[idx]):
                    df.at[idx, "opp_pace_zscore"] = pace
            if "Opp Pace" in df.columns:
                df.at[idx, "Opp Pace"] = pace

    after = _def_tier_fill_frac(df)
    if not dry_run and after > before:
        df.to_excel(path, engine="openpyxl", index=False)
    return before, after


def _row_missing_pace_z(row: pd.Series) -> bool:
    if "opp_pace_zscore" not in row.index:
        return False
    v = row["opp_pace_zscore"]
    return pd.isna(v) or str(v).strip().lower() in ("", "nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--date", default="", help="YYYY-MM-DD or all dates")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fill-threshold", type=float, default=0.50)
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    lookup, db_keys = _load_defense_lookup(repo)

    outputs = repo / "outputs"
    if not outputs.is_dir():
        raise SystemExit(f"Missing {outputs}")

    dates: list[str] = []
    if args.date:
        dates = [args.date[:10]]
    else:
        for p in sorted(outputs.iterdir()):
            if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name):
                dates.append(p.name)

    improved = 0
    processed = 0
    fills_before: list[float] = []
    fills_after: list[float] = []

    for d in dates:
        xlsx = outputs / d / f"step8_soccer_direction_clean_{d}.xlsx"
        if not xlsx.is_file():
            continue
        df_probe = pd.read_excel(xlsx, engine="openpyxl", nrows=5)
        fill_probe = _def_tier_fill_frac(pd.read_excel(xlsx, engine="openpyxl"))
        if fill_probe >= args.fill_threshold:
            continue

        processed += 1
        before, after = _enrich_xlsx(xlsx, lookup, db_keys, args.dry_run)
        fills_before.append(before)
        fills_after.append(after)
        mark = "✓" if after > before + 0.001 else "—"
        if after > before + 0.001:
            improved += 1
        print(f"[{d}] def_tier: {before * 100:.1f}% → {after * 100:.1f}% {mark}")

    avg_b = 100 * (sum(fills_before) / len(fills_before)) if fills_before else 0.0
    avg_a = 100 * (sum(fills_after) / len(fills_after)) if fills_after else 0.0
    print(
        f"\nSummary: {improved}/{processed} dates improved, "
        f"avg fill {avg_b:.1f}% → {avg_a:.1f}%"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
