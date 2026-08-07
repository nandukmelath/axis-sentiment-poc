"""ScrapeBadger Twitter/X API — the preferred X source (paid, ToS-clean, no browser).
Advanced tweet search with cursor pagination; captures rich per-tweet fields and the
REAL post datetime (parsed to ISO).

Auth: SCRAPEBADGER_API_KEY in .env.  Endpoint: GET /v1/twitter/tweets/advanced_search (x-api-key).

Also the home of the SHARED ScrapeBadger credit pre-flight (see credit_preflight below) used
by all 9 paid ScrapeBadger fetchers. It lives here, not in scrapebadger_web.py, because
scrapebadger_web imports FROM this module — putting it the other way round would be a circular
import.

Run:
  python -m fetch.scrapebadger                        # recent Axis mentions -> raw_posts
  python -m fetch.scrapebadger backfill --days 365 --query "(@AxisBank OR @AxisBankSupport)"
"""
import os, json, time, datetime, argparse, threading
import requests
from config import SB_QUERY, SB_PAGES, SB_QUERY_TYPE, X_BACKFILL_DAYS, X_BACKFILL_WINDOW

BASE = "https://scrapebadger.com/v1/twitter/tweets/advanced_search"

# FREE, zero-credit balance endpoint. Calling it costs nothing, so it is safe to use as a
# pre-flight in front of every paid request.
ACCOUNT_URL = "https://scrapebadger.com/v1/account/me"


class CreditsExhausted(Exception):
    pass


# ---------------------------------------------------------------------------------------
# SHARED CREDIT PRE-FLIGHT  (one HTTP call per process, memoised, used by all 9 paid sources)
#
# Why: with a 0-credit account every paid ScrapeBadger fetcher used to fire its own request,
# eat an HTTP 402 and print its own "credits exhausted" line — NINE wasted round-trips
# (~19s) per harvest that could never return a row. The free /v1/account/me endpoint tells us
# the balance for 0 credits, so we ask ONCE and short-circuit the rest.
#
# Fail-open contract: if the balance cannot be determined (no key, network error, non-200,
# unexpected JSON) the pre-flight does NOTHING and the real request proceeds exactly as
# before. It only ever blocks on a *provable* zero. A positive balance is likewise a no-op
# (beyond one informational line), so nothing changes the moment the user tops up.
# ---------------------------------------------------------------------------------------
_ACCOUNT_LOCK = threading.Lock()
_ACCOUNT_CACHE = None      # None = not checked yet this process; else the status dict
_BANNER_SHOWN = False      # the human-readable balance line is printed at most once


def _preflight_enabled():
    """Escape hatch: SB_PREFLIGHT=0 disables the pre-flight entirely (old behaviour)."""
    return os.getenv("SB_PREFLIGHT", "1").strip().lower() not in ("0", "false", "no", "off")


def _balance_of(j):
    """Pull the spendable balance out of /account/me, tolerating field-name drift.
    Returns None when no recognised numeric balance field is present (-> 'unknown')."""
    for k in ("total_credits_balance", "credits_balance", "credits", "balance",
              "subscription_credits_balance"):
        v = j.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v.strip())
    return None


