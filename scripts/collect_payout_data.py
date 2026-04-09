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
    """
    Anchor on More buttons and parse player/stat details from ancestor text.
    """
    cards: list[dict] = []
    try:
        import re as _re
        more_loc = frame.get_by_text("More")
        n = more_loc.count()
        print(f"[CARDS] Found {n} More buttons")
        debug_unparsed = 0
        for i in range(min(n, 200)):
            btn = more_loc.nth(i)
            try:
                card_info = btn.evaluate(
                    """
                    el => {
                      let p = el;
                      let best = null;
                      for (let i = 0; i < 10; i++) {
                        p = p ? p.parentElement : null;
                        if (!p) break;
                        const t = (p.innerText || '');
                        const hasGame = /\\s(vs|@)\\s/i.test(t);
                        const hasStat = /\\b\\d+(?:\\.\\d+)?\\s*[A-Za-z]/.test(t);
                        const hasMore = /\\bMore\\b/.test(t);
                        if (hasMore && hasStat && hasGame) {
                          best = p;
                          break;
                        }
                      }
                      if (!best) return null;
                      return {
                        text: best.innerText || '',
                        html: (best.innerHTML || '').slice(0, 1200)
                      };
                    }
                    """
                )
                if not card_info or not card_info.get("text"):
                    continue
                text = str(card_info["text"])
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) < 3:
                    continue
                player_name = None
                prop_type = "unknown"
                line_value = None
                stat_line = None
                game_idx = -1
                for idx, line in enumerate(lines):
                    if " vs " in line.lower() or " @ " in line.lower():
                        game_idx = idx
                        break
                if game_idx > 0:
                    for k in range(game_idx - 1, -1, -1):
                        candidate = lines[k]
                        if candidate not in ["More", "Less"] and len(candidate) > 2 and not _re.match(r"^[A-Z]{2,3}\s*[-–]", candidate):
                            player_name = candidate
                            break
                if player_name is None:
                    for line in lines:
                        if (
                            not _re.match(r"^[\d\.]+", line)
                            and line not in ["More", "Less", "More Less"]
                            and len(line) > 3
                            and not _re.match(r"^[A-Z]{2,3}\s*[-–]", line)
                        ):
                            player_name = line
                            break
                for line in lines:
                    stat_match = _re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(.+)$", line)
                    if stat_match:
                        line_value = float(stat_match.group(1))
                        prop_type = stat_match.group(2).strip()
                        stat_line = line
                html = str(card_info.get("html", ""))
                pick_type = "standard"
                if "goblin" in html.lower() or "goblin" in text.lower():
                    pick_type = "goblin"
                elif "demon" in html.lower() or "demon" in text.lower():
                    pick_type = "demon"
                if player_name and line_value is not None:
                    cards.append(
                        {
                            "player": player_name,
                            "prop_type": prop_type if stat_line else "unknown",
                            "line": line_value,
                            "pick_type": pick_type,
                            "more_btn": btn,
                            "raw_text": text[:200],
                        }
                    )
                elif debug_unparsed < 5:
                    debug_unparsed += 1
                    print(f"[CARDS][UNPARSED] sample {debug_unparsed}: {' | '.join(lines[:6])}")
            except Exception:
                continue
    except Exception as e:
        print(f"[CARDS] Error: {e}")
        return []
    print(f"[CARDS] Parsed {len(cards)} cards")
    for c in cards[:5]:
        print(f"  {c['player']} | {c['line']} {c['prop_type']} | {c['pick_type']}")
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


def click_leg(frame, card: dict, direction: str) -> bool:
    try:
        if direction.upper() in ["OVER", "MORE"]:
            card["more_btn"].click(timeout=1200)
        else:
            found_less = card["more_btn"].evaluate(
                """
                el => {
                  let p = el;
                  for (let i = 0; i < 4; i++) p = p?.parentElement;
                  if (!p) return false;
                  const btns = p.querySelectorAll('button');
                  for (const b of btns) {
                    if ((b.innerText || '').trim() === 'Less') { b.click(); return true; }
                  }
                  return false;
                }
                """
            )
            if not found_less:
                frame.get_by_text("Less").nth(0).click(timeout=1200)
        frame.wait_for_timeout(400)
        return True
    except Exception as e:
        print(f"[CLICK] {card.get('player', '?')} failed: {e}")
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


