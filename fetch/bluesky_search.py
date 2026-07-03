"""Bluesky public search — FREE, NO AUTH (public.api.bsky.app). Real-time global posts."""
import requests
from config import BLUESKY_QUERIES, FETCH_LIMITS

SEARCH = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"


def _rkey(uri):
    return uri.rsplit("/", 1)[-1] if uri else ""


def fetch():
    limit = FETCH_LIMITS.get("bluesky", 25)
    rows = {}
    for q in BLUESKY_QUERIES:
        try:
            r = requests.get(SEARCH, params={"q": q, "limit": limit}, timeout=25)
            if r.status_code != 200:
                continue
            posts = r.json().get("posts", [])
        except Exception as e:
            print(f"  [bluesky] error: {str(e)[:80]}")
            continue
        for p in posts:
            author = p.get("author") or {}
            rec = p.get("record") or {}
            handle = author.get("handle", "")
            rk = _rkey(p.get("uri", ""))
            sid = "bluesky:" + (p.get("cid") or rk)
            langs = rec.get("langs") or []
            rows[sid] = dict(
                source_id=sid, source="bluesky",
                author=("@" + handle) if handle else "", author_name=author.get("displayName"),
                text=rec.get("text", ""),
                url=f"https://bsky.app/profile/{handle}/post/{rk}" if (handle and rk) else "",
                created_at=rec.get("createdAt") or p.get("indexedAt", ""),
                engagement=int(p.get("likeCount", 0) or 0),
                reply_count=int(p.get("replyCount", 0) or 0),
                retweet_count=int(p.get("repostCount", 0) or 0),
                lang=langs[0] if langs else "en")
    print(f"  [bluesky] {len(rows)}")
    return list(rows.values())
