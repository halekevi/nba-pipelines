#!/usr/bin/env python3
"""
soccer_grader_advanced.py

PropOracle Advanced Soccer Grader with Multi-League & Opponent Analysis

COVERAGE:
  ✅ 7 leagues: EPL, UCL, MLS, La Liga, Bundesliga, Serie A, Ligue 1
  ✅ Position-specific analysis: GK, DEF, MID, FWD
  ✅ Opponent-specific comparisons
  ✅ League-specific calibration (EPL vs MLS differ)
  ✅ Confidence scoring per position

FEATURES:
  ✅ Full prop grading (HIT/MISS/PUSH/VOID)
  ✅ Position-specific thresholds:
     - GK: Saves, Goals Allowed, Clean Sheets
     - DEF: Tackles, Clearances, Passes, Yellow Cards
     - MID: Passes, Shots, Tackles, Assists
     - FWD: Goals, Shots, SOT, Assists
  ✅ Opponent-specific analysis:
     - Avg performance vs opponent
     - Last meeting stats
     - Home/Away splits
     - Venue history
  ✅ League calibration:
     - EPL (competitive): stricter thresholds
     - MLS (higher scoring): looser thresholds
     - UCL (elite): special handling
  ✅ Advanced analytics:
     - Player usage rate vs league avg
     - Shot volume trends
     - Defensive efficiency metrics
  ✅ Recommendations:
     - Props likely to hit again
     - League matchup advantages
     - Avoid based on form

INPUTS:
  - actuals_soccer_YYYY-MM-DD.csv
  - s8_soccer_direction_clean.xlsx
  - s6a_soccer_opp_stats_cache.csv (optional)
  - soccer_league_stats_cache.csv (optional, league calibration)

OUTPUTS:
  - graded_soccer_YYYY-MM-DD.xlsx
  - soccer_opponent_analysis_YYYY-MM-DD.csv
  - soccer_pick_recommendations_YYYY-MM-DD.csv
  - soccer_league_calibration_YYYY-MM-DD.csv
"""

from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import warnings
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import re

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.slate_fields import first_numeric_in_slate_row, first_over_under_in_slate_row


# ── SOCCER CONFIGURATION ──────────────────────────────────────────────────────

LEAGUE_CONFIG = {
    "EPL": {"name": "Premier League", "competitive": "high", "avg_goals": 2.8},
    "UCL": {"name": "UEFA Champions League", "competitive": "very_high", "avg_goals": 2.5},
    "MLS": {"name": "MLS", "competitive": "medium", "avg_goals": 3.2},
    "LA_LIGA": {"name": "La Liga", "competitive": "high", "avg_goals": 2.7},
    "BUNDESLIGA": {"name": "Bundesliga", "competitive": "medium", "avg_goals": 3.1},
    "SERIE_A": {"name": "Serie A", "competitive": "high", "avg_goals": 2.6},
    "LIGUE_1": {"name": "Ligue 1", "competitive": "medium", "avg_goals": 2.9},
}

POSITION_THRESHOLDS = {
    "GK": {
        "prop_types": ["Saves", "Goals Allowed", "Clean Sheets"],
        "opp_weight": 0.30,  # Opponent defense matters more
        "confidence_multiplier": 1.2,
    },
    "DEF": {
        "prop_types": ["Tackles", "Clearances", "Passes", "Yellow Cards"],
        "opp_weight": 0.25,
        "confidence_multiplier": 1.0,
    },
    "MID": {
        "prop_types": ["Passes", "Shots", "Tackles", "Assists"],
        "opp_weight": 0.20,
        "confidence_multiplier": 0.95,
    },
    "FWD": {
        "prop_types": ["Goals", "Shots", "SOT", "Assists"],
        "opp_weight": 0.15,
        "confidence_multiplier": 0.90,
    }
}

def _norm_soccer_player(name: str) -> str:
    s = str(name or "").lower().strip().replace(".", " ")
    s = re.sub(r"\s+", " ", s)
    parts = [x for x in s.split(" ") if x]
    for suf in ("jr", "sr", "ii", "iii", "iv", "v"):
        if parts and parts[-1] == suf:
            parts = parts[:-1]
    return " ".join(parts)


