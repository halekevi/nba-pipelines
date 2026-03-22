#!/usr/bin/env python3
"""
Build ticket_eval_{date}.html (+ ticket_eval_latest.html) for the Grades UI.
Reads ticket JSON and sport step8/graded workbooks, matches legs to actuals, writes self-contained HTML.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = REPO_ROOT / "ui_runner" / "templates"

# Ticket JSON search order
DATED_TICKET_JSON = "combined_slate_tickets_{date}.json"
FALLBACK_TICKET_JSON = TEMPLATES_DIR / "tickets_latest.json"

# Graded / slate workbooks (first existing path wins per sport)
SPORT_XLSX_CANDIDATES: dict[str, list[Path]] = {
    "NBA": [
        REPO_ROOT / "NbaPropPipelineA" / "step8_all_direction_clean.xlsx",
        REPO_ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
    ],
    "CBB": [
        REPO_ROOT / "cbb2" / "step6_ranked_cbb.xlsx",
        REPO_ROOT / "CBB" / "step6_ranked_cbb.xlsx",
    ],
    "NHL": [
        REPO_ROOT / "NHL" / "step8_nhl_direction_clean.xlsx",
        REPO_ROOT / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
    ],
    "SOCCER": [
        REPO_ROOT / "Soccer" / "step8_soccer_direction_clean.xlsx",
        REPO_ROOT / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
    ],
}


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _canon_player(row: dict[str, Any]) -> str:
    for k in ("player", "athlete", "name"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _canon_prop(row: dict[str, Any]) -> str:
    for k in ("prop_type", "prop type", "prop", "prop_display", "stat_type"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _canon_direction(row: dict[str, Any]) -> str:
    for k in ("direction", "bet_direction", "final_bet_direction", "pick direction"):
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip().upper()
    return ""


def _canon_line(row: dict[str, Any]) -> float | None:
    for k in ("line", "line_num"):
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _canon_actual(row: dict[str, Any]) -> float | None:
    for k in ("actual", "actual_value", "act", "result_value", "stat_actual", "final_stat"):
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _canon_grade_raw(row: dict[str, Any]) -> str:
    for k in ("grade", "result", "outcome", "leg_result"):
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip().upper()
        if s:
            return s
    return ""


def _normalize_workbook_rows(path: Path) -> list[dict[str, Any]]:
    """Load all sheets; normalize headers to lowercase single-space keys."""
    xl = pd.ExcelFile(path)
    out: list[dict[str, Any]] = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        if df.empty:
            continue
        df.columns = [_norm_header(c) for c in df.columns]
        out.extend(df.to_dict(orient="records"))
    return out


def _leg_grade(
    actual: float | None,
    line: float | None,
    direction: str,
    grade_col: str,
) -> str:
    g = (grade_col or "").strip().upper()
    if g in ("HIT", "WIN", "W", "1", "TRUE", "YES"):
        return "HIT"
    if g in ("MISS", "LOSS", "L", "0", "FALSE", "NO"):
        return "MISS"
    if g in ("VOID", "PUSH", "N/A", "NA"):
        return "PENDING"
    if actual is None or line is None:
        return "PENDING"
    d = direction.upper()
    if d == "OVER" and actual >= line:
        return "HIT"
    if d == "UNDER" and actual <= line:
        return "HIT"
    return "MISS"


def _pick_type_tier(pick_type: str) -> str:
    p = (pick_type or "").strip().lower()
    if "goblin" in p:
        return "G"
    if "demon" in p:
        return "D"
    if "standard" in p:
        return "S"
    return (pick_type[:1].upper() if pick_type else "?")


def _sport_key(sport: str) -> str:
    s = (sport or "").strip().upper()
    if s in ("NBA", "CBB", "NHL"):
        return s
    if s in ("SOCCER", "SOC", "MLS", "EPL"):
        return "SOCCER"
    return s


def _load_actuals_index() -> tuple[dict[tuple[str, str, str], dict], dict[tuple[str, str], list[dict]]]:
    """Triple-key -> row dict (last wins); pair-key -> list of rows for fallback."""
    triple: dict[tuple[str, str, str], dict] = {}
    pair_buckets: dict[tuple[str, str], list[dict]] = {}

    for sport, paths in SPORT_XLSX_CANDIDATES.items():
        src = next((p for p in paths if p.is_file()), None)
        if not src:
            continue
        try:
            rows = _normalize_workbook_rows(src)
        except Exception:
            continue
        for raw in rows:
            pl = _canon_player(raw).lower()
            pt = _canon_prop(raw).lower()
            dr = _canon_direction(raw)
            if not pl or not pt:
                continue
            row = {
                "player_lower": pl,
                "prop_lower": pt,
                "direction": dr,
                "line": _canon_line(raw),
                "actual": _canon_actual(raw),
                "grade_raw": _canon_grade_raw(raw),
            }
            key3 = (pl, pt, dr)
            triple[key3] = row
            pair_buckets.setdefault((pl, pt), []).append(row)

    return triple, pair_buckets


def _match_leg_to_row(
    leg: dict[str, Any],
    triple: dict[tuple[str, str, str], dict],
    pair_buckets: dict[tuple[str, str], list[dict]],
) -> dict | None:
    pl = str(leg.get("player") or "").strip().lower()
    pt = str(leg.get("prop_type") or "").strip().lower()
    dr = str(leg.get("direction") or "").strip().upper()
    if not pl or not pt:
        return None
    hit = triple.get((pl, pt, dr))
    if hit:
        return hit
    cands = pair_buckets.get((pl, pt))
    if not cands:
        return None
    for r in cands:
        if r["direction"] == dr:
            return r
    if len(cands) == 1:
        return cands[0]
    for r in cands:
        if r["direction"] == dr or not r["direction"]:
            return r
    return cands[0]


def _find_ticket_json(arg_date: str) -> Path | None:
    p1 = REPO_ROOT / DATED_TICKET_JSON.format(date=arg_date)
    if p1.is_file():
        return p1
    if FALLBACK_TICKET_JSON.is_file():
        return FALLBACK_TICKET_JSON
    return None


def _load_tickets(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _fmt_num(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, (int, float)):
        if isinstance(x, float) and x == int(x):
            return str(int(x))
        return f"{x:g}"
    return html.escape(str(x))


def _build_html(payload: dict[str, Any], arg_date: str) -> str:
    groups = payload.get("groups") or []
    triple, pair_buckets = _load_actuals_index()

    all_legs: list[tuple[dict, dict | None, str]] = []
    tickets_flat: list[dict] = []

    for g in groups:
        gname = str(g.get("group_name") or "Group")
        for t in g.get("tickets") or []:
            t["_group_name"] = gname
            tickets_flat.append(t)
            for leg in t.get("legs") or []:
                row = _match_leg_to_row(leg, triple, pair_buckets)
                line = leg.get("line")
                try:
                    line_f = float(line) if line is not None else None
                except (TypeError, ValueError):
                    line_f = None
                direction = str(leg.get("direction") or "").strip().upper()
                actual = row["actual"] if row else None
                graw = row["grade_raw"] if row else ""
                if row and row.get("line") is not None and line_f is None:
                    line_f = row["line"]
                grade = _leg_grade(actual, line_f, direction, graw)
                all_legs.append((leg, row, grade))

    total_legs = len(all_legs)
    hits = sum(1 for _, _, g in all_legs if g == "HIT")
    misses = sum(1 for _, _, g in all_legs if g == "MISS")
    pending = sum(1 for _, _, g in all_legs if g == "PENDING")
    decided = hits + misses
    leg_pct = (100.0 * hits / decided) if decided else 0.0

    perfect = 0
    with_misses = 0
    for t in tickets_flat:
        legs = t.get("legs") or []
        gs = []
        for leg in legs:
            row = _match_leg_to_row(leg, triple, pair_buckets)
            try:
                lf = float(leg.get("line"))
            except (TypeError, ValueError):
                lf = None
            d = str(leg.get("direction") or "").strip().upper()
            act = row["actual"] if row else None
            gr = row["grade_raw"] if row else ""
            if row and row.get("line") is not None and lf is None:
                lf = row["line"]
            g = _leg_grade(act, lf, d, gr)
            gs.append(g)
        if not gs:
            continue
        if all(x == "HIT" for x in gs):
            perfect += 1
        if any(x == "MISS" for x in gs):
            with_misses += 1

    # ── HTML
    esc = html.escape
    json_date = esc(str(payload.get("date") or arg_date))

    sport_colors_css = """
