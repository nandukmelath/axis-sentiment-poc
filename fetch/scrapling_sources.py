"""Free Scrapling-backed sources — no API keys, no ScrapeBadger credits.

Three sources, each chosen because it clears a bar the existing fetchers don't:

  consumercomplaints  the codebase writes this off as "Cloudflare-walled (would need
                      a real browser - not worth it)". That is stale: it returns 200
                      to a plain TLS-impersonated GET and yields 25 fully-parsed
                      complaints per page. Real Indian customer complaints, the
                      closest public analogue to the bank's own grievance desk.
  valuepickr          a Discourse forum, so it exposes /search.json - structured
                      JSON, no HTML scraping and nothing to break on a redesign.
                      Investor/analyst discussion rather than retail complaints.
  businessstandard    documented in the codebase as behind "a hard 403 bot wall",
                      and it still is: plain requests gets 403 on every attempt,
                      while curl_cffi's TLS impersonation gets 200. This is the one
                      source here that genuinely requires Scrapling.

All three return the standard raw_posts record shape used by fetch/run_fetch.py.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

# Scrapling 0.4.12 hardcodes a Chrome 149 fingerprint that the installed
# browserforge dataset (max Chrome 143) cannot build headers for, so importing a
# fetcher raises ValueError. Clamp it before that import. Same workaround as
# tools/scrapling_mcp.py - see the long note there. Upstream issue #396.
import scrapling.engines.toolbelt.fingerprints as _fp

if _fp.chromium_version > 143:
    _fp.chromium_version = 143
if _fp.chrome_version > 143:
    _fp.chrome_version = 143

from scrapling.fetchers import Fetcher  # noqa: E402  (must follow the patch)

from config import (AMBITIONBOX_MAX_ITEMS, AMBITIONBOX_PAGES,  # noqa: E402
                    AMBITIONBOX_SLEEP, AMBITIONBOX_URL, FETCH_LIMITS)

UA_PROFILE = "chrome"
BRAND_RE = re.compile(r"\baxis\b", re.IGNORECASE)


def _get(url, timeout=30):
    """One TLS-impersonated GET. Returns a Selector-backed Response, or None."""
    return Fetcher.get(url, impersonate=UA_PROFILE, timeout=timeout, retries=1)


def _txt(el):
    return " ".join((el.get_all_text() or "").split()) if el is not None else ""


def _iso(s):
    """Best-effort date parse. Returns '' rather than inventing a timestamp -
    run_fetch drops undated rows when a --window is set, which is the correct
    behaviour; a fabricated date would silently pass the window filter."""
    s = (s or "").strip()
    if not s:
        return ""
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return s


# --------------------------------------------------------------- consumercomplaints
CC_URL = "https://www.consumercomplaints.in/?search=axis+bank"
# The site now renders most search hits as company-aggregated anchors
# (/bycompany/axis-bank-...html#get-cl<id>) instead of per-complaint permalinks
# (/<slug>-c<id>). Both carry the same stable complaint id, so accept either.
CC_ID_RE = re.compile(r"(?:-c|#get-cl)(\d+)\b")


def fetch_consumercomplaints():
    """Parse the Axis search results page into individual complaints.

    Structure (verified live): each complaint is a `div.complaint-box-results`
    carrying a `__title` (anchored to the permalink, whose `-c<id>` suffix is a
    stable primary key), a `__text` body, and two `__info-item` spans holding the
    author and the date.
    """
    name = "consumercomplaints"
    limit = FETCH_LIMITS.get(name, 30)
    try:
        r = _get(CC_URL)
        if r.status != 200:
            print(f"  [{name}] HTTP {r.status}")
            return []
    except Exception as e:
        print(f"  [{name}] error: {str(e)[:90]}")
        return []

    out = []
    for box in r.css("div.complaint-box-results")[:limit]:
        # The title IS the anchor (a.complaint-box-results__title), not a child of it,
        # and its `-c<id>` suffix is the stable primary key.
        href = box.css("a.complaint-box-results__title::attr(href)").get() or ""
        m = CC_ID_RE.search(href)
        if not m:
            continue                      # no stable id -> can't dedupe, skip
        title = _txt(box.css("a.complaint-box-results__title").first)
        # Two __text spans per box: [0] is the literal "(complaint)" label, [1] the body.
        texts = [_txt(t) for t in box.css(".complaint-box-results__text")]
        body = next((t for t in texts if len(t) > 25), "")
        text = f"{title}. {body}".strip(". ").strip()
        if not text or not BRAND_RE.search(text):
            continue
        out.append(dict(
            source_id=f"consumercomplaints:{m.group(1)}", source="consumercomplaints",
            author=_txt(box.css(".author-box__user_bold").first),
            text=text,
            url=f"https://www.consumercomplaints.in{href}",
            created_at=_iso(_txt(box.css(".author-box__date").first)),
            engagement=0, reply_count=0, lang="en"))
    print(f"  [{name}] {len(out)}")
    return out


# --------------------------------------------------------------- valuepickr
# order:latest must live INSIDE q — Discourse ignores it as a URL param. Without it the
# default relevance ordering returns a frozen, historically-skewed set (median ~2017) that
# new forum posts never enter.
VP_URL = "https://forum.valuepickr.com/search.json?q=axis%20bank%20order%3Alatest"


def fetch_valuepickr():
    """Discourse exposes search as JSON, so this needs no HTML parsing at all."""
    name = "valuepickr"
    limit = FETCH_LIMITS.get(name, 30)
    try:
        r = _get(VP_URL)
        data = r.json() if r.status == 200 else {}
    except Exception as e:
        print(f"  [{name}] error: {str(e)[:90]}")
        return []

    topics = {t.get("id"): t for t in (data.get("topics") or [])}
    out = []
    for p in (data.get("posts") or [])[:limit]:
        blurb = " ".join((p.get("blurb") or "").split())
        topic = topics.get(p.get("topic_id"), {})
        title = topic.get("title", "")
        text = f"{title}. {blurb}".strip(". ").strip()
        if not text or not BRAND_RE.search(text):
            continue
        slug = topic.get("slug") or "t"
        out.append(dict(
            source_id=f"valuepickr:{p.get('id')}", source="valuepickr",
            author=p.get("username") or p.get("name") or "",
            text=text,
            url=f"https://forum.valuepickr.com/t/{slug}/{p.get('topic_id')}/{p.get('post_number', 1)}",
            created_at=p.get("created_at") or "",
            engagement=int(p.get("like_count") or 0), reply_count=0, lang="en"))
    print(f"  [{name}] {len(out)}")
    return out


# --------------------------------------------------------------- business-standard
BS_URL = "https://www.business-standard.com/topic/axis-bank"

# Every BS permalink ends in a 12-digit story id shaped 1 + YY + MM + DD + seq, e.g.
# ...-126072100140_1.html -> 1|26|07|21|00140 -> 21 Jul 2026. It is both the article's
# true primary key and a publish date of last resort. Verified live against the
# rendered card stamps: the two agree on every row.
BS_ID_RE = re.compile(r"-(1\d{11})(?:_\d+)?\.html")
# Rendered card stamp: "Updated On : 21 Jul 2026 | 11:13 AM IST" (time is optional).
BS_STAMP_RE = re.compile(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
                         r"(?:\s*\|\s*(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]))?")
IST = timezone(timedelta(hours=5, minutes=30))


def _bs_story_id(href):
    """The 12-digit story id ending a BS permalink, or '' if the URL carries none."""
    m = BS_ID_RE.search(href or "")
    return m.group(1) if m else ""


def _bs_sid(href):
    """Stable, collision-free key for one article.

    The story id when present. Otherwise a digest of the URL *path* — the old code
    used the slug's trailing segment truncated to 90 chars, which sheared the id off
    the end of most URLs and let two different articles share one source_id.
    """
    sid = _bs_story_id(href)
    if sid:
        return sid
    path = urlsplit(href or "").path.rstrip("/")     # drop query/fragment so ?utm=… dedupes
    return "u" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16] if path else ""


def _bs_iso_from_stamp(s):
    """'Updated On : 21 Jul 2026 | 11:13 AM IST' -> ISO 8601 UTC.

    The stamp is IST wall-clock, so it is shifted to UTC. A stamp with no clock is
    read as midnight UTC on that date, matching _iso()'s date-only convention.
    """
    m = BS_STAMP_RE.search(s or "")
    if not m:
        return ""
    date_s, clock = m.group(1), m.group(2)
    if clock:
        clock = re.sub(r"\s+|\.", "", clock).upper()
        for fmt in ("%d %b %Y %I:%M%p", "%d %B %Y %I:%M%p"):
            try:
                dt = datetime.strptime(f"{date_s} {clock}", fmt).replace(tzinfo=IST)
                return dt.astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return ""


def _bs_iso_from_id(sid):
    """Decode the date baked into a story id: '126072100140' -> 2026-07-21 (UTC midnight).

    Returns '' unless the digits form a real, plausible calendar date, so a numeric id
    that is not date-shaped can never fabricate a timestamp.
    """
    if len(sid) != 12 or not sid.isdigit() or sid[0] != "1":
        return ""
    try:
        dt = datetime(2000 + int(sid[1:3]), int(sid[3:5]), int(sid[5:7]), tzinfo=timezone.utc)
    except ValueError:                       # month 13, day 32, ... -> not a date
        return ""
    if not 2000 <= dt.year <= datetime.now(timezone.utc).year + 1:
        return ""
    return dt.isoformat()


def fetch_businessstandard():
    """Topic listing page. Plain requests gets 403 here; TLS impersonation gets 200.

    Each listing card (`div.cardlist`) holds the headline anchor, a summary paragraph
    and an "Updated On : 21 Jul 2026 | 11:13 AM IST" stamp. The stamp is the primary
    date; when a card lacks one the date is decoded from the story id in the permalink.
    Rows that resolve to no date at all are dropped rather than emitted undated —
    run_fetch._within discards undated rows on every windowed run anyway, so an
    undated row is invisible work, and the skip count below makes that visible.
    """
    name = "businessstandard"
    limit = FETCH_LIMITS.get(name, 30)
    try:
        r = _get(BS_URL)
        if r.status != 200:
            print(f"  [{name}] HTTP {r.status}")
            return []
    except Exception as e:
        print(f"  [{name}] error: {str(e)[:90]}")
        return []

    seen, out, undated = set(), [], 0

    def _add(href, title, blurb, stamp):
        nonlocal undated
        if len(out) >= limit or "business-standard.com" not in href:
            return
        text = f"{title}. {blurb}".strip(". ").strip()
        if len(title) < 25 or not BRAND_RE.search(text):
            return
        sid = _bs_sid(href)
        if not sid or sid in seen:
            return
        created_at = _bs_iso_from_stamp(stamp) or _bs_iso_from_id(_bs_story_id(href))
        if not created_at:
            undated += 1
            return
        seen.add(sid)
        out.append(dict(
            source_id=f"businessstandard:{sid}", source="businessstandard",
            author="Business Standard", text=text, url=href,
            created_at=created_at, engagement=0, reply_count=0, lang="en"))

    try:
        for card in r.css("div.cardlist"):
            href = card.css("a.smallcard-title::attr(href)").get() or ""
            if href:
                _add(href, _txt(card.css("a.smallcard-title").first),
                     _txt(card.css("p.bookreview-title").first), _txt(card.css(".updt-on").first))

        # The card classes are Next.js-hashed and can move on a rebuild, so also sweep bare
        # article anchors. These carry no stamp — the story id in the URL supplies the date.
        if len(out) < limit:
            for a in r.css("a"):
                href = a.attrib.get("href") or ""
                if href.count("/") >= 5:
                    _add(href, _txt(a), "", "")
    except Exception as e:                   # a redesign must degrade, never crash the run
        print(f"  [{name}] parse error: {str(e)[:90]}")

    print(f"  [{name}] {len(out)}" + (f" ({undated} undated, skipped)" if undated else ""))
    return out


# --------------------------------------------------------------- ambitionbox
# EMPLOYEE reviews (India's Glassdoor, Naukri-owned). Glassdoor/Indeed/Quora all hard-403 a
# plain GET; this one returns 200 and ships the whole review list in __NEXT_DATA__, so there
# is no HTML scraping, no browser and no AI-extraction step here — just JSON off the page.
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# AmbitionBox prints wall-clock IST with no offset ('2026-05-10 14:06:46'). Same treatment as
# MouthShut in scrapling_stealth.py — read it as IST, store UTC, so these rows sort against
# every other source instead of drifting 5.5h into the future.
_AB_IST = timezone(timedelta(hours=5, minutes=30))


def _ab_iso(s):
    """'2026-05-10 14:06:46' (IST, no offset) -> ISO 8601 UTC. '' when unparseable."""
    try:
        dt = datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""
    return dt.replace(tzinfo=_AB_IST).astimezone(timezone.utc).isoformat()


def _ab_int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _ab_reviews(html):
    """Pull pageProps.reviewsData out of __NEXT_DATA__. [] on any shape change."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return []
    try:
        pp = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError, TypeError):
        return []
    rows = pp.get("reviewsData")
    return rows if isinstance(rows, list) else []


