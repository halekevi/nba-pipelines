"""Assemble PrizePicks-style tickets with uniform per-leg hit rates.

Reads outputs/strict_mode/strict_<date>.csv (produced by apply_strict_mode.py),
groups picks by their estimated P(hit), and assembles N-leg tickets where
every leg lives in the same hit-rate bucket. This makes the ticket's joint
probability easy to reason about: a 5-leg "premium" ticket where each leg is
~70% has a known joint of ~16.8%.

Constraints applied during greedy assembly:
  * At most 1 prop per player per ticket.
  * At most 2 props from the same game (light correlation hedge).
  * Tickets are deduplicated by (sorted player+prop+direction+line) signature.

Output:
  outputs/tickets/uniform_tickets_<date>.csv
  outputs/tickets/uniform_tickets_<date>_top.json   (top-K per size+bucket)

Backtest mode replays historical graded JSONs through the same builder and
reports the realized joint hit rate per (size, bucket) so we can confirm
P(all hit) ≈ ∏ P(leg_hit) once correlations are factored.

Usage
    python scripts/build_uniform_tickets.py --date 2026-05-08
    python scripts/build_uniform_tickets.py --date 2026-05-08 --sizes 3 4 5
    python scripts/build_uniform_tickets.py --backtest --since 2026-04-15
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

import warnings

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

OUT_DIR = REPO / "outputs" / "tickets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Hit-rate buckets (per-leg P(hit)) ──────────────────────────────────────
BUCKETS: list[tuple[str, float, float]] = [
    ("elite",   0.75, 1.01),
    ("premium", 0.65, 0.75),
    ("strong",  0.55, 0.65),
    ("value",   0.45, 0.55),
]

# Fallback per-leg P(hit) when meta_prob is missing — derived from the
# tier_override backtest so the assembler still produces ranked tickets in
# small buckets where the classifier hasn't trained.
OVERRIDE_PRIOR = {"A": 0.77, "B": 0.61, "C": 0.45, "D": 0.22}

# PrizePicks Power Play base payouts for Standard-only legs.
POWER_BASE = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}

# Goblin power-play multiplier per leg (deviation level 1 — most common).
GOBLIN_MOD = 0.900

# Diversity constraints
MAX_PER_GAME = 2
MAX_PER_PLAYER = 1


# ── helpers ────────────────────────────────────────────────────────────────


def _wilson_low(hits: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - spread) / denom)


def _bucket_for(p: float) -> str | None:
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    return None


def _game_key(row: pd.Series) -> str:
    team = str(row.get("team", "") or "").strip().upper()
    opp = str(row.get("opp_team", "") or "").strip().upper()
    if not team and not opp:
        return ""
    pair = tuple(sorted([t for t in (team, opp) if t]))
    return "|".join(pair)


def _ticket_signature(legs: list[dict]) -> str:
    parts = sorted(
        f"{l.get('player','')}|{l.get('prop','')}|{l.get('direction','')}|{l.get('line','')}"
        for l in legs
    )
    return "::".join(parts)


def _payout(legs: list[dict]) -> dict:
    n = len(legs)
    base = POWER_BASE.get(n, POWER_BASE[max(POWER_BASE)])
    mod = 1.0
    for leg in legs:
        pt = str(leg.get("pick_type", "") or "").strip().lower()
        if "gob" in pt:
            mod *= GOBLIN_MOD
    payout = round(base * mod, 2)
    return {"power_payout": payout, "power_base": base, "mod": round(mod, 4)}


def _est_p_hit(row: pd.Series) -> float:
    p = row.get("meta_prob")
    try:
        f = float(p)
    except (TypeError, ValueError):
        f = float("nan")
    if f == f and 0.0 < f < 1.0:
        return f
    override = str(row.get("tier_override", "")).strip().upper()
    return OVERRIDE_PRIOR.get(override, 0.45)


def _eligible_pool(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "strict_label" in df.columns:
        df = df[df["strict_label"].astype(str).str.upper() != "AVOID"].copy()
    elif "tier_override" in df.columns:
        df = df[df["tier_override"].astype(str).str.upper() != "D"].copy()
    df["est_p"] = df.apply(_est_p_hit, axis=1)
    df["bucket"] = df["est_p"].map(_bucket_for)
    df = df[df["bucket"].notna()].copy()
    df["game_key"] = df.apply(_game_key, axis=1)
    df["pp_id"] = df.apply(
        lambda r: f"{r.get('player','')}|{r.get('prop','')}|{r.get('direction','')}|{r.get('line','')}",
        axis=1,
    )
    return df


# ── ticket assembly ────────────────────────────────────────────────────────


def _greedy_pick(
    pool: pd.DataFrame,
    *,
    n_legs: int,
    used_player_counts: dict[str, int] | None = None,
    used_game_counts: dict[str, int] | None = None,
) -> list[dict] | None:
    used_player_counts = dict(used_player_counts or {})
    used_game_counts = dict(used_game_counts or {})
    legs: list[dict] = []
    for _, row in pool.iterrows():
        player = str(row.get("player", "") or "").strip().lower()
        gkey = str(row.get("game_key", "") or "")
        if used_player_counts.get(player, 0) >= MAX_PER_PLAYER:
            continue
        if gkey and used_game_counts.get(gkey, 0) >= MAX_PER_GAME:
            continue
        legs.append(row.to_dict())
        used_player_counts[player] = used_player_counts.get(player, 0) + 1
        if gkey:
            used_game_counts[gkey] = used_game_counts.get(gkey, 0) + 1
        if len(legs) >= n_legs:
            return legs
    return None


def assemble(
    df: pd.DataFrame,
    *,
    sizes: list[int],
    top_per_combo: int,
) -> list[dict]:
    pool_full = _eligible_pool(df)
    out: list[dict] = []
    seen: set[str] = set()

    for bucket_name, _lo, _hi in BUCKETS:
        bucket_pool = pool_full[pool_full["bucket"] == bucket_name].copy()
        if bucket_pool.empty:
            continue
        bucket_pool = bucket_pool.sort_values("est_p", ascending=False).reset_index(drop=True)

        for n_legs in sizes:
            if len(bucket_pool) < n_legs:
                continue
            attempts = 0
            tickets_made = 0
            cursor_offset = 0
            while tickets_made < top_per_combo and cursor_offset < len(bucket_pool):
                # Rotate the starting cursor so subsequent tickets aren't identical.
                rotated = pd.concat(
                    [
                        bucket_pool.iloc[cursor_offset:],
                        bucket_pool.iloc[:cursor_offset],
                    ],
                    ignore_index=True,
                )
                legs = _greedy_pick(rotated, n_legs=n_legs)
                cursor_offset += 1
                attempts += 1
                if not legs or len(legs) < n_legs:
                    if attempts > 50:
                        break
                    continue
                sig = _ticket_signature(legs)
                if sig in seen:
                    if attempts > 50:
                        break
                    continue
                seen.add(sig)
                joint = 1.0
                for leg in legs:
                    joint *= float(leg.get("est_p", 0.0))
                payout = _payout(legs)
                ev = payout["power_payout"] * joint - 1.0  # $1 stake, profit
                ticket = {
                    "size": n_legs,
                    "bucket": bucket_name,
                    "joint_p_hit": round(joint, 4),
                    "power_payout": payout["power_payout"],
                    "expected_profit_per_$1": round(ev, 3),
                    "legs": legs,
                }
                out.append(ticket)
                tickets_made += 1
                if attempts > 200:
                    break
    return out


def _flatten_for_csv(tickets: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for ti, t in enumerate(tickets, start=1):
        for li, leg in enumerate(t["legs"], start=1):
            rows.append(
                {
                    "ticket_id": ti,
                    "size": t["size"],
                    "bucket": t["bucket"],
                    "joint_p_hit": t["joint_p_hit"],
                    "power_payout": t["power_payout"],
                    "expected_profit_per_$1": t["expected_profit_per_$1"],
                    "leg_idx": li,
                    "sport": leg.get("sport"),
                    "player": leg.get("player"),
                    "team": leg.get("team"),
                    "opp_team": leg.get("opp_team"),
                    "prop": leg.get("prop"),
                    "line": leg.get("line"),
                    "direction": leg.get("direction"),
                    "pick_type": leg.get("pick_type"),
                    "tier": leg.get("tier"),
                    "tier_override": leg.get("tier_override"),
                    "ml_prob": leg.get("ml_prob"),
                    "meta_prob": leg.get("meta_prob"),
                    "est_p": round(float(leg.get("est_p", 0.0)), 4),
                    "result": leg.get("result"),
                }
            )
    return pd.DataFrame(rows)


# ── one-day mode + backtest ────────────────────────────────────────────────


def _load_strict(date_str: str) -> pd.DataFrame:
    p = REPO / "outputs" / "strict_mode" / f"strict_{date_str}.csv"
    if not p.is_file():
        raise SystemExit(
            f"Missing {p}. Run scripts/apply_strict_mode.py --date {date_str} first."
        )
    return pd.read_csv(p)


def _ticket_realized_hit(t: dict) -> tuple[bool, int, int, int, int]:
    """Return (all_hit, n_decided, n_hit, n_void, effective_size).

    PrizePicks Power Play void rule: voided legs drop off the slip and the
    ticket pays at the next-smaller leg tier when the remaining (non-void)
    legs all hit. Reflect that here so a 5-leg ticket with 1 void + 4 hits
    counts as a realized win at the 4-leg tier (instead of being silently
    excluded from the backtest).
    """
    n_void = 0
    n_decided = 0
    n_hit = 0
    n_miss = 0
    for leg in t["legs"]:
        res = str(leg.get("result", "")).strip().upper()
        if res in {"VOID", "PUSH", "NO_ACTION", "NO_CONTEST", ""}:
            n_void += 1
            continue
        n_decided += 1
        if res == "HIT":
            n_hit += 1
        elif res == "MISS":
            n_miss += 1
    effective_size = len(t["legs"]) - n_void
    all_eff_hit = (
        effective_size >= 2
        and n_miss == 0
        and n_hit == effective_size
        and n_decided == effective_size
    )
    return all_eff_hit, n_decided, n_hit, n_void, effective_size


def _realized_power_payout(t: dict, effective_size: int) -> float:
    """Equivalent payout multiplier for a ticket after voids drop legs.

    Mirrors PrizePicks' behavior: a 5-leg slip with 1 void pays at the 4-leg
    base when remaining legs all hit. Per-leg Goblin modifier is preserved
    only for non-void legs so the effective multiplier reflects the actual
    surviving slip.
    """
    legs = t.get("legs") or []
    if effective_size == t["size"]:
        return float(t.get("power_payout") or 0.0)
    if effective_size < 2:
        return 1.0  # refund stake
    base_n = POWER_BASE.get(t["size"], 0.0) or 0.0
    base_eff = POWER_BASE.get(effective_size, 0.0) or 0.0
    if base_n <= 0 or base_eff <= 0:
        return 0.0
    full_mod = 1.0
    eff_mod = 1.0
    void_results = {"VOID", "PUSH", "NO_ACTION", "NO_CONTEST", ""}
    for leg in legs:
        pt = str(leg.get("pick_type") or "").strip().lower()
        m = GOBLIN_MOD if "gob" in pt else 1.0
        full_mod *= m
        res = str(leg.get("result") or "").strip().upper()
        if res not in void_results:
            eff_mod *= m
    if full_mod <= 0:
        return 0.0
    banner = float(t.get("power_payout") or 0.0)
    if banner <= 0:
        return 0.0
    return round(banner * (base_eff * eff_mod) / (base_n * full_mod), 4)


def _backtest(since: str, sizes: list[int], top_per_combo: int) -> None:
    rows: list[dict] = []
    for f in sorted((REPO / "outputs" / "strict_mode").glob("strict_*.csv")):
        date_str = f.stem.replace("strict_", "")
        if date_str < since:
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        tickets = assemble(df, sizes=sizes, top_per_combo=top_per_combo)
        for t in tickets:
            all_eff_hit, n_decided, n_hit, n_void, eff_size = _ticket_realized_hit(t)
            # Refunded (effective size < 2): exclude from win/loss accounting.
            if eff_size < 2:
                resolved = None
                realized_pay = 1.0
            else:
                # Decided when every non-void leg has HIT or MISS.
                resolved = (n_decided == eff_size)
                realized_pay = (
                    _realized_power_payout(t, eff_size) if resolved and all_eff_hit else 0.0
                )
            rows.append(
                {
                    "_date": date_str,
                    "size": t["size"],
                    "effective_size": eff_size,
                    "bucket": t["bucket"],
                    "joint_p_hit": t["joint_p_hit"],
                    "power_payout": t["power_payout"],
                    "realized_payout": realized_pay,
                    "n_void": n_void,
                    "n_decided": n_decided,
                    "n_hit": n_hit,
                    "all_hit": int(all_eff_hit) if resolved else None,
                }
            )
    if not rows:
        print("No backtest data.")
        return
    df = pd.DataFrame(rows)
    decided = df[df["all_hit"].notna()].copy()
    summary = (
        decided.groupby(["size", "bucket"], dropna=False)
        .agg(
            n_tickets=("all_hit", "size"),
            all_hit_count=("all_hit", "sum"),
            n_void_tickets=("n_void", lambda s: int((s > 0).sum())),
            avg_joint_pred=("joint_p_hit", "mean"),
            avg_payout=("power_payout", "mean"),
            avg_effective_payout=("realized_payout", "mean"),
        )
        .reset_index()
    )
    summary["realized_all_hit_rate"] = summary["all_hit_count"] / summary["n_tickets"]
    summary["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(summary["all_hit_count"], summary["n_tickets"])
    ]
    # Use the realized (void-aware) per-ticket payout instead of the full-size banner so
    # the EV column reflects PrizePicks' "drop a leg, pay smaller tier" rule.
    summary["realized_ev_per_$1"] = summary["avg_effective_payout"] - 1.0
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
    print(f"\nBacktest tickets across {decided['_date'].nunique()} dates, "
          f"{len(decided):,} fully-decided tickets")
    print("\n=== UNIFORM-BUCKET TICKET BACKTEST ===")
    print(summary.sort_values(["size", "bucket"]).to_string(index=False))
    out_csv = OUT_DIR / "backtest_summary.csv"
    summary.to_csv(out_csv, index=False)
    print(f"\nWrote {out_csv}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Slate date YYYY-MM-DD")
    ap.add_argument("--sizes", nargs="*", type=int, default=[2, 3, 4, 5, 6])
    ap.add_argument("--top-per-combo", type=int, default=10,
                    help="How many tickets to keep per (size, bucket)")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--since", default="2026-04-15")
    args = ap.parse_args()

    if args.backtest:
        _backtest(args.since, args.sizes, args.top_per_combo)
        return 0

    if not args.date:
        ap.error("Provide --date YYYY-MM-DD or --backtest")

    df = _load_strict(args.date)
    print(f"Loaded {len(df):,} strict-mode rows for {args.date}")

    tickets = assemble(df, sizes=args.sizes, top_per_combo=args.top_per_combo)
    print(f"Assembled {len(tickets)} tickets")

    flat = _flatten_for_csv(tickets)
    csv_out = OUT_DIR / f"uniform_tickets_{args.date}.csv"
    flat.to_csv(csv_out, index=False)

    json_out = OUT_DIR / f"uniform_tickets_{args.date}_top.json"
    json_out.write_text(json.dumps(tickets, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {csv_out}")
    print(f"Wrote {json_out}")

    summary = (
        flat.groupby(["size", "bucket"])
        .agg(
            n_tickets=("ticket_id", "nunique"),
            avg_joint_p=("joint_p_hit", "mean"),
            avg_payout=("power_payout", "mean"),
        )
        .reset_index()
    )
    summary["expected_value_per_$1"] = summary["avg_joint_p"] * summary["avg_payout"] - 1.0
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
    print("\n=== TICKET SUMMARY (per size, bucket) ===")
    print(summary.sort_values(["size", "bucket"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
