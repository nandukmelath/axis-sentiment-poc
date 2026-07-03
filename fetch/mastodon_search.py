"""Mastodon public hashtag timelines — FREE, NO AUTH. Pulls posts tagged #AxisBank etc."""
import re
import requests
from config import MASTODON_INSTANCE, MASTODON_TAGS, FETCH_LIMITS

TAG_RE = re.compile(r"<[^>]+>")


def _text(html):
    return TAG_RE.sub(" ", html or "").replace("&amp;", "&").replace("&#39;", "'").strip()


def fetch():
    inst = MASTODON_INSTANCE or "mastodon.social"
    n = FETCH_LIMITS.get("mastodon", 20)
    rows = {}
    for tag in MASTODON_TAGS:
        try:
            r = requests.get(f"https://{inst}/api/v1/timelines/tag/{tag}", params={"limit": n}, timeout=25)
            if r.status_code != 200:
                continue
            statuses = r.json()
        except Exception as e:
            print(f"  [mastodon] error: {str(e)[:80]}")
            continue
        for s in statuses:
            acct = (s.get("account") or {}).get("acct", "")
            sid = "mastodon:" + str(s.get("id"))
            rows[sid] = dict(
                source_id=sid, source="mastodon",
                author=("@" + acct) if acct else "", author_name=(s.get("account") or {}).get("display_name"),
                text=_text(s.get("content", "")), url=s.get("url", ""),
                created_at=s.get("created_at", ""),
                engagement=int(s.get("favourites_count", 0) or 0),
                reply_count=int(s.get("replies_count", 0) or 0),
                retweet_count=int(s.get("reblogs_count", 0) or 0),
                lang=s.get("language") or "en")
    print(f"  [mastodon] {len(rows)}")
    return list(rows.values())
