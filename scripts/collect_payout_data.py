#!/usr/bin/env python3
"""
Collect exact PrizePicks payout samples from a logged-in Chrome CDP session.

Read-only: builds/clears slips and reads multipliers; never submits entries.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from difflib import get_close_matches
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "payout_samples"
DEBUG_DIR = ROOT / "data" / "debug"
_LOOKUP_DIAG_PRINTED = False
_POPULAR_READY = False


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    m = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in m:
            return m[n.lower()]
    return None


def _line_key(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except Exception:
        return ""


def load_nba_legs(top_n: int = 30) -> list[dict]:
    step8 = ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx"
    step1 = ROOT / "NBA" / "data" / "outputs" / "step1_pp_props_today.csv"
    if not step8.exists() or not step1.exists():
        raise FileNotFoundError("Missing NBA step8/step1 files.")

    xls = pd.ExcelFile(step8)
    sh = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    df8 = pd.read_excel(step8, sheet_name=sh)
    df1 = pd.read_csv(step1, low_memory=False)

    p8 = _pick_col(df8, ["player"])
    team8 = _pick_col(df8, ["team"])
    prop8 = _pick_col(df8, ["prop_type", "prop"])
    line8 = _pick_col(df8, ["line"])
    dir8 = _pick_col(df8, ["direction", "final_bet_direction"])
    tier8 = _pick_col(df8, ["tier"])
    blend8 = _pick_col(df8, ["blended_score", "blended score"])
    pick8 = _pick_col(df8, ["pick_type"])
    proj8 = _pick_col(df8, ["projection_id", "pp_projection_id"])
    req = [p8, prop8, line8, dir8, tier8, blend8]
    if any(x is None for x in req):
        raise RuntimeError("NBA step8 missing required columns for sample build.")

    p1 = _pick_col(df1, ["player"])
    team1 = _pick_col(df1, ["team"])
    prop1 = _pick_col(df1, ["prop_type", "prop"])
    line1 = _pick_col(df1, ["line"])
    pick1 = _pick_col(df1, ["pick_type"])
    proj1 = _pick_col(df1, ["projection_id", "pp_projection_id"])
    if any(x is None for x in [p1, prop1, line1, proj1]):
        raise RuntimeError("NBA step1 missing required columns for pp_id mapping.")

    idx: dict[tuple[str, str, str, str], dict] = {}
    for _, r in df1.iterrows():
        key = (
            _norm(r.get(p1)),
            _norm(r.get(prop1)),
            _line_key(r.get(line1)),
            _norm(r.get(team1)) if team1 else "",
        )
        idx[key] = {
            "projection_id": str(r.get(proj1, "") or "").strip(),
            "pick_type": str(r.get(pick1, "Standard") or "Standard"),
            "line": r.get(line1),
        }

    d = df8.copy()
    tier = d[tier8].astype(str).str.upper().str.strip()
    direction = d[dir8].astype(str).str.upper().str.strip()
    blend = pd.to_numeric(d[blend8], errors="coerce")
    d = d[tier.isin(["A", "B", "C"]) & direction.ne("") & blend.notna()].copy()
    d["__blend"] = blend.loc[d.index]
    d = d.sort_values("__blend", ascending=False).head(top_n)

    out: list[dict] = []
    for _, r in d.iterrows():
        player = str(r.get(p8, "") or "").strip()
        prop = str(r.get(prop8, "") or "").strip()
        team = str(r.get(team8, "") or "").strip() if team8 else ""
        line = r.get(line8)
        ddir = str(r.get(dir8, "") or "").strip().upper()
        proj = str(r.get(proj8, "") or "").strip() if proj8 else ""
        ptype = str(r.get(pick8, "") or "").strip()
        if not proj:
            k1 = (_norm(player), _norm(prop), _line_key(line), _norm(team))
            k2 = (_norm(player), _norm(prop), _line_key(line), "")
            m = idx.get(k1) or idx.get(k2)
            if m:
                proj = str(m.get("projection_id", "") or "").strip()
                if not ptype:
                    ptype = str(m.get("pick_type", "Standard"))
        if not proj:
            continue
        out.append(
            {
                "player": player,
                "sport": "NBA",
                "prop_type": prop,
                "line": float(line) if str(line).strip() != "" else None,
                "pick_type": str(ptype or "Standard").strip().lower(),
                "direction": ddir if ddir in ("OVER", "UNDER") else "OVER",
                "pp_id": proj,
                "team": team,
                "blended_score": float(r.get("__blend") or 0.0),
            }
        )
    return out


def connect_existing_browser(cdp_url: str):
    try:
        p = sync_playwright().start()
        browser = p.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("No contexts found in CDP Chrome.")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        for pg in context.pages:
            if "prizepicks" in (pg.url or "").lower():
                page = pg
                break
        return p, browser, context, page
    except Exception as e:
        print("Close Chrome completely, then run:")
        print("Windows: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' --remote-debugging-port=9222 --user-data-dir=C:\\chrome_debug")
        print("Then navigate to prizepicks.com and log in.")
        print("Then run this script.")
        raise RuntimeError(str(e))


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "").strip())[:80]


def _scroll_board_for_lazy_load(page):
    # Load additional projection cards before lookup.
    for _ in range(3):
        try:
            page.mouse.wheel(0, 4000)
        except Exception:
            pass
        page.wait_for_timeout(1000)


def find_prizepicks_frame(page):
    """Find the frame that contains the actual projection board content."""
    for frame in page.frames:
        try:
            text = frame.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText : ''")
            if any(x in text for x in ["Turnovers", "Points", "Assists", "Rebounds", "More", "Less", "Popular"]):
                print(f"[FRAME] Found content in frame: {frame.url}")
                return frame
        except Exception:
            pass
    print("[FRAME] Falling back to main page")
    return page


def ensure_popular_filter(frame, page):
    global _POPULAR_READY
    if _POPULAR_READY:
        return
    clicked = False
    try:
        frame.get_by_text("Popular", exact=True).first.click(timeout=1200)
        clicked = True
    except Exception:
        for sel in ["[data-testid='popular-filter']", "text=Popular"]:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=1200)
                    clicked = True
                    break
            except Exception:
                continue
    frame.wait_for_timeout(1500)
    _scroll_board_for_lazy_load(page)
    cards = _extract_cards_data_js(frame)
    print(f"[LOOKUP] Popular filter click: {'OK' if clicked else 'NOT FOUND'}")
    print(f"[LOOKUP] Cards visible after Popular click: {len(cards)}")
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        shot = DEBUG_DIR / f"after_popular_{ts}.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"[LOOKUP] Popular screenshot saved: {shot}")
    except Exception:
        pass
    _POPULAR_READY = True


def _extract_cards_data_js(page) -> list[dict]:
    return page.evaluate(
        """
        () => {
          const allElements = document.querySelectorAll('*');
          const cards = [];
          for (const el of allElements) {
            const text = (el.innerText || '').trim();
            if (!text) continue;
            if ((text.includes(' vs ') || text.includes(' @ '))
                && /\\d+\\.?\\d*/.test(text)
                && text.length < 200
                && text.length > 20) {
              const r = el.getBoundingClientRect();
              cards.push({
                text,
                tag: el.tagName,
                rect: {x: r.x, y: r.y, w: r.width, h: r.height}
              });
            }
          }
          const seen = new Set();
          return cards.filter(c => {
            if (seen.has(c.text)) return false;
            seen.add(c.text);
            return true;
          }).slice(0, 200);
        }
        """
    )


def _player_name_from_card_text(text: str) -> str | None:
    lines = [l.strip() for l in str(text or "").split("\n") if l.strip()]
    if len(lines) > 1:
        return lines[1]
    return None


def _collect_visible_players(frame) -> tuple[list[str], str | None, dict[str, int]]:
    selectors_to_try = [
        "[data-testid='player-name']",
        "[data-testid='projection-player-name']",
        ".player-name",
        ".projection-card .name",
        "[class*='PlayerName']",
        "[class*='player_name']",
    ]
    selector_counts: dict[str, int] = {}
    best_sel = None
    best_vals: list[str] = []
    for sel in selectors_to_try:
        vals: list[str] = []
        try:
            loc = frame.locator(sel)
            n = loc.count()
            for i in range(min(n, 300)):
                t = str(loc.nth(i).inner_text(timeout=200) or "").strip()
                if t:
                    vals.append(t)
            vals = list(dict.fromkeys(vals))
            selector_counts[sel] = len(vals)
            if len(vals) > len(best_vals):
                best_vals = vals
                best_sel = sel
        except Exception:
            selector_counts[sel] = 0
    # JS fallback for React/hashed classes.
    cards_data = _extract_cards_data_js(frame)
    js_players: list[str] = []
    for card in cards_data:
        nm = _player_name_from_card_text(card.get("text", ""))
        if nm:
            js_players.append(nm)
    js_players = list(dict.fromkeys(js_players))
    selector_counts["__js_card_text_parse__"] = len(js_players)
    if len(js_players) > len(best_vals):
        best_vals = js_players
        best_sel = "__js_card_text_parse__"
    # print raw card text sample for diagnosis
    if cards_data:
        print("[LOOKUP] JS card text samples:")
        for c in cards_data[:10]:
            print(f"  - {str(c.get('text',''))[:100]}")
    return best_vals, best_sel, selector_counts


def get_all_cards(frame) -> list[dict]:
    """Find cards by anchoring on 'More' buttons and parsing parent text."""
    cards: list[dict] = []
    try:
        more_loc = frame.get_by_text("More", exact=True)
        n = more_loc.count()
        print(f"[CARDS] Found {n} More buttons")
        for i in range(min(n, 300)):
            btn = more_loc.nth(i)
            try:
                card_text = btn.evaluate(
                    """
                    el => {
                      let p = el;
                      for (let i = 0; i < 5; i++) {
                        p = p ? p.parentElement : null;
                        if (!p) break;
                        const t = (p.innerText || '').trim();
                        if (t.length > 30 && t.length < 300 && (t.includes('vs ') || t.includes('@ '))) {
                          return t;
                        }
                      }
                      return null;
                    }
                    """
                )
                if not card_text:
                    continue
                lines = [l.strip() for l in str(card_text).split("\n") if l.strip()]
                if len(lines) < 3:
                    continue
                cards.append(
                    {
                        "player": lines[1] if len(lines) > 1 else lines[0],
                        "stat": lines[3] if len(lines) > 3 else "",
                        "game": lines[2] if len(lines) > 2 else "",
                        "more_btn": btn,
                        "card_text": card_text,
                    }
                )
            except Exception:
                continue
    except Exception as e:
        print(f"[CARDS] Error: {e}")
        return []
    print(f"[CARDS] Parsed {len(cards)} cards")
    for c in cards[:5]:
        print(f"  {c['player']} | {c['stat']} | {c['game']}")
    return cards


def _click_player_direction(frame, matched_name: str, direction: str, prop: str) -> bool:
    # Rebuild current cards each attempt to avoid stale elements.
    cards = get_all_cards(frame)
    lname = _norm(matched_name)
    lprop = _norm(prop)
    target = None
    for c in cards:
        if lname in _norm(c.get("player", "")):
            if lprop and lprop not in _norm(c.get("card_text", "")):
                continue
            target = c
            break
    if target is None:
        return False
    try:
        if str(direction).upper() == "OVER":
            target["more_btn"].click(timeout=900)
        else:
            try:
                target["more_btn"].locator("xpath=../..//button[contains(., 'Less')]").first.click(timeout=900)
            except Exception:
                frame.get_by_text("Less", exact=True).first.click(timeout=900)
        frame.wait_for_timeout(500)
        return True
    except Exception as e:
        print(f"[CLICK] Failed: {e}")
        return False


def extract_number(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def extract_multiplier_from_any(value: Any) -> float | None:
    if isinstance(value, dict):
        for k, v in value.items():
            kl = str(k).lower()
            if any(x in kl for x in ("payout_multiplier", "winning_multiplier", "multiplier", "payout", "odds")):
                m = extract_multiplier_from_any(v)
                if m is not None:
                    return m
            m = extract_multiplier_from_any(v)
            if m is not None:
                return m
    elif isinstance(value, list):
        for v in value:
            m = extract_multiplier_from_any(v)
            if m is not None:
                return m
    elif isinstance(value, (int, float)):
        f = float(value)
        if f > 1:
            return f
    elif isinstance(value, str):
        m = re.search(r"(\d+(?:\.\d+)?)\s*x", value.lower())
        if m:
            return float(m.group(1))
    return None


def clear_slip(page):
    try:
        for txt in ["Clear All", "Clear", "Remove All"]:
            b = page.get_by_text(txt, exact=False).first
            if b.count() > 0:
                try:
                    b.click(timeout=500)
                except Exception:
                    pass
        for sel in ["[aria-label*='Remove']", "[aria-label*='Close']", "[data-testid*='remove']"]:
            btns = page.locator(sel)
            n = min(btns.count(), 20)
            for _ in range(n):
                try:
                    btns.nth(0).click(timeout=300)
                except Exception:
                    break
        page.wait_for_timeout(200)
    except Exception:
        pass


def set_ticket_type(page, ticket_type: str):
    if ticket_type == "flex":
        labels = ["Flex Play", "Flex", "Flex entry"]
    else:
        labels = ["Power Play", "Power", "Power entry"]
    for t in labels:
        try:
            b = page.get_by_text(t, exact=False).first
            if b.count() > 0:
                b.click(timeout=800)
                page.wait_for_timeout(150)
                return
        except Exception:
            continue


def add_leg(frame, page, leg: dict) -> bool:
    global _LOOKUP_DIAG_PRINTED
    player = leg["player"]
    prop = leg["prop_type"]
    direction = str(leg["direction"]).upper()
    try:
        ensure_popular_filter(frame, page)
        _scroll_board_for_lazy_load(page)
        visible_players, best_sel, sel_counts = _collect_visible_players(frame)
        if not _LOOKUP_DIAG_PRINTED:
            print("[LOOKUP] Card selector counts:")
            for sel, n in sel_counts.items():
                print(f"  {sel}: {n}")
            print(f"[LOOKUP] Using selector: {best_sel or '(none)'}")
            print("[LOOKUP] First 5 visible players:")
            for nm in visible_players[:5]:
                print(f"  - {nm}")
            _LOOKUP_DIAG_PRINTED = True

        print(f"[LOOKUP] Target player text: {player}")
        print(f"[LOOKUP] Target prop text: {prop}")
        print(f"[LOOKUP] Target direction text: {direction}")

        # Search player first when search exists.
        for sel in ["input[placeholder*='Search']", "input[type='search']", "input[aria-label*='Search']"]:
            box = frame.locator(sel).first
            if box.count() > 0:
                try:
                    print(f"[LOOKUP] Using search selector: {sel}")
                    box.click(timeout=500)
                    box.fill(player, timeout=1200)
                    page.wait_for_timeout(250)
                    break
                except Exception:
                    continue
        _scroll_board_for_lazy_load(page)
        visible_players2, _, _ = _collect_visible_players(frame)
        visible_pool = visible_players2 or visible_players
        match = get_close_matches(player, visible_pool, n=1, cutoff=0.7)
        matched_name = match[0] if match else None
        if matched_name:
            print(f"[LOOKUP] Fuzzy matched '{player}' -> '{matched_name}'")
            if _click_player_direction(frame, matched_name, direction, prop):
                return True

        print(f"[PAYOUT] SKIP: {player} not found on board")
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            shot = DEBUG_DIR / f"lookup_fail_{_safe_name(player)}_{ts}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"[LOOKUP] Failure screenshot saved: {shot}")
        except Exception as se:
            print(f"[LOOKUP] Screenshot failed: {se}")
        return False
    except Exception:
        print(f"[PAYOUT] SKIP: {player} not found on board")
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            shot = DEBUG_DIR / f"lookup_fail_{_safe_name(player)}_{ts}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"[LOOKUP] Failure screenshot saved: {shot}")
        except Exception:
            pass
        return False


def read_payout_from_dom(frame) -> tuple[float | None, float | None, float | None]:
    # Returns (displayed_multiplier, flex_first_place, flex_miss_1)
    displayed = None
    for sel in [
        "[data-testid='multiplier']",
        "[data-testid='payout-multiplier']",
        ".payout .multiplier",
        ".entry-payout",
    ]:
        try:
            txt = frame.locator(sel).first.inner_text(timeout=600)
            m = re.search(r"(\d+(?:\.\d+)?)\s*x", txt.lower())
            if m:
                displayed = float(m.group(1))
                break
        except Exception:
            continue
    if displayed is None:
        try:
            t = frame.locator("text=/\\d+\\.?\\d*x/i").first
            if t.count() > 0:
                m = re.search(r"(\d+(?:\.\d+)?)\s*x", t.inner_text(timeout=600).lower())
                if m:
                    displayed = float(m.group(1))
        except Exception:
            pass

    flex_first = None
    flex_miss1 = None
    try:
        txt = frame.content()
        m1 = re.search(r"1st\s*place\s*pays[^0-9]*(\d+(?:\.\d+)?)\s*x", txt, flags=re.I)
        if m1:
            flex_first = float(m1.group(1))
        m2 = re.search(r"(?:\d+\s*out\s*of\s*\d+|miss\s*1)[^0-9]*(\d+(?:\.\d+)?)\s*x", txt, flags=re.I)
        if m2:
            flex_miss1 = float(m2.group(1))
    except Exception:
        pass
    try:
        slip_text = frame.evaluate(
            """
            () => {
              const all = document.querySelectorAll('*');
              for (const el of all) {
                const t = (el.innerText || '').trim();
                if (t.includes('To Win') && t.length < 200) return t;
              }
              return null;
            }
            """
        )
        print(f"[LOOKUP] Slip panel text: {slip_text}")
    except Exception:
        pass
    try:
        mult_candidates = frame.evaluate(
            """
            () => {
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              const out = [];
              let node;
              while (node = walker.nextNode()) {
                const t = (node.textContent || '').trim();
                if (/^\\d+\\.?\\d*x$/.test(t)) out.push(t);
              }
              return out.slice(0, 20);
            }
            """
        )
        print(f"[LOOKUP] Multiplier candidates: {mult_candidates}")
    except Exception:
        pass
    return displayed, flex_first, flex_miss1


def read_to_win_amount(frame) -> float | None:
    try:
        txt = frame.content()
        m = re.search(r"to\s*win[^0-9$]*\$?\s*([0-9][0-9,]*(?:\.\d+)?)", txt, flags=re.I)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception:
        pass
    return None


def build_standard_line_map(legs: list[dict]) -> dict[tuple[str, str], float]:
    mp: dict[tuple[str, str], float] = {}
    for leg in legs:
        if "standard" in str(leg.get("pick_type", "")).lower() and leg.get("line") is not None:
            mp[(_norm(leg.get("player")), _norm(leg.get("prop_type")))] = float(leg["line"])
    return mp


def choose_leg_sets(legs: list[dict], ticket_type: str) -> list[list[dict]]:
    # Build requested matrix using today's available props.
    std = [x for x in legs if "standard" in x["pick_type"]]
    gob = [x for x in legs if "goblin" in x["pick_type"]]
    dem = [x for x in legs if "demon" in x["pick_type"]]
    std = sorted(std, key=lambda x: -x.get("blended_score", 0))
    gob = sorted(gob, key=lambda x: -x.get("blended_score", 0))
    dem = sorted(dem, key=lambda x: -x.get("blended_score", 0))

    def _pick(pool: list[dict], n: int, used: set[str]) -> list[dict] | None:
        out = []
        for leg in pool:
            key = str(leg["pp_id"])
            pname = _norm(leg["player"])
            if key in used:
                continue
            if pname in { _norm(x["player"]) for x in out }:
                continue
            out.append(leg)
            used.add(key)
            if len(out) == n:
                return out
        return None

    patterns = [
        ("power", 2, 0, 0),
        ("power", 3, 0, 0),
        ("power", 4, 0, 0),
        ("power", 5, 0, 0),
        ("power", 2, 1, 0),
        ("power", 3, 1, 0),
        ("power", 3, 2, 0),
        ("power", 3, 3, 0),
        ("power", 4, 1, 0),
        ("power", 4, 2, 0),
        ("power", 4, 4, 0),
        ("power", 2, 0, 1),
        ("power", 3, 0, 1),
        ("power", 3, 0, 2),
        ("power", 4, 0, 1),
        ("power", 4, 0, 2),
        ("power", 3, 1, 1),
        ("power", 4, 1, 1),
        ("power", 4, 2, 1),
        ("flex", 2, 0, 0),
        ("flex", 3, 0, 0),
        ("flex", 4, 0, 0),
        ("flex", 2, 1, 0),
        ("flex", 3, 1, 0),
        ("flex", 3, 2, 0),
        ("flex", 4, 1, 0),
        ("flex", 4, 2, 0),
    ]

    selected: list[list[dict]] = []
    for ttype, n_legs, n_g, n_d in patterns:
        if ttype != ticket_type:
            continue
        if n_g > len(gob) or n_d > len(dem):
            continue
        n_s = n_legs - n_g - n_d
        if n_s < 0:
            continue
        used: set[str] = set()
        pick_g = _pick(gob, n_g, used) if n_g > 0 else []
        if n_g > 0 and not pick_g:
            continue
        pick_d = _pick(dem, n_d, used) if n_d > 0 else []
        if n_d > 0 and not pick_d:
            continue
        pick_s = _pick(std, n_s, used) if n_s > 0 else []
        if n_s > 0 and not pick_s:
            continue
        combo = (pick_g or []) + (pick_d or []) + (pick_s or [])
        if len(combo) == n_legs:
            selected.append(combo)
    return selected


def append_rows_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp-url", default="http://localhost:9222")
    ap.add_argument("--entry-amount", type=float, default=1.0)
    ap.add_argument("--max-cases", type=int, default=60)
    ap.add_argument("--delay-sec", type=float, default=0.5)
    args = ap.parse_args()

    legs = load_nba_legs(top_n=40)
    if len(legs) < 5:
        raise RuntimeError("Not enough NBA candidate legs to build test matrix.")
    std_line_map = build_standard_line_map(legs)

    p, browser, context, page = connect_existing_browser(args.cdp_url)
    page.wait_for_timeout(500)
    captures: dict[str, float] = {}
    current_key = {"value": ""}

    def on_response(resp):
        if "entries" in resp.url or "entry" in resp.url:
            try:
                body = resp.json()
                mult = extract_multiplier_from_any(body)
                if mult is not None and current_key["value"]:
                    captures[current_key["value"]] = mult
            except Exception:
                pass

    page.on("response", on_response)

    all_cases = choose_leg_sets(legs, "power") + choose_leg_sets(legs, "flex")
    all_cases = all_cases[: max(1, int(args.max_cases))]
    out_rows: list[dict] = []
    ts_now = datetime.utcnow().isoformat()

    try:
        for combo in all_cases:
            clear_slip(page)
            # set ticket type based on case label (inferred by count and pattern attempt)
            # if 2-5 only, we toggle both while collecting from matrix order: first power then flex
            frame = find_prizepicks_frame(page)
            ticket_type = "flex" if combo in all_cases[len(choose_leg_sets(legs, "power")):] else "power"
            set_ticket_type(frame, ticket_type)

            ok = True
            for leg in combo:
                if not add_leg(frame, page, leg):
                    ok = False
                    break
            if not ok:
                clear_slip(page)
                time.sleep(max(float(args.delay_sec), 0.5))
                continue

            key = "|".join(sorted(str(x["pp_id"]) for x in combo))
            current_key["value"] = key
            displayed_multiplier, flex_first, flex_miss_1 = read_payout_from_dom(frame)
            if displayed_multiplier is None:
                displayed_multiplier = captures.get(key)
            to_win_amount = read_to_win_amount(frame)

            n_g = sum(1 for x in combo if "goblin" in x["pick_type"])
            n_d = sum(1 for x in combo if "demon" in x["pick_type"])
            n_s = sum(1 for x in combo if "standard" in x["pick_type"])
            legs_payload = []
            for leg in combo:
                std_line = std_line_map.get((_norm(leg["player"]), _norm(leg["prop_type"])))
                dist = None
                if std_line is not None and leg.get("line") is not None:
                    dist = abs(float(leg["line"]) - float(std_line))
                legs_payload.append(
                    {
                        "player": leg["player"],
                        "prop_type": leg["prop_type"],
                        "line": leg["line"],
                        "pick_type": leg["pick_type"],
                        "direction": leg["direction"].lower(),
                        "pp_id": leg["pp_id"],
                        "standard_line": std_line,
                        "line_distance": dist,
                    }
                )

            out_rows.append(
                {
                    "timestamp": ts_now,
                    "ticket_type": ticket_type,
                    "n_legs": len(combo),
                    "legs": json.dumps(legs_payload, ensure_ascii=False),
                    "n_goblins": n_g,
                    "n_demons": n_d,
                    "n_standard": n_s,
                    "displayed_multiplier": displayed_multiplier,
                    "flex_first_place": flex_first,
                    "flex_miss_1": flex_miss_1,
                    "entry_amount": float(args.entry_amount),
                    "to_win_amount": to_win_amount,
                }
            )
            clear_slip(page)
            time.sleep(max(float(args.delay_sec), 0.5))
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    date_tag = datetime.utcnow().strftime("%Y-%m-%d")
    out_csv = SAMPLES_DIR / f"payout_log_{date_tag}.csv"
    append_rows_csv(out_csv, out_rows)
    print(f"[PAYOUT] Collected samples: {len(out_rows)}")
    print(f"[PAYOUT] Saved -> {out_csv}")


if __name__ == "__main__":
    main()

