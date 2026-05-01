import os
import sqlite3
import glob

dbs = sorted(glob.glob("data/cache/*_props_history.db"))
print(f"Found {len(dbs)} history DBs:\n")
for db in dbs:
    name = os.path.basename(db)
    size = os.path.getsize(db)
    print(f"  {name} ({size:,} bytes)")
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(props_history)")
        cols = [r[1] for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM props_history")
        count = cur.fetchone()[0]
        date_col = None
        for x in ("grade_date", "slate_date", "game_date", "date_str", "day"):
            if x in cols:
                date_col = x
                break
        if date_col:
            cur.execute(f"SELECT MIN({date_col}), MAX({date_col}) FROM props_history")
            lo, hi = cur.fetchone()
            print(f"    Rows: {count}, {date_col} range: {lo} to {hi}")
        else:
            print(f"    Rows: {count}, (no grade_date/slate_date/game_date/date_str/day in schema)")
            print(f"    Sample columns: {cols[:25]}")
        if "ml_prob" in cols:
            cur.execute(
                "SELECT COUNT(*) FROM props_history WHERE ml_prob IS NOT NULL "
                "AND TRIM(CAST(ml_prob AS TEXT)) != ''"
            )
            mp = cur.fetchone()[0]
            print(f"    Rows with non-empty ml_prob: {mp}")
        else:
            print("    (no ml_prob column)")
        conn.close()
    except Exception as e:
        print(f"    Error: {e}")
    print()
