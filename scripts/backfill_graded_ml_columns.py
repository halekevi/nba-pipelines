#!/usr/bin/env python3
"""
Backfill ml_prob, ml_edge, edge_score, blended_score onto graded *Box Raw* workbooks.

Strategy (per file):
  1) Prefer merge from a dated step8/step6 clean slate in outputs/{{date}}/ when it has ML columns.
  2) Else run unified edge_model on Box Raw rows (after column aliasing) for maximum history coverage.

Default writes sibling: graded_nba_DATE_mlbackfill.xlsx (use --in-place to overwrite).

Usage:
  py -3.14 scripts/backfill_graded_ml_columns.py --repo-root .
  py -3.14 scripts/backfill_graded_ml_columns.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from edge_predict_utils import (  # noqa: E402
    graded_filename_sport_to_train_sport,
    predict_unified_edge_scores,
)
from grading.slate_grader import norm_player_key, norm_prop_key  # noqa: E402

GRADED_NAME_RE = re.compile(
    r"graded_(?P<sp>[a-z0-9]+)_(?P<dt>\d{4}-\d{2}-\d{2})\.xlsx$",
    re.IGNORECASE,
)

# outputs/{{d}}/filename
STEP8_TEMPLATES: dict[str, str] = {
    "nba": "step8_nba_direction_clean_{d}.xlsx",
    "nba1q": "step8_nba1q_direction_clean_{d}.xlsx",
    "nba1h": "step8_nba1h_direction_clean_{d}.xlsx",
    "wnba": "step8_wnba_direction_clean_{d}.xlsx",
    "nhl": "step8_nhl_direction_clean_{d}.xlsx",
    "soccer": "step8_soccer_direction_clean_{d}.xlsx",
    "mlb": "step8_mlb_direction_clean_{d}.xlsx",
}

ML_COLS = ["ml_prob", "ml_edge", "edge_score", "blended_score"]


def _repo_root(arg: Path | None) -> Path:
    return Path(arg).resolve() if arg else Path(__file__).resolve().parent.parent


def _dates_to_try(graded_path: Path, file_date: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(d: str) -> None:
        d = (d or "").strip()[:10]
        if len(d) == 10 and d not in seen:
            seen.add(d)
            out.append(d)

    try:
        parent = graded_path.parent.name
        if len(parent) == 10 and parent[4] == "-" and parent[7] == "-":
            add(parent)
    except Exception:
        pass
    add(file_date)
    return out


def _series_line_key(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").round(4).astype(str)


def build_join_key(
    df: pd.DataFrame,
    player_c: str,
    prop_c: str,
    line_c: str,
    dir_c: str,
) -> pd.Series:
    pl = df[player_c].astype(str).map(norm_player_key)
    pr = df[prop_c].astype(str).map(norm_prop_key)
    ln = _series_line_key(df[line_c])
    dr = df[dir_c].astype(str).str.strip().str.upper()
    return pl + "|" + pr + "|" + ln + "|" + dr


def _normalize_slate_for_join(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]] | None:
    """Return dataframe with canonical join columns renamed to _p,_prop,_line,_dir."""
    cols = {str(c).strip(): c for c in df.columns}
    lower = {k.lower(): k for k in cols}

    def pick(*names: str) -> str | None:
        for n in names:
            if n in cols:
                return cols[n]
            if n.lower() in lower:
                return lower[n.lower()]
        return None

    p = pick("player", "Player", "player_name")
    prop = pick("prop_type_norm", "Prop", "prop_norm", "stat_norm", "prop_type", "stat_type")
    line = pick("line", "Line", "line_score", "LINE")
    d = pick(
        "bet_direction",
        "Direction",
        "final_bet_direction",
        "direction",
        "side",
        "pick_side",
        "Bet Side",
    )
    if not all([p, prop, line, d]):
        return None
    out = df.copy()
    rename = {p: "_p", prop: "_prop", line: "_line", d: "_dir"}
    out = out.rename(columns=rename)
    return out, {"p": "_p", "prop": "_prop", "line": "_line", "dir": "_dir"}


def _extract_ml_from_slate(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce slate to join_key + ML columns (any present)."""
    norm = _normalize_slate_for_join(df)
    if norm is None:
        return pd.DataFrame()
    s2, _ = norm
    # ML Prob title case etc.
    for src, dst in (
        ("ML Prob", "ml_prob"),
        ("ml_prob", "ml_prob"),
        ("ML Edge", "ml_edge"),
        ("ml_edge", "ml_edge"),
        ("Edge Score", "edge_score"),
        ("edge_score", "edge_score"),
        ("Blended Score", "blended_score"),
        ("blended_score", "blended_score"),
    ):
        if src in s2.columns and dst not in s2.columns:
            s2[dst] = pd.to_numeric(s2[src], errors="coerce")
    jk = build_join_key(s2, "_p", "_prop", "_line", "_dir")
    out = pd.DataFrame({"_join_key": jk})
    for c in ML_COLS:
        if c in s2.columns:
            out[c] = pd.to_numeric(s2[c], errors="coerce")
    out = out.drop_duplicates(subset=["_join_key"], keep="last")
    return out


