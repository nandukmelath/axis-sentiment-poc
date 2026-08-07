"""Instagram — Axis Bank's public profile (@axisbank), via ScrapeBadger UNIVERSAL WEB SCRAPE
(no native ScrapeBadger Instagram endpoint exists, so we scrape the public profile page +
ai_extract the recent-post list).

HONEST NOTE: Instagram aggressively login-walls its profiles even against anti-bot scrapers —
logged-out profile pages typically render bio/avatar only, with the post grid hidden behind
an in-app/login gate. This fetcher degrades cleanly to [] (never raises) whenever the post
list isn't visible — treat it as a best-effort source, not a reliable one. ConsumerComplaints/
MouthShut/Trustpilot remain the high-yield channels.

Auth: SCRAPEBADGER_API_KEY in .env (shared with every ScrapeBadger-backed fetcher).
"""
import hashlib
import json

import pandas as pd

from config import IG_HANDLE
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import extract_items, has_key, web_scrape

IG_URL = f"https://www.instagram.com/{IG_HANDLE}/"

# Credit thrift: one page, ai_extract, small item cap — never loop-paginate by default.
MAX_ITEMS = 15
MAX_COST = 20  # credit budget for the single scrape call (anti_bot adds +5)

AI_PROMPT = (
    "Return a JSON array of the most recent posts visible on this Instagram profile page. "
    "For each post return an object with exactly these fields: "
    '{"caption": the full post caption text (empty string if none visible), "date": the '
    "post's published date or relative time exactly as shown (e.g. '2026-07-20' or "
    '\'3 weeks ago\'), "likes": the like count as a plain number (0 if none shown), "url": '
    'the direct permalink to the post (e.g. https://www.instagram.com/p/XXXXXXXX/) if one '
    "is visible, else empty string}. Only include actual posts — skip suggested accounts and "
    "the bio. Return an empty array [] if the post grid is not visible (e.g. a login wall)."
)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _iso(s):
    """Best-effort parse to ISO 8601. IG dates from ai_extract are sometimes absolute but
    often relative ("3d", "2 weeks ago") since logged-out pages rarely expose a real
    timestamp; relative/unparseable strings fall through to "" (honest: we don't fabricate
    a timestamp)."""
    if not s:
        return ""
    try:
        ts = pd.to_datetime(str(s), errors="coerce", utc=True)
        if pd.isna(ts):
            return ""
        return ts.isoformat()
    except Exception:
        return ""


def _row(item):
    caption = (item.get("caption") or item.get("text") or "").strip()
    url = item.get("url") or ""
    if not caption and not url:
        return None
    raw_key = url or caption
    sid = "ig:" + hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return dict(
        source_id=sid, source="instagram",
        author=f"@{IG_HANDLE}", author_name="Axis Bank",
        text=caption, url=url or IG_URL,
        created_at=_iso(item.get("date")), lang="en",
        engagement=_int(item.get("likes")),
        raw_json=json.dumps(item, ensure_ascii=False))


def fetch():
    if not has_key():
        print("  [instagram] SCRAPEBADGER_API_KEY not set — skipping (no IG source).")
        return []
    try:
        resp = web_scrape(IG_URL, ai_prompt=AI_PROMPT, fmt="markdown",
                           render_js=True, anti_bot=True, country="in", max_cost=MAX_COST)
    except CreditsExhausted as e:
        print(f"  [instagram] skipped — {e}")
        return []
    if not resp:
        print("  [instagram] no response — skipping.")
        return []
    if resp.get("blocking_detected"):
        print("  [instagram] blocking/login-wall detected — degrading to [] (expected for IG).")
        return []
    items = extract_items(resp, ai_prompt_used=True)
    if not items:
        print("  [instagram] AI extraction empty (login-walled or no posts found) — degrading to [].")
        return []
    rows = {}
    for it in items[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it)
        if r:
            rows[r["source_id"]] = r
    print(f"  [instagram] {len(rows)}")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
