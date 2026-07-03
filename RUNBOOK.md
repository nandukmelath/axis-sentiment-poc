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

## Secrets
- Keys live in `.env` (git-ignored). Never commit. `LLM_PROVIDER=groq` + `GROQ_API_KEY` for LLM depth.
- **Rotate a leaked key**: console.groq.com/keys (Groq) — revoke + recreate, update `.env`. No restart needed (read per run).

## Move DB to Supabase/Postgres (prod)
```bash
export DATABASE_URL="postgresql+psycopg2://...supabase...?sslmode=require"
python migrate_to_pg.py            # copy SQLite → Postgres
```
Then every module + Airflow task uses Postgres via the same env var. See `AIRFLOW.md`.

## Schedule
Set env `AXIS_SCHEDULE="*/30 * * * *"` on the Airflow worker to run every 30 min (no code change).
