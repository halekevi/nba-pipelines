#!/usr/bin/env python3
"""
Recompute Shortlist A threshold checks against current graded exports.

Sources:
  - ui_runner/templates/graded_props_*.json  (MLB, Soccer, NBA, NHL props)
  - outputs/**/graded_nba1q_*.xlsx sheet "Box Raw" (NBA1Q not present in graded_props JSON)

Writes:
  data/reports/graded_stratification/shortlist_a_audit_latest.csv
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
TEMPLATES = _REPO / "ui_runner" / "templates"
OUTPUTS = _REPO / "outputs"
OUT_CSV_DEFAULT = (
    _REPO / "data" / "reports" / "graded_stratification" / "shortlist_a_audit_latest.csv"
)

_GRADED_PROPS_DATE = re.compile(r"graded_props_(\d{4}-\d{2}-\d{2})\.json$", re.I)
_NBA1Q_FILE_DATE = re.compile(r"graded_nba1q_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)

# Same eight rows as shortlist_a_focus.csv (thresholds from that file).
SHORTLIST: list[dict] = [
    {
        "label": "MLB Demon OVER Total Bases",
        "sport": "MLB",
        "pick": "demon",
        "direction": "OVER",
        "props": ("Total Bases",),
        "threshold": 0.1109,
    },
    {
        "label": "Soccer (missing pick_type) OVER Shots",
        "sport": "Soccer",
        "pick": "missing",
        "direction": "OVER",
        "props": ("Shots",),
        "threshold": 0.3768,
    },
    {
        "label": "MLB Demon OVER Runs",
        "sport": "MLB",
        "pick": "demon",
        "direction": "OVER",
        "props": ("Runs",),
        "threshold": 0.1295,
    },
    {
        "label": "NBA Goblin OVER Pts+Rebs+Asts",
        "sport": "NBA",
        "pick": "goblin",
        "direction": "OVER",
        "props": ("Pts+Rebs+Asts",),
        "threshold": 0.9094823368486776,
    },
    {
        "label": "NBA Goblin OVER Pts+Rebs",
        "sport": "NBA",
        "pick": "goblin",
        "direction": "OVER",
        "props": ("Pts+Rebs",),
        "threshold": 0.9101,
    },
    {
        "label": "NBA Goblin OVER Assists",
        "sport": "NBA",
        "pick": "goblin",
        "direction": "OVER",
        "props": ("Assists",),
        "threshold": 0.9096,
    },
    {
        "label": "NBA1Q Goblin OVER Points",
        "sport": "NBA1Q",
        "pick": "goblin",
        "direction": "OVER",
        "props": ("Points",),
        "threshold": 0.9127,
    },
    {
        "label": "NBA Goblin OVER 3-PT Made",
        "sport": "NBA",
        "pick": "goblin",
        "direction": "OVER",
        "props": ("3-PT Made",),
        "threshold": 0.9118974754760168,
    },
]


MIN_KEPT_RELIABLE = 400  # Below this, threshold lift is often noisy; interpret cautiously.


def _norm_pick(raw) -> str:
    t = str(raw or "").strip().lower()
    if t in ("", "nan", "none", "null", "—", "–", "-", "(missing)"):
        return ""
    if t in ("standard", "std"):
        return "standard"
    if t == "goblin":
        return "goblin"
    if t == "demon":
        return "demon"
    return t


def _load_graded_props_frames(templates_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(templates_dir.glob("graded_props_*.json")):
        m = _GRADED_PROPS_DATE.match(path.name)
        fdate = m.group(1) if m else ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in data.get("props") or []:
            rows.append(
                {
                    "_fdate": fdate,
                    "sport": str(p.get("sport") or "").strip(),
                    "pick_type": p.get("pick_type"),
                    "direction": str(p.get("direction") or p.get("over_under") or "").strip().upper(),
                    "prop": str(p.get("prop") or "").strip(),
                    "line": str(p.get("line") or "").strip(),
                    "player": str(p.get("player") or "").strip(),
                    "ml_prob": p.get("ml_prob"),
                    "result": str(p.get("result") or "").strip().upper(),
                    "void_reason": str(p.get("void_reason") or "").strip(),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    df["pick_norm"] = df["pick_type"].map(_norm_pick)
    return df


def _load_nba1q_frames(outputs_dir: Path) -> pd.DataFrame:
    paths = sorted(outputs_dir.rglob("graded_nba1q_*.xlsx"))
    parts = []
    for path in paths:
        m = _NBA1Q_FILE_DATE.search(path.name)
        fdate = m.group(1) if m else ""
        try:
            raw = pd.read_excel(path, sheet_name="Box Raw")
        except Exception:
            continue
        if raw.empty:
            continue
        sub = pd.DataFrame(
            {
                "_fdate": fdate,
                "sport": "NBA1Q",
                "player": raw.get("player"),
                "pick_type": raw.get("pick_type"),
                "direction": raw.get("bet_direction", raw.get("direction")),
                "prop": raw.get("prop_type_norm", raw.get("prop")),
                "line": raw.get("line"),
                "ml_prob": raw.get("ml_prob"),
                "result": raw.get("result"),
            }
        )
        sub["player"] = sub["player"].astype(str).str.strip()
        sub["direction"] = sub["direction"].astype(str).str.strip().str.upper()
        sub["prop"] = sub["prop"].astype(str).str.strip()
        sub["line"] = sub["line"].apply(lambda x: str(x).strip() if pd.notna(x) else "")
        sub["result"] = sub["result"].astype(str).str.strip().str.upper()
        sub["ml_prob"] = pd.to_numeric(sub["ml_prob"], errors="coerce")
        sub["pick_norm"] = sub["pick_type"].map(_norm_pick)
        parts.append(sub)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _pick_mask(df: pd.DataFrame, pick_key: str) -> pd.Series:
    if pick_key == "missing":
        return df["pick_norm"].eq("")
    return df["pick_norm"].eq(pick_key)


def _slice_metrics(sub: pd.DataFrame, threshold: float) -> dict:
    decided = sub[sub["result"].isin(["HIT", "MISS"])].copy()
    base_n = len(decided)
    base_hr = float((decided["result"] == "HIT").mean()) if base_n else float("nan")

    thr_ok = decided["ml_prob"].notna() & (decided["ml_prob"] >= float(threshold))
    kept = decided.loc[thr_ok]
    kept_n = len(kept)
    kept_hr = float((kept["result"] == "HIT").mean()) if kept_n else float("nan")
    lift = (kept_hr - base_hr) * 100 if base_n and kept_n else float("nan")
    kept_pct = (kept_n / base_n * 100) if base_n else float("nan")

    void_n = int(sub["result"].eq("VOID").sum())
    pending = int((~sub["result"].isin(["HIT", "MISS", "VOID", "PUSH"])).sum())

    notes = []
    if kept_n and kept_n < MIN_KEPT_RELIABLE:
        notes.append(f"kept_n<{MIN_KEPT_RELIABLE}_noisy_lift")
    if void_n > base_n * 0.25 and base_n > 50:
        notes.append("high_void_share")

    return {
        "base_n": base_n,
        "base_hit_rate": base_hr,
        "kept_n": kept_n,
        "kept_hit_rate": kept_hr,
        "lift_pp": lift,
        "kept_pct_of_decided": kept_pct,
        "void_n": void_n,
        "non_standard_result_n": pending,
        "audit_notes": ";".join(notes),
    }


def _dedupe_nba1q_overlap(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer NBA1Q JSON/xlsx row over a duplicate NBA row for same slate date + prop key."""
    if df.empty or "_fdate" not in df.columns:
        return df
    if "player" not in df.columns:
        return df
    tmp = df.copy()
    tmp["_line_k"] = tmp.get("line", pd.Series([""] * len(tmp))).astype(str).str.strip()
    sport_rank = tmp["sport"].astype(str).str.upper().map(
        lambda s: 0 if s == "NBA1Q" else 1 if s == "NBA" else 2
    )
    tmp = tmp.assign(_sr=sport_rank).sort_values(
        ["_fdate", "player", "prop", "_line_k", "direction", "_sr"],
        ascending=[True, True, True, True, True, True],
    )
    dedup_cols = ["_fdate", "player", "prop", "_line_k", "direction"]
    if all(c in tmp.columns for c in dedup_cols):
        tmp = tmp.drop_duplicates(subset=dedup_cols, keep="first")
    return tmp.drop(columns=["_sr", "_line_k"], errors="ignore")


