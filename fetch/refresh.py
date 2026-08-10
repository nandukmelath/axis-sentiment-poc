"""Engagement refresh — re-poll posts we already hold and update their counts.

A post's text is final the moment it is published; its ENGAGEMENT is not. A
complaint sitting at 3 reshares when we first see it can be at 3,000 six hours
later, and that trajectory is the whole signal — by the time you notice the
final number, the crisis already happened.

So every post is enrolled in a refresh schedule when first fetched:

    every 2 hours, 12 times  ->  24 hours of coverage, then it retires

Each pass writes a row to engagement_history (never overwrites) so the curve is
reconstructable, and upserts the latest counts onto raw_posts so the rest of the
pipeline keeps reading one obvious place.

WHICH SOURCES ACTUALLY REFRESH
Only sources that expose a keyless per-post metrics lookup can do this honestly:

    reddit       Arctic Shift returns score/num_comments by id
    hackernews   Firebase item API, no key
    mastodon     public status endpoint on the origin instance

Everything else is registered as unsupported and skipped rather than faked.
Twitter is the painful one: it holds 884 of our engagement-bearing posts, but
live X scraping is dead and the API tier that would serve this costs real money,
so its rows retire immediately instead of pretending to refresh. `python -m
fetch.refresh --status` prints exactly which sources are live and which are not,
because a refresh system that silently no-ops looks identical to one that works.
"""
import argparse
import datetime as dt
import os
import sys

import requests

import db

MAX_ITERATIONS = int(os.getenv("REFRESH_MAX_ITERATIONS", "12"))
INTERVAL_HOURS = float(os.getenv("REFRESH_INTERVAL_HOURS", "2"))
BATCH = int(os.getenv("REFRESH_BATCH", "150"))
UA = {"User-Agent": "axis-sentiment-poc/1.0 (engagement refresh)"}
TIMEOUT = 20


# ------------------------------------------------------------------ scheduling
def _utc():
    return dt.datetime.now(dt.timezone.utc)


def is_due(first_seen, last_refreshed, iterations, now=None):
    """Pure scheduling predicate, so the policy is testable without a network.

    Due when: still inside the 24h window, under the iteration cap, and at least
    one interval has elapsed since the last pass.
    """
    now = now or _utc()
    if iterations is not None and iterations >= MAX_ITERATIONS:
        return False
    if first_seen is not None:
        age_h = (now - first_seen).total_seconds() / 3600.0
        if age_h > MAX_ITERATIONS * INTERVAL_HOURS:
            return False
    if last_refreshed is None:
        return True
    since_h = (now - last_refreshed).total_seconds() / 3600.0
    return since_h >= INTERVAL_HOURS


