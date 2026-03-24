import argparse
import json
import random
import re
import sqlite3
import sys
import time
from datetime import timedelta
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any

import pandas as pd
from playwright._impl._errors import TargetClosedError
from playwright.sync_api import Page, Response, sync_playwright
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    Console = None
    Panel = None
    Table = None

try:
    # Optional: reduces simple automation fingerprints.
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None


PRIZEPICKS_URL = "https://app.prizepicks.com/"
SESSION_DIR = Path("browser_session")
DB_PATH = Path("MyTicketPerformance.db")
LEGACY_DB_PATH = Path("PropOracle.db")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
# Replace these defaults with your exact coordinates if desired.
DEFAULT_LATITUDE = 40.7128
DEFAULT_LONGITUDE = -74.0060
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "America/New_York"
GOLD = "#D4AF37"


def _console() -> Any:
    return Console() if Console is not None else None


def maybe_migrate_legacy_db(new_db_path: Path) -> None:
    """
    One-time migration from legacy PropOracle.db -> MyTicketPerformance.db.
    """
    try:
        if new_db_path.exists() or new_db_path.resolve() != DB_PATH.resolve():
            return
        if not LEGACY_DB_PATH.exists():
            return
        import shutil

        shutil.copy2(LEGACY_DB_PATH, new_db_path)
        print(f"Migrated existing DB from {LEGACY_DB_PATH} to {new_db_path}")
    except Exception as exc:
        print(f"Legacy DB migration skipped: {exc}")


def _render_table(df: pd.DataFrame, title: str, style: str = "white") -> None:
    c = _console()
    if c is None or Table is None:
        print(f"\n{title}:")
        print(df.to_string(index=False) if not df.empty else "(no rows)")
        return
    table = Table(title=title, style=style, show_lines=False)
    for col in df.columns:
        table.add_column(str(col))
    for _, row in df.iterrows():
        table.add_row(*[str(row.get(col, "")) for col in df.columns])
    c.print(table)


def get_nested(data: Any, *keys: str) -> Any:
    """Safely retrieve nested dict values."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def parse_entries(payload: Any) -> list[dict[str, Any]]:
    """
    Parse PrizePicks /my-entries payload into flat records.
    Handles common API envelope patterns and nested selections.
    """
    if not isinstance(payload, dict):
        return []

    # Common list locations from JSON APIs.
    entries = (
        get_nested(payload, "data")
        or get_nested(payload, "entries")
        or get_nested(payload, "included")
        or []
    )
    if not isinstance(entries, list):
        return []

    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        attrs = entry.get("attributes", {}) if isinstance(entry.get("attributes"), dict) else {}
        entry_id = entry.get("id") or attrs.get("entry_id")
        status = attrs.get("status") or entry.get("status")

        # Selections can appear in different locations depending on API version.
        selections = (
            attrs.get("selections")
            or attrs.get("legs")
            or entry.get("selections")
            or entry.get("legs")
            or []
        )
        if not isinstance(selections, list):
            selections = []

        if not selections:
            rows.append(
                {
                    "entry_id": entry_id,
                    "player_name": attrs.get("player_name"),
                    "stat_type": attrs.get("stat_type"),
                    "line": attrs.get("line_score") or attrs.get("line"),
                    "status": status,
                }
            )
            continue

        for sel in selections:
            if not isinstance(sel, dict):
                continue
            sel_attrs = sel.get("attributes", {}) if isinstance(sel.get("attributes"), dict) else {}
            rows.append(
                {
                    "entry_id": entry_id,
                    "player_name": (
                        sel_attrs.get("player_name")
                        or get_nested(sel, "new_player", "name")
                        or get_nested(sel, "player", "name")
                        or sel.get("player_name")
                    ),
                    "stat_type": (
                        sel_attrs.get("stat_type")
                        or sel_attrs.get("market")
                        or get_nested(sel, "stat", "type")
                        or sel.get("stat_type")
                    ),
                    "line": sel_attrs.get("line_score") or sel_attrs.get("line") or sel.get("line"),
                    "status": status,
                }
            )

    return rows


def _extract_entry_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = get_nested(payload, "data") or get_nested(payload, "entries") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not candidates:
        candidates = get_nested(payload, "included") or []
    out: list[dict[str, Any]] = []
    for x in candidates:
        if not isinstance(x, dict):
            continue
        typ = str(x.get("type") or "").strip().lower()
        attrs = x.get("attributes", {}) if isinstance(x.get("attributes"), dict) else {}
        # Keep only wager/entry objects; reject projections/predictions payloads.
        if typ in {"new_wager", "entry", "entries", "wager"}:
            out.append(x)
            continue
        if "parlay_count" in attrs or "amount_bet_cents" in attrs or "amount_wagered" in attrs:
            out.append(x)
    return out


def process_and_save(
    json_data: Any, db_path: Path = DB_PATH
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    """
    Extract entry and leg details and store in PropOracle.db.
    Uses INSERT OR IGNORE on entry_id to prevent duplicate entries.
    """
    items = _extract_entry_items(json_data)
    included = get_nested(json_data, "included") if isinstance(json_data, dict) else []
    included = included if isinstance(included, list) else []
    included_by_id: dict[str, dict[str, Any]] = {}
    for obj in included:
        if not isinstance(obj, dict):
            continue
        obj_id = str(obj.get("id") or "").strip()
        if obj_id:
            included_by_id[obj_id] = obj

    entry_rows: list[dict[str, Any]] = []
    leg_rows: list[dict[str, Any]] = []

    for item in items:
        attrs = item.get("attributes", {}) if isinstance(item.get("attributes"), dict) else {}
        entry_id = str(item.get("id") or attrs.get("entry_id") or "").strip()
        if not entry_id:
            continue

        entry_rows.append(
            {
                "entry_id": entry_id,
                "payout": attrs.get("payout") or attrs.get("payout_amount"),
                "amount_staked": attrs.get("amount_wagered") or attrs.get("amount_staked") or attrs.get("wager"),
                "status": attrs.get("status") or item.get("status"),
                "league": attrs.get("league") or attrs.get("league_name"),
                "entry_type": attrs.get("entry_type") or attrs.get("pick_type"),
                "created_at": attrs.get("created_at") or attrs.get("created") or attrs.get("placed_at"),
                "raw_json": json.dumps(item, ensure_ascii=False),
            }
        )

        legs = (
            attrs.get("selections")
            or attrs.get("legs")
            or item.get("selections")
            or item.get("legs")
            or []
        )
        # JSON:API relationships fallback (common for settled entries responses).
        if not legs:
            rel_data = (
                get_nested(item, "relationships", "predictions", "data")
                or get_nested(item, "relationships", "picks", "data")
                or get_nested(item, "relationships", "entries", "data")
            )
            if isinstance(rel_data, list):
                resolved: list[dict[str, Any]] = []
                for rel in rel_data:
                    if not isinstance(rel, dict):
                        continue
                    rel_id = str(rel.get("id") or "").strip()
                    if rel_id and rel_id in included_by_id:
                        resolved.append(included_by_id[rel_id])
                legs = resolved
        if not isinstance(legs, list):
            legs = []

        for idx, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            leg_attrs = leg.get("attributes", {}) if isinstance(leg.get("attributes"), dict) else {}
            new_player_name = None
            proj_stat_type = None
            proj_league = None
            new_player_league = None
            # Resolve related objects for prediction legs.
            np_id = get_nested(leg, "relationships", "new_player", "data", "id")
            if np_id and str(np_id) in included_by_id:
                np_attrs = get_nested(included_by_id[str(np_id)], "attributes") or {}
                if isinstance(np_attrs, dict):
                    new_player_name = np_attrs.get("name") or np_attrs.get("display_name")
                    new_player_league = np_attrs.get("league")
            proj_id = get_nested(leg, "relationships", "projection", "data", "id")
            if proj_id and str(proj_id) in included_by_id:
                pr_attrs = get_nested(included_by_id[str(proj_id)], "attributes") or {}
                if isinstance(pr_attrs, dict):
                    proj_stat_type = pr_attrs.get("stat_display_name") or pr_attrs.get("stat_type")
                    proj_league = pr_attrs.get("league")
            leg_rows.append(
                {
                    "entry_id": entry_id,
                    "leg_index": idx,
                    "player_name": (
                        leg_attrs.get("player_name")
                        or new_player_name
                        or get_nested(leg, "new_player", "name")
                        or get_nested(leg, "player", "name")
                        or leg.get("player_name")
                    ),
                    "stat_type": (
                        leg_attrs.get("stat_type")
                        or proj_stat_type
                        or leg_attrs.get("market")
                        or get_nested(leg, "stat", "type")
                        or leg.get("stat_type")
                    ),
                    "line": leg_attrs.get("line_score") or leg_attrs.get("line") or leg.get("line"),
                    "description": (
                        leg_attrs.get("description")
                        or leg_attrs.get("direction")
                        or leg_attrs.get("wager_type")
                        or leg.get("description")
                        or leg.get("pick")
                    ),
                    "league": (
                        leg_attrs.get("league")
                        or proj_league
                        or new_player_league
                        or attrs.get("league")
                        or attrs.get("league_name")
                    ),
                }
            )

    entries_df = pd.DataFrame(entry_rows)
    legs_df = pd.DataFrame(leg_rows)

    with sqlite3.connect(db_path) as conn:
        before_total = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='user_entries'").fetchone()
        if before_total and before_total[0]:
            total_before_count = int(conn.execute("SELECT COUNT(*) FROM user_entries").fetchone()[0])
        else:
            total_before_count = 0

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_entries (
                entry_id TEXT PRIMARY KEY,
                payout REAL,
                amount_staked REAL,
                status TEXT,
                league TEXT,
                entry_type TEXT,
                created_at TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entry_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                leg_index INTEGER,
                player_name TEXT,
                stat_type TEXT,
                line REAL,
                description TEXT,
                league TEXT,
                UNIQUE(entry_id, leg_index, player_name, stat_type, line, description)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entry_legs_entry ON entry_legs (entry_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entry_legs_player ON entry_legs (player_name)"
        )

        inserted_entries = 0
        if not entries_df.empty:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO user_entries
                (entry_id, payout, amount_staked, status, league, entry_type, created_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(r.get("entry_id")),
                        r.get("payout"),
                        r.get("amount_staked"),
                        r.get("status"),
                        r.get("league"),
                        r.get("entry_type"),
                        r.get("created_at"),
                        r.get("raw_json"),
                    )
                    for _, r in entries_df.iterrows()
                ],
            )
            inserted_entries = max(int(cur.rowcount or 0), 0)

        if not legs_df.empty:
            conn.executemany(
                """
                INSERT OR IGNORE INTO entry_legs
                (entry_id, leg_index, player_name, stat_type, line, description, league)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(r.get("entry_id")),
                        r.get("leg_index"),
                        r.get("player_name"),
                        r.get("stat_type"),
                        r.get("line"),
                        r.get("description"),
                        r.get("league"),
                    )
                    for _, r in legs_df.iterrows()
                ],
            )
        conn.commit()
        total_entries = int(conn.execute("SELECT COUNT(*) FROM user_entries").fetchone()[0])

    print(f"Saved entries to {db_path.resolve()}: {len(entries_df)} entries, {len(legs_df)} legs")
    # rowcount can be unreliable on some sqlite drivers; fallback to count delta.
    if inserted_entries == 0 and total_entries > total_before_count:
        inserted_entries = total_entries - total_before_count
    return entries_df, legs_df, inserted_entries, total_entries