def run(templates_dir: Path, outputs_dir: Path, out_csv: Path) -> pd.DataFrame:
    gp = _load_graded_props_frames(templates_dir)
    n1 = _load_nba1q_frames(outputs_dir)
    combined = pd.concat([gp, n1], ignore_index=True) if len(n1) else gp
    combined = _dedupe_nba1q_overlap(combined)

    out_rows = []
    for row in SHORTLIST:
        sport = row["sport"]
        sub = combined[combined["sport"].astype(str).str.upper() == sport.upper()].copy()
        sub = sub[sub["direction"].eq(row["direction"])]
        sub = sub[_pick_mask(sub, row["pick"])]
        props = row["props"]
        sub = sub[sub["prop"].isin(props)]
        m = _slice_metrics(sub, row["threshold"])
        out_rows.append(
            {
                "label": row["label"],
                "sport": sport,
                "pick_rule": row["pick"],
                "direction": row["direction"],
                "props": "|".join(props),
                "threshold": row["threshold"],
                **m,
                "row_n_slate": int(len(sub)),
            }
        )

    rep = pd.DataFrame(out_rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit Shortlist A vs graded_props + NBA1Q graded xlsx")
    ap.add_argument("--templates", type=Path, default=TEMPLATES)
    ap.add_argument("--outputs", type=Path, default=OUTPUTS)
    ap.add_argument("--out", type=Path, default=OUT_CSV_DEFAULT)
    args = ap.parse_args()

    if not args.templates.is_dir():
        print(f"ERROR: templates dir not found: {args.templates}", file=sys.stderr)
        sys.exit(1)

    rep = run(args.templates, args.outputs, args.out)
    print(f"Wrote {args.out}  ({len(rep)} rows)")
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(rep.to_string(index=False))


if __name__ == "__main__":
    main()
