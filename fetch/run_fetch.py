"""Pull every source, normalize, land into raw_posts (deduped by source_id).
One dead source never kills the others. Supports a time window (1h/1d/1m) that keeps
only mentions created within the window.

Run:  python -m fetch.run_fetch [--only news] [--window 1h|1d|1m]
"""
import datetime
import pandas as pd
from db import init_db, upsert_posts
from fetch import (news, playstore, appstore, reddit, youtube, twitter, scrapebadger,
                   hackernews, mastodon_search, technofino, rss_news, gdelt, tiktok,
                   consumercomplaints, trustpilot, linkedin, instagram, facebook,
                   mouthshut, googlereviews, scrapling_sources, scrapling_stealth)

SOURCES = [
    ("news", news.fetch),
    ("play", playstore.fetch),
    ("appstore", appstore.fetch),
    ("reddit", reddit.fetch),               # PRAW when keyed; keyless RSS fallback otherwise
    ("youtube", youtube.fetch),
    ("scrapebadger", scrapebadger.fetch),   # X via API (primary)
    ("twitter", twitter.fetch),             # X via CSV (fallback)
    ("hackernews", hackernews.fetch),       # FREE Algolia (no key)
    ("mastodon", mastodon_search.fetch),    # FREE public hashtag timelines (no auth)
    ("technofino", technofino.fetch),       # FREE forum RSS+crawl — Axis CC complaint epicenter
    ("rssnews", rss_news.fetch),            # FREE direct-outlet banking-desk RSS pack
    ("gdelt", gdelt.fetch),                 # FREE global news index (throttled 1 req/6s)
    ("tiktok", tiktok.fetch),                # ScrapeBadger native TikTok search (paid)
    ("consumercomplaints", consumercomplaints.fetch),  # ScrapeBadger web-scrape — Axis complaint threads (paid)
    ("trustpilot", trustpilot.fetch),        # ScrapeBadger web-scrape — Axis Bank reviews (paid)
    ("linkedin", linkedin.fetch),            # ScrapeBadger native company/posts + web-scrape fallback (paid)
    ("instagram", instagram.fetch),          # ScrapeBadger web-scrape — @axisbank profile (paid, login-wall risk)
    ("facebook", facebook.fetch),            # ScrapeBadger web-scrape — Axis Bank Page (paid, login-wall risk)
    ("mouthshut", mouthshut.fetch),          # ScrapeBadger web-scrape — Axis Bank reviews (paid)
    ("googlereviews", googlereviews.fetch),  # ScrapeBadger web-scrape — Axis Bank branch reviews (paid, heaviest anti-bot)
    # --- FREE Scrapling sources (curl_cffi TLS impersonation, no key, no credits) ---
    ("cc_web", scrapling_sources.fetch_consumercomplaints),   # real Indian customer complaints
    ("valuepickr", scrapling_sources.fetch_valuepickr),       # Discourse JSON API — investor forum
    ("businessstandard", scrapling_sources.fetch_businessstandard),  # 403s plain requests; needs TLS impersonation
    ("ambitionbox", scrapling_sources.fetch_ambitionbox),     # EMPLOYEE reviews — Glassdoor 403s, this doesn't
    # --- FREE stealth-browser sources (Scrapling StealthyFetcher) — OFF unless STEALTH_SOURCES=1.
    # Each launches a real browser (~20s), so they self-skip on the fast/RUN path. ---
    ("trustpilot_web", scrapling_stealth.fetch_trustpilot),   # Cloudflare-solved; structured review JSON
    ("gmaps_web", scrapling_stealth.fetch_gmaps_branches),    # branch name + star rating
    ("mouthshut_web", scrapling_stealth.fetch_mouthshut),     # free replacement for the paid mouthshut.py
]
# Note: fetch/bluesky_search.py exists but public.api.bsky.app now 403s batch search —
# the working keyless Bluesky path is the Jetstream firehose in stream/bluesky.py.

WINDOW_DELTA = {"1h": datetime.timedelta(hours=1), "1d": datetime.timedelta(days=1),
                "1m": datetime.timedelta(days=30)}


def cutoff(window):
    d = WINDOW_DELTA.get(window)
    return datetime.datetime.now(datetime.timezone.utc) - d if d else None


def _within(created_at, cut):
    if cut is None:
        return True
    ts = pd.to_datetime(created_at, errors="coerce", utc=True, format="mixed")
    return pd.notna(ts) and ts >= cut


def run(only=None, window=None):
    """Fetch sources, keep only mentions inside the window (if any), upsert. Returns count."""
    init_db()
    cut = cutoff(window)
    sources = [(n, f) for n, f in SOURCES if (not only or n == only)]
    all_rows = []
    print(f"fetching{' ' + only if only else ''}{' last ' + window if window else ''} ...")
    for name, fn in sources:
        try:
            all_rows.extend(fn() or [])
        except Exception as e:
            print(f"  [{name}] FAILED: {str(e)[:120]}")
    uniq = {r["source_id"]: r for r in all_rows if r.get("text")}
    rows = list(uniq.values())
    if cut is not None:
        rows = [r for r in rows if _within(r.get("created_at"), cut)]
    upsert_posts(rows)
    # NOTE: this is fetched-UNIQUE this run, not newly-inserted rows — upsert dedups already-seen
    # source_ids at the DB (INSERT OR IGNORE), and does not report the true insert delta.
    print(f"fetched {len(rows)} unique posts{' in last ' + window if window else ''} "
          f"(already-seen source_ids are deduped on upsert).")
    return len(rows)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run a single source (e.g. news, scrapebadger)")
    ap.add_argument("--window", choices=["1h", "1d", "1m"], default=None,
                    help="keep only mentions from the last hour/day/month")
    a = ap.parse_args()
    run(only=a.only, window=a.window)
    print("next: python -m analyze.run_analyze")


if __name__ == "__main__":
    main()