def clear_slip(frame):
    try:
        for txt in ["Clear", "Clear All", "Remove All"]:
            b = frame.get_by_text(txt, exact=False).first
            if b.count() > 0:
                try:
                    b.click(timeout=500)
                    frame.wait_for_timeout(600)
                    print("[SLIP] Cleared")
                    return
                except Exception:
                    pass
        for sel in [
            "button[aria-label*='remove']",
            "button[aria-label*='delete']",
            "button[aria-label*='clear']",
            "[aria-label*='Close']",
            "[data-testid*='remove']",
        ]:
            btns = frame.locator(sel)
            n = min(btns.count(), 20)
            for _ in range(n):
                try:
                    btns.nth(0).click(timeout=300)
                    frame.wait_for_timeout(250)
                except Exception:
                    break
    except Exception:
        pass


MIN_SAMPLES = {
    "all_standard": 8,
    "has_goblin": 10,
    "has_demon": 5,
    "flex": 5,
}


def dismiss_modal(frame, page) -> bool:
    dismissed = False
    try:
        for sel in [".MuiBackdrop-root", "[class*='MuiBackdrop']"]:
            bd = frame.locator(sel).first
            if bd.count() > 0:
                try:
                    bd.click(force=True, timeout=600)
                    frame.wait_for_timeout(400)
                    print("[MODAL] Dismissed backdrop (force)")
                    dismissed = True
                except Exception:
                    pass
    except Exception:
        pass
    try:
        backdrop = frame.locator(
            "[class*='MuiBackdrop'], [class*='backdrop'], "
            "[class*='modal'], [class*='Modal'], "
            "[class*='overlay'], [class*='Overlay']"
        ).first
        if backdrop.count() > 0 and backdrop.is_visible():
            backdrop.click(force=True, timeout=800)
            frame.wait_for_timeout(500)
            print("[MODAL] Dismissed backdrop")
            dismissed = True
    except Exception:
        pass
    try:
        for _ in range(2):
            page.keyboard.press("Escape")
            frame.wait_for_timeout(200)
    except Exception:
        pass
    for label in ["Close", "Got it", "OK", "Dismiss", "×", "✕"]:
        try:
            loc = frame.get_by_text(label, exact=True).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=600)
                frame.wait_for_timeout(300)
                print(f"[MODAL] Dismissed via '{label}' button")
                return True
        except Exception:
            continue
    return dismissed


def _card_unique_key(c: dict) -> str:
    return (
        f"{_norm(c.get('player'))}|{_norm(c.get('prop_type'))}|"
        f"{_line_key(c.get('line'))}|{c.get('pick_type', '')}"
    )


def _is_valid_board_card(c: dict) -> bool:
    p = str(c.get("player", "") or "")
    if len(p) < 2 or len(p) > 55:
        return False
    lo = p.lower()
    if any(
        x in lo
        for x in (
            "learn more",
            "help center",
            "how to play",
            "scoring chart",
        )
    ):
        return False
    if "demons & goblins" in lo and "indicate" in lo:
        return False
    return True


def expand_card_pool(frame, page) -> list[dict]:
    all_cards: list[dict] = []
    seen: set[str] = set()
    for _ in range(3):
        dismiss_modal(frame, page)
        frame.wait_for_timeout(150)
    filters = [
        "Popular",
        "Points",
        "Assists",
        "Rebounds",
        "Turnovers",
        "Steals",
        "3-PT Made",
    ]
    for filter_name in filters:
        try:
            dismiss_modal(frame, page)
            loc = frame.get_by_text(filter_name, exact=True).first
            if loc.count() == 0:
                loc = frame.get_by_text(filter_name, exact=False).first
            loc.click(force=True, timeout=1500)
            frame.wait_for_timeout(800)
            _scroll_board_for_lazy_load(page)
            cards = get_all_cards(frame)
            new = 0
            gobs = dens = 0
            for c in cards:
                if not _is_valid_board_card(c):
                    continue
                k = _card_unique_key(c)
                if k in seen:
                    continue
                seen.add(k)
                c2 = dict(c)
                c2["source_filter"] = filter_name
                all_cards.append(c2)
                new += 1
                if c2.get("pick_type") == "goblin":
                    gobs += 1
                elif c2.get("pick_type") == "demon":
                    dens += 1
            print(f"[FILTER] {filter_name}: +{new} unique cards ({gobs} goblins, {dens} demons)")
        except Exception as e:
            print(f"[FILTER] {filter_name}: skip ({e})")
    try:
        dismiss_modal(frame, page)
        loc = frame.get_by_text("Popular", exact=True).first
        if loc.count() == 0:
            loc = frame.get_by_text("Popular", exact=False).first
        loc.click(force=True, timeout=1500)
        frame.wait_for_timeout(500)
    except Exception:
        pass
    if not all_cards:
        dismiss_modal(frame, page)
        _scroll_board_for_lazy_load(page)
        for c in get_all_cards(frame):
            if not _is_valid_board_card(c):
                continue
            k = _card_unique_key(c)
            if k in seen:
                continue
            seen.add(k)
            c2 = dict(c)
            c2.setdefault("source_filter", "Popular")
            all_cards.append(c2)
        print("[POOL] expand_card_pool fallback: using single-view get_all_cards")
    print(f"[POOL] Total expanded: {len(all_cards)} cards")
    print(f"  Standard: {sum(1 for c in all_cards if c['pick_type'] == 'standard')}")
    print(f"  Goblin:   {sum(1 for c in all_cards if c['pick_type'] == 'goblin')}")
    print(f"  Demon:    {sum(1 for c in all_cards if c['pick_type'] == 'demon')}")
    return all_cards


