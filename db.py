"""Data store — works on BOTH SQLite (default, zero-setup) and Postgres (Docker/prod).
Pick via DATABASE_URL env, e.g.
  sqlite:///.../axis.db                              (default)
  postgresql+psycopg2://axis:axis@db:5432/axis       (docker-compose)
Same function API as before; upserts are dialect-aware.
"""
import os, json, datetime
import pandas as pd
from sqlalchemy import create_engine, text, event
from config import DB_PATH

_default = "sqlite:///" + DB_PATH.replace("\\", "/")
DB_URL = os.getenv("DATABASE_URL", _default)
# busy_timeout lets a concurrent reader (dashboard) and writer (RUN) coordinate instead of
# erroring immediately — important when the dashboard is open while a refresh runs.
_connect_args = {"timeout": 30} if DB_URL.startswith("sqlite") else {}
# Pool tuned for a managed pooler (Supabase supavisor / PgBouncer): pre_ping drops dead
# conns; pool_recycle avoids the pooler silently closing idle ones; a small bounded pool
# stays well under the free-tier connection cap (one engine per process: dashboard + cron).
_pool_args = {} if DB_URL.startswith("sqlite") else {
    "pool_recycle": 1800, "pool_size": 5, "max_overflow": 5, "pool_timeout": 30}
_engine = create_engine(DB_URL, future=True, pool_pre_ping=True,
                        connect_args=_connect_args, **_pool_args)
DIALECT = _engine.dialect.name  # 'sqlite' | 'postgresql'

if DIALECT == "sqlite":
    @event.listens_for(_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        # busy_timeout makes a concurrent reader (dashboard) and writer (RUN) wait for each
        # other instead of erroring. NOT WAL: the DB file is shared between Windows/NTFS and
        # WSL/9p, and 9p can't do WAL's shared-memory — so WAL would break the Airflow side.
        try:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
        except Exception:
            pass

DDL = [
    """CREATE TABLE IF NOT EXISTS raw_posts (
        source_id TEXT PRIMARY KEY, source TEXT, author TEXT, author_name TEXT, text TEXT, url TEXT,
        created_at TEXT, fetched_at TEXT, lang TEXT, engagement BIGINT DEFAULT 0,
        reply_count BIGINT, retweet_count BIGINT, quote_count BIGINT, view_count BIGINT,
        bookmark_count BIGINT, conversation_id TEXT, raw_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS analysis (
        source_id TEXT PRIMARY KEY, sentiment TEXT, score REAL, emotion TEXT,
        emotion_intensity INTEGER, sarcasm INTEGER, intent TEXT, urgency TEXT, urgency_reason TEXT,
        product TEXT, root_cause TEXT, rbi_category TEXT, recommended_team TEXT, recommended_action TEXT,
        churn_risk INTEGER, fraud_signal INTEGER, fraud_type TEXT, pii_present INTEGER, theme TEXT,
        summary TEXT, confidence REAL, aspects_json TEXT, cluster_id INTEGER, model TEXT, analyzed_at TEXT,
        text_masked TEXT, pii_types TEXT)""",
    """CREATE TABLE IF NOT EXISTS clusters (
        cluster_id INTEGER PRIMARY KEY, title TEXT, size INTEGER, recent_share REAL,
        avg_score REAL, top_team TEXT, sample_ids TEXT, updated_at TEXT)""",
    # transform (silver-prep) output of the Apache Beam stage — normalise/dedup/lang/spam/mask
    """CREATE TABLE IF NOT EXISTS clean_posts (
        source_id TEXT PRIMARY KEY, clean_text TEXT, lang TEXT, text_hash TEXT,
        is_duplicate INTEGER, spam_flag INTEGER, pii_types TEXT, transformed_at TEXT)""",
]

CLEAN_COLS = ["source_id", "clean_text", "lang", "text_hash", "is_duplicate", "spam_flag",
              "pii_types", "transformed_at"]

# indexes on hot query columns (dialect-agnostic; created in init_db)
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raw_conversation ON raw_posts(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_posts(source)",
    "CREATE INDEX IF NOT EXISTS idx_raw_author ON raw_posts(author)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_model ON analysis(model)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_sentiment ON analysis(sentiment)",
    "CREATE INDEX IF NOT EXISTS idx_clean_hash ON clean_posts(text_hash)",
]

RAW_COLS = ["source_id", "source", "author", "author_name", "text", "url", "created_at", "fetched_at",
            "lang", "engagement", "reply_count", "retweet_count", "quote_count", "view_count",
            "bookmark_count", "conversation_id", "raw_json"]

# columns added after the first release — auto-added to existing DBs by migrate()
MIGRATIONS = {"raw_posts": {"author_name": "TEXT", "reply_count": "BIGINT", "retweet_count": "BIGINT",
                            "quote_count": "BIGINT", "view_count": "BIGINT", "bookmark_count": "BIGINT",
                            "conversation_id": "TEXT"},
              "analysis": {"text_masked": "TEXT", "pii_types": "TEXT"}}
ANALYSIS_COLS = ["source_id", "sentiment", "score", "emotion", "emotion_intensity", "sarcasm", "intent",
                 "urgency", "urgency_reason", "product", "root_cause", "rbi_category", "recommended_team",
                 "recommended_action", "churn_risk", "fraud_signal", "fraud_type", "pii_present", "theme",
                 "summary", "confidence", "aspects_json", "cluster_id", "model", "analyzed_at",
                 "text_masked", "pii_types"]
CLUSTER_COLS = ["cluster_id", "title", "size", "recent_share", "avg_score", "top_team", "sample_ids", "updated_at"]


def get_engine():
    return _engine


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_dt(s):
    """Robust UTC datetime parse tolerant of the MIXED formats our sources emit
    (ISO w/ offset, RFC-822 'Mon, 29 Jun 2026 ...', date-only). Accepts a Series or a
    scalar; unparseable -> NaT. Without format='mixed', pandas 2.x infers ONE format from
    the first row and silently coerces every other format to NaT (this NULLed ~87% of
    created_date and broke trends/forecast/windowing)."""
    return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")


def _existing_cols(table):
    if DIALECT == "sqlite":
        return set(df(f"PRAGMA table_info({table})")["name"])
    return set(df(f"SELECT column_name AS name FROM information_schema.columns WHERE table_name='{table}'")["name"])


def migrate():
    for table, cols in MIGRATIONS.items():
        have = _existing_cols(table)
        for col, typ in cols.items():
            if col not in have:
                with _engine.begin() as c:
                    c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))


