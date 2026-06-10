#!/usr/bin/env python3
"""Build sport-scoped ticket training CSVs and train combined + per-sport ticket ML models."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ticket_ml_sports import (  # noqa: E402
    TICKET_ML_SPORT_KEYS,
    dataset_path_for_sport,
    filter_training_rows,
    sport_display_name,
)
from train_ticket_model import train_ticket_model_from_df  # noqa: E402

BUILD_DATASET = SCRIPTS / "build_ticket_training_dataset.py"
DEFAULT_MASTER = ROOT / "data" / "ml" / "ticket_training_dataset.csv"
REGISTRY_PATH = ROOT / "data" / "ml" / "ticket_model_registry.json"


def _run_build_dataset(
    *,
    output: Path,
    start_date: str,
    end_date: str,
    include_undecided: bool,
) -> None:
    cmd = [
        sys.executable,
        str(BUILD_DATASET),
        "--output",
        str(output),
    ]
    if start_date:
        cmd.extend(["--start-date", start_date])
    if end_date:
        cmd.extend(["--end-date", end_date])
    if include_undecided:
        cmd.append("--include-undecided")
    print(f"[ticket-ml-all] Building master dataset -> {output}")
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Train ticket ML for combined pipeline and each sport family."
    )
    ap.add_argument("--input-csv", default=str(DEFAULT_MASTER), help="Master ticket training CSV (built if missing).")
    ap.add_argument("--rebuild-dataset", action="store_true", help="Re-run build_ticket_training_dataset.py first.")
    ap.add_argument("--start-date", default="", help="Optional lower bound when rebuilding dataset.")
    ap.add_argument("--end-date", default="", help="Optional upper bound when rebuilding dataset.")
    ap.add_argument(
        "--sports",
        default="all",
        help="Comma-separated sport keys to train, or 'all' for every key in registry.",
    )
    ap.add_argument("--target", default="label_cash", choices=["label_cash", "label_paid"])
    ap.add_argument("--dry-run", action="store_true", help="Summarize row counts only.")
    ap.add_argument("--write-sport-csvs", action="store_true", help="Write per-sport filtered CSV snapshots under data/ml/.")
    ap.add_argument("--skip-combined", action="store_true", help="Skip combined (global) model.")
    args = ap.parse_args()

    master = Path(args.input_csv)
    if args.rebuild_dataset or not master.is_file():
        _run_build_dataset(
            output=master,
            start_date=str(args.start_date or "").strip(),
            end_date=str(args.end_date or "").strip(),
            include_undecided=False,
        )

    if not master.is_file():
        raise FileNotFoundError(f"Master training CSV not found: {master}")

    df = pd.read_csv(master, low_memory=False)
    sport_keys = list(TICKET_ML_SPORT_KEYS) if str(args.sports).strip().lower() == "all" else [
        s.strip().lower() for s in str(args.sports).split(",") if s.strip()
    ]
    if args.skip_combined and "combined" in sport_keys:
        sport_keys = [k for k in sport_keys if k != "combined"]

    registry: dict = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "master_csv": str(master),
        "target": str(args.target),
        "sports": {},
    }

    print(f"[ticket-ml-all] Master rows={len(df)} | training keys={sport_keys}")
    for key in sport_keys:
        sub = filter_training_rows(df, key)
        decided = int(sub["label_cash"].isin([0, 1]).sum()) if "label_cash" in sub.columns else len(sub)
        print(f"  {key:8} ({sport_display_name(key)}): rows={len(sub)} decided={decided}")

        if args.write_sport_csvs and len(sub) > 0:
            out_csv = dataset_path_for_sport(key)
            out_csv.parent.mkdir(parents=True, exist_ok=True)
            sub.to_csv(out_csv, index=False, encoding="utf-8-sig")
            print(f"    wrote {out_csv}")

        if args.dry_run:
            registry["sports"][key] = {"trained": False, "reason": "dry_run", "n_rows": int(len(sub))}
            continue

        try:
            summary = train_ticket_model_from_df(
                sub,
                sport_key=key,
                target=str(args.target),
                bucketed=True,
                dry_run=False,
            )
            registry["sports"][key] = summary
        except Exception as exc:
            print(f"  [warn] {key} training failed: {type(exc).__name__}: {exc}")
            registry["sports"][key] = {"trained": False, "reason": str(exc), "n_rows": int(len(sub))}

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"\n[ticket-ml-all] Registry -> {REGISTRY_PATH}")

    trained = [k for k, v in registry["sports"].items() if v.get("trained")]
    skipped = [k for k, v in registry["sports"].items() if not v.get("trained")]
    print(f"trained={len(trained)}: {', '.join(trained) or '(none)'}")
    if skipped:
        print(f"skipped={len(skipped)}: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
