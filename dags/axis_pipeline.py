"""Airflow DAG — Axis Bank social sentiment pipeline (the full flow we built).

Task graph (visible in the Airflow UI as grouped nodes):

    ingest ─────────────────────────────┐
      scrape_news  scrape_play           │
      scrape_appstore  scrape_reddit     │  (parallel; one dead source
      scrape_youtube  scrape_scrapebadger│   never kills the others)
      scrape_twitter                     │
                                         ▼
    transform (Apache Beam, keyless)   normalise · dedup · lang-detect · spam · PII mask
                                         ▼
    enrich                          ┌────────────┐
      vader_baseline  ──▶  llm_classify  ──▶  cluster
      (VADER + PII mask, free)   (LLM depth on negatives)   (embeddings → issues)
                                         │
                     ┌───────────────────┴───────────────────┐
                     ▼                                        ▼
    report: exec_brief                          warehouse (gold layer)
      (board summary)                             wh_dims (SCD2 author) ──▶
                                                  wh_facts ──▶ wh_resolution ──▶ wh_marts
                                                  (RM cockpit + admin marts + Sentiment Recovery Rate)

Runs each project module via BashOperator so nothing heavy is imported at DAG-parse
time. The Airflow worker needs: the project code at AXIS_PROJECT_DIR, its Python deps,
and env vars (DATABASE_URL → Supabase, LLM_PROVIDER=groq + GROQ_API_KEY, SCRAPEBADGER_API_KEY).
With the Astro CLI these come from the project + its .env.

    Notes:
      * transform = Apache Beam (transform.beam_transform). Keyless — DirectRunner locally,
        DataflowRunner in the cloud (set BEAM_RUNNER). The BeamRunPythonPipelineOperator from
        airflow.providers.apache.beam is a drop-in alternative to the BashOperator used here.
      * LLM API: `llm_classify` already routes through analyze.llm (LLM_PROVIDER); swap the
        provider/key in the worker env, no DAG change. With no key the free VADER baseline stands.
"""
from datetime import datetime, timedelta
import os

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.task_group import TaskGroup

PROJ = os.getenv("AXIS_PROJECT_DIR", "/usr/local/airflow/axis")
# Python that runs the task modules (a venv holding the PROJECT deps — kept separate
# from the Airflow venv to avoid apache-beam/protobuf conflicts). Defaults to "python".
PY = os.getenv("AXIS_PYTHON", "python")
# Manual by default; set AXIS_SCHEDULE="*/30 * * * *" to run every 30 min.
SCHEDULE = os.getenv("AXIS_SCHEDULE") or None

INGEST_SOURCES = ["news", "play", "appstore", "reddit", "youtube", "scrapebadger", "twitter",
                  "hackernews", "mastodon"]

default_args = {
    "owner": "axis-data",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "depends_on_past": False,
}


def cmd(sub):
    # PYTHONIOENCODING avoids console unicode crashes; run from the project dir
    return f"cd {PROJ} && PYTHONIOENCODING=utf-8 {PY} -m {sub}"


with DAG(
    dag_id="axis_sentiment_pipeline",
    description="ingest → VADER+PII → LLM classify → cluster → (exec brief · warehouse marts)",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule=SCHEDULE,
    catchup=False,
    max_active_runs=1,
    tags=["axis", "sentiment", "warehouse"],
    doc_md=__doc__,
) as dag:

    # ---- ingest: pull every source in parallel into raw_posts ----
    with TaskGroup(group_id="ingest", tooltip="parallel scrape → raw_posts (deduped)") as ingest:
        for s in INGEST_SOURCES:
            BashOperator(task_id=f"scrape_{s}", bash_command=cmd(f"fetch.run_fetch --only {s}"))

    # ---- transform: Apache Beam — normalise / dedup / lang / spam / PII mask (keyless) ----
    transform = BashOperator(task_id="transform", bash_command=cmd("transform.beam_transform"))

    # ---- enrich: baseline (free) → LLM depth → cluster ----
    with TaskGroup(group_id="enrich", tooltip="VADER+PII baseline → LLM depth → cluster") as enrich:
        vader_baseline = BashOperator(task_id="vader_baseline",
                                      bash_command=cmd("analyze.run_analyze --phase baseline"))
        llm_classify = BashOperator(task_id="llm_classify",
                                    bash_command=cmd("analyze.run_analyze --phase llm"))
        cluster = BashOperator(task_id="cluster", bash_command=cmd("analyze.embed_cluster"))
        vader_baseline >> llm_classify >> cluster

    # ---- report: board-ready exec brief ----
    exec_brief = BashOperator(task_id="exec_brief", bash_command=cmd("analyze.exec_summary"))

    # ---- warehouse: gold layer, one task per stage for observability ----
    with TaskGroup(group_id="warehouse", tooltip="SCD2 dims → facts → resolution → marts") as warehouse:
        wh_dims = BashOperator(task_id="wh_dims", bash_command=cmd("warehouse.build --step dims"))
        wh_facts = BashOperator(task_id="wh_facts", bash_command=cmd("warehouse.build --step facts"))
        wh_resolution = BashOperator(task_id="wh_resolution", bash_command=cmd("warehouse.build --step resolution"))
        wh_marts = BashOperator(task_id="wh_marts", bash_command=cmd("warehouse.build --step marts"))
        wh_dims >> wh_facts >> wh_resolution >> wh_marts

    # ---- data-quality gate: fails the run if the gold layer is bad ----
    dq_check = BashOperator(task_id="dq_check", bash_command=cmd("warehouse.dq_checks"))

    # ---- product feature layer: competitor SOV, analytics marts, replies, alerts, digest ----
    with TaskGroup(group_id="features", tooltip="competitor SOV · marts · replies · alerts · digest") as features:
        f_competitor = BashOperator(task_id="competitor_sov", bash_command=cmd("analytics.competitor"))
        f_marts = BashOperator(task_id="analytics_marts", bash_command=cmd("analytics.features"))
        f_respond = BashOperator(task_id="response_drafts", bash_command=cmd("analytics.actions respond"))
        f_alerts = BashOperator(task_id="alerts", bash_command=cmd("analytics.actions alerts"))
        f_digest = BashOperator(task_id="weekly_digest", bash_command=cmd("analytics.actions digest"))
        f_competitor >> f_marts >> [f_respond, f_alerts, f_digest]

    ingest >> transform >> enrich
    enrich >> exec_brief
    enrich >> warehouse >> dq_check >> features