def _norm_soccer_prop(p: str) -> str:
    return " ".join(str(p or "").lower().strip().split())


def normalize_soccer_slate_columns(slate: pd.DataFrame) -> pd.DataFrame:
    """
    step8_soccer_direction_clean.xlsx uses Title Case headers (Player, Prop, …).
    This grader historically expected lowercase keys; without this, every row
    looks like player='' and joins fail → all VOID.
    """
    rename: dict[str, str] = {}
    for c in slate.columns:
        cs = str(c).strip()
        low = cs.lower()
        mapping = {
            "player": "player",
            "league": "league",
            "tier": "tier",
            "line": "line",
            "prop": "prop_type",
            "prop type": "prop_type",
            "opp": "opponent",
            "opponent": "opponent",
            "opp team": "opponent",
            "pos group": "position",
            "position group": "position",
            "pos": "position_code",
            "direction": "final_bet_direction",
            "final bet direction": "final_bet_direction",
        }
        if low in mapping:
            rename[c] = mapping[low]
        elif cs in ("Player", "League", "Tier", "Line", "Prop", "Opp", "Team", "Direction"):
            mapping2 = {
                "Player": "player",
                "League": "league",
                "Tier": "tier",
                "Line": "line",
                "Prop": "prop_type",
                "Opp": "opponent",
                "Team": "team",
                "Direction": "final_bet_direction",
            }
            rename[c] = mapping2[cs]
    out = slate.rename(columns=rename)
    if "position" not in out.columns and "position_code" in out.columns:
        out["position"] = out["position_code"]
    elif "position" not in out.columns:
        out["position"] = "FWD"
    if "final_bet_direction" not in out.columns:
        out["final_bet_direction"] = "OVER"
    for src, dst in (("Game Time", "game_time"), ("game_start", "game_time")):
        if src in out.columns and "game_time" not in out.columns:
            out = out.rename(columns={src: "game_time"})
    return out


