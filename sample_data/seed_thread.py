"""Seed one full public COMPLAINT->RESPONSE->RESOLUTION thread so the CX /
resolution fact and the north-star Sentiment Recovery Rate demo end-to-end.

Thread (shared conversation_id):
  1. customer complaint (negative) — deliberately laced with PII to prove masking
  2. @AxisBankSupport reply (bank response, offers refund + DM)
  3. customer follow-up (positive) — "resolved, thanks" -> satisfied

Run:  python sample_data/seed_thread.py   (after python db.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime
from db import init_db, upsert_posts

now = datetime.datetime.now(datetime.timezone.utc)
def ago(hours=0, days=0):
    return (now - datetime.timedelta(hours=hours, days=days)).isoformat(timespec="seconds")

CONV = "thread:axis:1"

# source_id, source, author, text, created_at, engagement
THREAD = [
    ("thr:1", "twitter", "@ravi_k",
     "@AxisBank my card 4111 1111 1111 1111 was charged twice, money debited! Call me on 9876543210, "
     "PAN ABCDE1234F, Aadhaar 1234 5678 9012. Fix this now!",
     ago(hours=30), 210),
    ("thr:2", "twitter", "@AxisBankSupport",
     "Hi Ravi, we're sorry for the trouble. We've raised a ticket and the duplicate charge is being "
     "refunded. Please check your DM so we can assist you further.",
     ago(hours=28), 3),
    ("thr:3", "twitter", "@ravi_k",
     "@AxisBankSupport refund received, issue resolved. Thanks for the quick help, great support! 👍",
     ago(hours=26), 44),
]


def main():
    init_db()
    rows = [dict(source_id=t[0], source=t[1], author=t[2], text=t[3], url=f"http://x.com/{t[0]}",
                 created_at=t[4], engagement=t[5], lang="en", conversation_id=CONV) for t in THREAD]
    upsert_posts(rows)
    print(f"seeded resolution thread ({len(rows)} posts, conversation_id={CONV}).")


if __name__ == "__main__":
    main()
