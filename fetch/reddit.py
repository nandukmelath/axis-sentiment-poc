"""Reddit — three paths, best available wins.

  KEYED    Official API via PRAW (free tier, ~100 req/min OAuth) — richest: submissions +
           top comments with real scores. Setup: https://www.reddit.com/prefs/apps ->
           "script" app -> REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (+ REDDIT_USER_AGENT).

  KEYLESS  Arctic Shift (arctic-shift.photon-reddit.com) — a public, keyless, no-signup
           mirror of Reddit's data with continuous live ingest. Serves the SAME record
           shape as Reddit's own API (id/title/selftext/score/num_comments/permalink),
           so we get scores and comment bodies without credentials.
           VERIFIED LIVE 2026-08-05: /api/posts/search?subreddit=CreditCardsIndia&query=axis
           returned posts timestamped 2026-08-05 11:43 UTC (same day), and
           /api/comments/search?link_id=t3_<id> returned their comment bodies.

  LEGACY   www.reddit.com search.rss — still alive, but slow, so it is the FALLBACK only.
           Re-probed 2026-08-05 from this host, and the old docstring was wrong about WHY
           it broke. Two independent things are going on:
             1. HEADERS. fetch/webutil.py sends only {User-Agent: Chrome/126,
                Accept-Language}. Reddit's edge now 403s that with a ~190 KB HTML app
                shell. Adding a browser-COMPLETE set — current Chrome UA, an explicit
                Accept for feed types, and the Sec-Fetch-* trio — returns a real 200 Atom
                feed. Measured back-to-back on one IP: webutil headers -> 403 (189,908 B
                shell); _RSS_HEADERS below -> 200, 10 <entry> elements. Reproduced 3/3.
             2. BUDGET. Anonymous feed reads are throttled to ~1 request per ~60 s window
                (x-ratelimit-used:1, x-ratelimit-remaining:0.0, x-ratelimit-reset:6-47).
                So 12 subreddits costs ~12 minutes, which is why Arctic Shift is primary
                and this path is capped to RSS_SUBS subreddits.
           Two hypotheses were tested and REJECTED, recorded so nobody re-tries them:
             - curl_cffi TLS impersonation (the trick that defeats Business-Standard in
               fetch/scrapling_sources.py) makes this WORSE, not better: Scrapling's
               Fetcher.get(impersonate="chrome") 403'd on 5/5 reddit attempts while plain
               requests with the same URL and good headers got 200. This is not a TLS
               fingerprinting wall.
             - Public Redlib/Libreddit mirrors: of 16 probed, 4 no longer resolve and every
               single instance still serving sits behind an Anubis proof-of-work
               interstitial ("Making sure you're not a bot!"), so none are usable keyless.
           Also rejected: api.pullpush.io — reachable and keyless, but its index is frozen
           at 2025-05-19 (~15 months stale), so it cannot serve a live sentiment feed.

Both submissions and comments land as separate rows in raw_posts so the AI layer scores
each independently. One dead subreddit never kills the rest, and no path ever raises.
"""
import os
import re
import json
import html
import time
import datetime

import requests

from config import BRAND_ALIASES, SUBREDDITS, FETCH_LIMITS, REDDIT_COMMENTS_PER
from fetch.webutil import brand_match, HEADERS

# OR-query across aliases; drop bare "Axis" (too noisy) and punctuation-only variants
_ALIASES = [a for a in BRAND_ALIASES if a.lower() not in {"axis"} and a.strip("@#")]
SEARCH_Q = " OR ".join(f'"{a}"' if " " in a else a for a in dict.fromkeys(
    a.lstrip("@#") for a in _ALIASES))

# ---- Arctic Shift (keyless) tunables. Read via os.getenv so this module stays
# ---- self-contained; the orchestrator mirrors these into config.py centrally.
ARCTIC_BASE = os.getenv("REDDIT_ARCTIC_BASE", "https://arctic-shift.photon-reddit.com/api")
# Full-text term sent to Arctic Shift. "axis" is deliberately the single term: webutil's
# brand filter is \baxis\b, so anything brand_match can accept must contain it. Measured
# 2026-08-05: 90/90 returned posts passed brand_match, i.e. zero wasted rows.
ARCTIC_QUERY = os.getenv("REDDIT_ARCTIC_QUERY", "axis")
# How many of the freshest matched posts get their comment thread expanded (1 call each).
ARCTIC_COMMENT_POSTS = int(os.getenv("REDDIT_ARCTIC_COMMENT_POSTS", "15"))
ARCTIC_SLEEP = float(os.getenv("REDDIT_ARCTIC_SLEEP", "1.0"))   # polite gap between calls
# Legacy reddit.com .rss fallback: how many subreddits to try. Each costs a full
# ~60s rate-limit window, so this is deliberately small.
RSS_SUBS = int(os.getenv("REDDIT_RSS_SUBS", "4"))
_DEAD = ("", "[deleted]", "[removed]")
_MAXLEN = 4000