VIEW_SELECT = """SELECT r.source_id, r.source, r.author, r.author_name, r.created_at, r.url, r.text,
       a.sentiment, a.score, a.emotion, a.urgency, a.intent, a.recommended_team,
       a.fraud_signal, a.churn_risk, a.theme, a.summary, a.model,
       r.engagement, r.view_count
FROM raw_posts r LEFT JOIN analysis a ON r.source_id = a.source_id"""


def create_view():
    # one table joining post text + date + author with its sentiment/analysis.
    # sqlite: CREATE IF NOT EXISTS (no per-run DROP → less write contention with the dashboard).
    with _engine.begin() as c:
        if DIALECT == "sqlite":
            c.execute(text(f"CREATE VIEW IF NOT EXISTS scored_posts AS {VIEW_SELECT}"))
        else:
            c.execute(text(f"CREATE OR REPLACE VIEW scored_posts AS {VIEW_SELECT}"))


def init_db():
    with _engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))
    migrate()
    with _engine.begin() as c:
        for ix in INDEXES:
            c.execute(text(ix))
    create_view()
    print(f"db ready [{DIALECT}]: {DB_URL}")


def _na(v):
    # SQLite tolerates NaN + numpy scalars; Postgres/psycopg2 do not. Coerce NaN/NaT -> NULL and
    # cast numpy scalars (np.float64/np.int64 — whose numpy-2 repr breaks SQL) to native Python.
    if v is None:
        return None
    try:
        if v != v:                        # NaN != NaN
            return None
    except Exception:
        pass
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except Exception:
            pass
    return v


def _upsert(table, rows, pk, cols, replace):
    if not rows:
        return 0
    norm = [{k: _na(r.get(k)) for k in cols} for r in rows]
    collist = ",".join(cols)
    ph = ",".join(f":{c}" for c in cols)
    if DIALECT == "sqlite":
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        sql = f"{verb} INTO {table} ({collist}) VALUES ({ph})"
    else:  # postgresql
        if replace:
            setc = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c != pk)
            sql = f"INSERT INTO {table} ({collist}) VALUES ({ph}) ON CONFLICT ({pk}) DO UPDATE SET {setc}"
        else:
            sql = f"INSERT INTO {table} ({collist}) VALUES ({ph}) ON CONFLICT ({pk}) DO NOTHING"
    with _engine.begin() as c:
        c.execute(text(sql), norm)
    return len(norm)


