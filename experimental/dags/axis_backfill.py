"""Airflow DAG — one-off historical X/Twitter backfill (manual trigger).

Separate from the recurring pipeline because it has a different cadence (run on demand),
different cost profile (burns ScrapeBadger credits), and different volume (date-windowed
crawl of up to a year). Trigger from the UI with params:

    days   how far back to go            (default 365)
    window days per search window        (default 7)
    pages  pages per window (100 tweets)  (default 5)
    query  advanced-search query          (default: user mentions to Axis handles)

Flow:  backfill_fetch → vader_baseline (+PII mask) → build_warehouse
LLM depth is intentionally NOT run here (huge volume vs Gemini daily quota); the recurring
axis_sentiment_pipeline enriches the backlog over time, or run it manually with Groq.
"""
from datetime import datetime, timedelta
import os

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.models.param import Param

PROJ = os.getenv("AXIS_PROJECT_DIR", "/usr/local/airflow/axis")
PY = os.getenv("AXIS_PYTHON", "python")
DEFAULT_QUERY = ("(@AxisBank OR @AxisBankSupport OR @AxisDirect_In) "
                 "-from:AxisBank -from:AxisBankSupport -from:AxisDirect_In")

default_args = {"owner": "axis-data", "retries": 1, "retry_delay": timedelta(minutes=5)}


def cmd(sub):
    return f"cd {PROJ} && PYTHONIOENCODING=utf-8 {PY} -m {sub}"


with DAG(
    dag_id="axis_x_backfill",
    description="date-windowed ScrapeBadger backfill → baseline → warehouse (manual)",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule=None,                 # manual only
    catchup=False,
    max_active_runs=1,
    params={
        "days": Param(365, type="integer", minimum=1),
        "window": Param(7, type="integer", minimum=1),
        "pages": Param(5, type="integer", minimum=1),
        "query": Param(DEFAULT_QUERY, type="string"),
    },
    tags=["axis", "backfill"],
    doc_md=__doc__,
) as dag:

    backfill_fetch = BashOperator(
        task_id="backfill_fetch",
        bash_command=cmd('fetch.scrapebadger backfill --days {{ params.days }} '
                         '--window {{ params.window }} --pages {{ params.pages }} '
                         '--query "{{ params.query }}"'),
    )
    vader_baseline = BashOperator(task_id="vader_baseline",
                                  bash_command=cmd("analyze.run_analyze --phase baseline"))
    build_warehouse = BashOperator(task_id="build_warehouse", bash_command=cmd("warehouse.build"))

    backfill_fetch >> vader_baseline >> build_warehouse
