#!/usr/bin/env python3
"""
Validate unacceptable VOID rows in graded workbooks.

Accepted void classes (configurable defaults):
- NO_DATA, DNP — NHL/Soccer/Tennis style
- NO_ACTUAL, NO_LINE — ``slate_grader.py`` / NBA+MLB Box Raw (missing box score, missing line)

Pushes use ``result=PUSH`` (not ``result=VOID`` with ``void_reason=PUSH``).

Anything else under result=VOID is reported as potentially unacceptable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


DEFAULT_ACCEPTABLE_VOID_TOKENS = ["NO_DATA", "DNP", "NO_ACTUAL", "NO_LINE"]


def _resolve_result_col(df: pd.DataFrame) -> str | None:
    cols = {str(c).strip().lower(): str(c) for c in df.columns}
    return cols.get("result") or cols.get("leg_result")


def _resolve_reason_col(df: pd.DataFrame) -> str | None:
    cols = {str(c).strip().lower(): str(c) for c in df.columns}
    for name in ("void_reason_grade", "void_reason", "reason", "notes", "status_note"):
        if name in cols:
            return cols[name]
    return None


def _load_workbook_rows(path: Path) -> pd.DataFrame:
    """
    NHL/NBA-style graded exports use ``Box Raw``; tennis (and some sport scripts)
    write a ``graded`` sheet. Prefer any sheet that exposes a ``result`` column.
    """
    xls = pd.ExcelFile(path, engine="openpyxl")
    names = list(xls.sheet_names)
    for sh in ("Box Raw", "graded", "GRADED", "Graded"):
        if sh not in names:
            continue
        df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")
        if _resolve_result_col(df) is not None:
            return df
    for sh in names:
        df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")
        if _resolve_result_col(df) is not None:
            return df
    return pd.read_excel(path, sheet_name=names[0], engine="openpyxl")


def _is_acceptable(reason: str, accepted_tokens: list[str]) -> bool:
    up = str(reason or "").upper()
    return any(tok in up for tok in accepted_tokens)


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate unacceptable VOID rows in graded workbooks.")
    ap.add_argument("--date", required=True, help="Slate date (YYYY-MM-DD), used for report naming.")
    ap.add_argument("--out-dir", required=True, help="Directory for CSV/JSON report outputs.")
    ap.add_argument(
        "--graded",
        action="append",
        default=[],
        help="Path to graded workbook (repeatable).",
    )
    ap.add_argument(
        "--accepted-void-token",
        action="append",
        default=[],
        help="Accepted VOID reason token (repeatable). Defaults: NO_DATA, DNP, NO_ACTUAL, NO_LINE.",
    )
    ap.add_argument(
        "--fail-on-unacceptable",
        action="store_true",
        help="Exit non-zero when unacceptable VOID rows are found.",
    )
    args = ap.parse_args()

    accepted_tokens = [x.strip().upper() for x in (args.accepted_void_token or []) if str(x).strip()]
    if not accepted_tokens:
        accepted_tokens = list(DEFAULT_ACCEPTABLE_VOID_TOKENS)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    summary: list[dict[str, object]] = []

    for raw_path in args.graded:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.is_file():
            summary.append(
                {
                    "file": str(p),
                    "status": "missing",
                    "rows": 0,
                    "void_rows": 0,
                    "acceptable_void_rows": 0,
                    "unacceptable_void_rows": 0,
                }
            )
            continue
        try:
            df = _load_workbook_rows(p)
        except Exception as e:
            summary.append(
                {
                    "file": str(p),
                    "status": f"read_error:{type(e).__name__}",
                    "rows": 0,
                    "void_rows": 0,
                    "acceptable_void_rows": 0,
                    "unacceptable_void_rows": 0,
                }
            )
            continue

        result_col = _resolve_result_col(df)
        reason_col = _resolve_reason_col(df)
        if result_col is None:
            summary.append(
                {
                    "file": str(p),
                    "status": "no_result_col",
                    "rows": int(len(df)),
                    "void_rows": 0,
                    "acceptable_void_rows": 0,
                    "unacceptable_void_rows": 0,
                }
            )
            continue

        res = df[result_col].astype(str).str.strip().str.upper()
        void_mask = res.eq("VOID")
        dvoid = df.loc[void_mask].copy()
        if reason_col is None:
            dvoid["_reason"] = ""
        else:
            dvoid["_reason"] = dvoid[reason_col].fillna("").astype(str).str.strip()

        dvoid["_acceptable"] = dvoid["_reason"].map(lambda x: _is_acceptable(x, accepted_tokens))
        bad = dvoid.loc[~dvoid["_acceptable"]].copy()

        # Keep concise, useful columns if present.
        wanted = ["player", "team", "league", "sport", "prop_type", "line", "direction", "result"]
        cols = [c for c in wanted if c in bad.columns]
        if result_col not in cols:
            cols.append(result_col)
        bad = bad.assign(file=str(p), reason=bad["_reason"])
        cols = ["file"] + cols + ["reason"]
        rows.extend(bad[cols].to_dict(orient="records"))

        summary.append(
            {
                "file": str(p),
                "status": "ok",
                "rows": int(len(df)),
                "void_rows": int(len(dvoid)),
                "acceptable_void_rows": int(dvoid["_acceptable"].sum()),
                "unacceptable_void_rows": int((~dvoid["_acceptable"]).sum()),
            }
        )

    summary_df = pd.DataFrame(summary)
    detail_df = pd.DataFrame(rows)

    stem = f"void_validator_{args.date}"
    summary_csv = out_dir / f"{stem}_summary.csv"
    detail_csv = out_dir / f"{stem}_unacceptable_rows.csv"
    json_out = out_dir / f"{stem}.json"

    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    detail_df.to_csv(detail_csv, index=False, encoding="utf-8-sig")
    payload = {
        "date": args.date,
        "accepted_void_tokens": accepted_tokens,
        "summary_rows": summary,
        "unacceptable_count": int(len(detail_df)),
    }
    json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"[void-validator] summary -> {summary_csv}")
    print(f"[void-validator] unacceptable rows -> {detail_csv}")
    print(f"[void-validator] json -> {json_out}")
    print(f"[void-validator] accepted tokens: {accepted_tokens}")
    print(f"[void-validator] unacceptable_count={len(detail_df)}")

    if args.fail_on_unacceptable and len(detail_df) > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

