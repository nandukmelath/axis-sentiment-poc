"""X / Twitter ingestion — CSV import of an operator-supplied export.

THIS MODULE HAS NO LIVE ACQUISITION PATH. The free Nitter scraper (`_scrape`) is dead
upstream — every instance ntscraper knows about fails its health check, so `_scrape()`
returns 0 rows and is kept only as a stub. The ToS-clean live X source in this repo is
fetch/scrapebadger.py (needs SCRAPEBADGER_API_KEY + credits); prefer it.

What this module does: imports tweets an operator exported themselves into
fetch/twitter_import.csv. It is NOT a data generator. The file shipped in the repo is a
blank template with zero data rows, and the module actively REFUSES placeholder rows so
fabricated sentiment can never reach raw_posts, the scorer, or the newsroom reach panel
(dashboard/newsroom.py REACH_SOURCES).

A row is imported only if it can be traced back to a real tweet:
  * it resolves to an X status id of >= _MIN_ID_DIGITS digits (via the `url` column or an
    `id`/`tweet_id`/`status_id` column). Real X ids are snowflake ids — 11 digits since
    2010, 19 today. Hand-written demo rows use 1, 2, 3.
  * its text is not one of the demo texts this repo used to ship (_DEMO_TEXT_HASHES).
  * it mentions the brand — webutil.brand_match OR an Axis handle/hashtag (see _on_brand;
    bare brand_match misses "@AxisBank", which is how most X mentions are written).
Rejected rows are counted and explained, never silently dropped.

CSV columns (header required, order free; extra columns ignored; leading `#` lines are
treated as comments):
    text | author | url | created_at | engagement
  aliases accepted: full_text/content, username/screen_name/handle, tweet_url/link,
  id/tweet_id/status_id, date/timestamp, likes/favorite_count/favorites
Point at a different file with the TWITTER_CSV env var.

Env:
  TWITTER_CSV              path to the export (default fetch/twitter_import.csv)
  TWITTER_ALLOW_DEMO_ROWS  "1" to deliberately re-enable the canned demo rows for a
                           scripted walkthrough. Default off. When on, the import is
                           labelled DEMO DATA on stdout so nobody mistakes it for real.

Mode via config.TWITTER_MODE (env TWITTER_MODE):
  csv    -> import the CSV only (default)
  scrape -> Nitter only (dead upstream; returns 0)
  auto   -> scrape, then fall back to CSV
"""
import os, csv, re, hashlib, datetime
from config import TWITTER_QUERIES, TWITTER_MODE, FETCH_LIMITS
from fetch.webutil import brand_match

CSV_PATH = os.getenv("TWITTER_CSV", os.path.join(os.path.dirname(__file__), "twitter_import.csv"))

# Opt-in escape hatch for a scripted demo. Default-deny.
ALLOW_DEMO = os.getenv("TWITTER_ALLOW_DEMO_ROWS", "0").strip().lower() in ("1", "true", "yes")

# Real X status ids are snowflake ids: ~11 digits from June 2010, 19 digits today. Anything
# shorter is either pre-2010 (irrelevant to a current brand-sentiment run) or hand-written.
_MIN_ID_DIGITS = 10

# sha256(normalised text)[:16] of the three synthetic rows this repo shipped as a fixture.
# Pinned so the demo sentiment stays refused even if someone restores the old file, renumbers
# its ids to look real, or pastes those rows into an otherwise-genuine export.
_DEMO_TEXT_HASHES = {
    "24c9978a5c93f542",   # "Axis Bank UPI failed thrice today ..."      (was x:1, engagement 180)
    "6ea4b7d105562570",   # "Kudos to Axis Bank, my credit card ..."     (was x:2, engagement 95)
    "e46ce18c9b24d329",   # "Beware: fake Axis Bank helpline number ..." (was x:3, engagement 320)
}

_STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)", re.IGNORECASE)
_DIGITS_RE = re.compile(r"^\d+$")

# webutil.brand_match is `\baxis\b`, which does NOT match "@AxisBank" / "#AxisBank" — the word
# boundary fails on the "B". On X the handle IS the dominant mention form: measured against the
# 887 genuine X rows already in raw_posts, brand_match alone keeps 171 and would wrongly drop
# 702 (79%). So the X brand filter is brand_match OR an Axis handle/hashtag/compound.
_X_BRAND_RE = re.compile(r"(?:[@#]axis\w*)|(?:\baxis\s*bank\b)", re.IGNORECASE)


