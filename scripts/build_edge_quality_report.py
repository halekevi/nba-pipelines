#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_HIST_MIN_SEGMENT_N = 30
_HIST_MIN_PLAYER_N = 12
_HIST_TOP_N = 20
_HIST_MIN_SEGMENT_N_EXT = 5


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _norm(x: Any) -> str:
    return str(x or "").strip()


def _up(x: Any) -> str:
    return _norm(x).upper()


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _top_counter(counter: Counter, top_n: int = 12) -> list[dict[str, Any]]:
    return [{"key": k, "count": int(v)} for k, v in counter.most_common(top_n)]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _line_bucket(val: Any) -> str:
    try:
        x = float(val)
    except Exception:
        return "(missing)"
    ax = abs(x)
    if ax < 1.5:
        return "micro"
    if ax < 5:
        return "low"
    if ax < 15:
        return "mid"
    if ax < 30:
        return "high"
    return "xl"


def _first_present(r: dict[str, Any], keys: list[str], default: str = "(unknown)") -> str:
    for k in keys:
        if k in r:
            v = _norm(r.get(k))
            if v and _up(v) not in {"NAN", "NONE", "NULL", "—", "-"}:
                return v
    return default


def _build_historical_stratification(repo_root: Path) -> dict[str, Any]:
    tpl = repo_root / "ui_runner" / "templates"
    if not tpl.exists():
        return {
            "history_dates": 0,
            "history_props_total": 0,
            "eligible_props_total": 0,
            "top_segments": [],
            "top_consistent_players": [],
        }

    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for p in sorted(tpl.glob("graded_props_*.json")):
        try:
            payload = _read_json(p)
        except Exception:
            continue
        date_str = _norm((payload or {}).get("date")) if isinstance(payload, dict) else ""
        if date_str:
            seen_dates.add(date_str)
        props = payload.get("props", []) if isinstance(payload, dict) else []
        for r in props:
            if not isinstance(r, dict):
                continue
            result = _up(r.get("result"))
            if result not in {"HIT", "MISS"}:
                continue
            sport = _up(r.get("sport")) or "UNKNOWN"
            prop = _norm(r.get("prop_type") or r.get("prop")) or "UNKNOWN"
            pick = _up(r.get("pick_type")) or "UNKNOWN"
            tier = _up(r.get("tier")) or "UNKNOWN"
            direction = _up(r.get("direction")) or "UNKNOWN"
            player = _norm(r.get("player")) or "UNKNOWN"
            bucket = _line_bucket(r.get("line"))
            is_hit = 1 if result == "HIT" else 0
            rows.append(
                {
                    "sport": sport,
                    "prop": prop,
                    "pick_type": pick,
                    "tier": tier,
                    "direction": direction,
                    "line_bucket": bucket,
                    "player": player,
                    # Rich segment dimensions (when present in graded payloads).
                    "def_tier": _first_present(
                        r,
                        ["def_tier", "defense_tier", "opp_def_tier", "def_rating_tier", "defense_bucket"],
                    ),
                    "h2h_bucket": _first_present(
                        r,
                        ["h2h_tier", "h2h_bucket", "h2h_edge_bucket", "head_to_head_bucket", "opp_h2h_tier"],
                    ),
                    "minutes_tier": _first_present(
                        r,
                        ["minutes_tier", "min_tier", "minutes_bucket", "minutes_role_tier"],
                    ),
                    "role_tier": _first_present(
                        r,
                        ["role_tier", "player_role", "usage_role", "team_role", "starter_bench_tier"],
                    ),
                    "game_total_bucket": _first_present(
                        r,
                        ["game_total_bucket", "ou_bucket", "over_under_bucket", "total_bucket", "ou_tier"],
                    ),
                    "opp_team": _first_present(r, ["opp_team", "opponent", "opponent_team"], default="(unknown)"),
                    "is_hit": is_hit,
                }
            )

    if not rows:
        return {
            "history_dates": len(seen_dates),
            "history_props_total": 0,
            "eligible_props_total": 0,
            "top_segments": [],
            "top_consistent_players": [],
        }

    seg_rollup: dict[tuple[str, str, str, str, str], list[int]] = defaultdict(list)
    ply_rollup: dict[tuple[str, str, str, str, str], list[int]] = defaultdict(list)
    for r in rows:
        seg_k = (r["sport"], r["prop"], r["pick_type"], r["tier"], r["line_bucket"])
        seg_rollup[seg_k].append(int(r["is_hit"]))
        ply_k = (r["player"], r["sport"], r["prop"], r["direction"], r["line_bucket"])
        ply_rollup[ply_k].append(int(r["is_hit"]))

    top_segments: list[dict[str, Any]] = []
    for (sport, prop, pick, tier, bucket), hits in seg_rollup.items():
        n = len(hits)
        if n < _HIST_MIN_SEGMENT_N:
            continue
        h = int(sum(hits))
        hr = h / n if n else 0.0
        top_segments.append(
            {
                "sport": sport,
                "prop_type": prop,
                "pick_type": pick,
                "tier": tier,
                "line_bucket": bucket,
                "n": n,
                "hits": h,
                "hit_rate": round(hr, 4),
            }
        )
    top_segments.sort(key=lambda x: (-float(x["hit_rate"]), -int(x["n"])))

    ext_rollup: dict[tuple[str, str, str, str, str, str, str, str, str, str, str], list[int]] = defaultdict(list)
    for r in rows:
        k = (
            r["sport"],
            r["prop"],
            r["pick_type"],
            r["tier"],
            r["line_bucket"],
            r["direction"],  # OVER/UNDER
            r["def_tier"],
            r["h2h_bucket"],
            r["minutes_tier"],
            r["role_tier"],
            r["game_total_bucket"],
        )
        ext_rollup[k].append(int(r["is_hit"]))

    top_segments_extended: list[dict[str, Any]] = []
    for (
        sport,
        prop,
        pick,
        tier,
        bucket,
        direction,
        def_tier,
        h2h_bucket,
        minutes_tier,
        role_tier,
        game_total_bucket,
    ), hits in ext_rollup.items():
        n = len(hits)
        if n < _HIST_MIN_SEGMENT_N_EXT:
            continue
        h = int(sum(hits))
        hr = h / n if n else 0.0
        top_segments_extended.append(
            {
                "sport": sport,
                "prop_type": prop,
                "pick_type": pick,
                "tier": tier,
                "line_bucket": bucket,
                "over_under": direction,
                "def_tier": def_tier,
                "h2h_bucket": h2h_bucket,
                "minutes_tier": minutes_tier,
                "role_tier": role_tier,
                "game_total_bucket": game_total_bucket,
                "n": n,
                "hits": h,
                "hit_rate": round(hr, 4),
            }
        )
    top_segments_extended.sort(key=lambda x: (-float(x["hit_rate"]), -int(x["n"])))

    top_players: list[dict[str, Any]] = []
    for (player, sport, prop, direction, bucket), hits in ply_rollup.items():
        n = len(hits)
        if n < _HIST_MIN_PLAYER_N:
            continue
        h = int(sum(hits))
        hr = h / n if n else 0.0
        top_players.append(
            {
                "player": player,
                "sport": sport,
                "prop_type": prop,
                "direction": direction,
                "line_bucket": bucket,
                "n": n,
                "hits": h,
                "hit_rate": round(hr, 4),
            }
        )
    top_players.sort(key=lambda x: (-float(x["hit_rate"]), -int(x["n"])))

    return {
        "history_dates": len(seen_dates),
        "history_props_total": len(rows),
        "eligible_props_total": len(rows),
        "segment_min_n": _HIST_MIN_SEGMENT_N,
        "segment_min_n_extended": _HIST_MIN_SEGMENT_N_EXT,
        "player_min_n": _HIST_MIN_PLAYER_N,
        "top_segments": top_segments[:_HIST_TOP_N],
        "top_segments_extended": top_segments_extended[:_HIST_TOP_N],
        "top_consistent_players": top_players[:_HIST_TOP_N],
    }