def upsert_posts(rows):
    for r in rows:
        r.setdefault("fetched_at", now())
    return _upsert("raw_posts", rows, "source_id", RAW_COLS, replace=False)


def upsert_analysis(a):
    a = {**a, "analyzed_at": now()}
    return _upsert("analysis", [a], "source_id", ANALYSIS_COLS, replace=True)


def upsert_cluster(row):
    return _upsert("clusters", [{**row, "updated_at": now()}], "cluster_id", CLUSTER_COLS, replace=True)


def set_cluster(source_id, cluster_id):
    with _engine.begin() as c:
        c.execute(text("UPDATE analysis SET cluster_id=:cid WHERE source_id=:sid"),
                  {"cid": cluster_id, "sid": source_id})


def get_unanalyzed(limit=None):
    sql = """SELECT r.* FROM raw_posts r
             LEFT JOIN analysis a ON r.source_id = a.source_id
             WHERE a.source_id IS NULL"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _engine.connect() as c:
        return [dict(m) for m in c.execute(text(sql)).mappings().all()]


def get_untransformed(limit=None):
    """raw_posts not yet processed by the Beam transform stage."""
    sql = """SELECT r.source_id, r.text, r.created_at, r.source, r.author FROM raw_posts r
             LEFT JOIN clean_posts c ON r.source_id = c.source_id
             WHERE c.source_id IS NULL"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _engine.connect() as c:
        return [dict(m) for m in c.execute(text(sql)).mappings().all()]


def get_needs_llm(limit=None):
    """VADER-scored posts that still need LLM depth (negative/neutral)."""
    sql = """SELECT r.* FROM raw_posts r JOIN analysis a ON r.source_id = a.source_id
             WHERE a.model = 'vader-fast' AND a.sentiment <> 'positive'"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _engine.connect() as c:
        return [dict(m) for m in c.execute(text(sql)).mappings().all()]


def df(sql, params=None):
    if params is not None:
        return pd.read_sql_query(text(sql), _engine, params=params)
    return pd.read_sql_query(sql, _engine)


# ---- generic helpers reused by the warehouse layer (dialect-aware) ----
def execute(sql, params=None):
    with _engine.begin() as c:
        c.execute(text(sql), params or {})


def executescript(stmts):
    with _engine.begin() as c:
        for s in stmts:
            c.execute(text(s))


def upsert_rows(table, rows, pk, cols, replace=True):
    """Public wrapper over the dialect-aware upsert (INSERT OR REPLACE / ON CONFLICT)."""
    return _upsert(table, rows, pk, cols, replace)


def insert_rows(table, rows, cols):
    """Plain INSERT (no conflict clause) — for fact tables rebuilt after DELETE."""
    if not rows:
        return 0
    norm = [{k: _na(r.get(k)) for k in cols} for r in rows]
    collist = ",".join(cols)
    ph = ",".join(f":{c}" for c in cols)
    with _engine.begin() as c:
        c.execute(text(f"INSERT INTO {table} ({collist}) VALUES ({ph})"), norm)
    return len(norm)


def replace_rows(table, rows, cols):
    """Atomic full-table refresh: DELETE + INSERT in ONE transaction, so a concurrent reader
    (the live dashboard/API on Neon) never sees the committed-empty gap between a separate
    DELETE-commit and INSERT-commit. No-op INSERT when rows is empty (table still cleared)."""
    norm = [{k: _na(r.get(k)) for k in cols} for r in rows]
    collist = ",".join(cols)
    ph = ",".join(f":{c}" for c in cols)
    with _engine.begin() as c:
        c.execute(text(f"DELETE FROM {table}"))
        if norm:
            c.execute(text(f"INSERT INTO {table} ({collist}) VALUES ({ph})"), norm)
    return len(norm)


def set_masked(source_id, text_masked, pii_types):
    with _engine.begin() as c:
        c.execute(text("UPDATE analysis SET text_masked=:m, pii_types=:t WHERE source_id=:s"),
                  {"m": text_masked, "t": pii_types, "s": source_id})


if __name__ == "__main__":
    init_db()
