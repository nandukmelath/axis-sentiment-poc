"""DB backup / restore.
- SQLite: consistent snapshot via the sqlite3 online-backup API (safe with the dashboard open).
- Postgres: prints the pg_dump command (server-side dump).

Run:  python -m tools.backup [dest_dir]
"""
import os
import sys
import datetime

import db


def _sqlite_path():
    return db.DB_URL.replace("sqlite:///", "")


def backup(dest_dir="backups"):
    os.makedirs(dest_dir, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    if db.DIALECT == "sqlite":
        import sqlite3
        out = os.path.join(dest_dir, f"axis-{ts}.db")
        src = sqlite3.connect(_sqlite_path())
        dst = sqlite3.connect(out)
        with dst:
            src.backup(dst)            # online backup — consistent even under concurrent access
        dst.close()
        src.close()
        print(f"backup -> {out}")
        return out
    print(f"Postgres — run:  pg_dump \"$DATABASE_URL\" > {dest_dir}/axis-{ts}.sql")
    return None


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else "backups")
