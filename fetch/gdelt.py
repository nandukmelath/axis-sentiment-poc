"""GDELT 2.0 DOC API — free keyless global news index. Surfaces regional/vernacular Indian
outlets that Google News RSS misses.

RATE-LIMIT REALITY (re-verified live 2026-08-05, ~30 probe requests):
  * The documented limit is 1 request / 5 s per IP, but the limiter is *stochastic* under
    contention: with >=15 s spacing this IP still saw ~22% success / ~78% HTTP 429. A 429 is
    NOT a lockout — the identical query succeeds on a later attempt, usually within 2-4 tries.
  * The 429 body is PLAIN TEXT ("Please limit requests to one every 5 seconds..."), there is
    NO Retry-After header and no Content-Type. So backoff has to be self-driven, and any
    r.json() must be guarded (a 200 can also carry a non-JSON error body).
  * Each request takes 12-21 s server-side, so wall-clock cost is dominated by the request,
    not by our sleeps.

Consequences for this module (this is the fix — the old code did `break` on the first 429,
so one unlucky moment cost the whole source):
  1. RETRY with exponential backoff + jitter instead of abandoning the run; honour Retry-After
     if GDELT ever starts sending one.
  2. ONE shared throttle clock at module scope, so the >=6 s spacing is enforced end-to-end
     across every query AND every retry — not just between loop iterations.
  3. Over-fetch the whole cap in a single request so that ONE success fills the quota; then
     stop early. This is what actually makes the source reliable: we need 1 win, not N.
  4. A hard WALL-CLOCK deadline for the whole fetch() call (not just a sleep budget — the
     12-21 s requests dominate, so capping sleep alone still let a run reach 5+ minutes), split
     into a fair share per query so one 429-storming query cannot starve the others. Deadline
     hit => return whatever we have, cleanly.

YIELD (measured live 2026-08-05, query '"Axis Bank"'): GDELT indexes article BODIES, so most
hits are generic market roundups that merely name-drop Axis. artlist exposes only `title` as
text, so a title-less-mention row would land as off-brand noise — brand_match dropping them is
correct, not a bug. Post-filter yield is therefore the thing to tune, and two levers matter:
  * timespan 7d -> 4 unique on-brand stories; 14d -> 13. 30d also returns exactly 13, because
    maxrecords tops out at 250 and sort=datedesc truncates ~14d back anyway. So 14d is the
    sweet spot — past that the record cap binds, not the window.
  * always request the full maxrecords=250: a request costs the same whatever the number, and
    asking for ~90 would cut post-filter yield roughly threefold.
sort=hybridrel was tested and returned the same 4 stories as datedesc, so recency sort stays.

Tunables (env, so no config.py edit is required): GDELT_MIN_GAP, GDELT_ATTEMPTS,
GDELT_MAX_WAIT, GDELT_TIMEOUT, GDELT_TIMESPAN.
"""
import os
import time
import json
import random
import hashlib
import datetime
import urllib.parse

import requests

from config import FETCH_LIMITS, GDELT_QUERIES
from fetch.webutil import brand_match

API = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT is an open research API; a descriptive UA is the polite thing to send. In probing,
# every success came from a non-browser UA (4/18 vs 0/12 for the shared Chrome UA) — too
# small a sample to call proven, but there is no upside to impersonating a browser here.
HEADERS = {"User-Agent": "axis-sentiment-poc/1.0 (research; keyless GDELT DOC client)",
           "Accept": "application/json, text/plain, */*"}

MIN_GAP = float(os.getenv("GDELT_MIN_GAP", "6"))        # >= 1 req / 5 s, with headroom
MAX_ATTEMPTS = int(os.getenv("GDELT_ATTEMPTS", "5"))    # tries per query before giving up on it
MAX_WAIT = float(os.getenv("GDELT_MAX_WAIT", "180"))    # total WALL-CLOCK budget for one fetch()
TIMEOUT = float(os.getenv("GDELT_TIMEOUT", "30"))       # GDELT routinely takes 12-21 s
RESERVE = 22.0                                          # don't start a request without this much left
TIMESPAN = os.getenv("GDELT_TIMESPAN", "14d")           # 14d = best post-brand-filter yield
MAXRECORDS = 250                                        # GDELT's hard ceiling; always ask for it
BACKOFF_BASE = 8.0
BACKOFF_CAP = 45.0

_SESSION = requests.Session()
_last_req = 0.0        # module-level: shared throttle clock across queries, retries and calls

# GDELT reports language NAMES ("English"), not codes; the rest of the pipeline uses codes.
_LANG = {
    "english": "en", "hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml",
    "kannada": "kn", "bengali": "bn", "marathi": "mr", "gujarati": "gu", "punjabi": "pa",
    "urdu": "ur", "nepali": "ne", "sinhala": "si", "assamese": "as", "oriya": "or",
    "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt", "italian": "it",
    "dutch": "nl", "russian": "ru", "arabic": "ar", "turkish": "tr", "indonesian": "id",
    "vietnamese": "vi", "thai": "th", "japanese": "ja", "korean": "ko", "chinese": "zh",
}


class _Budget:
    """Wall-clock deadline for one fetch() call — keeps a bad GDELT day from stalling the
    harvest. Bounds sleeps AND request time, since the 12-21 s requests dominate."""

    def __init__(self, seconds):
        self.end = time.monotonic() + max(0.0, seconds)

    @property
    def left(self):
        return self.end - time.monotonic()

    def can_request(self):
        """True only if there is enough time left to be worth starting another request."""
        return self.left >= RESERVE

    def sleep(self, secs):
        """Sleep, never past the deadline. False => no time left for another request."""
        time.sleep(max(0.0, min(secs, self.left - RESERVE)))
        return self.can_request()