def account_status(refresh=False):
    """GET the FREE /v1/account/me and return a status dict, memoised for the life of the
    process (this is what makes 9 checks cost 1 HTTP call):

        {"known": bool, "balance": int|None, "tier": str, "reason": str}

    known=False means "could not determine" — callers MUST fail open in that case.
    Never raises.
    """
    global _ACCOUNT_CACHE
    with _ACCOUNT_LOCK:
        if _ACCOUNT_CACHE is not None and not refresh:
            return _ACCOUNT_CACHE
        status = {"known": False, "balance": None, "tier": "", "reason": ""}
        key = os.getenv("SCRAPEBADGER_API_KEY")
        if not key:
            status["reason"] = "SCRAPEBADGER_API_KEY not set"
        else:
            try:
                r = requests.get(ACCOUNT_URL, headers={"x-api-key": key}, timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    if isinstance(j, dict):
                        bal = _balance_of(j)
                        if bal is None:
                            status["reason"] = "no balance field in /account/me response"
                        else:
                            status.update(known=True, balance=bal,
                                          tier=str(j.get("tier") or ""), reason="ok")
                    else:
                        status["reason"] = "unexpected /account/me payload"
                else:
                    # 401/403 = bad key, 5xx = their side. Both are honestly "unknown
                    # balance", not "no credits" — fail open and let the real call speak.
                    status["reason"] = f"/account/me HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as e:
                status["reason"] = f"/account/me unreachable ({type(e).__name__})"
        _ACCOUNT_CACHE = status
        return status


def note_402(tier_hint=""):
    """Record a real HTTP 402 seen by any paid call: pin the cached balance to 0 so the
    remaining sources short-circuit for free instead of each burning their own 402. Covers
    the mid-run exhaustion case (balance was positive at pre-flight, ran out during the run).
    """
    global _ACCOUNT_CACHE
    with _ACCOUNT_LOCK:
        tier = tier_hint or (_ACCOUNT_CACHE or {}).get("tier", "")
        _ACCOUNT_CACHE = {"known": True, "balance": 0, "tier": tier, "reason": "HTTP 402"}


def reset_credit_cache():
    """Forget the memoised balance (long-running/streaming processes, tests, post-top-up)."""
    global _ACCOUNT_CACHE, _BANNER_SHOWN
    with _ACCOUNT_LOCK:
        _ACCOUNT_CACHE = None
        _BANNER_SHOWN = False


def _banner_once(msg):
    global _BANNER_SHOWN
    with _ACCOUNT_LOCK:
        if _BANNER_SHOWN:
            return
        _BANNER_SHOWN = True
    print(msg)


def credit_preflight():
    """Called immediately before every paid ScrapeBadger request.

    Raises CreditsExhausted — WITHOUT sending a paid request — when the account balance is
    provably 0. Returns None (no-op) when the balance is positive or unknown. Callers already
    catch CreditsExhausted and degrade to [], so this changes no contract.
    """
    if not _preflight_enabled():
        return
    st = account_status()
    if not st["known"]:
        return                                   # fail open — behave exactly as before
    bal = st["balance"]
    tier = st["tier"] or "unknown"
    if bal > 0:
        # Positive balance: get out of the way. One informational line per process so the
        # user can see at a glance how much is left.
        _banner_once(f"  [scrapebadger] credits: {bal:,} remaining (tier: {tier}) — "
                     f"paid ScrapeBadger sources enabled.")
        return
    _banner_once(
        f"  [scrapebadger] ScrapeBadger balance is 0 credits (tier: {tier}). All 9 paid "
        f"ScrapeBadger sources (scrapebadger/X, tiktok, linkedin, instagram, facebook, "
        f"consumercomplaints, trustpilot, mouthshut, googlereviews) are SKIPPED this run — "
        f"no paid requests will be sent.\n"
        f"  [scrapebadger] TO REVIVE: top up at https://scrapebadger.com (dashboard -> "
        f"billing), or point SCRAPEBADGER_API_KEY in .env at a funded key. Nothing else to "
        f"change — the sources re-enable themselves on the next run. "
        f"(Balance read once from the FREE GET /v1/account/me; set SB_PREFLIGHT=0 to bypass "
        f"this check.)")
    raise CreditsExhausted(f"0 ScrapeBadger credits (tier: {tier}) — top up at scrapebadger.com "
                           f"[checked once via free /account/me; no paid call sent]")


def _has_key():
    return bool(os.getenv("SCRAPEBADGER_API_KEY"))


def _key():
    k = os.getenv("SCRAPEBADGER_API_KEY")
    if not k:
        raise RuntimeError("SCRAPEBADGER_API_KEY not set (.env)")
    return k


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _iso(s):
    """Twitter time 'Wed Oct 10 20:19:24 +0000 2018' -> ISO 8601. Leave ISO/others as-is."""
    if not s:
        return ""
    try:
        return datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except (ValueError, TypeError):
        return s


def _row(t):
    # ---------------------------------------------------------------------------------
    # TRAP — DO NOT "FIX" source="twitter" TO "scrapebadger". It looks wrong; it isn't.
    #
    # `source` here is not the name of this fetcher, it is the CHANNEL label, and it is a
    # foreign key in disguise:
    #   warehouse/star.py:151   UPDATE fact_mention SET source_key = source
    #   warehouse/star.py:174   FROM fact_mention f JOIN dim_source s ON f.source_key = s.source_key
    # That JOIN is an INNER join and dim_source is seeded ONLY from star.py SOURCE_SEED,
    # which contains ("twitter", "X / Twitter", ...) and has NO "scrapebadger" entry.
    # Rename the label here and every row this module produces silently vanishes from
    # mart_channel (and shows a NULL source_type in vw_mention's LEFT JOIN) — a silent data
    # loss, not an error. As of this writing that is 890 rows of raw_posts.
    #
    # Two consequences the audit flagged, both intentional and both to be left alone:
    #   1. SELECT ... WHERE source='scrapebadger' is 0 BY CONSTRUCTION. Never use the label
    #      to check whether this fetcher ran — the X rows it writes are indistinguishable by
    #      `source` from the other two X paths.
    #   2. THREE X paths deliberately share this one label: this module (paid API),
    #      fetch/twitter.py (CSV import + snscrape) and fetch/x_crawler.py. They dedup
    #      against each other on the shared "x:<tweet id>" source_id, which only works
    #      because the channel label matches too.
    # If a per-path label is ever genuinely needed, it is a TWO-file change: add the new
    # source_key to warehouse/star.py SOURCE_SEED *first*, rebuild the star, then change it
    # here. warehouse/star.py is not owned by this module.
    # ---------------------------------------------------------------------------------
    uid = t.get("username") or ""
    tid = str(t.get("id") or "")
    return dict(
        source_id=f"x:{tid}", source="twitter",
        author=("@" + uid) if uid else "", author_name=t.get("user_name"),
        text=t.get("full_text") or t.get("text") or "",
        url=f"https://x.com/{uid}/status/{tid}" if (uid and tid) else "",
        created_at=_iso(t.get("created_at", "")), lang=t.get("lang", "en"),
        engagement=_int(t.get("favorite_count")),
        reply_count=_int(t.get("reply_count")), retweet_count=_int(t.get("retweet_count")),
        quote_count=_int(t.get("quote_count")), view_count=_int(t.get("view_count")),
        bookmark_count=_int(t.get("bookmark_count")), conversation_id=t.get("conversation_id"),
        raw_json=json.dumps(t, ensure_ascii=False))


def _get(params, retries=6):
    """GET with 429 backoff; raise CreditsExhausted on 402 (or up-front, for free, when the
    cached balance is already known to be 0 — see credit_preflight)."""
    credit_preflight()          # cached: costs one free HTTP call per process, not per page
    for attempt in range(retries):
        try:
            r = requests.get(BASE, headers={"x-api-key": _key()}, params=params, timeout=45)
        except requests.RequestException as e:
            print(f"  [scrapebadger] network error: {e}")
            return None
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print("  [scrapebadger] non-JSON 200 response")
                return None
        if r.status_code == 429:
            wait = min(2 ** attempt + 2, 40)
            print(f"    rate-limited (429), waiting {wait}s ...")
            time.sleep(wait)
            continue
        if r.status_code == 402:
            note_402()   # pin balance to 0 so the other paid sources skip for free
            raise CreditsExhausted(
                "ScrapeBadger credits exhausted (HTTP 402) — top up at scrapebadger.com")
        print(f"  [scrapebadger] HTTP {r.status_code}: {r.text[:120]}")
        return None
    return None


def search(query, query_type="Latest", max_pages=5, count=100, sleep=1.2, upsert=False):
    from db import upsert_posts
    rows, cursor = {}, None
    for _ in range(max_pages):
        params = {"query": query, "query_type": query_type, "count": count}
        if cursor:
            params["cursor"] = cursor
        j = _get(params)
        if not j:
            break
        data = j.get("data") or []
        page = [_row(t) for t in data if t.get("id")]
        for pr in page:
            rows[pr["source_id"]] = pr
        if upsert and page:
            upsert_posts(page)   # incremental so backfill progress persists
        cursor = j.get("next_cursor")
        if not data or not cursor:
            break
        time.sleep(sleep)
    return list(rows.values())


def fetch():
    """Recent Axis mentions — used by run_fetch / streaming producer."""
    if not _has_key():
        print("  [scrapebadger] SCRAPEBADGER_API_KEY not set — skipping (no X source).")
        return []
    try:
        rows = search(SB_QUERY, SB_QUERY_TYPE, max_pages=SB_PAGES)
    except CreditsExhausted as e:
        # degrade gracefully — the other sources still run; twitter.py CSV import is the fallback
        print(f"  [scrapebadger] skipped — {e}")
        return []
    print(f"  [scrapebadger] {len(rows)}")
    return rows


def _windows(days, window):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    cur = end
    while cur > start:
        prev = max(start, cur - datetime.timedelta(days=window))
        yield prev.isoformat(), cur.isoformat()
        cur = prev


def backfill(days, window, pages, query=None):
    from db import init_db
    init_db()
    if not _has_key():
        print("  [scrapebadger] SCRAPEBADGER_API_KEY not set — skipping backfill (no X source configured).")
        return 0
    query = query or SB_QUERY
    wins = list(_windows(days, window))
    print(f"scrapebadger backfill: {len(wins)} windows of {window}d over {days}d\n  query: {query}")
    total, empty_streak = 0, 0
    for i, (since, until) in enumerate(wins, 1):
        q = f"{query} since:{since} until:{until}"
        try:
            rows = search(q, "Latest", max_pages=pages, upsert=True)
        except CreditsExhausted:
            print("  !! API credits exhausted — stopping. (top up ScrapeBadger to continue)")
            break
        total += len(rows)
        empty_streak = empty_streak + 1 if not rows else 0
        print(f"  [{i}/{len(wins)}] {since}..{until}: +{len(rows)} (running {total})")
        if empty_streak >= 6:
            print("  6 empty windows in a row — search index likely exhausted for older dates. Stopping.")
            break
    print(f"\nbackfill done: {total} tweets landed (dedup by id). "
          f"Classify: python -m analyze.run_analyze")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="fetch", choices=["fetch", "backfill"])
    ap.add_argument("--days", type=int, default=X_BACKFILL_DAYS)
    ap.add_argument("--window", type=int, default=X_BACKFILL_WINDOW)
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--query", type=str, default=None)
    a = ap.parse_args()
    if a.cmd == "backfill":
        backfill(a.days, a.window, a.pages, a.query)
    else:
        from db import init_db, upsert_posts
        init_db()
        rows = fetch()
        upsert_posts(rows)
        print(f"landed {len(rows)} -> raw_posts. Classify: python -m analyze.run_analyze")


if __name__ == "__main__":
    main()
