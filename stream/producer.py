"""Streaming PRODUCER — runs forever, lands new posts into raw_posts as they arrive.
Decoupled from scoring (that's stream/worker.py). Two kinds of source:
  - pollers: existing free connectors on short intervals (near-real-time)
  - firehose: Bluesky Jetstream, a true push stream

Run:  python -m stream.producer
"""
import threading, time
from db import init_db, upsert_posts
from fetch import news, playstore, appstore, reddit, youtube, scrapebadger
from stream import bluesky, mastodon

# (name, fetch_fn, interval_seconds)
POLLERS = [
    ("news", news.fetch, 300),
    ("play", playstore.fetch, 600),
    ("reddit", reddit.fetch, 300),
    ("youtube", youtube.fetch, 900),
    ("appstore", appstore.fetch, 900),
    ("scrapebadger", scrapebadger.fetch, 120),   # X mentions via API
]


def poll_loop(name, fn, interval, stop):
    while not stop.is_set():
        try:
            rows = fn() or []
            if rows:
                upsert_posts(rows)
                print(f"[producer:{name}] pulled {len(rows)} (new ones land, dupes ignored)")
        except Exception as e:
            print(f"[producer:{name}] error: {str(e)[:80]}")
        stop.wait(interval)


def firehose_loop(name, mod, stop):
    def on_post(row):
        upsert_posts([row])
        print(f"[producer:{name}] LIVE ● {row['text'][:70]}")
    while not stop.is_set():
        try:
            mod.stream_posts(on_post, stop)
        except Exception as e:
            print(f"[producer:{name}] reconnect ({str(e)[:60]})")
            stop.wait(5)


def x_loop(stop, interval=180):
    """Authenticated X.com crawl on an interval (only if a session exists)."""
    import os
    from config import X_STATE_FILE
    warned = False
    while not stop.is_set():
        if os.path.exists(X_STATE_FILE):
            try:
                from fetch import x_crawler
                rows = x_crawler.fetch()
                if rows:
                    upsert_posts(rows)
                    print(f"[producer:x] crawled {len(rows)} X posts")
            except Exception as e:
                print(f"[producer:x] error: {str(e)[:80]}")
        elif not warned:
            print("[producer:x] no X session yet — run: python -m fetch.x_crawler login")
            warned = True
        stop.wait(interval)


def main():
    init_db()
    stop = threading.Event()
    threads = [threading.Thread(target=poll_loop, args=(n, f, i, stop), daemon=True) for n, f, i in POLLERS]
    threads.append(threading.Thread(target=firehose_loop, args=("bluesky", bluesky, stop), daemon=True))
    threads.append(threading.Thread(target=firehose_loop, args=("mastodon", mastodon, stop), daemon=True))
    threads.append(threading.Thread(target=x_loop, args=(stop,), daemon=True))
    for t in threads:
        t.start()
    print(f"producer streaming: {len(POLLERS)} pollers + Bluesky + Mastodon firehoses + X crawler. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        print("\nproducer stopped.")


if __name__ == "__main__":
    main()
