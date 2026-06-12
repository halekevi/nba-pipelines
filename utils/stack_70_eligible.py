"""
70% stack eligibility for ticket legs.

Market notes (PrizePicks):
- Goblin UNDER does not exist — Goblin is an OVER-only softer ladder.
- Demon legs are data-collection only; excluded from rating and tickets elsewhere.

Stack layers (all must pass for stack_70_eligible):
1. Valid market side (Goblin → OVER only; no Demon)
2. Historical hit rate (strat segment >= 0.70 with n>=30, or row hit_rate >= 0.70)
3. L5 directional recency (side hits >= 4 of 5)
4. Opponent / top-3 matchup alignment for direction
5. Player consistency grade in S/A/B when grade is known
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.defense_tiers import normalize_def_tier_label
from utils.prop_category import prop_to_category

# Goblin UNDER is not a real market side on PrizePicks.
GOBLIN_UNDER_INVALID_NOTE = "Goblin UNDER does not exist (Goblin is OVER-only)."

_TOP3_PATHS: dict[str, Path] = {
    "NBA": Path("Sports/NBA/data/nba_top3_vs_defense.csv"),
    "NBA1Q": Path("Sports/NBA/data/nba_top3_vs_defense.csv"),
    "NBA1H": Path("Sports/NBA/data/nba_top3_vs_defense.csv"),
    "WNBA": Path("Sports/WNBA/data/wnba_top3_vs_defense.csv"),
    "NHL": Path("Sports/NHL/data/nhl_top3_vs_defense.csv"),
    "MLB": Path("Sports/MLB/data/mlb_hitter_top3_vs_defense.csv"),
    "SOCCER": Path("Sports/Soccer/data/soccer_top3_vs_defense.csv"),
}

_STRONG_CONSISTENCY = frozenset({"S", "A", "B"})
_OVER_FAVOR_DEF = frozenset({"WEAK", "BELOW AVG", "AVG"})
_UNDER_FAVOR_DEF = frozenset({"ELITE", "ABOVE AVG"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _norm_pick_type(raw: object) -> str:
    s = str(raw or "").strip().upper()
    if "GOBLIN" in s:
        return "GOBLIN"
    if "DEMON" in s:
        return "DEMON"
    return "STANDARD"


def _norm_direction(raw: object) -> str:
    s = str(raw or "").strip().upper()
    if s in {"UNDER", "LOWER"}:
        return "UNDER"
    return "OVER"


def _norm_player_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_prop_key(s: object) -> str:
    return str(s or "").strip().lower()


def is_invalid_market_side(pick_type: object, direction: object) -> bool:
    """True when pick_type × direction is not a valid PrizePicks market side."""
    pt = _norm_pick_type(pick_type)
    dr = _norm_direction(direction)
    if pt == "GOBLIN" and dr == "UNDER":
        return True
    return False


def exclude_invalid_market_sides_from_rating(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Goblin UNDER rows from hit-rate / tier rating pools."""
    if df.empty:
        return df
    pt_col = "pick_type" if "pick_type" in df.columns else None
    dir_col = next((c for c in ("direction", "bet_direction", "final_bet_direction") if c in df.columns), None)
    if not pt_col or not dir_col:
        return df
    bad = df.apply(lambda r: is_invalid_market_side(r[pt_col], r[dir_col]), axis=1)
    return df.loc[~bad].copy()


def _def_tier_series(df: pd.DataFrame) -> pd.Series:
    for c in ("def_tier", "opponent_def_tier", "Def Tier", "DEF_TIER"):
        if c in df.columns:
            return df[c].map(normalize_def_tier_label).astype(str).str.upper()
    return pd.Series("", index=df.index, dtype=str)