# Reddit's edge 403s fetch/webutil.py's 2-header set. This browser-COMPLETE set gets a
# real 200 Atom feed from the same IP — see the LEGACY note in the module docstring.
_RSS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


# A bare "axis" is a homonym. ARCTIC_QUERY is deliberately the single term "axis" for
# recall, and webutil's brand_match is only \baxis\b — the two together are the SAME test
# run twice, so they cannot separate the bank from these. Measured leaks: an "Axis powers
# attack on mainland British during WW2" political post, and "consultants like Y-Axis or
# Wave Visas" (an immigration consultancy). Both would have been scored as Axis Bank
# sentiment. Gate on meaning, not just on the token.
_STRONG_BRAND_RE = re.compile(
    r"axis\s*bank|axisbank|axis\s*mobile|axis\s*magnus|axis\s*atlas|axis\s*burgundy"
    r"|axis\s*direct|axis\s*max\s*life|axis\s*neo|@axis", re.IGNORECASE)
# Non-bank senses of "axis". If one of these is the only reason a row matched, drop it.
_AXIS_HOMONYM_RE = re.compile(
    r"\b(?:[xyz]|y)[-\s]?axis\b|\baxis\s+(?:powers?|of\s+evil|of\s+rotation|of\s+symmetry"
    r"|mundi|communications|records|bank\s+of\s+japan)\b|\bally?ies?\b.{0,40}\baxis\b"
    r"|\baxis\b.{0,40}\b(?:ww\s?(?:1|2|i|ii)|world\s+war|nazi|hitler|mussolini|visas?)\b",
    re.IGNORECASE)
# Retail-banking vocabulary — evidence that a bare "axis" really is the bank.
_BANK_CTX_RE = re.compile(
    r"\b(?:bank|banking|credit\s*card|debit\s*card|card|account|a/c|upi|neft|imps|rtgs"
    r"|loan|emi|branch|atm|netbanking|net\s*banking|cheque|ifsc|kyc|deposit|fd\b|rd\b"
    r"|savings|salary|forex|ombudsman|rbi|customer\s*care|helpline|statement|cashback"
    r"|reward\s*points?|lounge|annual\s*fee|charges?|refund|debited|credited)\b",
    re.IGNORECASE)


def _on_brand(text):
    """Brand-gate the value we will actually STORE.

    Must run on the truncated string, not the original: a post whose only 'Axis'
    mention sits past _MAXLEN would otherwise pass the filter and then land as an
    off-brand row once truncated.

    Three tiers:
      1. An explicit brand form ("Axis Bank", "AxisBank", "Axis Magnus", ...) -> accept.
      2. Otherwise a recognised non-bank sense of "axis" (WWII Axis powers, the x-axis,
         Y-Axis the visa consultancy) -> reject.
      3. Otherwise a bare "axis" is only accepted with retail-banking context nearby.
    """
    # Tier 1 runs BEFORE the brand_match floor on purpose: webutil's \baxis\b cannot match
    # "AxisBank" (the word boundary fails before 'B'), so gating on it first would throw
    # away the most unambiguous brand mentions there are.
    if _STRONG_BRAND_RE.search(text):
        return True
    if not brand_match(text):
        return False                       # keeps the existing \baxis\b floor
    if _AXIS_HOMONYM_RE.search(text):
        return False
    return bool(_BANK_CTX_RE.search(text))


def _iso(ts):
    """Epoch seconds -> ISO 8601 UTC. Returns '' rather than inventing a timestamp."""
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _clean(s):
    """Reddit selftext arrives HTML-escaped (&#x200B; zero-width joiners etc.)."""
    return html.unescape(s or "").strip()


def _reset_wait(resp, default=10.0, cap=60.0):
    """Seconds to wait, taken from the server's OWN throttle headers.

    Reddit and Arctic Shift both send x-ratelimit-reset (seconds until the current
    window ends); some paths send Retry-After instead. The previous implementation
    slept a hardcoded 10s while the observed reset was 22-36s, so every retry landed
    inside the SAME window and the "throttled >= 3" guard always tripped.
    """
    for h in ("x-ratelimit-reset", "retry-after"):
        raw = resp.headers.get(h)
        if not raw:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return min(v + 1.0, cap)
    return default


