"""Mastodon public hashtag timelines — FREE, NO AUTH. Pulls posts tagged #AxisBank etc.

Two things this module has to get right, both learned the hard way:

1. BRAND MATCHING. The shared ``fetch.webutil.BRAND_RE`` is ``\\baxis\\b``. That pattern cannot
   match "AxisBank" (the word boundary fails before the 'B') — i.e. it dropped the exact literal
   this source is built on — while happily passing "x axis of the graph", "Axis Records" and
   WWII "Axis powers" memes. webutil is shared with other sources so it is NOT ours to change;
   instead this module carries its own matcher (``_brand_match``) tuned to Axis Bank.

2. COVERAGE. A Mastodon tag timeline is *per instance* — it only shows statuses that particular
   server has federated in. mastodon.social alone holds ~11 #AxisBank statuses in its entire
   history, so it is a thin vein on its own. We therefore fan out across several public
   instances and union the results on the canonical ActivityPub ``uri``.

API gotchas verified live against mastodon.social / mas.to / social.vivaldi.net:
  * ``any[]`` lets one request cover several tags, but Mastodon honours at most FOUR tags total
    (the primary in the path + 3 in ``any[]``); extras are silently ignored. Hence _TAG_CHUNK=4.
  * The primary tag in the path must itself be a tag the instance knows, otherwise the response
    is an empty list even when ``any[]`` names a live tag. So every chunk must START with a tag
    that actually exists — keep the known-live tags at the front of the tag list.
  * Many instances answer 422/401/410 for anonymous tag timelines (public preview disabled).
    Those are skipped quietly; only a total wipe-out is worth a log line.
"""
import concurrent.futures as cf
import hashlib
import html
import os
import re
from urllib.parse import urlparse

import requests

from config import MASTODON_INSTANCE, MASTODON_TAGS, FETCH_LIMITS
from fetch.webutil import HEADERS

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

# ---------------------------------------------------------------- brand matching (source-local)
# STRONG: unambiguous Axis Bank forms. "axis[\s._-]*bank" covers "Axis Bank", "AxisBank",
# "axisbank", "axis-bank" and prefix forms like "AxisBankLtd"/"AxisBankIndia". The rest are
# Axis Bank products, subsidiaries and support handles.
_BRAND_STRONG = re.compile(r"""
      \baxis[\s._-]*bank                                  # axis bank / axisbank / AxisBankLtd
    | \baxis[\s._-]*(?: magnus | burgundy | atlas | myzone | vistara | flipkart | indianoil
                      | privee | olympus | neo | ace | rewards | aura | samsung | airtel )\b
    | \baxis[\s._-]*(?: mobile | direct | securities | finance | amc | mutual\s*fund
                      | max\s*life | maxlife )\b
    | \baxis[\s._-]*(?: credit | debit )[\s._-]*card
    | \baxis[\s._-]*(?: net[\s._-]*banking | netbanking | internet\s*banking | upi | atm
                      | ifsc | branch | passbook | cheque | fastag )\b
    | @axis(?: bank | direct | mobile | max | securities | support )\w*                # handles
""", re.IGNORECASE | re.VERBOSE)

# WEAK: a bare "axis" only counts when the post is clearly about banking/markets.
_AXIS_BARE = re.compile(r"\baxis\b", re.IGNORECASE)
_FIN_CTX = re.compile(r"""
    \b(?: bank | banks | banking | banknifty | nifty | sensex | nse | bse | rbi | upi | neft
        | imps | rtgs | ifsc | atm | netbanking | kyc | ombudsman | passbook | demat | npa
        | casa | mclr | fastag | emi | cheque | overdraft | fintech | brokerage )\b
  | \b(?: credit | debit )\s*card\b
  | \b(?: savings | current | salary )\s*account\b
  | \bfixed\s*deposit\b
""", re.IGNORECASE | re.VERBOSE)

# DENY: the false-positive families the shared BRAND_RE kept letting through. Vetoes anything
# that has not already matched _BRAND_STRONG.
_OFF_BRAND = re.compile(r"""
      \b[xyz]\s*[-‐-―]?\s*axis\b                        # x axis / y-axis / z axis
    | \baxis\s*[-‐-―]?\s*(?: label | labels | title | titles | tick | ticks
                                     | range | ranges | scale | scales | limit | limits
                                     | break | breaks | text ) \b
    | \b(?: horizontal | vertical | time | value | category | categorical | numeric | numerical
          | graph | chart | plot | coordinate | cartesian | polar | radial | principal | major
          | minor | semi[-\s]?major | semi[-\s]?minor | rotational | rotation | optical | neutral
          | central | longitudinal | transverse | magnetic | earth'?s | tilted | secondary
          | dual | shared | log | logarithmic )\s*[-‐-―]?\s*axis\b
    | \baxis\s+of\s+(?: evil | rotation | symmetry | the | this | that | a | an | resistance )\b
    | \baxis\s+powers?\b
    | \baxis\s+(?: records | record | communications | communication | entertainment
                 | camera | cameras | dental | bio | neuro | neuroscience | therapeutics | mundi
                 | alliance | deer | font | fonts | studios | games | vfx )\b
    | \b(?: hypothalamic | pituitary | adrenal | hpa | gut[-\s]?brain | thyroid )[-\s]*axis\b
    | \b(?: world\s*war\s*(?: ii | 2 | two ) | wwii | ww2 | nazi | nazis | hitler | mussolini
          | third\s+reich | allied\s+powers | wehrmacht )\b
""", re.IGNORECASE | re.VERBOSE)

