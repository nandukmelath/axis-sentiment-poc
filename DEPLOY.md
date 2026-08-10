# Deploy — one box, one command

The whole system runs on a single machine: Postgres, the 2-hourly pipeline, and
the dashboard, as three containers on one Docker network. No managed database, no
external scheduler, no CI cron. One `docker compose up`, one `docker compose logs`
when something breaks.

That is a deliberate trade. Spreading this across GitHub Actions + Neon +
Streamlit Cloud costs nothing but gives you three services that can fail
independently, three consoles to check, three sets of credentials, and three
different IP ranges for the scrapers to get blocked from. On one box there is one
of each.

---

## 1. Get a machine

Any Linux VM with **2 GB RAM and 20 GB disk**. The corpus is 16 MB; the RAM is for
Postgres plus the Python workers.

| Host | Spec | Cost | Note |
|---|---|---|---|
| **DigitalOcean, Bangalore (BLR1)** | 1 vCPU / 2 GB | **$12/mo** | India-hosted — worth saying out loud in a bank pitch |
| DigitalOcean, any region | 1 vCPU / 2 GB | $12/mo | |
| Hetzner CX22 (DE/FI/US) | 2 vCPU / 4 GB | **~€3.79/mo** | cheapest reliable option |
| Oracle Cloud Always Free | 4 ARM / 24 GB | $0 | genuinely free, but provisioning is unreliable and idle instances get reclaimed — do not build a demo on it |

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
