#!/usr/bin/env python3
"""NHL join rate breakdown by file_date for retrain gap analysis."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "retrain_dataset.csv", low_memory=False)
    nhl = df[df["sport"] == "NHL"].copy()
    nhl = nhl[nhl["result"].isin(["HIT", "MISS", "PUSH"])]

    nhl["joined"] = nhl["rank_score"].notna()
    nhl["joined_def"] = (
        nhl["def_tier"].notna()
        & (nhl["def_tier"].astype(str).str.strip() != "")
        & (nhl["def_tier"].astype(str).str.lower() != "nan")
    )

    summary = (
        nhl.groupby("file_date")
        .agg(total=("joined", "count"), joined=("joined", "sum"), joined_def=("joined_def", "sum"))
        .assign(
            join_pct=lambda x: (x.joined / x.total * 100).round(1),
            def_pct=lambda x: (x.joined_def / x.total * 100).round(1),
        )
        .sort_values("file_date")
    )

    print("=== NHL join by file_date (joined = rank_score present) ===")
    print(summary.to_string())
    print()

    low = summary[summary.join_pct < 50]
    print(f"Dates with <50% join: {len(low)}")
    print(f"Rows in <50% dates:   {int(low['total'].sum()):,}")
    print(f"Pct of all NHL decided: {100 * low['total'].sum() / len(nhl):.1f}%")
    print()

    print("=== Worst 15 dates ===")
    print(summary.nsmallest(15, "join_pct").to_string())
    print()

    unjoined = int((~nhl["joined"]).sum())
    by_date_unj = summary.assign(unjoined=lambda x: x.total - x.joined).sort_values(
        "unjoined", ascending=False
    )
    top5 = int(by_date_unj.head(5)["unjoined"].sum())
    top10 = int(by_date_unj.head(10)["unjoined"].sum())
    print(f"Total unjoined NHL rows: {unjoined:,}")
    print(f"Top 5 dates account for:  {top5:,} ({100 * top5 / unjoined:.1f}%)")
    print(f"Top 10 dates account for: {top10:,} ({100 * top10 / unjoined:.1f}%)")
    print()

    out = ROOT / "outputs"

    def has_step2(d: str) -> bool:
        return (out / d / "nhl" / "step2_nhl_picktypes.csv").is_file()

    def has_step8(d: str) -> bool:
        n = out / d / "nhl"
        return (n / "step8_nhl_direction_clean.csv").is_file() or (
            n / "step8_nhl_direction_clean.xlsx"
        ).is_file()

    def has_graded(d: str) -> bool:
        return (out / d / f"graded_nhl_{d}.xlsx").is_file()

    low2 = summary[summary.join_pct < 50].copy()
    low2["has_step2"] = [has_step2(str(d)[:10]) for d in low2.index]
    low2["has_step8"] = [has_step8(str(d)[:10]) for d in low2.index]
    low2["has_graded"] = [has_graded(str(d)[:10]) for d in low2.index]
    print("=== <50% join dates: on-disk artifacts ===")
    print(low2[["total", "joined", "join_pct", "has_step2", "has_step8", "has_graded"]].to_string())


if __name__ == "__main__":
    main()