def process_member_transactions(
    json_data: Any,
    db_path: Path = DB_PATH,
    source_url: str = "",
) -> int:
    """
    Persist transaction-log rows and seed entry ids discovered there.
    """
    rows: list[dict[str, Any]] = []
    if isinstance(json_data, dict):
        for key in ("member_transactions", "transactions", "data"):
            candidate = json_data.get(key)
            if isinstance(candidate, list):
                rows = [x for x in candidate if isinstance(x, dict)]
                if rows:
                    break
    elif isinstance(json_data, list):
        rows = [x for x in json_data if isinstance(x, dict)]

    if not rows:
        return 0

    source_month = None
    source_year = None
    try:
        qs = parse_qs(urlparse(source_url).query) if source_url else {}
        source_month = (qs.get("month") or [None])[0]
        source_year_raw = (qs.get("year") or [None])[0]
        source_year = int(source_year_raw) if source_year_raw else None
    except Exception:
        source_month = None
        source_year = None

    tx_rows: list[tuple[Any, ...]] = []
    seed_entries: list[tuple[Any, ...]] = []
    for r in rows:
        base = r.get("data") if isinstance(r.get("data"), dict) else r
        attrs = base.get("attributes", {}) if isinstance(base, dict) and isinstance(base.get("attributes"), dict) else {}

        tx_id = str(
            base.get("id")
            if isinstance(base, dict)
            else r.get("id")
        or r.get("uuid") or r.get("transaction_id") or attrs.get("id") or "").strip()
        action = (
            attrs.get("transaction_type")
            or r.get("action")
            or (base.get("type") if isinstance(base, dict) else None)
            or r.get("type")
            or r.get("event")
            or r.get("description")
        )
        created_at = (
            attrs.get("created_at")
            or r.get("created_at")
            or r.get("createdAt")
            or r.get("time")
            or r.get("timestamp")
            or r.get("occurred_at")
        )
        amount = attrs.get("amount_cents") or r.get("amount") or r.get("amount_cents") or r.get("delta")
        balance_before = attrs.get("credit_at_time_cents") or r.get("balance_before") or r.get("before_balance")
        balance_after = attrs.get("promo_at_time_cents") or r.get("balance_after") or r.get("after_balance") or r.get("balance")
        entry_id = (
            get_nested(attrs, "object", "data", "id")
            or get_nested(attrs, "object_id")
            or
            r.get("entry_id")
            or r.get("new_wager_id")
            or r.get("wager_id")
            or get_nested(r, "new_wager", "id")
            or get_nested(r, "entry", "id")
        )
        tx_rows.append(
            (
                tx_id if tx_id else None,
                str(action) if action is not None else None,
                created_at,
                amount,
                balance_before,
                balance_after,
                str(entry_id) if entry_id is not None else None,
                source_month,
                source_year,
                json.dumps(r, ensure_ascii=False),
            )
        )
        if entry_id:
            seed_entries.append(
                (
                    str(entry_id),
                    None,
                    None,
                    "UNKNOWN",
                    None,
                    None,
                    created_at,
                    json.dumps({"seeded_from": "member_transactions", "row": r}, ensure_ascii=False),
                )
            )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS member_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id TEXT,
                action TEXT,
                created_at TEXT,
                amount REAL,
                balance_before REAL,
                balance_after REAL,
                entry_id TEXT,
                source_month TEXT,
                source_year INTEGER,
                raw_json TEXT
            )
            """
        )
        # Backward-compatible in case table existed before source_* columns.
        try:
            conn.execute("ALTER TABLE member_transactions ADD COLUMN source_month TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE member_transactions ADD COLUMN source_year INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_member_tx_entry ON member_transactions(entry_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_member_tx_created ON member_transactions(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_member_tx_source ON member_transactions(source_year, source_month)")
        conn.executemany(
            """
            INSERT INTO member_transactions
            (tx_id, action, created_at, amount, balance_before, balance_after, entry_id, source_month, source_year, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tx_rows,
        )
        if seed_entries:
            conn.executemany(
                """
                INSERT OR IGNORE INTO user_entries
                (entry_id, payout, amount_staked, status, league, entry_type, created_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                seed_entries,
            )
        conn.commit()
    print(f"Saved member_transactions rows: {len(tx_rows)}")
    return len(tx_rows)


def _load_projection_frame(conn: sqlite3.Connection) -> tuple[pd.DataFrame, str]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    candidate_tables = []
    for t in tables:
        cols = {row[1].lower() for row in conn.execute(f"PRAGMA table_info('{t}')")}
        if {"player_name", "edge", "rank"}.issubset(cols) or {"player", "edge", "rank"}.issubset(cols):
            candidate_tables.append(t)
    if not candidate_tables:
        return pd.DataFrame(), ""
    proj_table = "projections" if "projections" in candidate_tables else candidate_tables[0]
    projections = pd.read_sql_query(f"SELECT * FROM '{proj_table}'", conn)
    if projections.empty:
        return projections, proj_table
    projections.columns = [c.lower() for c in projections.columns]

    player_col = "player_name" if "player_name" in projections.columns else "player"
    projections["player_key"] = projections[player_col].astype(str).str.strip().str.lower()
    stat_col = "stat_type" if "stat_type" in projections.columns else ("prop_type" if "prop_type" in projections.columns else "")
    projections["stat_type_key"] = (
        projections[stat_col].astype(str).str.strip().str.lower() if stat_col else ""
    )
    projections["league_key"] = (
        projections["league"].astype(str).str.strip().str.upper()
        if "league" in projections.columns
        else ""
    )
    if "created_at" in projections.columns:
        projections["projection_created_at"] = pd.to_datetime(
            projections["created_at"], errors="coerce", utc=True
        )
    else:
        projections["projection_created_at"] = pd.NaT
    if "line" in projections.columns:
        projections["projection_line"] = pd.to_numeric(projections["line"], errors="coerce")
    else:
        projections["projection_line"] = pd.NA
    projections["edge"] = pd.to_numeric(projections.get("edge"), errors="coerce")
    return projections, proj_table


def _resolve_projection_matches(legs: pd.DataFrame, projections: pd.DataFrame) -> pd.DataFrame:
    if legs.empty or projections.empty:
        return pd.DataFrame()
    legs = legs.copy()
    legs["entry_created_at"] = pd.to_datetime(legs.get("created_at"), errors="coerce", utc=True)
    legs["player_key"] = legs["player_name"].astype(str).str.strip().str.lower()
    legs["stat_type_key"] = legs["stat_type"].fillna("").astype(str).str.strip().str.lower()
    legs["league_key"] = legs["league"].fillna("").astype(str).str.strip().str.upper()
    legs["entry_line"] = pd.to_numeric(legs.get("line"), errors="coerce")

    # Stat category lock stays strict by including stat_type_key in joins.
    merged = legs.merge(
        projections,
        on=["player_key", "stat_type_key", "league_key"],
        how="left",
        suffixes=("", "_proj"),
    )
    if merged["edge"].isna().all():
        merged = legs.merge(
            projections,
            on=["player_key", "stat_type_key"],
            how="left",
            suffixes=("", "_proj"),
        )

    # Timestamp window: projection must be within 6h before entry time.
    has_time = merged["entry_created_at"].notna() & merged["projection_created_at"].notna()
    merged["time_ok"] = True
    merged.loc[has_time, "time_ok"] = (
        (merged.loc[has_time, "projection_created_at"] <= merged.loc[has_time, "entry_created_at"])
        & (
            merged.loc[has_time, "projection_created_at"]
            >= (merged.loc[has_time, "entry_created_at"] - timedelta(hours=6))
        )
    )
    if has_time.any():
        merged = merged[merged["time_ok"] | merged["projection_created_at"].isna()]

    # If multiple projections match, pick closest prior projection in window.
    if not merged.empty:
        merged["age_sec"] = (
            merged["entry_created_at"] - merged["projection_created_at"]
        ).dt.total_seconds()
        merged["age_sec"] = pd.to_numeric(merged["age_sec"], errors="coerce")
        merged = merged.sort_values(
            by=["entry_id", "leg_index", "age_sec"],
            ascending=[True, True, True],
            na_position="last",
        )
        merged = merged.drop_duplicates(subset=["entry_id", "leg_index"], keep="first")

    # Line movement tracker
    bet_side = merged.get("description", pd.Series("", index=merged.index)).astype(str).str.lower()
    proj_line = pd.to_numeric(merged.get("projection_line"), errors="coerce")
    entry_line = pd.to_numeric(merged.get("entry_line"), errors="coerce")
    merged["line_diff"] = entry_line - proj_line
    merged["line_value"] = merged["line_diff"]
    over_mask = bet_side.str.contains("over")
    under_mask = bet_side.str.contains("under")
    merged.loc[over_mask, "line_value"] = proj_line[over_mask] - entry_line[over_mask]
    merged.loc[under_mask, "line_value"] = entry_line[under_mask] - proj_line[under_mask]
    return merged


def compare_entries_with_projections(db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    Compare entry legs against projections table and print edge/rank summary.
    Tries `projections` table first; falls back to any table with player+edge+rank.
    """
    with sqlite3.connect(db_path) as conn:
        legs = pd.read_sql_query(
            """
            SELECT l.entry_id, l.leg_index, l.player_name, l.stat_type, l.line, l.description, l.league, e.created_at, e.status
            FROM entry_legs l
            LEFT JOIN user_entries e ON e.entry_id = l.entry_id
            """,
            conn,
        )
        projections, proj_table = _load_projection_frame(conn)
        if legs.empty:
            print("No legs found in database yet. Capture entries first.")
            return legs
        if projections.empty:
            print("No projections table with [player_name/player, edge, rank] found in PropOracle.db.")
            return pd.DataFrame()

    merged = _resolve_projection_matches(legs, projections)
    if merged.empty:
        print("No projection matches found after stat/time-window integrity checks.")
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "Player": merged.get("player_name"),
            "Line": merged.get("entry_line"),
            "Model Edge %": pd.to_numeric(merged.get("edge"), errors="coerce") * 100.0,
            "Rank": merged.get("rank"),
            "Bet": merged.get("description"),
            "Projection Line": pd.to_numeric(merged.get("projection_line"), errors="coerce"),
            "Line Value": pd.to_numeric(merged.get("line_value"), errors="coerce").round(2),
            "Status": merged.get("status"),
        }
    )
    out["Model Edge %"] = out["Model Edge %"].round(2)
    bet = out["Bet"].fillna("").astype(str).str.lower()
    edge = pd.to_numeric(out["Model Edge %"], errors="coerce")
    out["Against Model"] = (
        (bet.str.contains("over") & (edge < 0))
        | (bet.str.contains("under") & (edge > 0))
    )

    printable = out[
        ["Player", "Line", "Projection Line", "Line Value", "Model Edge %", "Rank", "Against Model", "Status"]
    ]
    _render_table(
        printable,
        f"Model Comparison ({proj_table})",
        style=GOLD,
    )

    against = printable[printable["Against Model"] == True]  # noqa: E712
    if not against.empty:
        _render_table(against, "Against-Model Legs", style="red")
    else:
        print("\nNo obvious against-model legs detected from edge sign.")

    return printable