def _load_grade_history_row(repo_root: Path, date_str: str) -> dict[str, Any] | None:
    p = repo_root / "data" / "grade_history.json"
    if not p.exists():
        return None
    raw = _read_json(p)
    rows = raw if isinstance(raw, list) else raw.get("runs", [])
    for r in rows:
        if isinstance(r, dict) and _norm(r.get("date")) == date_str:
            return r
    return None


def _load_ml_eval_row(repo_root: Path, date_str: str) -> dict[str, Any] | None:
    p = repo_root / "data" / "ml" / "ticket_model_eval_by_date.csv"
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if _norm(row.get("slate_date")) == date_str:
                return row
    return None


def build_report(repo_root: Path, date_str: str) -> dict[str, Any]:
    graded_props_path = repo_root / "ui_runner" / "templates" / f"graded_props_{date_str}.json"
    if not graded_props_path.exists():
        raise FileNotFoundError(f"Missing graded props JSON: {graded_props_path}")

    payload = _read_json(graded_props_path)
    props = payload.get("props", []) if isinstance(payload, dict) else []
    props = [r for r in props if isinstance(r, dict)]

    result_counts = Counter(_up(r.get("result")) for r in props)
    hit_rows = [r for r in props if _up(r.get("result")) == "HIT"]
    miss_rows = [r for r in props if _up(r.get("result")) == "MISS"]
    void_rows = [r for r in props if _up(r.get("result")) == "VOID"]

    by_sport_result: dict[str, Counter] = defaultdict(Counter)
    for r in props:
        sport = _up(r.get("sport")) or "UNKNOWN"
        by_sport_result[sport][_up(r.get("result"))] += 1

    miss_by_prop = Counter(_norm(r.get("prop_type") or r.get("prop")) or "UNKNOWN" for r in miss_rows)
    hit_by_prop = Counter(_norm(r.get("prop_type") or r.get("prop")) or "UNKNOWN" for r in hit_rows)
    miss_by_tier = Counter(_up(r.get("tier")) or "UNKNOWN" for r in miss_rows)
    hit_by_tier = Counter(_up(r.get("tier")) or "UNKNOWN" for r in hit_rows)
    miss_by_pick = Counter(_up(r.get("pick_type")) or "UNKNOWN" for r in miss_rows)
    hit_by_pick = Counter(_up(r.get("pick_type")) or "UNKNOWN" for r in hit_rows)
    void_reasons = Counter(_norm(r.get("void_reason") or r.get("reason")) or "UNKNOWN" for r in void_rows)

    sport_decided = {}
    for sport, c in sorted(by_sport_result.items()):
        h = int(c.get("HIT", 0))
        m = int(c.get("MISS", 0))
        d = h + m
        sport_decided[sport] = {
            "hits": h,
            "misses": m,
            "decided": d,
            "hit_rate_pct": round((100.0 * h / d), 2) if d else None,
            "voids": int(c.get("VOID", 0)),
        }

    grade_history = _load_grade_history_row(repo_root, date_str)
    ml_eval = _load_ml_eval_row(repo_root, date_str)
    historical = _build_historical_stratification(repo_root)

    report: dict[str, Any] = {
        "date": date_str,
        "source_files": {
            "graded_props_json": str(graded_props_path),
            "grade_history_json": str(repo_root / "data" / "grade_history.json"),
            "ml_eval_by_date_csv": str(repo_root / "data" / "ml" / "ticket_model_eval_by_date.csv"),
        },
        "prop_outcomes": {
            "total_props": int(len(props)),
            "result_counts": {k: int(v) for k, v in result_counts.items()},
            "hits": int(len(hit_rows)),
            "misses": int(len(miss_rows)),
            "voids": int(len(void_rows)),
            "sport_decided": sport_decided,
            "top_hit_prop_types": _top_counter(hit_by_prop, top_n=15),
            "top_miss_prop_types": _top_counter(miss_by_prop, top_n=15),
            "hit_by_tier": {k: int(v) for k, v in sorted(hit_by_tier.items())},
            "miss_by_tier": {k: int(v) for k, v in sorted(miss_by_tier.items())},
            "hit_by_pick_type": {k: int(v) for k, v in sorted(hit_by_pick.items())},
            "miss_by_pick_type": {k: int(v) for k, v in sorted(miss_by_pick.items())},
            "top_void_reasons": _top_counter(void_reasons, top_n=15),
        },
        "ticket_outcomes": None,
        "ticket_model_eval": None,
        "historical_stratification": historical,
    }

    if isinstance(grade_history, dict):
        wins = _safe_int(grade_history.get("wins"))
        losses = _safe_int(grade_history.get("losses"))
        guarantees = _safe_int(grade_history.get("guarantees"))
        n_tickets = _safe_int(grade_history.get("n_tickets"))
        report["ticket_outcomes"] = {
            "n_tickets": n_tickets,
            "wins": wins,
            "losses": losses,
            "guarantees": guarantees,
            "decided_win_loss": wins + losses,
            "decided_including_guarantees": wins + losses + guarantees,
            "win_rate": _safe_float(grade_history.get("win_rate"), default=0.0),
            "net_per_10": _safe_float(grade_history.get("net_per_10"), default=0.0),
            "roi_pct": _safe_float(grade_history.get("roi_pct"), default=0.0),
        }

    if isinstance(ml_eval, dict):
        report["ticket_model_eval"] = {
            "top_n": _safe_int(ml_eval.get("top_n"), default=0),
            "weight": _safe_float(ml_eval.get("weight"), default=0.0),
            "ev_n": _safe_int(ml_eval.get("ev_n"), default=0),
            "model_n": _safe_int(ml_eval.get("model_n"), default=0),
            "ev_cash_rate": _safe_float(ml_eval.get("ev_cash_rate"), default=0.0),
            "model_cash_rate": _safe_float(ml_eval.get("model_cash_rate"), default=0.0),
            "delta_cash_rate": _safe_float(ml_eval.get("delta_cash_rate"), default=0.0),
            "delta_avg_net_10": _safe_float(ml_eval.get("delta_avg_net_10"), default=0.0),
            "top_swapped_count": _safe_int(ml_eval.get("top_swapped_count"), default=0),
        }

    return report


