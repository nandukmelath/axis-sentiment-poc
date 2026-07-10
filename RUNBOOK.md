# Runbook — operations

## Run locally (no Airflow, no keys)
```bash
python db.py                       # schema + indexes
python sample_data/seed_posts.py   # or: python -m fetch.run_fetch   (live keyless sources)
python -m transform.beam_transform
python -m analyze.run_analyze      # VADER baseline + LLM depth (if a key is set)
python -m analyze.embed_cluster
python -m warehouse.build          # dims + facts + resolution + marts
python -m warehouse.dq_checks      # data-quality gate
streamlit run dashboard/app.py     # War-Room / RM Cockpit / Admin Analytics
```

## Tests & quality gate
```bash
pip install -r requirements-dev.txt
pytest                             # 43 tests, keyless/offline
python -m warehouse.dq_checks      # exits non-zero on bad data
```

## Airflow (real UI) — WSL2, no Docker
Start (must stay attached, or WSL reaps it):
```bash
wsl -d Ubuntu bash -lc 'cd ~/airflow-axis && source venv/bin/activate && \
  AIRFLOW_HOME=~/airflow-axis AXIS_PROJECT_DIR=/mnt/c/Users/nandu/axis-sentiment-poc \
  AXIS_PYTHON=~/axis-venv/bin/python airflow standalone'
```
→ UI http://localhost:8080 (user `admin`, pw in `~/airflow-axis/standalone_admin_password.txt`).
Refresh DAGs after editing: `cp dags/*.py ~/airflow-axis/dags/`.
Trigger: UI **Trigger DAG**, or `airflow dags trigger axis_sentiment_pipeline`.

## Common issues
| Symptom | Cause | Fix |
|---|---|---|
| Runs stuck `queued` | scheduler died (standalone detached) | relaunch standalone **attached** (above) |
| `exec_brief` writes template | LLM daily budget hit (429) | brief already uses `BRIEF_MODEL` (8b); waits ~24h to reset, or set a paid tier |
| `llm_classify` slow / 0 enriched | LLM 429 rate-limit | expected on free tier; VADER baseline stands; retries self-throttle |
| `cluster` uses TF-IDF | no/weak embedding key | add `GEMINI_API_KEY` for real embeddings |
| a `scrape_*` task green but 0 rows | source needs a key / low volume | add the source key (Reddit/YouTube/ScrapeBadger) |
| `dq_check` fails | bad gold data | read its log — it names the failing check |

## Secrets — THREE synced locations (keep them in step)
Secrets live in 3 places; `.env` is the source of truth. After changing `.env`, propagate:

| Location | Used by | How to update |
|---|---|---|
| **`.env`** (git-ignored) | local dev + `run_harvest` | edit the file (source of truth) |
| **GitHub Actions repo secrets** | the 12h cron (`pipeline.yml`) | `python -m tools.sync_secrets` (pushes DATABASE_URL + LLM keys from `.env`) |
| **Streamlit Cloud app secrets** | the dashboard's `DATABASE_URL` | app menu → Settings → Secrets (manual — NOT synced by the script) |

- `python -m tools.sync_secrets --dry-run` shows which keys will sync. `LLM_PROVIDER=groq` + `GROQ_API_KEY` = LLM depth.
- **Rotate a leaked key**: revoke+recreate at the provider, update `.env`, then `python -m tools.sync_secrets` + update the Streamlit secret. No app restart needed for `.env` (read per run); GH/Streamlit need the secret re-set.
- **DB moved (Neon→Supabase etc.)**: update `.env` DATABASE_URL → `sync_secrets` (cron) → Streamlit Settings→Secrets → the dashboard needs a **reboot** (engine is created at import) — bump the `rebuild-token` in `requirements.txt` to force it.

## Production: Postgres + Airflow LocalExecutor (parallel) — verified locally
Kills the SQLite-on-WSL fragility and runs tasks in parallel (measured: 9 concurrent, 27/27 green).

```bash
# 1. Postgres (local WSL, or a Supabase/Neon URL)
sudo apt-get install -y postgresql
sudo -u postgres psql -c "CREATE USER axis WITH PASSWORD 'axis' SUPERUSER;" -c "CREATE DATABASE axis OWNER axis;"

# 2. Migrate the data + rebuild the derived layer on Postgres
DATABASE_URL="postgresql+psycopg2://axis:axis@localhost:5432/axis" python -m tools.migrate_pg
DATABASE_URL="postgresql+psycopg2://axis:axis@localhost:5432/axis" python -m warehouse.dq_checks   # 11/11

# 3. Airflow on Postgres + LocalExecutor (parallel)
sudo -u postgres createdb -O axis airflow
pip install psycopg2-binary
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://axis:axis@localhost:5432/airflow"
export AIRFLOW__CORE__EXECUTOR=LocalExecutor AIRFLOW__CORE__PARALLELISM=16
export DATABASE_URL="postgresql+psycopg2://axis:axis@localhost:5432/axis"    # tasks use PG too
# Route LLM through FreeLLMAPI. The server runs on Windows; from WSL use the Windows host IP
# (NOT localhost). Find it: `ip route | grep default` or the 172.x.x.1 gateway.
export FREELLM_BASE_URL="http://172.22.112.1:3001/v1"
airflow db migrate && airflow standalone
```
Run the suite on Postgres (dual-dialect): `TEST_DATABASE_URL="postgresql+psycopg2://axis:axis@localhost:5432/axis_test" pytest`.

## Move DB to Supabase/Postgres (prod)
```bash
export DATABASE_URL="postgresql+psycopg2://...supabase...?sslmode=require"
python migrate_to_pg.py            # copy SQLite → Postgres
```
Then every module + Airflow task uses Postgres via the same env var. See `AIRFLOW.md`.

## Schedule
Set env `AXIS_SCHEDULE="*/30 * * * *"` on the Airflow worker to run every 30 min (no code change).
