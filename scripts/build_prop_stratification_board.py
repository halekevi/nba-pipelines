#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _norm(x: Any) -> str:
    return str(x or "").strip()


def _up(x: Any) -> str:
    return _norm(x).upper()


def _line_bucket(v: Any) -> str:
    try:
        x = abs(float(v))
    except Exception:
        return "(missing)"
    if x < 1.5:
        return "micro"
    if x < 5:
        return "low"
    if x < 15:
        return "mid"
    if x < 30:
        return "high"
    return "xl"


def _norm_prop(v: Any) -> str:
    s = _norm(v).lower().replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _load_reliability(repo_root: Path) -> dict[tuple[str, str, str, str], dict]:
    p = repo_root / "data" / "reports" / "prop_reliability_latest.json"
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[tuple[str, str, str, str], dict] = {}
    for r in payload.get("rows", []):
        if not isinstance(r, dict):
            continue
        k = (
            _up(r.get("sport")),
            _norm_prop(r.get("prop_type")),
            _up(r.get("direction")),
            _norm(r.get("line_bucket")).lower(),
        )
        out[k] = r
    return out


def _iter_props(tpl: Path) -> list[dict]:
    pat = re.compile(r"^graded_props_\d{4}-\d{2}-\d{2}\.json$")
    rows = []
    for p in sorted(tpl.glob("graded_props_*.json")):
        if not pat.match(p.name):
            continue
        date_str = p.stem.replace("graded_props_", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            dt = None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in payload.get("props", []):
            if isinstance(r, dict) and _up(r.get("result")) in {"HIT", "MISS"}:
                rr = dict(r)
                rr["_date"] = dt
                rows.append(rr)
    return rows


def build_board(repo_root: Path, min_n: int, top_n: int) -> dict[str, Any]:
    reliability = _load_reliability(repo_root)
    rows = _iter_props(repo_root / "ui_runner" / "templates")

    agg: dict[tuple[str, ...], list[int]] = defaultdict(list)
    agg_recent: dict[tuple[str, ...], list[tuple[Any, int]]] = defaultdict(list)
    for r in rows:
        sport = _up(r.get("sport")) or "UNKNOWN"
        prop = _norm_prop(r.get("prop_type") or r.get("prop")) or "unknown"
        pick = _up(r.get("pick_type")) or "UNKNOWN"
        tier = _up(r.get("tier")) or "UNKNOWN"
        direction = _up(r.get("direction")) or "UNKNOWN"
        lb = _line_bucket(r.get("line"))
        rel = reliability.get((sport, prop, direction, lb), {})
        rel_status = _up(rel.get("status")) or "UNKNOWN"
        if rel_status not in {"RELIABLE", "WATCHLIST"}:
            continue
        def_tier = _norm(r.get("def_tier")) or "(unknown)"
        h2h = _norm(r.get("h2h_bucket")) or "(unknown)"
        minutes = _norm(r.get("minutes_tier")) or "(unknown)"
        role = _norm(r.get("role_tier")) or "(unknown)"
        ou = _norm(r.get("game_total_bucket")) or "(unknown)"
        k = (sport, prop, direction, pick, tier, lb, def_tier, h2h, minutes, role, ou, rel_status)
        hit = 1 if _up(r.get("result")) == "HIT" else 0
        agg[k].append(hit)
        agg_recent[k].append((r.get("_date"), hit))

    top = []
    for k, hits in agg.items():
        n = len(hits)
        if n < min_n:
            continue
        h = int(sum(hits))
        hr = h / n
        recent = sorted(agg_recent.get(k, []), key=lambda x: (x[0] is not None, x[0]))
        last5_hits = [int(x[1]) for x in recent[-5:]]
        last5_n = len(last5_hits)
        last5_hr = (sum(last5_hits) / last5_n) if last5_n else None
        top.append(
            {
                "sport": k[0],
                "prop_type": k[1],
                "direction": k[2],
                "pick_type": k[3],
                "tier": k[4],
                "line_bucket": k[5],
                "def_tier": k[6],
                "h2h_bucket": k[7],
                "minutes_tier": k[8],
                "role_tier": k[9],
                "game_total_bucket": k[10],
                "reliability_status": k[11],
                "n": n,
                "hits": h,
                "hit_rate": round(hr, 4),
                "last5_n": last5_n,
                "last5_hit_rate": round(last5_hr, 4) if last5_hr is not None else None,
            }
        )
    top.sort(key=lambda x: (-x["hit_rate"], -x["n"]))
    return {
        "summary": {"rows_considered": len(rows), "rows_ranked": len(top), "min_n": min_n},
        "top_trusted_segments": top[:top_n],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build trusted prop stratification board from graded props history.")
    ap.add_argument("--out-dir", default="", help="Output dir (default: data/reports/)")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--top-n", type=int, default=200)
    args = ap.parse_args()
    root = _repo_root()
    out_dir = Path(args.out_dir) if args.out_dir else (root / "data" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    board = build_board(root, int(args.min_n), int(args.top_n))
    js = out_dir / "prop_stratification_board_latest.json"
    txt = out_dir / "prop_stratification_board_latest.txt"
    js.write_text(json.dumps(board, indent=2, ensure_ascii=True), encoding="utf-8")
    lines = [
        "Prop Stratification Board",
        f"rows_considered={board['summary']['rows_considered']} rows_ranked={board['summary']['rows_ranked']} min_n={board['summary']['min_n']}",
        "",
        "Top trusted segments:",
    ]
    for x in board["top_trusted_segments"][:50]:
        lines.append(
            f"- {x['sport']} | {x['prop_type']} {x['direction']} | {x['pick_type']} | tier={x['tier']} | line={x['line_bucket']} | def={x['def_tier']} | h2h={x['h2h_bucket']} | min={x['minutes_tier']} | role={x['role_tier']} | ou={x['game_total_bucket']} | rel={x['reliability_status']} | hit_rate={x['hit_rate']} n={x['n']} | l5={x['last5_hit_rate']} ({x['last5_n']})"
        )
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {js}")
    print(f"Wrote: {txt}")


if __name__ == "__main__":
    main()