def _throttle(budget):
    """Enforce the shared >=MIN_GAP spacing. False => out of time, stop issuing requests.
    The politeness gap is always slept in full — it is never traded away for the deadline,
    or we would be firing requests faster than GDELT's documented 1/5 s."""
    gap = MIN_GAP - (time.monotonic() - _last_req)
    if gap > 0:
        time.sleep(gap)
    return budget.can_request()


def _retry_after(resp):
    """Seconds from a Retry-After header (numeric or HTTP-date), else None."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
    except Exception:
        return None


def _articles(resp):
    """Parse the artlist payload. None => body was not usable JSON (GDELT returns plain-text
    errors, sometimes even under a 200)."""
    body = (resp.text or "").lstrip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body, strict=False)   # GDELT occasionally emits raw control chars
    except ValueError:
        return None
    arts = data.get("articles")
    return arts if isinstance(arts, list) else []


def _iso(seendate):
    """GDELT seendate '20260805T123000Z' -> ISO 8601 UTC. Empty string if unparseable —
    never pass a non-ISO string through, downstream treats created_at as ISO."""
    try:
        return datetime.datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ""


def _row(a):
    title = (a.get("title") or "").strip()
    url = a.get("url") or ""
    sid = "gdelt:" + hashlib.md5((url or title).encode(),
                                 usedforsecurity=False).hexdigest()[:12]
    name = (a.get("language") or "").strip().lower()
    return dict(
        source_id=sid, source="gdelt", author=a.get("domain") or "gdelt",
        text=title[:4000], url=url, created_at=_iso(a.get("seendate", "")),
        engagement=0, lang=_LANG.get(name, name[:8] or "en"))


def _query(q, per, budget):
    """Run one GDELT query with retries. Returns a list of articles ([] = genuinely nothing),
    or None if the query could not be completed (429 storm / budget spent / transport error)."""
    global _last_req
    url = (f"{API}?query={urllib.parse.quote(q)}&mode=artlist&maxrecords={per}"
           f"&format=json&timespan={urllib.parse.quote(TIMESPAN)}&sort=datedesc")
    delay = BACKOFF_BASE
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not _throttle(budget):
            print("  [gdelt] time budget spent — stopping early")
            return None
        try:
            resp = _SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
        except Exception as e:
            _last_req = time.monotonic()
            print(f"  [gdelt] transport error: {type(e).__name__} {str(e)[:60]}")
            if attempt == MAX_ATTEMPTS or not budget.sleep(delay):
                return None
            delay = min(delay * 2, BACKOFF_CAP)
            continue
        _last_req = time.monotonic()

        if resp.status_code == 200:
            arts = _articles(resp)
            if arts is not None:
                return arts
            # 200 with a plain-text throttle/error body — treat exactly like a 429.
            reason = "200 non-JSON body"
        elif resp.status_code in (429, 503, 502, 504):
            reason = f"HTTP {resp.status_code}"
        else:
            print(f"  [gdelt] HTTP {resp.status_code} — skipped '{q[:40]}'")
            return None

        if attempt == MAX_ATTEMPTS:
            print(f"  [gdelt] {reason} after {MAX_ATTEMPTS} tries — giving up on '{q[:40]}'")
            return None
        wait = _retry_after(resp)
        if wait is None:
            wait = delay + random.uniform(0, 3)     # jitter: de-sync from other clients
        delay = min(delay * 2, BACKOFF_CAP)
        print(f"  [gdelt] {reason} — retry {attempt + 1}/{MAX_ATTEMPTS} in {wait:.0f}s")
        if not budget.sleep(wait):
            print("  [gdelt] time budget spent — stopping early")
            return None
    return None


def fetch():
    """Contract: no args, never raises, returns list[dict] with keys subset of db.RAW_COLS."""
    rows = {}
    try:
        limit = int(FETCH_LIMITS.get("gdelt", 30))
        queries = list(GDELT_QUERIES) or ['"Axis Bank"']
        # Ask for GDELT's maximum in ONE request: brand_match drops ~90% of titles, and a
        # single success should fill the cap so we can stop before the next 429 roll.
        per = MAXRECORDS
        run = _Budget(MAX_WAIT)
        completed = 0

        for i, q in enumerate(queries):
            if len(rows) >= limit or not run.can_request():
                break
            # Fair share of the remaining time, so a query stuck in a 429 storm cannot eat
            # the whole budget and starve the queries behind it (seen live: query 1 burned
            # 5 attempts, query 2 then returned every row of the run).
            share = _Budget(run.left / (len(queries) - i))
            arts = _query(q, per, share)
            if arts is None:
                continue
            completed += 1
            for a in arts:
                if not brand_match(a.get("title", "")):
                    continue
                row = _row(a)
                if row["text"]:
                    rows.setdefault(row["source_id"], row)

        if not rows and not completed:
            print("  [gdelt] no query completed (429 storm / timeouts) — GDELT DOC API is "
                  "throttling this IP; no rows this run, harvest continues (source is additive)")
    except Exception as e:
        print(f"  [gdelt] error: {type(e).__name__} {str(e)[:70]}")

    out = list(rows.values())[:int(FETCH_LIMITS.get("gdelt", 30))]
    print(f"  [gdelt] {len(out)}")
    return out


if __name__ == "__main__":
    from db import init_db, upsert_posts
    init_db()
    rs = fetch()
    upsert_posts(rs)
    print(f"landed {len(rs)} -> raw_posts")