def resolve_merge_source(root: Path, sport_key: str, dates: list[str]) -> Path | None:
    sk = sport_key.lower()
    if sk in STEP8_TEMPLATES:
        fmt = STEP8_TEMPLATES[sk]
        for d in dates:
            p = root / "outputs" / d / fmt.format(d=d)
            if p.is_file():
                return p
        # legacy roots (non-dated)
        leg = {
            "nba": root / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
            "nba1q": root / "NBA" / "step8_nba1q_direction_clean.xlsx",
            "nba1h": root / "NBA" / "step8_nba1h_direction_clean.xlsx",
            "wnba": root / "WNBA" / "step8_wnba_direction_clean.xlsx",
            "nhl": root / "NHL" / "step8_nhl_direction_clean.xlsx",
            "soccer": root / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
            "mlb": root / "MLB" / "step8_mlb_direction_clean.xlsx",
        }
        lp = leg.get(sk)
        if lp and lp.is_file():
            return lp
    if sk in ("cbb", "wcbb"):
        cbb_paths: list[Path] = []
        for d in dates:
            cbb_paths.extend(
                [
                    root / "CBB" / "outputs" / d / "step6_ranked_cbb.xlsx",
                    root / "CBB" / "outputs" / d / f"step6_ranked_cbb_{d}.xlsx",
                ]
            )
        cbb_paths.extend(
            [
                root / "CBB" / "step6_ranked_cbb.xlsx",
                root / "CBB" / "step6_ranked_wcbb.xlsx",
            ]
        )
        for p in cbb_paths:
            if p.is_file():
                return p
    return None


def _pick_box_raw_sheet(path: Path, xl: pd.ExcelFile) -> str | None:
    """Prefer canonical Box Raw names; skip empty sheets; avoid lone placeholder tabs."""
    order = ("Box Raw", "Props", "ALL", "All", "All Props", "GRADED", "Sheet1", "Sheet")
    for name in order:
        if name not in xl.sheet_names:
            continue
        try:
            df = pd.read_excel(path, sheet_name=name, engine="openpyxl", nrows=5)
        except Exception:
            continue
        if len(df.columns) > 0 and len(df) > 0:
            return name
    for name in xl.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=name, engine="openpyxl", nrows=5)
        except Exception:
            continue
        if len(df.columns) > 0 and len(df) > 0:
            return name
    return None


def _read_first_slate_sheet(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path, engine="openpyxl")
    prefer = ("ALL", "All", "All Props", "all props", "Sheet", next(iter(xl.sheet_names), "Sheet1"))
    for name in prefer:
        if name in xl.sheet_names:
            return pd.read_excel(path, sheet_name=name, engine="openpyxl")
    return pd.read_excel(path, sheet_name=xl.sheet_names[0], engine="openpyxl")


