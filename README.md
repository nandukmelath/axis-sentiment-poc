# Axis Bank — Social Intelligence Platform

Fetch public social posts about Axis Bank → **LLM decision-grade sentiment analysis** → star-schema warehouse → insight marts (churn/forecast/fraud/geo/competitor-SOV) → live war-room dashboard + read API. Free stack end-to-end; a 6-provider LLM failover pool (FreeLLMAPI or direct) powers the AI.

**Everything below is built and tested** (83 tests, dual-dialect SQLite/Postgres, 11-check DQ gate):
fetchers (9 sources) · Beam transform · VADER→LLM cascade · clustering · SCD2 warehouse ·
13 insight marts · Streamlit war-room (role-based tabs) · FastAPI read API · Airflow DAG (27 tasks) ·
dbt project · CI (pytest+bandit+pip-audit). Cloud deploy: see **CLOUD-MIGRATION.md**
(Neon Postgres + GitHub Actions cron + Koyeb API + Streamlit Cloud). Ops: **RUNBOOK.md**.

## Pipeline

```
FETCH (9 sources)     STORE               AI LAYER                    WAREHOUSE + INSIGHTS         SERVE
News/Play/AppStore -> SQLite or   ->  Beam transform            ->  dims/facts (SCD2)        ->  Streamlit war-room
Reddit/YT/X/HN/     Postgres          VADER -> LLM cascade          13 marts + alerts            FastAPI read API
Mastodon/Bluesky    (DATABASE_URL)    embed_cluster + exec brief    DQ gate (11 checks)          weekly digest
```

## What the AI layer extracts (per post)
Decision-grade record, not just pos/neg: sentiment+score, emotion+intensity, sarcasm, **aspect-based** sentiment (app/UPI/cards/loans/branch/support/fees/fraud) with evidence, intent (complaint/churn_threat/legal_threat/fraud_report/journalist/…), urgency+reason, RBI category, named product, root cause, **recommended team + action**, churn/fraud/PII flags, theme, summary, confidence. See `analyze/schema.py`.

Then `embed_cluster` groups duplicates into ranked **issues** (with an *emerging-in-last-24h* signal), and `exec_summary` writes a board-ready "top 5 issues + actions".

## Setup

```bash
cd C:\Users\nandu\axis-sentiment-poc
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then paste your GEMINI_API_KEY  (free: aistudio.google.com)
```

## Run the AI layer on seed data (no fetchers/keys except Gemini)

```bash
python db.py                       # create axis.db
python sample_data\seed_posts.py   # load 16 realistic sample posts
python -m analyze.run_analyze      # LLM scores every post  (add --verify for fraud 2nd pass)
python -m analyze.embed_cluster    # cluster into issues + flag emerging ones
python -m analyze.exec_summary     # Gemini writes exec_summary.md
python -m eval.run_eval            # accuracy vs human-labeled gold set  <-- the credibility number
```

## Free-tier notes (verified on a real key, 2026-07)
- **Model matters:** `gemini-2.0-*` free tier is often **disabled** (429 `limit: 0`). Use **`gemini-2.5-flash`** (default) — its free tier = **~5 requests/min**. Embeddings model = **`gemini-embedding-001`** (3072-dim); `text-embedding-004` is not available on newer keys.
- We **batch 12 posts/call + cache** (already-scored posts skipped) and the client **respects the server `retryDelay`** on 429, so multi-batch runs self-throttle to the 5 RPM window. Guard a big first run with `--limit`.
- `--verify` (2nd-pass fraud) multiplies calls — skip it on the free tier for large runs.
- Embeddings + clustering are free (Gemini embeddings, or swap to local `sentence-transformers`; clustering is sklearn).
- **Measured zero-shot accuracy** on the gold set: sentiment 94%, urgency 89%, intent 94%.

## Fetching live data (Phase 1 — BUILT)

```bash
python -m fetch.run_fetch     # pull all sources -> raw_posts (deduped)
python run_all.py             # fetch -> analyze -> cluster -> exec brief (schedule this)
```

Per source:
| Source | Needs | Status |
|---|---|---|
| News (Google News RSS) | nothing | ✅ live |
| Direct banking-desk RSS pack (ET/ETBFSI/HBL/Mint) | nothing | ✅ live |
| **Technofino forum** (Axis CC complaint epicenter — threads + replies) | nothing | ✅ live |
| GDELT global news index (regional/vernacular outlets) | nothing (1 req/6s throttle) | ✅ live |
| Google Play reviews | nothing | ✅ live |
| Apple App Store | nothing (id auto-resolved) | ✅ live |
| Reddit | nothing (**keyless RSS fallback**); keys unlock scores + deeper comments | ✅ live |
| Hacker News (Algolia) | nothing | ✅ live |
| Mastodon | nothing | ✅ live |
| YouTube | `YOUTUBE_API_KEY` (free) | ready |
| **X / Twitter** | ScrapeBadger key (paid) or **CSV import** | ✅ via CSV |

Keyless-source facts learned live (2026-07-07): Reddit 403s `.json` for scripts but serves
`.rss` with a browser UA; Moneycontrol RSS is frozen at Apr-2024 (staleness guard drops it);
Business-Standard RSS is behind a hard 403 bot wall; ConsumerComplaints.in + MouthShut are
Cloudflare-walled (would need a real browser — not worth it); GDELT enforces 1 request/5s.

