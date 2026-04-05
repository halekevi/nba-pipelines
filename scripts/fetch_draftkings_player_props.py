#!/usr/bin/env python3
"""
Fetch DraftKings *sportsbook* player (and skill) props via the unofficial v5
eventgroups JSON API, normalized to the PrizePicks-shaped step1 columns plus
DraftKings metadata.

API shape (community-documented, may change):
  GET https://sportsbook.draftkings.com/sites/{SITE}/api/v5/eventgroups/{ID}?format=json
  GET .../eventgroups/{ID}/categories/{categoryId}?format=json
  GET .../eventgroups/{ID}/categories/{categoryId}/subcategories/{subId}?format=json

League presets use the numeric IDs from DraftKings league URLs (e.g. MLB /leagues/baseball/84240).

Examples:
  py -3 scripts/fetch_draftkings_player_props.py --league nba -o dk_nba_props.csv
  py -3 scripts/fetch_draftkings_player_props.py --league nfl --categories "Passing Props" "Rush/Rec Props" -o dk_nfl.csv
  py -3 scripts/fetch_draftkings_player_props.py --list-categories --league nba

Notes:
  - Some networks/datacenters get HTTP 403 (Akamai). Run from a normal home/residential IP if needed.
  - Try --site-code US-NJ-SB or US-PA-SB if US-SB fails.
  - This is not Pick6; it is standard sportsbook markets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from pickem_step1_schema import DK_OUTPUT_COLUMNS

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# League slug -> event group id (from DK /leagues/.../{id} URLs and community references).
EVENT_GROUP_IDS: Dict[str, str] = {
    "nba": "42648",
    "nhl": "42133",
    "nfl": "88808",
    "cfb": "87637",
    "mlb": "84240",
    "epl": "40253",
    "wnba": "94682",
}

# If --auto-categories, keep categories whose names match any of these substrings.
_PLAYER_CATEGORY_HINTS: Tuple[str, ...] = (
    "prop",
    "player",
    "rush",
    "rec ",
    "receiv",
    "pass",
    "touchdown",
    "td ",
    "scorer",
    "goal",
    "point",  # points / 3-point — also matches "game points" sometimes
    "rebound",
    "assist",
    "three",
    "steal",
    "block",
    "combo",
    "double",
    "triple",
    "strikeout",
    "pitcher",
    "batter",
    "hits",
    "home run",
    "shots",
    "saves",
    "yards",
)

# Exclude these even if they matched a hint (team/game level).
_CATEGORY_BLOCKLIST_SUBSTR: Tuple[str, ...] = (
    "game lines",
    "futures",
    "team futures",
    "win total",
    "division",
    "award",
    "same game parlay",
    "sgp ",
    "quick pick",
    "specials",
    "boost",
    "live",
)


def _session(site_code: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://sportsbook.draftkings.com",
            "Referer": f"https://sportsbook.draftkings.com/",
        }
    )
    return s


def _base_url(site_code: str, event_group_id: str) -> str:
    return f"https://sportsbook.draftkings.com/sites/{site_code}/api/v5/eventgroups/{event_group_id}"


def _get_json(session: requests.Session, url: str, retries: int = 4) -> dict:
    last: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                time.sleep(random.uniform(0.8, 2.0))
            r = session.get(url, timeout=90)
            if r.status_code == 403:
                raise RuntimeError(
                    "HTTP 403 Access Denied from DraftKings (often geo/bot filtering). "
                    "Try another network, or --site-code US-NJ-SB / US-PA-SB, or run from home IP."
                )
            r.raise_for_status()
            ct = (r.headers.get("content-type") or "").lower()
            if "json" not in ct:
                raise RuntimeError(
                    f"Expected JSON from {url[:80]}..., got content-type={ct!r} body_prefix={r.text[:120]!r}"
                )
            return r.json()
        except Exception as e:
            last = e
            time.sleep(min(20.0, 2**attempt))
    raise RuntimeError(f"GET failed after {retries} attempts: {url} | {last}")


def _category_id(cat: dict) -> str:
    return str(
        cat.get("offerCategoryId")
        or cat.get("categoryId")
        or cat.get("id")
        or ""
    ).strip()


def _iter_category_descriptors(eg: dict) -> Iterable[Tuple[dict, dict]]:
    """Yield (category_dict, descriptor_dict) for each subcategory row."""
    for cat in eg.get("offerCategories") or []:
        if not isinstance(cat, dict):
            continue
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            if isinstance(desc, dict):
                yield cat, desc


def _subcategory_id(desc: dict) -> str:
    return str(
        desc.get("subcategoryId")
        or desc.get("offerSubcategoryId")
        or desc.get("id")
        or ""
    ).strip()


def _auto_pick_categories(categories: List[dict]) -> List[dict]:
    chosen: List[dict] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        name = (cat.get("name") or "").strip()
        nl = name.lower()
        if any(b in nl for b in _CATEGORY_BLOCKLIST_SUBSTR):
            continue
        if any(h in nl for h in _PLAYER_CATEGORY_HINTS):
            chosen.append(cat)
    return chosen


def _find_categories_by_name(categories: List[dict], wanted: List[str]) -> List[dict]:
    out: List[dict] = []
    for w in wanted:
        wl = w.strip().lower()
        if not wl:
            continue
        match: Optional[dict] = None
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            nm = (cat.get("name") or "").strip().lower()
            if nm == wl:
                match = cat
                break
        if match is None:
            for cat in categories:
                if not isinstance(cat, dict):
                    continue
                nm = (cat.get("name") or "").strip().lower()
                if wl in nm or nm in wl:
                    match = cat
                    break
        if match:
            out.append(match)
        else:
            print(f"[warn] No offer category matched {w!r}", file=sys.stderr)
    return out


def _event_lookup(root: dict) -> Dict[str, dict]:
    eg = root.get("eventGroup") or {}
    events = eg.get("events") or []
    return {str(e.get("eventId")): e for e in events if isinstance(e, dict) and e.get("eventId") is not None}


def _parse_teams_from_event_name(name: str) -> Tuple[str, str]:
    """Best-effort away/home abbrevs from event name like 'MEM @ MIL' or 'Team A vs Team B'."""
    if not name:
        return "", ""
    s = name.strip()
    if "@" in s:
        left, right = s.split("@", 1)
        return _norm_abbr(left), _norm_abbr(right)
    if " vs " in s.lower():
        parts = re.split(r"\s+vs\.?\s+", s, flags=re.I, maxsplit=1)
        if len(parts) == 2:
            return _norm_abbr(parts[0]), _norm_abbr(parts[1])
    return "", ""


def _norm_abbr(s: str) -> str:
    t = str(s or "").strip()
    t = re.sub(r"\s+", " ", t)
    # take last token as abbr guess e.g. "Memphis Grizzlies" -> use full if no abbr
    if len(t) <= 4:
        return t.upper()
    return t[:20].upper()


def _participant_name(outcome: dict, market_label: str) -> str:
    p = outcome.get("participant")
    if isinstance(p, dict):
        for k in ("name", "displayName", "shortName"):
            v = p.get(k)
            if v:
                return str(v).strip()
    parts = outcome.get("participants")
    if isinstance(parts, list) and parts:
        p0 = parts[0]
        if isinstance(p0, dict):
            for k in ("name", "displayName", "shortName"):
                v = p0.get(k)
                if v:
                    return str(v).strip()
    lab = (outcome.get("label") or "").strip()
    if lab and lab.lower() not in ("over", "under", "yes", "no"):
        return lab
    # Over/under markets: player often embedded in market label
    ml = (market_label or "").strip()
    if ml:
        return re.split(r"\s+[-–]\s+", ml, maxsplit=1)[0].strip()
    return ""


def _american_odds(outcome: dict) -> str:
    for k in ("americanOdds", "american odds", "oddsAmerican", "displayOdds", "trueOdds"):
        v = outcome.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    od = outcome.get("odds") or {}
    if isinstance(od, dict):
        v = od.get("american") or od.get("americanOdds")
        if v is not None:
            return str(v).strip()
    return ""


def _flatten_offers_for_subcategory(
    payload: dict,
    *,
    category_name: str,
    sub_name: str,
    events_by_id: Dict[str, dict],
) -> List[dict]:
    rows: List[dict] = []
    eg = payload.get("eventGroup") or {}
    events = eg.get("events") or []
    local_lookup = _event_lookup(payload)
    if local_lookup:
        events_by_id = {**events_by_id, **local_lookup}

    for cat in eg.get("offerCategories") or []:
        if not isinstance(cat, dict):
            continue
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            if not isinstance(desc, dict):
                continue
            osc = desc.get("offerSubcategory") or {}
            offers = osc.get("offers")
            if offers is None:
                continue
            sn = (desc.get("name") or sub_name or "").strip()

            # offers: list[list[market]] aligned with events, or flat list
            if offers and isinstance(offers, list) and offers and isinstance(offers[0], dict):
                market_blocks: List[Tuple[Optional[dict], List[dict]]] = [(None, offers)]  # type: ignore
            elif offers and isinstance(offers, list) and offers and isinstance(offers[0], list):
                if len(events) == len(offers):
                    market_blocks = [(events[i], offers[i]) for i in range(len(offers))]
                else:
                    market_blocks = [(None, block) for block in offers if isinstance(block, list)]
            else:
                continue

            for event_obj, markets in market_blocks:
                if not isinstance(markets, list):
                    continue
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    mlabel = (market.get("label") or market.get("name") or "").strip()
                    event_id = ""
                    start_time = ""
                    away_abbr, home_abbr = "", ""
                    if event_obj:
                        event_id = str(event_obj.get("eventId") or "")
                        start_time = str(event_obj.get("startEventDate") or "").strip()
                        away_abbr, home_abbr = _parse_teams_from_event_name(
                            str(event_obj.get("name") or "")
                        )
                    if not event_id:
                        event_id = str(market.get("eventId") or market.get("event_id") or "")
                    if event_id and (not start_time or not away_abbr):
                        ev = events_by_id.get(event_id) or {}
                        if not start_time:
                            start_time = str(ev.get("startEventDate") or "").strip()
                        if not away_abbr:
                            away_abbr, home_abbr = _parse_teams_from_event_name(str(ev.get("name") or ""))

                    for out in market.get("outcomes") or []:
                        if not isinstance(out, dict):
                            continue
                        sel = (out.get("label") or "").strip()
                        player = _participant_name(out, mlabel)
                        line_val = out.get("line")
                        try:
                            line_num = float(line_val) if line_val is not None and str(line_val).strip() != "" else float("nan")
                        except (TypeError, ValueError):
                            line_num = float("nan")

                        prop = f"{category_name} / {sn} / {mlabel}".strip(" /") if mlabel else f"{category_name} / {sn}"

                        pid_src = "|".join(
                            [
                                event_id,
                                category_name,
                                sn,
                                mlabel,
                                player,
                                str(line_val),
                                sel,
                            ]
                        )
                        pid = hashlib.sha256(pid_src.encode("utf-8")).hexdigest()[:20]

                        team_guess = ""
                        opp_guess = ""
                        if player and "(" in player and ")" in player:
                            m = re.search(r"\(([^)]+)\)\s*$", player)
                            if m:
                                team_guess = _norm_abbr(m.group(1))
                                player = player[: m.start()].strip()

                        rows.append(
                            {
                                "projection_id": pid,
                                "pp_projection_id": pid,
                                "player_id": "",
                                "pp_game_id": event_id,
                                "start_time": start_time,
                                "player": player,
                                "pos": "",
                                "team": team_guess,
                                "opp_team": opp_guess,
                                "prop_type": prop,
                                "line": line_num,
                                "pick_type": "Standard",
                                "pp_home_team": home_abbr,
                                "pp_away_team": away_abbr,
                                "image_url": "",
                                "source_book": "draftkings",
                                "dk_event_id": event_id,
                                "dk_category": category_name,
                                "dk_subcategory": sn,
                                "dk_market_label": mlabel,
                                "dk_selection_label": sel,
                                "dk_american_odds": _american_odds(out),
                            }
                        )
    return rows


def run_fetch(
    *,
    event_group_id: str,
    site_code: str,
    categories: Optional[List[str]],
    auto_categories: bool,
    list_only: bool,
    retries: int,
) -> Tuple[List[dict], int]:
    session = _session(site_code)
    base = _base_url(site_code, event_group_id)
    root_url = f"{base}?format=json"
    print(f"[dk] GET {root_url[:90]}...")
    root = _get_json(session, root_url, retries=retries)
    eg = root.get("eventGroup") or {}
    offer_categories = [c for c in (eg.get("offerCategories") or []) if isinstance(c, dict)]

    if list_only:
        print("[dk] offerCategories:")
        for c in offer_categories:
            print(f"  {_category_id(c):>8}  {c.get('name')}")
        return [], 0

    if categories:
        targets = _find_categories_by_name(offer_categories, categories)
    elif auto_categories:
        targets = _auto_pick_categories(offer_categories)
        print(f"[dk] auto-selected {len(targets)} categories (hints + blocklist)")
    else:
        targets = list(offer_categories)
        print(f"[dk] using all {len(targets)} categories (heavy)")

    events_by_id = _event_lookup(root)
    all_rows: List[dict] = []

    for cat in targets:
        cid = _category_id(cat)
        cname = (cat.get("name") or "").strip()
        if not cid:
            print(f"[warn] skip category without id: {cname!r}", file=sys.stderr)
            continue
        cat_url = f"{base}/categories/{cid}?format=json"
        print(f"[dk] category {cname!r} ({cid})")
        try:
            cat_json = _get_json(session, cat_url, retries=retries)
        except Exception as e:
            print(f"[warn] category fetch failed: {e}", file=sys.stderr)
            continue

        cat_eg = cat_json.get("eventGroup") or {}
        descriptors: List[dict] = []
        for c2 in cat_eg.get("offerCategories") or []:
            if isinstance(c2, dict):
                for d in c2.get("offerSubcategoryDescriptors") or []:
                    if isinstance(d, dict):
                        descriptors.append(d)
        if not descriptors:
            for _, d in _iter_category_descriptors(cat_eg):
                descriptors.append(d)

        for desc in descriptors:
            sid = _subcategory_id(desc)
            sname = (desc.get("name") or "").strip()
            if not sid:
                continue
            sub_url = f"{base}/categories/{cid}/subcategories/{sid}?format=json"
            try:
                sub_json = _get_json(session, sub_url, retries=retries)
            except Exception as e:
                print(f"[warn] subcategory {sname!r} ({sid}): {e}", file=sys.stderr)
                continue
            rows = _flatten_offers_for_subcategory(
                sub_json,
                category_name=cname,
                sub_name=sname,
                events_by_id=events_by_id,
            )
            all_rows.extend(rows)
            print(f"    + {sname!r} -> {len(rows)} selections")

    return all_rows, len(targets)


def main() -> None:
    ap = argparse.ArgumentParser(description="DraftKings sportsbook player props -> PP-shaped CSV")
    ap.add_argument("--league", default="nba", help=f"Preset: {', '.join(sorted(EVENT_GROUP_IDS))} or use --event-group-id")
    ap.add_argument("--event-group-id", default="", help="Override numeric event group id (from DK league URL)")
    ap.add_argument("--site-code", default="US-SB", help="DraftKings site fragment, e.g. US-SB, US-NJ-SB")
    ap.add_argument(
        "--categories",
        nargs="*",
        default=None,
        help="Offer category names (exact or substring). If omitted, player-prop-like categories are auto-selected.",
    )
    ap.add_argument(
        "--all-categories",
        action="store_true",
        help="Fetch every offer category (slow; includes game lines and futures).",
    )
    ap.add_argument("--list-categories", action="store_true", help="Print category ids/names from the league root and exit")
    ap.add_argument("--output", "-o", default="step1_draftkings_player_props.csv")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--raw-json", default="", help="Optional path to dump the root eventgroup JSON")
    args = ap.parse_args()

    league_key = args.league.strip().lower()
    eg_id = (args.event_group_id or "").strip()
    if not eg_id:
        eg_id = EVENT_GROUP_IDS.get(league_key, "")
    if not eg_id:
        print(
            f"[err] Unknown league {args.league!r} - set --event-group-id from the DK league URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.list_categories:
        run_fetch(
            event_group_id=eg_id,
            site_code=args.site_code,
            categories=None,
            auto_categories=False,
            list_only=True,
            retries=args.retries,
        )
        sys.exit(0)

    if args.all_categories:
        cat_mode = None
        auto = False
        all_cats = True
    elif args.categories is not None and len(args.categories) > 0:
        cat_mode = list(args.categories)
        auto = False
        all_cats = False
    else:
        cat_mode = None
        auto = True
        all_cats = False

    try:
        if all_cats:
            rows, _ncat = run_fetch(
                event_group_id=eg_id,
                site_code=args.site_code,
                categories=None,
                auto_categories=False,
                list_only=False,
                retries=args.retries,
            )
        else:
            rows, _ncat = run_fetch(
                event_group_id=eg_id,
                site_code=args.site_code,
                categories=cat_mode,
                auto_categories=auto,
                list_only=False,
                retries=args.retries,
            )
    except RuntimeError as e:
        print(f"[err] {e}", file=sys.stderr)
        pd.DataFrame(columns=DK_OUTPUT_COLUMNS).to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    if args.raw_json:
        try:
            session = _session(args.site_code)
            root = _get_json(session, f"{_base_url(args.site_code, eg_id)}?format=json", retries=args.retries)
            with open(args.raw_json, "w", encoding="utf-8") as f:
                json.dump(root, f, ensure_ascii=False)
            print(f"[dk] Wrote root JSON -> {args.raw_json}")
        except OSError as e:
            print(f"[warn] raw-json: {e}", file=sys.stderr)

    df = pd.DataFrame(rows)
    if df.empty:
        print("[warn] No prop rows parsed — board may be empty or JSON shape changed.")
        df = pd.DataFrame(columns=DK_OUTPUT_COLUMNS)
    else:
        df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
        df = df[DK_OUTPUT_COLUMNS]

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"[ok] {len(df)} rows -> {args.output}")


if __name__ == "__main__":
    main()
