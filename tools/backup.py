"""DB backup / restore.
- SQLite: consistent snapshot via the sqlite3 online-backup API (safe with the dashboard open).
- Postgres (Neon): real pg_dump to a timestamped .sql; RAISES on failure so a caller can never
  mistake a no-op for a successful backup. (For managed DR, Neon PITR / branching is the backstop.)

Run:  python -m tools.backup [dest_dir]
"""
import os
import sys
import shutil
import datetime
import subprocess

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
    # Postgres — actually run pg_dump; raise on any failure (no silent no-op).
    pgd = shutil.which("pg_dump")
    if not pgd:
        raise RuntimeError("pg_dump not on PATH — install postgresql-client to back up Postgres, "
                           "or rely on Neon PITR/branching for DR.")
    out = os.path.join(dest_dir, f"axis-{ts}.sql")
    libpq = db.DB_URL.replace("postgresql+psycopg2://", "postgresql://")
    with open(out, "wb") as fh:
        r = subprocess.run([pgd, libpq], stdout=fh, stderr=subprocess.PIPE)  # nosec B603 — resolved path, list args
    if r.returncode != 0:
        os.path.exists(out) and os.remove(out)
        raise RuntimeError(f"pg_dump failed (exit {r.returncode}): {r.stderr.decode('utf-8', 'replace')[:200]}")
    print(f"backup -> {out}")
    return out


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else "backups")