def calculate_roi(db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    ROI tracker by league and entry type.
    Assumes payout includes stake return (common sportsbook convention).
    profit_loss = payout - amount_staked.
    """
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT
                COALESCE(NULLIF(TRIM(league), ''), 'UNKNOWN') AS league,
                COALESCE(NULLIF(TRIM(entry_type), ''), 'UNKNOWN') AS entry_type,
                COALESCE(CAST(amount_staked AS REAL), 0.0) AS amount_staked,
                COALESCE(CAST(payout AS REAL), 0.0) AS payout
            FROM user_entries
            """,
            conn,
        )

    if df.empty:
        print("No user_entries found for ROI calculation yet.")
        return df

    df["profit_loss"] = df["payout"] - df["amount_staked"]
    summary = (
        df.groupby(["league", "entry_type"], dropna=False)
        .agg(
            entries=("entry_type", "size"),
            total_staked=("amount_staked", "sum"),
            total_payout=("payout", "sum"),
            profit_loss=("profit_loss", "sum"),
        )
        .reset_index()
    )
    summary["roi_pct"] = summary.apply(
        lambda r: (r["profit_loss"] / r["total_staked"] * 100.0) if r["total_staked"] else 0.0,
        axis=1,
    )
    summary["roi_pct"] = summary["roi_pct"].round(2)

    _render_table(summary, "ROI by League and Entry Type", style=GOLD)
    return summary


def sync_to_sqlite(df: pd.DataFrame) -> None:
    """Backward-compatible placeholder wrapper."""
    _ = df
    print("sync_to_sqlite() retained for compatibility. Use process_and_save() for full ingest.")