def _attach_top3_context(df: pd.DataFrame, repo: Path | None = None) -> pd.DataFrame:
    """Merge team top/bottom-3 vs defense flags when sport CSV exists."""
    out = df.copy()
    for col, default in (
        ("team_top3_rank", np.nan),
        ("team_bottom3_rank", np.nan),
        ("def_boost_hist", np.nan),
        ("top3_weak_overperformer", 0),
        ("top3_elite_fader", 0),
    ):
        if col not in out.columns:
            out[col] = default
    for col in ("team_top3_rank", "team_bottom3_rank", "def_boost_hist"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ("top3_weak_overperformer", "top3_elite_fader"):
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)

    if out.empty or "sport" not in out.columns:
        return out

    root = repo or _repo_root()
    player_col = next((c for c in ("player", "Player", "player_name") if c in out.columns), None)
    prop_col = next((c for c in ("prop_type", "prop_norm", "prop", "Prop") if c in out.columns), None)
    if not player_col or not prop_col:
        return out

    sport_u = out["sport"].astype(str).str.upper().str.strip()
    for sport in sport_u.unique():
        if sport not in _TOP3_PATHS:
            continue
        sm = sport_u.eq(sport)
        if not sm.any():
            continue
        path = root / _TOP3_PATHS[sport]
        if not path.is_file():
            continue
        try:
            t3 = pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            continue
        need = {
            "PLAYER_NORM",
            "category",
            "rank_on_team",
            "def_boost",
            "overperform_vs_weak",
            "fades_vs_elite",
            "leader_side",
        }
        if not need.issubset(t3.columns):
            continue
        chunk = out.loc[sm, [player_col, prop_col]].copy()
        chunk["_player_norm"] = chunk[player_col].map(_norm_player_name)
        chunk["_prop_norm"] = chunk[prop_col].map(
            lambda p: prop_to_category(sport, p) or _norm_prop_key(p)
        )
        top_sub = (
            t3[t3["leader_side"].astype(str).str.lower().eq("top")]
            .drop_duplicates(subset=["PLAYER_NORM", "category"], keep="first")
        )
        bot_sub = (
            t3[t3["leader_side"].astype(str).str.lower().eq("bottom")]
            .drop_duplicates(subset=["PLAYER_NORM", "category"], keep="first")
        )
        merged = chunk.merge(
            top_sub[
                ["PLAYER_NORM", "category", "rank_on_team", "def_boost", "overperform_vs_weak", "fades_vs_elite"]
            ],
            left_on=["_player_norm", "_prop_norm"],
            right_on=["PLAYER_NORM", "category"],
            how="left",
        )
        merged = merged.merge(
            bot_sub[["PLAYER_NORM", "category", "rank_on_team"]].rename(
                columns={"rank_on_team": "team_bottom3_rank"}
            ),
            left_on=["_player_norm", "_prop_norm"],
            right_on=["PLAYER_NORM", "category"],
            how="left",
            suffixes=("", "_b3"),
        )
        if merged.index.duplicated().any():
            merged = merged[~merged.index.duplicated(keep="first")]
        out.loc[sm, "team_top3_rank"] = pd.to_numeric(merged.get("rank_on_team"), errors="coerce").values
        out.loc[sm, "team_bottom3_rank"] = pd.to_numeric(merged.get("team_bottom3_rank"), errors="coerce").values
        out.loc[sm, "def_boost_hist"] = pd.to_numeric(merged.get("def_boost"), errors="coerce").values
        out.loc[sm, "top3_weak_overperformer"] = merged.get("overperform_vs_weak", False).fillna(False).astype(int).values
        out.loc[sm, "top3_elite_fader"] = merged.get("fades_vs_elite", False).fillna(False).astype(int).values
    return out


def _has_matchup_context(row: pd.Series, def_tier: str) -> bool:
    if def_tier:
        return True
    if pd.notna(pd.to_numeric(row.get("team_top3_rank"), errors="coerce")):
        return True
    if pd.notna(pd.to_numeric(row.get("team_bottom3_rank"), errors="coerce")):
        return True
    if pd.notna(pd.to_numeric(row.get("def_boost_hist"), errors="coerce")):
        return True
    if int(pd.to_numeric(row.get("top3_weak_overperformer"), errors="coerce") or 0) == 1:
        return True
    if int(pd.to_numeric(row.get("top3_elite_fader"), errors="coerce") or 0) == 1:
        return True
    return False