.sport-nba{background:rgba(212,160,23,.12);color:#f0a500;border:1px solid rgba(212,160,23,.35);}
.sport-cbb{background:rgba(0,229,255,.10);color:#00e5ff;border:1px solid rgba(0,229,255,.32);}
.sport-nhl{background:rgba(186,130,255,.12);color:#c4a5ff;border:1px solid rgba(186,130,255,.38);}
.sport-soccer{background:rgba(240,165,0,.10);color:#e8b84a;border:1px solid rgba(240,165,0,.34);}
.sport-default{background:rgba(255,255,255,.04);color:#888;border:1px solid rgba(255,255,255,.1);}
"""

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0"/>',
        f"<title>Ticket Eval — {json_date}</title>",
        '<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>',
        "<style>",
        ":root{--gold:#f0a500;--gold2:#d4a017;--green:#39ff6e;--red:#ff4d4d;--cyan:#00e5ff;--pending:#666;--muted:#94a3b8;"
        "--glass:rgba(255,255,255,0.03);--glass-bd:rgba(255,255,255,0.08);}",
        "*{box-sizing:border-box;margin:0;padding:0;}",
        "body{font-family:'Share Tech Mono',monospace;background:transparent;color:rgba(232,236,255,.95);min-height:100vh;padding-bottom:48px;}",
        "h1,h2,h3,h4,h5,h6{font-family:'Bebas Neue',sans-serif;letter-spacing:3px;}",
        ".bebas{font-family:'Bebas Neue',sans-serif;letter-spacing:3px;}",
        ".stats-bar{position:sticky;top:0;z-index:50;margin:0 auto 18px;width:100%;max-width:min(1520px,96vw);padding:18px clamp(16px,2.5vw,32px);"
        "background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);"
        "border:1px solid var(--glass-bd);border-radius:18px;box-shadow:0 8px 32px rgba(0,0,0,.35);}",
        ".sum-row{display:flex;flex-wrap:wrap;gap:18px 36px;align-items:center;justify-content:center;}",
        ".sum-item{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:88px;}",
        ".sum-val{font-family:'Share Tech Mono',monospace;font-size:clamp(22px,2.6vw,30px);font-weight:700;color:var(--gold);text-shadow:0 0 20px rgba(240,165,0,.25);}",
        ".sum-val.green{color:var(--green);text-shadow:0 0 14px rgba(57,255,110,.35);}",
        ".sum-val.red{color:var(--red);text-shadow:0 0 14px rgba(255,77,77,.35);}",
        ".sum-val.pend{color:var(--pending);text-shadow:none;}",
        ".sum-val-sm{font-size:clamp(18px,2.1vw,24px)!important;}",
        ".sum-lab{font-family:'Bebas Neue',sans-serif;font-size:11px;letter-spacing:2.2px;color:var(--muted);text-align:center;line-height:1.2;max-width:11em;}",
        ".wrap{width:100%;max-width:min(1520px,96vw);margin:0 auto;padding:10px clamp(14px,2.5vw,32px) 0;}",
        ".sec{margin-top:36px;}",
        ".sec-head{font-family:'Bebas Neue',sans-serif;font-size:clamp(30px,3.2vw,40px);color:var(--gold);margin-bottom:8px;padding-bottom:14px;"
        "border-bottom:1px solid var(--glass-bd);letter-spacing:3px;text-shadow:0 0 24px rgba(240,165,0,.2);}",
        ".ticket-card{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);"
        "border:1px solid var(--glass-bd);border-radius:14px;margin-bottom:22px;overflow:hidden;"
        "box-shadow:0 8px 32px rgba(0,0,0,.35);}",
        ".ticket-card.all-hit{background:rgba(57,255,110,0.06);border-color:rgba(57,255,110,.42);"
        "box-shadow:0 0 28px rgba(57,255,110,.14),0 8px 32px rgba(0,0,0,.3);}",
        ".ticket-card.card-missed{background:rgba(255,77,77,0.06);border:1px solid rgba(255,77,77,0.35);"
        "box-shadow:0 0 24px rgba(255,77,77,0.12),0 0 1px rgba(255,77,77,0.4),0 8px 32px rgba(0,0,0,.28);position:relative;}",
        ".ticket-card.card-missed::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:#ff4d4d;"
        "box-shadow:0 0 14px rgba(255,77,77,0.45);z-index:2;border-radius:14px 14px 0 0;pointer-events:none;}",
        ".thdr{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;padding:18px clamp(14px,2vw,24px);border-bottom:1px solid var(--glass-bd);"
        "background:rgba(0,0,0,.18);backdrop-filter:blur(12px);}",
        ".thdr .tn{font-size:clamp(24px,2.8vw,32px);font-family:'Bebas Neue',sans-serif;letter-spacing:2px;color:var(--gold);}",
        ".thdr .tg{font-family:'Share Tech Mono',monospace;font-size:clamp(12px,1.35vw,15px);color:var(--muted);letter-spacing:0.5px;line-height:1.35;}",
        ".payout{font-family:'Share Tech Mono',monospace;font-size:clamp(13px,1.4vw,16px);color:var(--cyan);}",
        ".banner{font-family:'Bebas Neue',sans-serif;font-size:clamp(11px,1.2vw,13px);letter-spacing:2px;padding:8px 18px;border-radius:999px;font-weight:700;"
        "background:rgba(255,255,255,0.04);backdrop-filter:blur(20px);border:1px solid var(--glass-bd);}",
        ".banner.hit{color:var(--green);border-color:rgba(57,255,110,.45);box-shadow:0 0 16px rgba(57,255,110,.15);}",
        ".banner.miss{color:var(--red);border-color:rgba(255,77,77,.5);box-shadow:0 0 16px rgba(255,77,77,.12);}",
        ".banner.pend{color:var(--pending);border-color:rgba(255,255,255,.12);}",
        "@keyframes missRowPulse{0%,100%{box-shadow:0 0 0 1px rgba(255,77,77,0.4),inset 0 0 20px rgba(255,77,77,0.06);}"
        "50%{box-shadow:0 0 0 2px rgba(255,77,77,0.65),0 0 22px rgba(255,77,77,0.18),inset 0 0 26px rgba(255,77,77,0.09);}}",
        ".legrow{font-family:'Share Tech Mono',monospace;display:grid;"
        "grid-template-columns:56px 92px minmax(120px,1fr) 44px minmax(240px,1.45fr) minmax(108px,1fr) minmax(96px,1fr) minmax(76px,.85fr);gap:12px;"
        "align-items:center;padding:14px clamp(14px,2vw,22px);font-size:clamp(13px,1.45vw,16px);line-height:1.35;"
        "border-bottom:1px solid rgba(255,255,255,.06);border-left:3px solid transparent;}",
        ".legrow:last-child{border-bottom:none;}",
        ".legrow.leg-hit{background:rgba(57,255,110,0.04);border-left-color:var(--green);}",
        ".legrow.leg-miss{background:rgba(255,77,77,0.10);border-left:4px solid #ff4d4d;"
        "box-shadow:0 0 0 1px rgba(255,77,77,0.4),inset 0 0 20px rgba(255,77,77,0.06);"
        "animation:missRowPulse 2.2s ease-in-out infinite;}",
        ".legrow.leg-miss .pl-miss{color:#ff4d4d;font-weight:700;"
        "text-shadow:0 0 16px rgba(255,77,77,0.8),0 0 32px rgba(255,77,77,0.4);}",
        ".legrow.leg-miss .pl-line{display:flex;align-items:center;flex-wrap:wrap;gap:8px;}",
        ".miss-tag{font-family:'Bebas Neue',sans-serif;display:inline-flex;align-items:center;"
        "background:rgba(255,77,77,0.15);border:1px solid #ff4d4d;color:#ff4d4d;font-size:9px;"
        "letter-spacing:2px;padding:2px 8px;border-radius:20px;line-height:1;vertical-align:middle;}",
        ".legrow.leg-miss .badge.miss{width:44px;height:44px;min-width:44px;border-radius:12px;display:flex;align-items:center;"
        "justify-content:center;font-size:clamp(22px,2.5vw,28px);line-height:1;background:rgba(255,77,77,0.25);"
        "border:2px solid #ff4d4d;box-shadow:0 0 12px rgba(255,77,77,0.6);color:#ff4d4d;text-shadow:none;}",
        ".legrow.leg-miss .leg-extra.val-miss{color:#ff4d4d;font-weight:700;}",
        ".legrow.leg-pend{background:transparent;border-left-color:transparent;}",
        ".legrow.leg-pend .pl-pend,.legrow.leg-pend .meta-muted{color:var(--pending)!important;}",
        ".legrow.leg-pend .pill{background:rgba(255,255,255,0.04)!important;border-color:rgba(255,255,255,0.1)!important;color:var(--pending)!important;}",
        ".badge{font-size:clamp(28px,3.2vw,36px);line-height:1;text-align:center;}",
        ".badge.hit{color:var(--green);text-shadow:0 0 14px rgba(57,255,110,.6);}",
        ".badge.miss{color:var(--red);text-shadow:0 0 14px rgba(255,77,77,.55);}",
        ".badge.pend{color:var(--pending);text-shadow:none;}",
        ".pill{font-family:'Bebas Neue',sans-serif;font-size:clamp(10px,1.1vw,12px);letter-spacing:1.2px;padding:5px 12px;border-radius:999px;text-transform:uppercase;}",
        sport_colors_css,
        ".tier{font-family:'Bebas Neue',sans-serif;width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;"
        "font-weight:800;font-size:clamp(13px,1.4vw,15px);letter-spacing:0;background:rgba(255,255,255,0.05);color:var(--gold);"
        "border:1px solid var(--glass-bd);backdrop-filter:blur(12px);box-shadow:inset 0 1px 0 rgba(255,255,255,.06);}",
        ".pl-hit{color:var(--green);text-shadow:0 0 8px rgba(57,255,110,.4);}",
        ".pl-miss{color:var(--red);}",
        ".pl-pend{color:var(--pending);}",
        ".dir-over{color:var(--cyan);font-weight:700;}",
        ".dir-under{color:var(--gold);font-weight:700;}",
        ".meta-muted{font-family:'Share Tech Mono',monospace;color:var(--muted);font-size:clamp(11px,1.2vw,13px);margin-top:3px;}",
        ".slate-kicker{font-family:'Share Tech Mono',monospace;font-size:clamp(11px,1.2vw,13px);letter-spacing:3px;color:var(--muted);margin-bottom:10px;}",
        ".pl-hit,.pl-pend{font-size:1em;font-weight:600;}",
        "@media(max-width:900px){.legrow{grid-template-columns:52px 80px 1fr;gap:10px;padding:12px;font-size:14px;}.leg-extra{display:none;}"
        ".stats-bar{padding:14px 16px;}.sum-val{font-size:22px;}}",
        "</style>",
        "</head>",
        "<body>",
        '<div class="stats-bar">',
        '<div class="sum-row">',
        f'<div class="sum-item"><div class="sum-val">{leg_pct:.1f}%</div><div class="sum-lab">LEG HIT RATE</div></div>',
        f'<div class="sum-item"><div class="sum-val green">{hits}</div><div class="sum-lab">HITS</div></div>',
        f'<div class="sum-item"><div class="sum-val red">{misses}</div><div class="sum-lab">MISSES</div></div>',
        f'<div class="sum-item"><div class="sum-val pend">{pending}</div><div class="sum-lab">PENDING</div></div>',
        f'<div class="sum-item"><div class="sum-val">{perfect}</div><div class="sum-lab">PERFECT TICKETS</div></div>',
        f'<div class="sum-item"><div class="sum-val">{with_misses}</div><div class="sum-lab">TIX W/ MISS</div></div>',
        f'<div class="sum-item"><div class="sum-val sum-val-sm">{total_legs}</div><div class="sum-lab">TOTAL LEGS</div></div>',
        "</div></div>",
        '<div class="wrap">',
        f'<p class="slate-kicker">SLATE DATE · {json_date}</p>',
    ]

    for g in groups:
        gname = str(g.get("group_name") or "Group")
        parts.append(f'<section class="sec"><h2 class="sec-head bebas">{esc(gname)}</h2>')
        for t in g.get("tickets") or []:
            tno = t.get("ticket_no", "?")
            pp = t.get("power_payout")
            fp = t.get("flex_payout")
            legs = t.get("legs") or []
            leg_grades: list[str] = []
            for leg in legs:
                row = _match_leg_to_row(leg, triple, pair_buckets)
                try:
                    lf = float(leg.get("line"))
                except (TypeError, ValueError):
                    lf = None
                d = str(leg.get("direction") or "").strip().upper()
                act = row["actual"] if row else None
                gr = row["grade_raw"] if row else ""
                if row and row.get("line") is not None and lf is None:
                    lf = row["line"]
                leg_grades.append(_leg_grade(act, lf, d, gr))

            h = leg_grades.count("HIT")
            m = leg_grades.count("MISS")
            pnd = leg_grades.count("PENDING")
            n = len(leg_grades)

            if pnd > 0:
                banner_cls, banner_txt = "pend", "PENDING"
            elif m == 0 and n > 0:
                banner_cls, banner_txt = "hit", "ALL HIT"
            else:
                banner_cls, banner_txt = "miss", f"MISSED {m}"

            card_cls = "ticket-card"
            if banner_txt == "ALL HIT":
                card_cls += " all-hit"
            elif banner_cls == "miss":
                card_cls += " card-missed"

            parts.append(f'<article class="{card_cls}">')
            parts.append('<div class="thdr">')
            parts.append(f'<span class="tn bebas">#{esc(str(tno))}</span>')
            parts.append(f'<span class="tg">{esc(gname)}</span>')
            parts.append(f'<span class="tg">{h}✓ {m}✗ / {n}</span>')
            parts.append(f'<span class="payout">PWR {_fmt_num(pp)}× · FLEX {_fmt_num(fp)}×</span>')
            parts.append(f'<span class="banner {banner_cls}">{esc(banner_txt)}</span>')
            parts.append("</div>")

            for leg, lg in zip(legs, leg_grades):
                row = _match_leg_to_row(leg, triple, pair_buckets)
                try:
                    lf = float(leg.get("line"))
                except (TypeError, ValueError):
                    lf = None
                d = str(leg.get("direction") or "").strip().upper()
                act = row["actual"] if row else None
                gr = row["grade_raw"] if row else ""
                if row and row.get("line") is not None and lf is None:
                    lf = row["line"]

                if lg == "HIT":
                    bcls, plcls = "hit", "pl-hit"
                elif lg == "MISS":
                    bcls, plcls = "miss", "pl-miss"
                else:
                    bcls, plcls = "pend", "pl-pend"

                sk = _sport_key(str(leg.get("sport") or ""))
                sp_class = {
                    "NBA": "sport-nba",
                    "CBB": "sport-cbb",
                    "NHL": "sport-nhl",
                    "SOCCER": "sport-soccer",
                }.get(sk, "sport-default")

                tier = _pick_type_tier(str(leg.get("pick_type") or ""))
                team = esc(str(leg.get("team") or ""))
                opp = esc(str(leg.get("opp") or ""))
                ptype = esc(str(leg.get("prop_type") or ""))
                player = esc(str(leg.get("player") or ""))
                edge = leg.get("edge")
                dir_cls = "dir-over" if d == "OVER" else "dir-under" if d == "UNDER" else ""

                if lg == "HIT":
                    row_cls = "legrow leg-hit"
                elif lg == "MISS":
                    row_cls = "legrow leg-miss"
                else:
                    row_cls = "legrow leg-pend"
                sym = "✓" if lg == "HIT" else "✗" if lg == "MISS" else "·"

                if lg == "MISS":
                    pl_html = (
                        f'<div class="{plcls} pl-line">'
                        f'<span class="pl-name">{player}</span>'
                        '<span class="miss-tag" aria-label="Missed leg">MISSED</span></div>'
                    )
                else:
                    pl_html = f'<div class="{plcls}">{player}</div>'

                if lg == "MISS":
                    act_div_cls = "leg-extra val-miss"
                elif lg == "HIT":
                    act_div_cls = "leg-extra pl-hit"
                else:
                    act_div_cls = "leg-extra pl-pend"

                parts.append(f'<div class="{row_cls}">')
                parts.append(f'<div class="badge {bcls}">{sym}</div>')
                parts.append(f'<div><span class="pill {sp_class}">{esc(sk)}</span></div>')
                parts.append(pl_html)
                parts.append(f'<div class="tier">{esc(tier)}</div>')
                parts.append(
                    f'<div><div>{ptype}</div><div class="meta-muted">{team} vs {opp}</div></div>'
                )
                parts.append(
                    f'<div class="leg-extra">{_fmt_num(lf)} <span class="{dir_cls}">{esc(d)}</span></div>'
                )
                parts.append(f'<div class="{act_div_cls}">{_fmt_num(act)}</div>')
                parts.append(f'<div class="leg-extra">{_fmt_num(edge)}</div>')
                parts.append("</div>")

            parts.append("</article>")
        parts.append("</section>")

    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build ticket_eval HTML for Grades UI.")
    ap.add_argument(
        "--date",
        default="",
        help="Slate date YYYY-MM-DD (default: yesterday local)",
    )
    args = ap.parse_args()
    if args.date:
        arg_date = args.date.strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", arg_date):
            print("ERROR: --date must be YYYY-MM-DD")
            return 1
    else:
        arg_date = (date.today() - timedelta(days=1)).isoformat()

    tpath = _find_ticket_json(arg_date)
    if not tpath:
        print("ERROR: No ticket JSON found (combined_slate_tickets_{date}.json or tickets_latest.json).")
        return 1

    try:
        payload = _load_tickets(tpath)
    except Exception as e:
        print(f"ERROR: Failed to read ticket JSON: {e}")
        return 1

    html_out = _build_html(payload, arg_date)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dated_name = f"ticket_eval_{arg_date}.html"
    out_dated = TEMPLATES_DIR / dated_name
    out_latest = TEMPLATES_DIR / "ticket_eval_latest.html"
    try:
        out_dated.write_text(html_out, encoding="utf-8")
        out_latest.write_text(html_out, encoding="utf-8")
    except OSError as e:
        print(f"ERROR: Write failed: {e}")
        return 1

    print(f"Wrote {out_dated}")
    print(f"Wrote {out_latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
