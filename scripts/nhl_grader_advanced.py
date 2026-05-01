#!/usr/bin/env python3
"""
nhl_grader_advanced.py

PropOracle Advanced NHL Grader with Opponent Comparison & Analytics

FEATURES:
  ✅ Full prop grading (HIT/MISS/PUSH/VOID)
  ✅ Opponent-specific comparisons:
     - Avg performance vs this opponent (SOG, Saves, GA, etc.)
     - Last game vs this opponent
     - Home/Away splits
  ✅ Confidence scoring (0-100)
  ✅ Multi-dimensional analysis:
     - Edge analysis (actual vs line vs projection)
     - Tier performance tracking
     - Player role impact (skater vs goalie)
  ✅ Visual reports:
     - Hit rate by position
     - Goalie save % vs expected
     - Skater SOG distribution
  ✅ Pick strengthening recommendations:
     - Props that consistently hit vs opponent
     - Line movement analysis
     - Usage tier impact

INPUTS:
  - actuals_nhl_YYYY-MM-DD.csv (player, team, prop_type, actual)
  - s8_nhl_direction_clean.xlsx (ranked slate)
  - s6a_nhl_opp_stats_cache.csv (optional, opponent history)
  - nhl_gamelog_cache.json (optional, game logs)

OUTPUTS:
  - graded_nhl_YYYY-MM-DD.xlsx (detailed grades + analytics)
  - grades_report_nhl_YYYY-MM-DD.html (HTML dashboard)
  - nhl_opponent_analysis_YYYY-MM-DD.csv (opponent-specific insights)
  - nhl_pick_recommendations_YYYY-MM-DD.csv (strengthening suggestions)

USAGE:
  py nhl_grader_advanced.py \\
    --date 2026-02-21 \\
    --actuals actuals_nhl_2026-02-21.csv \\
    --slate s8_nhl_direction_clean.xlsx \\
    --opp-cache s6a_nhl_opp_stats_cache.csv
"""

from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import json

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils.dataframe import dataframe_to_rows

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from player_name_norm import fold_player_name  # noqa: E402
from utils.slate_fields import (  # noqa: E402
    first_numeric_in_slate_row,
    first_over_under_in_slate_row,
)


