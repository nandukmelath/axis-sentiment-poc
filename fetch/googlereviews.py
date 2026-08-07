"""Google Maps/Business reviews — Axis Bank branches, via ScrapeBadger universal web-scrape.
No native API for this site, and Google is heavily anti-bot protected even for paid scrapers
(login walls, consent interstitials, dynamic map SPA) — this is a BEST-EFFORT source. Scrape a
Google Maps search results page with render_js + anti_bot + ai_extract, asking for a JSON array
of reviews. Expect [] on a meaningful fraction of runs; that is the honest, correct outcome for
this source, not a bug.

Auth: SCRAPEBADGER_API_KEY in .env (shared with every fetch/scrapebadger* module).

Run:
  python -m fetch.googlereviews
"""
import hashlib
import json
import re

from dateutil import parser as dateparser

from config import GOOGLE_REVIEWS_URL, GOOGLE_REVIEWS_MAX_ITEMS
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import web_scrape, extract_items, has_key

AI_PROMPT = (
    "This is a Google Maps search-results page for 'Axis Bank' branches, showing one or more "
    "business listings with customer reviews. Extract every distinct review visible on the page "
    "(across all listed branches) as a JSON array. Each element must be an object with EXACTLY "
    "these keys: author (the reviewer's display name, empty string if not shown), text (the full "
    "review text shown, empty string if only a rating with no text), rating (the star rating "
    "given, as a plain number 1-5, empty string if not shown), date (the review date/age exactly "
    "as written on the page, e.g. '2 months ago' or an absolute date). Return ONLY the JSON "
    "array, no prose, no markdown fences, no trailing commentary. If nothing relevant is found "
    "(e.g. the page is login-walled or reviews are not visible), return []."
)


def _iso(s):
    """Best-effort parse of whatever date string the page/AI-extraction returns -> ISO 8601.
    Never raises: unparseable/relative strings ('2 months ago') are returned as-is (created_at
    sort just degrades for that one row instead of the whole fetch failing)."""
    if not s:
        return ""
    s = str(s).strip()
    try:
        return dateparser.parse(s, fuzzy=True).isoformat()
    except (ValueError, OverflowError, TypeError):
        return s


def _num(v):
    """Coerce a rating value ('4', '4/5', '4.5 stars', '') to a number, else 0."""
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"[\d.]+", str(v))
    if not m:
        return 0
    try:
        n = float(m.group())
        return int(n) if n.is_integer() else n
    except ValueError:
        return 0


def _hash_id(*parts):
    key = "|".join(p for p in parts if p)
    return "greview:" + hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _row(item):
    author = str(item.get("author") or "").strip() or "anonymous"
    body = str(item.get("text") or "").strip()
    rating = _num(item.get("rating"))
    if not (body or rating):
        return None
    text = f"[{rating}★] {body}".strip() if rating else body
    sid = _hash_id(author, body, str(rating))
    return dict(
        source_id=sid, source="googlereviews",
        author=author, author_name=author,
        text=text, url=GOOGLE_REVIEWS_URL, created_at=_iso(item.get("date", "")),
        lang="en", engagement=rating,
        raw_json=json.dumps(item, ensure_ascii=False))


def fetch():
    """Scrape one page of Google Maps reviews for Axis Bank branches. Degrades to [] on any
    failure — never raises (login walls / anti-bot / missing key / credits-exhausted all just
    skip cleanly). Google is the most heavily protected source in this pipeline; an empty result
    here is expected and not a sign of a broken fetcher."""
    if not has_key():
        print("  [googlereviews] SCRAPEBADGER_API_KEY not set — skipping (no scraper key).")
        return []

    try:
        resp = web_scrape(GOOGLE_REVIEWS_URL, ai_prompt=AI_PROMPT, fmt="markdown",
                           render_js=True, anti_bot=True, country="in")
    except CreditsExhausted as e:
        print(f"  [googlereviews] skipped — {e}")
        return []

    if not resp:
        print("  [googlereviews] scrape returned nothing (network/API error) — degrading to [].")
        return []
    if resp.get("success") is False:
        print(f"  [googlereviews] scrape unsuccessful "
              f"(status={resp.get('status_code', 'n/a')}) — degrading to [].")
        return []
    if resp.get("blocking_detected"):
        print("  [googlereviews] anti-bot blocking detected by ScrapeBadger — "
              "Google is heavily protected; results (if any) may be partial.")

    items = extract_items(resp, AI_PROMPT)
    if not items:
        print("  [googlereviews] no AI-extracted reviews this run "
              "(login-walled, layout changed, or blocked — expected for Google) — degrading to [].")
        return []

    rows = {}
    for it in items[:GOOGLE_REVIEWS_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it)
        if not r:
            continue
        # NOTE: unlike consumercomplaints/mouthshut, individual review text rarely mentions
        # "Axis" by name (it's a branch review, not a product post) — the page itself is already
        # scoped to the "Axis Bank" Maps search, so no brand_match filter is applied here.
        rows[r["source_id"]] = r

    print(f"  [googlereviews] {len(rows)}")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
