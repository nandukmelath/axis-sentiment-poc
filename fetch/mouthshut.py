"""MouthShut — Axis Bank customer reviews via ScrapeBadger universal web-scrape.
No native API for this site; it's Cloudflare-walled for plain requests (confirmed, see README),
so scrape the reviews listing page with render_js + ai_extract, asking for a JSON array of
reviews (title/body/author/rating/date/url + helpful count where shown).

Auth: SCRAPEBADGER_API_KEY in .env (shared with every fetch/scrapebadger* module).

Run:
  python -m fetch.mouthshut
"""
import hashlib
import json
import re

from dateutil import parser as dateparser

from config import MOUTHSHUT_URL, MOUTHSHUT_MAX_ITEMS
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import web_scrape, extract_items, has_key

AI_PROMPT = (
    "This is a customer-reviews listing page for 'Axis Bank' on mouthshut.com. Extract every "
    "distinct review visible on the page as a JSON array. Each element must be an object with "
    "EXACTLY these keys: title (the review headline), body (the full review text shown on the "
    "listing, as much as is visible), author (the reviewer's display name or username, empty "
    "string if not shown), rating (the star/numeric rating given, as a plain number e.g. 3 or "
    "3.5, empty string if not shown), date (the review date exactly as written on the page), "
    "url (the absolute link to the individual review — resolve relative links against "
    "https://www.mouthshut.com, empty string if not shown), helpful (the 'X found this review "
    "helpful' count, as a plain number, empty string if not shown). Return ONLY the JSON array, "
    "no prose, no markdown fences, no trailing commentary. If nothing relevant is found, return []."
)


def _iso(s):
    """Best-effort parse of whatever date string the page/AI-extraction returns -> ISO 8601.
    Never raises: unparseable strings (including relative dates like '3 months ago') are
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
    """Coerce a rating/helpful-count value ('4', '4/5', '4.5 stars', '') to a number, else 0."""
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
    return "mouthshut:" + hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _row(item):
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    if not (title or body):
        return None
    rating = _num(item.get("rating"))
    helpful = _num(item.get("helpful"))
    text = f"[{rating}] {title} {body}".strip()
    url = str(item.get("url") or "").strip()
    author = str(item.get("author") or "").strip() or "anonymous"
    sid = _hash_id(url) if url else _hash_id(title, body)
    engagement = helpful if helpful else rating   # prefer "found helpful" votes; fall back to rating
    return dict(
        source_id=sid, source="mouthshut",
        author=author, author_name=author,
        text=text, url=url or MOUTHSHUT_URL, created_at=_iso(item.get("date", "")),
        lang="en", engagement=engagement,
        raw_json=json.dumps(item, ensure_ascii=False))


def fetch():
    """Scrape one page of Axis Bank reviews. Degrades to [] on any failure — never raises
    (login walls / anti-bot / missing key / credits-exhausted all just skip cleanly)."""
    if not has_key():
        print("  [mouthshut] SCRAPEBADGER_API_KEY not set — skipping (no scraper key).")
        return []

    try:
        resp = web_scrape(MOUTHSHUT_URL, ai_prompt=AI_PROMPT, fmt="markdown", render_js=True,
                          anti_bot=False, country="in")
    except CreditsExhausted as e:
        print(f"  [mouthshut] skipped — {e}")
        return []

    if not resp:
        print("  [mouthshut] scrape returned nothing (network/API error) — degrading to [].")
        return []
    if resp.get("success") is False:
        print(f"  [mouthshut] scrape unsuccessful "
              f"(status={resp.get('status_code', 'n/a')}) — degrading to [].")
        return []
    if resp.get("blocking_detected"):
        print("  [mouthshut] anti-bot blocking detected by ScrapeBadger — "
              "results (if any) may be partial.")

    items = extract_items(resp, AI_PROMPT)
    if not items:
        print("  [mouthshut] no AI-extracted reviews this run "
              "(page empty, layout changed, or blocked) — degrading to [].")
        return []

    rows = {}
    for it in items[:MOUTHSHUT_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it)
        if not r:
            continue
        # No brand_match filter here (unlike consumercomplaints.in's generic search page):
        # MOUTHSHUT_URL is a dedicated axis-bank-reviews-* product page, so every row on it
        # is already an Axis Bank review even if a given review's text never repeats "Axis".
        rows[r["source_id"]] = r

    print(f"  [mouthshut] {len(rows)}")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
