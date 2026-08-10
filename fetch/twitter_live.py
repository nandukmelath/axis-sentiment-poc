"""Live X / Twitter acquisition — keyless, two-stage.

X killed the free API and the public search page needs a logged-in session, so
there is no single endpoint that both FINDS tweets and returns full data. This
module uses two, each doing the half it is good at:

  DISCOVERY   Nitter RSS search                    ->  tweet ids + rough text
  HYDRATION   api.twitter.com GraphQL + guest token ->  every field, incl. reach
              cdn.syndication.twimg.com             ->  fallback, no reach

Discovery is the fragile half. Nitter instances die constantly — of 23 probed on
2026-08-10 only two served results, the rest returned 403 (Anubis JS challenge),
502, or an empty feed. So instances are a rotating pool, tried in order, with a
failing instance dropped for the rest of the run. Expect to update INSTANCES; the
`--probe` flag re-tests the pool and prints what is alive.

HYDRATION — GraphQL first
X's web client authenticates logged-out users with a GUEST token: POST to
/1.1/guest/activate.json against the public web bearer (a constant in X's JS
bundle, identifying the client, not a user) and it mints one. That token opens
TweetResultByRestId, which returns the full legacy object:

    retweet_count · quote_count · bookmark_count · views.count
    favorite_count · reply_count · full_text · author · created_at

Measured 2026-08-10: 60/60 lookups returned HTTP 200 with no rate-limiting at
~0.4s spacing, 56 with complete metrics and 4 tombstoned (deleted or protected).

The catch is the query id in the URL. It is pinned per web-client build and
rotates every few months; when it stops resolving, hydration falls back to
syndication rather than failing the run, and TWITTER_GRAPHQL_QID overrides it.

HYDRATION — syndication fallback
cdn.syndication.twimg.com is what the embedded-tweet widget calls: public,
keyless, and far more stable than the GraphQL query id. It carries text, author,
timestamp, likes and replies but NOT reshares, quotes or views — those stay null
on this path rather than being zeroed, so a missing metric can never be mistaken
for a measured zero. The `token` param is required but not validated.

Why not just the CSV? Because it goes stale the moment the operator stops
exporting — the corpus sat frozen at 2026-07-02 for five weeks. This path is
unattended.
"""
import argparse
import datetime as dt
import html as _html
import json
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

# X's own web client calls this with a GUEST token — no account, no API key. It is
# the only keyless source of retweet_count, quote_count, bookmark_count and views,
# none of which the syndication endpoint exposes.
#
# The public web bearer is a constant shipped in X's JS bundle; it identifies the
# web client, not a user. /guest/activate.json mints a short-lived guest token
# against it, exactly as a logged-out browser does.
GRAPHQL_BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                  "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
GUEST_ACTIVATE = "https://api.twitter.com/1.1/guest/activate.json"
# Query ids are pinned per client build and rotate every few months. When this 404s,
# the fetch falls back to syndication rather than dying; override via env.
GRAPHQL_QUERY_ID = os.getenv("TWITTER_GRAPHQL_QID", "0hWvDhmW8YQ-S_ib3azIrw")
GRAPHQL_URL = f"https://api.twitter.com/graphql/{GRAPHQL_QUERY_ID}/TweetResultByRestId"

# Required by the endpoint; omitting any key returns 400. Values mirror a logged-out
# web client. view_counts_everywhere_api_enabled is the one that matters — without it
# the `views` object comes back absent.
GRAPHQL_FEATURES = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": False,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

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