def resolve_leg_card(template: dict, fresh: list[dict]) -> dict | None:
    nt = _norm(template.get("player"))
    nl = _line_key(template.get("line"))
    np = _norm(template.get("prop_type"))
    pt = template.get("pick_type")
    for c in fresh:
        if _norm(c.get("player")) != nt:
            continue
        if _line_key(c.get("line")) != nl:
            continue
        if _norm(c.get("prop_type")) != np:
            continue
        if c.get("pick_type") != pt:
            continue
        return c
    for c in fresh:
        if nt not in _norm(c.get("player")) and _norm(c.get("player")) not in nt:
            continue
        if _line_key(c.get("line")) != nl:
            continue
        if c.get("pick_type") != pt:
            continue
        return c
    return None


def click_case_legs_with_filter_switches(
    frame, page, tc: dict
) -> bool:
    """Switch stat filters as needed so each leg's More button is in the live DOM."""
    current_tab = None
    for leg in tc["legs"]:
        tab = str(leg["card"].get("source_filter") or "Popular")
        if tab != current_tab:
            try:
                dismiss_modal(frame, page)
                tloc = frame.get_by_text(tab, exact=True).first
                if tloc.count() == 0:
                    tloc = frame.get_by_text(tab, exact=False).first
                tloc.click(force=True, timeout=1500)
                frame.wait_for_timeout(800)
                _scroll_board_for_lazy_load(page)
            except Exception as e:
                print(f"[FILTER] Could not switch to {tab}: {e}")
                return False
            current_tab = tab
        dismiss_modal(frame, page)
        fresh = get_all_cards(frame)
        resolved = resolve_leg_card(leg["card"], fresh)
        if resolved is None:
            fresh = get_all_cards(frame)
            resolved = resolve_leg_card(leg["card"], fresh)
        if resolved is None:
            print(f"[CLICK] Could not resolve card for {leg['card'].get('player')}")
            return False
        if not click_leg(frame, resolved, leg["direction"]):
            return False
        frame.wait_for_timeout(300)
    return True


def case_target_buckets(tc: dict) -> set[str]:
    buckets: set[str] = set()
    if tc["ticket_type"] == "flex":
        buckets.add("flex")
    n_g = sum(1 for l in tc["legs"] if l["card"]["pick_type"] == "goblin")
    n_d = sum(1 for l in tc["legs"] if l["card"]["pick_type"] == "demon")
    if n_g > 0:
        buckets.add("has_goblin")
    elif n_d > 0:
        buckets.add("has_demon")
    else:
        buckets.add("all_standard")
    return buckets


def bucket_needs_fill(
    bucket: str,
    counts: dict[str, int],
    goblins_avail: bool,
    demons_avail: bool,
) -> bool:
    if bucket == "has_goblin" and not goblins_avail:
        return False
    if bucket == "has_demon" and not demons_avail:
        return False
    return counts[bucket] < MIN_SAMPLES[bucket]


