---
title: Axis Bank Social Sentiment
emoji: 🏦
colorFrom: purple
colorTo: gray
sdk: docker
app_port: 8501
pinned: false
short_description: Live social-sentiment war room for Axis Bank
---

# Axis Bank — Social Sentiment

Streamlit dashboard over a Supabase Postgres warehouse. The ingestion pipeline
runs separately on GitHub Actions every 2 hours; this Space is read-only against
the same database.

## This file is the Space card, not the project README

Hugging Face reads the YAML front-matter above to configure the Space. It has to
live at the ROOT of the Space repo, which is why it is kept here rather than
merged into the project README — that one would overwrite it.

`app_port: 8501` matches the Dockerfile's default. The Dockerfile binds
`${PORT:-8501}`, so it works whether or not the platform injects `PORT`.

## Deploying

1. Create a Space: **New Space → Docker → Blank**.
2. Push this repo to it, with THIS file as the root `README.md`:

   ```bash
   git clone https://huggingface.co/spaces/<user>/<space> hf-space
   cd hf-space
   # copy the project in, then overwrite the README with the Space card
   rsync -a --exclude .git --exclude axis.db --exclude .env /path/to/axis-sentiment-poc/ .
   cp deploy/huggingface/README.md README.md
   git add -A && git commit -m "deploy dashboard" && git push
   ```

3. **Settings → Variables and secrets**, add as *secrets*:

   | Secret | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase pooled connection string |
   | `AXIS_DASH_PASSWORD` | any strong string — the Space URL is public |

`AXIS_DASH_PASSWORD` is not optional here. A Space is world-readable by default
and this dashboard shows customer complaints and masked PII. Set it, or set the
Space itself to private.

## Sleep behaviour

Free CPU-basic hardware (2 vCPU, 16 GB RAM) pauses after **48 hours** of no
traffic and resumes on the next visit. That is four times Streamlit Community
Cloud's 12-hour window, and any regular use keeps it up. It is still a pause, so
for a permanently-mounted screen use the paid tier or Railway instead.

Nothing is stored in the Space — the disk is ephemeral and all state lives in
Supabase, so a pause loses nothing.