def graphql_session():
    """A requests.Session carrying a fresh guest token."""
    s = requests.Session()
    s.headers.update({"User-Agent": WEB_HEADERS["User-Agent"],
                      "Authorization": f"Bearer {GRAPHQL_BEARER}"})
    try:
        r = s.post(GUEST_ACTIVATE, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        s.headers["x-guest-token"] = r.json()["guest_token"]
    except (requests.RequestException, ValueError, KeyError):
        return None
    return s


def hydrate_graphql(tid, session):
    """Full metrics for one tweet, or None.

    Returns retweet/quote/bookmark/view counts that syndication does not carry.
    None means "ask syndication instead" — a deleted or protected tweet comes back
    as a Tombstone with no legacy block, and so does a rotated query id.
    """
    if session is None:
        return None
    try:
        r = session.get(GRAPHQL_URL, timeout=TIMEOUT, params={
            "variables": json.dumps({"tweetId": str(tid), "withCommunity": False,
                                     "includePromotedContent": False, "withVoice": False}),
            "features": json.dumps(GRAPHQL_FEATURES)})
        if r.status_code != 200:
            return None
        res = (((r.json() or {}).get("data") or {}).get("tweetResult") or {}).get("result") or {}
    except (requests.RequestException, ValueError):
        return None

    leg = res.get("legacy") or {}
    if not leg:
        return None
    user = (((res.get("core") or {}).get("user_results") or {}).get("result") or {})
    uleg = user.get("legacy") or {}
    views = res.get("views") or {}

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "id": str(res.get("rest_id") or tid),
        "text": leg.get("full_text") or "",
        "author": uleg.get("screen_name") or (user.get("core") or {}).get("screen_name") or "",
        "author_name": uleg.get("name") or (user.get("core") or {}).get("name") or "",
        "created_at": leg.get("created_at") or "",
        "lang": leg.get("lang") or "",
        "engagement": _int(leg.get("favorite_count")),
        "reply_count": _int(leg.get("reply_count")),
        "retweet_count": _int(leg.get("retweet_count")),
        "quote_count": _int(leg.get("quote_count")),
        "bookmark_count": _int(leg.get("bookmark_count")),
        # views is {"count": "7451", "state": ...} and absent on older tweets
        "view_count": _int(views.get("count")),
        "conversation_id": leg.get("conversation_id_str") or str(tid),
    }


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
    """Normalise every date shape the three sources emit to ISO8601 UTC.

    Syndication returns ISO already. Nitter emits RFC-822 pubDate. GraphQL emits
    Twitter's legacy format ("Mon Aug 10 07:28:08 +0000 2026") — no comma, year
    last — which none of the RFC-822 patterns match, so it has to be listed
    explicitly or it lands in the DB as an unparseable string.
    """
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a %b %d %H:%M:%S %z %Y",                 # GraphQL / Twitter legacy
                "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
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
    rows, hydrated, via_gql = [], 0, 0
    gql = graphql_session()
    if gql is None and verbose:
        print("  [x] guest-token activation failed — falling back to syndication "
              "(no reshare/quote/view counts)")
    with requests.Session() as sess:
        for tid in ids:
            base = found[tid]
            # GraphQL first: it is the only keyless source of reshares/quotes/views.
            h = hydrate_graphql(tid, gql)
            if h:
                via_gql += 1
            else:
                h = hydrate(tid, sess)
            time.sleep(HYDRATE_PAUSE)
            if h:
                hydrated += 1
                text = h["text"] or base["text"]
                author = h["author"] or base["author"]
                # Always through _iso: GraphQL and syndication use different date
                # formats, and only one of them is already ISO.
                created = _iso(h["created_at"]) or _iso(base["created_at"])
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
                # Present only on the GraphQL path; None (not 0) on syndication, so a
                # missing metric never masquerades as a measured zero.
                "retweet_count": (h or {}).get("retweet_count"),
                "quote_count": (h or {}).get("quote_count"),
                "view_count": (h or {}).get("view_count"),
                "bookmark_count": (h or {}).get("bookmark_count"),
                "conversation_id": (h or {}).get("conversation_id") or tid,
            })
    if verbose:
        print(f"  [x] discovered {len(found)}, hydrated {hydrated} "
              f"({via_gql} with full metrics via GraphQL), kept {len(rows)} on-brand")
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