def all_targets_met(
    counts: dict[str, int],
    goblins_avail: bool,
    demons_avail: bool,
) -> bool:
    if counts["all_standard"] < MIN_SAMPLES["all_standard"]:
        return False
    if counts["flex"] < MIN_SAMPLES["flex"]:
        return False
    if goblins_avail and counts["has_goblin"] < MIN_SAMPLES["has_goblin"]:
        return False
    if demons_avail and counts["has_demon"] < MIN_SAMPLES["has_demon"]:
        return False
    return True


def bump_counts_from_record(counts: dict[str, int], rec: dict) -> None:
    if str(rec.get("ticket_type", "")).lower() == "flex":
        counts["flex"] += 1
    n_g = int(rec.get("n_goblins", 0) or 0)
    n_d = int(rec.get("n_demons", 0) or 0)
    if n_g > 0:
        counts["has_goblin"] += 1
    elif n_d > 0:
        counts["has_demon"] += 1
    else:
        counts["all_standard"] += 1


def pick_next_test_case(
    cases: list[dict],
    counts: dict[str, int],
    cases_run: int,
    goblins_avail: bool,
    demons_avail: bool,
) -> dict | None:
    if not cases:
        return None
    for tc in cases:
        if any(
            bucket_needs_fill(b, counts, goblins_avail, demons_avail)
            for b in case_target_buckets(tc)
        ):
            return tc
    return cases[cases_run % len(cases)]


def build_payout_test_matrix(
    standard: list[dict], goblins: list[dict], demons: list[dict]
) -> list[dict]:
    cases: list[dict] = []
    for ticket_type in ("power", "flex"):
        for n in [2, 3, 4, 5]:
            if len(standard) >= n:
                cases.append({
                    "legs": [{"card": standard[i], "direction": "OVER"} for i in range(n)],
                    "ticket_type": ticket_type,
                    "label": f"{n}-leg all-{ticket_type} standard",
                })
        if len(goblins) >= 1 and len(standard) >= 1:
            for n_gob in [1, 2]:
                for total in [2, 3, 4]:
                    n_std = total - n_gob
                    if len(goblins) >= n_gob and len(standard) >= n_std and n_std >= 0:
                        legs_case = (
                            [{"card": goblins[i], "direction": "OVER"} for i in range(n_gob)]
                            + [{"card": standard[i], "direction": "OVER"} for i in range(n_std)]
                        )
                        cases.append({
                            "legs": legs_case,
                            "ticket_type": ticket_type,
                            "label": f"{total}-leg {n_gob}gob-{ticket_type} {n_std}std",
                        })
        if len(demons) >= 1 and len(standard) >= 1:
            for n_dem in [1, 2]:
                for total in [2, 3, 4]:
                    n_std = total - n_dem
                    if len(demons) >= n_dem and len(standard) >= n_std and n_std >= 0:
                        legs_case = (
                            [{"card": demons[i], "direction": "OVER"} for i in range(n_dem)]
                            + [{"card": standard[i], "direction": "OVER"} for i in range(n_std)]
                        )
                        cases.append({
                            "legs": legs_case,
                            "ticket_type": ticket_type,
                            "label": f"{total}-leg {n_dem}dem-{ticket_type} {n_std}std",
                        })
    return cases


def set_ticket_type(frame, ticket_type: str):
    if ticket_type == "flex":
        labels = ["Flex Play", "Flex", "Flex entry"]
    else:
        labels = ["Power Play", "Power", "Power entry"]
    for t in labels:
        try:
            b = frame.get_by_text(t, exact=False).first
            if b.count() > 0:
                b.click(timeout=800)
                frame.wait_for_timeout(150)
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


