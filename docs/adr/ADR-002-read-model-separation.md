# ADR-002: Separate the dashboard read-model from the write DB (CQRS-lite)

**Status:** Proposed
**Date:** 2026-07-09
**Deciders:** project owner (solo)

## Context
The Streamlit dashboard reads the **same Postgres** the cron writes, and its `fresh()` loader
pulled the **full `analysis ⨝ raw_posts ⨝ clean_posts` join — every row, full post text** —
on every auto-refresh (1s + 6s fragments). Uncached, that streamed GBs/day and **exhausted
Neon's free 5 GB data-transfer quota**, taking the dashboard down and forcing a migration to
Supabase. A `@st.cache_data(ttl=1800)` patch cut egress ~99.9%, but the underlying design is
wrong: a presentation layer should never scan bronze raw text, and it's coupled to the write
DB's limits.

Forces: free-tier egress/connection caps; "LIVE" UX expectation; single small DB; no budget
for a separate read store.

## Decision
Give the dashboard/API a **thin read-model of pre-aggregated marts only**, and serve raw
mentions through a **bounded, paginated** query — never the full table. Keep bronze raw text
out of the hot read path.

## Options Considered

### Option A: Read-model = marts only + bounded "recent mentions" (CQRS-lite)
KPIs/charts read `mart_kpis`, `fact_daily`, `mart_channel`, `mart_*` (tens–hundreds of rows).
Ticker/detail read `SELECT … FROM vw_mention ORDER BY created_date DESC LIMIT 200`.
| Dimension | Assessment |
|---|---|
| Complexity | Med — add a couple of dashboard-shaped marts; refactor `fresh()` |
| Cost | $0; egress drops from ~MB/query to ~KB/query |
| Scalability | Good — read cost is O(marts), independent of total data |
| Team familiarity | High |

**Pros:** kills the egress-blowout class of bug; scales; fewer columns over the wire; the cache becomes belt-not-lifeline.
**Cons:** filters that need per-post fields (search over full text, arbitrary slicing) must move server-side or to a dedicated mart; some dashboard refactor.

### Option B: Supabase read replica / separate read DB
Point the dashboard at a replica; cron writes primary; replication keeps read isolated.
| Dimension | Assessment |
|---|---|
| Complexity | Med-High — replica provisioning, lag handling |
| Cost | Replicas are a **paid** Supabase feature (not free tier) |
| Scalability | Best — full read/write isolation |
| Team familiarity | Med |

**Pros:** true isolation; dashboard load can't affect the cron; no query refactor.
**Cons:** not free; still egresses full rows unless Option A is also done; overkill at POC scale.

### Option C: Materialize a single denormalized "dashboard snapshot" table + a JSON blob
Cron writes one `dashboard_snapshot` row (KPIs + pre-computed chart series as JSON) each build;
the dashboard reads that one row + a small recent-mentions table.
| Dimension | Assessment |
|---|---|
| Complexity | Med — a snapshot builder |
| Cost | $0; minimal egress (one row) |
| Scalability | Excellent for fixed views; rigid for ad-hoc filtering |
| Team familiarity | Med |

**Pros:** absolute minimum egress; trivially cacheable.
**Cons:** every new chart/filter needs a snapshot change; loses interactive slicing.

## Trade-off Analysis
Option B costs money and doesn't fix the fat-row read on its own — defer until real traffic.
Option C is minimal-egress but too rigid for an interactive war-room. **Option A** keeps
interactivity, is free, and directly removes the failure class — the right first move. It also
front-runs Option B (a replica later just serves the same thin model).

## Consequences
- **Easier:** the dashboard can't blow the DB's egress/connection budget; scales to 10⁵–10⁶ rows; cache TTL can shorten (queries are cheap) → feels more "live" again.
- **Harder:** full-text search + arbitrary per-post filters need a server-side endpoint or a purpose-built mart; more marts to maintain.
- **Revisit:** if concurrent viewers grow, add Option B (paid replica) reading the same thin model.

## Action Items
1. [ ] Add dashboard-shaped marts if missing (daily sentiment series, emotion counts, source mix) so charts never scan `analysis`.
2. [ ] Refactor `dashboard/app.py fresh()`: KPIs/charts ← marts; ticker/detail ← `vw_mention … LIMIT 200`.
3. [ ] Move full-text search to a server-side `WHERE text ILIKE :q LIMIT N` (or a `pg_trgm` index) instead of pulling all text to pandas.
4. [ ] Keep `@st.cache_data` but lower the TTL once per-query cost is small.
5. [ ] Add an egress smoke check: assert a dashboard render reads < ~200 KB from the DB.
