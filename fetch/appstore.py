"""Apple App Store reviews — free iTunes customer-reviews RSS (no key, no account).

Endpoint reality check (probed live 2026-08-05):
  * `https://itunes.apple.com/{cc}/rss/customerreviews/page={p}/id={id}/sortby=mostrecent/json`
    IS ALIVE and returns 50 real reviews/page, pages 1..10 (page 11 -> HTTP 400), strictly
    reverse-chronological with zero overlap between pages. This is the working source.
  * `amp-api.apps.apple.com` / `amp-api-edge.apps.apple.com` (the "keyless same-origin proxy")
    returns **HTTP 401 Unauthorized**, with or without User-Agent/Origin/Referer headers. It
    needs a Bearer token, and the token is no longer embedded in the apps.apple.com HTML, so
    there is no keyless path there. Do not "upgrade" to it — it is a downgrade.

What used to make this return 0 rows:
  1. `entries[...][1:]` — a copy/paste of the *top-apps* feed shape, where entry[0] is app
     metadata. In the CUSTOMER REVIEWS feed entry[0] is a real review, so the newest review
     was silently discarded every run (and a 1-review feed collapsed to 0).
  2. Bare `urlopen` sent a `Python-urllib/3.x` UA and had no retry, so one flaky edge response
     (Apple's CDN intermittently serves an `entry`-less feed) produced a clean, silent 0.
  3. App-id resolution went through `/search?limit=1`, which is fuzzy — the same query returns
     Instagram and WhatsApp a few slots down. One bad day = off-brand reviews, or 0.

Resolution is now developer-scoped: iTunes `/lookup?id={artistId}&entity=software` enumerates
every app published by "Axis Bank Ltd" (6 apps as of 2026-08-05), each brand-verified before
use, so the reviews are on-brand by construction.
"""
import os
import json
import time
import datetime
import urllib.request
import urllib.parse

from config import APPSTORE_APP_ID, APPSTORE_SEARCH, FETCH_LIMITS
from fetch.webutil import HEADERS, brand_match


def _env_int(name, default):
    """Never let a typo'd env var raise at IMPORT time — that would take run_fetch down."""
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return int(default)


# iTunes artist id for "Axis Bank Ltd" (verified live: /lookup?id=699582556 -> artistId).
# Developer-scoped enumeration beats keyword search — it cannot drift onto another brand.
APPSTORE_ARTIST_ID = os.getenv("APPSTORE_ARTIST_ID", "517259642")
APPSTORE_COUNTRY = os.getenv("APPSTORE_COUNTRY", "in")          # Indian storefront
APPSTORE_PAGES = _env_int("APPSTORE_PAGES", 10)                 # Apple hard-caps at 10
# Last-resort id if BOTH lookup and search are unreachable: "Axis Bank Mobile Banking"
# (bundle com.axisbank.axismobile, 366k ratings) — independently confirmed via /lookup.
APPSTORE_FALLBACK_ID = os.getenv("APPSTORE_FALLBACK_ID", "699582556")
# Staleness guard, mirroring RSS_NEWS_MAX_AGE_DAYS. The low-volume Axis apps (Kochi1,
# SecureAuth) have page-1 feeds reaching back to 2023; without this they eat cap slots
# with years-old reviews while the flagship's recent pages go unread. 0 disables.
APPSTORE_MAX_AGE_DAYS = _env_int("APPSTORE_MAX_AGE_DAYS", 365)
_MAX_PAGES = 10          # Apple returns HTTP 400 beyond page 10
_PAGE_SLEEP = 0.3        # politeness between page requests
_FULL_PAGE = 50          # a short page means that app's feed is exhausted


def _open_https(url, timeout=20):
    """urlopen restricted to https — blocks file:// / custom-scheme injection (bandit B310).

    Also sends the shared browser headers: a bare Python-urllib UA is the kind of thing
    Apple's edge throttles first, and it costs nothing to look like a browser.
    """
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https URL: {url[:60]}")
    req = urllib.request.Request(url, headers=dict(HEADERS))
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 — scheme enforced above


