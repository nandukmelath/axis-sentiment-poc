"""One-shot backfill of reshare / quote / view counts on tweets already stored.

The corpus was built when only likes and replies were reachable, so ~890 tweets
carry null or stale reshare and view counts. The GraphQL guest-token path can now
serve all of them; this walks the stored tweets and fills the gaps.

Not part of the 2-hourly cycle — that is fetch/refresh.py, which only touches
posts inside their 24h window. This is for the historical tail, which the refresh
scheduler has (correctly) already retired.

Run:  python -m fetch.twitter_backfill            # only rows missing metrics
      python -m fetch.twitter_backfill --all      # re-poll every tweet
      python -m fetch.twitter_backfill --limit 50
"""
import argparse
import time

import db
from fetch.twitter_live import graphql_session, hydrate_graphql

PAUSE = 0.35


def run(limit=None, recompute=False, verbose=True):
    db.init_db()
    where = "" if recompute else """AND (retweet_count IS NULL OR view_count IS NULL
                                        OR quote_count IS NULL)"""
    rows = db.df(f"""SELECT source_id, url FROM raw_posts
                     WHERE source = 'twitter' {where}
                     ORDER BY created_at DESC""")
    if rows.empty:
        print("backfill: nothing to do — every tweet already has full metrics")
        return 0
    if limit:
        rows = rows.head(limit)

    gql = graphql_session()
    if gql is None:
        print("backfill: guest-token activation failed; nothing written")
        return 0

    updates, missed = [], 0
    for i, (_, r) in enumerate(rows.iterrows(), 1):
        tid = str(r["source_id"]).split(":", 1)[-1]
        h = hydrate_graphql(tid, gql)
        if h:
            updates.append({"source_id": r["source_id"],
                            "retweet_count": h.get("retweet_count"),
                            "quote_count": h.get("quote_count"),
                            "view_count": h.get("view_count"),
                            "bookmark_count": h.get("bookmark_count"),
                            "engagement": h.get("engagement"),
                            "reply_count": h.get("reply_count")})
        else:
            missed += 1        # deleted, protected, or suspended author
        if verbose and i % 50 == 0:
            print(f"  {i}/{len(rows)}  filled={len(updates)} unavailable={missed}")
        time.sleep(PAUSE)

    if updates:
        db.set_tweet_metrics(updates)
    print(f"backfill: {len(updates)} tweets filled, {missed} unavailable "
          f"(deleted/protected) out of {len(rows)}")
    return len(updates)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="re-poll tweets that already have metrics")
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    run(limit=a.limit, recompute=a.all)
