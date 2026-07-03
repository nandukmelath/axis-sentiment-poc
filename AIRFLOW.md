# Data pipeline — Supabase (DB) + Apache Airflow (orchestration)

Free POC stack: **Supabase** (hosted Postgres, free) as the shared DB, **Airflow** (self-hosted via Astro CLI, free) to run + schedule + visualize the pipeline. `db.py` is already Postgres-ready — it all follows one env var, `DATABASE_URL`.

## 1. Move the DB to Supabase (free)
1. Create a project at supabase.com (free tier).
2. Project Settings → Database → **Connection string → URI**. It looks like:
   `postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres`
   Append `?sslmode=require`.
3. Migrate your local data up:
   ```powershell
   $env:DATABASE_URL="postgresql://postgres.<ref>:<PW>@...pooler.supabase.com:5432/postgres?sslmode=require"
   python migrate_to_pg.py
   ```
4. Point the app at Supabase — add the same line to `.env`:
   ```
   DATABASE_URL=postgresql://postgres.<ref>:<PW>@...pooler.supabase.com:5432/postgres?sslmode=require
   ```
   Now every script + the dashboard read/write Supabase. (Unset `DATABASE_URL` to go back to local SQLite.)

## 2. Run Airflow locally (free, full UI)
Managed Airflow (Astronomer/Composer/MWAA) is **not free** — self-host with the **Astro CLI**:

```bash
# install Astro CLI (Windows: winget install -e --id Astronomer.Astro)
mkdir axis-airflow && cd axis-airflow
astro dev init
```
Then:
- Copy this project into the Astro project (e.g. into `include/axis/`) OR set the DAG's `AXIS_PROJECT_DIR` to where the code lives in the container (default `/usr/local/airflow/axis`).
- Copy `dags/axis_pipeline.py` **and** `dags/axis_backfill.py` into the Astro project's `dags/`.
- Copy `requirements-airflow.txt` → the Astro project's `requirements.txt`.
- Put your keys in the Astro project's `.env`:
  ```
  DATABASE_URL=postgresql://...supabase...?sslmode=require
  LLM_PROVIDER=groq
  GROQ_API_KEY=gsk_...
  SCRAPEBADGER_API_KEY=sb_live_...
  AXIS_PROJECT_DIR=/usr/local/airflow/axis
  ```
```bash
astro dev start        # Airflow UI at http://localhost:8080  (admin/admin)
```

## 3. The DAGs

### `axis_sentiment_pipeline` — the recurring pipeline (grouped into TaskGroups)
```
ingest        scrape_news · scrape_play · scrape_appstore · scrape_reddit
              scrape_youtube · scrape_scrapebadger · scrape_twitter      (parallel → raw_posts)
                                     │
enrich        vader_baseline ──▶ llm_classify ──▶ cluster
              (VADER + PII mask,  (LLM depth on   (embeddings → issues
               free, every post)   negatives)      + emerging alarm)
                                     │
              ┌──────────────────────┴──────────────────────┐
              ▼                                              ▼
report: exec_brief                        warehouse   wh_dims ──▶ wh_facts ──▶
        (board summary)                                wh_resolution ──▶ wh_marts
                                          (SCD2 author dim · fact_mention/aspect ·
                                           fact_interaction · RM + admin marts +
                                           Sentiment Recovery Rate)
```
- Grouped as **ingest / enrich / warehouse** TaskGroups — clean graph, each stage is its own task with logs + retries (2 built in).
- **`vader_baseline` and `llm_classify` are separate tasks**: the free baseline always runs; the LLM task is batch-resilient, so a missing/expired key degrades gracefully (baseline stands) instead of failing the DAG.
- The **warehouse** stage is split (dims → facts → resolution → marts) so a failure is pinpointed to one gold step.
- Manual by default. To schedule: set env `AXIS_SCHEDULE="*/30 * * * *"` (every 30 min) on the worker — no code edit.

### `axis_x_backfill` — one-off historical X backfill (manual, parameterized)
Trigger from the UI ("Trigger DAG w/ config") with `days` / `window` / `pages` / `query`.
Flow: `backfill_fetch` (ScrapeBadger, burns credits) → `vader_baseline` (+PII mask) → `build_warehouse`.
LLM depth is deliberately left to the recurring pipeline (backfill volume vs Gemini daily quota).

## 4. What wires in next (per plan)
- **Apache Beam / Dataflow**: a `transform` TaskGroup slots **between `ingest` and `enrich`**
  (normalise + dedup + language-detect + heavy cleaning at scale). Make `ingest >> transform >> enrich`.
  Use `BeamRunPythonPipelineOperator` (or `DataflowCreatePythonJobOperator` for cloud).
- **LLM API**: `llm_classify` already routes through `analyze.llm` (`LLM_PROVIDER`); point it at
  the managed API + key in the worker env — no DAG change. On-prem Ollama = residency mode.

## Future / cloud
- Always-on free: run Airflow (Docker) on an **Oracle Cloud Always-Free** ARM VM.
- Or managed later: Astronomer / Google Cloud Composer (paid).
- DB already on Supabase, so moving compute to the cloud is just running the same DAGs against the same `DATABASE_URL`.
- Warehouse gold layer → port `warehouse/` to **dbt-core** (snapshots for SCD2, tests) run as a downstream Airflow task; see `warehouse/README.md`.