# Hashtags that are themselves an on-brand signal (a post can be tagged #AxisBank while its body
# is entirely in Tamil/Hindi with no Latin "Axis Bank" string to regex against).
_BRAND_TAGS = {"axisbank", "axisbankltd", "axisbankindia", "axisbanklimited", "axisbanknews",
               "axismagnus", "axismobile", "axisdirect", "axismaxlife", "axisbanksupport",
               "axisbankcreditcard", "axisbankcard"}


def _brand_match(text, tags=()):
    """True when `text`/`tags` are about Axis Bank. Source-local on purpose — see module docstring.

    Order matters: an unambiguous brand form wins outright; everything softer (a brand hashtag,
    or a bare "axis" in a banking context) must first survive the off-brand veto.
    """
    t = text or ""
    if _BRAND_STRONG.search(t):
        return True
    if _OFF_BRAND.search(t):
        return False
    if {str(x).lower().lstrip("#") for x in (tags or ())} & _BRAND_TAGS:
        return True
    return bool(_AXIS_BARE.search(t) and _FIN_CTX.search(t))


# ---------------------------------------------------------------- coverage
_PRIMARY = (MASTODON_INSTANCE or "mastodon.social").strip().lower()

# Instances that actually federate Axis/Indian-banking content, ranked by unique statuses each
# one contributed in a live sweep on 2026-08-05 (35 unique statuses total; these carry 34 of
# them — the rest of the fediverse added nothing). Override with MASTODON_INSTANCES=a,b,c.
_DEFAULT_INSTANCES = ["mastodon.social", "mas.to", "social.vivaldi.net", "me.dm", "mastodon.au",
                      "c.im", "universeodon.com", "social.coop", "hostux.social",
                      "fediscience.org", "mathstodon.xyz", "mastodon.nz"]

# Extra tags unioned with config.MASTODON_TAGS. Front of the list must stay known-live because a
# dead primary tag zeroes out its whole chunk (see module docstring). #indianbankingnews and
# #bankinginindia are Indian-banking news-relay tags: broad, but _brand_match gates them, and
# they carried the freshest Axis item in the sweep.
_DEFAULT_EXTRA_TAGS = ["AxisBankLtd", "AxisBankIndia", "AxisMagnus",
                       "indianbankingnews", "bankinginindia", "AxisMobile", "AxisDirect"]

_TAG_CHUNK = 4          # Mastodon honours at most 4 tags per request (primary + 3 in any[])
_TIMEOUT = 20
# These instances answer slowly (4-7s per tag timeline), so the fan-out runs in a pool; workers
# >= len(instances) keeps the whole fetch to ~2 request waves instead of serialising ~20 calls.
_WORKERS = 12


def _cfg_list(env_name, cfg_value, default):
    """config.<NAME> if the orchestrator added it, else env CSV, else the built-in default."""
    if cfg_value:
        return [str(x).strip() for x in cfg_value if str(x).strip()]
    raw = os.getenv(env_name, "")
    if raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(default)


def _instances():
    try:
        from config import MASTODON_INSTANCES as cfg          # added centrally? use it
    except ImportError:
        cfg = None
    out = _cfg_list("MASTODON_INSTANCES", cfg, _DEFAULT_INSTANCES)
    return list(dict.fromkeys([_PRIMARY] + [i.strip().lower() for i in out if i.strip()]))


def _tags():
    base = [str(t).strip() for t in (MASTODON_TAGS or []) if str(t).strip()]
    extra = _cfg_list("MASTODON_TAGS_EXTRA", None, _DEFAULT_EXTRA_TAGS)
    return list(dict.fromkeys(base + extra)) or ["AxisBank"]