def export_run_cover_report(db_path: Path = DB_PATH) -> Path:
    """
    Build a lightweight run summary artifact (CSV) in project root.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(f"MyTicketPerformance_summary_{ts}.csv")
    with sqlite3.connect(db_path) as conn:
        entries = conn.execute("SELECT COUNT(*) FROM user_entries").fetchone()[0]
        legs = conn.execute("SELECT COUNT(*) FROM entry_legs").fetchone()[0]
        tx = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='member_transactions'"
        ).fetchone()[0]
        tx_count = (
            conn.execute("SELECT COUNT(*) FROM member_transactions").fetchone()[0] if tx else 0
        )
        # Month summary from transaction-log source.
        month_rows = (
            conn.execute(
                """
                SELECT
                    COALESCE(source_year, 0) AS source_year,
                    COALESCE(source_month, 'UNKNOWN') AS source_month,
                    COUNT(*) AS tx_rows
                FROM member_transactions
                GROUP BY source_year, source_month
                """
            ).fetchall()
            if tx
            else []
        )

    base_rows = [
        {"section": "totals", "metric": "user_entries", "value": entries},
        {"section": "totals", "metric": "entry_legs", "value": legs},
        {"section": "totals", "metric": "member_transactions", "value": tx_count},
    ]
    month_detail = [
        {
            "section": "month_coverage",
            "metric": f"{yr}-{mo}",
            "value": cnt,
        }
        for (yr, mo, cnt) in month_rows
    ]
    df = pd.DataFrame(base_rows + month_detail, columns=["section", "metric", "value"])
    df.to_csv(out_path, index=False)
    print(f"Cover report written: {out_path.resolve()}")
    return out_path


def attach_response_listener(
    page: Page,
    captured: dict[str, Any],
    on_payload: Any | None = None,
    discovery_mode: bool = False,
) -> None:
    """Attach response listener and process every /my-entries payload."""
    entry_markers = ("/my-entries", "/entries?", "/v1/entries")
    projection_markers = ("/projections", "/v1/projections")
    tx_markers = ("/member_transactions",)

    def handle_response(response: Response) -> None:
        url = response.url
        if discovery_mode and "api.prizepicks.com" in url:
            ctype = response.headers.get("content-type", "")
            if "json" in ctype:
                print(f"[DISCOVERY] {response.status} {url}")
        is_api = "api.prizepicks.com" in url
        is_entry = is_api and any(marker in url for marker in entry_markers)
        is_projection = is_api and any(marker in url for marker in projection_markers)
        is_tx = any(marker in url for marker in tx_markers)
        if not (is_entry or is_tx or is_projection):
            return
        if response.status != 200:
            return

        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - runtime safety
            print(f"Failed to decode JSON for {url}: {exc}")
            return

        captured["last_capture_ts"] = time.time()
        if is_entry:
            captured["payload"] = payload
            print(f"Captured entries response from: {url}")
        elif is_projection:
            print(f"Captured projections response from: {url}")
        elif is_tx:
            print(f"Captured member_transactions response from: {url}")
        if callable(on_payload):
            on_payload(payload, url)

    page.on("response", handle_response)


def wait_for_manual_interaction(page: Page, timeout_seconds: int = 180) -> None:
    """
    Wait until a real user clicks/keys once in the page.
    This avoids bot-like "instant automation" behavior.
    """
    page.evaluate(
        """
        () => {
            if (window.__human_interacted_installed) return;
            window.__human_interacted_installed = true;
            window.__human_interacted = false;
            const mark = () => {
                window.__human_interacted = true;
            };
            window.addEventListener('pointerdown', mark, { once: true, capture: true });
            window.addEventListener('keydown', mark, { once: true, capture: true });
        }
        """
    )
    print("Waiting for your first manual interaction (click or key press)...")
    start = time.time()
    while time.time() - start < timeout_seconds:
        if page.evaluate("() => Boolean(window.__human_interacted)"):
            print("Manual interaction detected. Continuing.")
            return
        page.wait_for_timeout(500)
    print("No manual interaction detected within timeout; continuing anyway.")


def run_diagnostics(
    page: Page,
    expected_timezone: str = DEFAULT_TIMEZONE,
    pause_on_warning: bool = False,
) -> dict[str, Any]:
    """
    Preflight browser geolocation diagnostics before PrizePicks navigation.
    """
    print("\nRunning geolocation diagnostics...")
    diag = page.evaluate(
        """
        async () => {
            const out = {
                permission_state: "unknown",
                latitude: null,
                longitude: null,
                accuracy_m: null,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown",
                position_error: null,
            };

            try {
                const perm = await navigator.permissions.query({ name: "geolocation" });
                out.permission_state = perm.state || "unknown";
            } catch (err) {
                out.position_error = `permission_query_failed: ${String(err)}`;
            }

            try {
                const pos = await new Promise((resolve, reject) => {
                    navigator.geolocation.getCurrentPosition(resolve, reject, {
                        enableHighAccuracy: true,
                        timeout: 12000,
                        maximumAge: 0,
                    });
                });
                out.latitude = pos.coords.latitude;
                out.longitude = pos.coords.longitude;
                out.accuracy_m = pos.coords.accuracy;
            } catch (err) {
                out.position_error = out.position_error || `position_failed: ${String(err)}`;
            }

            return out;
        }
        """
    )

    print(f"Geolocation permission: {diag.get('permission_state')}")
    print(
        "Reported position: "
        f"lat={diag.get('latitude')}, lon={diag.get('longitude')}, "
        f"accuracy_m={diag.get('accuracy_m')}"
    )
    print(f"Reported timezone: {diag.get('timezone')}")
    if diag.get("position_error"):
        print(f"Position error: {diag.get('position_error')}")
    if str(diag.get("timezone", "")).strip() != expected_timezone:
        print(
            f"Warning: timezone mismatch (expected {expected_timezone}, "
            f"got {diag.get('timezone')})."
        )

    accuracy = diag.get("accuracy_m")
    denied = str(diag.get("permission_state", "")).lower() == "denied"
    too_inaccurate = isinstance(accuracy, (int, float)) and accuracy > 1000
    if denied or too_inaccurate:
        print(
            "\nGeolocation preflight warning: permission denied or accuracy > 1000m.\n"
            "Please check Windows Location settings, Wi-Fi/location services, and browser permissions."
        )
        if pause_on_warning:
            try:
                input("Press Enter to continue after checks...")
            except EOFError:
                print("Non-interactive shell detected; continuing without pause.")

    return diag


def harvest_history(
    page: Page,
    shared_state: dict[str, Any],
    *,
    scroll_px: int = 800,
    sleep_seconds: float = 1.5,
    max_no_new_scrolls: int = 5,
) -> None:
    """
    Scroll through My Entries and stop when no new entries are found repeatedly.
    """
    c = _console()
    no_new_scrolls = 0
    last_seen_new = int(shared_state.get("new_slips_found", 0))

    print("Starting history harvest scroll...")
    while no_new_scrolls < max_no_new_scrolls:
        page.mouse.wheel(0, scroll_px)
        page.wait_for_timeout(int(sleep_seconds * 1000))

        current_new = int(shared_state.get("new_slips_found", 0))
        total_db = int(shared_state.get("total_in_db", 0))
        if current_new > last_seen_new:
            no_new_scrolls = 0
            last_seen_new = current_new
        else:
            no_new_scrolls += 1

        msg = f"New Slips Found: {current_new} | Total in DB: {total_db} | No-new scrolls: {no_new_scrolls}/{max_no_new_scrolls}"
        if c is not None:
            c.print(f"[{GOLD}]{msg}[/{GOLD}]")
        else:
            print(msg)

    print("Stopped scrolling: no new entries detected for 5 consecutive scrolls.")


def launch_persistent_context_with_fallback(playwright: Any, session_dir: Path, args: Any) -> Any:
    """
    Launch persistent context with retries for fragile platform-specific options.
    """
    base_kwargs = {
        "user_data_dir": str(session_dir.resolve()),
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
        "user_agent": args.user_agent,
        "args": ["--disable-blink-features=AutomationControlled"],
        "ignore_https_errors": True,
        "chromium_sandbox": True,
    }
    last_exc: Exception | None = None

    try:
        return playwright.chromium.launch_persistent_context(
            **base_kwargs,
            geolocation={
                "latitude": args.latitude,
                "longitude": args.longitude,
                "accuracy": 30,
            },
            locale=args.locale,
            timezone_id=args.timezone_id,
        )
    except Exception as exc:
        last_exc = exc
        print(f"Primary launch failed; retrying with safe fallback. Reason: {exc}")

    # Attempt 2: bundled chromium with minimal options.
    try:
        return playwright.chromium.launch_persistent_context(**base_kwargs)
    except Exception as exc:
        last_exc = exc
        print(f"Fallback launch failed; retrying with local Chrome channel. Reason: {exc}")

    # Attempt 3: system Chrome channel.
    try:
        return playwright.chromium.launch_persistent_context(**base_kwargs, channel="chrome")
    except Exception as exc:
        last_exc = exc
        print(f"Chrome channel launch failed; retrying with Edge channel. Reason: {exc}")

    # Attempt 4: system Edge channel.
    try:
        return playwright.chromium.launch_persistent_context(**base_kwargs, channel="msedge")
    except Exception as exc:
        last_exc = exc
        raise RuntimeError(
            "Unable to launch any persistent browser context (bundled Chromium, Chrome, Edge)."
        ) from last_exc


def attach_over_cdp(playwright: Any, cdp_url: str) -> tuple[Any, Any, Any]:
    """
    Attach to an already-running Chromium browser via CDP.
    Returns (browser, context, page).
    """
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()
    if context.pages:
        tx_pages = [p for p in context.pages if "prizepicks.com/transaction-log" in (p.url or "")]
        if tx_pages:
            page = tx_pages[-1]
        else:
            pp_pages = [p for p in context.pages if "prizepicks.com" in (p.url or "")]
            page = pp_pages[-1] if pp_pages else context.pages[-1]
    else:
        page = context.new_page()
    return browser, context, page


def trigger_entry_filters(page: Page) -> None:
    """
    Best-effort trigger of entry API filters from browser context.
    Responses are still captured by the network listener.
    """
    print("Triggering entry filter fetches (pending/settled/won/lost/all)...")
    script = """
    async () => {
        const filters = ["pending", "settled", "won", "lost", "all"];
        for (const f of filters) {
            try {
                await fetch(`https://api.prizepicks.com/v1/entries?filter=${f}`, {
                    credentials: "include",
                    headers: { "accept": "application/json" },
                });
            } catch (_) {
                // Best effort only.
            }
        }
    }
    """
    try:
        page.evaluate(script)
    except Exception as exc:
        print(f"Entry filter trigger skipped due to navigation race: {exc}")


def trigger_member_transactions_history(page: Page, months_back: int = 24) -> None:
    """
    Trigger transaction-log requests across recent months.
    """
    print(f"Triggering member_transactions for {months_back} month(s)...")
    page.evaluate(
        """
        async (monthsBack) => {
            const monthNames = [
                "January","February","March","April","May","June",
                "July","August","September","October","November","December"
            ];
            const now = new Date();
            for (let i = 0; i < monthsBack; i += 1) {
                const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
                const month = monthNames[d.getMonth()];
                const year = d.getFullYear();
                try {
                    await fetch(`https://api.prizepicks.com/member_transactions?month=${encodeURIComponent(month)}&year=${encodeURIComponent(year)}`, {
                        credentials: "include",
                        headers: { "accept": "application/json" },
                    });
                } catch (_) {
                    // Best effort.
                }
            }
        }
        """,
        int(max(1, months_back)),
    )


def trigger_member_transactions_month_year(page: Page, month: str, year: int) -> None:
    """
    Trigger a specific member_transactions request for targeted rehydration.
    """
    if not month or not year:
        return
    print(f"Triggering member_transactions for {month} {year}...")
    page.evaluate(
        """
        async ({month, year}) => {
            try {
                await fetch(`https://api.prizepicks.com/member_transactions?month=${encodeURIComponent(month)}&year=${encodeURIComponent(year)}`, {
                    credentials: "include",
                    headers: { "accept": "application/json" },
                });
            } catch (_) {
                // Best effort.
            }
        }
        """,
        {"month": month, "year": int(year)},
    )


def harvest_transaction_log_details(
    page: Page,
    *,
    max_passes: int = 30,
    scroll_px: int = 950,
    pause_ms: int = 900,
) -> int:
    """
    Expand transaction-log "VIEW DETAILS" cards to trigger entry detail payloads.
    This avoids opening entry pages and keeps the active tab stable.
    """
    print(f"Harvesting transaction-log detail cards (passes={max_passes})...")
    if int(max_passes) <= 0:
        print("Transaction-log detail click pass skipped (passes=0).")
        return 0
    total_clicked = 0
    idle_passes = 0
    for _ in range(max(0, int(max_passes))):
        try:
            clicked_now = int(
                page.evaluate(
                    """
                    () => {
                        const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
                        const nodes = Array.from(document.querySelectorAll("*"));
                        const candidates = nodes.filter((el) => {
                            const text = norm(el.innerText || el.textContent || "");
                            if (!text.includes("view details")) return false;
                            if (text.length > 32) return false;
                            const rect = el.getBoundingClientRect();
                            if (!(rect.width > 6 && rect.height > 6)) return false;
                            if (rect.width > 360 || rect.height > 120) return false;
                            return true;
                        });
                        // Deduplicate layered wrappers by rounded on-screen position.
                        const seen = new Set();
                        const targets = [];
                        for (const el of candidates) {
                            const r = el.getBoundingClientRect();
                            const key = `${Math.round(r.x/4)}:${Math.round(r.y/4)}`;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            targets.push({el, area: r.width * r.height});
                        }
                        targets.sort((a, b) => a.area - b.area);
                        let clicked = 0;
                        for (const t of targets) {
                            const el = t.el;
                            if (el.dataset.poClicked === "1") continue;
                            el.dataset.poClicked = "1";
                            el.click();
                            clicked += 1;
                        }
                        return clicked;
                    }
                    """
                )
            )
        except Exception:
            clicked_now = 0

        if clicked_now > 0:
            total_clicked += clicked_now
            idle_passes = 0
        else:
            idle_passes += 1

        page.wait_for_timeout(max(200, int(pause_ms)))
        page.mouse.wheel(0, int(scroll_px))
        page.wait_for_timeout(max(200, int(pause_ms)))

        if idle_passes >= 6:
            break

    print(f"Transaction-log detail clicks attempted: {total_clicked}")
    return total_clicked


def extract_transaction_log_dom_legs(page: Page, db_path: Path = DB_PATH) -> int:
    """
    Parse expanded transaction-log cards from DOM and persist legs as fallback.
    """
    print("Extracting legs from transaction-log DOM...")
    try:
        page.wait_for_timeout(1200)
        page.wait_for_function(
            "() => ((document.body && document.body.innerText) || '').toLowerCase().includes('view details')",
            timeout=5000,
        )
    except Exception:
        pass
    raw_cards = page.evaluate(
        """
        () => {
            const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
            const hasCardShape = (txt) =>
                (txt.includes("lineup won") || txt.includes("lineup placed")) &&
                txt.includes("view details") &&
                /\\d{2}\\/\\d{2}\\/\\d{4}\\s+at\\s+\\d{1,2}:\\d{2}\\s*[ap]m/.test(txt);
            // Prefer row/card containers over tiny text nodes.
            const nodes = Array.from(document.querySelectorAll("div, section, article, li"))
                .filter((el) => {
                    if (!isVisible(el)) return false;
                    const t = norm(el.innerText || el.textContent || "");
                    if (!t || t.length < 80) return false;
                    return hasCardShape(t);
                });
            const out = [];
            const seen = new Set();
            for (const n of nodes) {
                if (out.length >= 120) break;
                const text = (n.innerText || "").trim();
                if (!text) continue;
                const head = text.split("\\n").slice(0, 4).join("|");
                if (seen.has(head)) continue;
                seen.add(head);
                const attrs = [];
                const els = [n, ...Array.from(n.querySelectorAll("*"))];
                for (const el of els) {
                    for (const a of el.getAttributeNames ? el.getAttributeNames() : []) {
                        const v = el.getAttribute(a);
                        if (!v) continue;
                        attrs.push(String(v));
                    }
                }
                out.push({
                    text,
                    attrs: attrs.slice(0, 300),
                });
            }
            return out;
        }
        """
    )
    if not isinstance(raw_cards, list) or not raw_cards:
        print("DOM leg extraction found no cards.")
        return 0

    with sqlite3.connect(db_path) as conn:
        known_entries = []
        for r in conn.execute(
            "SELECT entry_id, created_at, amount_staked FROM user_entries WHERE entry_id IS NOT NULL"
        ).fetchall():
            eid = str(r[0] or "").strip()
            created_raw = str(r[1] or "").strip()
            amount_staked = float(r[2]) if r[2] is not None else None
            if not eid:
                continue
            created_dt = None
            if created_raw:
                try:
                    created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    try:
                        created_dt = datetime.fromisoformat(created_raw).replace(tzinfo=None)
                    except Exception:
                        created_dt = None
            known_entries.append((eid, created_raw, amount_staked, created_dt))

    pick_re = re.compile(
        r"(?i)\\b(more than|less than|higher|lower)\\b|\\b(pts|reb|ast|shots|goals|hits|saves|pass|rush|rec|fantasy|3pt|3-pt)\\b"
    )
    id_re = re.compile(r"\\b\\d{6,}\\b")
    skip_words = ("lineup", "view details", "before:", "balance:", "pick power", "pick flex", "$")

    legs: list[dict[str, Any]] = []
    staged_rows: list[dict[str, Any]] = []
    card_snapshots: list[dict[str, Any]] = []
    missing_id_samples: list[str] = []
    known_ids = {e[0] for e in known_entries}
    for card in raw_cards:
        if not isinstance(card, dict):
            continue
        text = str(card.get("text") or "")
        attrs = card.get("attrs") if isinstance(card.get("attrs"), list) else []
        lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
        header = " | ".join(lines[:4])
        card_snapshots.append(
            {
                "card_header": header,
                "raw_card_text": text[:12000],
                "candidate_ids": ",".join(sorted(set(id_re.findall(text)))[:20]),
            }
        )

        candidates = set(id_re.findall(text))
        for a in attrs:
            candidates.update(id_re.findall(str(a)))
        entry_id = ""
        if candidates:
            entry_id = next((cid for cid in candidates if cid in known_ids), "")

        # Fallback: map card header datetime+stake to closest known entry.
        dt_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)", header, flags=re.I)
        money_match = re.search(r"[-+]?\s*\$\s*(\d+(?:\.\d{1,2})?)", header)
        header_dt = None
        stake_val = None
        entry_datetime_text = ""
        if dt_match:
            entry_datetime_text = f"{dt_match.group(1)} {dt_match.group(2).upper().replace('  ', ' ')}"
            try:
                header_dt = datetime.strptime(entry_datetime_text, "%m/%d/%Y %I:%M %p")
            except Exception:
                header_dt = None
        if money_match:
            try:
                stake_val = abs(float(money_match.group(1)))
            except Exception:
                stake_val = None

        if not entry_id:
            if header_dt is not None:
                best = None
                best_score = None
                for eid, _created_raw, amount_staked, created_dt in known_entries:
                    if created_dt is None:
                        continue
                    diff_sec = abs((created_dt - header_dt).total_seconds())
                    if diff_sec > 12 * 3600:
                        continue
                    amt_penalty = 0.0
                    if stake_val is not None and amount_staked is not None:
                        amt_penalty = abs(float(amount_staked) - float(stake_val)) * 600.0
                    score = diff_sec + amt_penalty
                    if best_score is None or score < best_score:
                        best_score = score
                        best = eid
                if best:
                    entry_id = str(best)
        for i in range(1, len(lines)):
            desc = lines[i].strip()
            prev = lines[i - 1].strip()
            prev_l = prev.lower()
            desc_l = desc.lower()
            if not pick_re.search(desc):
                continue
            if any(w in prev_l for w in skip_words):
                continue
            if any(w in desc_l for w in ("lineup ", "view details", "before:", "balance:")):
                continue
            m = re.search(r"(-?\\d+(?:\\.\\d+)?)", desc)
            line_val = float(m.group(1)) if m else None
            stat_type = re.sub(r"(?i)\\b(more than|less than|higher|lower)\\b", "", desc)
            stat_type = re.sub(r"\\d+(?:\\.\\d+)?", "", stat_type).strip()
            staged_rows.append(
                {
                    "entry_datetime_text": entry_datetime_text or None,
                    "stake_amount": stake_val,
                    "card_header": header,
                    "player_name": prev,
                    "pick_text": desc,
                    "line": line_val,
                    "stat_type": stat_type or None,
                    "raw_card_text": text[:5000],
                    "mapped_entry_id": entry_id or None,
                }
            )
            if not entry_id:
                continue
            legs.append(
                {
                    "entry_id": entry_id,
                    "leg_index": i - 1,
                    "player_name": prev,
                    "stat_type": stat_type or None,
                    "line": line_val,
                    "description": desc,
                    "league": None,
                }
            )

    inserted = 0
    staged_inserted = 0
    cards_inserted = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_log_cards_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT DEFAULT (datetime('now')),
                card_header TEXT,
                raw_card_text TEXT,
                candidate_ids TEXT,
                UNIQUE(card_header, raw_card_text)
            )
            """
        )
        if card_snapshots:
            cur_cards = conn.executemany(
                """
                INSERT OR IGNORE INTO transaction_log_cards_staging
                (card_header, raw_card_text, candidate_ids)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        r["card_header"],
                        r["raw_card_text"],
                        r["candidate_ids"],
                    )
                    for r in card_snapshots
                ],
            )
            cards_inserted = max(int(cur_cards.rowcount or 0), 0)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_log_legs_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT DEFAULT (datetime('now')),
                entry_datetime_text TEXT,
                stake_amount REAL,
                card_header TEXT,
                player_name TEXT,
                pick_text TEXT,
                line REAL,
                stat_type TEXT,
                raw_card_text TEXT,
                mapped_entry_id TEXT,
                UNIQUE(entry_datetime_text, stake_amount, player_name, pick_text)
            )
            """
        )
        cur_stage = conn.executemany(
            """
            INSERT OR IGNORE INTO transaction_log_legs_staging
            (entry_datetime_text, stake_amount, card_header, player_name, pick_text, line, stat_type, raw_card_text, mapped_entry_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["entry_datetime_text"],
                    r["stake_amount"],
                    r["card_header"],
                    r["player_name"],
                    r["pick_text"],
                    r["line"],
                    r["stat_type"],
                    r["raw_card_text"],
                    r["mapped_entry_id"],
                )
                for r in staged_rows
            ],
        )
        staged_inserted = max(int(cur_stage.rowcount or 0), 0)
        # Try mapping unmapped staged rows on each run so future runs improve coverage.
        pending = conn.execute(
            "SELECT id, entry_datetime_text, stake_amount FROM transaction_log_legs_staging WHERE mapped_entry_id IS NULL"
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for sid, entry_dt_text, stake_amt in pending:
            if not entry_dt_text:
                continue
            try:
                header_dt = datetime.strptime(str(entry_dt_text), "%m/%d/%Y %I:%M %p")
            except Exception:
                continue
            best = None
            best_score = None
            for eid, _created_raw, amount_staked, created_dt in known_entries:
                if created_dt is None:
                    continue
                diff_sec = abs((created_dt - header_dt).total_seconds())
                if diff_sec > 12 * 3600:
                    continue
                amt_penalty = 0.0
                if stake_amt is not None and amount_staked is not None:
                    amt_penalty = abs(float(amount_staked) - float(stake_amt)) * 600.0
                score = diff_sec + amt_penalty
                if best_score is None or score < best_score:
                    best_score = score
                    best = eid
            if best:
                updates.append((str(best), int(sid)))
        if updates:
            conn.executemany(
                "UPDATE transaction_log_legs_staging SET mapped_entry_id = ? WHERE id = ?",
                updates,
            )

        if legs:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO entry_legs
                (entry_id, leg_index, player_name, stat_type, line, description, league)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["entry_id"],
                        r["leg_index"],
                        r["player_name"],
                        r["stat_type"],
                        r["line"],
                        r["description"],
                        r["league"],
                    )
                    for r in legs
                ],
            )
            inserted = max(int(cur.rowcount or 0), 0)
        mapped = conn.execute(
            """
            SELECT mapped_entry_id, player_name, stat_type, line, pick_text
            FROM transaction_log_legs_staging
            WHERE mapped_entry_id IS NOT NULL
            """
        ).fetchall()
        if mapped:
            cur2 = conn.executemany(
                """
                INSERT OR IGNORE INTO entry_legs
                (entry_id, leg_index, player_name, stat_type, line, description, league)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(r[0]),
                        0,
                        r[1],
                        r[2],
                        r[3],
                        r[4],
                        None,
                    )
                    for r in mapped
                ],
            )
            inserted += max(int(cur2.rowcount or 0), 0)
        conn.commit()
    if not staged_rows and missing_id_samples:
        print("DOM leg extraction missing-entry-id samples:")
        for s in missing_id_samples:
            print(f" - {s}")
    print(
        f"DOM card snapshots staged: {cards_inserted} ({len(card_snapshots)} seen), "
        f"leg rows staged: {staged_inserted} ({len(staged_rows)} parsed), saved legs: {inserted}."
    )
    return inserted


