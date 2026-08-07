# Demo Safety Brief — read before you present

Findings from a full read of the source + live queries against `axis.db`.
CODEMAP.md explains every file. This file is what could go wrong on screen.

---

## DO NOT DO THESE

| Action | Why |
|---|---|
| Press **"Run fetch + refresh"** | Synchronous `subprocess.run(timeout=900)` — up to 15 min frozen browser tab. On failure it prints raw stderr to the screen. |
| Open the **GitHub Actions** tab | Last 30 scheduled runs are all `cancelled` at 50m — the 12h cron has not completed since 2026-07-10. |
| Run `docker compose up` | The `stream` service crash-loops (`stream/` was moved to `experimental/`). Also `COPY . .` would bake the live Neon password from `*.log` files into an image layer. |
| Open the **cluster / Root Cause dropdown** | 1,106 clusters for 1,769 posts; 918 are singletons. Titles include raw truncated tweets with @handles. |
| Show the **per-class eval report** | n=18 gold set; several classes have n=1, so F1 is 0.0 or 1.0. |
| Run `dbt build` or `python -m tools.backup` | dbt is not installed anywhere; backup needs `pg_dump` on PATH. |
| Open **exec_summary.md / Digest tab** unregenerated | Stale (8 Jul). Its #1 issue is literally "Other". Digest's top-5 "issues" are all *praise*. |
| Scroll the **RM Cockpit** name column | Synthetic customers: "Fedup Sharma", "Nikhil Newbie", "Deepak Deals". |

## Numbers that will not reconcile if a client cross-checks

- **Mentions:** War-Room shows 1,769 · Admin tab shows 1,754.
- **Fraud:** War-Room 126 · Fraud board 125 · Team Queues 73 (queue filters out `team='none'`, which holds 52 fraud rows).
- **Sources:** README says 9 · code registers 20 · DB has rows from 10.
- **Tests:** RUNBOOK says 43 · actual suite is 97.

## Claims to soften before you say them

| Don't say | Say instead |
|---|---|
| "Every post is AI-classified" | "VADER scores 100% instantly; the LLM escalates the negative/ambiguous half." **963 of 1,769 rows (54%) are VADER-only** — by design (the cost cascade), but say it first. |
| "Auto-routes to the owning team" | Covers 38% — `recommended_team='none'` on 1,090 rows. |
| "25% sentiment recovery rate" | It is **1 of 4** threads, and the 1 is a synthetic seed row. Present the mechanism, not the percentage. |
| "94% accuracy" | 17/18 on a hand-written gold set. Binomial 95% CI ≈ 73–99%. Also measured on a different code path (`gemini_client`) than the one that classified the data (`freellmapi`). |
| "Validated churn model" | LogisticRegression fit and scored on the same rows, with the label leaking into the features. All 50 rows sit at p≥0.945. **#2 entry is @MyIndusIndBank — a competitor's corporate handle.** Call it a rules-weighted risk score. |
| "86% share of voice" | Axis is counted from 9 social sources; competitors from Google News RSS only, capped. Different scorers too (LLM vs VADER). Not like-for-like. |
| "Every mart is REST-exposed" | 10 of 13. |
| "Real-time" | `@st.cache_data(ttl=1800)` + a 12h cron. The clock and ticker animate; the numbers can be 30 min stale, the data 12h. |

## Visuals that are silent on this data

- **Red "EMERGING" banner** — 0 clusters match `recent_share>=0.6 AND avg_score<0 AND size>=2`.
- **Trends spike alarm** — `mart_trends` has 0 rows with `anomaly=1`.
- **Alerts tab** — all 5 rows are `severity=critical` and `sent=0`; three of them are the same fraud alert from three runs quoting different counts (111 / 122 / 126).
- **Audit tab** — 0 rows, and it only renders under the "Admin (all)" role.
- **War-Room itself** — only in `ROLE_TABS['Exec']`. Switch the role selector and the whole flagship screen disappears.

## Data provenance a client could stumble on

- 3 **fabricated tweets** (`x:1,2,3`) from `fetch/twitter_import.csv` — invented authors, URLs ending `/status/1` that 404. One is a fake fraud alert.
- 19 **synthetic seed rows** (`seed:*`, `thr:*`) in the same `raw_posts` table as real data, including the Visa test card `4111 1111 1111 1111`.
- All **458 Play Store rows** have `url='com.axis.mobile'` — not a URL. Clicking any Play mention goes nowhere.
- Newest `fetched_at` is **2026-07-07**. The "live war room" is showing three-week-old data.
- `dim_date` spans 2013→2026 (4,667 days) because a scraped post carries a 2013 timestamp.

## Security questions a bank will ask

- **Both auth gates are OFF.** Neither `AXIS_DASH_PASSWORD` nor `AXIS_API_KEY` is set, and CORS defaults to `*`. The code supports both — set them if this is on a public URL.
- API rate limit trusts a client-supplied `X-Forwarded-For` with no proxy allowlist → trivially bypassed by varying the header.
- `pyproject.toml` sets `bandit skips = ["B608"]` (the SQL-injection rule). The written justification is sound (bind params everywhere; only `int()`-cast LIMIT/OFFSET is interpolated) — **have that sentence ready**.
- `pip-audit` in CI is `|| true` — advisory, cannot fail the build.
- The public GitHub repo carries Axis branding and scraped complaint text.

## Answers to have ready

- **"Do you deduplicate?"** → "We detect duplicates in the Beam stage. They're flagged, not yet excluded from the facts." (144 `is_duplicate=1` rows are currently inside `fact_mention`.)
- **"Which orchestrator?"** → "GitHub Actions cron runs it today; the Airflow DAG is the drop-in for an enterprise scheduler and calls the identical modules." (ARCHITECTURE.md, PITCH.md and CLOUD-MIGRATION.md contradict each other in writing.)
- **"Is dbt in production?"** → No. 2 of 13 marts ported, never run, not installed.
- **"Why the test card number in your database?"** → Synthetic test vector, deliberately seeded to prove the PII masker works. Only masked text (`analysis.text_masked`) ever reaches an LLM.

---

## Pre-demo checklist

Run from `C:\Users\nandu\axis-sentiment-poc` (relative paths — `exec_summary.md` and `weekly_digest.md` load from CWD):

```bash
py -3.14 -m pytest -q
```
Expect `97 passed`. Plain `pytest` on PATH resolves to Python 3.10 and produces 6 collection errors.

Rebuild the stale trend/forecast marts (they were built before the date-parse fix — currently 19 rows covering 27% of mentions and zero anomalies; a rebuild yields ~224 rows and 1 real anomaly):

```bash
py -3.14 -m analytics.features
```

```bash
py -3.14 -m analytics.intelligence
```

Then: start the dashboard from the repo root, demo in **light** mode (dark is only half-wired), click **Export** once so the Audit tab has a row, and keep the role selector on **Exec**.