def _normalize_nhl_prop_key(raw) -> str:
    """
    Match slate snake_case (blocked_shots) to actuals labels ("Blocked Shots", "Blocked Shots").
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s = str(raw).strip().lower().replace("/", "_")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _slate_ml_prob_nhl(row: pd.Series) -> float:
    """Carry ml_prob from NHL step8 into graded rows (non-fatal if missing)."""
    for k in ("ml_prob", "ML Prob", "MLProb"):
        if k not in row.index:
            continue
        v = row[k]
        if pd.isna(v):
            continue
        s = str(v).strip().lower()
        if s in ("", "nan", "none"):
            continue
        try:
            x = float(v)
            if x > 1.0:
                x /= 100.0
            return float(np.clip(x, 1e-3, 1.0 - 1e-3))
        except (TypeError, ValueError):
            continue
    return float(np.nan)


def _build_nhl_actuals_lookup(actuals_df: pd.DataFrame) -> Dict[Tuple[str, str, str], tuple[float, str, int]]:
    """(folded_player, TEAM, normalized_prop) -> (actual, source, source_conflict)."""
    lut: Dict[Tuple[str, str, str], tuple[float, str, int]] = {}
    for _, row in actuals_df.iterrows():
        pl = fold_player_name(row.get("player", ""))
        tm = str(row.get("team", "")).strip().upper()
        pk = _normalize_nhl_prop_key(row.get("prop_type", ""))
        if not pl or not tm or not pk:
            continue
        act = pd.to_numeric(row.get("actual"), errors="coerce")
        if pd.isna(act):
            continue
        src = str(row.get("source", "")).strip().lower()
        src_conf = int(pd.to_numeric(row.get("source_conflict"), errors="coerce") or 0)
        lut[(pl, tm, pk)] = (float(act), src, src_conf)
    return lut


# ── NHL CONFIGURATION ─────────────────────────────────────────────────────────

NHL_POSITIONS = {
    "skater": ["Shots on Goal", "Hits", "Blocked Shots", "Assists", "Points"],
    "goalie": ["Saves", "Goals Allowed", "Save %", "Shutout"],
}

PLAYER_TYPE_THRESHOLDS = {
    "goalie": ["Saves", "Goals Allowed", "Save %", "Shutout"],
    "skater": ["Shots", "Goals", "Assists", "Points", "Hits", "Blocks", "SOG"],
}

CONFIDENCE_WEIGHTS = {
    "goalie": {
        "hit_rate": 0.40,
        "edge": 0.20,
        "opp_history": 0.20,
        "sample_size": 0.20,
    },
    "skater": {
        "hit_rate": 0.35,
        "edge": 0.25,
        "opp_history": 0.20,
        "role_impact": 0.20,
    }
}


# ── GRADING ENGINE ────────────────────────────────────────────────────────────

class NHLGrader:
    """Advanced NHL prop grader with opponent analysis."""
    
    def __init__(self, opp_cache: pd.DataFrame = None, gamelog: dict = None):
        self.opp_cache = opp_cache
        self.gamelog = gamelog or {}
    
    def grade_prop(
        self,
        actual: float,
        line: float,
        direction: str = "OVER",
        player_type: str = "skater"
    ) -> Tuple[str, float, float]:
        """
        Grade a single NHL prop.
        
        Returns: (result, edge, % above/below line)
        """
        if pd.isna(actual) or pd.isna(line):
            return "VOID", np.nan, np.nan
        
        pct_of_line = (actual / line - 1) * 100 if line > 0 else np.nan
        edge = actual - line
        
        if direction == "OVER":
            if actual > line + 0.01:  # Small buffer for floating point
                return "HIT", edge, pct_of_line
            elif abs(actual - line) < 0.01:  # Push
                return "PUSH", 0, 0
            else:
                return "MISS", edge, pct_of_line
        else:  # UNDER
            if actual < line - 0.01:
                return "HIT", -edge, pct_of_line
            elif abs(actual - line) < 0.01:
                return "PUSH", 0, 0
            else:
                return "MISS", -edge, pct_of_line
    
    def get_opponent_analysis(
        self,
        player: str,
        opp_team: str,
        prop_type: str
    ) -> Dict[str, float]:
        """
        Analyze player's historical performance vs opponent.
        
        Returns: avg vs opponent, last game, home/away
        """
        result = {
            "opp_avg": np.nan,
            "opp_last_game": np.nan,
            "opp_games": 0,
            "opp_home_avg": np.nan,
            "opp_away_avg": np.nan,
            "opp_trend": "stable",  # up, down, stable
        }
        
        if self.opp_cache is None or len(self.opp_cache) == 0:
            return result
        
        # Filter opponent games
        opp_games = self.opp_cache[
            (self.opp_cache["player"].str.lower() == player.lower()) &
            (self.opp_cache["opp_team"].str.upper() == opp_team.upper()) &
            (self.opp_cache["prop_type"].str.lower() == prop_type.lower())
        ]
        
        if len(opp_games) == 0:
            return result
        
        opp_games = opp_games.sort_values("game_date")
        
        result["opp_games"] = len(opp_games)
        result["opp_avg"] = pd.to_numeric(opp_games["actual"], errors="coerce").mean()
        result["opp_last_game"] = pd.to_numeric(opp_games.iloc[-1].get("actual"), errors="coerce")
        
        # Home/away split
        if "is_home" in opp_games.columns:
            home = opp_games[opp_games["is_home"] == 1]
            away = opp_games[opp_games["is_home"] == 0]
            if len(home) > 0:
                result["opp_home_avg"] = pd.to_numeric(home["actual"], errors="coerce").mean()
            if len(away) > 0:
                result["opp_away_avg"] = pd.to_numeric(away["actual"], errors="coerce").mean()
        
        # Trend analysis
        if len(opp_games) >= 3:
            last_3 = opp_games.tail(3)["actual"].astype(float).mean()
            earlier = opp_games[:-3]["actual"].astype(float).mean() if len(opp_games) > 3 else last_3
            
            if last_3 > earlier * 1.05:
                result["opp_trend"] = "up"
            elif last_3 < earlier * 0.95:
                result["opp_trend"] = "down"
            else:
                result["opp_trend"] = "stable"
        
        return result
    
    def compute_confidence_score(
        self,
        result: str,
        edge: float,
        tier: str,
        opp_analysis: Dict,
        player_type: str = "skater",
        sample_size: int = 0
    ) -> float:
        """
        Compute confidence score (0-100) for pick strength.
        
        Weighted by:
          - Hit/Miss result
          - Edge magnitude
          - Opponent history
          - Sample size
          - Player type
        """
        if result == "VOID":
            return 0
        
        weights = CONFIDENCE_WEIGHTS.get(player_type, CONFIDENCE_WEIGHTS["skater"])
        tier_mult = {"A": 1.0, "B": 0.75, "C": 0.50, "D": 0.25}.get(tier, 0.25)
        
        # Hit rate component (from result)
        hit_component = 50 if result == "HIT" else 30
        
        # Edge component
        edge_component = np.clip(abs(edge) * 5, 0, 15)
        
        # Opponent history component
        opp_component = 0
        if opp_analysis.get("opp_games", 0) > 0:
            opp_consistency = 1.0 / (1.0 + opp_analysis.get("opp_games", 5) * 0.1)
            opp_component = opp_consistency * 10
        
        # Sample size component (goalie-specific)
        sample_component = 0
        if player_type == "goalie" and sample_size > 0:
            sample_component = min(sample_size / 10, 10)  # Max 10 points
        
        # Combine with weights
        confidence = (
            hit_component * weights.get("hit_rate", 0.35) * tier_mult +
            edge_component * weights.get("edge", 0.25) +
            opp_component * weights.get("opp_history", 0.20) +
            sample_component * weights.get("sample_size", 0.20)
        )
        
        confidence = np.clip(confidence, 0, 100)
        return confidence


def _resolve_direction_from_slate_row(slate_row: pd.Series) -> str:
    """
    Canonical direction fallback for NHL grading:
      final_bet_direction -> bet_direction -> direction -> recommended_side -> OVER
    """
    v = first_over_under_in_slate_row(
        slate_row,
        ("final_bet_direction", "bet_direction", "direction", "recommended_side", "Direction"),
    )
    return v if v else "OVER"


def nhl_signed_margin(actual, line, direction: str) -> float:
    """
    Favorable margin is positive (same convention as scripts/grading/slate_grader.grade).
    OVER: actual - line; UNDER: line - actual. NaN when actual/line missing.
    """
    act = pd.to_numeric(actual, errors="coerce")
    ln = pd.to_numeric(line, errors="coerce")
    if pd.isna(act) or pd.isna(ln):
        return np.nan
    actual_f = float(act)
    line_f = float(ln)
    if abs(actual_f - line_f) < 0.01:
        return 0.0
    d = str(direction or "OVER").upper().strip()
    if d == "OVER":
        return round(actual_f - line_f, 2)
    return round(line_f - actual_f, 2)


# ── ADVANCED ANALYTICS ────────────────────────────────────────────────────────

class NHLAnalytics:
    """Generate actionable recommendations for pick strengthening."""
    
    @staticmethod
    def identify_consistent_hitters(graded_df: pd.DataFrame, min_games: int = 3) -> pd.DataFrame:
        """Find props that consistently hit for specific players vs opponents."""
        
        consistent = []
        
        # Group by player + opponent + prop type
        if graded_df.empty: return pd.DataFrame()
        for (player, opp, prop), group in graded_df.groupby(["player", "opponent", "prop_type"]):
            valid = group[group["result"].isin(["HIT", "MISS"])]
            if len(valid) >= min_games:
                hit_rate = (valid["result"] == "HIT").sum() / len(valid)
                if hit_rate >= 0.66:  # 2/3 or better
                    consistent.append({
                        "player": player,
                        "opponent": opp,
                        "prop_type": prop,
                        "hit_rate": hit_rate,
                        "games": len(valid),
                        "avg_edge": group["edge"].mean(),
                        "recommendation": "STRONG BUY" if hit_rate >= 0.75 else "BUY",
                    })
        
        if not consistent:
            return pd.DataFrame(
                columns=[
                    "player",
                    "opponent",
                    "prop_type",
                    "hit_rate",
                    "games",
                    "avg_edge",
                    "recommendation",
                ]
            )
        return pd.DataFrame(consistent).sort_values("hit_rate", ascending=False)
    
    @staticmethod
    def analyze_line_movement(
        slate_df: pd.DataFrame,
        graded_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Analyze impact of line movement on outcomes."""
        
        # Merge graded with original slate
        merged = graded_df.merge(
            slate_df[["player", "team", "prop_type", "line", "tier"]],
            on=["player", "team", "prop_type"],
            how="left"
        )
        
        # Calculate deviation
        merged["line_deviation"] = (merged["actual"] - merged["line"]) / merged["line"]
        
        # Analyze by tier
        analysis = merged.groupby("tier").agg({
            "result": lambda x: (x == "HIT").sum() / len(x[x.isin(["HIT", "MISS"])]),
            "line_deviation": "mean",
            "edge": "mean"
        }).round(3)
        
        return analysis
    
    @staticmethod
    def generate_recommendations(
        graded_df: pd.DataFrame,
        consistent_hitters: pd.DataFrame,
        min_confidence: float = 60
    ) -> pd.DataFrame:
        """Generate pick strengthening recommendations."""
        
        recommendations = []
        
        # High-confidence hits (strengthen these picks)
        strong_hits = graded_df[
            (graded_df["result"] == "HIT") &
            (graded_df["confidence_score"] >= min_confidence)
        ]
        
        for _, row in strong_hits.iterrows():
            recommendations.append({
                "type": "STRENGTHEN_HIT",
                "player": row["player"],
                "prop": row["prop_type"],
                "reason": f"High confidence ({row['confidence_score']:.0f}) vs {row['opponent']}",
                "action": "Increase stake on similar matchups",
                "confidence": row["confidence_score"]
            })
        
        # Low-confidence misses (avoid these)
        weak_misses = graded_df[
            (graded_df["result"] == "MISS") &
            (graded_df["confidence_score"] < 40)
        ]
        
        for _, row in weak_misses.iterrows():
            recommendations.append({
                "type": "AVOID",
                "player": row["player"],
                "prop": row["prop_type"],
                "reason": f"Low confidence ({row['confidence_score']:.0f}) vs {row['opponent']}",
                "action": "Skip this player-opponent-prop combo",
                "confidence": 100 - row["confidence_score"]
            })
        
        # Consistent hitters recommendation
        if len(consistent_hitters) > 0:
            top_consistent = consistent_hitters.head(10)
            for _, row in top_consistent.iterrows():
                recommendations.append({
                    "type": "CONSISTENT_PATTERN",
                    "player": row["player"],
                    "prop": row["prop_type"],
                    "reason": f"{row['hit_rate']:.0%} hit rate vs {row['opponent']} ({row['games']} games)",
                    "action": f"Prioritize this combo ({row['recommendation']})",
                    "confidence": row["hit_rate"] * 100
                })
        
        if not recommendations:
            return pd.DataFrame(
                columns=["type", "player", "prop", "reason", "action", "confidence"]
            )
        return pd.DataFrame(recommendations).sort_values("confidence", ascending=False)