# ---------- KEYLESS Arctic Shift path ----------

# Circuit breaker. Without it a total Arctic outage costs 12 subreddits x 3 tries x
# (2+5+9)s of backoff before the RSS path even starts — over 5 minutes of a harvest spent
# proving one host is down. After this many CONSECUTIVE all-tries-failed calls, stop
# asking and fall through to the legacy path. Reset by any successful call.
ARCTIC_MAX_CONSEC_FAIL = int(os.getenv("REDDIT_ARCTIC_MAX_CONSEC_FAIL", "3"))
_arctic_consec_fail = 0


def _arctic_get(session, path, params, tries=3):
    """One Arctic Shift API call with status-aware backoff. Returns a list (never raises).

    422 "Timeout. Maybe slow down a bit" is a server-side *query* timeout, not a ban —
    a short retry clears it (measured: 3/3 previously-422 subreddits succeeded on retry).
    429 is a real rate limit, so that one honours x-ratelimit-reset.
    """
    global _arctic_consec_fail
    if _arctic_consec_fail >= ARCTIC_MAX_CONSEC_FAIL:
        return []                          # breaker open — host is down, stop paying for it
    short = (2.0, 5.0, 9.0)
    for i in range(tries):
        try:
            r = session.get(f"{ARCTIC_BASE}/{path}", params=params, timeout=45)
        except Exception as e:
            print(f"  [reddit] arctic {path} net error: {str(e)[:60]}")
            time.sleep(short[min(i, len(short) - 1)])
            continue
        if r.status_code == 200:
            _arctic_consec_fail = 0
            try:
                data = r.json().get("data")
            except ValueError:
                return []
            return data if isinstance(data, list) else []
        if r.status_code == 429:
            time.sleep(_reset_wait(r))
            continue
        if r.status_code in (422, 502, 503, 504):
            time.sleep(short[min(i, len(short) - 1)])
            continue
        print(f"  [reddit] arctic {path} HTTP {r.status_code}")
        return []
    # every try burned without a 200 — count it toward opening the breaker
    _arctic_consec_fail += 1
    if _arctic_consec_fail == ARCTIC_MAX_CONSEC_FAIL:
        print(f"  [reddit] arctic unreachable after {ARCTIC_MAX_CONSEC_FAIL} calls "
              f"— skipping remaining subreddits, falling back to reddit.com RSS")
    return []


