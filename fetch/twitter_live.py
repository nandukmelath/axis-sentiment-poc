"""Live X / Twitter acquisition — keyless, two-stage.

X killed the free API and the public search page needs a logged-in session, so
there is no single endpoint that both FINDS tweets and returns full data. This
module uses two, each doing the half it is good at:

  DISCOVERY   Nitter RSS search  ->  tweet ids + rough text
  HYDRATION   cdn.syndication.twimg.com/tweet-result  ->  authoritative fields

Discovery is the fragile half. Nitter instances die constantly — of 23 probed on
2026-08-10 only two served results, the rest returned 403 (Anubis JS challenge),
502, or an empty feed. So instances are a rotating pool, tried in order, with a
failing instance dropped for the rest of the run. Expect to update INSTANCES; the
`--probe` flag re-tests the pool and prints what is alive.

Hydration is the solid half and the reason this works at all.
cdn.syndication.twimg.com is the endpoint X's own embedded-tweet widget calls, so
it is public, keyless and stable. It returns the real text (not Nitter's
truncated title), the real author, the real timestamp, likes and reply count.
The `token` query param is NOT validated — any value returns 200 — but it is
required to be present.

WHAT IT DOES NOT GIVE
Syndication omits retweet_count, quote_count and view_count. Those stay null
rather than being guessed. Nitter's RSS does not carry them either. If you need
reshare counts, that is the paid API or nothing, and the dashboard already labels
reach as Twitter-only for exactly this reason.

Why not just the CSV? Because it goes stale the moment the operator stops
exporting — the corpus sat frozen at 2026-07-02 for five weeks. This path is
unattended.
"""
import argparse
import datetime as dt
import html as _html
import os
import re
import sys
import time

import requests

from config import TWITTER_QUERIES
from fetch.webutil import brand_match

# Probed 2026-08-10: only these two of 23 candidates returned search results.
# Override with TWITTER_NITTER_INSTANCES="host1,host2".
INSTANCES = [h.strip() for h in os.getenv(
    "TWITTER_NITTER_INSTANCES",
    "nitter.privacyredirect.com,nitter.perennialte.ch").split(",") if h.strip()]

SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"

# Nitter's RSS gate rejects browser User-Agents on some instances — it wants to look
# like a feed reader. This is not evasion; it is the client type the endpoint serves.
RSS_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rssbot/1.0)",
               "Accept": "application/rss+xml, application/xml, text/xml"}
WEB_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/131.0.0.0 Safari/537.36"}

TIMEOUT = int(os.getenv("TWITTER_TIMEOUT", "20"))
PER_QUERY_PAUSE = float(os.getenv("TWITTER_PAUSE", "1.5"))
HYDRATE_PAUSE = float(os.getenv("TWITTER_HYDRATE_PAUSE", "0.25"))
MAX_HYDRATE = int(os.getenv("TWITTER_MAX_HYDRATE", "400"))

_ITEM_RX = re.compile(r"<item>(.*?)</item>", re.S)
_STATUS_RX = re.compile(r"/status/(\d+)")
# Nitter prefixes replies with "R to @someone: ". Keep the text, drop the marker —
# the reply relationship comes from syndication, which is authoritative.
_REPLY_PREFIX_RX = re.compile(r"^R to @[\w]+:\s*")


