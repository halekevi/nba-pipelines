#!/usr/bin/env python3
"""
Backtest WNBA/NBA ticket-building gates on historical graded_main tickets.

Gates (legs we would NOT ticket):
  1. top-3 in prop category + OVER + elite defense (rank <= 4)
  2. bottom-3 in prop category + OVER (any defense)

Compares actual ticket cash rate vs tickets with zero gate violations.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from backtest_player_tier_vs_defense import (  # noqa: E402
    NBA_PROP_CAT,
    _build_pit_lookup_bball,
    _load_nba_logs,
    _load_wnba_logs,
    _norm_name,
)

ELITE_CUT = 4
WEAK_CUT = 10  # WNBA 15-team scale; NBA uses same rank cut as step7

WNBA_PROP_CAT = {
    **NBA_PROP_CAT,
    "freethrowsmade": None,
    "freethrowsattempted": None,
    "twopointersmade": None,
    "fieldgoalsattempted": None,
}

_WNBA_ESPN_SLATE = {
    "LV": "LVA", "LA": "LAS", "NY": "NYL", "GS": "GSV", "PHO": "PHX",
    "CONN": "CON", "CON": "CON", "DAL": "DAL", "IND": "IND", "ATL": "ATL",
    "CHI": "CHI", "MIN": "MIN", "SEA": "SEA", "POR": "PDX", "TOR": "TOR",
    "WSH": "WSH", "PHX": "PHX",
}

_CATEGORIES = ("pts", "reb", "ast", "stl", "blk", "stocks", "fg3m", "pra")


def prop_cat(prop: str, sport: str) -> str | None:
    p = re.sub(r"\s+", " ", str(prop or "").lower().strip())
    p = re.sub(r"\(combo\)\s*$", "", p).strip()
    p = p.replace("-", "").replace(" ", "").replace("+", "")
    mapping = NBA_PROP_CAT if sport == "NBA" else WNBA_PROP_CAT
    if p in mapping:
        return mapping[p]
    # display labels
    disp = {
        "points": "pts", "rebounds": "reb", "assists": "ast", "steals": "stl",
        "blocks": "blk", "3ptmade": "fg3m", "3ptattempted": "fg3a",
        "ptsasts": "pra", "ptsrebs": "pra", "ptsrebsasts": "pra",
        "defensiverebounds": "reb",
    }
    key = re.sub(r"[^a-z0-9+]", "", p.replace(" ", "").lower())
    if key in disp:
        return disp[key]
    if "combo" in str(prop).lower() and "point" in str(prop).lower():
        return "pts"
    return None


def load_def_ranks(sport: str) -> dict[str, int]:
    if sport == "WNBA":
        path = REPO / "Sports/WNBA/wnba_defense_summary.csv"
    else:
        path = REPO / "Sports/NBA/data/defense_team_summary.csv"
        if not path.exists():
            path = REPO / "Sports/NBA/defense_team_summary.csv"
    if not path.exists():
        return {}
    d = pd.read_csv(path, encoding="utf-8-sig")
    out: dict[str, int] = {}
    for _, row in d.iterrows():
        abbr = str(row.get("TEAM_ABBREVIATION", "")).upper()
        rk = int(row["OVERALL_DEF_RANK"])
        out[abbr] = rk
        if sport == "WNBA":
            out[_WNBA_ESPN_SLATE.get(abbr, abbr)] = rk
    return out


def _wnba_team_key(t: object) -> str:
    k = str(t or "").strip().upper()
    return _WNBA_ESPN_SLATE.get(k, k)


def parse_matchup_opp(matchup: str, team_hint: str = "") -> tuple[str, str]:
    parts = [p.strip() for p in matchup.split(" vs ")]
    if len(parts) != 2:
        return team_hint, ""
    left = parts[0].split("/")[-1].strip().upper()
    right = parts[1].split("/")[0].strip().upper()
    if team_hint and team_hint.upper() in (left, right):
        opp = right if team_hint.upper() == left else left
        return team_hint.upper(), opp
    return left, right


@dataclass
class Leg:
    date: str
    sport: str
    ticket_id: str
    ticket_win: bool
    leg_result: str
    player: str
    prop: str
    direction: str
    line: str
    matchup: str
    team: str = ""
    opp: str = ""
    player_tier: str = ""
    opp_rank: int | None = None
    pick_type: str = ""
    violation: bool = False
    legacy_violation: bool = False
    violation_reason: str = ""


@dataclass
class Ticket:
    date: str
    sport: str
    ticket_id: str
    win: bool
    legs: list[Leg] = field(default_factory=list)

    @property
    def has_violation(self) -> bool:
        return any(l.violation for l in self.legs)

    @property
    def sports(self) -> set[str]:
        return {l.sport for l in self.legs}


def parse_ticket_eval(path: Path) -> list[Ticket]:
    date = path.stem.replace("ticket_eval_", "")
    html = path.read_text(encoding="utf-8")
    if "grade-eval-summary-empty" in html and "tickets graded" not in html:
        return []
    tickets: list[Ticket] = []
    cards = re.split(r'<article class="ticket-card ', html)[1:]
    for i, raw in enumerate(cards):
        cls = raw.split(">", 1)[0]
        if "all-hit" in cls:
            win = True
        elif "card-missed" in cls:
            win = False
        else:
            continue
        grp_m = re.search(r'<span class="tg">([^<]+)</span>\s*<span class="tg">\d+', raw)
        group = grp_m.group(1).strip() if grp_m else f"t{i}"
        tid = f"{date}|{group}|{i}"
        t = Ticket(date=date, sport="", ticket_id=tid, win=win)
        for m in re.finditer(
            r'<div class="legrow leg-(hit|miss|pending)">.*?'
            r'<span class="pill sport-default">([A-Z0-9]+)</span>.*?'
            r'<div class="pl-(?:hit|miss|line|pending)[^"]*">(?:<span class="pl-name">)?([^<]+).*?'
            r'<div class="leg-prop-col[^"]*"><div>([^<]+)</div><div class="meta-muted">([^<]+)</div>.*?'
            r'<div class="leg-extra[^"]*">\s*([\d.]+)\s*<span class="dir-(over|under)">',
            raw,
            re.DOTALL,
        ):
            sport = m.group(2).upper()
            if sport not in ("WNBA", "NBA"):
                continue
            player = re.sub(r"&#x27;", "'", m.group(3)).strip()
            t.legs.append(
                Leg(
                    date=date,
                    sport=sport,
                    ticket_id=tid,
                    ticket_win=win,
                    leg_result=m.group(1).upper(),
                    player=player,
                    prop=m.group(4).strip(),
                    direction=m.group(7).upper(),
                    line=m.group(6),
                    matchup=m.group(5).strip(),
                )
            )
        if t.legs and all(l.leg_result in ("HIT", "MISS") for l in t.legs):
            tickets.append(t)
    return _dedupe_tickets(tickets)


def _ticket_sig(t: Ticket) -> str:
    parts = [
        f"{l.sport}|{l.player}|{l.prop}|{l.line}|{l.direction}|{l.matchup}"
        for l in t.legs
    ]
    return "||".join(sorted(parts))


def _dedupe_tickets(tickets: list[Ticket]) -> list[Ticket]:
    seen: set[str] = set()
    out: list[Ticket] = []
    for t in tickets:
        sig = _ticket_sig(t)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(t)
    return out


def load_graded_index(date: str) -> dict[tuple, dict]:
    p = REPO / f"mobile/www/graded_props_{date}.json"
    if not p.exists():
        return {}
    try:
        props = json.loads(p.read_text(encoding="utf-8")).get("props", [])
    except (OSError, json.JSONDecodeError):
        return {}
    idx: dict[tuple, dict] = {}
    for row in props:
        k = (
            str(row.get("sport", "")).upper(),
            _norm_name(str(row.get("player", ""))),
            str(row.get("prop") or row.get("prop_type", "")).lower().strip(),
            str(row.get("direction") or row.get("over_under", "")).upper().strip(),
            str(row.get("line", "")).strip(),
        )
        idx[k] = row
    return idx


def load_static_top3(sport: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = REPO / f"Sports/{sport}/data/{sport.lower()}_top3_vs_defense.csv"
    if not path.exists():
        return pd.DataFrame(), pd.DataFrame()
    t3 = pd.read_csv(path, encoding="utf-8-sig")
    top = t3[t3["leader_side"].astype(str).str.lower() == "top"]
    bot = t3[t3["leader_side"].astype(str).str.lower() == "bottom"]
    return top, bot


def static_tier(player: str, cat: str | None, top: pd.DataFrame, bot: pd.DataFrame) -> str:
    if not cat:
        return ""
    pn = _norm_name(player.split("+")[0].strip())
    if len(top[(top["PLAYER_NORM"] == pn) & (top["category"] == cat) & (top["rank_on_team"] <= 3)]):
        return "top"
    if len(bot[(bot["PLAYER_NORM"] == pn) & (bot["category"] == cat) & (bot["rank_on_team"] <= 3)]):
        return "bottom"
    return ""


def _norm_pick_type_label(raw: object) -> str:
    s = str(raw or "Standard").strip().lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    return "standard"



def print_goblin_standard_tier_split(
    tickets: list[Ticket] | None = None,
    *,
    legs: list | None = None,
    sport: str | None = None,
    title: str = "GOBLIN vs STANDARD (tier gate)",
) -> None:
    """Print pick-type mix and tier-gate impact (production vs legacy blanket gate)."""
    all_legs: list = []
    if legs is not None:
        all_legs = list(legs)
    elif tickets:
        for t in tickets:
            for leg in t.legs:
                all_legs.append(leg)
    if sport:
        all_legs = [l for l in all_legs if str(getattr(l, "sport", "")).upper() == sport.upper()]
    if not all_legs:
        return

    def _result(l) -> str:
        return str(getattr(l, "leg_result", "") or "").upper()

    def _hit(l) -> bool | None:
        r = _result(l)
        if r == "HIT":
            return True
        if r == "MISS":
            return False
        return None

    decided = [l for l in all_legs if _hit(l) is not None]
    goblin_over = [
        l for l in decided
        if _norm_pick_type_label(getattr(l, "pick_type", "Standard")) == "goblin"
        and str(getattr(l, "direction", "")).upper() == "OVER"
    ]
    standard_legs = [
        l for l in decided if _norm_pick_type_label(getattr(l, "pick_type", "Standard")) == "standard"
    ]
    std_over = [l for l in standard_legs if str(getattr(l, "direction", "")).upper() == "OVER"]
    std_under = [l for l in standard_legs if str(getattr(l, "direction", "")).upper() == "UNDER"]

    def _hr(sub: list) -> str:
        if not sub:
            return "—"
        hits = sum(1 for l in sub if _hit(l))
        return f"{hits}/{len(sub)} = {100 * hits / len(sub):.1f}%"

    viol_std = [l for l in standard_legs if bool(getattr(l, "violation", False))]
    freed_goblin = [l for l in goblin_over if bool(getattr(l, "legacy_violation", False))]
    clean_std = [l for l in standard_legs if not bool(getattr(l, "violation", False))]

    print(f"\n### {title}")
    print("  Source: graded_main tickets (same slips as ticket_eval HTML, sliced counterfactually)")
    print(
        f"  Leg mix (decided): Goblin OVER {len(goblin_over)} | "
        f"Standard OVER {len(std_over)} | Standard UNDER {len(std_under)}"
    )
    print(
        f"  Leg HR:  Goblin OVER {_hr(goblin_over)}  |  "
        f"Standard OVER {_hr(std_over)}  |  Standard UNDER {_hr(std_under)}"
    )
    print(f"  Production gate (Standard only): {len(viol_std)} violating legs, HR {_hr(viol_std)}")
    print(f"  Goblin OVER exempted (legacy blanket would drop): {len(freed_goblin)} legs, HR {_hr(freed_goblin)}")
    if freed_goblin:
        from collections import Counter
        reasons = Counter(str(getattr(l, "violation_reason", "") or "") for l in freed_goblin)
        reasons.pop("", None)
        if reasons:
            print(f"    reasons: {dict(reasons)}")
    print(f"  Standard passing gate: {len(clean_std)} legs, HR {_hr(clean_std)}")


def enrich_and_flag(
    tickets: list[Ticket],
    pit: dict[tuple[str, str, str, str], str],
    def_ranks: dict[str, dict[str, int]],
    static_top3: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
) -> None:
    graded_by_date: dict[str, dict[tuple, dict]] = {}
    for t in tickets:
        for leg in t.legs:
            if leg.date not in graded_by_date:
                graded_by_date[leg.date] = load_graded_index(leg.date)
            gidx = graded_by_date[leg.date]
            gk = (
                leg.sport,
                _norm_name(leg.player),
                leg.prop.lower().strip(),
                leg.direction,
                str(leg.line).strip(),
            )
            grow = gidx.get(gk, {})
            team = str(grow.get("team") or "").upper()
            opp = str(grow.get("opp_team") or "").upper()
            if not team or not opp:
                team, opp = parse_matchup_opp(leg.matchup, team)
            leg.team, leg.opp = team, opp

            cat = prop_cat(leg.prop, leg.sport)
            pn = _norm_name(leg.player.split("+")[0].strip())
            tier = pit.get((leg.date, team, pn, cat), "") if cat else ""
            if not tier and cat:
                top, bot = static_top3.get(leg.sport, (pd.DataFrame(), pd.DataFrame()))
                tier = static_tier(leg.player, cat, top, bot)
            leg.player_tier = tier

            ranks = def_ranks.get(leg.sport, {})
            leg.opp_rank = ranks.get(opp)
            if leg.opp_rank is None and grow.get("def_tier"):
                dt = str(grow.get("def_tier")).strip()
                if dt == "Elite":
                    leg.opp_rank = ELITE_CUT
                elif dt in ("Weak", "Below Avg"):
                    leg.opp_rank = WEAK_CUT

            leg.pick_type = str(grow.get("pick_type", "Standard"))
            pt = _norm_pick_type_label(leg.pick_type)
            leg.violation = False
            leg.legacy_violation = False
            leg.violation_reason = ""

            would_violate = False
            reason = ""
            if leg.direction == "OVER" and tier == "bottom":
                would_violate = True
                reason = "bottom3_OVER"
            elif (
                leg.direction == "OVER"
                and tier == "top"
                and leg.opp_rank is not None
                and leg.opp_rank <= ELITE_CUT
            ):
                would_violate = True
                reason = "top3_OVER_vs_elite"

            if would_violate:
                leg.violation_reason = reason
                if pt == "goblin" and leg.direction == "OVER":
                    leg.legacy_violation = True
                elif pt == "standard":
                    leg.violation = True


def summarize(name: str, tickets: list[Ticket], sport: str | None = None) -> dict:
    ts = tickets
    if sport:
        ts = [t for t in tickets if sport in t.sports]
    if not ts:
        return {"name": name, "n": 0}
    wins = sum(1 for t in ts if t.win)
    n = len(ts)
    viol_legs = [l for t in ts for l in t.legs if l.violation]
    clean = [t for t in ts if not t.has_violation]
    dirty = [t for t in ts if t.has_violation]
    cw = sum(1 for t in clean if t.win)
    dw = sum(1 for t in dirty if t.win)
    decided_legs = [l for t in ts for l in t.legs if l.leg_result in ("HIT", "MISS")]
    vlegs = [l for l in decided_legs if l.violation]
    clelegs = [l for l in decided_legs if not l.violation]
    return {
        "name": name,
        "n": n,
        "wins": wins,
        "cash_pct": 100 * wins / n if n else 0,
        "net_flat10_3x": wins * 20 - (n - wins) * 10,
        "clean_n": len(clean),
        "clean_cash": 100 * cw / len(clean) if clean else None,
        "dirty_n": len(dirty),
        "dirty_cash": 100 * dw / len(dirty) if dirty else None,
        "viol_leg_n": len(viol_legs),
        "viol_leg_hr": 100 * sum(1 for l in vlegs if l.leg_result == "HIT") / len(vlegs) if vlegs else None,
        "clean_leg_hr": 100 * sum(1 for l in clelegs if l.leg_result == "HIT") / len(clelegs) if clelegs else None,
    }


def main() -> None:
    date_from, date_to = "2026-06-01", "2026-06-15"
    paths = sorted(REPO.glob("mobile/www/ticket_eval_*.html"))
    paths = [p for p in paths if date_from <= p.stem.replace("ticket_eval_", "") <= date_to]

    all_tickets: list[Ticket] = []
    for p in paths:
        all_tickets.extend(parse_ticket_eval(p))

    dates = sorted({t.date for t in all_tickets})
    wnba_pit = _build_pit_lookup_bball(
        _load_wnba_logs(),
        slate_dates=dates,
        team_col="TEAM",
        categories=_CATEGORIES,
        team_key_fn=_wnba_team_key,
    )
    nba_pit = _build_pit_lookup_bball(
        _load_nba_logs(),
        slate_dates=dates,
        team_col="TEAM",
        categories=_CATEGORIES,
    )
    pit: dict[tuple[str, str, str, str], str] = {}
    pit.update(wnba_pit)
    pit.update(nba_pit)

    def_ranks = {"WNBA": load_def_ranks("WNBA"), "NBA": load_def_ranks("NBA")}
    static_top3 = {
        "WNBA": load_static_top3("WNBA"),
        "NBA": load_static_top3("NBA"),
    }
    enrich_and_flag(all_tickets, pit, def_ranks, static_top3)

    print("=" * 72)
    print(f"TICKET GATE BACKTEST  {date_from} → {date_to}")
    print("Gates (Standard only): drop bottom3+OVER; drop top3+OVER vs elite (def rank ≤4)")
    print("Goblin OVER bypasses tier gate (matches combined_slate_tickets.py pool())")
    print("=" * 72)

    for sport in ("WNBA", "NBA", None):
        label = sport or "ALL BASKETBALL"
        s_all = summarize("ACTUAL (all tickets)", all_tickets, sport)
        s_clean = summarize("CLEAN (no violation legs)", [t for t in all_tickets if not t.has_violation], sport)
        if s_all["n"] == 0:
            continue
        print(f"\n### {label}")
        print(f"  Actual:     {s_all['wins']}/{s_all['n']} cash = {s_all['cash_pct']:.1f}%")
        if s_clean["clean_n"]:
            print(
                f"  If gated:   {s_clean['wins']}/{s_clean['clean_n']} cash = {s_clean['cash_pct']:.1f}%"
                f"  ({s_all['n'] - s_clean['clean_n']} tickets removed)"
                f"  |  net @ $10 3x: ${s_clean['net_flat10_3x']:,.0f} vs actual ${s_all['net_flat10_3x']:,.0f}"
            )
        if s_all["dirty_n"]:
            print(f"  Removed tickets would have cashed: {s_all['dirty_cash']:.1f}% ({s_all['dirty_n']} tix)")
        if s_all.get("viol_leg_hr") is not None:
            print(
                f"  Violating legs HR: {s_all['viol_leg_hr']:.1f}%  |  "
                f"Clean legs HR: {s_all['clean_leg_hr']:.1f}%"
            )

    print_goblin_standard_tier_split(all_tickets, sport="WNBA")

    # per-date WNBA
    print("\n### WNBA BY DATE")
    print(f"{'date':<12} {'actual':>14} {'gated':>14} {'removed':>8} {'viol legs':>10}")
    for d in dates:
        day = [t for t in all_tickets if t.date == d and "WNBA" in t.sports]
        if not day:
            continue
        a = summarize("", day, "WNBA")
        c = [t for t in day if not t.has_violation]
        cw = sum(1 for t in c if t.win)
        print(
            f"{d:<12} {a['wins']:>3}/{a['n']:<3} {a['cash_pct']:>5.1f}%"
            f"  {cw:>3}/{len(c):<3} {100*cw/len(c) if c else 0:>5.1f}%"
            f"  {a['n']-len(c):>8}"
            f"  {a['viol_leg_n']:>10}"
        )

    # violation breakdown
    vlegs = [l for t in all_tickets for l in t.legs if l.violation and l.leg_result in ("HIT", "MISS")]
    if vlegs:
        print("\n### VIOLATION LEG BREAKDOWN (decided)")
        from collections import Counter
        c = Counter(l.violation_reason for l in vlegs)
        for reason, n in c.items():
            h = sum(1 for l in vlegs if l.violation_reason == reason and l.leg_result == "HIT")
            print(f"  {reason}: {h}/{n} = {100*h/n:.0f}% leg HR")


if __name__ == "__main__":
    main()
