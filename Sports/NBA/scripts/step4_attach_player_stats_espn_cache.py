#!/usr/bin/env python3
"""
step4_attach_player_stats_espn_cache.py  (DB version)

Replaces live ESPN API fetching with indexed reads from proporacle_ref.db.
The DB is populated nightly by build_boxscore_ref.py (called from run_grader.ps1).

Usage:
    py step4_attach_player_stats_espn_cache.py \
        --slate step3_with_defense.csv \
        --out   step4_with_stats.csv \
        --date  2026-03-09

Required DB column: ESPN_ATHLETE_ID (populated by step2/step1 ID attach).
If your slate uses nba_player_id instead, pass --id-col nba_player_id
and ensure it maps to ESPN IDs via the idmap (or use step5a to pre-attach).
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# Allow running from any working directory
# Walk up from this file to find scripts/step4_db_reader.py
_here = Path(__file__).resolve().parent
for _ in range(6):
    if (_here / "scripts" / "step4_db_reader.py").exists():
        sys.path.insert(0, str(_here / "scripts"))
        break
    _here = _here.parent
from step4_db_reader import open_db, attach_stats, db_summary, DB_PATH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate",    default="step3_with_defense.csv")
    ap.add_argument("--out",      default="step4_with_stats.csv")
    ap.add_argument("--date",     default="",   help="Slate date YYYY-MM-DD (informational)")
    ap.add_argument("--segment",  default="",   help="Optional period segment hint (e.g. 1Q)")
    ap.add_argument("--n",        type=int, default=10, help="Max games to pull per player")
    ap.add_argument("--id-col",   default="ESPN_ATHLETE_ID",
                    help="Column containing ESPN athlete ID (default: ESPN_ATHLETE_ID)")
    ap.add_argument("--db",       default="", help="Override DB path")
    ap.add_argument("--summary",  action="store_true", help="Print DB summary and exit")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.slate}")
    slate = pd.read_csv(args.slate, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    # Fallback: if id_col not present but nba_player_id is, warn and use it
    id_col = args.id_col
    if id_col not in slate.columns:
        fallbacks = ["ESPN_ATHLETE_ID", "espn_athlete_id", "nba_player_id"]
        for fb in fallbacks:
            if fb in slate.columns:
                print(f"  ⚠️  '{id_col}' not found — using '{fb}' instead")
                id_col = fb
                break
        else:
            raise SystemExit(f"No ID column found. Columns: {list(slate.columns)}")

    # ── Bridge nba_player_id → ESPN_ATHLETE_ID via DB ─────────────────────────
    # The DB nba table stores espn_athlete_id per row. Build a name→espn_id map
    # so attach_stats uses ESPN IDs (primary key) instead of nba_player_ids.
    #
    # FIX: previously only ran when ESPN_ATHLETE_ID column was absent entirely.
    # Now also runs when the column exists but has blank values (e.g. unicode
    # player names like Luka Dončić, Nikola Jokić that upstream step2 couldn't
    # resolve via nba_api but whose ESPN IDs are in the DB).
    _has_espn_col = "ESPN_ATHLETE_ID" in slate.columns
    _any_empty_espn = (not _has_espn_col) or (
        slate["ESPN_ATHLETE_ID"].astype(str).str.strip().eq("").any()
    )
    _icp = pd.to_numeric(slate.get("is_combo_player", 0), errors="coerce").fillna(0).astype(int).eq(1)
    _pipe_pid = slate.get("nba_player_id", pd.Series("", index=slate.index)).astype(str).str.contains("|", na=False)
    _combo_gaps = (
        "player_1" in slate.columns
        and "player_2" in slate.columns
        and (_icp | _pipe_pid).any()
        and (
            not _has_espn_col
            or (_icp & slate["ESPN_ATHLETE_ID"].astype(str).str.strip().eq("")).any()
        )
    )
    _needs_bridge = (id_col == "nba_player_id" and _any_empty_espn) or _combo_gaps
    if _needs_bridge:
        print("→ Building ESPN ID bridge from DB (player name lookup)...")
        rows = con.execute(
            "SELECT player, espn_athlete_id FROM nba "
            "WHERE espn_athlete_id IS NOT NULL "
            "GROUP BY player, espn_athlete_id"
        ).fetchall()

        import unicodedata as _ud

        def _nfkd(s: str) -> str:
            """Strip accents via NFKD decomposition — matches step2 norm_name_strict."""
            n = _ud.normalize("NFKD", str(s).strip().lower())
            return "".join(c for c in n if not _ud.combining(c))

        # Build TWO lookup dicts:
        #   name_to_espn        — keyed by exact lowercase DB name (accents preserved)
        #   name_to_espn_ascii  — keyed by NFKD-normalized DB name (accents stripped)
        # This covers both cases: DB stores accented names OR ASCII-normalized names.
        name_to_espn       = {}
        name_to_espn_ascii = {}
        for r in rows:
            if r[0] and r[1]:
                key_raw   = r[0].strip().lower()
                key_ascii = _nfkd(r[0])
                # Prefer most-recent espn_id if duplicates (GROUP BY handles this)
                name_to_espn.setdefault(key_raw,   r[1])
                name_to_espn_ascii.setdefault(key_ascii, r[1])

        # ── Name normalization: handles Jr./Sr./II/III suffixes, particles (da/de/van),
        #    unicode accents, and explicit aliases for known PP→ESPN name mismatches ──────
        _SUFFIXES = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv"}
        _PARTICLES = {"da", "de", "van", "le", "la"}

        # Explicit aliases: PrizePicks name (lowercase) -> DB name (lowercase)
        _ALIASES = {
            "tristan silva":       "tristan da silva",
            "tristan da silva":    "tristan da silva",
        }

        def _resolve_name(raw: str) -> str:
            n       = raw.strip().lower()          # accents preserved
            n_ascii = _nfkd(raw)                   # accents stripped

            # 1) exact match (accented key)
            if n in name_to_espn:
                return name_to_espn[n]
            # 1b) exact match (ASCII-normalized key) — covers unicode names
            if n_ascii in name_to_espn_ascii:
                return name_to_espn_ascii[n_ascii]
            # 2) alias table
            for lookup in (n, n_ascii):
                if lookup in _ALIASES:
                    aliased = _ALIASES[lookup]
                    for d in (name_to_espn, name_to_espn_ascii):
                        if aliased in d:
                            return d[aliased]
            # 3) strip known suffixes ("Kevin Porter Jr." -> "kevin porter")
            for base in (n, n_ascii):
                parts = base.split()
                while parts and parts[-1].rstrip(".") in _SUFFIXES:
                    parts = parts[:-1]
                stripped = " ".join(parts)
                for d in (name_to_espn, name_to_espn_ascii):
                    if stripped in d:
                        return d[stripped]
                # 4) add Jr./II/III variants
                for suffix in ("jr.", "ii", "iii"):
                    for d in (name_to_espn, name_to_espn_ascii):
                        if stripped + " " + suffix in d:
                            return d[stripped + " " + suffix]
                # 5) remove particles
                parts2      = stripped.split()
                no_particle = " ".join(p for p in parts2 if p not in _PARTICLES)
                if no_particle != stripped:
                    for d in (name_to_espn, name_to_espn_ascii):
                        if no_particle in d:
                            return d[no_particle]
                # 6) last-token / first-token match
                if parts:
                    last = parts[-1].rstrip(".")
                    first = parts[0]
                    for d in (name_to_espn, name_to_espn_ascii):
                        candidates = [k for k in d
                                      if k.split()[-1].rstrip(".") == last
                                      and k.split()[0] == first]
                        if len(candidates) == 1:
                            return d[candidates[0]]
            return ""

        # Apply bridge: fill blank ESPN_ATHLETE_ID cells, don't overwrite existing values
        if "ESPN_ATHLETE_ID" not in slate.columns:
            slate["ESPN_ATHLETE_ID"] = slate["player"].str.strip().map(_resolve_name)
        else:
            blank_mask = slate["ESPN_ATHLETE_ID"].astype(str).str.strip().eq("")
            slate.loc[blank_mask, "ESPN_ATHLETE_ID"] = (
                slate.loc[blank_mask, "player"].str.strip().map(_resolve_name)
            )

        # Combo rows: `player` is "A + B" so the map above leaves ESPN blank.
        # Step2 provides player_1 / player_2 — build "espn1|espn2" for get_vals_combo().
        _icp = pd.to_numeric(slate.get("is_combo_player", 0), errors="coerce").fillna(0).astype(int).eq(1)
        _pipe = slate.get("nba_player_id", pd.Series("", index=slate.index)).astype(str).str.contains("|", na=False)
        _combo_like = _icp | _pipe
        _still_blank = slate["ESPN_ATHLETE_ID"].astype(str).str.strip().eq("")
        if (
            _combo_like.any()
            and _still_blank.any()
            and "player_1" in slate.columns
            and "player_2" in slate.columns
        ):
            for idx in slate.index[_combo_like & _still_blank]:
                p1 = str(slate.at[idx, "player_1"]).strip()
                p2 = str(slate.at[idx, "player_2"]).strip()
                if not p1 or not p2:
                    continue
                e1, e2 = _resolve_name(p1), _resolve_name(p2)
                if e1 and e2:
                    slate.at[idx, "ESPN_ATHLETE_ID"] = f"{e1}|{e2}"

        no_id   = slate[slate["ESPN_ATHLETE_ID"].astype(str).str.strip().eq("")]["player"].unique()
        bridged = slate["ESPN_ATHLETE_ID"].astype(str).str.strip().ne("").sum()
        print(f"  Bridged {bridged}/{len(slate)} rows to ESPN IDs")
        if len(no_id):
            print(f"  NO_ID players ({len(no_id)}): {sorted(no_id)}")
        id_col = "ESPN_ATHLETE_ID"

    slate_name = str(Path(args.slate).name).lower()
    segment = str(args.segment or "").strip().upper()
    is_nba1q = ("nba1q" in slate_name) or ("nba1q" in str(args.out).lower()) or (segment == "1Q")
    is_nba1h = ("nba1h" in slate_name) or ("nba1h" in str(args.out).lower()) or (segment == "1H")
    sport_key = "nba1q" if is_nba1q else ("nba1h" if is_nba1h else "nba")
    if "data_source" not in slate.columns:
        slate["data_source"] = ""

    print(f"\n→ Attaching {sport_key.upper()} stats from DB (id_col={id_col}, n={args.n})...")
    slate, counts = attach_stats(slate, sport_key, con, id_col=id_col, n=args.n)
    if is_nba1q:
        # Explicit marker for sparse period history.
        status = slate.get("stat_status", pd.Series([""] * len(slate)))
        sparse_mask = status.astype(str).isin(["NO_DATA", "INSUFFICIENT_GAMES"])
        slate.loc[sparse_mask, "data_source"] = "insufficient_q1_history"
        slate.loc[~sparse_mask, "data_source"] = "nba1q_db"
    elif is_nba1h:
        status = slate.get("stat_status", pd.Series([""] * len(slate)))
        sparse_mask = status.astype(str).isin(["NO_DATA", "INSUFFICIENT_GAMES"])
        slate.loc[sparse_mask, "data_source"] = "insufficient_1h_history"
        slate.loc[~sparse_mask, "data_source"] = "nba1h_db"
    else:
        slate["data_source"] = slate["data_source"].replace("", "nba_db")

    slate.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n✅ Saved → {args.out}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
