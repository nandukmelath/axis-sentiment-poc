"""Sync the cron's secrets from local .env -> GitHub Actions repo secrets.

The pipeline's secrets live in THREE places (see RUNBOOK "Secrets"):
  1. .env                       — local dev + this script's source of truth
  2. GitHub Actions repo secrets — the 12h cron (this script pushes here)
  3. Streamlit Cloud app secrets — the dashboard's DATABASE_URL (set in the app UI; not here)

Only pushes keys the cron actually reads; never prints secret VALUES. Requires `gh` authed.

Run:  python -m tools.sync_secrets            # push
      python -m tools.sync_secrets --dry-run  # show which keys would sync
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path

REPO = os.getenv("AXIS_REPO", "nandukmelath/axis-sentiment-poc")
# exactly the keys .github/workflows/pipeline.yml references
CRON_KEYS = ["DATABASE_URL", "GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY",
             "OPENROUTER_API_KEY", "FREELLM_API_KEY", "FREELLM_BASE_URL"]


def _load_env():
    env = {}
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        sys.exit(".env not found next to the project root")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    dry = "--dry-run" in sys.argv
    if not shutil.which("gh"):
        sys.exit("gh CLI not found — install + `gh auth login` first.")
    env = _load_env()
    present = [k for k in CRON_KEYS if env.get(k)]
    missing = [k for k in CRON_KEYS if not env.get(k)]
    print(f"repo: {REPO}")
    print(f"will sync ({len(present)}): {', '.join(present) or '(none)'}")
    if missing:
        print(f"skipped (unset in .env): {', '.join(missing)}")
    if dry:
        print("dry-run — nothing pushed.")
        return
    for k in present:
        r = subprocess.run(["gh", "secret", "set", k, "--repo", REPO, "--body", env[k]],
                           capture_output=True, text=True)  # nosec B603/B607 — fixed argv, value from local .env
        print(f"  {'OK ' if r.returncode == 0 else 'ERR'} {k}"
              + ("" if r.returncode == 0 else f" — {r.stderr.strip()[:80]}"))
    print("done. NOTE: the Streamlit dashboard's DATABASE_URL is set separately in the app's "
          "Settings -> Secrets (it is NOT synced by this script).")


if __name__ == "__main__":
    main()
