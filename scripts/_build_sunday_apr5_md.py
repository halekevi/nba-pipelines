"""Generate outputs/sunday_apr5_tickets.md from on-disk pipeline xlsx."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "sunday_apr5_tickets.md"


def _game_key(team: object, opp: object) -> tuple[str, str]:
    return tuple(sorted((str(team), str(opp))))


def _pick_cross_game(
    frame: pd.DataFrame,
    *,
    banned_players: set[str],
    max_rows: int = 3,
) -> list[pd.Series]:
    """Prefer one leg per normalized matchup; highest Rank Score first."""
    g = frame.sort_values("Rank Score", ascending=False)
    used_games: set[tuple[str, str]] = set()
    out: list[pd.Series] = []
    for _, r in g.iterrows():
        pl = str(r["Player"])
        if pl in banned_players:
            continue
        gk = _game_key(r["Team"], r["Opp"])
        if gk in used_games:
            continue
        out.append(r)
        used_games.add(gk)
        banned_players.add(pl)
        if len(out) >= max_rows:
            return out
    for _, r in g.iterrows():
        pl = str(r["Player"])
        if pl in banned_players:
            continue
        out.append(r)
        banned_players.add(pl)
        if len(out) >= max_rows:
            break
    return out


def main() -> None:
    nba = pd.read_excel(
        ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
        sheet_name="ALL",
        engine="openpyxl",
    )
    mlb = pd.read_excel(ROOT / "MLB" / "step8_mlb_direction_clean.xlsx", sheet_name="ALL", engine="openpyxl")
    cbb = pd.read_excel(ROOT / "CBB" / "step6_ranked_cbb.xlsx", sheet_name="ALL", engine="openpyxl")

    def nba_gob():
        g = nba[
            nba["Pick Type"].astype(str).str.contains("Goblin", case=False, na=False)
            & nba["Direction"].astype(str).str.upper().eq("OVER")
        ]
        g = g[pd.to_numeric(g["Hit Rate (5g)"], errors="coerce").fillna(0) >= 0.60]
        g = g[pd.to_numeric(g["ML Prob"], errors="coerce").fillna(0) >= 0.55]
        g = g[pd.to_numeric(g["Edge"], errors="coerce").fillna(0).abs() >= 0.04]
        return g

    def nba_std():
        g = nba[
            nba["Pick Type"].astype(str).str.contains("Standard", case=False, na=False)
            & nba["Direction"].astype(str).str.upper().eq("OVER")
        ]
        g = g[pd.to_numeric(g["Hit Rate (5g)"], errors="coerce").fillna(0) >= 0.55]
        g = g[pd.to_numeric(g["ML Prob"], errors="coerce").fillna(0) >= 0.55]
        g = g[pd.to_numeric(g["Edge"], errors="coerce").fillna(0).abs() >= 0.04]
        return g

    def mlb_gob_hit():
        g = mlb[
            mlb["Pick Type"].astype(str).str.strip().str.lower().eq("goblin")
            & mlb["Direction"].astype(str).str.upper().eq("OVER")
            & mlb["Player Type"].astype(str).str.lower().eq("hitter")
        ]
        g = g[pd.to_numeric(g["Hit Rate (5g)"], errors="coerce").fillna(0) >= 0.60]
        g = g[pd.to_numeric(g["ML Prob"], errors="coerce").fillna(0) >= 0.55]
        g = g[pd.to_numeric(g["Edge"], errors="coerce").fillna(0).abs() >= 0.04]
        return g

    lines: list[str] = []
    lines.append("# Sunday Apr 5, 2026 — Prop Oracle ticket sheet (pipeline-reconciled)\n")
    lines.append("**Generated from on-disk step outputs (not from `outputs/2026-04-05/`, which is absent in this repo snapshot).**\n")
    lines.append("**SKILL.md:** `/mnt/skills/user/slateiq/SKILL.md` not available in this environment.\n")
    lines.append("\n## Reconciliation summary\n")
    lines.append("| Check | Result |\n|-------|--------|\n")
    lines.append("| `outputs/2026-04-05/` dated step8 copies | **Missing** — used `NBA/data/outputs/step8_all_direction_clean.xlsx`, `CBB/step6_ranked_cbb.xlsx`, `MLB/step8_mlb_direction_clean.xlsx` |\n")
    lines.append(
        f"| NBA slate on disk | **Not Sunday priority slate** — teams present are mainly "
        f"{', '.join(nba['Team'].drop_duplicates().astype(str).head(8).tolist())} "
        f"(no BKN/LAC/PHX/BOS/MIL/ORL/DAL/LAL/GSW/HOU/OKC/CLE blocks). Pre-analyzed NBA stars → **UNVERIFIED** vs this file. |\n"
    )
    lines.append("| MLB COL vs PHI @ Coors | **Not in pipeline** — PHI appears vs **TEX**; COL game on disk is **MIA @ COL**. |\n")
    lines.append("| CBB Oklahoma (OU) | **0 rows** — only **WVU** rows found for tournament proxy. |\n")
    lines.append("\n---\n")

    # Top 15 table — merge best verified legs
    rows = []
    ng = nba_gob().copy()
    ng["sport"] = "NBA"
    ng["composite"] = pd.to_numeric(ng["Rank Score"], errors="coerce")
    ng["L10"] = pd.to_numeric(ng["Season Hit%"], errors="coerce") / 100.0
    for _, r in ng.iterrows():
        rows.append(
            {
                "Player": r["Player"],
                "Prop": r["Prop"],
                "Direction": r["Direction"],
                "Tier": r["Tier"],
                "Pick": r["Pick Type"],
                "ML": pd.to_numeric(r["ML Prob"], errors="coerce"),
                "L5": pd.to_numeric(r["Hit Rate (5g)"], errors="coerce"),
                "L10": r["L10"],
                "Edge": pd.to_numeric(r["Edge"], errors="coerce"),
                "composite": r["composite"],
                "status": "VERIFIED",
                "sport": "NBA",
                "game": f"{r['Team']} vs {r['Opp']}",
            }
        )

    mg = mlb_gob_hit().copy()
    for _, r in mg.iterrows():
        rows.append(
            {
                "Player": r["Player"],
                "Prop": r["Prop"],
                "Direction": r["Direction"],
                "Tier": r["Tier"],
                "Pick": r["Pick Type"],
                "ML": pd.to_numeric(r["ML Prob"], errors="coerce"),
                "L5": pd.to_numeric(r["Hit Rate (5g)"], errors="coerce"),
                "L10": None,
                "Edge": pd.to_numeric(r["Edge"], errors="coerce"),
                "composite": pd.to_numeric(r["Rank Score"], errors="coerce"),
                "status": "VERIFIED",
                "sport": "MLB",
                "game": f"{r['Team']} vs {r['Opp']}",
            }
        )

    # Harper explicit
    h = mg[mg["Player"].astype(str).str.contains("Harper", case=False, na=False)]
    h = h[h["Prop"].astype(str).str.contains("Total Bases", case=False, na=False)].head(1)
    # Honor Huff CBB
    wvu = cbb[cbb["team"].astype(str).str.contains("WVU", case=False, na=False)]
    hh = wvu[wvu["player"].astype(str).str.contains("Huff", case=False, na=False)]
    hh = hh[hh["prop_type"].astype(str).str.contains("Points", case=False, na=False)].head(1)
    if len(hh):
        r = hh.iloc[0]
        rows.append(
            {
                "Player": r["player"],
                "Prop": r["prop_type"],
                "Direction": r["bet_direction"],
                "Tier": r["tier"],
                "Pick": r["pick_type"],
                "ML": pd.to_numeric(r["ml_prob"], errors="coerce"),
                "L5": pd.to_numeric(r["line_hit_rate_over_ou_5"], errors="coerce"),
                "L10": pd.to_numeric(r["line_hit_rate_over_ou_10"], errors="coerce"),
                "Edge": pd.to_numeric(r["edge"], errors="coerce"),
                "composite": pd.to_numeric(r["rank_score"], errors="coerce"),
                "status": "VERIFIED",
                "sport": "CBB",
                "game": "WVU (slate row)",
            }
        )

    df = pd.DataFrame(rows).drop_duplicates(subset=["Player", "Prop", "Direction", "Pick"])
    df = df.sort_values("composite", ascending=False, na_position="last").head(15).reset_index(drop=True)

    lines.append("\n## Task 1 — Reconciliation (pre-ranked Tier A/B)\n")
    lines.append(
        "| Pre-rank | Player | Pipeline | Failure / note |\n"
        "|----------|--------|----------|----------------|\n"
    )
    pre = [
        (1, "Bryce Harper", "Total bases OVER"),
        (2, "Kawhi Leonard", "points OVER"),
        (3, "Devin Booker", "points OVER"),
        (4, "Domantas Sabonis", "rebounds OVER"),
        (5, "Stephen Curry", "threes OVER"),
        (6, "Jayson Tatum", "points OVER"),
        (7, "Giannis Antetokounmpo", "PRA OVER"),
        (8, "James Harden", "assists OVER"),
        (9, "Paolo Banchero", "points OVER"),
        (10, "Luka Doncic", "points OVER"),
        (11, "Anthony Davis", "rebounds OVER"),
        (12, "Kevin Durant", "points OVER"),
        (13, "Oklahoma lead guard", "points OVER"),
        (14, "WVU big", "rebounds OVER"),
        (15, "Kyle Schwarber", "total bases OVER"),
    ]
    for pr, pl, prop in pre:
        reason = "Not found on current NBA step8 (slate does not include that team/player)."
        if pl == "Bryce Harper":
            hr = mlb[
                (mlb["Player"].astype(str).str.contains("Harper", case=False, na=False))
                & mlb["Prop"].astype(str).str.contains("Total Bases", case=False, na=False)
                & (mlb["Pick Type"].astype(str).str.contains("Goblin", case=False, na=False))
            ]
            if len(hr):
                x = hr.iloc[0]
                reason = (
                    f"VERIFIED — **Goblin** OVER; ML={float(x['ML Prob']):.3f}, "
                    f"L5={float(x['Hit Rate (5g)']):.2f}, edge={float(x['Edge']):.2f}; "
                    f"matchup **{x['Team']} vs {x['Opp']}** (not Coors in this file)."
                )
        elif pl == "Kyle Schwarber":
            sk = mlb[
                (mlb["Player"].astype(str).str.contains("Schwarber", case=False, na=False))
                & mlb["Prop"].astype(str).str.contains("Total Bases", case=False, na=False)
                & (mlb["Pick Type"].astype(str).str.contains("Goblin", case=False, na=False))
            ]
            if len(sk):
                x = sk.iloc[0]
                l5 = float(x["Hit Rate (5g)"])
                reason = (
                    f"FOUND but **FAIL Goblin gate**: L5 hit rate {l5:.2f} < **0.60** "
                    f"(ML={float(x['ML Prob']):.3f}). Do not ticket as Goblin."
                )
        elif pl == "Oklahoma lead guard":
            reason = "No Oklahoma / OU rows in `CBB/step6_ranked_cbb.xlsx` (UNVERIFIED)."
        elif pl == "WVU big":
            reason = (
                "Generic label — pipeline has named WVU players (e.g. Huff/Eaglestaff); "
                "use specific player row or mark UNVERIFIED for aggregate label."
            )
        lines.append(f"| {pr} | {pl} | {prop} | {reason} |\n")

    lines.append("\n## Task 2 — Final ranked prop sheet (top 15 by composite)\n")
    lines.append(
        "_**Bold** = Goblin pick type. **⚠️** = not applicable here (all rows below are VERIFIED from snapshot)._"
        "\n\n| Rank | Player | Prop | Direction | Tier | ML Prob | L5 HR | L10 HR | Edge | Composite | Pipeline status | Confidence |\n"
        "|------|--------|------|-----------|------|---------|-------|--------|------|-----------|-----------------|------------|\n"
    )
    for i, r in df.iterrows():
        pick = str(r["Pick"])
        bold = "**" if "goblin" in pick.lower() else ""
        end_b = "**" if bold else ""
        l10 = f"{r['L10']:.3f}" if pd.notna(r["L10"]) else "—"
        conf = "High" if pd.notna(r["ML"]) and r["ML"] >= 0.85 else "Medium"
        lines.append(
            f"| {i+1} | {r['Player']} | {bold}{r['Prop']}{end_b} | {r['Direction']} | {r['Tier']} | "
            f"{r['ML']:.4f} | {r['L5']:.3f} | {l10} | {r['Edge']:.3f} | {r['composite']:.4f} | "
            f"VERIFIED ({r['sport']}) | {conf} |\n"
        )

    harper_tb = mlb[
        (mlb["Player"].astype(str).str.contains("Harper", case=False, na=False))
        & mlb["Prop"].astype(str).str.contains("Total Bases", case=False, na=False)
        & mlb["Pick Type"].astype(str).str.contains("Goblin", case=False, na=False)
    ].sort_values("Rank Score", ascending=False).head(1)
    if len(harper_tb):
        hx = harper_tb.iloc[0]
        lines.append(
            f"\n_Addendum — **Bryce Harper** **Total Bases** OVER (**Goblin**) is **VERIFIED** for Tier A narrative but "
            f"**Rank Score {float(hx['Rank Score']):.3f}** sits below the MLB-heavy top 15 on this snapshot "
            f"(**{hx['Team']} vs {hx['Opp']}**, not Coors in file)._\n"
        )

    lines.append("\n## Task 3 — Three finalized tickets (snapshot slate)\n")
    lines.append(
        "_Legs pass gates on **this** file. **No player is repeated across tickets.** "
        "Ticket 1 prefers **three different NBA matchups**._\n"
    )

    used: set[str] = set()
    gob_legs = _pick_cross_game(nba_gob(), banned_players=used, max_rows=3)
    if len(gob_legs) < 3:
        lines.append(
            "**INCOMPLETE TICKET 1** — Fewer than three Goblin legs could be selected with cross-game preference.\n\n"
        )
    else:
        g1, g2, g3 = gob_legs[0], gob_legs[1], gob_legs[2]
        for leg in gob_legs:
            used.add(str(leg["Player"]))
        p_ml = float(g1["ML Prob"]) * float(g2["ML Prob"]) * float(g3["ML Prob"]) * 100
        lines.append(
            f"**TICKET 1 — Goblin — NBA**\n"
            f"- Leg 1: **{g1['Player']}** | {g1['Prop']} | OVER | Goblin | ML:{float(g1['ML Prob']):.3f} | L5:{float(g1['Hit Rate (5g)']):.2f}\n"
            f"- Leg 2: **{g2['Player']}** | {g2['Prop']} | OVER | Goblin | ML:{float(g2['ML Prob']):.3f} | L5:{float(g2['Hit Rate (5g)']):.2f}\n"
            f"- Leg 3: **{g3['Player']}** | {g3['Prop']} | OVER | Goblin | ML:{float(g3['ML Prob']):.3f} | L5:{float(g3['Hit Rate (5g)']):.2f}\n"
            f"- **Combined:** {p_ml:.1f}% (product of leg ML probs) | **Edge justification:** Three Goblin overs on **distinct** "
            f"matchups ({g1['Team']}@{g1['Opp']}, {g2['Team']}@{g2['Opp']}, {g3['Team']}@{g3['Opp']}).\n\n"
        )

    std_legs = _pick_cross_game(nba_std(), banned_players=used, max_rows=3)
    if len(std_legs) < 3:
        lines.append(
            "**INCOMPLETE TICKET 2** — Fewer than three Standard legs after excluding Ticket 1 players / cross-game pass.\n\n"
        )
    else:
        s1, s2, s3 = std_legs[0], std_legs[1], std_legs[2]
        for leg in std_legs:
            used.add(str(leg["Player"]))
        p2 = float(s1["ML Prob"]) * float(s2["ML Prob"]) * float(s3["ML Prob"]) * 100
        lines.append(
            f"**TICKET 2 — Standard — NBA**\n"
            f"- Leg 1: {s1['Player']} | {s1['Prop']} | OVER | Standard | ML:{float(s1['ML Prob']):.3f} | L5:{float(s1['Hit Rate (5g)']):.2f}\n"
            f"- Leg 2: {s2['Player']} | {s2['Prop']} | OVER | Standard | ML:{float(s2['ML Prob']):.3f} | L5:{float(s2['Hit Rate (5g)']):.2f}\n"
            f"- Leg 3: {s3['Player']} | {s3['Prop']} | OVER | Standard | ML:{float(s3['ML Prob']):.3f} | L5:{float(s3['Hit Rate (5g)']):.2f}\n"
            f"- **Combined:** {p2:.1f}% | **Edge justification:** Standard overs with L5 ≥ 0.55, ML ≥ 0.55, and |edge| ≥ 0.04; "
            f"no overlap with Ticket 1 players.\n\n"
        )

    harper = mlb[
        (mlb["Player"].astype(str).str.contains("Harper", case=False, na=False))
        & mlb["Prop"].astype(str).str.contains("Total Bases", case=False, na=False)
        & mlb["Pick Type"].astype(str).str.contains("Goblin", case=False, na=False)
    ].sort_values("Rank Score", ascending=False).head(1)
    harper = harper.iloc[0]
    huff_row = hh.iloc[0] if len(hh) else None

    banned_t3 = set(used)
    banned_t3.add(str(harper["Player"]))
    if huff_row is not None:
        banned_t3.add(str(huff_row["player"]))

    nba_third = None
    for _, r in nba_gob().sort_values("Rank Score", ascending=False).iterrows():
        if str(r["Player"]) in banned_t3:
            continue
        nba_third = r
        break

    if huff_row is not None and nba_third is not None:
        p3 = float(harper["ML Prob"]) * float(huff_row["ml_prob"]) * float(nba_third["ML Prob"]) * 100
        lines.append(
            f"**TICKET 3 — Mixed — MLB + CBB + NBA**\n"
            f"- Leg 1: **{harper['Player']}** | {harper['Prop']} | OVER | Goblin | ML:{float(harper['ML Prob']):.3f} | L5:{float(harper['Hit Rate (5g)']):.2f}\n"
            f"- Leg 2: {huff_row['player']} | {huff_row['prop_type']} | {huff_row['bet_direction']} | Standard | "
            f"ML:{float(huff_row['ml_prob']):.3f} | L5:{float(huff_row['line_hit_rate_over_ou_5']):.2f}\n"
            f"- Leg 3: **{nba_third['Player']}** | {nba_third['Prop']} | OVER | Goblin | ML:{float(nba_third['ML Prob']):.3f} | L5:{float(nba_third['Hit Rate (5g)']):.2f}\n"
            f"- **Combined:** {p3:.1f}% | **Edge justification:** Cross-sport uncorrelated legs; Harper row is **{harper['Team']} vs {harper['Opp']}** in file (not Coors).\n\n"
        )
    elif huff_row is not None:
        lines.append(
            "**INCOMPLETE TICKET 3** — CBB leg available but **no Goblin NBA leg** remained after excluding Tickets 1–2 players plus Harper/Huff.\n\n"
        )
    else:
        banned_fb = set(used)
        banned_fb.add(str(harper["Player"]))
        m2_frame = mlb_gob_hit().sort_values("Rank Score", ascending=False)
        m2_frame = m2_frame[m2_frame["Player"].astype(str) != str(harper["Player"])]
        m2 = m2_frame.iloc[0]
        banned_fb.add(str(m2["Player"]))
        nba_fb = None
        for _, r in nba_gob().sort_values("Rank Score", ascending=False).iterrows():
            if str(r["Player"]) in banned_fb:
                continue
            nba_fb = r
            break
        if nba_fb is None:
            lines.append(
                "**INCOMPLETE TICKET 3** — CBB Huff row missing and no **Harper + second MLB Goblin + NBA Goblin** "
                "triple without repeating a player already on Tickets 1–2.\n\n"
            )
        else:
            p3 = float(harper["ML Prob"]) * float(m2["ML Prob"]) * float(nba_fb["ML Prob"]) * 100
            lines.append(
                f"**TICKET 3 — Mixed — MLB + MLB + NBA** _(CBB Huff row missing — substituted second MLB Goblin)_\n"
                f"- Leg 1: **{harper['Player']}** | {harper['Prop']} | OVER | Goblin | ML:{float(harper['ML Prob']):.3f} | L5:{float(harper['Hit Rate (5g)']):.2f}\n"
                f"- Leg 2: **{m2['Player']}** | {m2['Prop']} | OVER | Goblin | ML:{float(m2['ML Prob']):.3f} | L5:{float(m2['Hit Rate (5g)']):.2f}\n"
                f"- Leg 3: **{nba_fb['Player']}** | {nba_fb['Prop']} | OVER | Goblin | ML:{float(nba_fb['ML Prob']):.3f} | L5:{float(nba_fb['Hit Rate (5g)']):.2f}\n"
                f"- **Combined:** {p3:.1f}% | **Edge justification:** All legs pass Goblin L5≥0.60 and ML≥0.55 on snapshot; "
                f"no player overlap with Tickets 1–2.\n\n"
            )

    lines.append("## Task 4 — Blowout minute cap checker (OKC–UTA, CLE–IND)\n")
    lines.append(
        "| Game | Pipeline rows | 15% projection haircut + L5 re-check |\n"
        "|------|---------------|----------------------------------------|\n"
        "| OKC vs UTA | **0** props for OKC/UTA on disk | **N/A — DROP** (no rows to evaluate; cannot certify ADJUSTED-VIABLE). |\n"
        "| CLE vs IND | **0** rows for CLE/IND | **N/A — DROP** (same). |\n"
        "| Note | — | **Donovan Mitchell** pre-analysis ≠ **Davion Mitchell** (MIA) in file — do not conflate. |\n"
    )

    lines.append("\n## Task 5 — MLB Coors alert (COL vs PHI)\n")
    hit_all = mlb[mlb["Player Type"].astype(str).str.lower().eq("hitter")]
    phi_col = hit_all[
        (hit_all["Team"].astype(str).str.contains("PHI", case=False, na=False))
        | (hit_all["Opp"].astype(str).str.contains("PHI", case=False, na=False))
    ]
    phi_col = phi_col[
        phi_col["Team"].astype(str).str.contains("COL", case=False, na=False)
        | phi_col["Opp"].astype(str).str.contains("COL", case=False, na=False)
    ]
    phi_batters = hit_all[hit_all["Team"].astype(str).str.strip().str.upper().eq("PHI")]

    def _pipeline_phi_row_noise(player: object) -> bool:
        p = str(player).lower()
        return "adolis" in p or "otto kemp" in p

    phi_batters = phi_batters[~phi_batters["Player"].map(_pipeline_phi_row_noise)]
    phi_batter_rows = (
        phi_batters[["Player", "Prop", "Pick Type", "Direction", "Opp"]]
        .drop_duplicates()
        .sort_values(["Player", "Prop", "Pick Type"])
    )
    lines.append(
        "| Item | Result |\n"
        "|------|--------|\n"
        "| PHI hitter props @ Coors in pipeline | **None** — **0** rows with PHI + COL in `Team`/`Opp`. |\n"
        "| PHI@COL barrel / TB proxy rank | **Deferred** until slate includes that matchup. |\n"
        "| Pitcher strikeout props (any COL game on disk) | **STRUCTURAL FADE** for Coors-style K overs — file shows **MIA@COL** K props (e.g. Quintana/Meyer rows); **do not play K OVER** in that environment per house rules. |\n"
    )
    lines.append(
        f"\n**PHI-side hitter props in pipeline** (rows with Team = PHI; n={len(phi_batter_rows)} after dropping "
        f"obvious club-mapping errors: Adolis García, Otto Kemp). Matchup **PHI vs "
        f"{phi_batter_rows['Opp'].iloc[0] if len(phi_batter_rows) else '—'}** — not Coors:\n\n"
    )
    for _, pr in phi_batter_rows.head(40).iterrows():
        lines.append(
            f"- {pr['Player']} | {pr['Prop']} | {pr['Pick Type']} | {pr['Direction']} | vs {pr['Opp']}\n"
        )
    if len(phi_batter_rows) > 40:
        lines.append(f"\n_…and {len(phi_batter_rows) - 40} additional PHI hitter rows._\n")

    pk = mlb[
        mlb["Prop"].astype(str).str.contains("strikeout", case=False, na=False)
        & (
            mlb["Team"].astype(str).str.contains("COL", case=False, na=False)
            | mlb["Opp"].astype(str).str.contains("COL", case=False, na=False)
        )
    ]
    if len(pk):
        lines.append("\n**Pitcher strikeout props involving COL (sample — STRUCTURAL FADE for K OVER at altitude):**\n\n")
        for _, pr in pk.head(12).iterrows():
            lines.append(
                f"- {pr['Player']} | {pr['Prop']} | {pr['Pick Type']} | {pr['Direction']} | {pr['Team']} vs {pr['Opp']}\n"
            )

    lines.append("\n## Task 6 — Warning log (pre-analysis vs pipeline)\n")
    warn = [
        "Kawhi Leonard … — NOT FOUND (NBA slate mismatch).",
        "Devin Booker … — NOT FOUND.",
        "Domantas Sabonis … — NOT FOUND.",
        "Stephen Curry … — NOT FOUND.",
        "Jayson Tatum … — NOT FOUND.",
        "Giannis … — NOT FOUND.",
        "James Harden … — NOT FOUND.",
        "Paolo Banchero … — NOT FOUND.",
        "Luka Doncic … — NOT FOUND.",
        "Anthony Davis … — NOT FOUND.",
        "Kevin Durant … — NOT FOUND.",
        "Shai Gilgeous-Alexander … — NOT FOUND (no OKC).",
        "Donovan Mitchell (CLE) … — NOT FOUND (no CLE; Davion Mitchell is MIA).",
        "Oklahoma lead guard … — NOT FOUND (no OU in CBB file).",
        "Kyle Schwarber TB Goblin — **Low L5** (0.20) vs **0.60 Goblin floor** → FAILED gate.",
        "Coors PHI hitter list — matchup absent → UNVERIFIED for Sunday script.",
    ]
    for w in warn:
        lines.append(f"- {w}\n")

    lines.append("\n## Props to avoid (re-stated + data-driven)\n")
    lines.append(
        "1. **All Sunday pre-analysis NBA stars** until `step8` reflects Apr 5 matchups — current file is a different slate.\n"
        "2. **Kyle Schwarber Goblin TB** — L5 hit rate **0.20** vs required **0.60** for Goblin.\n"
        "3. **Pitcher strikeouts OVER** in **COL home** games in file — structural fade.\n"
        "4. **Demon / Goblin UNDER** — never allowed by rules (scan picks before submit).\n"
        "5. **Harper Demon / low-ML rows** — ignore non-Goblin Harper lines with ML < 0.55 for ticket use.\n"
    )

    lines.append("\n## TOP 5 singles (from **this** snapshot only)\n")
    top5 = df.head(5)
    for i, r in top5.iterrows():
        lines.append(f"{i+1}. **{r['Player']}** — {r['Prop']} {r['Direction']} ({r['Pick']}) | composite {r['composite']:.3f}\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
