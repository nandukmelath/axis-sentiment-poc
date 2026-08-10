FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# Shared, well-known path for Chromium's binary rather than the per-user default
# (~/.cache/ms-playwright) — the browser is installed as root below, before the
# USER switch, and a per-user path would land it somewhere the runtime user
# (Hugging Face Spaces forces UID 1000; see below) cannot read.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Hugging Face Spaces always runs the container as UID 1000, whatever the image's
# own default user is — this is platform-enforced, not optional. A Dockerfile with
# no USER directive builds and runs fine everywhere else (compose, Render, a bare
# `docker run`), then fails specifically on HF the moment anything tries to WRITE
# — Streamlit's ~/.streamlit cache, or a stealth fetch needing Chromium's own
# runtime files — because those were created as root during the build and UID 1000
# cannot touch them. Matches HF's own documented pattern (useradd -m -u 1000,
# --chown on every COPY, USER + HOME/PATH after installs) exactly, which is also
# just correct Docker hygiene everywhere else this image runs.
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# requirements.txt installs the playwright PYTHON PACKAGE; the browser binary is a
# separate download this step was missing. Without it, every STEALTH_SOURCES=1
# fetcher (trustpilot_web, gmaps_web, mouthshut_web — fetch/scrapling_stealth.py,
# the Cloudflare-solving path for real customer reviews) would crash on first use
# in the container with "Executable doesn't exist", even though `import playwright`
# succeeds and every local test looks fine. --with-deps pulls the OS libraries
# Chromium needs that python:3.12-slim does not ship (libnss3, libatk, etc) — and
# needs root, which is why this still runs before the USER switch below.
RUN python -m playwright install --with-deps chromium && \
    chmod -R o+rX /ms-playwright

COPY --chown=user . .

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

EXPOSE 8501

# Shell form on purpose, so ${PORT} actually expands. Every managed host assigns
# the port through an env var and health-checks THAT port — Render, Hugging Face
# Spaces, Cloud Run, Railway. Exec form hardcoded 8501, which binds the wrong port
# everywhere except compose and fails the health check with no useful error.
# Falls back to 8501 so local `docker run` is unchanged.
#
# Default command is the dashboard; compose and the Render blueprint override it
# for the scheduler and stream services.
CMD streamlit run dashboard/app.py \
      --server.address 0.0.0.0 \
      --server.port ${PORT:-8501} \
      --server.headless true \
      --browser.gatherUsageStats false
