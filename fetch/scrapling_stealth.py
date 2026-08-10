"""FREE stealth-browser sources via Scrapling's StealthyFetcher (patchright Chromium).

These reach what plain TLS-impersonated HTTP (fetch/scrapling_sources.py) cannot, and they
cost nothing — they are the free replacement for the dead ScrapeBadger-backed modules:

  trustpilot   Cloudflare-fronted. A plain GET returns 403 "Verifying Connection"; the
               stealth browser rides that out and the page ships every review inside a
               `__NEXT_DATA__` JSON blob — star rating, title, body, author and dates, all
               structured. Highest-value stealth source: real customer-service reviews,
               freshly dated, star-rated.
  mouthshut    Free replacement for the (dead, paid) fetch/mouthshut.py. Cloudflare-walled
               for plain requests; the stealth browser gets the full server-rendered listing
               and we parse `div.review-article` directly — title, full body, author, star
               count and date, no AI-extraction step and no credits.
  gmaps        A dynamic map SPA: the search results carry each branch's name + star rating
               but no review text. So after stashing the branch list we navigate into the top
               STEALTH_GMAPS_PLACES branches and pull their actual reviews — author, stars,
               body and a real date — which is the only branch-level customer voice in this
               whole pipeline. Both row kinds are returned; the reviews are best-effort.

WHY THESE USED TO FLAKE (fixed 2026-08-05)
Trustpilot succeeded on only ~1 run in 3. Root cause, caught by polling the live DOM: the
first document response is a 1KB interstitial titled "Verifying Connection" that Scrapling's
`_detect_cloudflare` does NOT recognise ("No Cloudflare challenge found." in the log), so
`solve_cloudflare=True` returned immediately without waiting. That interstitial clears itself
via JS and re-navigates to the real page ~2-3s later, but the fetcher had already snapshotted
the 403 shell. Runs only "worked" when unrelated waits happened to straddle the redirect.
The fix is `_ready_action`: a page_action that polls `page.content()` until a content marker
proving the REAL page is loaded appears (with `_stealth_fetch` retrying the whole launch if it
never does). Correctness no longer depends on luck — and a good run got faster (~82s -> ~20s),
because we can now drop `network_idle` and stop waiting on Trustpilot's endless analytics.

Cost & safety: each source launches a real browser (~20-40s). That is why config.STEALTH_SOURCES
gates them OFF by default — they must never fire on the dashboard's RUN button (which the audit
already flags as a 15-minute hang risk). Enable for a full harvest with STEALTH_SOURCES=1. Every
fetcher early-returns [] when the flag is off, so they are always safe to register in
run_fetch.SOURCES.

We use the StealthyFetcher.fetch() classmethod (one launch + teardown per call) rather than
a persistent StealthySession — Scrapling had an async session-close deadlock (upstream #232),
and a fresh browser per source is the robust choice for a low-frequency harvest.

Standalone:  STEALTH_SOURCES=1 python -m fetch.scrapling_stealth
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

# Clamp Scrapling's hardcoded Chrome-149 fingerprint to one the installed dataset
# supports, before importing any fetcher. See tools/scrapling_mcp.py for the full
# note (upstream issue #396).
import scrapling.engines.toolbelt.fingerprints as _fp

if _fp.chromium_version > 143:
    _fp.chromium_version = 143
if _fp.chrome_version > 143:
    _fp.chrome_version = 143

from scrapling import Selector  # noqa: E402  (must follow the patch)
from scrapling.fetchers import StealthyFetcher  # noqa: E402

from config import (  # noqa: E402
    STEALTH_SOURCES, STEALTH_HEADLESS, STEALTH_TRUSTPILOT_URLS, STEALTH_GMAPS_URL,
    STEALTH_MAX_ITEMS, MOUTHSHUT_URL, MOUTHSHUT_MAX_ITEMS,
)

# Pending central config (orchestrator adds these to config.py; read locally until then).
STEALTH_RETRIES = int(os.getenv("STEALTH_RETRIES", "3"))      # browser relaunches per URL
STEALTH_READY_SECS = int(os.getenv("STEALTH_READY_SECS", "45"))  # DOM-readiness poll budget
# How many Maps branches to open for their actual reviews (~9s each). 0 = branch stars only.
# 4 -> 6: this is the only branch-level customer voice in the whole pipeline (see
# module docstring), and a 2h-cycle run has the time budget to visit two more
# branches per pass. Each visit is real browser-navigation time, so this stays a
# modest bump, not a large one.
STEALTH_GMAPS_PLACES = int(os.getenv("STEALTH_GMAPS_PLACES", "6"))

BRAND_RE = re.compile(r"\baxis\b", re.IGNORECASE)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# "Axis Bank - Kuvempunagar 3.7 Bank ·" -> capture name and the rating that follows
_GMAPS_CARD_RE = re.compile(r"^(.*?)\s(\d\.\d)\s")

# Star rating is carried into the record as a `[N★]` text prefix — the exact convention
# fetch/playstore.py and appstore.py use, which the VADER/LLM classifier already reads. We
# deliberately do NOT emit a pre-computed sentiment: raw_posts has no such column (db.RAW_COLS),
# the classifier is the single source of truth for sentiment, and a rating and a sentiment are
# not the same thing (a 5★ "finally fixed after 3 months of hell" is not positive).


# --------------------------------------------------------------- stealth plumbing
def _ready_action(marker, secs):
    """Build a page_action that polls the LIVE DOM until `marker(html)` is true.

    This is what makes these fetchers deterministic. Both Trustpilot and MouthShut can serve
    a short-lived JS interstitial that Scrapling's Cloudflare detector does not recognise; it
    clears itself and re-navigates a couple of seconds later. Without this poll the fetcher
    snapshots whichever shell happened to be on screen. `page.content()` raises mid-navigation
    ("Execution context was destroyed") — exactly the moment we care about — so that is caught
    and treated as "not ready yet", never as a failure.
    """
    def action(page):
        deadline = time.time() + secs
        while time.time() < deadline:
            try:
                if marker(page.content()):
                    return page
            except Exception:
                pass          # mid-navigation; the redirect we are waiting for is in flight
            page.wait_for_timeout(1000)
        return page
    return action


def _stealth_fetch(url, marker=None, solve_cloudflare=False, wait=0, network_idle=False,
                   tries=None, action=None, accept=None):
    """Launch a stealth browser, wait for real content, retry the whole launch on failure.

    Returns (response, body_text) on success, or (None, "<reason>") — callers print the reason
    and degrade to []. `marker` is the readiness/verification predicate: the same function
    gates the in-page poll AND the final accept, so a page that never escaped its interstitial
    is a failed attempt (retry) rather than a silent zero-row "success".

    `action` overrides the default readiness poll for fetchers that must drive the page (see
    gmaps); such a fetcher passes `accept` because after it clicks around, the final DOM is no
    longer the page `marker` describes.
    """
    tries = tries or STEALTH_RETRIES
    accept = accept or marker
    reason = "no attempt made"
    for attempt in range(1, tries + 1):
        try:
            r = StealthyFetcher.fetch(
                url, headless=STEALTH_HEADLESS, timeout=75000, network_idle=network_idle,
                load_dom=True, solve_cloudflare=solve_cloudflare, block_webrtc=True,
                google_search=True, wait=wait,
                page_action=action or (_ready_action(marker, STEALTH_READY_SECS)
                                       if marker else None))
            body = (r.body or b"").decode("utf-8", "ignore")
            if r.status == 200 and (accept is None or accept(body)):
                return r, body
            reason = (f"HTTP {r.status}"
                      + ("" if r.status != 200 else " but page never left its interstitial"))
        except Exception as e:            # browser launch / navigation / timeout
            reason = f"{type(e).__name__}: {str(e)[:70]}"
        if attempt < tries:
            time.sleep(2)
    return None, f"{reason} (after {tries} tries)"


def _int(v):
    """Coerce whatever a page/JSON hands us into a non-negative int (never raises)."""
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


def _utc_iso(dt):
    """datetime -> ISO 8601 UTC, e.g. '2026-08-05T07:26:29+00:00'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# Relative timestamps ("17 hrs 27 mins ago", "4 months ago", "a month ago") are all these