def write_report_files(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    date_str = _norm(report.get("date"))
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"edge_quality_report_{date_str}.json"
    txt_path = out_dir / f"edge_quality_report_{date_str}.txt"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    p = report["prop_outcomes"]
    lines = [
        f"Edge Quality Report - {date_str}",
        "",
        f"Total props: {p['total_props']}",
        f"Hits: {p['hits']}",
        f"Misses: {p['misses']}",
        f"Voids: {p['voids']}",
        "",
        "Sport decided hit rates:",
    ]
    for sport, row in sorted(p["sport_decided"].items()):
        lines.append(
            f"- {sport}: hits={row['hits']} misses={row['misses']} "
            f"hit_rate={row['hit_rate_pct']}% voids={row['voids']}"
        )

    lines.append("")
    lines.append("Top hit prop types:")
    for x in p["top_hit_prop_types"][:10]:
        lines.append(f"- {x['key']}: {x['count']}")

    lines.append("")
    lines.append("Top miss prop types:")
    for x in p["top_miss_prop_types"][:10]:
        lines.append(f"- {x['key']}: {x['count']}")

    t = report.get("ticket_outcomes")
    if isinstance(t, dict):
        lines.extend(
            [
                "",
                "Ticket outcomes:",
                f"- tickets={t['n_tickets']} wins={t['wins']} losses={t['losses']} guarantees={t['guarantees']}",
                f"- decided(win/loss)={t['decided_win_loss']} decided(+guarantee)={t['decided_including_guarantees']}",
                f"- net_per_10={t['net_per_10']} roi_pct={t['roi_pct']}",
            ]
        )

    m = report.get("ticket_model_eval")
    if isinstance(m, dict):
        lines.extend(
            [
                "",
                "Ticket model eval (date row):",
                f"- ev_cash_rate={m['ev_cash_rate']:.4f} model_cash_rate={m['model_cash_rate']:.4f} delta_cash_rate={m['delta_cash_rate']:.4f}",
                f"- delta_avg_net_10={m['delta_avg_net_10']:.4f} top_swapped_count={m['top_swapped_count']}",
            ]
        )
    else:
        lines.extend(["", "Ticket model eval: no row for this date in ticket_model_eval_by_date.csv"])

    hist = report.get("historical_stratification")
    if isinstance(hist, dict):
        lines.extend(
            [
                "",
                "Historical stratification (all graded_props history):",
                f"- dates={hist.get('history_dates', 0)} decided_props={hist.get('eligible_props_total', 0)}",
                f"- segment_min_n={hist.get('segment_min_n', _HIST_MIN_SEGMENT_N)} player_min_n={hist.get('player_min_n', _HIST_MIN_PLAYER_N)}",
                "",
                "Top high-efficiency segments:",
            ]
        )
        for x in hist.get("top_segments", [])[:10]:
            lines.append(
                f"- {x['sport']} | {x['prop_type']} | {x['pick_type']} | tier={x['tier']} | line={x['line_bucket']} | hit_rate={x['hit_rate']:.4f} n={x['n']}"
            )
        lines.append("")
        lines.append("Top high-efficiency extended segments (def/h2h/minutes/role/OU):")
        for x in hist.get("top_segments_extended", [])[:10]:
            lines.append(
                f"- {x['sport']} | {x['prop_type']} | {x['pick_type']} | tier={x['tier']} | line={x['line_bucket']} | ou={x['over_under']} | def={x['def_tier']} | h2h={x['h2h_bucket']} | min={x['minutes_tier']} | role={x['role_tier']} | total={x['game_total_bucket']} | hit_rate={x['hit_rate']:.4f} n={x['n']}"
            )
        lines.append("")
        lines.append("Top consistent player pockets:")
        for x in hist.get("top_consistent_players", [])[:10]:
            lines.append(
                f"- {x['player']} | {x['sport']} | {x['prop_type']} {x['direction']} | line={x['line_bucket']} | hit_rate={x['hit_rate']:.4f} n={x['n']}"
            )

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build daily edge quality summary report.")
    ap.add_argument("--date", required=True, help="Run date in YYYY-MM-DD")
    ap.add_argument(
        "--out-dir",
        default="",
        help="Output directory (default: outputs/<date>/)",
    )
    args = ap.parse_args()

    repo_root = _repo_root()
    out_dir = Path(args.out_dir) if args.out_dir else (repo_root / "outputs" / args.date)
    report = build_report(repo_root, args.date)
    json_path, txt_path = write_report_files(report, out_dir)
    print(f"Wrote: {json_path}")
    print(f"Wrote: {txt_path}")


if __name__ == "__main__":
    main()
