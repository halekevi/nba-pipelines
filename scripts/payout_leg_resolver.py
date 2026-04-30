#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

QUALITY_EXACT_SAME = "exact_same_snapshot"
QUALITY_EXACT_NEAREST = "exact_nearest_snapshot"
QUALITY_APPROX = "approximate"
QUALITY_UNRESOLVED = "unresolved"


def _safe_float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _norm_ascii(s: Any) -> str:
    raw = str(s or "").strip().lower()
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("&", " and ")
    raw = raw.replace("+/-", " plus minus ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


PROP_ALIASES = {
    "sogs": "shots on goal",
    "sog": "shots on goal",
    "shot on goal": "shots on goal",
    "goalie save": "goalie saves",
}


def normalize_prop_name(v: Any) -> str:
    n = _norm_ascii(v)
    return PROP_ALIASES.get(n, n)


PLAYER_ALIASES = {
    "jj peterka": "j j peterka",
    "j j peterka": "j j peterka",
}


def normalize_player_name(v: Any) -> str:
    n = _norm_ascii(v)
    n = n.replace(".", " ")
    n = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1 \2", n)
    n = re.sub(r"\s+", " ", n).strip()
    return PLAYER_ALIASES.get(n, n)


def _norm_dir(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"MORE", "OVER", "O"}:
        return "OVER"
    if s in {"LESS", "UNDER", "U"}:
        return "UNDER"
    return ""


def _norm_pick_type(v: Any) -> str:
    s = _norm_ascii(v)
    if "gob" in s:
        return "goblin"
    if "dem" in s:
        return "demon"
    return "standard"


def _parse_date(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return pd.to_datetime(s, errors="coerce").date().isoformat()
    except Exception:
        return ""


@dataclass
class SnapshotRow:
    snapshot_date: str
    snapshot_path: str
    player_norm: str
    prop_norm: str
    direction: str
    line: float
    pick_type: str
    player_raw: str
    prop_raw: str


class PayoutLegResolver:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self._rows: list[SnapshotRow] = []
        self._load()

    def _snapshot_candidates(self) -> list[Path]:
        rr = self.repo_root
        cands = [
            rr / "NHL" / "outputs" / "step1_nhl_props.csv",
            rr / "NHL" / "outputs" / "step2_nhl_picktypes.csv",
            rr / "NHL" / "outputs" / "step8_nhl_direction_clean.csv",
            rr / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
        ]
        cands.extend(sorted((rr / "outputs").glob("*/combined_slate_tickets_*.xlsx"), reverse=True)[:5])
        return [p for p in cands if p.exists()]

    def _col(self, df: pd.DataFrame, names: list[str]) -> str | None:
        m = {str(c).strip().lower(): c for c in df.columns}
        for n in names:
            if n.lower() in m:
                return m[n.lower()]
        return None

    def _load_csv_like(self, p: Path) -> pd.DataFrame:
        if p.suffix.lower() == ".xlsx":
            xls = pd.ExcelFile(p)
            sh = "Full Slate" if "Full Slate" in xls.sheet_names else xls.sheet_names[0]
            return pd.read_excel(p, sheet_name=sh)
        return pd.read_csv(p, low_memory=False)

    def _load(self) -> None:
        rows: list[SnapshotRow] = []
        for p in self._snapshot_candidates():
            try:
                df = self._load_csv_like(p)
            except Exception:
                continue
            pcol = self._col(df, ["player", "player_name", "Player"])
            prcol = self._col(df, ["prop_type", "prop", "Prop", "stat_type"])
            lcol = self._col(df, ["line", "Line", "line_score"])
            dcol = self._col(df, ["direction", "dir", "Direction", "final_bet_direction"])
            ptcol = self._col(df, ["pick_type", "Pick Type"])
            dtcol = self._col(df, ["game_date", "date", "Date", "start_time", "Start Time", "game_start"])
            sport_col = self._col(df, ["sport", "Sport"])
            if not pcol or not prcol or not lcol:
                continue
            for _, r in df.iterrows():
                if sport_col and _norm_ascii(r.get(sport_col)) not in {"", "nhl"}:
                    continue
                line = _safe_float(r.get(lcol))
                if line is None:
                    continue
                pick = _norm_pick_type(r.get(ptcol))
                if pick != "standard":
                    continue
                player_raw = str(r.get(pcol) or "").strip()
                prop_raw = str(r.get(prcol) or "").strip()
                if not player_raw or not prop_raw:
                    continue
                rows.append(
                    SnapshotRow(
                        snapshot_date=_parse_date(r.get(dtcol)),
                        snapshot_path=str(p),
                        player_norm=normalize_player_name(player_raw),
                        prop_norm=normalize_prop_name(prop_raw),
                        direction=_norm_dir(r.get(dcol)),
                        line=float(line),
                        pick_type=pick,
                        player_raw=player_raw,
                        prop_raw=prop_raw,
                    )
                )
        self._rows = rows

    def resolve_leg(
        self,
        *,
        date: str,
        sport: str,
        player: str,
        prop: str,
        direction: str,
        played_line: Any,
        pick_type: str,
    ) -> dict[str, Any]:
        if _norm_ascii(sport) not in {"nhl", ""}:
            return {
                "delta_quality": QUALITY_UNRESOLVED,
                "matched_snapshot_path": "",
                "matched_standard_line": None,
                "delta_method": "non_nhl_sport",
                "delta": None,
            }
        played = _safe_float(played_line)
        if played is None:
            return {
                "delta_quality": QUALITY_UNRESOLVED,
                "matched_snapshot_path": "",
                "matched_standard_line": None,
                "delta_method": "invalid_played_line",
                "delta": None,
            }
        req_date = _parse_date(date)
        pn = normalize_player_name(player)
        pr = normalize_prop_name(prop)
        dd = _norm_dir(direction)
        ptype = _norm_pick_type(pick_type)

        exact_pool = [
            r
            for r in self._rows
            if r.player_norm == pn and r.prop_norm == pr and (not dd or not r.direction or r.direction == dd)
        ]
        if not exact_pool:
            approx_pool = [
                r
                for r in self._rows
                if r.player_norm == pn and (r.prop_norm == pr or pr in r.prop_norm or r.prop_norm in pr)
            ]
            if not approx_pool:
                return {
                    "delta_quality": QUALITY_UNRESOLVED,
                    "matched_snapshot_path": "",
                    "matched_standard_line": None,
                    "delta_method": "no_candidate_row",
                    "delta": None,
                }
            best = min(approx_pool, key=lambda r: abs(r.line - played))
            return {
                "delta_quality": QUALITY_APPROX,
                "matched_snapshot_path": best.snapshot_path,
                "matched_standard_line": round(float(best.line), 3),
                "delta_method": "approx_player_prop",
                "delta": round(abs(float(best.line) - played), 3) if ptype != "standard" else 0.0,
            }

        same_day = [r for r in exact_pool if req_date and r.snapshot_date == req_date]
        if same_day:
            best = min(same_day, key=lambda r: abs(r.line - played))
            return {
                "delta_quality": QUALITY_EXACT_SAME,
                "matched_snapshot_path": best.snapshot_path,
                "matched_standard_line": round(float(best.line), 3),
                "delta_method": "exact_same_snapshot",
                "delta": round(abs(float(best.line) - played), 3) if ptype != "standard" else 0.0,
            }

        if req_date:
            def _days(r: SnapshotRow) -> int:
                if not r.snapshot_date:
                    return 999999
                try:
                    a = datetime.strptime(req_date, "%Y-%m-%d").date()
                    b = datetime.strptime(r.snapshot_date, "%Y-%m-%d").date()
                    return abs((a - b).days)
                except ValueError:
                    return 999999

            best = min(exact_pool, key=lambda r: (_days(r), abs(r.line - played)))
            return {
                "delta_quality": QUALITY_EXACT_NEAREST,
                "matched_snapshot_path": best.snapshot_path,
                "matched_standard_line": round(float(best.line), 3),
                "delta_method": "exact_nearest_snapshot",
                "delta": round(abs(float(best.line) - played), 3) if ptype != "standard" else 0.0,
            }

        best = min(exact_pool, key=lambda r: abs(r.line - played))
        return {
            "delta_quality": QUALITY_EXACT_NEAREST,
            "matched_snapshot_path": best.snapshot_path,
            "matched_standard_line": round(float(best.line), 3),
            "delta_method": "exact_nearest_snapshot",
            "delta": round(abs(float(best.line) - played), 3) if ptype != "standard" else 0.0,
        }
