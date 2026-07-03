# Architecture

Axis Bank social-sentiment platform: ingest public mentions → clean → classify → cluster →
warehouse → RM/CX marts. Runs fully **keyless** (VADER + Beam + heuristics); LLM keys unlock depth.

## Data flow (medallion → star)

```
SOURCES ──▶ BRONZE ──▶ (transform) ──▶ SILVER ──▶ GOLD ──▶ MARTS
 fetch/     raw_posts   Apache Beam     analysis   dim_* / fact_*   RM cockpit
 (9 keyless)            clean_posts     (+PII mask) star schema      admin analytics
```

| Layer | Tables | Produced by |
|---|---|---|
| Bronze | `raw_posts` | `fetch/*` (news, play, appstore, reddit, youtube, scrapebadger, twitter, hackernews, mastodon) |
| Transform | `clean_posts` | `transform/beam_transform.py` (Apache Beam: normalise, dedup, lang, spam, PII mask) |
| Silver | `analysis` (+`text_masked`,`pii_types`) | `analyze/` — VADER cascade + LLM depth (`LLM_PROVIDER`) |
| Issues | `clusters` | `analyze/embed_cluster.py` (Gemini embeddings → TF-IDF fallback) |
| Gold dims | `dim_author` (**SCD2**), `dim_customer`, `dim_rm`, `dim_product`, `bridge_handle_customer` | `warehouse/build.py` |
| Gold facts | `fact_mention`, `fact_aspect_sentiment`, `fact_interaction` (accumulating snapshot) | `warehouse/build.py`, `warehouse/resolution.py` |
| Marts | `mart_rm_enablement`, `mart_admin_analytics`, `mart_kpis` | `warehouse/build.py` |

## Key design points
- **Cascade classification** — VADER scores every post free/instantly; only negative/neutral posts
  escalate to the LLM. Nothing is ever left unscored, and cost scales with *problems*, not volume.
- **PII masking before the cloud** — card (Luhn)/PAN/Aadhaar/phone/OTP/email masked in `analyze/pii.py`
  before any third-party LLM call. Raw text stays in bronze; masked text in silver. On-prem Ollama = full residency.
- **SCD Type 2 author dimension** — versioned handle history (`effective_from/to`, `is_current`) so a
  mention is analysed against who the author *was at the time*.
- **Resolution / CX fact** — reconstructs threads by `conversation_id`, detects a bank reply via
  `AXIS_HANDLES`, classifies resolved?/satisfied?/recovery → north-star **Sentiment Recovery Rate**.
- **Graceful degradation** — no LLM key: classify skips (VADER stands), cluster→TF-IDF, brief→template.
  Missing source key: that scraper skips. One bad source never fails the run.

## Orchestration
Apache Airflow (`dags/axis_pipeline.py`): `ingest → transform → enrich → (exec_brief · warehouse → dq_check)`.
Tasks are `BashOperator`s calling `python -m ...` via an isolated exec venv (`AXIS_PYTHON`). See `AIRFLOW.md`.

## Storage & scale
- Dual-dialect (`db.py`, SQLAlchemy): SQLite local, Postgres/Supabase prod via `DATABASE_URL`.
- Indexes on hot columns (conversation_id, model, sentiment, fact FKs, dim_author current, text_hash).
- Beam `DirectRunner` local → `DataflowRunner` for cloud scale (same code).
- Prod warehouse path: port `warehouse/` to dbt-core snapshots/tests.

## Quality
- `tests/` — 43 pytest unit/integration tests (PII, LLM parser, cascade, transform, SCD2, resolution,
  marts, cross-sell, KPIs, DQ). Run: `pytest`.
- `warehouse/dq_checks.py` — data-quality gate (coverage, enum validity, SCD2 integrity, orphans,
  resolution integrity) runs as the final DAG task; non-zero exit fails the run.
- CI: `.github/workflows/ci.yml` runs the keyless suite + byte-compile on every push.