# review sites print. Resolve them against now(UTC) so created_at is a real ISO instant and
# the pipeline's recency/staleness logic works on these rows like any other.
_AGO_RE = re.compile(r"\b(\d+|an?)\s*(sec|min|hour|hr|day|week|month|year)", re.I)
_AGO_UNIT = {"sec": 1, "min": 60, "hour": 3600, "hr": 3600, "day": 86400,
             "week": 604800, "month": 2592000, "year": 31536000}


def _ago_iso(s, first_only=False):
    """'6 days ago' -> ISO 8601 UTC. `first_only` for blobs where a later "a year ago"
    belongs to something else (Google's owner-response line). "" when nothing parses."""
    hits = _AGO_RE.findall(s or "")
    if not hits:
        return ""
    if first_only:
        hits = hits[:1]
    secs = sum((1 if n.lower() in ("a", "an") else int(n)) * _AGO_UNIT[u.lower()]
               for n, u in hits)
    if not secs:
        return ""
    # floor to the minute: the page's own "N mins ago" ticks between runs, and a whole-minute
    # timestamp keeps re-fetches from rewriting created_at every time.
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return _utc_iso(now - timedelta(seconds=secs))


# --------------------------------------------------------------- trustpilot
def _find_reviews(obj):
    """Locate the reviews list anywhere in Trustpilot's __NEXT_DATA__ tree."""
    if isinstance(obj, dict):
        revs = obj.get("reviews")
        if isinstance(revs, list) and revs and isinstance(revs[0], dict) \
                and ("text" in revs[0] or "title" in revs[0]):
            return revs
        for v in obj.values():
            found = _find_reviews(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_reviews(v)
            if found:
                return found
    return None


def _tp_iso(s):
    """Trustpilot ships '2026-08-05T07:26:29.000Z' -> ISO 8601 UTC with a real offset."""
    if not s:
        return ""
    try:
        return _utc_iso(datetime.fromisoformat(str(s).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return str(s)


def _tp_ready(html):
    """The real review page carries __NEXT_DATA__ *containing review objects*. The
    "Verifying Connection" interstitial carries neither."""
    return "__NEXT_DATA__" in html and "experiencedDate" in html


def fetch_trustpilot():
    name = "trustpilot_web"
    if not STEALTH_SOURCES:
        print(f"  [{name}] skipped (set STEALTH_SOURCES=1)")
        return []
    out, seen = [], set()
    for url in STEALTH_TRUSTPILOT_URLS:
        r, body = _stealth_fetch(url, marker=_tp_ready, solve_cloudflare=True)
        if r is None:
            print(f"  [{name}] {body} — {url}")
            continue
        try:
            m = _NEXT_DATA_RE.search(body)
            data = json.loads(m.group(1)) if m else {}
            reviews = _find_reviews(data)
        except Exception as e:      # malformed/renamed payload — degrade, never raise
            print(f"  [{name}] unparseable __NEXT_DATA__: {str(e)[:70]}")
            continue
        # Brand gate at the page level, which is where it belongs on a review site: confirm
        # from the page's own payload that the reviewed business really is Axis Bank, and
        # every review on it is on-brand by construction (a 1★ "worst bank ever" that never
        # spells the name is still an Axis Bank review — a per-row keyword filter would
        # silently bin ~40% of the real signal).
        biz = ((data.get("props") or {}).get("pageProps") or {}).get("businessUnit") or {}
        ident = f"{biz.get('displayName', '')} {biz.get('identifyingName', '')}"
        if not BRAND_RE.search(ident.replace(".", " ")):
            print(f"  [{name}] not an Axis Bank page ({ident.strip()[:40]!r}) — skipping {url}")
            continue
        for rv in (reviews or [])[:STEALTH_MAX_ITEMS]:
            try:
                rid = rv.get("id")
                title = (rv.get("title") or "").strip()
                body_txt = (rv.get("text") or "").strip()
                text = f"{title}. {body_txt}".strip(". ").strip()
                if not rid or not text or rid in seen:
                    continue
                seen.add(rid)
                dates = rv.get("dates") or {}
                created = dates.get("publishedDate") or dates.get("experiencedDate") or ""
                out.append(dict(
                    source_id=f"trustpilot:{rid}", source="trustpilot",
                    author=(rv.get("consumer") or {}).get("displayName", ""),
                    text=f"[{rv.get('rating')}★] {text}" if rv.get("rating") else text,
                    url=url, created_at=_tp_iso(created),
                    engagement=_int(rv.get("likes")),
                    reply_count=1 if rv.get("reply") else 0,
                    lang=rv.get("language") or "en"))
            except Exception:
                continue            # one odd review never costs us the other nineteen
    print(f"  [{name}] {len(out)}")
    return out


# --------------------------------------------------------------- mouthshut
# Free stealth replacement for the ScrapeBadger-backed fetch/mouthshut.py (dead: 0 credits).
# The listing is fully server-rendered once the interstitial clears, so no AI extraction is
# needed — these are exact selectors off the real markup, not a model's guess at it.
_IST = timezone(timedelta(hours=5, minutes=30))   # MouthShut prints wall-clock IST


def _ms_iso(s):
    """MouthShut dates -> ISO 8601 UTC. Two shapes on the listing:
       '17 hrs 27 mins  ago' / '6 days ago'  (relative)  and
       'Jul 04, 2026 05:13 PM'               (absolute, IST wall-clock).
    Unparseable input returns "" rather than a bogus timestamp."""
    s = " ".join(str(s or "").split())
    if not s:
        return ""
    if s.lower().endswith("ago"):
        return _ago_iso(s)
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y", "%d %b %Y"):
        try:
            return _utc_iso(datetime.strptime(s, fmt).replace(tzinfo=_IST))
        except ValueError:
            continue
    return ""


def _ms_ready(html):
    """Real listing = review cards present. The Cloudflare/CDN shell has neither marker."""
    return "review-article" in html and "lblDateTime" in html


def _ms_text(node, selector):
    hit = node.css(selector)
    return " ".join((hit[0].get_all_text() or "").split()) if hit else ""


def _ms_int(node, selector):
    """First integer in the matched node's text ('167 Views' -> 167), else 0."""
    m = re.search(r"\d[\d,]*", _ms_text(node, selector))
    return int(m.group().replace(",", "")) if m else 0


def fetch_mouthshut():
    name = "mouthshut_web"
    if not STEALTH_SOURCES:
        print(f"  [{name}] skipped (set STEALTH_SOURCES=1)")
        return []
    r, body = _stealth_fetch(MOUTHSHUT_URL, marker=_ms_ready, solve_cloudflare=True)
    if r is None:
        print(f"  [{name}] {body}")
        return []
    try:
        cards = r.css("div.review-article")
    except Exception as e:
        print(f"  [{name}] parse error: {str(e)[:90]}")
        return []

    out, seen = [], set()
    for card in cards[:MOUTHSHUT_MAX_ITEMS]:
        try:
            link = card.css("a[id$=lnkTitle]")
            if not link:
                continue
            href = (link[0].attrib.get("href") or "").strip()
            title = " ".join((link[0].get_all_text() or "").split())
            review = _ms_text(card, "div.reviewdata")
            author = _ms_text(card, "div.user-ms-name a") or "anonymous"
            rating = len(card.css("i.rated-star"))          # filled stars out of 5
            created = _ms_iso(_ms_text(card, "span[id$=lblDateTime]"))
            text = f"{title}. {review}".strip(". ").strip()
            if not text:
                continue
            # Per-row brand gate — every genuine row's permalink is /review/axis-bank-review-*,
            # so a foreign card injected by a layout change (related products, ads) is dropped.
            if not BRAND_RE.search(f"{href} {text}"):
                continue
            # Stable id from the permalink slug ("axis-bank-review-tstqnolpuop").
            slug = href.rstrip("/").rsplit("/", 1)[-1] or re.sub(
                r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
            sid = f"mouthshut:{slug}"
            if not slug or sid in seen:
                continue
            seen.add(sid)
            out.append(dict(
                source_id=sid, source="mouthshut",
                author=author, author_name=author,
                text=f"[{rating}★] {text}" if rating else text,
                url=href or MOUTHSHUT_URL, created_at=created,
                engagement=_ms_int(card, "div[id$=divlike]"),   # "found helpful" likes
                view_count=_ms_int(card, "span.rated-view, div.views"),
                reply_count=0, lang="en"))
        except Exception:
            continue                # one malformed card never sinks the page
    print(f"  [{name}] {len(out)}")
    return out


# --------------------------------------------------------------- google maps branches
def _gm_ready(html):
    return 'role="article"' in html


_GM_REVIEW_BTN = re.compile(r"review", re.I)


def _gm_harvest(state, places):
    """page_action that first stashes the branch list, then opens the top `places` branches
    and stashes each place panel's HTML.

    The search results DOM carries no per-review text — only branch name + stars. The reviews
    exist one navigation deeper, in the place panel, so we drive the browser there. Everything
    past the branch list is best-effort: a branch that misbehaves is skipped and we still
    return the branch rows, which is why the list HTML is stashed BEFORE we navigate away
    rather than read off the final snapshot (which by then shows a place panel, not the list).
    """
    def action(page):
        state["list_html"] = ""
        state["places"].clear()
        deadline = time.time() + STEALTH_READY_SECS
        while time.time() < deadline:
            try:
                html = page.content()
                if _gm_ready(html):
                    state["list_html"] = html
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        if not state["list_html"] or places <= 0:
            return page
        # Collect the place links up front and navigate to them directly. Clicking a card and
        # then going back looks more natural but is unreliable — the list re-renders and the
        # locator goes stale (observed: a 75s click timeout on the 2nd branch). The result
        # anchor also carries the branch name in aria-label, which the article div does not.
        try:
            links = page.locator('[role="article"] a[href*="/maps/place/"]')
            targets = [(links.nth(i).get_attribute("aria-label") or "",
                        links.nth(i).get_attribute("href") or "")
                       for i in range(min(places, links.count()))]
        except Exception:
            return page
        for branch, href in targets:
            if not href:
                continue
            try:
                page.goto(href)
                page.wait_for_timeout(3500)
                btn = page.get_by_role("button", name=_GM_REVIEW_BTN)
                if btn.count():
                    btn.first.click()
                    page.wait_for_timeout(3500)
                state["places"].append((branch, href, page.content()))
            except Exception:
                continue  # this branch's panel misbehaved; the others still count
        return page
    return action


def _gm_reviews(branch, place_url, html):
    """Parse the real, dated customer reviews out of one place panel."""
    rows = []
    try:
        nodes = Selector(html).css("[data-review-id][aria-label]")
    except Exception:
        return rows
    for n in nodes:
        rid = n.attrib.get("data-review-id") or ""
        author = (n.attrib.get("aria-label") or "").strip()
        body = n.css(".MyEned") or n.css(".wiI7pd")
        text = " ".join((body[0].get_all_text() or "").split()) if body else ""
        text = re.sub(r"\s*…?\s*(More|Read more)$", "", text).strip()
        if not rid or len(text) < 10:
            continue
        stars = n.css("[role=img][aria-label]")
        m = re.match(r"(\d)", stars[0].attrib.get("aria-label") or "") if stars else None
        rating = m.group(1) if m else ""
        created = _ago_iso(" ".join((n.get_all_text() or "").split()), first_only=True)
        head = f"{branch} — " if branch else ""
        rows.append(dict(
            source_id="gmaps:rev:" + re.sub(r"[^A-Za-z0-9]+", "", rid)[:32],
            source="gmaps", author=author, author_name=author,
            text=(f"[{rating}★] {head}{text}" if rating else f"{head}{text}"),
            url=place_url or STEALTH_GMAPS_URL, created_at=created,
            engagement=0, reply_count=0, lang="en"))
    return rows


def fetch_gmaps_branches():
    name = "gmaps_web"
    if not STEALTH_SOURCES:
        print(f"  [{name}] skipped (set STEALTH_SOURCES=1)")
        return []
    state = {"list_html": "", "places": []}
    r, body = _stealth_fetch(
        STEALTH_GMAPS_URL, marker=_gm_ready, wait=4000, network_idle=True,
        action=_gm_harvest(state, STEALTH_GMAPS_PLACES),
        accept=lambda _b: bool(state["list_html"]))
    if r is None:
        print(f"  [{name}] {body}")
        return []
    try:
        cards = Selector(state["list_html"]).css("[role=article]")
    except Exception as e:
        print(f"  [{name}] parse error: {str(e)[:90]}")
        return []

    out, seen = [], set()
    for card in cards[:STEALTH_MAX_ITEMS]:
        try:
            raw = " ".join((card.get_all_text() or "").split())
            # The card repeats the branch name; collapse "Name Name 3.7 Bank ·" to one.
            m = _GMAPS_CARD_RE.match(raw)
            if not m:
                continue
            blob, rating = m.group(1), m.group(2)
            # de-duplicate the doubled name: "Axis Bank - X Axis Bank - X" -> "Axis Bank - X"
            half = blob[: len(blob) // 2].strip()
            branch = half if half and blob.startswith(half) else blob
            if not BRAND_RE.search(branch) or branch in seen:
                continue
            seen.add(branch)
            out.append(dict(
                source_id=f"gmaps:{re.sub(r'[^a-z0-9]+', '-', branch.lower()).strip('-')}",
                source="gmaps", author="",
                text=f"[{rating}★] {branch} — branch rating {rating}/5 on Google Maps",
                url=STEALTH_GMAPS_URL, created_at="", engagement=0, reply_count=0, lang="en"))
        except Exception:
            continue

    # Per-review text + dates from the branches we opened (best effort; the branch-rating
    # rows above stand on their own if this yielded nothing).
    ids = {row["source_id"] for row in out}
    for branch, place_url, html in state["places"]:
        for row in _gm_reviews(branch, place_url, html):
            if len(out) >= STEALTH_MAX_ITEMS:
                break
            if row["source_id"] in ids or not BRAND_RE.search(f"{branch} {row['text']}"):
                continue
            ids.add(row["source_id"])
            out.append(row)
    print(f"  [{name}] {len(out)}")
    return out


if __name__ == "__main__":
    # Force-enable for a standalone smoke run. This rebinds the module global the
    # fetchers read at call time (they run in this same module's namespace).
    STEALTH_SOURCES = True
    for fn in (fetch_trustpilot, fetch_mouthshut, fetch_gmaps_branches):
        for row in fn()[:3]:
            print(f"     {row['source_id']:40} {row['created_at'][:19]:19} "
                  f"{row['text'][:70]!r}")
