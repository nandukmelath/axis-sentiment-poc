"""GDELT 2.0 DOC API — free keyless global news index. Surfaces regional/vernacular Indian
outlets that Google News RSS misses. HARD rate limit: one request per 5 seconds (verified
live 2026-07-07 — the 429 body says exactly that), so we throttle 6s and treat 429 as a
clean skip (news sources are additive, never critical)."""
import time, hashlib, datetime
from config import FETCH_LIMITS, GDELT_QUERIES
from fetch.webutil import get, brand_match

DOC = ("https://api.gdeltproject.org/api/v2/doc/doc?query={q}"
       "&mode=artlist&maxrecords={n}&format=json&timespan=7d&sort=datedesc")


def _iso(seendate):
    # GDELT seendate: 20260707T123000Z
    try:
        return datetime.datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc).isoformat()
    except (ValueError, TypeError):
        return seendate or ""


def _row(a):
    title = a.get("title", "")
    sid = "gdelt:" + hashlib.md5((a.get("url") or title).encode(),
                                 usedforsecurity=False).hexdigest()[:12]
    return dict(
        source_id=sid, source="gdelt", author=a.get("domain", "gdelt"),
        text=title[:4000], url=a.get("url", ""), created_at=_iso(a.get("seendate", "")),
        engagement=0, lang=(a.get("language") or "en")[:8].lower())


def fetch():
    import urllib.parse
    per = max(10, FETCH_LIMITS.get("gdelt", 30) // max(1, len(GDELT_QUERIES)))
    rows = {}
    for i, q in enumerate(GDELT_QUERIES):
        if i:
            time.sleep(6)            # GDELT: >= 1 request / 5 s
        try:
            r = get(DOC.format(q=urllib.parse.quote(q), n=per))
            if r.status_code == 429:
                print("  [gdelt] rate-limited (429) — skipping remaining queries")
                break
            if r.status_code != 200:
                print(f"  [gdelt] HTTP {r.status_code} — skipped")
                continue
            for a in r.json().get("articles", []):
                if not brand_match(a.get("title", "")):
                    continue
                row = _row(a)
                rows.setdefault(row["source_id"], row)
        except Exception as e:
            print(f"  [gdelt] error: {str(e)[:70]}")
    print(f"  [gdelt] {len(rows)}")
    return list(rows.values())


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    rs = fetch()
    upsert_posts(rs)
    print(f"landed {len(rs)} -> raw_posts")