def _fetch_arctic():
    """Keyless primary. Per-subreddit full-text search, then comments for the freshest hits."""
    limit = FETCH_LIMITS.get("reddit", 40)
    per_sub = max(5, limit // max(1, len(SUBREDDITS)))
    session = requests.Session()
    session.headers.update({"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"})

    seen, posts = set(), []
    for sub in SUBREDDITS:
        for p in _arctic_get(session, "posts/search",
                             {"subreddit": sub, "query": ARCTIC_QUERY,
                              "limit": per_sub, "sort": "desc"}):
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            title, body = _clean(p.get("title")), _clean(p.get("selftext"))
            if body in ("[deleted]", "[removed]"):
                body = ""
            text = f"{title}\n{body}".strip()[:_MAXLEN]
            if not text or not _on_brand(text):
                continue                      # off-topic never lands
            seen.add(pid)
            sr = p.get("subreddit") or sub
            permalink = p.get("permalink") or f"/r/{sr}/comments/{pid}/"
            posts.append(dict(
                source_id=f"reddit:{pid}", source="reddit",
                author=str(p.get("author") or ""), author_name=str(p.get("author") or ""),
                text=text, url=f"https://reddit.com{permalink}",
                created_at=_iso(p.get("created_utc")), lang="en",
                engagement=int(p.get("score") or 0),
                reply_count=int(p.get("num_comments") or 0),
                conversation_id=pid,
                raw_json=json.dumps({"type": "submission", "subreddit": sr,
                                     "upvote_ratio": p.get("upvote_ratio"),
                                     "via": "arctic-shift"})))
        time.sleep(ARCTIC_SLEEP)

    # Respect the configured cap precisely, newest first. Comments then ride on top of
    # the capped post set — the same convention the PRAW path has always used.
    posts.sort(key=lambda r: r["created_at"], reverse=True)
    posts = posts[:limit]

    out = list(posts)
    if REDDIT_COMMENTS_PER > 0 and posts:
        # posts are already newest-first — expand the freshest threads, where live
        # sentiment actually is
        for p in posts[:ARCTIC_COMMENT_POSTS]:
            pid = p["conversation_id"]
            for c in _arctic_get(session, "comments/search",
                                 {"link_id": f"t3_{pid}", "limit": REDDIT_COMMENTS_PER,
                                  "sort": "desc"}):
                cid = c.get("id")
                body = _clean(c.get("body"))
                if not cid or not body or body in _DEAD:
                    continue
                key = f"reddit:c_{cid}"
                if key in seen:
                    continue
                seen.add(key)
                # Comments inherit the thread's brand match: the parent submission already
                # passed brand_match, so a reply inside it is on-topic by construction.
                out.append(dict(
                    source_id=key, source="reddit",
                    author=str(c.get("author") or ""), author_name=str(c.get("author") or ""),
                    text=body[:_MAXLEN],
                    url=f"https://reddit.com{c.get('permalink') or ''}",
                    created_at=_iso(c.get("created_utc")), lang="en",
                    engagement=int(c.get("score") or 0), reply_count=None,
                    conversation_id=pid,
                    raw_json=json.dumps({"type": "comment", "subreddit": c.get("subreddit"),
                                         "parent_submission": pid, "via": "arctic-shift"})))
            time.sleep(ARCTIC_SLEEP)
    return out


# ---------- LEGACY reddit.com RSS path (works, but ~1 req/60s — see module docstring) -------

def _rss_text(entry):
    from bs4 import BeautifulSoup
    raw = entry.get("summary", "")
    if entry.get("content"):
        raw = entry["content"][0].get("value", raw)
    body = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    # Reddit RSS bodies end with "submitted by /u/x [link] [comments]" boilerplate — strip it
    return re.sub(r"submitted by\s+/u/\S+.*$", "", body).strip()


def _post_id(link):
    m = re.search(r"/comments/([a-z0-9]+)/", link or "")
    return m.group(1) if m else ""


def _entry_iso(entry):
    """feedparser struct_time -> ISO 8601 UTC (Reddit's Atom 'updated' is already ISO,
    but normalise so a future RFC-822 feed can never leak through)."""
    for key in ("updated_parsed", "published_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.datetime(*st[:6], tzinfo=datetime.timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass
    return entry.get("updated", entry.get("published", "")) or ""


def _rss_get(session, url):
    """https-only GET with the browser-complete header set reddit actually accepts."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https URL: {url[:60]}")
    return session.get(url, headers=_RSS_HEADERS, timeout=25, allow_redirects=True)


def _fetch_rss():
    import feedparser

    q = SEARCH_Q.replace(" ", "+")
    per_sub = max(10, FETCH_LIMITS.get("reddit", 40) // max(1, len(SUBREDDITS)))
    session = requests.Session()
    seen, out = set(), []
    blocked = 0
    subs = SUBREDDITS[:max(1, RSS_SUBS)]     # each sub costs a full ~60s window

    for i, sub in enumerate(subs):
        url = (f"https://www.reddit.com/r/{sub}/search.rss?"
               f"q={q}&restrict_sr=1&sort=new&limit={per_sub}")
        wait = 5.0
        try:
            r = _rss_get(session, url)
            if r.status_code in (403, 429):
                # Honour the server's OWN reset rather than a hardcoded 10s. The old code
                # slept 10s against an observed 22-47s window, so every retry landed in the
                # SAME bucket, nothing ever recovered, and the "throttled >= 3" guard
                # always tripped and killed the run.
                time.sleep(_reset_wait(r, default=60.0, cap=90.0))
                r = _rss_get(session, url)
            wait = _reset_wait(r, default=60.0, cap=90.0)
            if r.status_code != 200:
                blocked += 1
                continue
            for e in feedparser.parse(r.text).entries:
                pid = _post_id(e.get("link", ""))
                if not pid or pid in seen:
                    continue
                text = f"{e.get('title', '')}\n{_rss_text(e)}".strip()[:_MAXLEN]
                if not text or not _on_brand(text):
                    continue
                seen.add(pid)
                out.append(dict(
                    source_id=f"reddit:{pid}", source="reddit",
                    author=(e.get("author") or "").replace("/u/", "@"),
                    author_name=(e.get("author") or "").replace("/u/", ""),
                    text=text, url=e.get("link", ""),
                    created_at=_entry_iso(e), engagement=0, lang="en", conversation_id=pid,
                    raw_json=json.dumps({"type": "submission", "subreddit": sub, "via": "rss"})))
        except Exception as ex:
            print(f"  [reddit] r/{sub} rss error: {str(ex)[:60]}")
        # anonymous budget is ~1 request per window — pace off the server's own header
        if i < len(subs) - 1:
            time.sleep(wait)

    if blocked:
        print(f"  [reddit] legacy .rss: {blocked}/{len(subs)} subreddits refused "
              f"(reddit throttles anonymous feed reads to ~1 req/60s per IP)")
    return out


# ---------- KEYED PRAW path ----------

def _fetch_praw(cid, sec):
    try:
        import praw
    except ImportError:
        print("  [reddit] REDDIT_CLIENT_ID set but praw not installed — pip install praw")
        return []
    try:
        r = praw.Reddit(client_id=cid, client_secret=sec,
                        user_agent=os.getenv("REDDIT_USER_AGENT", "axis-sentiment-poc"),
                        check_for_async=False)
        r.read_only = True
    except Exception as e:
        print(f"  [reddit] praw init failed: {str(e)[:80]}")
        return []

    targets = ["all"] + SUBREDDITS          # r/all catches viral spillover into other subs
    per = max(5, FETCH_LIMITS["reddit"] // len(targets))
    seen, out = set(), []

    for sub in targets:
        try:
            for s in r.subreddit(sub).search(SEARCH_Q, sort="new", limit=per):
                if s.id in seen:
                    continue
                text = f"{s.title}\n{s.selftext or ''}".strip()[:_MAXLEN]
                if not text or not _on_brand(text):
                    continue
                seen.add(s.id)
                sr = getattr(s.subreddit, "display_name", sub)
                out.append(dict(
                    source_id=f"reddit:{s.id}", source="reddit",
                    author=str(s.author), author_name=str(s.author),
                    text=text,
                    url=f"https://reddit.com{s.permalink}",
                    created_at=_iso(s.created_utc), lang="en",
                    engagement=int(s.score or 0), reply_count=int(s.num_comments or 0),
                    conversation_id=s.id,
                    raw_json=json.dumps({"type": "submission", "subreddit": sr,
                                         "upvote_ratio": getattr(s, "upvote_ratio", None),
                                         "via": "praw"})))

                # top comments under this submission — where complaints actually live
                if REDDIT_COMMENTS_PER > 0:
                    try:
                        s.comment_sort = "top"
                        s.comments.replace_more(limit=0)
                        for c in s.comments[:REDDIT_COMMENTS_PER]:
                            body = (c.body or "").strip()
                            if not body or body in _DEAD:
                                continue
                            cid_ = f"reddit:c_{c.id}"
                            if cid_ in seen:
                                continue
                            seen.add(cid_)
                            out.append(dict(
                                source_id=cid_, source="reddit",
                                author=str(c.author), author_name=str(c.author),
                                text=body[:_MAXLEN],
                                url=f"https://reddit.com{c.permalink}",
                                created_at=_iso(c.created_utc), lang="en",
                                engagement=int(c.score or 0), reply_count=None,
                                conversation_id=s.id,
                                raw_json=json.dumps({"type": "comment", "subreddit": sr,
                                                     "parent_submission": s.id,
                                                     "via": "praw"})))
                    except Exception as e:
                        print(f"  [reddit] {sub}:{s.id} comments error: {str(e)[:60]}")
        except Exception as e:
            print(f"  [reddit] {sub} error: {str(e)[:80]}")
    return out


def fetch():
    """Best available path. Never raises; prints one progress line and returns rows."""
    rows, via = [], "none"
    try:
        cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
        if cid and sec:
            rows, via = _fetch_praw(cid, sec), "PRAW (keyed)"
        if not rows:
            arctic = _fetch_arctic()
            if arctic:
                rows, via = arctic, "Arctic Shift (keyless)"
        if not rows:
            rss = _fetch_rss()
            if rss:
                rows, via = rss, "reddit.com RSS (legacy)"
    except Exception as e:                     # belt-and-braces: fetch() must never raise
        print(f"  [reddit] unexpected error: {str(e)[:90]}")
        rows, via = rows or [], via

    n_com = sum(1 for o in rows if o["source_id"].startswith("reddit:c_"))
    print(f"  [reddit] {len(rows)} ({len(rows) - n_com} posts + {n_com} comments) via {via}")
    if not rows:
        print("  [reddit] 0 rows: Arctic Shift returned nothing AND reddit.com's anonymous "
              "feed refused every request (it allows ~1 read/60s per IP and 403s an "
              "incomplete header set). For a guaranteed path, create a 'script' app at "
              "https://www.reddit.com/prefs/apps and set REDDIT_CLIENT_ID + "
              "REDDIT_CLIENT_SECRET in .env — the keyed PRAW path above then takes over.")
    return rows


if __name__ == "__main__":
    got = fetch()
    for row in got[:5]:
        print(f"   {row['source_id']:18} {row['created_at'][:16]} "
              f"{row['text'][:90]!r}")