def fetch_ambitionbox():
    """Axis Bank employee reviews — pros/cons prose plus a 1-5 overall star rating.

    Paginates `?page=N`. The site pins one featured review to the top of every page, so the
    id-dedup below is load-bearing, not belt-and-braces.
    """
    name = "ambitionbox"
    limit = AMBITIONBOX_MAX_ITEMS
    out, seen = [], set()
    for page in range(1, max(1, AMBITIONBOX_PAGES) + 1):
        if len(out) >= limit:
            break
        url = AMBITIONBOX_URL if page == 1 else f"{AMBITIONBOX_URL}?page={page}"
        try:
            r = _get(url)
        except Exception as e:               # network/TLS — degrade, never crash the run
            print(f"  [{name}] error on page {page}: {str(e)[:80]}")
            break
        if r is None or getattr(r, "status", 0) != 200:
            print(f"  [{name}] HTTP {getattr(r, 'status', '?')} on page {page}")
            break
        reviews = _ab_reviews(getattr(r, "body", b"").decode("utf-8", "ignore")
                              if isinstance(getattr(r, "body", None), bytes) else str(r))
        if not reviews:
            print(f"  [{name}] no reviewsData on page {page} (layout change?)")
            break
        for rv in reviews:
            if len(out) >= limit:
                break
            try:
                rid = rv.get("id")
                if not rid or rid in seen:
                    continue
                # Brand gate at the page level, as with Trustpilot: this is Axis Bank's own
                # employer page, so a per-row keyword filter would bin the many real reviews
                # that never spell the employer's name ("management is rude, targets are mad").
                if not BRAND_RE.search(str(rv.get("companyName") or "")):
                    continue
                pros = (rv.get("likesText") or "").strip()
                cons = (rv.get("disLikesText") or "").strip()
                if not pros and not cons:
                    continue                 # ratings-only review — no text to score
                # reviewTitle is the role+location line ("rated by a Branch Manager in Pune"),
                # which is what makes an employee review actionable for the bank.
                parts = [(rv.get("reviewTitle") or "").strip().rstrip(".")]
                if pros:
                    parts.append(f"Pros: {pros}")
                if cons:
                    parts.append(f"Cons: {cons}")
                text = ". ".join(p for p in parts if p).strip()
                stars = _ab_int(rv.get("overallCompanyRating"))
                href = rv.get("url") or ""
                out.append(dict(
                    source_id=f"ambitionbox:{rid}", source="ambitionbox",
                    author=rv.get("userName") or "Anonymous",
                    text=f"[{stars}★] {text}" if stars else text,
                    url=(f"https://www.ambitionbox.com{href}" if href.startswith("/") else
                         href or AMBITIONBOX_URL),
                    created_at=_ab_iso(rv.get("created")),
                    engagement=_ab_int(rv.get("helpfulCount")),
                    reply_count=1 if rv.get("isEmployerResponded") else 0,
                    lang="en"))
                seen.add(rid)
            except Exception:
                continue                     # one odd review never costs us the other twenty
        time.sleep(AMBITIONBOX_SLEEP)        # be a polite guest on a free source
    print(f"  [{name}] {len(out)}")
    return out


if __name__ == "__main__":
    for fn in (fetch_consumercomplaints, fetch_valuepickr, fetch_businessstandard,
               fetch_ambitionbox):
        rows = fn()
        for row in rows[:2]:
            print(f"     {row['source_id']:34} {row['text'][:88]!r}")