def _tag(block, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    return _html.unescape(m.group(1)).strip() if m else ""


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


# ------------------------------------------------------------------ discovery
def discover(queries=None, instances=None, verbose=True):
    """Nitter RSS search across the instance pool. Returns {id: {...}}.

    An instance that errors or rate-limits is dropped for the remainder of the
    run rather than retried per query — once a Nitter box starts 503ing it stays
    that way for minutes, and hammering it just slows the fetch down.
    """
    queries = queries or TWITTER_QUERIES
    pool = list(instances or INSTANCES)
    found, dead = {}, set()

    for q in queries:
        for host in pool:
            if host in dead:
                continue
            url = f"https://{host}/search/rss"
            try:
                r = requests.get(url, params={"f": "tweets", "q": q},
                                 headers=RSS_HEADERS, timeout=TIMEOUT)
            except requests.RequestException as e:
                if verbose:
                    print(f"  [x] {host} net error: {type(e).__name__}")
                dead.add(host)
                continue

            if r.status_code != 200:
                if verbose:
                    print(f"  [x] {host} HTTP {r.status_code} for {q!r}")
                if r.status_code in (403, 429, 502, 503):
                    dead.add(host)
                continue

            n_before = len(found)
            for block in _ITEM_RX.findall(r.text):
                link = _tag(block, "link")
                m = _STATUS_RX.search(link)
                if not m:
                    continue
                tid = m.group(1)
                if tid in found:
                    continue
                author = _tag(block, "dc:creator").lstrip("@")
                found[tid] = {
                    "id": tid,
                    "author": author,
                    "text": _REPLY_PREFIX_RX.sub("", _clean(_tag(block, "title"))),
                    "created_at": _tag(block, "pubDate"),
                    "url": f"https://x.com/{author or 'i'}/status/{tid}",
                }
            if verbose:
                print(f"  [x] {host} {q!r}: +{len(found) - n_before} new "
                      f"({len(found)} total)")
            time.sleep(PER_QUERY_PAUSE)
            break                       # one working instance per query is enough
    return found


# ------------------------------------------------------------------ hydration
def _syndication_token(tid):
    """X's widget derives a token from the id. The endpoint does not validate it,
    but it must be present, so we reproduce the shape rather than send junk."""
    try:
        return format(int(int(tid) / 1e15 * 3.141592653589793), "x").replace("0", "")
    except (ValueError, TypeError):
        return "a"


def hydrate(tid, session=None):
    """Authoritative fields for one tweet, or None if X will not serve it
    (deleted, protected, or suspended author)."""
    get = (session or requests).get
    try:
        r = get(SYNDICATION, params={"id": tid, "token": _syndication_token(tid),
                                     "lang": "en"},
                headers=WEB_HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        j = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(j, dict) or not j.get("id_str"):
        return None

    user = j.get("user") or {}
    return {
        "id": str(j["id_str"]),
        "text": j.get("text") or "",
        "author": user.get("screen_name") or "",
        "author_name": user.get("name") or "",
        "created_at": j.get("created_at") or "",
        "lang": j.get("lang") or "",
        # Syndication exposes likes and replies only. Reshares/quotes/views are
        # left null rather than guessed — a fabricated reach number would flow
        # straight into the triage ranking.
        "engagement": j.get("favorite_count"),
        "reply_count": j.get("conversation_count"),
        "conversation_id": j.get("in_reply_to_status_id_str") or str(j["id_str"]),
    }


# ------------------------------------------------------------------ public
def _iso(s):
    """Nitter pubDate -> ISO8601. Syndication already returns ISO."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M %Z", "%a, %d %b %Y %H:%M"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(dt.timezone.utc).isoformat()
        except ValueError:
            continue
    return s if s[:4].isdigit() else None


def fetch(queries=None, limit=None, verbose=True):
    """Discover then hydrate. Returns raw_posts-shaped rows."""
    found = discover(queries, verbose=verbose)
    if not found:
        if verbose:
            print("  [x] no tweets discovered — every Nitter instance is down. "
                  "Run `python -m fetch.twitter_live --probe` to refresh the pool.")
        return []

    ids = list(found)[:limit or MAX_HYDRATE]
    rows, hydrated = [], 0
    with requests.Session() as sess:
        for tid in ids:
            base = found[tid]
            h = hydrate(tid, sess)
            time.sleep(HYDRATE_PAUSE)
            if h:
                hydrated += 1
                text = h["text"] or base["text"]
                author = h["author"] or base["author"]
                created = h["created_at"] or _iso(base["created_at"])
            else:
                text, author = base["text"], base["author"]
                created = _iso(base["created_at"])

            # Brand gate: a search for "Axis Bank" also returns Axis Communications,
            # Axis Mutual Fund and assorted noise.
            if not (brand_match(text) or re.search(r"@axisbank", text, re.I)):
                continue

            rows.append({
                "source_id": f"twitter:{tid}",
                "source": "twitter",
                "author": f"@{author}" if author else "",
                "author_name": (h or {}).get("author_name") or "",
                "text": text,
                "url": f"https://x.com/{author or 'i'}/status/{tid}",
                "created_at": created,
                "lang": (h or {}).get("lang") or "",
                "engagement": (h or {}).get("engagement"),
                "reply_count": (h or {}).get("reply_count"),
                "retweet_count": None,      # not exposed by either endpoint
                "quote_count": None,
                "view_count": None,
                "conversation_id": (h or {}).get("conversation_id") or tid,
            })
    if verbose:
        print(f"  [x] discovered {len(found)}, hydrated {hydrated}, "
              f"kept {len(rows)} on-brand")
    return rows


def probe(candidates=None):
    """Re-test the instance pool. Nitter churn is constant; this is how you find
    the replacements when the fetch starts returning zero."""
    cands = candidates or (INSTANCES + [
        "nitter.net", "nitter.poast.org", "nitter.tiekoetter.com", "nitter.space",
        "lightbrd.com", "nitter.cz", "n.opnxng.com", "nitter.catsarch.com",
        "nitter.qwik.space", "xcancel.com", "nitter.kavin.rocks"])
    print(f"{'instance':34} {'status':10} tweets")
    alive = []
    for h in dict.fromkeys(cands):
        try:
            r = requests.get(f"https://{h}/search/rss", params={"f": "tweets", "q": "AxisBank"},
                             headers=RSS_HEADERS, timeout=12)
            n = len(set(_STATUS_RX.findall(r.text)))
            print(f"{h:34} {r.status_code:<10} {n}")
            if n:
                alive.append(h)
        except requests.RequestException as e:
            print(f"{h:34} {type(e).__name__:<10} 0")
    print(f"\nworking: {','.join(alive) if alive else 'NONE'}")
    return alive


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true", help="re-test the Nitter pool")
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    if a.probe:
        probe()
        sys.exit(0)
    got = fetch(limit=a.limit)
    print(f"\n{len(got)} rows")
    for r in got[:5]:
        print(f"  {r['author']:18} {str(r['created_at'])[:19]}  {r['text'][:70]}")