def _on_brand(text):
    return brand_match(text) or bool(_X_BRAND_RE.search(text or ""))


# NB: conversation_id is deliberately NOT an alias for tweet_id — it is the thread ROOT's id,
# so using it would collapse a whole thread onto one source_id and build the wrong URL.
_ALIASES = {
    "text":       ("text", "full_text", "content", "tweet", "body"),
    "author":     ("author", "username", "screen_name", "handle", "user", "user_name"),
    "url":        ("url", "tweet_url", "link", "permalink"),
    "tweet_id":   ("tweet_id", "id", "status_id", "id_str"),
    "created_at": ("created_at", "date", "timestamp", "time", "datetime"),
    "engagement": ("engagement", "likes", "favorite_count", "favorites", "like_count"),
}


def _help():
    return (
        f"  [twitter] {CSV_PATH} holds no usable rows — X contributes nothing this run.\n"
        "  [twitter] To supply real X data, either:\n"
        "  [twitter]   (A) PREFERRED — top up ScrapeBadger credits and set SCRAPEBADGER_API_KEY in .env;\n"
        "  [twitter]       fetch/scrapebadger.py is the ToS-clean live X API source and needs no CSV.\n"
        "  [twitter]   (B) Export tweets yourself (X 'Your data' archive, X API, or a licensed\n"
        "  [twitter]       provider) and save them as CSV with a header row:\n"
        "  [twitter]         text,author,url,created_at,engagement\n"
        "  [twitter]       Every row needs a real https://x.com/<user>/status/<id> URL (or an id\n"
        "  [twitter]       column) — rows without a traceable tweet id are refused on purpose.\n"
        "  [twitter]       Overwrite that file, or point TWITTER_CSV at your own export."
    )


def _sid(url, text):
    """Stable id so re-importing the same tweet doesn't create duplicates."""
    seg = (url or "").rstrip("/").split("/")[-1]
    if seg.isdigit():
        return f"x:{seg}"
    return "x:" + hashlib.md5((text or url or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _text_hash(s):
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]


def _pick(row, field):
    """Read a logical field from a CSV row, tolerating common export column names."""
    for name in _ALIASES[field]:
        for key, val in row.items():
            if key and key.strip().lower() == name and (val or "").strip():
                return val.strip()
    return ""


def _iso(s):
    """Normalise a timestamp to ISO 8601 UTC. Naive input is assumed UTC. Junk -> ""."""
    s = (s or "").strip()
    if not s:
        return ""
    if _DIGITS_RE.match(s) and len(s) in (10, 13):          # epoch seconds / millis
        try:
            ts = int(s) / (1000 if len(s) == 13 else 1)
            return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    for parse in (
        lambda v: datetime.datetime.fromisoformat(v.replace("Z", "+00:00")),
        lambda v: datetime.datetime.strptime(v, "%a %b %d %H:%M:%S %z %Y"),   # X RFC-822-ish
        lambda v: datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),
        lambda v: datetime.datetime.strptime(v, "%Y-%m-%d"),
    ):
        try:
            dt = parse(s)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    return ""


def _status_id(url, explicit_id):
    """Return the tweet's numeric status id, or "" if it can't be traced to a real tweet."""
    if explicit_id and _DIGITS_RE.match(explicit_id):
        return explicit_id
    m = _STATUS_RE.search(url or "")
    return m.group(1) if m else ""


