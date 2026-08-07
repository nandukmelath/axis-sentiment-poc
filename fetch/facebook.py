"""Facebook — Axis Bank's public Page, via ScrapeBadger UNIVERSAL WEB SCRAPE (no native
ScrapeBadger Facebook endpoint exists, so we scrape the public page + ai_extract the post list).

HONEST NOTE: Facebook aggressively login-walls its public pages even against anti-bot scrapers.
This fetcher degrades cleanly to [] (never raises) when the feed isn't visible — treat it as a
best-effort source, not a reliable one. ConsumerComplaints/MouthShut/Trustpilot remain the
high-yield channels.

Auth: SCRAPEBADGER_API_KEY in .env (shared with every ScrapeBadger-backed fetcher).
"""
import hashlib
import json

import pandas as pd

from config import FB_PAGE
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import web_scrape, has_key, extract_items

FB_URL = f"https://www.facebook.com/{FB_PAGE}/"

# Credit thrift: one page, ai_extract, small item cap — never loop-paginate by default.
MAX_ITEMS = 15
MAX_COST = 20  # credit budget for the single scrape call (anti_bot adds +5)

AI_PROMPT = (
    "Return a JSON array of the most recent posts visible on this Facebook Page's public feed. "
    "For each post return an object with exactly these fields: "
    '{"text": the full post caption/body text, "date": the post\'s published date/timestamp as '
    'shown on the page, "likes": the reaction/like count as a plain number (0 if none shown), '
    '"url": the direct permalink to the post if one is visible, else empty string}. '
    "Only include actual page posts — skip ads, suggested pages, and comments. "
    "Return an empty array [] if the feed is not visible (e.g. a login wall)."
)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _iso(s):
    """Best-effort parse to ISO 8601. FB dates from ai_extract are often absolute
    ("July 28 at 10:32 AM") but sometimes relative ("3d") — pandas handles the former;
    the latter falls through to "" (honest: we don't fabricate a timestamp)."""
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
    text = (item.get("text") or "").strip()
    if not text:
        return None
    url = item.get("url") or ""
    raw_key = url or text
    sid = "fb:" + hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return dict(
        source_id=sid, source="facebook",
        author=f"@{FB_PAGE}", author_name="Axis Bank",
        text=text, url=url or FB_URL,
        created_at=_iso(item.get("date")), lang="en",
        engagement=_int(item.get("likes")),
        raw_json=json.dumps(item, ensure_ascii=False))


def fetch():
    if not has_key():
        print("  [facebook] SCRAPEBADGER_API_KEY not set — skipping (no FB source).")
        return []
    try:
        resp = web_scrape(FB_URL, ai_prompt=AI_PROMPT, fmt="markdown",
                           render_js=True, anti_bot=True, country="in", max_cost=MAX_COST)
    except CreditsExhausted as e:
        print(f"  [facebook] skipped — {e}")
        return []
    if not resp:
        print("  [facebook] no response — skipping.")
        return []
    if resp.get("blocking_detected"):
        print("  [facebook] blocking/login-wall detected — degrading to [] (expected for FB).")
        return []
    items = extract_items(resp, ai_prompt_used=True)
    if not items:
        print("  [facebook] AI extraction empty (login-walled or no posts found) — degrading to [].")
        return []
    rows = {}
    for it in items[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it)
        if r:
            rows[r["source_id"]] = r
    print(f"  [facebook] {len(rows)}")
    return list(rows.values())
