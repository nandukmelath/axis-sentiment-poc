"""ScrapeBadger TikTok — NATIVE endpoints first, universal web-scrape as fallback.

Auth: SCRAPEBADGER_API_KEY in .env (shared with the rest of the ScrapeBadger fetchers).

Order of attempts (each degrades cleanly to the next on empty/blocked):
  1. GET /v1/tiktok/search?query=Axis+Bank            (native, structured)
  2. GET /v1/tiktok/get-hashtag-videos?hashtag=axisbank  (native, structured)
  3. POST /v1/web/scrape on the public TikTok search URL, ai_extract=true  (universal fallback,
     only reached if both native paths 404/return nothing — TikTok is frequently login-walled
     even for scrapers, so this is expected to sometimes land empty too)

Run:
  python -m fetch.tiktok
"""
import datetime
import hashlib
import json

from config import TIKTOK_QUERY, TIKTOK_HASHTAG, TIKTOK_MAX
from fetch.scrapebadger import CreditsExhausted
from fetch.scrapebadger_web import has_key, sb_get, web_scrape, extract_items
from fetch.webutil import brand_match

SEARCH_PATH = "/tiktok/search"
HASHTAG_PATH = "/tiktok/get-hashtag-videos"
PUBLIC_SEARCH_URL = "https://www.tiktok.com/search?q={q}"
AI_PROMPT = (
    "Extract TikTok videos mentioning 'Axis Bank' from this search results page. "
    "Return a JSON array of objects with fields: author (the @username), text "
    "(the video caption/description), date (post date if shown), url (the video "
    "permalink), likes (like count as a number, 0 if not shown)."
)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _iso(v):
    """TikTok createTime is usually a unix epoch (int/str-of-int); pass through ISO/other
    strings as-is. Returns '' on anything unparseable."""
    if v in (None, "", 0):
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(v), tz=datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(v)


def _hash_id(*parts):
    return hashlib.md5("|".join(str(p) for p in parts if p).encode(), usedforsecurity=False).hexdigest()[:12]


def _extract_native_list(resp):
    """TikTok scraper JSON shapes vary by provider version — try the common container keys,
    then fall back to treating the whole response as the list."""
    if not resp:
        return []
    for key in ("data", "videos", "items", "aweme_list", "results"):
        v = resp.get(key)
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict):
            for subkey in ("videos", "items", "aweme_list"):
                sv = v.get(subkey)
                if isinstance(sv, list) and sv:
                    return sv
    if isinstance(resp, list):
        return resp
    return []


def _row_from_native(item):
    """Normalize one native-endpoint TikTok video item (defensive against field-name drift)."""
    vid = str(item.get("id") or item.get("video_id") or item.get("aweme_id") or "")
    author_info = item.get("author") if isinstance(item.get("author"), dict) else {}
    handle = (author_info.get("uniqueId") or author_info.get("unique_id")
              or item.get("author_unique_id") or item.get("username") or item.get("author") or "")
    if isinstance(handle, dict):
        handle = handle.get("uniqueId") or handle.get("unique_id") or ""
    author_name = (author_info.get("nickname") or item.get("author_name")
                   or item.get("nickname") or handle)
    text = item.get("desc") or item.get("description") or item.get("text") or item.get("title") or ""
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    likes = stats.get("diggCount") if stats else item.get("digg_count") or item.get("likes") or item.get("like_count")
    views = stats.get("playCount") if stats else item.get("play_count") or item.get("views") or item.get("view_count")
    replies = stats.get("commentCount") if stats else item.get("comment_count") or item.get("reply_count")
    created = item.get("createTime") or item.get("create_time") or item.get("date") or item.get("created_at")
    url = item.get("url") or item.get("share_url") or item.get("webVideoUrl") or (
        f"https://www.tiktok.com/@{handle}/video/{vid}" if handle and vid else "")
    sid = f"tiktok:{vid}" if vid else f"tiktok:{_hash_id(url, text)}"
    return dict(
        source_id=sid, source="tiktok",
        author=("@" + handle) if handle and not str(handle).startswith("@") else (handle or ""),
        author_name=author_name or handle,
        text=text, url=url, created_at=_iso(created), lang="en",
        engagement=_int(likes), reply_count=_int(replies), view_count=_int(views),
        raw_json=json.dumps(item, ensure_ascii=False, default=str))


