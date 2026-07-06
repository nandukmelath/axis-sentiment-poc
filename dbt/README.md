# dbt project — warehouse port (Tier 4)

Ports the warehouse to dbt for lineage, tests, and SCD2 snapshots — the prod-grade
replacement for the hand-rolled SQL in `warehouse/`.

- **models/staging/stg_mentions.sql** — one row per analysed mention.
- **models/marts/** — `mart_sentiment_daily`, `mart_team_load` (add the rest incrementally).
- **snapshots/dim_customer_snapshot.sql** — dbt-native SCD Type 2 on the customer master.
- **models/schema.yml** — data tests (not_null, unique, accepted_values).

## Run
```bash
pip install dbt-core dbt-sqlite     # local; or dbt-postgres for prod
export DBT_PROFILES_DIR=./dbt
export AXIS_DB_PATH=../axis.db       # sqlite target
cd dbt && dbt deps && dbt build      # runs models + snapshots + tests
dbt docs generate && dbt docs serve  # lineage graph
```
For Postgres/Supabase: `dbt build --target postgres` (set PGHOST/PGUSER/PGPASSWORD/PGDATABASE).

The Python marts and these dbt models produce the same shapes; migrate incrementally,
one mart at a time, verifying row counts match before cutting over.