def _int(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _read_rows(path):
    """Return dict rows, skipping a leading block of `#` comment lines above the header."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()
    start = 0
    for i, ln in enumerate(lines):                  # only a LEADING comment block, so a real
        if ln.strip() and not ln.lstrip().startswith("#"):   # tweet starting with #hashtag
            start = i                                        # is never mistaken for a comment
            break
    else:
        return []
    return list(csv.DictReader(lines[start:]))


def _from_csv():
    if not os.path.exists(CSV_PATH):
        print(f"  [twitter] 0 — no CSV at {CSV_PATH}")
        print(_help())
        return []
    try:
        raw = _read_rows(CSV_PATH)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        print(f"  [twitter] 0 — cannot read {os.path.basename(CSV_PATH)}: {str(e)[:100]}")
        return []

    rows, seen = [], set()
    n_demo = n_untraceable = n_offbrand = n_empty = 0
    cap = FETCH_LIMITS.get("twitter", 30)

    for r in raw:
        if not isinstance(r, dict):
            continue
        text = _pick(r, "text")
        if not text:
            n_empty += 1
            continue
        if _text_hash(text) in _DEMO_TEXT_HASHES and not ALLOW_DEMO:
            n_demo += 1
            continue
        url = _pick(r, "url")
        tid = _status_id(url, _pick(r, "tweet_id"))
        if (not tid or len(tid) < _MIN_ID_DIGITS) and not ALLOW_DEMO:
            n_untraceable += 1
            continue
        if not _on_brand(text):
            n_offbrand += 1
            continue
        author = _pick(r, "author")
        if author and not author.startswith("@"):
            author = "@" + author
        if tid and not url:
            url = f"https://x.com/{author.lstrip('@') or 'i'}/status/{tid}"
        sid = f"x:{tid}" if tid else _sid(url, text)
        if sid in seen:
            continue
        seen.add(sid)
        rows.append(dict(
            source_id=sid, source="twitter", author=author, text=text, url=url,
            created_at=_iso(_pick(r, "created_at")),
            engagement=_int(_pick(r, "engagement")), lang="en"))
        if len(rows) >= cap:
            break

    dropped = []
    if n_demo:
        dropped.append(f"{n_demo} demo/placeholder")
    if n_untraceable:
        dropped.append(f"{n_untraceable} with no real tweet id")
    if n_offbrand:
        dropped.append(f"{n_offbrand} off-brand")
    if n_empty:
        dropped.append(f"{n_empty} empty")
    suffix = f" ({os.path.basename(CSV_PATH)}"
    suffix += f"; dropped {', '.join(dropped)})" if dropped else ")"
    print(f"  [twitter] {len(rows)} imported from CSV{suffix}")

    if rows and ALLOW_DEMO:
        print("  [twitter] !! TWITTER_ALLOW_DEMO_ROWS=1 — these rows are DEMO DATA, not real "
              "customer sentiment. Do not report them.")
    if not rows:
        if n_demo or n_untraceable:
            print("  [twitter] every row was placeholder/untraceable — this is the repo's demo "
                  "fixture, not a real export.")
        print(_help())
    return rows


def _scrape():
    """Dead upstream: ntscraper's Nitter instances all fail health-check. Kept as a stub."""
    rows = []
    try:
        from ntscraper import Nitter
    except ImportError:
        print("  [twitter] ntscraper not installed")
        return rows
    try:
        n = Nitter(log_level=0)
    except Exception as e:
        print(f"  [twitter] no working Nitter instance: {str(e)[:80]}")
        return rows
    for q in TWITTER_QUERIES:
        try:
            res = n.get_tweets(q, mode="term", number=FETCH_LIMITS["twitter"])
            for t in res.get("tweets", []):
                link = t.get("link", "") or ""
                text = t.get("text", "") or ""
                tid = _status_id(link, "")
                if not tid or len(tid) < _MIN_ID_DIGITS or not _on_brand(text):
                    continue
                stats = t.get("stats", {}) or {}
                rows.append(dict(
                    source_id=f"x:{tid}", source="twitter",
                    author=(t.get("user", {}) or {}).get("username", ""),
                    text=text, url=link, created_at=_iso(t.get("date", "")),
                    engagement=int(stats.get("likes", 0) or 0), lang="en"))
        except Exception as e:
            print(f"  [twitter] query '{q}' failed: {str(e)[:80]}")
    if not rows:
        print("  [twitter] 0 from Nitter (all instances dead — use fetch/scrapebadger.py)")
    return rows[:FETCH_LIMITS.get("twitter", 30)]


def fetch():
    """Return X rows from the operator's CSV export. Never raises; [] on any failure."""
    try:
        mode = (TWITTER_MODE or "csv").lower()
        if mode == "csv":
            return _from_csv()
        if mode == "scrape":
            return _scrape()
        rows = _scrape()                      # auto
        return rows if rows else _from_csv()
    except Exception as e:                    # contract: never raise
        print(f"  [twitter] 0 — unexpected error: {type(e).__name__}: {str(e)[:100]}")
        return []