def _get_json(url, tries=3, timeout=20):
    """GET + parse JSON with backoff. Returns {} instead of raising."""
    for i in range(tries):
        try:
            with _open_https(url, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                print(f"  [appstore] {type(e).__name__}: {str(e)[:80]} <- {url[:90]}")
                return {}
            time.sleep(0.8 * (i + 1))
    return {}


def _iso(s):
    """Apple RSS stamp '2026-08-04T02:02:13-07:00' -> ISO 8601 UTC. Never raises."""
    if not s:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(str(s).strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    except (ValueError, TypeError):
        return str(s)


def _as_list(v):
    """Apple collapses a single-element feed to a bare object — normalize to a list."""
    if isinstance(v, list):
        return v
    return [v] if isinstance(v, dict) else []


def _is_brand_app(app):
    """Brand gate, applied to the APP not the review text.

    Review bodies ('Worst bank', 'app crashes on login') almost never contain the word
    'axis', so a text-level brand_match would throw away nearly every genuine row. Gating
    on app identity is strictly stronger: it is what keeps the fuzzy /search fallback from
    ever ingesting Instagram/WhatsApp reviews.
    """
    blob = " ".join(str(app.get(k, "")) for k in ("trackName", "artistName", "sellerName", "bundleId"))
    return brand_match(blob)


def _apps():
    """Resolve the Axis app catalogue to harvest -> [(app_id, app_name), ...]."""
    # 0) Operator override wins outright.
    if APPSTORE_APP_ID:
        return [(str(APPSTORE_APP_ID), "Axis Bank")]

    out, seen = [], set()

    def _absorb(results):
        for a in results or []:
            if a.get("wrapperType") != "software" and not a.get("trackId"):
                continue
            aid = str(a.get("trackId") or "")
            if not aid or aid in seen or not _is_brand_app(a):
                continue
            seen.add(aid)
            try:
                vol = int(a.get("userRatingCount") or 0)
            except (TypeError, ValueError):
                vol = 0
            out.append((aid, a.get("trackName") or "Axis Bank", vol))

    # 1) Developer-scoped enumeration — every app published by Axis Bank Ltd.
    if APPSTORE_ARTIST_ID:
        q = urllib.parse.urlencode({"id": APPSTORE_ARTIST_ID, "country": APPSTORE_COUNTRY,
                                    "entity": "software", "limit": 50})
        _absorb(_get_json("https://itunes.apple.com/lookup?" + q).get("results"))

    # 2) Keyword search as a backstop (brand-gated, so fuzzy hits are dropped).
    if not out:
        q = urllib.parse.urlencode({"term": APPSTORE_SEARCH, "country": APPSTORE_COUNTRY,
                                    "entity": "software", "limit": 25})
        _absorb(_get_json("https://itunes.apple.com/search?" + q).get("results"))

    # 3) Verified hard fallback so a resolver outage never silently yields 0 rows.
    if not out and APPSTORE_FALLBACK_ID:
        print("  [appstore] id resolution failed - using verified fallback id "
              f"{APPSTORE_FALLBACK_ID}")
        return [(str(APPSTORE_FALLBACK_ID), "Axis Bank Mobile Banking")]

    # Busiest app first — review volume tracks signal density.
    out.sort(key=lambda t: -t[2])
    return [(aid, name) for aid, name, _ in out]


def _page_url(app_id, page):
    return (f"https://itunes.apple.com/{APPSTORE_COUNTRY}/rss/customerreviews/"
            f"page={page}/id={app_id}/sortby=mostrecent/json")


def _row(entry, app_id, app_name):
    """One RSS entry -> one raw_posts dict. Returns None if unusable."""
    rid = str(entry.get("id", {}).get("label", "")).split("/")[-1].strip()
    body = (entry.get("content", {}) or {}).get("label", "") or ""
    title = (entry.get("title", {}) or {}).get("label", "") or ""
    text = f"{title}. {body}".strip(" .") if title else body.strip()
    if not rid or not text:
        return None
    rating = (entry.get("im:rating", {}) or {}).get("label", "")
    votes = (entry.get("im:voteCount", {}) or {}).get("label", "0")
    try:
        votes = int(votes)
    except (TypeError, ValueError):
        votes = 0
    author = ((entry.get("author", {}) or {}).get("name", {}) or {}).get("label", "")
    # App name is carried in the text so downstream text-level brand checks also pass,
    # and so the classifier knows WHICH Axis app the complaint is about.
    return dict(
        source_id=f"appstore:{rid}", source="appstore",
        author=author, author_name=author,
        text=f"[{app_name}] [{rating}★] {text}",
        url=f"https://apps.apple.com/{APPSTORE_COUNTRY}/app/id{app_id}?see-all=reviews",
        created_at=_iso((entry.get("updated", {}) or {}).get("label", "")),
        engagement=votes, lang="en")


def fetch():
    cap = int(FETCH_LIMITS.get("appstore", 40))
    try:
        apps = _apps()
    except Exception as e:                                   # belt and braces — never raise
        print(f"  [appstore] resolve error: {str(e)[:100]}")
        return []
    if not apps:
        print("  [appstore] 0 (no Axis app resolved on the "
              f"'{APPSTORE_COUNTRY}' storefront)")
        return []

    cutoff = ""
    if APPSTORE_MAX_AGE_DAYS > 0:
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=APPSTORE_MAX_AGE_DAYS)).isoformat()

    out, seen, stale = [], set(), 0
    live = list(apps)                       # apps whose feed still has pages left
    pages = max(1, min(APPSTORE_PAGES, _MAX_PAGES))
    # Page-major order: page 1 of EVERY Axis app before page 2 of any, so a small cap
    # still yields the most recent reviews across the whole catalogue.
    try:
        for page in range(1, pages + 1):
            if len(out) >= cap or not live:
                break
            for app_id, app_name in list(live):
                if len(out) >= cap:
                    break
                feed = _get_json(_page_url(app_id, page)).get("feed", {})
                entries = _as_list(feed.get("entry"))
                if len(entries) < _FULL_PAGE:      # short/empty page == feed exhausted
                    live = [a for a in live if a[0] != app_id]
                if not entries:
                    continue
                for e in entries:
                    if len(out) >= cap:
                        break
                    try:
                        r = _row(e, app_id, app_name)
                    except Exception:
                        continue
                    if not r or r["source_id"] in seen:
                        continue
                    if cutoff and r["created_at"] and r["created_at"] < cutoff:
                        stale += 1             # ISO-UTC strings compare lexicographically
                        continue
                    seen.add(r["source_id"])
                    out.append(r)
                time.sleep(_PAGE_SLEEP)
    except Exception as e:                                   # contract: never raises
        print(f"  [appstore] error: {str(e)[:100]}")

    if not out:
        print(f"  [appstore] 0 (reviews feed returned nothing usable for "
              f"{len(apps)} Axis app(s); {stale} dropped as stale)")
    else:
        print(f"  [appstore] {len(out)}")
    return out


if __name__ == "__main__":   # read-only probe; writes nothing
    rows = fetch()
    print(f"apps={[a for a, _ in _apps()]} rows={len(rows)}")
    for r in rows[:3]:
        print(r["source_id"], r["created_at"], r["text"][:70].encode("ascii", "replace").decode())
