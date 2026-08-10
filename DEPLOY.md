# Deploy

Three supported paths.

| | **A — Free stack** | **B — Render (managed)** | **C — one VM** |
|---|---|---|---|
| Cost | **$0** | ~$21/mo | €3.79–$12/mo |
| Pipeline compute | GitHub Actions, **unmetered** | worker | container |
| Database | Neon free, 0.5 GB | managed PG | container |
| Dashboard | Streamlit Cloud | web service | container |
| You administer | nothing | nothing | OS, patches, backups |
| Consoles to check | 3 | 1 | 1 |
| Real ceiling | Neon 100 CU-hours/mo | none | RAM/disk |

**Take A.** GitHub-hosted standard runners are free and *unmetered on public
repositories* — and this repo is public, so the pipeline has no practical limit:
12 runs a day, 50-minute budget each, no minute cap and no bill. That is the
closest thing to "unlimited 24/7" that exists for free.

The honest cost of A is three consoles instead of one. Everything in it is
already written — `.github/workflows/pipeline.yml` runs the cycle, `db.py` speaks
Postgres, and `requirements.txt` is already tuned for Streamlit Cloud.

---

# Path A — free, unmetered

## 1. Pipeline (GitHub Actions)

`.github/workflows/pipeline.yml` already runs the 2-hourly cycle. It needs repo
secrets under **Settings → Secrets and variables → Actions**:

| Secret | Why |
|---|---|
| `DATABASE_URL` | the Neon connection string from step 2 |
| `GROQ_API_KEY` | else everything falls back to lexicon-only scoring |
| `CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | optional failover |

Pushing the workflow needs the `workflow` OAuth scope:

```bash
gh auth refresh -h github.com -s workflow
```

Actions cron is best-effort and can fire 10–30 minutes late. That is fine here:
the engagement refresh schedules on **elapsed time**, not on the run being
punctual, so a late run self-corrects rather than skipping a post's slot.

## 2. Database (Neon)

Create a free project, copy the pooled connection string into `DATABASE_URL` (as
an Actions secret and a Streamlit secret), then move the corpus:

```bash
DATABASE_URL='postgresql+psycopg2://...neon.tech/...?sslmode=require' python migrate_to_pg.py
```

Run `python -m tools.fix_dates` **first** or the RFC-822 date corruption migrates
with the data.

**The one real ceiling in this stack:** Neon free gives 0.5 GB storage and
**100 compute-hours/month**. Storage is a non-issue — the corpus is 16 MB, 3% of
the allowance. Compute is the thing to watch: Neon autosuspends after 5 minutes
idle, and the dashboard's live fragments are cached at 30-minute TTL so they do
not hold it awake. Normal demo use lands comfortably inside 100 CU-hours; leaving
the dashboard open all day for weeks would not. If you exceed it, that is the
signal to move to Path B or C.

## 3. Dashboard (Streamlit Community Cloud)

Point it at this repo, `dashboard/app.py`, and set two secrets:
`DATABASE_URL` and `AXIS_DASH_PASSWORD`.

Free apps sleep after 12 hours without traffic and wake on the next visit — fine
for a demo, wrong for a wall-mounted war-room screen. 1 GB RAM, unlimited public
apps.

---

# Path B — Render Blueprint

`render.yaml` in the repo root defines all three services. Render reads it and
provisions managed Postgres, a background worker running the 2h cycle, and the
dashboard.

1. Push the repo to GitHub.
2. Render → **New → Blueprint** → pick the repo.
3. Set the secrets it prompts for:
   - `AXIS_DASH_PASSWORD` — **mandatory.** The dashboard is on a public URL and
     shows complaints and masked PII.
   - `GROQ_API_KEY` — else everything falls back to lexicon-only scoring.
   - `CEREBRAS_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` — optional failover.
4. Deploy. `DATABASE_URL` is injected from the managed database; no connection
   string is ever typed or committed.

**Costs, honestly.** `starter` × 2 services + `basic-256mb` Postgres ≈ **$21/mo**.
The free Postgres tier expires after 90 days and free web services sleep — a
sleeping war-room dashboard is not a dashboard, so neither is used here.

**Migrating the existing corpus:** copy `axis.db` up and run
`python migrate_to_pg.py` from the Render shell with `DATABASE_URL` set, or start
empty and let the pipeline build history.

**The scheduler is a worker, not a cron job.** `run_cycle` owns its own 2h loop,
so there is no external schedule that can silently stop firing.

---

# Path C — one VM

Postgres, the pipeline and the dashboard as three containers on one Docker
network. One `docker compose up`, one `docker compose logs`.

## 1. Get a machine

Any Linux VM with **2 GB RAM and 20 GB disk**. The corpus is 16 MB; the RAM is for
Postgres plus the Python workers.

| Host | Spec | Cost | Note |
|---|---|---|---|
| **DigitalOcean, Bangalore (BLR1)** | 1 vCPU / 2 GB | **$12/mo** | India-hosted — worth saying out loud in a bank pitch |
| DigitalOcean, any region | 1 vCPU / 2 GB | $12/mo | |
| Hetzner CX22 (DE/FI/US) | 2 vCPU / 4 GB | **~€3.79/mo** | cheapest reliable option |
| Oracle Cloud Always Free | 2 ARM / 12 GB | $0 | halved from 4/24 in June 2026; provisioning often fails with "out of host capacity" in busy regions — do not build a demo deadline on it |

If the deadline matters more than €4, take Hetzner. If the audience is Axis, take
Bangalore and say the data never leaves India.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Clone and configure

```bash
git clone https://github.com/nandukmelath/axis-sentiment-poc.git
cd axis-sentiment-poc
cp .env.docker.example .env
```

Edit `.env`. Three values are mandatory:

- `POSTGRES_PASSWORD` — `openssl rand -hex 24`
- `AXIS_DASH_PASSWORD` — **leave this set.** Without it the dashboard, which shows
  customer complaints and masked PII, is open to anyone who finds the IP.
- `GROQ_API_KEY` — otherwise every post falls back to lexicon-only scoring

## 4. Start

```bash
docker compose up -d --build
```

Dashboard on `http://<server-ip>:8501`. Schema is created on first boot.

