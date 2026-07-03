"""Pull every source, normalize, land into raw_posts (deduped by source_id).
One dead source never kills the others.

Run:  python -m fetch.run_fetch
"""
from db import init_db, upsert_posts
from fetch import (news, playstore, appstore, reddit, youtube, twitter, scrapebadger,
                   hackernews, mastodon_search)

SOURCES = [
    ("news", news.fetch),
    ("play", playstore.fetch),
    ("appstore", appstore.fetch),
    ("reddit", reddit.fetch),
    ("youtube", youtube.fetch),
    ("scrapebadger", scrapebadger.fetch),   # X via API (primary)
    ("twitter", twitter.fetch),             # X via CSV (fallback)
    ("hackernews", hackernews.fetch),       # FREE Algolia (no key)
    ("mastodon", mastodon_search.fetch),    # FREE public hashtag timelines (no auth)
]
# Note: fetch/bluesky_search.py exists but public.api.bsky.app now 403s batch search —
# the working keyless Bluesky path is the Jetstream firehose in stream/bluesky.py.


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run a single source (e.g. news, scrapebadger)")
    a = ap.parse_args()
    init_db()
    sources = [(n, f) for n, f in SOURCES if (not a.only or n == a.only)]
    all_rows = []
    print(f"fetching{' ' + a.only if a.only else ''} ...")
    for name, fn in sources:
        try:
            all_rows.extend(fn() or [])
        except Exception as e:
            print(f"  [{name}] FAILED: {str(e)[:120]}")
    # de-dup within this run by source_id
    uniq = {r["source_id"]: r for r in all_rows if r.get("text")}
    n = upsert_posts(list(uniq.values()))
    print(f"fetched {len(uniq)} unique posts this run (INSERT OR IGNORE keeps only new ones).")
    print("next: python -m analyze.run_analyze")


if __name__ == "__main__":
    main()
