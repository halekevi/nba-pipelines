"""
Enrich slate prop rows with standardized read fields for rank/edge/probability analysis.

Used by combined_slate_tickets (dataframe) and scripts/enrich_pipeline_read_fields.py (audit/export).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.goblin_demon_multiplier import leg_delta_pct

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_PATH = REPO_ROOT / "data" / "pipeline_read_checklist.json"
SCHEMA_PATH = REPO_ROOT / "data" / "schemas" / "pipeline_read_fields.schema.json"

_PICK_RULES: dict[str, Any] | None = None
_SPORT_SPECS: dict[str, Any] | None = None


def _load_checklist() -> tuple[dict[str, Any], dict[str, Any]]:
    global _PICK_RULES, _SPORT_SPECS
    if _PICK_RULES is not None and _SPORT_SPECS is not None:
        return _PICK_RULES, _SPORT_SPECS
    data = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    _PICK_RULES = data.get("pick_type_rules") or {}
    _SPORT_SPECS = data.get("sports") or {}
    return _PICK_RULES, _SPORT_SPECS


def norm_pick_type(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    return "standard"


def norm_direction(raw: Any) -> str:
    d = str(raw or "").strip().upper()
    if d in ("UNDER", "LOWER"):
        return "UNDER"
    return "OVER"


def norm_sport(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s in ("SOC", "FOOTBALL"):
        return "SOCCER"
    return s or "UNKNOWN"


def _to_prob_0_1(v: Any) -> float | None:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if x > 1.0:
        x = x / 100.0
    return float(np.clip(x, 0.0, 1.0))


def _l5_hit_rate(raw: Any, gp: float = 5.0) -> float | None:
    """L5 columns are hit counts out of gp (same convention as combined_slate_tickets)."""
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if x > gp and x <= 100.0:
        return float(np.clip(x / 100.0, 0.0, 1.0))
    return float(np.clip(x / gp, 0.0, 1.0))


# Excel Full Slate display headers -> internal column names (mirrors combined_slate_tickets SLATE_HDRS).
_SLATE_HEADER_RENAME: dict[str, str] = {
    "Sport": "sport",
    "Tier": "tier",
    "Rank Score": "rank_score",
    "Player": "player",
    "Team": "team",
    "Opp": "opp",
    "Team Seed": "team_seed",
    "Team Region": "team_region",
    "Team AP": "team_ap_rank",
    "Opp Seed": "opp_seed",
    "Opp Region": "opp_region",
    "Opp AP": "opp_ap_rank",
    "NCAA Rank": "ncaa_rank",
    "Prop": "prop_type",
    "Pick Type": "pick_type",
    "Platform": "pick_platform",
    "Line": "line",
    "Line UD": "line_underdog",
    "Line (UD)": "line_underdog",
    "Line DK": "line_draftkings",
    "Line (DK)": "line_draftkings",
    "Best Line": "best_cross_line",
    "Best Book": "best_cross_book",
    "Edge vs PP": "cross_edge_vs_pp",
    "#Books": "cross_n_books",
    "Dir": "direction",
    "Edge": "edge",
    "Proj": "projection",
    "Hit Rate": "hit_rate",
    "Hit Rate (5g)": "hit_rate",
    "Effective Hit Rate": "effective_hit_rate",
    "Ml Prob": "ml_prob",
    "ML Prob": "ml_prob",
    "Abs Edge": "abs_edge",
    "L5 Avg": "l5_avg",
    "Szn Avg": "season_avg",
    "L5 Over": "l5_over",
    "L5 Under": "l5_under",
    "L5 Side Hits": "l5_side_hits",
    "L5 Match %": "l5_consistency",
    "L10 Over": "l10_over",
    "L10 Under": "l10_under",
    "Def Tier": "def_tier",
    "Min Tier": "min_tier",
    "Shot Role": "shot_role",
    "Usage Role": "usage_role",
    "H2H Avg": "h2h_avg",
    "H2H Over%": "h2h_over_rate",
    "H2H GP": "h2h_games",
    "H2H Last": "h2h_last",
    "B2B": "b2b_flag",
    "CV%": "cv_pct",
    "Opp vs Avg%": "opp_vs_avg_pct",
    "Game Time": "game_time",
    "Pace Tier": "pace_tier",
    "Prop Quality Score": "prop_quality_score",
    "Hit Prob Over": "hit_prob_over",
    "Hit Prob Under": "hit_prob_under",
    "Hit Prob Selected": "hit_prob_selected",
    "Hit Prob Actionable": "hit_prob_actionable",
    "Rank Read Score": "rank_read_score",
    "Data Completeness Score": "data_completeness_score",
    "Pick Type Eligible": "pick_type_eligible",
    "Standard Line": "standard_line",
    "Standard Edge": "standard_edge",
    "Standard Projection": "standard_projection",
    "Line Delta Vs Standard": "line_delta_vs_standard",
}

# Empirical L5 / line_hit_rate columns are computed against the played line in step7/8.
_AT_LINE_OVER_SOURCES = frozenset(
    {"l5_over", "over_hit_rate", "l5_over_at_line", "line_hit_rate", "line_hit_rate_over_ou_5"}
)
_AT_LINE_UNDER_SOURCES = frozenset(
    {"l5_under", "under_hit_rate", "l5_under_at_line", "line_hit_rate_under_ou_5"}
)
_STD_PROXY_OVER_SOURCES = frozenset(
    {"hit_rate", "ml_prob", "rank_score", "hit_rate_inverted", "ml_prob_inverted", "rank_score_inverted"}
)


def normalize_slate_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Excel display headers to snake_case pipeline columns."""
    if df is None or len(df) == 0:
        return df
    rename = {h: c for h, c in _SLATE_HEADER_RENAME.items() if h in df.columns}
    if rename:
        return df.rename(columns=rename)
    return df