def _row_from_scraped(item):
    """Normalize one item out of the universal web-scrape ai_extraction fallback."""
    text = item.get("text") or item.get("caption") or item.get("description") or ""
    author = item.get("author") or item.get("username") or ""
    url = item.get("url") or item.get("link") or ""
    likes = item.get("likes") or item.get("like_count") or 0
    sid = f"tiktok:{_hash_id(url, text, author)}"
    return dict(
        source_id=sid, source="tiktok",
        author=("@" + author.lstrip("@")) if author else "", author_name=author,
        text=text, url=url, created_at=_iso(item.get("date")) or "", lang="en",
        engagement=_int(likes), raw_json=json.dumps(item, ensure_ascii=False, default=str))


def _native_pass(path, params, row_fn):
    """Native endpoints are already query/hashtag-scoped to Axis, so we keep rows even when
    the caption itself doesn't literally say "Axis" (e.g. a reply video, a meme caption) —
    we only require non-empty text. brand_match is reserved for the unscoped web-scrape
    fallback below, where it actually does filtering work."""
    try:
        resp = sb_get(path, params=params)
    except CreditsExhausted as e:
        print(f"  [tiktok] skipped — {e}")
        return None   # signal "stop trying further passes", distinct from "empty, try next"
    items = _extract_native_list(resp)
    rows = {}
    for it in items[:TIKTOK_MAX]:
        if not isinstance(it, dict):
            continue
        r = row_fn(it)
        if r["text"]:
            rows[r["source_id"]] = r
    return rows


def fetch():
    if not has_key():
        print("  [tiktok] SCRAPEBADGER_API_KEY not set — skipping (no TikTok source).")
        return []

    rows = {}

    # Pass 1: native search
    p1 = _native_pass(SEARCH_PATH, {"query": TIKTOK_QUERY}, _row_from_native)
    if p1 is None:
        print(f"  [tiktok] {len(rows)}")
        return list(rows.values())
    rows.update(p1)

    # Pass 2: native hashtag (only if pass 1 came up short — credit thrift)
    if len(rows) < TIKTOK_MAX:
        p2 = _native_pass(HASHTAG_PATH, {"hashtag": TIKTOK_HASHTAG}, _row_from_native)
        if p2 is None:
            print(f"  [tiktok] {len(rows)}")
            return list(rows.values())
        rows.update(p2)

    # Pass 3: universal web-scrape fallback — only if both native paths landed nothing
    # (likely 404 on this ScrapeBadger plan, or TikTok login-walled the native scraper).
    if not rows:
        try:
            resp = web_scrape(
                PUBLIC_SEARCH_URL.format(q=TIKTOK_QUERY.replace(" ", "%20")),
                ai_prompt=AI_PROMPT, anti_bot=True, max_cost=15)
        except CreditsExhausted as e:
            print(f"  [tiktok] skipped — {e}")
            resp = {}
        items = extract_items(resp, ai_prompt_used=True)
        if not items:
            print("  [tiktok] no rows — TikTok is frequently login-walled even for scrapers; "
                  "native endpoints and web-scrape fallback both came up empty.")
        for it in items[:TIKTOK_MAX]:
            if isinstance(it, dict):
                r = _row_from_scraped(it)
                if r["text"] and brand_match(r["text"] + " " + TIKTOK_QUERY):
                    rows[r["source_id"]] = r

    out = list(rows.values())[:TIKTOK_MAX]
    print(f"  [tiktok] {len(out)}")
    return out


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    r = fetch()
    upsert_posts(r)
    print(f"landed {len(r)} -> raw_posts. Classify: python -m analyze.run_analyze")