# ------------------------------------------------------------------ per-source
def _reddit(ids):
    """Arctic Shift mirror — same shape as Reddit's own API, no credentials."""
    base = os.getenv("REDDIT_ARCTIC_BASE", "https://arctic-shift.photon-reddit.com/api")
    out = {}
    for i in range(0, len(ids), 50):
        chunk = [x.split(":", 1)[-1] for x in ids[i:i + 50]]
        try:
            r = requests.get(f"{base}/posts/ids", params={"ids": ",".join(chunk)},
                             headers=UA, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            for item in (r.json() or {}).get("data", []):
                out[str(item.get("id"))] = {
                    "likes": item.get("score"),
                    "comments": item.get("num_comments"),
                    "reshares": None, "views": None,
                }
        except (requests.RequestException, ValueError):
            continue
    return out


def _hackernews(ids):
    out = {}
    for sid in ids:
        item_id = sid.split(":", 1)[-1]
        try:
            r = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
                             headers=UA, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            j = r.json() or {}
            out[item_id] = {"likes": j.get("score"),
                            "comments": len(j.get("kids") or []) or j.get("descendants"),
                            "reshares": None, "views": None}
        except (requests.RequestException, ValueError):
            continue
    return out


def _mastodon(ids):
    """Statuses live on their origin instance; the id alone is not addressable
    without knowing which one, so we read it back off the stored URL."""
    out = {}
    if not ids:
        return out
    # Filtered in Python rather than with a SQL IN: a bare `IN :ids` needs an
    # expanding bindparam to bind a tuple, and the mastodon table is small enough
    # that the round trip is not worth the sharp edge.
    wanted = set(map(str, ids))
    rows = db.df("SELECT source_id, url FROM raw_posts WHERE source='mastodon'")
    if rows.empty:
        return out
    rows = rows[rows["source_id"].astype(str).isin(wanted)]
    for _, r in rows.iterrows():
        url = str(r["url"] or "")
        if "/" not in url:
            continue
        host = url.split("/")[2] if url.startswith("http") else None
        status_id = url.rstrip("/").split("/")[-1]
        if not host or not status_id.isdigit():
            continue
        try:
            resp = requests.get(f"https://{host}/api/v1/statuses/{status_id}",
                                headers=UA, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            j = resp.json() or {}
            out[str(r["source_id"])] = {"likes": j.get("favourites_count"),
                                        "reshares": j.get("reblogs_count"),
                                        "comments": j.get("replies_count"), "views": None}
        except (requests.RequestException, ValueError):
            continue
    return out


# Sources with a keyless per-post metrics lookup. Absence here is deliberate and
# visible via --status, not an oversight.
REFRESHERS = {"reddit": _reddit, "hackernews": _hackernews, "mastodon": _mastodon}
UNSUPPORTED_REASON = {
    "twitter": "live X scraping dead; API tier is paid",
    "play": "Play Store exposes no stable per-review metric endpoint",
    "appstore": "App Store reviews carry no engagement counts",
    "youtube": "per-video stats need a second HTML fetch per post; not wired yet",
}


# ------------------------------------------------------------------ runner
def due_posts(limit=BATCH):
    """Posts inside the window that have waited out an interval."""
    rows = db.df("""SELECT source_id, source, fetched_at, last_refreshed_at,
                           coalesce(refresh_count, 0) AS refresh_count
                    FROM raw_posts
                    WHERE coalesce(refresh_count, 0) < :cap""", {"cap": MAX_ITERATIONS})
    if rows.empty:
        return rows
    now = _utc()
    keep = [is_due(db.parse_dt(r["fetched_at"]), db.parse_dt(r["last_refreshed_at"]),
                   int(r["refresh_count"]), now)
            and r["source"] in REFRESHERS
            for _, r in rows.iterrows()]
    return rows[keep].head(limit)


def run(dry_run=False):
    db.init_db()
    due = due_posts()
    if due.empty:
        print("refresh: nothing due")
        return 0

    by_source = {}
    for _, r in due.iterrows():
        by_source.setdefault(r["source"], []).append(r)

    print(f"refresh: {len(due)} posts due across {len(by_source)} source(s)")
    if dry_run:
        for s, rows in by_source.items():
            print(f"  would refresh {len(rows):4d} from {s}")
        return len(due)

    stamp, updated, history = db.now(), [], []
    for source, rows in by_source.items():
        ids = [str(r["source_id"]) for r in rows]
        try:
            metrics = REFRESHERS[source](ids)
        except Exception as e:                                  # noqa: BLE001
            print(f"  [{source}] refresher failed: {str(e)[:80]}")
            continue
        hit = 0
        for r in rows:
            sid = str(r["source_id"])
            m = metrics.get(sid) or metrics.get(sid.split(":", 1)[-1])
            it = int(r["refresh_count"]) + 1
            # Bump the counter even on a miss, so a permanently-deleted post cannot
            # sit in the queue forever burning a request every two hours.
            updated.append({"source_id": sid, "refresh_count": it, "last_refreshed_at": stamp,
                            "retweet_count": (m or {}).get("reshares"),
                            "reply_count": (m or {}).get("comments"),
                            "view_count": (m or {}).get("views"),
                            "engagement": (m or {}).get("likes")})
            # A dict of all-None is still truthy, and an all-null history row is
            # indistinguishable from "measured zero" later. Require one real value.
            if m and any(m.get(k) is not None for k in ("likes", "reshares", "comments", "views")):
                hit += 1
                history.append({"source_id": sid, "iteration": it, "captured_at": stamp,
                                "likes": m.get("likes"), "reshares": m.get("reshares"),
                                "comments": m.get("comments"), "views": m.get("views")})
        print(f"  [{source}] {hit}/{len(rows)} returned metrics")

    if history:
        db.upsert_rows("engagement_history", history, "source_id", db.ENGAGEMENT_COLS)
    if updated:
        db.bump_refresh(updated)
    print(f"refresh: updated {len(updated)} posts, {len(history)} history rows")
    return len(updated)


def status():
    db.init_db()
    counts = db.df("""SELECT source, COUNT(*) n, coalesce(SUM(refresh_count), 0) passes
                      FROM raw_posts GROUP BY source ORDER BY n DESC""")
    print(f"refresh policy: every {INTERVAL_HOURS}h x {MAX_ITERATIONS} = "
          f"{INTERVAL_HOURS * MAX_ITERATIONS:.0f}h coverage\n")
    print(f"{'source':20} {'posts':>7} {'passes':>7}  status")
    for _, r in counts.iterrows():
        s = r["source"]
        state = "LIVE" if s in REFRESHERS else f"skipped — {UNSUPPORTED_REASON.get(s, 'no metric endpoint')}"
        print(f"{s:20} {int(r['n']):7d} {int(r['passes']):7d}  {state}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    a = p.parse_args()
    if a.status:
        status()
        sys.exit(0)
    run(dry_run=a.dry_run)
