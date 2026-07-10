# experimental/ — unwired prototypes (NOT in the live path)

These were built as alternative implementations but are **not used** by the production
pipeline. The live system is: `run_harvest` (batch) triggered by the GitHub Actions cron
(`.github/workflows/pipeline.yml`), writing to Supabase, served by the Streamlit dashboard
+ FastAPI. Nothing here is imported by that path (verified) — kept for reference / future use.

| Dir | What it is | Why it's here, not live | If you revive it |
|-----|-----------|--------------------------|------------------|
| `stream/` | Real-time streaming ingest (Bluesky Jetstream + Mastodon firehose + continuous worker) | The cron batch path covers ingest at POC volume; streaming adds an always-on process to run/pay for | Only when you need sub-12h freshness; run as a separate always-on service |
| `dags/` | Apache Airflow DAGs (27 tasks) | GitHub Actions cron replaced Airflow in the cloud (no Airflow host to maintain) | Pick ONE orchestrator — Airflow OR GitHub Actions, not both |
| `dbt/` | dbt-core project (staging/marts models, snapshots, tests) | The warehouse is hand-rolled in `warehouse/build.py` + `star.py`; dbt was documented as the "prod path" but never wired | Adopt dbt to REPLACE the hand-rolled build (gets lineage/tests/incremental) — see docs/adr/ADR-001 |

**Decision owed (see the architecture review):** collapse to one story. Either adopt `dbt/`
(and delete `warehouse/build.py`'s hand-rolled SQL), or delete `dbt/`. Same for streaming vs batch.