def _matchup_aligned(row: pd.Series, direction: str, def_tier: str) -> bool:
    if not _has_matchup_context(row, def_tier):
        return True
    over = direction == "OVER"
    weak_over = int(pd.to_numeric(row.get("top3_weak_overperformer"), errors="coerce") or 0) == 1
    elite_fade = int(pd.to_numeric(row.get("top3_elite_fader"), errors="coerce") or 0) == 1
    top_rank = pd.to_numeric(row.get("team_top3_rank"), errors="coerce")
    bot_rank = pd.to_numeric(row.get("team_bottom3_rank"), errors="coerce")
    boost = pd.to_numeric(row.get("def_boost_hist"), errors="coerce")

    if over:
        if def_tier in _OVER_FAVOR_DEF:
            return True
        if weak_over:
            return True
        if pd.notna(top_rank) and top_rank <= 3:
            return True
        if pd.notna(boost) and boost > 0:
            return True
        return False

    if def_tier in _UNDER_FAVOR_DEF:
        return True
    if elite_fade:
        return True
    if pd.notna(bot_rank) and bot_rank <= 3:
        return True
    if pd.notna(boost) and boost < 0:
        return True
    return False


def stack_70_eligible_row(row: pd.Series | dict[str, Any]) -> bool:
    """Return True when a leg passes the full 70% stack."""
    if isinstance(row, dict):
        row = pd.Series(row)
    pick = _norm_pick_type(row.get("pick_type", row.get("Pick Type", "")))
    direction = _norm_direction(
        row.get("direction", row.get("bet_direction", row.get("Direction", "")))
    )
    if pick == "DEMON":
        return False
    if is_invalid_market_side(pick, direction):
        return False
    if pick == "GOBLIN" and direction != "OVER":
        return False

    hr = pd.to_numeric(row.get("hit_rate"), errors="coerce")
    strat_hr = pd.to_numeric(row.get("strat_hit_rate"), errors="coerce")
    strat_n = pd.to_numeric(row.get("strat_n"), errors="coerce")
    hr_ok = bool(
        (pd.notna(strat_hr) and pd.notna(strat_n) and strat_hr >= 0.70 and strat_n >= 30)
        or (pd.notna(hr) and hr >= 0.70)
    )
    if not hr_ok:
        return False

    l5o = pd.to_numeric(row.get("l5_over"), errors="coerce")
    l5u = pd.to_numeric(row.get("l5_under"), errors="coerce")
    side_l5 = l5u if direction == "UNDER" else l5o
    if pd.isna(side_l5) or float(side_l5) < 4:
        return False

    def_tier = normalize_def_tier_label(
        row.get("def_tier", row.get("opponent_def_tier", row.get("Def Tier", "")))
    ).upper()
    if not _matchup_aligned(row, direction, def_tier):
        return False

    cg = str(row.get("consistency_grade", row.get("consistency_grade_norm", "")) or "").strip().upper()
    if cg and cg not in _STRONG_CONSISTENCY and cg != "?":
        return False

    return True


def attach_stack_70_columns(
    df: pd.DataFrame,
    *,
    repo: Path | None = None,
    compute_eligible: bool = True,
) -> pd.DataFrame:
    """Add top-3 context columns and optional stack_70_eligible flag."""
    if df is None or df.empty:
        return df
    out = _attach_top3_context(df, repo=repo)
    pick = out.get("pick_type", out.get("Pick Type", pd.Series("", index=out.index))).map(_norm_pick_type)
    direction = out.get("direction", out.get("bet_direction", pd.Series("", index=out.index))).map(_norm_direction)
    out["invalid_market_side"] = [
        is_invalid_market_side(p, d) for p, d in zip(pick.tolist(), direction.tolist())
    ]
    if compute_eligible:
        out["stack_70_eligible"] = out.apply(stack_70_eligible_row, axis=1)
    return out


def filter_stack_70_only(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "stack_70_eligible" not in df.columns:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()
    return df[df["stack_70_eligible"].fillna(False)].copy()
