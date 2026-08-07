"""ConsumerComplaints.in — Axis Bank complaint threads via ScrapeBadger universal web-scrape.
No native API for this site; scrape the Axis Bank search/listing page with render_js + ai_extract,
asking for a JSON array of complaint threads. HIGH-VALUE vein: these are intent-rich grievances
(disputes, fraud reports, service failures) rather than casual chatter.

Auth: SCRAPEBADGER_API_KEY in .env (shared with every fetch/scrapebadger* module).

Run:
  python -m fetch.consumercomplaints
"""
import hashlib
import json

from dateutil import parser as dateparser

from config import CC_URL, CC_MAX_ITEMS
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import web_scrape, extract_items, has_key
from fetch.webutil import brand_match

AI_PROMPT = (
    "This is a consumer-complaints listing/search-results page for 'Axis Bank' on "
    "consumercomplaints.in. Extract every distinct complaint thread visible on the page as a "
    "JSON array. Each element must be an object with EXACTLY these keys: "
    "title (the complaint headline), body (the complaint description/summary text shown on the "
    "listing, as much as is visible), author (the complainant's display name or username, empty "
    "string if not shown), date (the posted/reported date exactly as written on the page), "
    "url (the absolute link to the full complaint thread — resolve relative links against "
    "https://www.consumercomplaints.in). Return ONLY the JSON array, no prose, no markdown "
    "fences, no trailing commentary. If nothing relevant is found, return []."
)


def _iso(s):
    """Best-effort parse of whatever date string the page/AI-extraction returns -> ISO 8601.
    Never raises: unparseable strings are returned as-is (created_at sort just degrades for
    that one row instead of the whole fetch failing)."""
    if not s:
        return ""
    s = str(s).strip()
    try:
        return dateparser.parse(s, fuzzy=True).isoformat()
    except (ValueError, OverflowError, TypeError):
        return s


def _hash_id(*parts):
    key = "|".join(p for p in parts if p)
    return "ccomplaints:" + hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _row(item):
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    text = f"{title}\n{body}".strip()
    if not text:
        return None
    url = str(item.get("url") or "").strip()
    author = str(item.get("author") or "").strip() or "anonymous"
    sid = _hash_id(url) if url else _hash_id(title, body)
    return dict(
        source_id=sid, source="consumercomplaints",
        author=author, author_name=author,
        text=text, url=url, created_at=_iso(item.get("date", "")),
        lang="en", engagement=0,
        raw_json=json.dumps(item, ensure_ascii=False))


def fetch():
    """Scrape one page of Axis Bank complaint threads. Degrades to [] on any failure — never
    raises (login walls / anti-bot / missing key / credits-exhausted all just skip cleanly)."""
    if not has_key():
        print("  [consumercomplaints] SCRAPEBADGER_API_KEY not set — skipping (no scraper key).")
        return []

    try:
        resp = web_scrape(CC_URL, ai_prompt=AI_PROMPT, fmt="markdown", render_js=True,
                          anti_bot=False, country="in")
    except CreditsExhausted as e:
        print(f"  [consumercomplaints] skipped — {e}")
        return []

    if not resp:
        print("  [consumercomplaints] scrape returned nothing (network/API error) — degrading to [].")
        return []
    if resp.get("success") is False:
        print(f"  [consumercomplaints] scrape unsuccessful "
              f"(status={resp.get('status_code', 'n/a')}) — degrading to [].")
        return []
    if resp.get("blocking_detected"):
        print("  [consumercomplaints] anti-bot blocking detected by ScrapeBadger — "
              "results (if any) may be partial.")

    items = extract_items(resp, AI_PROMPT)
    if not items:
        print("  [consumercomplaints] no AI-extracted complaints this run "
              "(page empty, layout changed, or blocked) — degrading to [].")
        return []

    rows = {}
    for it in items[:CC_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        r = _row(it)
        if not r:
            continue
        # AI extraction is already scoped to the Axis Bank search page, but the site sometimes
        # bleeds in unrelated/"related company" rows — keep only ones that actually mention Axis.
        if not brand_match(r["text"]):
            continue
        rows[r["source_id"]] = r

    print(f"  [consumercomplaints] {len(rows)}")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
