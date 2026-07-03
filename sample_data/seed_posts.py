"""Seed realistic (synthetic) Axis Bank posts so the AI layer runs end-to-end
today, before any fetchers/keys exist. Includes a recent UPI-failure cluster to
demonstrate emerging-issue detection.

Run:  python sample_data/seed_posts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime
from db import init_db, upsert_posts

now = datetime.datetime.now(datetime.timezone.utc)
def ago(hours=0, days=0):
    return (now - datetime.timedelta(hours=hours, days=days)).isoformat(timespec="seconds")

POSTS = [
    # --- recent UPI-failure cluster (emerging) ---
    ("seed:1","twitter","@ravi_k","Axis Bank UPI not working since morning, payment failed but money got debited! Fix this ASAP @AxisBank","http://x.com/1",ago(hours=2),240),
    ("seed:2","reddit","u/fin_guy","Axis UPI down? My payment failed twice today after the app update, money stuck","http://reddit.com/2",ago(hours=3),58),
    ("seed:3","twitter","@meera","Kisi ka Axis UPI chal raha hai? Mera subah se fail ho raha hai, paisa atak gaya","http://x.com/3",ago(hours=4),90),
    ("seed:4","play","AppUser22","After the latest Axis Mobile update UPI keeps failing, please fix, 1 star","http://play/4",ago(hours=6),12),
    ("seed:5","twitter","@startup_ceo","Third UPI failure on Axis today, salary vendor payment stuck. Unacceptable.","http://x.com/5",ago(hours=8),410),
    # --- fraud / impersonation ---
    ("seed:6","twitter","@anita","Got a call claiming to be Axis Bank asking for my OTP to 'unblock' account. Scam? @AxisBank","http://x.com/6",ago(hours=5),33),
    ("seed:7","reddit","u/safebanker","Fake Axis Bank support handle on twitter DMed me a link asking KYC details, beware everyone","http://reddit.com/7",ago(hours=10),120),
    # --- praise ---
    ("seed:8","play","HappyUser","Loving the new Axis Mobile app, so much faster and cleaner now","http://play/8",ago(days=1),8),
    ("seed:9","twitter","@traveller","Axis Magnus lounge access is amazing, best card for travel","http://x.com/9",ago(days=2),140),
    # --- churn + fee ---
    ("seed:10","twitter","@fedup","Closing my Axis salary account, worst customer service ever, moving to HDFC","http://x.com/10",ago(days=1),75),
    ("seed:11","reddit","u/deals","Axis credit card charged annual fee despite lifetime-free promise, feeling cheated","http://reddit.com/11",ago(days=3),64),
    ("seed:12","twitter","@sarcastic1","Wah Axis, ek aur hidden charge, thank you so much for looting us 🙏","http://x.com/12",ago(days=2),52),
    # --- legal / journalist ---
    ("seed:13","twitter","@consumervoice","Filing complaint with RBI ombudsman against Axis Bank for wrong charges, enough is enough","http://x.com/13",ago(days=1),190),
    ("seed:14","news","Moneycontrol","Axis Bank faces customer complaints over intermittent UPI outages this week","http://news/14",ago(hours=12),0),
    # --- neutral / query ---
    ("seed:15","reddit","u/newbie","Is Axis Bank net banking safe for large transactions? Thinking of switching","http://reddit.com/15",ago(days=4),5),
    ("seed:16","youtube","viewer99","Axis Bank FD interest rate is decent compared to others, decent option","http://yt/16",ago(days=5),3),
]

def main():
    init_db()
    rows = [dict(source_id=p[0], source=p[1], author=p[2], text=p[3], url=p[4],
                 created_at=p[5], engagement=p[6], lang="en") for p in POSTS]
    upsert_posts(rows)
    print(f"seeded {len(rows)} posts into raw_posts.")

if __name__ == "__main__":
    main()
