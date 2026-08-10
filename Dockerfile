FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# requirements.txt installs the playwright PYTHON PACKAGE; the browser binary is a
# separate download this step was missing. Without it, every STEALTH_SOURCES=1
# fetcher (trustpilot_web, gmaps_web, mouthshut_web — fetch/scrapling_stealth.py,
# the Cloudflare-solving path for real customer reviews) would crash on first use
# in the container with "Executable doesn't exist", even though `import playwright`
# succeeds and every local test looks fine. --with-deps pulls the OS libraries
# Chromium needs that python:3.12-slim does not ship (libnss3, libatk, etc).
RUN python -m playwright install --with-deps chromium

COPY . .

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
