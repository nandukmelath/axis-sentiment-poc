"""LinkedIn — Axis Bank company page + its recent posts/updates, via ScrapeBadger NATIVE
endpoints (paid). Falls back to the universal web-scrape (ai_extract) of the public posts
URL when the native posts path isn't available for this account. LinkedIn public content
(company pages, posts) is frequently login-walled even for scrapers — every failure mode
here degrades to [] cleanly, never raises.

Native:  GET /v1/linkedin/companies/{slug}?country=in            (company profile — used
         only to confirm reachability / get a display name; not emitted as its own row)
         GET /v1/linkedin/companies/{slug}/posts?country=in       (best-guess posts path;
         we also try a couple of sibling paths since the exact posts/updates endpoint name
         isn't pinned down in our ScrapeBadger notes — first one returning a usable list wins)
Fallback: POST /v1/web/scrape on LINKEDIN_POSTS_URL, ai_extract=True, anti_bot=True
         (LinkedIn needs anti-bot), asking for a JSON array of {author, text, date, url, likes}.

Auth: SCRAPEBADGER_API_KEY in .env.

Run:  python -m fetch.linkedin
"""
import hashlib
import json

import pandas as pd

from config import LINKEDIN_COMPANY, LINKEDIN_MAX_POSTS, LINKEDIN_POSTS_URL
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import extract_items, has_key, sb_get, web_scrape

# Candidate native paths for the company's post/update listing — tried in order, first
# response with a usable list of items wins. (Company-profile path is separate, above.)
CANDIDATE_POST_PATHS = [
    f"/linkedin/companies/{LINKEDIN_COMPANY}/posts",
    f"/linkedin/companies/{LINKEDIN_COMPANY}/updates",
]

AI_PROMPT = (
    "Extract the recent posts/updates shown on this LinkedIn company page as a JSON array. "
    "Each item must be an object with exactly these fields: "
    '"author" (the posting company or person name), '
    '"text" (the full post caption/body text), '
    '"date" (the post\'s posted date or relative time exactly as shown, e.g. "2d" or "Jul 10, 2026"), '
    '"url" (the post\'s permalink if one is visible, else the page URL), '
    '"likes" (the like/reaction count as a number, 0 if not shown). '
    "Only return real posts actually present on the page — do not invent any."
)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _hash(s):
    return hashlib.md5((s or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _iso(s):
    """Best-effort -> ISO 8601. Handles ISO/RFC strings, epoch seconds/millis (int), and
    anything pandas can parse; unparseable/relative-only strings ("2d", "3w") -> ''."""
    if s is None or s == "":
        return ""
    try:
        if isinstance(s, (int, float)) or (isinstance(s, str) and s.strip().isdigit()):
            n = int(s)
            unit = "ms" if n > 10 ** 12 else "s"
            ts = pd.to_datetime(n, unit=unit, utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(s, utc=True, errors="coerce", format="mixed")
        if pd.notna(ts):
            return ts.isoformat()
    except Exception:
        pass
    return ""


def _find_list(resp):
    """A native-endpoint response's shape isn't pinned down — look under the common keys
    ScrapeBadger list endpoints use for the item array."""
    if not isinstance(resp, dict):
        return []
    for key in ("posts", "updates", "data", "items", "results"):
        v = resp.get(key)
        if isinstance(v, list) and v:
            return v
    return []


def _row_native(p, company_name):
    pid = str(p.get("id") or p.get("urn") or p.get("post_id") or p.get("activity_id") or "")
    text = (p.get("text") or p.get("commentary") or p.get("content") or p.get("description") or "").strip()
    url = p.get("url") or p.get("permalink") or p.get("link") or ""
    if not url and pid:
        url = f"https://www.linkedin.com/feed/update/{pid}/"
    author = p.get("author") or p.get("author_name") or company_name
    author_name = p.get("author_name") or p.get("author") or company_name
    created = p.get("created_at") or p.get("posted_at") or p.get("publishedAt") or p.get("date") or ""
    likes = p.get("likes") or p.get("num_likes") or p.get("reaction_count") or p.get("likeCount") or 0
    comments = p.get("comments") or p.get("num_comments") or p.get("commentCount") or 0
    shares = p.get("shares") or p.get("num_shares") or p.get("repostCount") or 0
    sid = f"linkedin:{pid}" if pid else f"linkedin:{_hash(url or text)}"
    return dict(
        source_id=sid, source="linkedin",
        author=author, author_name=author_name,
        text=text, url=url, created_at=_iso(created), lang="en",
        engagement=_int(likes), reply_count=_int(comments), retweet_count=_int(shares),
        conversation_id=pid or None,
        raw_json=json.dumps(p, ensure_ascii=False, default=str),
    )


def _row_extracted(item):
    text = (item.get("text") or "").strip()
    if not text:
        return None
    url = item.get("url") or LINKEDIN_POSTS_URL
    author = item.get("author") or "Axis Bank"
    likes = item.get("likes") or 0
    created = item.get("date") or ""
    basis = url if url and url != LINKEDIN_POSTS_URL else text
    sid = f"linkedin:{_hash(basis)}"
    return dict(
        source_id=sid, source="linkedin",
        author=author, author_name=author,
        text=text, url=url, created_at=_iso(created), lang="en",
        engagement=_int(likes),
        raw_json=json.dumps(item, ensure_ascii=False),
    )


def _fetch_native():
    company = sb_get(f"/linkedin/companies/{LINKEDIN_COMPANY}", {"country": "in"})
    if not company:
        print("  [linkedin] company endpoint returned nothing (404/login-wall) — trying posts anyway.")
    company_name = "Axis Bank"
    if isinstance(company, dict):
        company_name = company.get("name") or company.get("company_name") or company_name

    posts_raw = []
    for path in CANDIDATE_POST_PATHS:
        resp = sb_get(path, {"country": "in", "count": LINKEDIN_MAX_POSTS})
        items = _find_list(resp)
        if items:
            print(f"  [linkedin] native posts via {path}: {len(items)} raw items")
            posts_raw = items[:LINKEDIN_MAX_POSTS]
            break
    if not posts_raw:
        return []

    rows = {}
    for p in posts_raw:
        if not isinstance(p, dict):
            continue
        r = _row_native(p, company_name)
        if r["text"]:
            rows[r["source_id"]] = r
    return list(rows.values())


def _fetch_fallback():
    resp = web_scrape(LINKEDIN_POSTS_URL, ai_prompt=AI_PROMPT, anti_bot=True, country="in", max_cost=15)
    if not resp:
        print("  [linkedin] web-scrape fallback returned nothing (likely login wall) — degrading to [].")
        return []
    items = extract_items(resp, ai_prompt_used=True)
    if not items:
        print("  [linkedin] no ai_extraction items in fallback response (login wall likely) — degrading to [].")
        return []

    rows = {}
    for it in items[:LINKEDIN_MAX_POSTS]:
        if not isinstance(it, dict):
            continue
        r = _row_extracted(it)
        if r:
            rows[r["source_id"]] = r
    return list(rows.values())


def fetch():
    if not has_key():
        print("  [linkedin] SCRAPEBADGER_API_KEY not set — skipping (no LinkedIn source).")
        return []
    try:
        rows = _fetch_native()
        if not rows:
            rows = _fetch_fallback()
    except CreditsExhausted as e:
        print(f"  [linkedin] skipped — {e}")
        return []
    print(f"  [linkedin] {len(rows)}")
    return rows


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    out = fetch()
    upsert_posts(out)
    print(f"landed {len(out)} -> raw_posts. Classify: python -m analyze.run_analyze")