def reconcile_staged_cards_to_legs(db_path: Path = DB_PATH) -> int:
    """
    Parse persisted card snapshots into leg staging rows and map them to entry IDs.
    """
    with sqlite3.connect(db_path) as conn:
        known_entries = []
        for r in conn.execute(
            "SELECT entry_id, created_at, amount_staked FROM user_entries WHERE entry_id IS NOT NULL"
        ).fetchall():
            eid = str(r[0] or "").strip()
            created_raw = str(r[1] or "").strip()
            amount_staked = float(r[2]) if r[2] is not None else None
            if not eid:
                continue
            created_dt = None
            if created_raw:
                try:
                    created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    try:
                        created_dt = datetime.fromisoformat(created_raw).replace(tzinfo=None)
                    except Exception:
                        created_dt = None
            known_entries.append((eid, amount_staked, created_dt))

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_log_legs_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT DEFAULT (datetime('now')),
                entry_datetime_text TEXT,
                stake_amount REAL,
                card_header TEXT,
                player_name TEXT,
                pick_text TEXT,
                line REAL,
                stat_type TEXT,
                raw_card_text TEXT,
                mapped_entry_id TEXT,
                UNIQUE(entry_datetime_text, stake_amount, player_name, pick_text)
            )
            """
        )
        cards = conn.execute(
            "SELECT id, raw_card_text FROM transaction_log_cards_staging ORDER BY id DESC"
        ).fetchall()

        lineup_block_re = re.compile(
            r"Lineup (?:placed|won|lost|refunded)\n"
            r"(?P<dt>\d{2}/\d{2}/\d{4} at \d{1,2}:\d{2} [AP]M)\n"
            r"(?P<entry_type>[^\n]+)\n"
            r"(?P<amount>[+-]\s*\$\d+(?:\.\d{1,2})?)"
            r"(?P<body>.*?)(?=\nLineup (?:placed|won|lost|refunded)\n|\Z)",
            flags=re.I | re.S,
        )
        pick_re = re.compile(r"(?i)\b(more than|less than|higher|lower)\b")
        num_re = re.compile(r"(-?\d+(?:\.\d+)?)")

        parsed_rows: list[tuple[Any, ...]] = []
        for _cid, raw in cards:
            text = str(raw or "")
            if not text:
                continue
            for m in lineup_block_re.finditer(text):
                dt_text = str(m.group("dt")).strip()
                amount_text = str(m.group("amount")).replace("$", "").replace(" ", "")
                try:
                    stake = abs(float(amount_text))
                except Exception:
                    stake = None
                body = str(m.group("body") or "")
                lines = [ln.strip() for ln in body.splitlines() if ln and ln.strip()]
                for i in range(1, len(lines)):
                    prev = lines[i - 1]
                    cur = lines[i]
                    if not pick_re.search(cur):
                        continue
                    if any(tok in prev.lower() for tok in ("before:", "balance:", "view details")):
                        continue
                    line_val = None
                    n = num_re.search(cur)
                    if n:
                        try:
                            line_val = float(n.group(1))
                        except Exception:
                            line_val = None
                    stat_type = re.sub(r"(?i)\b(more than|less than|higher|lower)\b", "", cur)
                    stat_type = re.sub(r"\d+(?:\.\d+)?", "", stat_type).strip() or None
                    parsed_rows.append(
                        (
                            dt_text,
                            stake,
                            "",
                            prev,
                            cur,
                            line_val,
                            stat_type,
                            text[:5000],
                            None,
                        )
                    )

        inserted_stage = 0
        if parsed_rows:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO transaction_log_legs_staging
                (entry_datetime_text, stake_amount, card_header, player_name, pick_text, line, stat_type, raw_card_text, mapped_entry_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                parsed_rows,
            )
            inserted_stage = max(int(cur.rowcount or 0), 0)

        pending = conn.execute(
            "SELECT id, entry_datetime_text, stake_amount FROM transaction_log_legs_staging WHERE mapped_entry_id IS NULL"
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for sid, entry_dt_text, stake_amt in pending:
            if not entry_dt_text:
                continue
            try:
                header_dt = datetime.strptime(str(entry_dt_text), "%m/%d/%Y at %I:%M %p")
            except Exception:
                try:
                    header_dt = datetime.strptime(str(entry_dt_text), "%m/%d/%Y %I:%M %p")
                except Exception:
                    continue
            best = None
            best_score = None
            for eid, amount_staked, created_dt in known_entries:
                if created_dt is None:
                    continue
                diff_sec = abs((created_dt - header_dt).total_seconds())
                if diff_sec > 12 * 3600:
                    continue
                amt_penalty = 0.0
                if stake_amt is not None and amount_staked is not None:
                    amt_penalty = abs(float(amount_staked) - float(stake_amt)) * 600.0
                score = diff_sec + amt_penalty
                if best_score is None or score < best_score:
                    best_score = score
                    best = eid
            if best:
                updates.append((str(best), int(sid)))
        if updates:
            conn.executemany(
                "UPDATE transaction_log_legs_staging SET mapped_entry_id = ? WHERE id = ?",
                updates,
            )

        mapped_rows = conn.execute(
            """
            SELECT mapped_entry_id, player_name, stat_type, line, pick_text
            FROM transaction_log_legs_staging
            WHERE mapped_entry_id IS NOT NULL
            """
        ).fetchall()
        saved = 0
        if mapped_rows:
            cur2 = conn.executemany(
                """
                INSERT OR IGNORE INTO entry_legs
                (entry_id, leg_index, player_name, stat_type, line, description, league)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (str(r[0]), 0, r[1], r[2], r[3], r[4], None)
                    for r in mapped_rows
                ],
            )
            saved = max(int(cur2.rowcount or 0), 0)
        conn.commit()
    print(f"Reconciled staged cards -> legs: {inserted_stage} new staged, {saved} new saved legs.")
    return saved