# ── MAIN GRADER PIPELINE ──────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="PropOracle Advanced NHL Grader with Opponent Analysis",
    )
    ap.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    ap.add_argument("--actuals", required=True, help="Actuals CSV")
    ap.add_argument("--slate", required=True, help="Slate XLSX")
    ap.add_argument("--opp-cache", default=None, help="Opponent stats cache")
    ap.add_argument("--output-dir", default=".", help="Output directory")
    args = ap.parse_args()
    
    print(f"""
    ╔════════════════════════════════════════════════════════════════╗
    ║              PropOracle Advanced NHL Grader                       ║
    ║           with Opponent Analysis & Recommendations            ║
    ║                                                                ║
    ║  Date: {args.date}                                               ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    # ── LOAD DATA ─────────────────────────────────────────────────────────
    print("[NHL Grader] Loading data...")
    
    try:
        actuals = pd.read_csv(args.actuals, encoding="utf-8")
        slate = pd.read_excel(args.slate)
    except Exception as e:
        print(f"❌ Failed to load data: {e}")
        sys.exit(1)
    
    # Load optional opponent cache
    opp_cache = None
    if args.opp_cache and Path(args.opp_cache).exists():
        try:
            opp_cache = pd.read_csv(args.opp_cache, encoding="utf-8")
            print(f"  Opponent cache: {len(opp_cache)} rows")
        except:
            print("⚠️  Could not load opponent cache")
    
    print(f"  Actuals: {len(actuals)} rows")
    print(f"  Slate: {len(slate)} rows")

    actuals_lut = _build_nhl_actuals_lookup(actuals)
    print(f"  Actuals lookup keys: {len(actuals_lut)}")
    
    # ── GRADE PROPS ───────────────────────────────────────────────────────
    print("[NHL Grader] Grading props...")
    
    grader = NHLGrader(opp_cache=opp_cache)
    graded = []
    
    for _, slate_row in slate.iterrows():
        player = slate_row.get("player", "")
        team = slate_row.get("team", "")
        opp_team = slate_row.get("opponent", slate_row.get("opp_team", slate_row.get("opp", "")))
        prop_type = slate_row.get("prop_type", "")
        line = first_numeric_in_slate_row(
            slate_row, ("line", "Line", "line_score", "LINE")
        )
        direction = _resolve_direction_from_slate_row(slate_row)
        tier = slate_row.get("tier", "D")
        
        # Determine player type
        player_type = "goalie" if any(
            x in str(prop_type).lower() for x in ["saves", "ga", "shutout"]
        ) else "skater"
        
        # Find actual — slate uses snake_case props; actuals CSV uses Title Case labels
        lk = (
            fold_player_name(player),
            str(team).strip().upper(),
            _normalize_nhl_prop_key(prop_type),
        )
        actual_pack = actuals_lut.get(lk, (np.nan, "", 0))
        actual = actual_pack[0]
        actual_source = actual_pack[1]
        actual_source_conflict = int(actual_pack[2])
        
        # Grade
        result, edge, pct_of_line = grader.grade_prop(actual, line, direction, player_type)
        void_reason = "NO_DATA" if result == "VOID" else ""
        margin = nhl_signed_margin(actual, line, direction)

        # Opponent analysis
        opp_analysis = grader.get_opponent_analysis(player, opp_team, prop_type)
        
        # Confidence score
        confidence = grader.compute_confidence_score(
            result, edge if not pd.isna(edge) else 0, tier,
            opp_analysis, player_type, sample_size=opp_analysis.get("opp_games", 0)
        )
        
        graded.append({
            "player": player,
            "team": team,
            "opponent": opp_team,
            "prop_type": prop_type,
            "line": line,
            "actual": actual,
            "actual_source": actual_source,
            "actual_source_conflict": actual_source_conflict,
            "margin": margin,
            "direction": direction,
            "bet_direction": direction,
            "tier": tier,
            "player_type": player_type,
            "result": result,
            "reason": void_reason,
            "edge": edge,
            "ml_prob": _slate_ml_prob_nhl(slate_row),
            "pct_of_line": pct_of_line,
            "confidence_score": confidence,
            "opp_avg": opp_analysis["opp_avg"],
            "opp_games": opp_analysis["opp_games"],
            "opp_trend": opp_analysis["opp_trend"],
        })
    
    graded_df = pd.DataFrame(graded)
    print(f"  Graded: {len(graded_df)} props")
    if graded_df.empty:
        print("  No props graded -- skipping analytics")
        xlsx_path = output_dir / f"graded_nhl_{args.date}.xlsx"
        pd.DataFrame().to_excel(xlsx_path, sheet_name="GRADED", index=False)
        print(f"  Saved empty graded file -> {xlsx_path}")
        exit(0)
    
    # ── ADVANCED ANALYTICS ────────────────────────────────────────────────
    print("[NHL Grader] Running advanced analytics...")
    
    consistent_hitters = NHLAnalytics.identify_consistent_hitters(graded_df, min_games=2)
    recommendations = NHLAnalytics.generate_recommendations(graded_df, consistent_hitters)
    
    print(f"  Consistent hitters: {len(consistent_hitters)}")
    print(f"  Recommendations: {len(recommendations)}")
    
    # ── OUTPUT ────────────────────────────────────────────────────────────
    print("[NHL Grader] Saving results...")
    
    # Graded Excel
    xlsx_path = output_dir / f"graded_nhl_{args.date}.xlsx"
    graded_df.to_excel(xlsx_path, sheet_name="GRADED", index=False)
    print(f"✅ {xlsx_path}")
    
    # Opponent analysis
    opp_analysis_path = output_dir / f"nhl_opponent_analysis_{args.date}.csv"
    consistent_hitters.to_csv(opp_analysis_path, index=False, encoding="utf-8-sig")
    print(f"✅ {opp_analysis_path}")
    
    # Recommendations
    rec_path = output_dir / f"nhl_pick_recommendations_{args.date}.csv"
    recommendations.to_csv(rec_path, index=False, encoding="utf-8-sig")
    print(f"✅ {rec_path}")
    
    print(f"\n[NHL Grader] ✅ Complete")


if __name__ == "__main__":
    main()