def backfill_one_workbook(
    root: Path,
    graded_path: Path,
    *,
    dry_run: bool,
    in_place: bool,
    prefer_merge: bool,
) -> dict[str, int | str]:
    m = GRADED_NAME_RE.search(graded_path.name)
    if not m:
        return {"status": "skip_name", "file": graded_path.name}
    sport_key = m.group("sp").lower()
    file_date = m.group("dt")
    dates = _dates_to_try(graded_path, file_date)

    xl = pd.ExcelFile(graded_path, engine="openpyxl")
    br_name = _pick_box_raw_sheet(graded_path, xl)
    if br_name is None:
        return {"status": "no_box_raw", "file": str(graded_path)}
    sheets = {sn: pd.read_excel(graded_path, sheet_name=sn, engine="openpyxl") for sn in xl.sheet_names}
    raw = sheets[br_name].copy()

    if "prop_type_norm" not in raw.columns:
        for alt in ("prop_norm", "stat_norm", "Prop", "prop_type"):
            if alt in raw.columns:
                raw = raw.rename(columns={alt: "prop_type_norm"})
                sheets[br_name] = raw
                break
    if "player" not in raw.columns:
        for alt in ("Player", "player_name"):
            if alt in raw.columns:
                raw = raw.rename(columns={alt: "player"})
                sheets[br_name] = raw
                break
    if "bet_direction" not in raw.columns:
        for alt in ("Direction", "final_bet_direction", "direction", "side", "pick_side", "Bet Side"):
            if alt in raw.columns:
                raw = raw.rename(columns={alt: "bet_direction"})
                sheets[br_name] = raw
                break

    if "player" not in raw.columns or "prop_type_norm" not in raw.columns:
        return {"status": "skip_columns", "file": str(graded_path)}
    if "bet_direction" not in raw.columns:
        return {"status": "no_direction", "file": str(graded_path)}
    if "line" not in raw.columns and "Line" in raw.columns:
        raw = raw.rename(columns={"Line": "line"})
        sheets[br_name] = raw
    elif "line" in raw.columns and "Line" in raw.columns:
        ln = pd.to_numeric(raw["line"], errors="coerce")
        lL = pd.to_numeric(raw["Line"], errors="coerce")
        raw["line"] = ln.where(ln.notna(), lL)
        raw = raw.drop(columns=["Line"])
        sheets[br_name] = raw
    line_col = "line" if "line" in raw.columns else None
    if line_col is None:
        return {"status": "no_line", "file": str(graded_path)}

    raw["_join_key"] = build_join_key(raw, "player", "prop_type_norm", line_col, "bet_direction")
    n0 = len(raw)
    merged_rows = 0
    predict_rows = 0

    src = resolve_merge_source(root, sport_key, dates) if prefer_merge else None
    ml_from_slate: pd.DataFrame | None = None
    if src is not None:
        try:
            slate_df = _read_first_slate_sheet(src)
            ml_from_slate = _extract_ml_from_slate(slate_df)
        except Exception:
            ml_from_slate = None

    if ml_from_slate is not None and not ml_from_slate.empty:
        idxm = ml_from_slate.set_index("_join_key")
        for c in ML_COLS:
            if c not in idxm.columns:
                continue
            mapped = raw["_join_key"].map(idxm[c])
            if c not in raw.columns:
                raw[c] = mapped
            else:
                old = pd.to_numeric(raw[c], errors="coerce")
                raw[c] = old.where(old.notna(), mapped)
        if "ml_prob" in idxm.columns:
            merged_rows = int(pd.to_numeric(raw.get("ml_prob"), errors="coerce").notna().sum())

    need_predict = True
    if "ml_prob" in raw.columns and raw["ml_prob"].notna().sum() >= max(5, int(0.85 * n0)):
        need_predict = False
    if need_predict:
        train_sp = graded_filename_sport_to_train_sport(sport_key)
        pred = predict_unified_edge_scores(
            raw.drop(columns=["_join_key"], errors="ignore"),
            sport_for_model=train_sp,
        )
        if pred is not None:
            ml_p, es, bl = pred
            mask = raw["ml_prob"].isna() if "ml_prob" in raw.columns else pd.Series(True, index=raw.index)
            raw.loc[mask, "ml_prob"] = ml_p.loc[mask]
            raw.loc[mask, "edge_score"] = es.loc[mask]
            raw.loc[mask, "blended_score"] = bl.loc[mask]
            raw.loc[mask, "ml_edge"] = ml_p.loc[mask] - 0.5
            predict_rows = int(mask.sum())

    if "ml_prob" in raw.columns and "ml_edge" not in raw.columns:
        raw["ml_edge"] = pd.to_numeric(raw["ml_prob"], errors="coerce") - 0.5
    elif "ml_prob" in raw.columns:
        m1 = raw["ml_prob"].notna()
        raw.loc[m1, "ml_edge"] = pd.to_numeric(raw["ml_prob"], errors="coerce").loc[m1] - 0.5

    raw = raw.drop(columns=["_join_key"], errors="ignore")
    sheets[br_name] = raw

    out_path = graded_path
    if not in_place:
        out_path = graded_path.with_name(graded_path.stem + "_mlbackfill.xlsx")

    counts = {
        "status": "ok",
        "file": str(graded_path.name),
        "rows": n0,
        "merged_hint": merged_rows,
        "predict_fill": predict_rows,
        "out": str(out_path.name),
    }
    if dry_run:
        counts["status"] = "dry_run"
        return counts

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        for sn, frame in sheets.items():
            frame.to_excel(w, sheet_name=sn, index=False)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--glob-dir", type=Path, default=None, help="Root to rglob graded_*.xlsx (default: outputs/)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--in-place", action="store_true", help="Overwrite original graded file (default: *_mlbackfill.xlsx)")
    ap.add_argument("--no-merge", action="store_true", help="Only model predict; skip step8 merge")
    args = ap.parse_args()

    root = _repo_root(args.repo_root)
    base = Path(args.glob_dir).resolve() if args.glob_dir else root / "outputs"
    files = sorted(base.rglob("graded_*.xlsx"))
    if not files:
        print(f"No graded_*.xlsx under {base}")
        return
    print(f"Found {len(files)} graded workbooks under {base}")
    ok = 0
    for p in files:
        if "_mlbackfill" in p.name.lower():
            continue
        r = backfill_one_workbook(
            root,
            p,
            dry_run=args.dry_run,
            in_place=args.in_place,
            prefer_merge=not args.no_merge,
        )
        print(r)
        if r.get("status") in ("ok", "dry_run"):
            ok += 1
    print(f"\nProcessed (ok+dry): {ok}/{len(files)}")


if __name__ == "__main__":
    main()
