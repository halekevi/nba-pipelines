#!/usr/bin/env python3
"""
Backtest ticket cash rate: baseline vs tier-def gates vs stack-70 vs both.

June 2026 graded_main tickets (WNBA/NBA), legs enriched from graded_props JSON.
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
    _build_pit_lookup_bball,
    _load_nba_logs,
    _load_wnba_logs,
)
from backtest_ticket_top3_gates import (  # noqa: E402
    load_def_ranks,
    load_static_top3,
    parse_ticket_eval,
    print_goblin_standard_tier_split,
    _CATEGORIES,
    _wnba_team_key,
)
from utils.stack_70_eligible import attach_stack_70_columns, stack_70_eligible_row
from utils.ticket_tier_defense_gates import (
    blanket_tier_defense_exclusion_mask,
    tier_defense_exclusion_mask,
)

DATE_FROM, DATE_TO = "2026-06-01", "2026-06-15"
BASKETBALL = frozenset({"WNBA", "NBA", "NBA1H", "NBA1Q", "WCBB", "CBB"})


def _norm_name(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


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


@dataclass
class EnrichedLeg:
    date: str
    sport: str
    ticket_win: bool
    leg_result: str
    player: str
    prop: str
    direction: str
    line: str
    pick_type: str = "Standard"
    violation: bool = False
    legacy_violation: bool = False
    violation_reason: str = ""
    stack_70: bool = False
    stack_70_known: bool = False
    graded_hit: int | None = None


@dataclass
class TicketRow:
    date: str
    ticket_id: str
    win: bool
    legs: list[EnrichedLeg] = field(default_factory=list)

    @property
    def sports(self) -> set[str]:
        return {l.sport for l in self.legs}

    @property
    def all_tier_clean(self) -> bool:
        return all(not l.violation for l in self.legs)

    @property
    def all_stack_70(self) -> bool:
        return all(l.stack_70 for l in self.legs if l.stack_70_known) and any(l.stack_70_known for l in self.legs)

    @property
    def all_stack_70_strict(self) -> bool:
        return bool(self.legs) and all(l.stack_70_known and l.stack_70 for l in self.legs)

    @property
    def tier_and_stack(self) -> bool:
        return self.all_tier_clean and self.all_stack_70_strict


def enrich_tickets(tickets: list, pit, def_ranks, static_top3) -> list[TicketRow]:
    graded_cache: dict[str, dict] = {}
    out: list[TicketRow] = []
    for t in tickets:
        legs_out: list[EnrichedLeg] = []
        for leg in t.legs:
            if leg.sport not in BASKETBALL:
                continue
            if leg.date not in graded_cache:
                graded_cache[leg.date] = load_graded_index(leg.date)
            gk = (
                leg.sport,
                _norm_name(leg.player),
                leg.prop.lower().strip(),
                leg.direction,
                str(leg.line).strip(),
            )
            grow = graded_cache[leg.date].get(gk, {})
            el = EnrichedLeg(
                date=leg.date,
                sport=leg.sport,
                ticket_win=leg.ticket_win,
                leg_result=leg.leg_result,
                player=leg.player,
                prop=leg.prop,
                direction=leg.direction,
                line=leg.line,
                graded_hit=1 if leg.leg_result == "HIT" else 0 if leg.leg_result == "MISS" else None,
            )
            legs_out.append(el)
        if not legs_out:
            continue
        out.append(TicketRow(date=t.date, ticket_id=t.ticket_id, win=t.win, legs=legs_out))

    # tier flags via existing enrich on synthetic legs
    class _L:
        pass

    for tr in out:
        for el in tr.legs:
            fake = type("Leg", (), {
                "date": el.date, "sport": el.sport, "ticket_win": el.ticket_win,
                "leg_result": el.leg_result, "player": el.player, "prop": el.prop,
                "matchup": "", "direction": el.direction, "line": el.line,
                "team": "", "opp": "", "player_tier": "", "opp_rank": None,
                "violation": False, "violation_reason": "",
            })()
            graded_cache_row = graded_cache.get(el.date, {}).get(
                (el.sport, _norm_name(el.player), el.prop.lower().strip(), el.direction, str(el.line).strip()),
                {},
            )
            fake.team = str(graded_cache_row.get("team") or "").upper()
            fake.opp = str(graded_cache_row.get("opp_team") or "").upper()
            if not fake.team:
                fake.matchup = f"{fake.team} vs {fake.opp}"

    # Re-run enrich_and_flag on ticket objects - simpler: build dataframe per leg batch
    rows = []
    for tr in out:
        for el in tr.legs:
            gk = (el.sport, _norm_name(el.player), el.prop.lower().strip(), el.direction, str(el.line).strip())
            g = graded_cache.get(el.date, {}).get(gk, {})
            rows.append({
                "date": el.date,
                "sport": el.sport,
                "player": el.player,
                "prop": el.prop,
                "prop_type": el.prop,
                "direction": el.direction,
                "line": el.line,
                "pick_type": g.get("pick_type", "Standard"),
                "team": g.get("team", ""),
                "opp_team": g.get("opp_team", ""),
                "hit_rate": g.get("hit_rate"),
                "strat_hit_rate": g.get("strat_hit_rate"),
                "strat_n": g.get("strat_n"),
                "l5_over": g.get("l5_over"),
                "l5_under": g.get("l5_under"),
                "def_tier": g.get("def_tier"),
                "consistency_grade": g.get("consistency_grade"),
                "ticket_id": tr.ticket_id,
                "leg_result": el.leg_result,
            })
    if not rows:
        return out
    df = pd.DataFrame(rows)
    df = attach_stack_70_columns(df, repo=REPO)
    blanket_excl = blanket_tier_defense_exclusion_mask(df)
    tier_excl = tier_defense_exclusion_mask(df)
    df["stack_70"] = df.apply(stack_70_eligible_row, axis=1)
    df["stack_70_known"] = df["hit_rate"].notna() | df["strat_hit_rate"].notna()
    df["tier_violation"] = tier_excl
    df["legacy_violation"] = blanket_excl & ~tier_excl

    lookup = df.set_index("ticket_id", drop=False)
    by_ticket: dict[str, list[dict]] = {}
    for _, r in df.iterrows():
        by_ticket.setdefault(r["ticket_id"], []).append(r.to_dict())

    for tr in out:
        leg_rows = by_ticket.get(tr.ticket_id, [])
        for el, r in zip(tr.legs, leg_rows):
            el.pick_type = str(r.get("pick_type", "Standard"))
            el.violation = bool(r.get("tier_violation"))
            el.legacy_violation = bool(r.get("legacy_violation"))
            el.stack_70 = bool(r.get("stack_70"))
            el.stack_70_known = bool(r.get("stack_70_known"))
    return out


def summarize(label: str, tickets: list[TicketRow], pred) -> dict:
    sub = [t for t in tickets if pred(t)]
    if not sub:
        return {"label": label, "n": 0, "wins": 0, "cash_pct": None, "net_10_3x": None}
    w = sum(1 for t in sub if t.win)
    n = len(sub)
    return {
        "label": label,
        "n": n,
        "wins": w,
        "cash_pct": 100 * w / n,
        "net_10_3x": w * 20 - (n - w) * 10,
    }


def leg_summary(tickets: list[TicketRow], pred) -> dict:
    legs = [l for t in tickets for l in t.legs if pred(t)]
    if not legs:
        return {"n": 0, "hr": None}
    dec = [l for l in legs if l.graded_hit is not None]
    if not dec:
        return {"n": len(legs), "hr": None}
    return {"n": len(dec), "hr": 100 * sum(l.graded_hit for l in dec) / len(dec)}


def main() -> None:
    paths = sorted(REPO.glob("mobile/www/ticket_eval_*.html"))
    paths = [p for p in paths if DATE_FROM <= p.stem.replace("ticket_eval_", "") <= DATE_TO]

    raw = []
    for p in paths:
        raw.extend(parse_ticket_eval(p))

    dates = sorted({t.date for t in raw})
    pit = _build_pit_lookup_bball(_load_wnba_logs(), slate_dates=dates, team_col="TEAM", categories=_CATEGORIES, team_key_fn=_wnba_team_key)
    pit.update(_build_pit_lookup_bball(_load_nba_logs(), slate_dates=dates, team_col="TEAM", categories=_CATEGORIES))
    def_ranks = {"WNBA": load_def_ranks("WNBA"), "NBA": load_def_ranks("NBA")}
    static_top3 = {"WNBA": load_static_top3("WNBA"), "NBA": load_static_top3("NBA")}

    # tier on raw for violation flag on Ticket objects from parse
    class TWrap:
        def __init__(self, t):
            self.date, self.ticket_id, self.win, self.legs = t.date, t.ticket_id, t.win, t.legs

    tickets = enrich_tickets([TWrap(t) for t in raw], pit, def_ranks, static_top3)
    wnba = [t for t in tickets if t.sports <= BASKETBALL and "WNBA" in t.sports]

    print("=" * 72)
    print(f"COMBINED BACKTEST  {DATE_FROM} → {DATE_TO}  (WNBA tickets, deduped)")
    print("Source: graded_main ticket_eval HTML — counterfactual slices, not a separate builder")
    print("  tier-def gate  → matches pool() in combined_slate_tickets.py (SHIPS)")
    print("  stack-70 only  → --stack-70-only flag (NOT default production)")
    print("=" * 72)

    scenarios = [
        ("1. ACTUAL (all tickets)", lambda t: True),
        ("2. TIER-DEF GATE (no violation legs)", lambda t: t.all_tier_clean),
        ("3. STACK-70 (all legs stack_70 eligible)", lambda t: t.all_stack_70_strict),
        ("4. TIER + STACK-70 (both)", lambda t: t.tier_and_stack),
    ]

    print(f"\n{'Scenario':<42} {'Tickets':>10} {'Cash%':>8} {'Net@$10':>10}")
    print("-" * 72)
    for label, pred in scenarios:
        s = summarize(label, wnba, pred)
        if s["n"] == 0:
            print(f"{label:<42} {'0':>10} {'—':>8} {'—':>10}")
        else:
            print(f"{label:<42} {s['wins']:>4}/{s['n']:<5} {s['cash_pct']:>7.1f}% {s['net_10_3x']:>+10,.0f}")

    print("\nLeg-level hit rate (decided legs in each ticket set):")
    for label, pred in scenarios:
        ls = leg_summary(wnba, pred)
        hr = f"{ls['hr']:.1f}%" if ls["hr"] is not None else "—"
        print(f"  {label:<42} legs={ls['n']:,}  HR={hr}")

    # stack-70 leg pool size
    all_legs = [l for t in wnba for l in t.legs]
    s70_legs = [l for l in all_legs if l.stack_70_known and l.stack_70]
    print(f"\nStack-70 legs: {len(s70_legs)}/{sum(1 for l in all_legs if l.stack_70_known)} known "
          f"({100*len(s70_legs)/max(1,len(all_legs)):.1f}% of all legs)")

    print_goblin_standard_tier_split(legs=[l for t in wnba for l in t.legs], sport="WNBA")

    print("\nBy date — TIER+STACK-70 cash rate:")
    print(f"{'date':<12} {'actual':>12} {'tier only':>12} {'stack70':>12} {'both':>12}")
    for d in dates:
        day = [t for t in wnba if t.date == d]
        if not day:
            continue
        def pct(pred):
            sub = [t for t in day if pred(t)]
            if not sub:
                return "—"
            w = sum(1 for t in sub if t.win)
            return f"{w}/{len(sub)} {100*w/len(sub):.0f}%"
        print(f"{d:<12} {pct(lambda t: True):>12} {pct(lambda t: t.all_tier_clean):>12} "
              f"{pct(lambda t: t.all_stack_70_strict):>12} {pct(lambda t: t.tier_and_stack):>12}")


if __name__ == "__main__":
    main()
