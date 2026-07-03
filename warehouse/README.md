# Gold layer — warehouse / RM + CX marts

Turns the raw sentiment (`raw_posts` → `analysis`, silver) into a Kimball star schema
plus two business marts. Dialect-aware SQL (SQLite for the demo, Postgres/Supabase for
prod). Built by `warehouse/build.py`; runs as the `build_warehouse` task in the Airflow DAG.

## Layers

```
BRONZE  raw_posts                         full text, access-controlled (PII kept here only)
SILVER  analysis + text_masked+pii_types  PII masked before any LLM call (analyze/pii.py)
GOLD    dim_* / fact_* / mart_*           this package
```

## Tables

| Table | Grain | Notes |
|---|---|---|
| `dim_author` | @handle × version | **SCD Type 2** — history of display name / influence tier / customer-link |
| `dim_customer`, `dim_rm`, `dim_product` | entity | from CRM extract (synthetic seed for the demo) |
| `bridge_handle_customer` | handle | join spine social↔CRM (deterministic; probabilistic optional) |
| `fact_mention` | analyzed post | customer voice only (bank handles excluded) |
| `fact_aspect_sentiment` | mention × aspect | exploded from `analysis.aspects_json` |
| `fact_interaction` | issue / thread | accumulating snapshot: opened→responded→resolved→satisfied |
| `mart_rm_enablement` | customer | pain point + cross-sell + talking point for the RM |
| `mart_admin_analytics` | category × team | follow-up bifurcation + SLA |
| `mart_kpis` | 1 row | headline incl. **Sentiment Recovery Rate** |

## Run

```bash
python db.py                      # silver + migrations
python sample_data/seed_crm.py    # synthetic customer master + handle bridge
python sample_data/seed_thread.py # one resolved complaint thread (for CX demo)
python -m analyze.run_analyze     # VADER baseline + PII mask (+ LLM depth if a key is set)
python -m warehouse.build         # dims + facts + resolution + marts
streamlit run dashboard/app.py    # War-Room / RM Cockpit / Admin Analytics tabs
```

`warehouse.build.main()` runs everything: `ensure_tables → build_dim_author (SCD2) →
build_facts → resolution.build_interactions → build_marts`. Re-running is idempotent;
`dim_author` only writes a new version when a tracked attribute actually changes.

## Config

- `AXIS_HANDLES` (config) — official reply handles used to detect a bank response.
- `SLA_RESPONSE_HOURS` — first-response SLA window.
- `PII_MASK=1` — mask card/PAN/Aadhaar/phone/OTP/email before the LLM.
- `RESOLUTION_LLM=1` — optional LLM pass to disambiguate unclear thread outcomes.

## Prod path — dbt on Supabase

The hand-rolled SQL here keeps the POC runnable on SQLite. For production, point
`DATABASE_URL` at Supabase and port this package to **dbt-core**:

- `dim_author` → a **dbt snapshot** (SCD2 is dbt's native strategy — no hand-rolled versioning).
- `fact_*` / `mart_*` → incremental models with `unique_key`.
- add `schema.yml` tests (not_null, relationships, accepted_values) for lineage + trust —
  exactly what the Axis data team will expect to review.

Same grain, same columns; only the execution engine changes.