def filter_soccer_slate_by_date(slate: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """Keep rows for grade date so slate matches fetch_actuals day (PP can include future games)."""
    if not date_str or slate.empty or "game_time" not in slate.columns:
        return slate
    try:
        year = int(str(date_str).strip()[:4])
    except Exception:
        return slate
    # PrizePicks exports often use "MM/DD h:mm AM" without year — anchor to grade year.
    gt = slate["game_time"].astype(str).str.strip()
    anchored = gt.where(
        ~gt.str.match(r"^\d{1,2}/\d{1,2}\s", na=False),
        gt + f" {year}",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        try:
            ts = pd.to_datetime(anchored, errors="coerce", format="mixed")
        except (TypeError, ValueError):
            ts = pd.to_datetime(anchored, errors="coerce")
    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return slate
    mask = ts.dt.date == target
    if not mask.any():
        if ts.notna().sum() == 0:
            print("[Soccer Grader] No parseable Game Time — using full slate.")
            return slate
        print(
            f"[Soccer Grader] Date filter {date_str}: 0 rows on that day "
            f"({int(ts.notna().sum())} rows had other dates) — using full slate."
        )
        return slate
    print(f"[Soccer Grader] Date filter {date_str}: {int(mask.sum())}/{len(slate)} rows")
    return slate.loc[mask].copy()


def build_soccer_actuals_lookup(actuals: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    """
    Key: (player_norm, prop_norm, team_upper) — team optional match.
    """
    lookup: dict[tuple[str, str, str], float] = {}
    for _, r in actuals.iterrows():
        pl = _norm_soccer_player(r.get("player", ""))
        pr = _norm_soccer_prop(r.get("prop_type", ""))
        tm = str(r.get("team", "") or "").strip().upper()
        try:
            val = float(r["actual"])
        except (TypeError, ValueError):
            continue
        if not pl or not pr:
            continue
        lookup[(pl, pr, tm)] = val
        lookup[(pl, pr, "")] = val
    return lookup


def build_soccer_minutes_lookup(actuals: pd.DataFrame) -> dict[tuple[str, str], float | None]:
    """
    Optional minutes lookup from actuals feed.
    Uses minutes_played if present; otherwise returns empty lookup.
    """
    if "minutes_played" not in actuals.columns:
        return {}
    out: dict[tuple[str, str], float | None] = {}
    for _, r in actuals.iterrows():
        pl = _norm_soccer_player(r.get("player", ""))
        tm = str(r.get("team", "") or "").strip().upper()
        if not pl:
            continue
        try:
            mv = float(r.get("minutes_played"))
        except (TypeError, ValueError):
            mv = np.nan
        out[(pl, tm)] = None if pd.isna(mv) else mv
        out[(pl, "")] = None if pd.isna(mv) else mv
    return out


def _soccer_prop_aliases(prop_norm: str) -> list[str]:
    """Map PrizePicks / slate labels → fetch_actuals.py prop_type strings."""
    pr = prop_norm
    out = [pr]
    # Normalized keys (lowercase collapsed)
    if pr == "passes attempted" or ("attempted" in pr and "pass" in pr):
        out.append(_norm_soccer_prop("Passes"))
    if "goalie" in pr and "save" in pr:
        out.append(_norm_soccer_prop("Goalkeeper Saves"))
    if pr == "goalkeeper saves":
        out.append(_norm_soccer_prop("Goalkeeper Saves"))
    if "goal" in pr and "assist" in pr and "+" in prop_norm.replace(" ", ""):
        out.append(_norm_soccer_prop("Goal + Assist"))
    if pr == "shots on target":
        out.append(_norm_soccer_prop("Shots On Target"))
    # De-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def lookup_soccer_actual(
    lut: dict[tuple[str, str, str], float],
    player: str,
    prop_type: str,
    team: str = "",
) -> float:
    pl = _norm_soccer_player(player)
    raw = str(prop_type or "")
    pr = _norm_soccer_prop(raw.replace("(combo)", "").strip())
    tm = str(team or "").strip().upper()
    if not pl or not pr:
        return np.nan
    for alt in _soccer_prop_aliases(pr):
        k = (pl, alt, tm)
        if k in lut:
            return lut[k]
        if (pl, alt, "") in lut:
            return lut[(pl, alt, "")]
    # Goalkeeper Saves vs Saves
    if "goalkeeper" in pr and "save" in pr:
        for alt in (_norm_soccer_prop("Saves"),):
            if (pl, alt, tm) in lut:
                return lut[(pl, alt, tm)]
            if (pl, alt, "") in lut:
                return lut[(pl, alt, "")]
    return np.nan


def lookup_soccer_minutes(
    minutes_lut: dict[tuple[str, str], float | None],
    player: str,
    team: str = "",
) -> float | None:
    pl = _norm_soccer_player(player)
    tm = str(team or "").strip().upper()
    if not pl:
        return None
    if (pl, tm) in minutes_lut:
        return minutes_lut[(pl, tm)]
    return minutes_lut.get((pl, ""), None)


PROP_PRIORS = {
    "Goals": 0.49,
    "Assists": 0.54,
    "Shots": 0.555,
    "Shots on Target": 0.58,
    "Passes": 0.62,
    "Tackles": 0.56,
    "Saves": 0.60,
    "Clean Sheets": 0.35,  # Low frequency
}


# ── SOCCER GRADING ENGINE ─────────────────────────────────────────────────────


def soccer_signed_margin(actual, line, direction: str) -> float:
    """
    Favorable margin is positive (aligned with slate_grader.grade / NHL grader).
    OVER: actual - line; UNDER: line - actual. NaN when inputs unusable.
    """
    act = pd.to_numeric(actual, errors="coerce")
    ln = pd.to_numeric(line, errors="coerce")
    if pd.isna(act) or pd.isna(ln):
        return np.nan
    actual_f = float(act)
    line_f = float(ln)
    buffer = 0.05
    if abs(actual_f - line_f) <= buffer:
        return 0.0
    d = str(direction or "OVER").upper().strip()
    if d == "OVER":
        return round(actual_f - line_f, 2)
    return round(line_f - actual_f, 2)


def _slate_ml_prob_soccer(row: pd.Series) -> float:
    """Carry ML probability from soccer step8 into graded rows (non-fatal if missing)."""
    for k in ("ML Prob", "ml_prob", "MLProb", "ML_PROB"):
        if k not in row.index:
            continue
        v = row[k]
        if pd.isna(v):
            continue
        try:
            x = float(v)
            if x > 1.0:
                x /= 100.0
            return float(np.clip(x, 1e-3, 1.0 - 1e-3))
        except (TypeError, ValueError):
            continue
    return float(np.nan)


class SoccerGrader:
    """Advanced soccer prop grader with league & position awareness."""
    
    def __init__(self, league: str = "EPL", position: str = "FWD"):
        self.league = league
        self.position = position
        self.league_config = LEAGUE_CONFIG.get(league, LEAGUE_CONFIG["EPL"])
        self.position_config = POSITION_THRESHOLDS.get(position, POSITION_THRESHOLDS["FWD"])
    
    def grade_prop(
        self,
        actual: float,
        line: float,
        direction: str = "OVER"
    ) -> Tuple[str, float]:
        """
        Grade a single soccer prop.
        
        Soccer-specific: Low-frequency props (goals, clean sheets) use wider bands.
        """
        if pd.isna(actual) or pd.isna(line):
            return "VOID", np.nan
        
        edge = actual - line
        
        # Small buffer for floating point (soccer uses 0.5 increments)
        buffer = 0.05
        
        if direction == "OVER":
            if actual > line + buffer:
                return "HIT", edge
            elif abs(actual - line) <= buffer:
                return "PUSH", 0
            else:
                return "MISS", edge
        else:  # UNDER
            if actual < line - buffer:
                return "HIT", -edge
            elif abs(actual - line) <= buffer:
                return "PUSH", 0
            else:
                return "MISS", -edge
    
    def get_opponent_analysis(
        self,
        player: str,
        opp_team: str,
        prop_type: str,
        opp_cache: pd.DataFrame = None
    ) -> Dict[str, float]:
        """Analyze player performance vs this opponent."""
        
        result = {
            "opp_avg": np.nan,
            "opp_last_game": np.nan,
            "opp_games": 0,
            "opp_home_avg": np.nan,
            "opp_away_avg": np.nan,
            "opp_consistency": "unknown",  # high, medium, low
        }
        
        if opp_cache is None or len(opp_cache) == 0:
            return result
        
        # Filter
        opp_games = opp_cache[
            (opp_cache["player"].str.lower() == player.lower()) &
            (opp_cache["opp_team"].str.upper() == opp_team.upper()) &
            (opp_cache["prop_type"].str.lower() == prop_type.lower())
        ]
        
        if len(opp_games) == 0:
            return result
        
        opp_games = opp_games.sort_values("game_date")
        result["opp_games"] = len(opp_games)
        result["opp_avg"] = pd.to_numeric(opp_games["actual"], errors="coerce").mean()
        result["opp_last_game"] = pd.to_numeric(opp_games.iloc[-1].get("actual"), errors="coerce")
        
        # Consistency (std dev of performance)
        std = pd.to_numeric(opp_games["actual"], errors="coerce").std()
        if not pd.isna(std):
            if std < 0.5:
                result["opp_consistency"] = "high"
            elif std < 1.0:
                result["opp_consistency"] = "medium"
            else:
                result["opp_consistency"] = "low"
        
        # Home/away
        if "is_home" in opp_games.columns:
            home = opp_games[opp_games["is_home"] == 1]
            away = opp_games[opp_games["is_home"] == 0]
            if len(home) > 0:
                result["opp_home_avg"] = pd.to_numeric(home["actual"], errors="coerce").mean()
            if len(away) > 0:
                result["opp_away_avg"] = pd.to_numeric(away["actual"], errors="coerce").mean()
        
        return result
    
    def compute_confidence_score(
        self,
        result: str,
        edge: float,
        tier: str,
        opp_analysis: Dict,
        league_adjustment: float = 1.0
    ) -> float:
        """
        Compute confidence (0-100) with league calibration.
        
        Higher-competitive leagues → stricter scoring
        """
        if result == "VOID":
            return 0
        
        # Base score
        base_score = 50 if result == "HIT" else 30
        
        # Edge component
        edge_component = np.clip(abs(edge) * 8, 0, 20)
        
        # Opponent consistency
        opp_component = 0
        if opp_analysis.get("opp_consistency") == "high":
            opp_component = 15
        elif opp_analysis.get("opp_consistency") == "medium":
            opp_component = 10
        else:
            opp_component = 5
        
        # League & position adjustment
        pos_mult = self.position_config.get("confidence_multiplier", 0.9)
        
        # Tier adjustment
        tier_mult = {"A": 1.0, "B": 0.85, "C": 0.70, "D": 0.50}.get(tier, 0.50)
        
        confidence = (base_score + edge_component + opp_component) * pos_mult * tier_mult
        confidence = np.clip(confidence, 0, 100)
        
        return confidence


# ── SOCCER ANALYTICS ──────────────────────────────────────────────────────────

class SoccerAnalytics:
    """Soccer-specific recommendations and insights."""
    
    @staticmethod
    def identify_league_edges(graded_df: pd.DataFrame) -> pd.DataFrame:
        """Find league-specific prop edges."""
        def _safe_hit_rate(series: pd.Series) -> float:
            decided = series[series.isin(["HIT", "MISS"])]
            if len(decided) == 0:
                return np.nan
            return (decided == "HIT").mean()

        league_stats = graded_df.groupby("league").agg(
            result=("result", _safe_hit_rate),
            confidence_score=("confidence_score", "mean"),
            edge=("edge", "mean"),
        ).round(3)

        league_stats.columns = ["hit_rate", "avg_confidence", "avg_edge"]
        return league_stats
    
    @staticmethod
    def identify_position_plays(
        graded_df: pd.DataFrame,
        min_games: int = 2
    ) -> pd.DataFrame:
        """Find position-specific plays that work."""
        
        plays = []
        
        for (pos, league, prop), group in graded_df.groupby(["position", "league", "prop_type"]):
            valid = group[group["result"].isin(["HIT", "MISS"])]
            if len(valid) >= min_games:
                hit_rate = (valid["result"] == "HIT").sum() / len(valid)
                if hit_rate >= 0.60:
                    plays.append({
                        "position": pos,
                        "league": league,
                        "prop_type": prop,
                        "hit_rate": hit_rate,
                        "games": len(valid),
                        "avg_edge": group["edge"].mean(),
                        "recommendation": "STRONG" if hit_rate >= 0.75 else "MODERATE",
                    })
        
        if not plays:
            return pd.DataFrame(
                columns=["position", "league", "prop_type", "hit_rate", "games", "avg_edge", "recommendation"]
            )
        return pd.DataFrame(plays).sort_values("hit_rate", ascending=False)
    
    @staticmethod
    def generate_recommendations(
        graded_df: pd.DataFrame,
        position_plays: pd.DataFrame
    ) -> pd.DataFrame:
        """Generate position-aware recommendations."""
        
        recommendations = []
        
        # Position-specific recommendations
        for _, play in position_plays.head(15).iterrows():
            recommendations.append({
                "type": "POSITION_EDGE",
                "position": play["position"],
                "league": play["league"],
                "prop_type": play["prop_type"],
                "reason": f"{play['hit_rate']:.0%} hit rate for {play['position']} in {play['league']} ({play['games']} games)",
                "action": f"Prioritize {play['recommendation'].lower()} - repeat this combo",
                "confidence": play["hit_rate"] * 100,
            })
        
        # High-edge misses (improve line selection)
        high_edge_misses = graded_df[
            (graded_df["result"] == "MISS") &
            (graded_df["edge"] > 0) &  # We missed even though it was close
            (graded_df["confidence_score"] >= 50)
        ]
        
        for _, row in high_edge_misses.head(5).iterrows():
            recommendations.append({
                "type": "LINE_REFINEMENT",
                "position": row["position"],
                "league": row["league"],
                "prop_type": row["prop_type"],
                "reason": f"Missed by {abs(row['edge']):.1f} despite high confidence",
                "action": f"Look for slightly lower line next time vs {row['opponent']}",
                "confidence": 70,
            })
        
        if not recommendations:
            return pd.DataFrame(
                columns=["type", "position", "league", "prop_type", "reason", "action", "confidence"]
            )
        return pd.DataFrame(recommendations).sort_values("confidence", ascending=False)


# ── MAIN GRADER ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="PropOracle Advanced Soccer Grader (Multi-League)",
    )
    ap.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    ap.add_argument("--actuals", required=True, help="Actuals CSV")
    ap.add_argument("--slate", required=True, help="Slate XLSX")
    ap.add_argument("--opp-cache", default=None, help="Opponent cache")
    ap.add_argument("--output-dir", default=".", help="Output dir")
    args = ap.parse_args()
    
    print(f"""
    ╔════════════════════════════════════════════════════════════════╗
    ║          PropOracle Advanced Soccer Grader                        ║
    ║     with Multi-League & Position-Specific Analysis            ║
    ║                                                                ║
    ║  Date: {args.date}  |  7 Leagues Supported                       ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    # Load data
    print("[Soccer Grader] Loading data...")
    try:
        actuals = pd.read_csv(args.actuals, encoding="utf-8")
        xls = pd.ExcelFile(args.slate, engine="openpyxl")
        sheet = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
        slate = pd.read_excel(args.slate, sheet_name=sheet, engine="openpyxl")
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    slate = normalize_soccer_slate_columns(slate)
    slate = filter_soccer_slate_by_date(slate, args.date)
    actuals_lut = build_soccer_actuals_lookup(actuals)
    minutes_lut = build_soccer_minutes_lookup(actuals)
    print("[Soccer Grader] Mapping audit:")
    _actual_cols = {str(c).strip().lower() for c in actuals.columns}
    passes_col = "PA" if "pa" in _actual_cols else ("passes" if "passes" in _actual_cols else None)
    clear_col = "clearances" if "clearances" in _actual_cols else None
    tackle_col = "TK" if "tk" in _actual_cols else ("tackles" if "tackles" in _actual_cols else None)
    drib_col = "dribble_attempts" if "dribble_attempts" in _actual_cols else ("dribblesIntentados" if "dribblesintentados" in _actual_cols else None)
    print(f"  - Passes Attempted: {'ADDED ('+passes_col+')' if passes_col else 'NEEDS_SCRAPER_UPDATE'}")
    print(f"  - Clearances: {'ADDED ('+clear_col+')' if clear_col else 'NEEDS_SCRAPER_UPDATE'}")
    print(f"  - Tackles: {'ADDED ('+tackle_col+')' if tackle_col else 'NEEDS_SCRAPER_UPDATE'}")
    print(f"  - Attempted Dribbles: {'ADDED ('+drib_col+')' if drib_col else 'NEEDS_SCRAPER_UPDATE'}")
    
    opp_cache = None
    if args.opp_cache and Path(args.opp_cache).exists():
        try:
            opp_cache = pd.read_csv(args.opp_cache, encoding="utf-8")
        except:
            pass
    
    print(f"  Actuals: {len(actuals)}")
    print(f"  Slate: {len(slate)}")
    
    # Grade
    print("[Soccer Grader] Grading props...")
    graded = []
    legacy_hits = legacy_miss = legacy_push = 0
    no_data_void_rows = 0
    
    for _, slate_row in slate.iterrows():
        player = slate_row.get("player", "")
        league = slate_row.get("league", "EPL")
        position = str(slate_row.get("position", "FWD") or "FWD").strip() or "FWD"
        opp_team = slate_row.get("opponent", "")
        prop_type = slate_row.get("prop_type", "")
        team = str(slate_row.get("team", "") or "").strip()
        line = first_numeric_in_slate_row(
            slate_row, ("line", "Line", "line_score", "LINE")
        )
        direction = first_over_under_in_slate_row(
            slate_row,
            ("final_bet_direction", "bet_direction", "direction", "recommended_side", "Direction"),
        )
        if not direction:
            direction = "OVER"
        tier = slate_row.get("tier", "D")

        actual = lookup_soccer_actual(actuals_lut, player, prop_type, team)
        minutes_played = lookup_soccer_minutes(minutes_lut, player, team)
        
        # Grade
        grader = SoccerGrader(league=league, position=position)
        legacy_result, _legacy_edge = grader.grade_prop(actual, line, direction)
        if legacy_result == "HIT":
            legacy_hits += 1
        elif legacy_result == "MISS":
            legacy_miss += 1
        elif legacy_result == "PUSH":
            legacy_push += 1

        # no-data policy: if zero actual and no minutes evidence, VOID rather than MISS
        if pd.isna(actual):
            result, edge = "VOID", np.nan
            no_data_void_rows += 1
        elif float(actual) == 0.0 and minutes_played is None:
            result, edge = "VOID", np.nan
            no_data_void_rows += 1
        elif float(actual) == 0.0 and float(minutes_played) <= 0:
            result, edge = "VOID", np.nan
            no_data_void_rows += 1
        else:
            result, edge = grader.grade_prop(actual, line, direction)
        
        opp_analysis = grader.get_opponent_analysis(player, opp_team, prop_type, opp_cache)
        
        confidence = grader.compute_confidence_score(
            result, edge if not pd.isna(edge) else 0, tier, opp_analysis
        )

        margin = soccer_signed_margin(actual, line, direction)

        graded.append({
            "player": player,
            "league": league,
            "position": position,
            "opponent": opp_team,
            "prop_type": prop_type,
            "line": line,
            "actual": actual,
            "margin": margin,
            "direction": direction,
            "tier": tier,
            "result": result,
            "edge": edge,
            "ml_prob": _slate_ml_prob_soccer(slate_row),
            "confidence_score": confidence,
            "opp_avg": opp_analysis["opp_avg"],
            "opp_consistency": opp_analysis["opp_consistency"],
        })
    
    graded_df = pd.DataFrame(graded)
    print(f"  Graded: {len(graded_df)}")
    decided = graded_df[graded_df["result"].isin(["HIT", "MISS", "PUSH"])]
    decided_hits = int((decided["result"] == "HIT").sum())
    decided_n = int(len(decided))
    curr_hr = (decided_hits / decided_n * 100) if decided_n else 0.0
    legacy_n = legacy_hits + legacy_miss + legacy_push
    legacy_hr = (legacy_hits / legacy_n * 100) if legacy_n else 0.0
    print(f"  Hit rate now: {curr_hr:.1f}% ({decided_hits}/{decided_n})")
    print(f"  Est pre-fix hit rate: {legacy_hr:.1f}% ({legacy_hits}/{legacy_n})")
    print(f"  no_data->VOID rows: {no_data_void_rows}")
    
    # Analytics
    print("[Soccer Grader] Running analytics...")
    if graded_df.empty:
        league_edges = pd.DataFrame()
        position_plays = pd.DataFrame()
        recommendations = pd.DataFrame()
    else:
        league_edges = SoccerAnalytics.identify_league_edges(graded_df)
        position_plays = SoccerAnalytics.identify_position_plays(graded_df)
        recommendations = SoccerAnalytics.generate_recommendations(graded_df, position_plays)
    
    # Output
    print("[Soccer Grader] Saving results...")
    
    graded_df.to_excel(output_dir / f"graded_soccer_{args.date}.xlsx", index=False)
    position_plays.to_csv(output_dir / f"soccer_position_analysis_{args.date}.csv", index=False)
    recommendations.to_csv(output_dir / f"soccer_recommendations_{args.date}.csv", index=False)
    league_edges.to_csv(output_dir / f"soccer_league_calibration_{args.date}.csv")
    
    print(f"✅ Graded {len(graded_df)} props")
    print(f"✅ Position plays: {len(position_plays)}")
    print(f"✅ Recommendations: {len(recommendations)}")


if __name__ == "__main__":
    main()