def fetch_all_settled_pages(context: Any, on_payload: Any, max_pages: int = 200) -> int:
    """
    Fetch all settled entry pages via authenticated API and feed each payload.
    """
    page_num = 1
    total_pages = 1
    fetched_pages = 0
    print("Fetching all settled pages...")
    while page_num <= total_pages and page_num <= max_pages:
        try:
            resp = context.request.get(
                f"https://api.prizepicks.com/v1/entries?filter=settled&page={page_num}"
            )
            if not resp.ok:
                print(f"Settled page {page_num} request failed with status {resp.status}.")
                break
            payload = resp.json()
            if callable(on_payload):
                on_payload(payload, "https://api.prizepicks.com/v1/entries?filter=settled")
            meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
            total_pages = int(meta.get("total_pages") or total_pages or 1)
            fetched_pages += 1
            print(f"Fetched settled page {page_num}/{total_pages}")
            page_num += 1
        except Exception as exc:
            print(f"Stopped settled pagination at page {page_num}: {exc}")
            break
    print(f"Settled pagination complete: {fetched_pages} page(s) fetched.")
    return fetched_pages


def trigger_entry_detail_fetches(page: Page, entry_ids: list[str]) -> None:
    """
    Trigger per-entry detail fetches so listener can capture full leg context.
    """
    clean_ids = [str(x).strip() for x in entry_ids if str(x).strip()]
    if not clean_ids:
        return
    print(f"Triggering detail fetches for {len(clean_ids)} entries...")
    page.evaluate(
        """
        async (ids) => {
            for (const id of ids) {
                try {
                    await fetch(`https://api.prizepicks.com/v1/entries/${id}`, {
                        credentials: "include",
                        headers: { "accept": "application/json" },
                    });
                } catch (_) {
                    // Best effort.
                }
            }
        }
        """,
        clean_ids,
    )


def fetch_entry_details_via_api(context: Any, entry_ids: list[str], on_payload: Any | None = None) -> int:
    """
    Fetch per-entry details through authenticated API request context.
    More reliable than relying only on intercepted response bodies.
    """
    clean_ids = [str(x).strip() for x in entry_ids if str(x).strip()]
    if not clean_ids:
        return 0
    fetched = 0
    print(f"Fetching detail JSON via API context for {len(clean_ids)} entries...")
    for eid in clean_ids:
        try:
            resp = context.request.get(f"https://api.prizepicks.com/v1/entries/{eid}")
            if not resp.ok:
                continue
            payload = resp.json()
            fetched += 1
            if callable(on_payload):
                on_payload(payload, f"https://api.prizepicks.com/v1/entries/{eid}")
        except Exception:
            continue
    print(f"Detail API fetch complete: {fetched} successful.")
    return fetched


def _entry_prediction_map_from_db(db_path: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT entry_id, raw_json FROM user_entries WHERE raw_json IS NOT NULL AND raw_json != ''"
        ).fetchall()
    for entry_id, raw_json in rows:
        try:
            obj = json.loads(raw_json)
        except Exception:
            continue
        preds = get_nested(obj, "relationships", "predictions", "data") or []
        if not isinstance(preds, list):
            continue
        pids = [str(p.get("id") or "").strip() for p in preds if isinstance(p, dict)]
        pids = [p for p in pids if p]
        if pids:
            mapping[str(entry_id)] = pids
    return mapping


def _parse_prediction_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    attrs = data.get("attributes", {}) if isinstance(data.get("attributes"), dict) else {}
    included = payload.get("included")
    included = included if isinstance(included, list) else []
    included_by_id: dict[str, dict[str, Any]] = {}
    for obj in included:
        if not isinstance(obj, dict):
            continue
        oid = str(obj.get("id") or "").strip()
        if oid:
            included_by_id[oid] = obj

    player_name = (
        attrs.get("player_name")
        or attrs.get("name")
        or get_nested(attrs, "new_player", "name")
    )
    if not player_name:
        np_id = get_nested(data, "relationships", "new_player", "data", "id")
        if np_id and str(np_id) in included_by_id:
            player_name = get_nested(included_by_id[str(np_id)], "attributes", "name")

    return {
        "prediction_id": str(data.get("id") or "").strip(),
        "player_name": player_name,
        "stat_type": attrs.get("stat_type") or attrs.get("market") or attrs.get("stat"),
        "line": attrs.get("line_score") or attrs.get("line"),
        "description": attrs.get("description") or attrs.get("prediction_type") or attrs.get("pick"),
        "league": attrs.get("league") or attrs.get("sport"),
    }