def read_slip(frame) -> dict:
    try:
        text = frame.evaluate("() => document.body.innerText")
        slip_start = text.find("Current Lineup")
        if slip_start == -1:
            slip_start = text.find("Players Selected")
        if slip_start == -1:
            slip_start = text.find("Power Play")
        slip_section = text[slip_start : slip_start + 600] if slip_start >= 0 else ""
        if slip_start >= 0:
            print("[SLIP RAW] Slip section:")
            print(slip_section)
            print("---")

        multipliers: list[str] = []
        multiplier_patterns = [
            r"(\d+\.?\d*)\s*x\b",
            r"(\d+\.?\d*)[Xx]",
            r"payout[:\s]+\$?(\d+\.?\d*)",
            r"win[s]?[:\s]+\$?(\d+\.?\d*)",
            r"\$(\d+\.?\d*)\s*prize",
            r"(\d+\.?\d*)\s*times",
        ]
        for pat in multiplier_patterns:
            found = re.findall(pat, slip_section, re.IGNORECASE)
            if found:
                print(f"[SLIP REGEX] Pattern '{pat}' found: {found}")
                for v in found:
                    sv = str(v).strip()
                    if sv:
                        multipliers.append(sv)
        multipliers = list(dict.fromkeys(multipliers))

        to_win_hits: list[str] = []
        to_win_patterns = [
            r"To\s*Win[\s\n]*\$?(\d+\.?\d+)",
            r"to\s*win[:\s\n]*\$?(\d+\.?\d+)",
            r"You\s*win[:\s\n]*\$?(\d+\.?\d+)",
            r"Prize[:\s\n]*\$?(\d+\.?\d+)",
            r"Payout[:\s\n]*\$?(\d+\.?\d+)",
            r"\$(\d+\.\d{2})",
        ]
        for pat in to_win_patterns:
            found = re.findall(pat, slip_section, re.IGNORECASE)
            if found:
                print(f"[SLIP REGEX] ToWin pattern '{pat}' found: {found}")
                for v in found:
                    sv = str(v).strip()
                    if sv:
                        to_win_hits.append(sv)
        to_win_clean = []
        for v in list(dict.fromkeys(to_win_hits)):
            try:
                fv = float(v)
                if fv > 0:
                    to_win_clean.append(fv)
            except Exception:
                continue
        to_win_num = to_win_clean[0] if to_win_clean else None

        n_selected = re.findall(r"(\d+)\s*Players?\s*Selected", slip_section, re.IGNORECASE)
        flex_first = re.findall(r"1st\s*place\s*pays[\s\n]*(\d+\.?\d*)[Xx]", slip_section, re.IGNORECASE)
        flex_correct_pays = re.findall(r"(\d+)\s*correct\s*pays[\s\n]*(\d+\.?\d*)[Xx]", slip_section, re.IGNORECASE)

        entry_amt: list[str] = []
        entry_patterns = [
            r"Entry[:\s\n]*\$?(\d+\.?\d+)",
            r"Entry\s*Fee[:\s\n]*\$?(\d+\.?\d+)",
            r"Wager[:\s\n]*\$?(\d+\.?\d+)",
        ]
        for pat in entry_patterns:
            found = re.findall(pat, slip_section, re.IGNORECASE)
            if found:
                print(f"[SLIP REGEX] Entry pattern '{pat}' found: {found}")
                for v in found:
                    sv = str(v).strip()
                    if sv:
                        entry_amt.append(sv)
        entry_amt = list(dict.fromkeys(entry_amt))
        entry_num = float(entry_amt[0]) if entry_amt else 10.0
        computed_mult = None
        if to_win_num is not None and entry_num > 0:
            computed_mult = round(to_win_num / entry_num, 3)
            print(f"[SLIP] Computed mult from towin/entry: {computed_mult}x")

        displayed_multiplier = None
        if multipliers:
            try:
                displayed_multiplier = float(multipliers[0])
            except Exception:
                displayed_multiplier = None
        if displayed_multiplier is None:
            displayed_multiplier = computed_mult

        slip = {
            "multipliers": multipliers,
            "displayed_multiplier": displayed_multiplier,
            "to_win": to_win_num,
            "n_selected": int(n_selected[0]) if n_selected else None,
            "flex_first_place": float(flex_first[0]) if flex_first else None,
            "flex_correct_pays": flex_correct_pays,
            "flex_miss_1": flex_correct_pays,
            "entry_amount": entry_num,
            "computed_multiplier": computed_mult,
            "has_slip": slip_start >= 0,
        }
        if slip["has_slip"]:
            print(
                f"[SLIP] n={slip['n_selected']} | mult={slip['multipliers']} | "
                f"displayed={slip['displayed_multiplier']} | towin={slip['to_win']} | "
                f"flex={slip['flex_first_place']}"
            )
        return slip
    except Exception as e:
        print(f"[SLIP] Read error: {e}")
        return {}


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
    ap.add_argument("--max-cases", type=int, default=100)
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

    out_rows: list[dict] = []
    ts_now = datetime.utcnow().isoformat()

    try:
        frame = find_prizepicks_frame(page)
        ensure_popular_filter(frame, page)
        dismiss_modal(frame, page)
        cards = expand_card_pool(frame, page)
        if not cards:
            print("[FATAL] No cards parsed — check board state")
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "fatal_no_cards.png"), full_page=True)
            return

        standard = [c for c in cards if c["pick_type"] == "standard"]
        goblins = [c for c in cards if c["pick_type"] == "goblin"]
        demons = [c for c in cards if c["pick_type"] == "demon"]
        goblins_avail = len(goblins) > 0
        demons_avail = len(demons) > 0
        print(f"[POOL] Standard={len(standard)} Goblin={len(goblins)} Demon={len(demons)}")

        test_cases = build_payout_test_matrix(standard, goblins, demons)
        print(f"[MATRIX] {len(test_cases)} test cases planned")

        max_cases = max(1, int(args.max_cases))
        counts = {k: 0 for k in MIN_SAMPLES}
        cases_run = 0
        test_idx = 0

        while cases_run < max_cases:
            if all_targets_met(counts, goblins_avail, demons_avail):
                print("[TARGETS] All MIN_SAMPLES satisfied.")
                break
            tc = pick_next_test_case(
                test_cases, counts, cases_run, goblins_avail, demons_avail
            )
            if tc is None:
                break
            test_idx += 1
            print(f"[TEST {test_idx}] (run {cases_run + 1}/{max_cases}) {tc['label']}")
            print(
                f"[TARGETS] std={counts['all_standard']}/{MIN_SAMPLES['all_standard']} "
                f"gob={counts['has_goblin']}/{MIN_SAMPLES['has_goblin']} "
                f"dem={counts['has_demon']}/{MIN_SAMPLES['has_demon']} "
                f"flex={counts['flex']}/{MIN_SAMPLES['flex']}"
            )
            try:
                dismiss_modal(frame, page)
                set_ticket_type(frame, tc["ticket_type"])
                clear_slip(frame)
                dismiss_modal(frame, page)
                ok = click_case_legs_with_filter_switches(frame, page, tc)
                if not ok:
                    print("  [SKIP] Leg click sequence failed")
                    clear_slip(frame)
                    dismiss_modal(frame, page)
                    cases_run += 1
                    continue
                frame.wait_for_timeout(1000)
                slip = read_slip(frame)
                if slip.get("has_slip"):
                    legs_payload = []
                    for leg in tc["legs"]:
                        c = leg["card"]
                        std_line = std_line_map.get((_norm(c["player"]), _norm(c["prop_type"])))
                        dist = abs(float(c["line"]) - float(std_line)) if std_line is not None else None
                        legs_payload.append({
                            "player": c["player"],
                            "prop_type": c["prop_type"],
                            "line": c["line"],
                            "pick_type": c["pick_type"],
                            "direction": leg["direction"].lower(),
                            "pp_id": "",
                            "standard_line": std_line,
                            "line_distance": dist,
                        })
                    rec = {
                        "timestamp": ts_now,
                        "ticket_type": tc["ticket_type"],
                        "n_legs": len(tc["legs"]),
                        "legs": json.dumps(legs_payload, ensure_ascii=False),
                        "n_goblins": sum(1 for l in tc["legs"] if l["card"]["pick_type"] == "goblin"),
                        "n_demons": sum(1 for l in tc["legs"] if l["card"]["pick_type"] == "demon"),
                        "n_standard": sum(1 for l in tc["legs"] if l["card"]["pick_type"] == "standard"),
                        "displayed_multiplier": slip.get("displayed_multiplier"),
                        "flex_first_place": slip.get("flex_first_place"),
                        "flex_miss_1": slip.get("flex_miss_1"),
                        "entry_amount": float(slip.get("entry_amount") or args.entry_amount),
                        "to_win_amount": slip.get("to_win"),
                    }
                    out_rows.append(rec)
                    bump_counts_from_record(counts, rec)
                    print(f"  [RECORDED] mult={rec['displayed_multiplier']} towin={rec['to_win_amount']}")
                else:
                    print("  [NO SLIP] Slip panel not detected")
                clear_slip(frame)
                dismiss_modal(frame, page)
                frame.wait_for_timeout(600)
            except Exception as e:
                print(f"  [ERROR] {e}")
                try:
                    clear_slip(frame)
                    dismiss_modal(frame, page)
                except Exception:
                    pass
            cases_run += 1
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

