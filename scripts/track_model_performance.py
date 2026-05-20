#!/usr/bin/env python3
"""
Per-sport model performance from graded_props JSON (AUC, Brier, hit rate).

Appends one JSON line per run to data/model_performance_log.jsonl.
Writes data/model_alerts.json when a sport AUC < 0.50 for 3 consecutive days.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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


def write_gate_recommendations(sports: dict[str, dict]) -> dict[str, dict]:
    """Sport-level ticket gate flags for combined_slate_tickets auto-gate."""
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
    _GATES.parent.mkdir(parents=True, exist_ok=True)
    _GATES.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


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
    args = ap.parse_args()
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
