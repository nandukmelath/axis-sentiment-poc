# ADR-001: Make the DQ gate a real pre-commit gate

**Status:** Accepted — **v1 (snapshot + restore-on-failure) SHIPPED 2026-07-09**; v2 (schema-swap) deferred
**Date:** 2026-07-09
**Deciders:** project owner (solo)

## Implemented (v1)
Shipped a **snapshot → build → DQ → publish-or-restore** gate instead of the pure staging-swap
(Option A below), chosen because it is **dual-dialect, view/index-safe, and needs no builder
rewrites** — much lower risk to land on the live cron:
- `db.snapshot_tables()` copies the read-critical rebuilt tables (`db.GATED_TABLES`) to `{t}__bak`
  before any are rebuilt (after `ensure_tables()` so columns match).
- `run_harvest` runs the full build, then `dq_checks.run()`. On **PASS** → `drop_snapshots()`
  (publish). On **FAIL** → `restore_tables()` (DELETE+INSERT from `__bak`, so the live tables —
  and their indexes + dependent views — are never dropped) and `sys.exit(1)` so the cron goes red.
- Tested both dialects (`tests/test_gate.py`) + verified end-to-end (a wiped fact_mention fails
  DQ and is rolled back to 1754 rows with `vw_mention` intact).

**Effect:** the window where bad/partial data is visible shrinks from **~12h (until next run)** to
**the build duration (~seconds)** — and a failed build never *persists*. **Residual gap (why v2):**
during a build the dashboard can still briefly see cross-table inconsistency (fact updated, mart
not yet). v2 (a Postgres schema-swap that builds into `stg` and swaps atomically) also hides the
mid-build state; deferred because it is Postgres-specific + interacts with the connection pool.

---
## Original analysis (v2 options retained for the deferred work)

## Context
`run_harvest` builds facts/marts by writing DIRECTLY to the live tables the dashboard + API
read (`db.replace_rows` does an atomic per-table DELETE+INSERT), and only THEN runs
`warehouse/dq_checks.py`. So DQ is **post-hoc detection, not a gate**: a bad build (LLM
outage → mostly-VADER rows, a schema drift, a bad join) is already visible to users before
DQ turns the GitHub Actions run red. The docstring was corrected to say so, but the
architecture still lets bad data reach production for the duration of a run.

Forces: dual-dialect (SQLite + Postgres) must both work; the live dashboard reads
continuously; the cron is unattended; Supabase free (no staging infra budget).

## Decision
Build the derived layer into **`stg_*` staging tables**, run `dq_checks` against staging,
and **atomically swap into the live names only if DQ passes** — so bad data never reaches
readers, and a failed run leaves the previous-good tables untouched.

## Options Considered

### Option A: Staging tables + atomic RENAME swap
Build `stg_fact_mention`, `stg_mart_*`, etc.; DQ against `stg_*`; on PASS, in ONE transaction
`ALTER TABLE fact_mention RENAME TO old; ALTER TABLE stg_fact_mention RENAME TO fact_mention; DROP old`.
| Dimension | Assessment |
|---|---|
| Complexity | Med — a swap helper + build-target indirection |
| Cost | $0 (same DB, ~2× transient rows during build) |
| Scalability | Good — swap is metadata-only, instant |
| Team familiarity | High — standard blue/green table pattern |

**Pros:** true gate; instant swap; failed build is a no-op; readers never see partial data.
**Cons:** Postgres DDL is transactional (clean); SQLite `ALTER … RENAME` is not fully transactional (needs a `BEGIN IMMEDIATE` + rename dance); views (`vw_mention`) must be recreated after rename.

### Option B: One long transaction, COMMIT only on DQ pass
Wrap the entire build + DQ in a single `with engine.begin()`; COMMIT only if DQ passes, else rollback.
| Dimension | Assessment |
|---|---|
| Complexity | High — `db.py` auto-commits per call; needs a shared-connection refactor of every builder |
| Cost | $0 but long-held write locks/txn on the live tables |
| Scalability | Poor — a multi-minute open transaction on Postgres blocks vacuum + risks pooler timeouts |
| Team familiarity | Med |

**Pros:** conceptually simplest gate.
**Cons:** requires threading one connection through 8+ builders; long transactions fight the Supabase pooler; DELETE+INSERT still locks live tables for the whole build.

### Option C: Adopt dbt (build + test + swap handled by the framework)
Replace `warehouse/build.py`/`star.py` hand-rolled SQL with dbt models + tests; dbt does
incremental builds, `dbt test` is the gate, and dbt handles atomic swaps.
| Dimension | Assessment |
|---|---|
| Complexity | High upfront (port the whole warehouse to dbt), low after |
| Cost | dbt-core is free; adds a build tool to CI/cron |
| Scalability | Best — incremental + lineage + tests native |
| Team familiarity | Med — dbt is standard but a learning curve |

**Pros:** solves ADR-001 + incremental builds + lineage in one move; the `experimental/dbt` project already exists.
**Cons:** large migration; couples the fix to a framework adoption.

## Trade-off Analysis
Option B's long transaction is the wrong shape for a managed pooler — reject. Option C is the
"right" long-term answer but is a big bet best made deliberately (see the dead-code decision).
**Option A** is the pragmatic, framework-free gate that ships now and is dialect-safe with a
small swap helper. It also composes with a later dbt migration (staging is dbt's model too).

## Consequences
- **Easier:** a failed/partial build can no longer corrupt the live dashboard; rollback is free.
- **Harder:** every builder must target a configurable table prefix; views recreated post-swap; SQLite swap needs care (test both dialects).
- **Revisit:** if we adopt dbt (Option C), this hand-rolled swap is superseded.

## Action Items
1. [ ] Add `db.swap_table(stg, live)` — dialect-aware atomic rename (PG txn; SQLite drop+rename under `BEGIN IMMEDIATE`).
2. [ ] Parameterize builders (`build_facts`, marts, star) with a target prefix (`""` vs `"stg_"`).
3. [ ] `run_harvest`: build `stg_*` → `dq_checks(prefix="stg_")` → swap-all on PASS, else abort + keep previous.
4. [ ] Recreate `vw_mention`/`vw_daily_sentiment` after swap.
5. [ ] Test the swap on SQLite AND Postgres; add a "reader never sees empty" concurrency test.