def fetch_predictions_and_save_legs(context: Any, db_path: Path = DB_PATH) -> int:
    """
    Resolve prediction IDs from saved entries and store leg-level details.
    """
    entry_to_preds = _entry_prediction_map_from_db(db_path)
    if not entry_to_preds:
        print("No prediction IDs found in saved entries.")
        return 0

    prediction_cache: dict[str, dict[str, Any]] = {}
    unique_prediction_ids = sorted({pid for pids in entry_to_preds.values() for pid in pids})
    print(f"Fetching prediction details for {len(unique_prediction_ids)} IDs...")
    for pid in unique_prediction_ids:
        try:
            resp = context.request.get(f"https://api.prizepicks.com/v1/predictions/{pid}")
            if not resp.ok:
                resp = context.request.get(f"https://api.prizepicks.com/projections/{pid}")
            if not resp.ok:
                continue
            payload = resp.json()
            # projections endpoint can return bare object, normalize to JSON:API-like form
            if isinstance(payload, dict) and isinstance(payload.get("id"), (str, int)) and "data" not in payload:
                payload = {"data": payload}
            parsed = _parse_prediction_payload(payload)
            if parsed.get("prediction_id"):
                prediction_cache[parsed["prediction_id"]] = parsed
        except Exception:
            continue

    leg_rows: list[tuple[Any, ...]] = []
    for entry_id, pids in entry_to_preds.items():
        for idx, pid in enumerate(pids):
            pred = prediction_cache.get(pid)
            if not pred:
                continue
            leg_rows.append(
                (
                    entry_id,
                    idx,
                    pred.get("player_name"),
                    pred.get("stat_type"),
                    pred.get("line"),
                    pred.get("description"),
                    pred.get("league"),
                )
            )
    if not leg_rows:
        print("No prediction detail rows resolved.")
        return 0

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO entry_legs
            (entry_id, leg_index, player_name, stat_type, line, description, league)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            leg_rows,
        )
        conn.commit()
    print(f"Saved prediction-derived legs: {len(leg_rows)}")
    return len(leg_rows)


