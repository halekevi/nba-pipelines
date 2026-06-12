#!/usr/bin/env python3
"""
Per-sport model performance from graded_props JSON (AUC, Brier, hit rate).

Appends one JSON line per run to data/model_performance_log.jsonl.
Writes data/model_alerts.json when a sport AUC < 0.50 for 3 consecutive days.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_LOG = _REPO / "data" / "model_performance_log.jsonl"
_ALERTS = _REPO / "data" / "model_alerts.json"
_GATES = _REPO / "data" / "model_gate_recommendations.json"
_MIN_N = 30
_ALWAYS_ALLOW_SPORTS = frozenset({"NBA", "MLB"})
_GATE_AUC_THRESHOLD = 0.50
_NBA1H_UNBLOCK_AUC = 0.52
_NBA1H_MONITOR_MIN_30D = 10
_NBA1H_MONITOR_MIN_7D = 5
_GRADED_DIRS = (
    _REPO / "ui_runner" / "templates",
    _REPO / "mobile" / "www",
)


def _parse_hit(result: object) -> int | None:
    t = str(result or "").strip().upper()
    if t in ("HIT", "WIN", "W", "1", "TRUE"):
        return 1
    if t in ("MISS", "LOSS", "L", "0", "FALSE"):
        return 0
    if result in (0, 1):
        return int(result)
    return None


def _roc_auc(y_true: np.ndarray, y_score: np.ndarray, *, min_n: int) -> float | None:
    if len(y_true) < min_n:
        return None
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y_true, y_score))
    except Exception:
        pass
    n_pos = float(y_true.sum())
    n_neg = float(len(y_true) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(y_score)
    y_sorted = y_true[order]
    tpr = np.cumsum(y_sorted) / n_pos
    fpr = np.cumsum(1 - y_sorted) / n_neg
    return float(np.trapz(tpr, fpr))


def _brier(y_true: np.ndarray, y_prob: np.ndarray, *, min_n: int) -> float | None:
    if len(y_true) < min_n:
        return None
    return float(np.mean((y_prob - y_true) ** 2))


def load_graded(root: Path, *, days: int) -> pd.DataFrame:
    paths = sorted((root / "mobile" / "www").glob("graded_props_*.json"))
    if days > 0:
        paths = paths[-days:]
    rows: list[dict] = []
    for path in paths:
        date_str = path.stem.replace("graded_props_", "")[:10]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunk = data if isinstance(data, list) else data.get("props", data.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            hit = r.get("hit")
            if hit is None:
                hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            prob = pd.to_numeric(r.get("ml_prob"), errors="coerce")
            rows.append(
                {
                    "date": date_str,
                    "sport": str(r.get("sport", "")).strip().upper(),
                    "hit": int(hit),
                    "ml_prob": prob,
                }
            )
    return pd.DataFrame(rows)


def sport_metrics(df: pd.DataFrame, *, min_n: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sport, g in df.groupby("sport"):
        sub = g[g["ml_prob"].notna()].copy()
        n = len(sub)
        if n == 0:
            out[sport] = {
                "n": int(len(g)),
                "auc": None,
                "hit_rate": float(g["hit"].mean()) if len(g) else None,
                "calibration_error": None,
                "status": "insufficient data",
                "note": "no ml_prob",
            }
            continue
        y = sub["hit"].astype(int).to_numpy()
        p = sub["ml_prob"].astype(float).to_numpy()
        p = np.clip(p, 0.01, 0.99)
        auc = _roc_auc(y, p, min_n=min_n)
        brier = _brier(y, p, min_n=min_n)
        hr = float(y.mean())
        cal_err = abs(hr - float(p.mean())) if len(p) else None
        if auc is None:
            status = "insufficient data"
        elif auc >= 0.55:
            status = "OK"
        elif auc >= 0.50:
            status = "WARN"
        else:
            status = "ALERT"
        out[sport] = {
            "n": int(n),
            "auc": auc,
            "hit_rate": hr,
            "calibration_error": cal_err,
            "brier": brier,
            "status": status,
        }
    return out


def _status_icon(status: str) -> str:
    return {"OK": "OK", "WARN": "WARN", "ALERT": "ALERT"}.get(status, status)


def print_table(sports: dict[str, dict]) -> None:
    print(f"\n{'Sport':<10} {'AUC':>8} {'HR':>8} {'CalErr':>8} {'N':>8} {'Status':>12}")
    print("-" * 58)
    ranked = sorted(
        sports.items(),
        key=lambda kv: (kv[1].get("auc") is not None, kv[1].get("auc") or 0.0),
        reverse=True,
    )
    for sport, m in ranked:
        auc = m.get("auc")
        hr = m.get("hit_rate")
        cal = m.get("calibration_error")
        auc_s = f"{auc:.4f}" if auc is not None else "—"
        hr_s = f"{hr:.1%}" if hr is not None else "—"
        cal_s = f"{cal:.3f}" if cal is not None else "—"
        print(
            f"{sport:<10} {auc_s:>8} {hr_s:>8} {cal_s:>8} {int(m.get('n', 0)):8d} "
            f"{_status_icon(str(m.get('status', ''))):>12}"
        )


_NBA1H_MONITOR_KEYS = (
    "consecutive_days_above_052",
    "rolling_30d_auc",
    "rolling_7d_auc",
    "inversion_flag",
    "sample_n_30d",
    "sample_n_7d",
    "sample_n",
    "trend",
    "last_checked",
)


def write_gate_recommendations(sports: dict[str, dict]) -> dict[str, dict]:
    """Sport-level ticket gate flags for combined_slate_tickets auto-gate."""
    prior: dict = {}
    if _GATES.is_file():
        try:
            loaded = json.loads(_GATES.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prior = loaded
        except Exception:
            prior = {}
    out: dict[str, dict] = {}
    for sport, metrics in sorted(sports.items()):
        su = str(sport).strip().upper()
        auc = metrics.get("auc")
        if su in _ALWAYS_ALLOW_SPORTS:
            out[su] = {
                "gate": False,
                "auc": auc,
                "reason": "OK (ALWAYS_ALLOW_SPORTS)",
            }
            continue
        status = str(metrics.get("status") or "")
        if auc is None or status == "insufficient data":
            out[su] = {"gate": False, "auc": auc, "reason": "insufficient data"}
        elif float(auc) < _GATE_AUC_THRESHOLD:
            out[su] = {
                "gate": True,
                "auc": float(auc),
                "reason": f"AUC {float(auc):.4f} < {_GATE_AUC_THRESHOLD}",
            }
        else:
            out[su] = {
                "gate": False,
                "auc": float(auc),
                "reason": "OK",
            }
    prior_nba1h = prior.get("NBA1H") if isinstance(prior.get("NBA1H"), dict) else {}
    if prior_nba1h:
        merged = dict(out.get("NBA1H", {}))
        for key in _NBA1H_MONITOR_KEYS:
            if key in prior_nba1h:
                merged[key] = prior_nba1h[key]
        if "consecutive_days_above_052" in prior_nba1h:
            streak = int(prior_nba1h.get("consecutive_days_above_052") or 0)
            roll_auc = prior_nba1h.get("rolling_30d_auc", merged.get("auc"))
            hard_block = roll_auc is not None and float(roll_auc) < _GATE_AUC_THRESHOLD
            unblocked = (
                streak >= 3
                and roll_auc is not None
                and float(roll_auc) >= _NBA1H_UNBLOCK_AUC
            )
            merged["gate"] = bool(hard_block or not unblocked)
            if hard_block:
                merged["reason"] = f"AUC {float(roll_auc):.4f} < {_GATE_AUC_THRESHOLD}"
            elif not unblocked:
                merged["reason"] = (
                    f"AUC {float(roll_auc):.4f} OK; streak {streak}/3 above {_NBA1H_UNBLOCK_AUC}"
                )
        out["NBA1H"] = merged
    _GATES.parent.mkdir(parents=True, exist_ok=True)
    _GATES.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _graded_props_paths() -> dict[str, Path]:
    """file_date -> path; ui_runner/templates wins over mobile/www."""
    by_date: dict[str, Path] = {}
    for base in (_REPO / "mobile" / "www", _REPO / "ui_runner" / "templates"):
        if not base.is_dir():
            continue
        for path in sorted(base.glob("graded_props_*.json")):
            m = re.match(r"graded_props_(\d{4}-\d{2}-\d{2})\.json$", path.name)
            if m:
                by_date[m.group(1)] = path
    return by_date


def load_nba1h_graded_rows() -> list[dict]:
    rows: list[dict] = []
    for file_date, path in _graded_props_paths().items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunk = data if isinstance(data, list) else data.get("props", data.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            if str(r.get("sport", "")).strip().upper() != "NBA1H":
                continue
            res = str(r.get("result", "")).strip().upper()
            if res in ("VOID", "PUSH", ""):
                continue
            hit = r.get("hit")
            if hit is None:
                hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            prob = pd.to_numeric(r.get("ml_prob"), errors="coerce")
            if pd.isna(prob):
                continue
            rows.append(
                {
                    "file_date": file_date,
                    "hit": int(hit),
                    "ml_prob": float(prob),
                }
            )
    return rows


def _auc_for_window(rows: list[dict], *, end: date, days: int, min_n: int) -> tuple[float | None, int]:
    start = end - timedelta(days=days)
    sub = [
        r
        for r in rows
        if start <= date.fromisoformat(r["file_date"]) <= end
    ]
    if len(sub) < min_n:
        return None, len(sub)
    y = np.array([r["hit"] for r in sub], dtype=int)
    p = np.clip(np.array([r["ml_prob"] for r in sub], dtype=float), 0.01, 0.99)
    if len(np.unique(y)) < 2:
        return None, len(sub)
    return _roc_auc(y, p, min_n=min_n), len(sub)


def _daily_nba1h_auc_from_log() -> list[tuple[date, float]]:
    """Descending by date: (day, auc) from monitor lines and legacy sports.NBA1H entries."""
    daily: dict[date, float] = {}
    if not _LOG.is_file():
        return []
    for line in _LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if str(entry.get("sport", "")).strip().upper() == "NBA1H":
            d_raw = str(entry.get("date", "")).strip()[:10]
            auc = entry.get("auc")
            if d_raw and isinstance(auc, (int, float)):
                daily[date.fromisoformat(d_raw)] = float(auc)
            continue
        sports = entry.get("sports") or {}
        sm = sports.get("NBA1H") or {}
        auc = sm.get("auc")
        if auc is None:
            continue
        run_at = str(entry.get("run_at", "")).strip()
        d_raw = run_at[:10] if len(run_at) >= 10 else ""
        if d_raw:
            daily[date.fromisoformat(d_raw)] = float(auc)
    return sorted(daily.items(), key=lambda kv: kv[0], reverse=True)


def _consecutive_days_above_052(daily_desc: list[tuple[date, float]], *, threshold: float = _NBA1H_UNBLOCK_AUC) -> int:
    if not daily_desc:
        return 0
    streak = 0
    prev_day: date | None = None
    for day, auc in daily_desc:
        if auc < threshold:
            break
        if prev_day is not None and (prev_day - day).days > 1:
            break
        streak += 1
        prev_day = day
    return streak


def run_nba1h_monitor(run_date: date | None = None) -> None:
    end = run_date or date.today()
    rows = load_nba1h_graded_rows()
    auc_30d, n_30d = _auc_for_window(rows, end=end, days=30, min_n=_NBA1H_MONITOR_MIN_30D)
    auc_7d, n_7d = _auc_for_window(rows, end=end, days=7, min_n=_NBA1H_MONITOR_MIN_7D)

    if n_30d < _NBA1H_MONITOR_MIN_30D:
        print(
            f"[NBA1H monitor] {end.isoformat()} | SKIP — only {n_30d} graded NBA1H legs "
            f"in 30d (need {_NBA1H_MONITOR_MIN_30D})"
        )
        return

    gates: dict = {}
    if _GATES.is_file():
        try:
            gates = json.loads(_GATES.read_text(encoding="utf-8"))
            if not isinstance(gates, dict):
                gates = {}
        except Exception:
            gates = {}

    prior_block = gates.get("NBA1H") if isinstance(gates.get("NBA1H"), dict) else {}
    prior_30 = prior_block.get("rolling_30d_auc")
    if auc_30d is None:
        trend = "unknown"
    elif prior_30 is None or not isinstance(prior_30, (int, float)):
        trend = "unknown"
    else:
        delta = float(auc_30d) - float(prior_30)
        if delta > 0.01:
            trend = "improving"
        elif delta < -0.01:
            trend = "degrading"
        else:
            trend = "flat"

    daily = _daily_nba1h_auc_from_log()
    if auc_30d is not None:
        daily = sorted(
            {(end, float(auc_30d)), *daily},
            key=lambda kv: kv[0],
            reverse=True,
        )
    streak = _consecutive_days_above_052(daily)

    hard_block = bool(auc_30d is not None and float(auc_30d) < _GATE_AUC_THRESHOLD)
    unblocked = (
        streak >= 3
        and auc_30d is not None
        and float(auc_30d) >= _NBA1H_UNBLOCK_AUC
    )
    gated = bool(hard_block or not unblocked)
    if auc_30d is None:
        reason = "insufficient data for 30d AUC"
    elif hard_block:
        reason = f"AUC {float(auc_30d):.4f} < {_GATE_AUC_THRESHOLD}"
    elif unblocked:
        reason = f"AUC {float(auc_30d):.4f} OK; streak {streak}/3 above {_NBA1H_UNBLOCK_AUC}"
    else:
        reason = f"AUC {float(auc_30d):.4f} OK; streak {streak}/3 above {_NBA1H_UNBLOCK_AUC}"

    nba1h_block = dict(prior_block)
    nba1h_block.update(
        {
            "rolling_30d_auc": auc_30d,
            "rolling_7d_auc": auc_7d,
            "inversion_flag": bool(auc_30d is not None and float(auc_30d) < 0.50),
            "sample_n_30d": int(n_30d),
            "sample_n_7d": int(n_7d),
            "sample_n": int(n_30d),
            "trend": trend,
            "last_checked": end.isoformat(),
            "consecutive_days_above_052": int(streak),
            "auc": auc_30d,
            "gate": gated,
            "reason": reason,
        }
    )
    gates["NBA1H"] = nba1h_block
    _GATES.parent.mkdir(parents=True, exist_ok=True)
    _GATES.write_text(json.dumps(gates, indent=2), encoding="utf-8")

    status = "UNBLOCKED" if streak >= 3 else ("BLOCKED" if gated else "OK")
    log_line = {
        "date": end.isoformat(),
        "sport": "NBA1H",
        "auc": auc_30d,
        "n": n_30d,
        "status": status,
    }
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_line, ensure_ascii=False) + "\n")

    auc30_s = f"{auc_30d:.4f}" if auc_30d is not None else "—"
    auc7_s = f"{auc_7d:.4f}" if auc_7d is not None else "—"
    gate_label = "BLOCKED" if _nba1h_gated_from_block(nba1h_block) else "OPEN"
    print(
        f"[NBA1H monitor] {end.isoformat()} | 30d AUC: {auc30_s} | 7d AUC: {auc7_s} | "
        f"n={n_30d} | trend: {trend} | streak={streak}/3 | gate: {gate_label}"
    )


def _nba1h_gated_from_block(block: dict) -> bool:
    streak = int(block.get("consecutive_days_above_052") or 0)
    auc = block.get("rolling_30d_auc")
    if streak >= 3 and auc is not None and float(auc) >= _NBA1H_UNBLOCK_AUC:
        return False
    return bool(block.get("gate", True))


def update_alerts(sports: dict[str, dict], *, history_lines: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    recent = history_lines[-3:]
    for sport in sports:
        series = []
        for entry in recent:
            sm = (entry.get("sports") or {}).get(sport) or {}
            auc = sm.get("auc")
            if auc is not None:
                series.append(float(auc))
        if len(series) >= 3 and all(a < 0.50 for a in series):
            alerts.append(
                {
                    "sport": sport,
                    "message": f"{sport} AUC below 0.50 for 3 consecutive tracking runs",
                    "auc_series": series,
                }
            )
    _ALERTS.parent.mkdir(parents=True, exist_ok=True)
    _ALERTS.write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    return alerts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=_MIN_N)
    ap.add_argument(
        "--nba1h-monitor",
        action="store_true",
        help="Run NBA1H-only rolling AUC monitor and update gate JSON (then exit).",
    )
    ap.add_argument(
        "--date",
        default="",
        help="Reference date YYYY-MM-DD for --nba1h-monitor (default: today).",
    )
    args = ap.parse_args()

    if args.nba1h_monitor:
        run_d: date | None = None
        if str(args.date).strip():
            run_d = date.fromisoformat(str(args.date).strip()[:10])
        run_nba1h_monitor(run_d)
        return 0

    min_n = max(10, int(args.min_n))

    df = load_graded(_REPO, days=args.days)
    if df.empty:
        print("No graded props with ml_prob and decided results.")
        return 1

    sports = sport_metrics(df, min_n=min_n)
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {"run_at": run_at, "window_days": args.days, "sports": sports}
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    history: list[dict] = []
    if _LOG.is_file():
        for line in _LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    history.append(json.loads(line))
                except Exception:
                    pass

    alerts = update_alerts(sports, history_lines=history)
    gates = write_gate_recommendations(sports)
    print_table(sports)
    print("\nTicket gate recommendations:")
    for sport, rec in sorted(gates.items()):
        status = "GATED" if rec.get("gate") else "OK"
        auc = rec.get("auc")
        auc_s = f"{auc:.4f}" if auc is not None else "—"
        print(f"  {sport:<10} AUC={auc_s} {status} — {rec.get('reason', '')}")
    if alerts:
        print(f"\n{len(alerts)} alert(s) written -> {_ALERTS}")
    print(f"\nGate file -> {_GATES}")
    print(f"Appended run -> {_LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