### X / Twitter — CSV import (default, `TWITTER_MODE=csv`)
Free live X scraping is non-functional in 2026 (Nitter dead, X needs login). So drop tweets into **`fetch/twitter_import.csv`**:
```
text,author,url,created_at,engagement
"Axis UPI failed, money debited, no help",@user,https://x.com/user/status/123,2026-07-02T08:10:00Z,180
```
`run_fetch` imports them with stable ids (re-import = no duplicates). Get the rows from: a manual X search copy/paste, a browser export, or a paid X API pull. Set `TWITTER_MODE=scrape` to try the (usually empty) Nitter scraper, or `auto` for scrape-then-CSV.

## One-shot runs
- `python -m run_window --window 1h|1d|1m` — windowed fetch→classify→marts (the dashboard RUN button).
- `python -m run_harvest` — max harvest: everything above + resolution/translate/competitor/brief/DQ.
  Scale fetch caps with `FETCH_MULT=8`.
- Competitor SOV (HDFC/ICICI/SBI/Kotak) is built in (`analytics/competitor.py`, runs inside harvest).

## Phase 5 — streaming, Postgres/Docker, cost cascade (BUILT)

### Streaming pipeline (decoupled producer + worker)
```bash
python -m stream.run_stream      # producer (pollers + Bluesky + Mastodon firehoses) + worker (continuous scoring)
```
- **Bluesky Jetstream firehose** — free, no auth, real-time (verified live).
- **Mastodon** — free but most instances need a token: create an app (Settings → Development → New application), copy the access token → set `MASTODON_TOKEN` (+ optional `MASTODON_INSTANCE`).
- **X/Twitter** stays CSV-only (no free API exists).

### Cheap-classifier-first cascade (`analyze/cascade.py`)
VADER scores every post instantly & free; only **negative / ambiguous / high-stakes** posts escalate to Gemini for full decision-grade extraction. Clear positives get a light record and never hit the LLM — big quota/cost saver at scale. Records are tagged `model = vader-fast` vs the Gemini model. Toggle with `CASCADE=0` (force all-LLM).

### Postgres + Docker (production)
- **Dual-store:** `db.py` runs on SQLite (default) or Postgres — just set `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db`.
- **One command:** `GEMINI_API_KEY=xxx docker compose up --build` → Postgres + dashboard (`:8501`) + streaming worker.

## Phase 6 — X / Twitter ingestion

### ScrapeBadger API (PREFERRED — paid, ToS-clean, no browser)
The reliable X source. Set `SCRAPEBADGER_API_KEY` in `.env`. Captures rich fields per tweet:
handle (`author`), display name (`author_name`), text, url, created_at, lang, and
like/reply/retweet/quote/view/bookmark counts + `conversation_id` — plus the **full tweet JSON**
(media, hashtags, mentions, poll, place…) preserved in `raw_json`.
```bash
python -m fetch.scrapebadger                       # recent Axis mentions -> raw_posts
python -m fetch.scrapebadger backfill --days 365   # historical year (date-windowed, paginated, incremental)
python -m analyze.run_analyze                       # classify
```
Query = `(@AxisBank OR "Axis Bank" OR @AxisBankSupport)` (config `SB_QUERY`). Wired into `run_fetch` and the streaming `producer` (polls every 120s). **This is the clean way to backfill the past year** — reliable pagination, no ban risk (costs API credits per request).

### X.com authenticated crawler (fallback — free, fragile)

X has no free API and blocks anonymous search (redirects to a login wall — verified). This crawls with a real logged-in browser (Playwright). **Use a burner X account — automated scraping breaks X's ToS.**

```bash
pip install playwright && python -m playwright install chromium
python -m fetch.x_crawler login     # one-time: log in manually, saves session cookies to fetch/x_state.json
python -m fetch.x_crawler           # crawl "Axis Bank" / @AxisBank / to:AxisBank(Support) -> raw_posts
```
Once `x_state.json` exists, the streaming `producer` auto-crawls X every few minutes, and the `worker` classifies the posts live — no extra step. Config: `X_SEARCH_QUERIES`, `X_HANDLES`, `X_SCROLLS`, `X_HEADLESS`. `x_state.json` holds live cookies → gitignored, keep secret.

### Historical backfill (past year, date-windowed)
```bash
python -m fetch.x_backfill                       # 365 days, 7-day windows (53 windows)
python -m fetch.x_backfill --days 90 --window 7  # smaller
python -m analyze.run_analyze                    # classify what landed
```
Steps back week-by-week with `since:/until:`, upserts each window incrementally (progress survives interruption). **Honest limits:** X won't return a *complete* year for free (you get a strong sample); many heavy scrolls → rate-limit/ban risk (burner account); large volumes take a long time to classify on the Gemini free tier (the VADER cascade absorbs most).

## Free-tier reality (learned live)
- Gemini free quota is **per-model, per-day**. `gemini-2.5-flash` daily cap is small; if you hit 429, switch `GEMINI_MODEL` to `gemini-2.5-flash-lite` (separate pool). ~5 req/min either way → client self-throttles.