def get_entry_ids_for_rehydration(
    db_path: Path = DB_PATH,
    month: str | None = None,
    year: int | None = None,
    limit: int = 1000,
) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(member_transactions)").fetchall()}
        if "source_month" not in cols:
            try:
                conn.execute("ALTER TABLE member_transactions ADD COLUMN source_month TEXT")
            except sqlite3.OperationalError:
                pass
        if "source_year" not in cols:
            try:
                conn.execute("ALTER TABLE member_transactions ADD COLUMN source_year INTEGER")
            except sqlite3.OperationalError:
                pass
        conn.commit()

    where = ["entry_id IS NOT NULL", "TRIM(entry_id) != ''"]
    params: list[Any] = []
    if month:
        where.append("LOWER(source_month) = LOWER(?)")
        params.append(month)
    if year:
        where.append("source_year = ?")
        params.append(int(year))
    sql = (
        "SELECT DISTINCT entry_id FROM member_transactions "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT ?"
    )
    params.append(max(1, int(limit)))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def get_entry_ids_missing_legs(db_path: Path = DB_PATH, limit: int = 2000) -> list[str]:
    """
    Entry IDs that have no usable leg rows yet.
    """
    sql = """
    SELECT ue.entry_id
    FROM user_entries ue
    LEFT JOIN entry_legs el ON el.entry_id = ue.entry_id
    GROUP BY ue.entry_id
    HAVING
        COUNT(el.id) = 0
        OR SUM(CASE WHEN COALESCE(TRIM(el.player_name), '') != '' THEN 1 ELSE 0 END) = 0
    ORDER BY ue.created_at DESC
    LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, (max(1, int(limit)),)).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def rehydrate_entries_via_pages(
    page: Page,
    context: Any,
    entry_ids: list[str],
    on_payload: Any | None = None,
) -> int:
    """
    Rehydrate entry payloads without navigating tabs/pages.
    """
    hydrated = 0
    total = len(entry_ids)
    for i, eid in enumerate(entry_ids, start=1):
        api_hydrated = False
        try:
            # First try same-page authenticated fetch using browser session cookies.
            try:
                js_payload = page.evaluate(
                    """
                    async (entryId) => {
                        const res = await fetch(`https://api.prizepicks.com/v1/entries/${entryId}`, {
                            credentials: "include",
                            headers: { "accept": "application/json" },
                        });
                        if (!res.ok) return null;
                        return await res.json();
                    }
                    """,
                    str(eid),
                )
                if js_payload is not None and callable(on_payload):
                    on_payload(js_payload, f"https://api.prizepicks.com/v1/entries/{eid}")
                    api_hydrated = True
            except Exception:
                pass

            # Primary path: direct API call is more reliable than inspector-backed response bodies.
            if not api_hydrated:
                try:
                    resp = context.request.get(f"https://api.prizepicks.com/v1/entries/{eid}")
                    if resp.ok and callable(on_payload):
                        on_payload(resp.json(), f"https://api.prizepicks.com/v1/entries/{eid}")
                        api_hydrated = True
                except Exception:
                    pass

            # Do not navigate entry pages here; keep user's browser state stable.
            page.wait_for_timeout(300)
            hydrated += 1
            if i % 25 == 0 or i == total:
                print(f"Rehydration progress: {i}/{total}")
        except Exception as exc:
            print(f"Rehydration skip {eid}: {exc}")
    print(f"Rehydration attempted {hydrated}/{total} entry ids.")
    return hydrated


def sync_entry_statuses(json_data: Any, db_path: Path = DB_PATH) -> int:
    """
    Update existing pending statuses when payload reports resolved outcomes.
    """
    items = _extract_entry_items(json_data)
    status_map: dict[str, str] = {}
    for item in items:
        attrs = item.get("attributes", {}) if isinstance(item.get("attributes"), dict) else {}
        entry_id = str(item.get("id") or attrs.get("entry_id") or "").strip()
        status = str(attrs.get("status") or item.get("status") or "").strip()
        if entry_id and status:
            status_map[entry_id] = status
    if not status_map:
        return 0

    updated = 0
    with sqlite3.connect(db_path) as conn:
        for entry_id, new_status in status_map.items():
            row = conn.execute(
                "SELECT status FROM user_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            if not row:
                continue
            old_status = str(row[0] or "").strip().lower()
            new_norm = new_status.lower()
            if old_status in {"pending", "open"} and new_norm in {"complete", "win", "loss"}:
                conn.execute(
                    "UPDATE user_entries SET status = ? WHERE entry_id = ?",
                    (new_status, entry_id),
                )
                updated += 1
        conn.commit()
    print(f"Status sync complete: {updated} updated.")
    return updated


def generate_edge_audit(db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    Bucket resolved legs by model edge and report win rate.
    """
    with sqlite3.connect(db_path) as conn:
        legs = pd.read_sql_query(
            """
            SELECT l.entry_id, l.leg_index, l.player_name, l.stat_type, l.line, l.description, l.league, e.created_at, e.status
            FROM entry_legs l
            LEFT JOIN user_entries e ON e.entry_id = l.entry_id
            """,
            conn,
        )
        projections, _ = _load_projection_frame(conn)

    if legs.empty or projections.empty:
        print("Edge audit skipped: missing legs or projections.")
        return pd.DataFrame()

    merged = _resolve_projection_matches(legs, projections)
    if merged.empty:
        print("Edge audit skipped: no projection matches.")
        return pd.DataFrame()

    status = merged.get("status", pd.Series("", index=merged.index)).astype(str).str.lower()
    merged = merged[status.isin(["win", "loss"])]
    if merged.empty:
        print("Edge audit skipped: no resolved Win/Loss entries yet.")
        return pd.DataFrame()

    merged["edge_pct"] = pd.to_numeric(merged.get("edge"), errors="coerce") * 100.0
    merged["is_win"] = status.eq("win")
    merged["edge_bucket"] = pd.cut(
        merged["edge_pct"].abs(),
        bins=[0, 5, 10, float("inf")],
        labels=["0-5%", "5-10%", "10%+"],
        include_lowest=True,
        right=False,
    )
    report = (
        merged.groupby("edge_bucket", observed=True)
        .agg(legs=("is_win", "size"), wins=("is_win", "sum"))
        .reset_index()
    )
    report["win_rate_pct"] = (report["wins"] / report["legs"] * 100.0).round(2)
    _render_table(report, "Edge Accuracy Audit", style="green")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture PrizePicks /my-entries payload and parse entries."
    )
    parser.add_argument(
        "--session-dir",
        default=str(SESSION_DIR),
        help="Path for persistent browser profile (default: browser_session)",
    )
    parser.add_argument(
        "--target-url",
        default=PRIZEPICKS_URL,
        help="Page to open for manual login/session refresh.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Browser User-Agent string to use for requests.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DB_PATH),
        help="SQLite database path for entries/projections/ROI tracking.",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=DEFAULT_LATITUDE,
        help="Fixed geolocation latitude used by browser context.",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        default=DEFAULT_LONGITUDE,
        help="Fixed geolocation longitude used by browser context.",
    )
    parser.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        help="Browser locale (default: en-US).",
    )
    parser.add_argument(
        "--timezone-id",
        default=DEFAULT_TIMEZONE,
        help="Browser timezone id (default: America/New_York).",
    )
    parser.add_argument(
        "--pause-on-diagnostics-warning",
        action="store_true",
        help="Pause for Enter when diagnostics detect denied/low-accuracy geolocation.",
    )
    parser.add_argument(
        "--attach-cdp",
        default="",
        help="Attach to an existing Chromium instance via CDP (e.g., http://127.0.0.1:9222).",
    )
    parser.add_argument(
        "--no-nav-on-attach",
        action="store_true",
        help="When using --attach-cdp, do not navigate; stay on current visible page.",
    )
    parser.add_argument(
        "--discovery-mode",
        action="store_true",
        help="Log API JSON response URLs and wait for manual ticket click.",
    )
    parser.add_argument(
        "--max-settled-pages",
        type=int,
        default=200,
        help="Safety cap for settled pagination pages (default: 200).",
    )
    parser.add_argument(
        "--listen-seconds",
        type=int,
        default=0,
        help="Passive listener window (seconds) to capture manual filter changes.",
    )
    parser.add_argument(
        "--rehydrate-from-member-transactions",
        action="store_true",
        help="Open /entries/{id} pages from member_transactions and capture detailed legs.",
    )
    parser.add_argument(
        "--rehydrate-month",
        default="",
        help="Month name filter for rehydration (e.g., February).",
    )
    parser.add_argument(
        "--rehydrate-year",
        type=int,
        default=0,
        help="Year filter for rehydration (e.g., 2025).",
    )
    parser.add_argument(
        "--rehydrate-limit",
        type=int,
        default=2000,
        help="Max number of entry IDs to rehydrate from member_transactions.",
    )
    parser.add_argument(
        "--rehydrate-missing-legs",
        action="store_true",
        help="Rehydrate IDs from user_entries that currently have no leg details.",
    )
    parser.add_argument(
        "--harvest-transaction-log-details",
        action="store_true",
        help="Auto-click VIEW DETAILS cards on transaction-log to trigger leg payloads.",
    )
    parser.add_argument(
        "--transaction-log-passes",
        type=int,
        default=30,
        help="How many scroll/click passes to run for transaction-log detail harvesting.",
    )
    parser.add_argument(
        "--no-cover-report",
        action="store_true",
        help="Skip writing the run summary CSV artifact.",
    )
    args = parser.parse_args()
    maybe_migrate_legacy_db(Path(args.db_path))

    session_dir = Path(args.session_dir)
    captured: dict[str, Any] = {"payload": None}
    session_state: dict[str, Any] = {
        "new_slips_found": 0,
        "total_in_db": 0,
        "payloads_seen": 0,
    }

    with sync_playwright() as p:
        browser = None
        should_close_context = True
        if args.attach_cdp:
            print(f"Attaching to existing browser via CDP: {args.attach_cdp}")
            browser, context, page = attach_over_cdp(p, args.attach_cdp)
            should_close_context = False
            print("CDP attach successful. Using existing browser session.")
            if args.no_nav_on_attach:
                print("Attach mode: keeping current page (no navigation).")
            else:
                print(f"Ensuring target page is loaded: {args.target_url}")
                page.goto(args.target_url, wait_until="domcontentloaded")
        else:
            session_dir.mkdir(parents=True, exist_ok=True)
            context = launch_persistent_context_with_fallback(p, session_dir, args)
            try:
                context.grant_permissions(["geolocation"], origin="https://app.prizepicks.com")
                context.grant_permissions(["geolocation"], origin="https://www.google.com")
            except Exception:
                print("Could not grant geolocation permissions explicitly in this run.")
            page = context.new_page()
            if stealth_sync is not None:
                stealth_sync(page)

            # Randomized startup delay to avoid deterministic bot timing.
            nav_delay = random.uniform(2.5, 6.5)
            print(f"Sleeping {nav_delay:.2f}s before initial navigation...")
            time.sleep(nav_delay)

            print(f"Using persistent session dir: {session_dir.resolve()}")
            print(
                "Geolocation settings -> "
                f"lat={args.latitude}, lon={args.longitude}, locale={args.locale}, tz={args.timezone_id}"
            )
            run_diagnostics(
                page,
                expected_timezone=args.timezone_id,
                pause_on_warning=args.pause_on_diagnostics_warning,
            )
            print(f"Opening {args.target_url}")
            page.goto(args.target_url, wait_until="domcontentloaded")
            wait_for_manual_interaction(page)
            print("Waiting 5 seconds for manual Press & Hold / challenge handling...")
            time.sleep(5)

        db_path = Path(args.db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_entries (
                    entry_id TEXT PRIMARY KEY,
                    payout REAL,
                    amount_staked REAL,
                    status TEXT,
                    league TEXT,
                    entry_type TEXT,
                    created_at TEXT,
                    raw_json TEXT
                )
                """
            )
            session_state["total_in_db"] = int(conn.execute("SELECT COUNT(*) FROM user_entries").fetchone()[0])

        def on_payload(payload: Any, source_url: str = "") -> None:
            inserted_count = 0
            total_count = int(session_state.get("total_in_db", 0))
            if "/member_transactions" in source_url:
                process_member_transactions(payload, db_path=db_path, source_url=source_url)
            elif "/projections" in source_url:
                # Projection feeds are not entry slips; ignore for user_entries ingest.
                return
            else:
                sync_entry_statuses(payload, db_path=db_path)
                _, _, inserted_count, total_count = process_and_save(payload, db_path=db_path)
            session_state["payloads_seen"] = int(session_state.get("payloads_seen", 0)) + 1
            session_state["new_slips_found"] = int(session_state.get("new_slips_found", 0)) + inserted_count
            session_state["total_in_db"] = total_count
            c = _console()
            line = (
                f"New Slips Found: {session_state['new_slips_found']} | "
                f"Total in DB: {session_state['total_in_db']}"
            )
            if c is not None:
                c.print(f"[{GOLD}]{line}[/{GOLD}]")
            else:
                print(line)

        attach_response_listener(
            page,
            captured,
            on_payload=on_payload,
            discovery_mode=args.discovery_mode,
        )
        if not args.discovery_mode and not args.rehydrate_missing_legs:
            trigger_entry_filters(page)
            fetch_all_settled_pages(
                context,
                on_payload=on_payload,
                max_pages=max(1, int(args.max_settled_pages)),
            )
            trigger_member_transactions_history(page, months_back=24)

        if args.rehydrate_from_member_transactions:
            month = args.rehydrate_month.strip() or None
            year = int(args.rehydrate_year) if int(args.rehydrate_year) > 0 else None
            if month and year:
                trigger_member_transactions_month_year(page, month=month, year=year)
                page.wait_for_timeout(2500)
            ids = get_entry_ids_for_rehydration(
                db_path=db_path,
                month=month,
                year=year,
                limit=max(1, int(args.rehydrate_limit)),
            )
            print(
                "Rehydration target IDs: "
                f"{len(ids)} (month={month or 'ANY'}, year={year or 'ANY'})"
            )
            rehydrate_entries_via_pages(page, context, ids, on_payload=on_payload)

        if args.rehydrate_missing_legs:
            ids_missing = get_entry_ids_missing_legs(
                db_path=db_path,
                limit=max(1, int(args.rehydrate_limit)),
            )
            print(f"Missing-leg rehydration target IDs: {len(ids_missing)}")
            rehydrate_entries_via_pages(page, context, ids_missing, on_payload=on_payload)
            # After refreshing entry raw_json, resolve prediction IDs into leg rows.
            fetch_predictions_and_save_legs(context, db_path=db_path)

        if args.harvest_transaction_log_details:
            harvest_transaction_log_details(
                page,
                max_passes=max(0, int(args.transaction_log_passes)),
            )
            extract_transaction_log_dom_legs(page, db_path=db_path)
            reconcile_staged_cards_to_legs(db_path=db_path)

        print("Listener active. If already on My Entries, auto-scroll harvesting will begin.")
        try:
            if args.discovery_mode:
                print("Discovery mode active: click one past ticket now. Listening for 45 seconds...")
                page.wait_for_timeout(45000)
            else:
                if int(args.listen_seconds) > 0:
                    print(f"Passive listen active for {int(args.listen_seconds)}s. Change transaction-log filters now.")
                    page.wait_for_timeout(int(args.listen_seconds) * 1000)
                if not args.rehydrate_missing_legs:
                    harvest_history(page, session_state, scroll_px=800, sleep_seconds=1.5, max_no_new_scrolls=5)
                    # Pull full ticket context by entry id to capture legs (player/stat/line/direction).
                    with sqlite3.connect(db_path) as conn:
                        ids = [
                            str(r[0])
                            for r in conn.execute(
                                "SELECT entry_id FROM user_entries WHERE entry_id IS NOT NULL ORDER BY created_at DESC LIMIT 500"
                            ).fetchall()
                        ]
                    trigger_entry_detail_fetches(page, ids)
                    fetch_entry_details_via_api(context, ids, on_payload=on_payload)
                    fetch_predictions_and_save_legs(context, db_path=db_path)
                    page.wait_for_timeout(5000)
            print("Harvest complete. Finalizing analytics...")
        except KeyboardInterrupt:
            print("\nStopped by user.")
        except TargetClosedError:
            print("Browser was closed. Continuing with any captured data.")
        finally:
            if should_close_context:
                try:
                    context.close()
                except TargetClosedError:
                    # Browser/context already closed by user action.
                    pass
            elif browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    payload = captured.get("payload")
    if payload is None and not (args.rehydrate_missing_legs or args.rehydrate_from_member_transactions):
        print("No /my-entries JSON captured in this run.")
        return 1
    if payload is None:
        print("No /my-entries JSON captured in this run (rehydration-only run).")
        return 0

    rows = parse_entries(payload)
    if not rows:
        if (
            args.rehydrate_missing_legs
            or args.rehydrate_from_member_transactions
            or args.harvest_transaction_log_details
        ):
            print("No parseable /entries rows in final payload (rehydration/harvest run).")
            return 0
        print("Captured payload, but no entries were parsed.")
        print("Raw payload preview:")
        print(json.dumps(payload, indent=2)[:4000])
        return 1

    df = pd.DataFrame(rows, columns=["entry_id", "player_name", "stat_type", "line", "status"])
    _render_table(df, "Captured Entry Legs", style=GOLD)
    db_path = Path(args.db_path)
    compare_df = compare_entries_with_projections(db_path=db_path)
    roi_df = calculate_roi(db_path=db_path)
    generate_edge_audit(db_path=db_path)
    if not args.no_cover_report:
        export_run_cover_report(db_path=db_path)
    c = _console()
    if c is not None and Panel is not None and not roi_df.empty:
        total_pl = float(roi_df["profit_loss"].sum())
        total_staked = float(roi_df["total_staked"].sum())
        roi_pct = (total_pl / total_staked * 100.0) if total_staked else 0.0
        color = "green" if total_pl >= 0 else "red"
        c.print(
            Panel(
                f"[{color}]Session ROI: {roi_pct:.2f}%[/{color}]  |  "
                f"[{color}]Bankroll Impact: {total_pl:+.2f}[/{color}]",
                title="Session ROI",
                border_style=GOLD,
            )
        )
    sync_to_sqlite(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
