# Cloud Migration — Axis Social Intelligence (all-free, always-on)

Move the whole local stack (WSL Postgres + Airflow + Streamlit + freellmapi) to free cloud
hosts so it runs with the computer off. 3-tier: **frontend / backend / DB**, plus scheduled
jobs and the LLM router.

```
 FRONTEND                 BACKEND                          DB
┌──────────────┐   ┌──────────────────────────┐   ┌──────────────┐
│ Streamlit    │──▶│ FastAPI (api.main:app)    │──▶│  Neon        │
│ dashboard    │   │  Koyeb  (always-on)       │   │  Postgres    │
│ Streamlit    │───┼───────────── reads ───────────▶│  0.5 GB free │
│ Community    │   │ Pipeline jobs             │   │              │
│ Cloud (free) │   │  GitHub Actions cron ─────────▶│  writes      │
└──────────────┘   └────────────┬─────────────┘   └──────────────┘
                                │ LLM
                                ▼
                    freellmapi on Sealos (already live)
```

| Tier | Component | Host | Free? | Needs card |
|------|-----------|------|-------|-----------|
| Frontend | `dashboard/app.py` | Streamlit Community Cloud | yes, always-on | no |
| Backend API | `api/main.py` | Koyeb (Docker) | yes, always-on | no |
| Backend jobs | `run_harvest` | GitHub Actions cron | yes | no |
| DB | Postgres warehouse | Neon serverless | yes, 0.5 GB | no |
| LLM | freellmapi | Sealos (live) | free credits | no |

Everything reads/writes the same Neon DB via `DATABASE_URL`, so the tiers stay decoupled.

---

## Artifacts in this repo (already written)
- `requirements-api.txt` — lean deps for the API image
- `Dockerfile.api` — FastAPI image (Koyeb/Render/HF)
- `.github/workflows/pipeline.yml` — the scheduled pipeline (replaces Airflow)
- `.streamlit/config.toml` + `.streamlit/secrets.toml.example` — dashboard config
- `dashboard/app.py` — bridges `st.secrets["DATABASE_URL"]` → env at startup
- `tools/migrate_pg.py` — copies SQLite → any Postgres + rebuilds the warehouse (existing)

---

## Phase 0 — Code → GitHub  ✅ (done)
Repo pushed **private**: https://github.com/nandukmelath/axis-sentiment-poc
`.env`, `axis.db`, `fetch/x_state.json` gitignored — no secrets left the machine.

> ⚠️ The `.github/workflows/` files are **held back** — the gh OAuth token lacks the
> `workflow` scope. They exist locally (`pipeline.yml`, `ci.yml`). To land them, either:
> 1. `gh auth refresh -h github.com -s workflow` → `git add -f .github/workflows && git commit -m "add workflows" && git push`, **or**
> 2. GitHub repo → **Actions → New workflow → set up a workflow yourself** → paste `pipeline.yml` (web UI isn't scope-gated).

## Phase 1 — DB tier → Neon
1. Sign up at **neon.tech** (GitHub login, no card). New project → region close to you.
2. Copy the **connection string**, convert to SQLAlchemy form:
   `postgresql+psycopg2://USER:PASSWORD@ep-xxxx.neon.tech/DBNAME?sslmode=require`
3. Assistant runs the migration (from the local machine, one time):
   ```bash
   DATABASE_URL="<neon-url>" python -m tools.migrate_pg
   DATABASE_URL="<neon-url>" python -m warehouse.dq_checks     # expect 11/11
   ```
   → all 1,644 posts + full star warehouse now live on Neon.

## Phase 2 — LLM tier → Sealos freellmapi (already live)
`https://llxegfifaxml.usw-1.sealos.app` is up. Add the 6 provider keys to THAT instance
(same `POST /api/keys` calls used locally) so cloud jobs have the token pool. Grab its
unified key from `GET /api/settings/api-key`.
> Zero-burn alternative: skip freellmapi in the cron and rely on the direct
> `LLM_FALLBACKS=groq,cerebras,gemini,openrouter` chain (keys already GH secrets).

## Phase 3 — Backend jobs → GitHub Actions
1. Add these repo **Secrets** (Settings → Secrets and variables → Actions):
   `DATABASE_URL`, `FREELLM_API_KEY`, `FREELLM_BASE_URL`, `GROQ_API_KEY`,
   `CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`.
2. Actions tab → **axis-pipeline** → *Run workflow* (manual first run).
3. Confirm the run is green + Neon row counts grow. Cron then fires every 12 h.
   - Private repo = 2,000 Actions min/mo; run ~25 min → 12 h cadence fits.
   - Want more frequent? Make the repo public (unlimited) or lower `FETCH_MULT`.

## Phase 4 — Backend API → Koyeb
1. Sign up at **koyeb.com** (GitHub login, no card).
2. Create service → **GitHub** → this repo → **Dockerfile** = `Dockerfile.api`.
3. Env var: `DATABASE_URL` = the Neon URL. (Optional `API_KEY` to gate writes.)
4. Deploy → public `https://<app>.koyeb.app`. Test:
   `bash tools/smoke_api.sh https://<app>.koyeb.app` (all 11 endpoints must return 200).

**Free alternatives (same Dockerfile.api):**
- **Render** (render.com) — deploys unchanged (`$PORT` injected); sleeps after 15 min idle (~30 s wake). Fine here — the dashboard reads Neon directly, not via the API.
- **Hugging Face Spaces** (huggingface.co/spaces, Docker Space) — 16 GB RAM, rarely sleeps; needs the app on port **7860** (`ENV PORT=7860`) and offers no free custom domain.

## Phase 5 — Frontend → Streamlit Community Cloud
1. Sign in at **share.streamlit.io** (GitHub login).
2. New app → this repo → `dashboard/app.py`.
3. Advanced → **Secrets** → paste `DATABASE_URL = "<neon-url>"` (see `secrets.toml.example`).
4. Deploy → always-on public dashboard reading live Neon data.

---

## Who does what
- **Assistant (no account needed):** all code, Dockerfiles, CI, the SQLite→Neon migration, adding freellmapi keys, verification.
- **You (accounts + secret paste only):** create Neon / Koyeb / Streamlit accounts; paste secrets into GitHub + Koyeb + Streamlit (assistant never handles your secrets).

## Free-tier notes
- **Neon** autosuspends when idle → ~0.5 s cold start. Data here is ~2 MB, tons of headroom.
- **Koyeb / Streamlit** free instances stay running (no forced sleep).
- **Sealos** credits burn ~$0.25/day (finite) — use the direct-failover alternative to avoid it.
- **Airflow is retired** in the cloud — GitHub Actions cron is the orchestrator. The local
  Airflow setup still works for dev (see RUNBOOK.md).