# ---------------------------------------------------------------- parsing helpers
def _text(status):
    """Status content -> plain text. Hashtag/mention anchors become ' #Tag ', entities unescaped."""
    parts = [status.get("spoiler_text") or "", TAG_RE.sub(" ", status.get("content") or "")]
    card = status.get("card") or {}
    if isinstance(card, dict):                       # link preview carries the actual headline
        parts += [card.get("title") or "", card.get("description") or ""]
    return WS_RE.sub(" ", html.unescape(" ".join(p for p in parts if p))).strip()


def _tag_names(status):
    return [t.get("name", "") for t in (status.get("tags") or []) if isinstance(t, dict)]


def _sid(status):
    """Stable, globally unique id from the canonical ActivityPub uri.

    Status ids in the API are *instance-local*, so the same post fetched from two instances would
    otherwise land as two rows. The uri (https://<origin>/users/<u>/statuses/<id>) is global.
    Posts originating on the primary instance keep the historical bare-id form so they still
    dedupe against rows already in the DB.
    """
    uri = status.get("uri") or status.get("url") or ""
    host = (urlparse(uri).netloc or _PRIMARY).lower()
    ident = urlparse(uri).path.rstrip("/").rsplit("/", 1)[-1]
    if not ident.isdigit():                       # bridged/non-Mastodon ids: hash the whole uri
        # A short, stable dedup key, not a security boundary — usedforsecurity=False
        # says exactly that to both bandit and anyone reading this later.
        ident = hashlib.sha1(uri.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:16] \
            if uri else str(status.get("id") or "")
    return f"mastodon:{ident}" if host == _PRIMARY else f"mastodon:{host}:{ident}"


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _row(status):
    acct = (status.get("account") or {}).get("acct", "")
    lang = status.get("language") or "en"
    return dict(
        source_id=_sid(status), source="mastodon",
        author=("@" + acct) if acct else "",
        author_name=(status.get("account") or {}).get("display_name"),
        text=_text(status), url=status.get("url") or status.get("uri") or "",
        created_at=status.get("created_at", ""),          # Mastodon already emits ISO 8601 UTC
        engagement=_int(status.get("favourites_count")),
        reply_count=_int(status.get("replies_count")),
        retweet_count=_int(status.get("reblogs_count")),
        lang="en" if lang in ("und", "unknown") else lang)


# ---------------------------------------------------------------- fetching
def _pull(session, inst, chunk, page):
    """One tag-timeline request -> (answered_ok, statuses).

    `answered_ok` separates "instance served us a timeline (possibly empty)" from "instance
    refused / errored", so an empty harvest can report which of the two actually happened.
    """
    params = [("limit", page)] + [("any[]", t) for t in chunk[1:]]
    try:
        r = session.get(f"https://{inst}/api/v1/timelines/tag/{chunk[0]}",
                        params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return False, []                # 401/410/422 = anonymous timeline disabled; skip
        data = r.json()
        return True, (data if isinstance(data, list) else [])
    except Exception:
        return False, []                    # per-instance failure must not sink the source


def fetch():
    try:
        page = FETCH_LIMITS.get("mastodon", 20)
        # FETCH_LIMITS caps each request's page size (same meaning as before, and as
        # bluesky_search.py). MASTODON_MAX_ROWS bounds the union across instances/tags.
        max_rows = int(os.getenv("MASTODON_MAX_ROWS", str(page * 10)))
        instances, tags = _instances(), _tags()
        chunks = [tags[i:i + _TAG_CHUNK] for i in range(0, len(tags), _TAG_CHUNK)]
        jobs = [(inst, c) for inst in instances for c in chunks]

        rows, seen, ok = {}, set(), 0
        with requests.Session() as session:
            session.headers.update(HEADERS)
            with cf.ThreadPoolExecutor(max_workers=_WORKERS) as ex:
                futures = [ex.submit(_pull, session, inst, c, page) for inst, c in jobs]
                for fut in cf.as_completed(futures):
                    answered, statuses = fut.result()
                    ok += 1 if answered else 0
                    for s in statuses:
                        if not isinstance(s, dict):
                            continue
                        s = s.get("reblog") or s          # unwrap boosts to the original post
                        key = s.get("uri") or s.get("url") or str(s.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        body = _text(s)
                        if not body or not _brand_match(body, _tag_names(s)):
                            continue
                        row = _row(s)
                        rows[row["source_id"]] = row

        out = sorted(rows.values(), key=lambda r: r.get("created_at") or "", reverse=True)[:max_rows]
        if not out and not ok:
            print(f"  [mastodon] 0 - all {len(jobs)} tag-timeline requests across "
                  f"{len(instances)} instances refused/errored (anonymous preview disabled?)")
        else:
            print(f"  [mastodon] {len(out)}")
        return out
    except Exception as e:
        # ascii-safe: a non-cp1252 char in the message must not turn a handled error into a raise
        print("  [mastodon] error: " + str(e)[:80].encode("ascii", "replace").decode())
        return []