READ_SLATE_EXPORT_KEYS = [
    "hit_prob_over",
    "hit_prob_under",
    "hit_prob_over_at_line",
    "hit_prob_over_standard_proxy",
    "hit_prob_selected",
    "hit_prob_actionable",
    "prob_over_source",
    "prob_under_source",
    "l5_side_hit_rate",
    "l10_side_hit_rate",
    "consistency_score",
    "def_matchup_signal",
    "rank_read_score",
    "rank_edge_component",
    "rank_prob_component",
    "rank_consistency_component",
    "rank_matchup_component",
    "rank_tier_component",
    "prop_quality_score",
    "data_completeness_score",
    "pick_type_eligible",
    "edge_signed",
    "edge_pct_vs_line",
    "line_delta_vs_standard",
    "effective_hit_rate",
]


def _alias_sport_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Map step8 column names to checklist field names when present."""
    out = df
    if "min_tier" in out.columns and "minutes_tier" not in out.columns:
        out = out.copy()
        out["minutes_tier"] = out["min_tier"]
    if "standard_line" not in out.columns and "std_line" in out.columns:
        out = out.copy()
        out["standard_line"] = out["std_line"]
    return out


def _series_or_nan(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index, dtype=float)


def _resolve_hit_prob_over_under(row: pd.Series) -> tuple[float | None, float | None, str, str]:
    """Return (p_over, p_under, over_source, under_source)."""
    over_src = under_src = ""
    p_over = _to_prob_0_1(row.get("over_hit_rate"))
    if p_over is not None:
        over_src = "over_hit_rate"
    p_under = _to_prob_0_1(row.get("under_hit_rate"))
    if p_under is not None:
        under_src = "under_hit_rate"

    lhr = _to_prob_0_1(row.get("line_hit_rate") or row.get("line_hit_rate_over_ou_5"))
    if p_over is None and lhr is not None:
        p_over = lhr
        over_src = "line_hit_rate"

    if p_over is None:
        p_over = _l5_hit_rate(row.get("l5_over"))
        if p_over is not None:
            over_src = "l5_over"
    if p_under is None:
        p_under = _l5_hit_rate(row.get("l5_under"))
        if p_under is not None:
            under_src = "l5_under"

    hr = _to_prob_0_1(row.get("hit_rate") or row.get("effective_hit_rate"))
    if hr is not None:
        if p_over is None:
            p_over = hr
            over_src = over_src or "hit_rate"
        if p_under is None:
            p_under = 1.0 - hr
            under_src = under_src or "hit_rate_inverted"

    mlp = _to_prob_0_1(row.get("ml_prob"))
    if mlp is not None:
        if p_over is None:
            p_over = mlp
            over_src = over_src or "ml_prob"
        if p_under is None:
            p_under = 1.0 - mlp
            under_src = under_src or "ml_prob_inverted"

    rs = pd.to_numeric(row.get("rank_score"), errors="coerce")
    if pd.notna(rs):
        sig = 1.0 / (1.0 + math.exp(-float(rs) * 0.35))
        if p_over is None:
            p_over = float(sig)
            over_src = over_src or "rank_score"
        if p_under is None:
            p_under = 1.0 - float(sig)
            under_src = under_src or "rank_score_inverted"

    if p_over is None:
        edge_raw = pd.to_numeric(row.get("edge_signed") if row.get("edge_signed") is not None else row.get("edge"), errors="coerce")
        if pd.notna(edge_raw):
            prob = 1.0 / (1.0 + math.exp(-float(edge_raw) * 0.6))
            p_over = float(np.clip(prob, 0.0, 1.0))
            over_src = over_src or "edge"
            if p_under is None:
                p_under = 1.0 - p_over
                under_src = under_src or "edge_inverted"

    return p_over, p_under, over_src, under_src


def _adjust_over_prob_for_played_line(
    p_base: float | None,
    line: Any,
    standard_line: Any,
    pick_type: str,
) -> float | None:
    """
    Map a standard-line proxy P(OVER) to the played Goblin/Demon line.

    Goblin (line easier vs standard for OVER): monotonic boost toward 1.0.
    Demon (line harder): monotonic reduction toward 0.0.
    """
    if p_base is None:
        return None
    pt = norm_pick_type(pick_type)
    if pt not in ("goblin", "demon"):
        return p_base
    try:
        played = float(line)
        std = float(standard_line)
    except (TypeError, ValueError):
        return p_base
    if not math.isfinite(played) or not math.isfinite(std) or std == 0:
        return p_base
    delta = leg_delta_pct(played, std)
    if delta is None:
        return p_base
    p = float(np.clip(p_base, 0.0, 1.0))
    if pt == "goblin":
        ease = float(np.clip((1.0 - delta) * 0.9, 0.0, 0.4))
        return float(np.clip(p + (1.0 - p) * ease, 0.0, 1.0))
    excess = float(np.clip(delta - 1.0, 0.0, 0.5))
    penalty = float(np.clip(excess * 0.85, 0.0, 0.4))
    return float(np.clip(p - penalty, 0.0, 1.0))


def _resolve_goblin_demon_over_at_line(
    row: pd.Series,
    p_over: float | None,
    over_src: str,
) -> tuple[float | None, float | None, str]:
    """
    Return (p_over_at_line, p_over_standard_proxy, source_tag).

    L5/line_hit_rate are already at the played line; hit_rate/ml_prob/rank_score are adjusted
    when standard_line is available.
    """
    pt = norm_pick_type(row.get("pick_type") or row.get("pick_type_norm"))
    if pt not in ("goblin", "demon"):
        return p_over, None, over_src

    p_std_proxy: float | None = None
    src = over_src or ""
    if src in _AT_LINE_OVER_SOURCES or src.endswith("_at_line"):
        tag = "l5_over_at_line" if src == "l5_over" else (src or "at_line")
        return p_over, None, tag

    line = row.get("line")
    std = row.get("standard_line")
    if src in _STD_PROXY_OVER_SOURCES or src.endswith("_inverted"):
        p_std_proxy = p_over
        adjusted = _adjust_over_prob_for_played_line(p_over, line, std, pt)
        if adjusted is not None and adjusted != p_over:
            return adjusted, p_std_proxy, f"{src}_line_adjusted" if src else "line_adjusted"
        if adjusted is not None:
            return adjusted, p_std_proxy, src or "line_adjusted"

    if p_over is not None and src:
        return p_over, p_std_proxy, src
    return p_over, p_std_proxy, src or "unknown"


def _def_matchup_signal(direction: str, def_tier: str) -> float:
    """Simple directional defense alignment in [-1, 1]."""
    dt = str(def_tier or "").strip().lower()
    weak = any(x in dt for x in ("weak", "below", "poor", "bottom"))
    elite = any(x in dt for x in ("elite", "strong", "top", "above"))
    if direction == "OVER":
        if weak:
            return 1.0
        if elite:
            return -0.6
        return 0.0
    if elite:
        return 1.0
    if weak:
        return -0.6
    return 0.0


def _sport_spec_for(sport: str, sport_specs: dict[str, Any]) -> dict[str, Any] | None:
    su = norm_sport(sport)
    if su in sport_specs:
        return sport_specs[su]
    for key, val in sport_specs.items():
        aliases = [str(a).upper() for a in (val.get("aliases") or [])]
        if su == key or su in aliases:
            return val
    return None


def _min_hit_prob_actionable_for_row(
    row: pd.Series,
    pick_rules: dict[str, Any],
    sport_specs: dict[str, Any],
) -> float | None:
    pt = norm_pick_type(row.get("pick_type") or row.get("pick_type_norm"))
    if pt not in ("goblin", "demon"):
        return None
    rules = pick_rules.get(pt) or {}
    sport = norm_sport(row.get("sport_norm") or row.get("sport"))
    spec = _sport_spec_for(sport, sport_specs)
    overrides = (spec or {}).get("pick_type_overrides") or {}
    pt_over = overrides.get(pt) if isinstance(overrides, dict) else {}
    if isinstance(pt_over, dict) and pt_over.get("min_hit_prob_actionable") is not None:
        return float(pt_over["min_hit_prob_actionable"])
    if rules.get("min_hit_prob_actionable") is not None:
        return float(rules["min_hit_prob_actionable"])
    if pt == "demon" and rules.get("min_hit_prob_over") is not None:
        return float(rules["min_hit_prob_over"])
    return None


def _pick_type_eligible(
    row: pd.Series,
    pick_rules: dict[str, Any],
    sport_specs: dict[str, Any] | None = None,
) -> bool:
    pt = norm_pick_type(row.get("pick_type") or row.get("pick_type_norm"))
    direction = norm_direction(row.get("direction") or row.get("direction_norm"))
    rules = pick_rules.get(pt) or pick_rules.get("standard") or {}
    allowed = [str(x).upper() for x in (rules.get("allowed_directions") or ["OVER", "UNDER"])]
    if direction not in allowed:
        return False

    specs = sport_specs if sport_specs is not None else (_load_checklist()[1])
    min_act = _min_hit_prob_actionable_for_row(row, pick_rules, specs)
    if min_act is not None:
        p_act = _to_prob_0_1(row.get("hit_prob_actionable"))
        if p_act is None:
            p_act = _to_prob_0_1(row.get("hit_prob_over_at_line"))
        if p_act is None:
            p_act = _to_prob_0_1(row.get("hit_prob_over"))
        if p_act is None or p_act < min_act:
            return False

    if pt == "demon":
        tier = str(row.get("tier") or "").strip().upper()
        allowed_tiers = {str(t).upper() for t in (rules.get("allowed_tiers") or ["A", "B"])}
        if tier and tier not in allowed_tiers:
            return False
        try:
            blend = float(row.get("blended_score") or row.get("rank_score") or 0)
        except (TypeError, ValueError):
            blend = 0.0
        min_blend = float(rules.get("min_blend_score", 0.7))
        if blend < min_blend:
            return False
    return True


def _missing_fields(row: pd.Series, sport: str, checklist_sports: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    core = [
        "player",
        "prop_type",
        "direction",
        "pick_type",
        "line",
        "rank_score",
        "edge",
    ]
    for c in core:
        v = row.get(c)
        if v is None or (isinstance(v, float) and not math.isfinite(v)) or str(v).strip() == "":
            missing.append(c)

    if pd.isna(row.get("hit_prob_over")):
        missing.append("hit_prob_over")
    if pd.isna(row.get("hit_prob_under")):
        missing.append("hit_prob_under")

    spec = None
    for key, val in checklist_sports.items():
        aliases = [str(a).upper() for a in (val.get("aliases") or [])]
        if sport == key or sport in aliases:
            spec = val
            break
    if spec:
        for c in spec.get("required_extra") or []:
            if c not in row.index:
                missing.append(c)
                continue
            v = row.get(c)
            if v is None or (isinstance(v, str) and not str(v).strip()):
                missing.append(c)
    return missing


def enrich_read_fields_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    """Add standardized read/probability columns to a props dataframe."""
    if df is None or len(df) == 0:
        return df
    pick_rules, sport_specs = _load_checklist()
    out = normalize_slate_column_names(_alias_sport_fields(df)).copy()
    out = out.loc[:, ~out.columns.duplicated()].copy()

    if "sport" not in out.columns:
        out["sport"] = ""
    out["sport_norm"] = out["sport"].map(norm_sport)

    if "direction" not in out.columns:
        out["direction"] = "OVER"
    out["direction_norm"] = out["direction"].map(norm_direction)

    if "pick_type" not in out.columns:
        out["pick_type"] = "Standard"
    out["pick_type_norm"] = out["pick_type"].map(norm_pick_type)

    line = _series_or_nan(out, "line")
    std_line = _series_or_nan(out, "standard_line")
    edge = _series_or_nan(out, "edge")
    abs_edge = _series_or_nan(out, "abs_edge")
    if "abs_edge" not in out.columns or abs_edge.isna().all():
        abs_edge = edge.abs()
        out["abs_edge"] = abs_edge

    out["line_delta_vs_standard"] = np.where(
        std_line.notna() & line.notna(),
        line - std_line,
        np.nan,
    )
    out["edge_signed"] = np.where(
        out["direction_norm"].eq("UNDER"),
        -edge,
        edge,
    )
    out["edge_pct_vs_line"] = np.where(
        line.notna() & (line.abs() > 1e-9),
        (out["edge_signed"] / line.abs()) * 100.0,
        np.nan,
    )

    l5o = _series_or_nan(out, "l5_over", 0)
    l5u = _series_or_nan(out, "l5_under", 0)
    l10o = _series_or_nan(out, "l10_over", 0)
    l10u = _series_or_nan(out, "l10_under", 0)
    out["l5_side_hit_rate"] = np.where(
        out["direction_norm"].eq("UNDER"),
        np.clip(l5u / 5.0, 0, 1),
        np.clip(l5o / 5.0, 0, 1),
    )
    out["l10_side_hit_rate"] = np.where(
        out["direction_norm"].eq("UNDER"),
        np.clip(l10u / 10.0, 0, 1),
        np.clip(l10o / 10.0, 0, 1),
    )
    out["consistency_score"] = (
        0.6 * out["l5_side_hit_rate"].fillna(0.5) + 0.4 * out["l10_side_hit_rate"].fillna(0.5)
    ).clip(0, 1)

    p_over_list: list[float | None] = []
    p_under_list: list[float | None] = []
    p_over_at_line_list: list[float | None] = []
    p_over_std_proxy_list: list[float | None] = []
    over_src_list: list[str] = []
    under_src_list: list[str] = []
    for _, r in out.iterrows():
        po, pu, osrc, usrc = _resolve_hit_prob_over_under(r)
        po_line, po_std, osrc_line = _resolve_goblin_demon_over_at_line(r, po, osrc)
        p_over_list.append(po_line if po_line is not None else po)
        p_under_list.append(pu)
        p_over_at_line_list.append(po_line)
        p_over_std_proxy_list.append(po_std)
        over_src_list.append(osrc_line)
        under_src_list.append(usrc)
    out["hit_prob_over"] = pd.Series(p_over_list, index=out.index, dtype=float)
    out["hit_prob_under"] = pd.Series(p_under_list, index=out.index, dtype=float)
    out["hit_prob_over_at_line"] = pd.Series(p_over_at_line_list, index=out.index, dtype=float)
    out["hit_prob_over_standard_proxy"] = pd.Series(p_over_std_proxy_list, index=out.index, dtype=float)
    out["prob_over_source"] = over_src_list
    out["prob_under_source"] = under_src_list

    out["hit_prob_selected"] = np.where(
        out["direction_norm"].eq("UNDER"),
        out["hit_prob_under"],
        out["hit_prob_over"],
    )

    # Goblin/Demon actionable prob is OVER-only at the played line
    out["hit_prob_actionable"] = np.where(
        out["pick_type_norm"].isin(["goblin", "demon"]),
        out["hit_prob_over_at_line"].fillna(out["hit_prob_over"]),
        out["hit_prob_selected"],
    )

    if "leg_prob_used" not in out.columns:
        out["leg_prob_used"] = out["hit_prob_actionable"]
    else:
        mask = pd.to_numeric(out["leg_prob_used"], errors="coerce").isna()
        out.loc[mask, "leg_prob_used"] = out.loc[mask, "hit_prob_actionable"]

    def_tier = out.get("def_tier", out.get("Def Tier", pd.Series("", index=out.index)))
    def_tier_s = def_tier.astype(str) if def_tier is not None else pd.Series("", index=out.index)
    out["def_matchup_signal"] = [
        _def_matchup_signal(d, dt) for d, dt in zip(out["direction_norm"], def_tier_s, strict=False)
    ]

    rs = _series_or_nan(out, "rank_score").fillna(0)
    edge_norm = np.clip(abs_edge.fillna(0) / 15.0, 0, 1)
    prob_comp = out["hit_prob_actionable"].fillna(0.5)
    tier_map = {"A": 1.0, "B": 0.86, "C": 0.70, "D": 0.45}
    tier_s = out.get("tier", pd.Series("C", index=out.index)).astype(str).str.upper().str.strip()
    tier_comp = tier_s.map(tier_map).fillna(0.55)
    matchup_comp = (out["def_matchup_signal"].astype(float) + 1.0) / 2.0

    out["rank_edge_component"] = edge_norm
    out["rank_prob_component"] = prob_comp
    out["rank_consistency_component"] = out["consistency_score"]
    out["rank_matchup_component"] = matchup_comp
    out["rank_tier_component"] = tier_comp
    out["rank_read_score"] = (
        0.28 * edge_norm
        + 0.30 * prob_comp
        + 0.18 * out["consistency_score"].fillna(0.5)
        + 0.14 * matchup_comp
        + 0.10 * tier_comp
    ).clip(0, 1)

    if "prop_quality_score" in out.columns:
        pq = pd.to_numeric(out["prop_quality_score"], errors="coerce").fillna(out["rank_read_score"])
    else:
        pq = out["rank_read_score"]
        out["prop_quality_score"] = pq

    eligible: list[bool] = []
    missing_json: list[str] = []
    completeness: list[float] = []
    for _, r in out.iterrows():
        sport = norm_sport(r.get("sport_norm") or r.get("sport"))
        miss = _missing_fields(r, sport, sport_specs)
        core_n = 10
        comp = 1.0 - (len(miss) / core_n)
        eligible.append(_pick_type_eligible(r, pick_rules, sport_specs))
        missing_json.append(json.dumps(miss))
        completeness.append(float(np.clip(comp, 0, 1)))

    out["pick_type_eligible"] = eligible
    out["read_fields_missing"] = missing_json
    out["data_completeness_score"] = completeness

    # Enforce product rule in dataframe (drop ineligible from ticketing pools elsewhere)
    bad_pick_dir = out["pick_type_norm"].isin(["goblin", "demon"]) & out["direction_norm"].eq("UNDER")
    out.loc[bad_pick_dir, "pick_type_eligible"] = False

    return out


def audit_read_fields_dataframe(df: pd.DataFrame | None, sport: str | None = None) -> dict[str, Any]:
    """Summarize completeness and average probabilities for a sport or full slate."""
    if df is None or len(df) == 0:
        return {"rows": 0, "sports": {}}
    enriched = enrich_read_fields_dataframe(df)
    if sport:
        sp = norm_sport(sport)
        enriched = enriched[enriched["sport_norm"].eq(sp)].copy()
    sports_out: dict[str, Any] = {}
    for sp, g in enriched.groupby("sport_norm", dropna=False):
        if not str(sp).strip():
            continue
        n = len(g)
        elig = int(pd.Series(g["pick_type_eligible"]).astype(bool).sum()) if "pick_type_eligible" in g.columns else n
        sports_out[str(sp)] = {
            "rows": int(n),
            "pick_type_eligible_pct": round(100.0 * elig / n, 2) if n else 0.0,
            "avg_data_completeness": round(float(pd.to_numeric(g["data_completeness_score"], errors="coerce").mean()), 3),
            "avg_hit_prob_over": round(float(pd.to_numeric(g["hit_prob_over"], errors="coerce").mean()), 3),
            "avg_hit_prob_under": round(float(pd.to_numeric(g["hit_prob_under"], errors="coerce").mean()), 3),
            "avg_hit_prob_actionable": round(float(pd.to_numeric(g["hit_prob_actionable"], errors="coerce").mean()), 3),
            "avg_rank_read_score": round(float(pd.to_numeric(g["rank_read_score"], errors="coerce").mean()), 3),
            "avg_prop_quality_score": round(float(pd.to_numeric(g["prop_quality_score"], errors="coerce").mean()), 3),
            "pct_missing_hit_prob_over": round(
                100.0 * pd.to_numeric(g["hit_prob_over"], errors="coerce").isna().mean(), 2
            ),
        }
    return {
        "rows": int(len(enriched)),
        "sports": sports_out,
    }


def enrich_slate_row_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Enrich a single Slate Explorer JSON row dict."""
    df = pd.DataFrame([row])
    if "direction" not in df.columns and "dir" in row:
        df["direction"] = row.get("dir")
    if "prop_type" not in df.columns and "prop" in row:
        df["prop_type"] = row.get("prop")
    enriched = enrich_read_fields_dataframe(df)
    if enriched is None or len(enriched) == 0:
        return row
    out = dict(row)
    for col in enriched.columns:
        if col.endswith("_norm"):
            continue
        val = enriched.iloc[0][col]
        try:
            if pd.isna(val):
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(val, (np.floating, np.integer)):
            out[col] = float(val)
        else:
            out[col] = val
    try:
        miss = json.loads(str(enriched.iloc[0].get("read_fields_missing") or "[]"))
        out["read_fields_missing"] = miss
    except json.JSONDecodeError:
        pass
    return out