## 5. Move the existing corpus (optional)

To carry the 3,097 scored posts across instead of starting empty, copy `axis.db`
to the server and:

```bash
docker compose run --rm scheduler python migrate_to_pg.py
```

Fresh installs can skip this — the pipeline backfills on its own, it just takes a
few cycles to build history.

---

## Running it

```bash
docker compose logs -f scheduler     # what the pipeline is doing
docker compose ps                    # what is up
docker compose restart scheduler     # after an .env change
docker compose down && docker compose up -d --build   # after a git pull
```

Every service is `restart: unless-stopped`, so crashes and host reboots recover
without a human.

## Backups

Postgres lives in the `pgdata` volume. Nothing backs it up automatically:

```bash
docker compose exec db pg_dump -U axis axis | gzip > axis-$(date +%F).sql.gz
```

Worth a weekly cron on the host.

## The one thing that will break

**Nitter instances rot.** X discovery depends on volunteer instances, and of 23
probed on 2026-08-10 only two served results. When X ingestion goes quiet:

```bash
docker compose exec scheduler python -m fetch.twitter_live --probe
```

Put the working hosts in `TWITTER_NITTER_INSTANCES` and
`docker compose restart scheduler`. Per-tweet hydration (metrics, text, author)
uses X's own endpoints and is unaffected — only *discovery* depends on Nitter.

**Also worth knowing:** datacenter IPs get blocked more aggressively than home
connections. If Nitter or the X guest token misbehaves from the server but works
locally, that is why — the fix is an outbound proxy on the `scheduler` service,
not a code change.

## Hardening before anyone else sees it

- `ufw allow 22 && ufw allow 8501 && ufw enable` — Postgres is not published to the
  host, so it stays on the internal Docker network
- Put Caddy or nginx in front for HTTPS if this gets a domain
- Do not commit `.env`
