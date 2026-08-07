"""Trustpilot — Axis Bank customer reviews via ScrapeBadger universal web-scrape.
No native ScrapeBadger endpoint for Trustpilot; scrape the company review page with
render_js + ai_extract, asking for a JSON array of reviews (title/body/author/rating/date/url).
Axis Bank's Trustpilot slug isn't 100% certain, so we try a couple of likely domain slugs in
order and use the first one that actually yields reviews.

Auth: SCRAPEBADGER_API_KEY in .env (shared with every fetch/scrapebadger* module).

Run:
  python -m fetch.trustpilot
"""
import hashlib
import json
import re

from dateutil import parser as dateparser

from config import TRUSTPILOT_URLS, TRUSTPILOT_MAX_ITEMS
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import web_scrape, extract_items, has_key
from fetch.webutil import brand_match

AI_PROMPT = (
    "This is a company review page for 'Axis Bank' on trustpilot.com. Extract every distinct "
    "customer review visible on the page as a JSON array. Each element must be an object with "
    "EXACTLY these keys: title (the review headline, empty string if not shown), body (the full "
    "review text shown on the page, as much as is visible), author (the reviewer's display name "
    "or username, empty string if not shown), rating (the star rating given, as a plain number "
    "e.g. 1-5, empty string if not shown), date (the review date exactly as written on the page, "
    "e.g. 'Reviewed on <date>' or a relative string like '3 days ago' — extract just the date "
    "part), url (the absolute permalink to the individual review if one is shown, else the page "
    "URL itself). Return ONLY the JSON array, no prose, no markdown fences, no trailing "
    "commentary. If nothing relevant is found or the page shows no reviews, return []."
)


def _iso(s):
    """Best-effort parse of whatever date string the page/AI-extraction returns -> ISO 8601.
    Never raises: unparseable strings (including relative dates like '3 days ago') are
    returned as-is (created_at sort just degrades for that one row instead of the whole fetch
    failing)."""
    if not s:
        return ""
    s = str(s).strip()
    try:
        return dateparser.parse(s, fuzzy=True).isoformat()
    except (ValueError, OverflowError, TypeError):
        return s


def _num(v):
    """Coerce a rating value ('4', '4/5', '4 out of 5 stars', '') to a number, else 0."""
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
    return "trustpilot:" + hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _row(item, page_url):
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    if not (title or body):
        return None
    rating = _num(item.get("rating"))
    text = f"[{rating}] {title} {body}".strip() if rating else f"{title} {body}".strip()
    url = str(item.get("url") or "").strip() or page_url
    author = str(item.get("author") or "").strip() or "anonymous"
    sid = _hash_id(url, title) if url else _hash_id(title, body)
    return dict(
        source_id=sid, source="trustpilot",
        author=author, author_name=author,
        text=text, url=url, created_at=_iso(item.get("date", "")),
        lang="en", engagement=rating,
        raw_json=json.dumps(item, ensure_ascii=False))


def _scrape_one(url):
    """Scrape a single candidate Trustpilot URL. Returns (rows_dict, hit_credits_exhausted)."""
    try:
        resp = web_scrape(url, ai_prompt=AI_PROMPT, fmt="markdown", render_js=True,
                          anti_bot=False, country="in")
    except CreditsExhausted as e:
        print(f"  [trustpilot] skipped — {e}")
        return {}, True

    if not resp:
        print(f"  [trustpilot] {url}: scrape returned nothing (network/API error).")
        return {}, False
    if resp.get("success") is False:
        print(f"  [trustpilot] {url}: scrape unsuccessful "
              f"(status={resp.get('status_code', 'n/a')}).")
        return {}, False
    if resp.get("blocking_detected"):
        print(f"  [trustpilot] {url}: anti-bot blocking detected by ScrapeBadger — "
              "results (if any) may be partial.")

    items = extract_items(resp, AI_PROMPT)
    if not items:
        print(f"  [trustpilot] {url}: no AI-extracted reviews (page empty, wrong slug, or "
              "layout changed).")
        return {}, False

    rows = {}
    for it in items[:TRUSTPILOT_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it, url)
        if not r:
            continue
        # ai_extraction is scoped to the Axis Bank page, but keep the brand check for safety
        # in case Trustpilot bleeds in unrelated/suggested-company rows.
        if not brand_match(r["text"]):
            continue
        rows[r["source_id"]] = r
    return rows, False


def fetch():
    """Scrape one page of Axis Bank Trustpilot reviews. Tries each configured URL in order,
    stopping at the first that yields rows. Degrades to [] on any failure — never raises
    (login walls / anti-bot / missing key / credits-exhausted / wrong slug all just skip
    cleanly)."""
    if not has_key():
        print("  [trustpilot] SCRAPEBADGER_API_KEY not set — skipping (no scraper key).")
        return []

    for url in TRUSTPILOT_URLS:
        rows, exhausted = _scrape_one(url)
        if exhausted:
            return []
        if rows:
            print(f"  [trustpilot] {len(rows)} (via {url})")
            return list(rows.values())

    print("  [trustpilot] 0 — no candidate URL yielded reviews (Axis Bank may not have a "
          "Trustpilot profile, or the page is login/JS-walled).")
    return []


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
