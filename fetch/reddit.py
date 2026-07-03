"""Reddit — official API via PRAW (free tier, ~100 req/min OAuth).

Setup (one time, free, no approval needed):
  1. https://www.reddit.com/prefs/apps  ->  create app  ->  type = "script"
  2. copy client id (under the app name) + secret into .env:
       REDDIT_CLIENT_ID=...
       REDDIT_CLIENT_SECRET=...
       REDDIT_USER_AGENT=axis-sentiment-poc by u/yourname
  app-only (client_credentials) auth is enough for read-only search.

What it pulls, per target subreddit:
  - submissions matching any brand alias (sort=new)
  - the top comments under each matched submission (comments = biggest sentiment vein)
Both land as separate rows in raw_posts so the AI layer scores each independently.
One dead subreddit never kills the rest.
"""
import os, json, datetime
from config import BRAND_ALIASES, SUBREDDITS, FETCH_LIMITS, REDDIT_COMMENTS_PER

# OR-query across aliases; drop bare "Axis" (too noisy) and punctuation-only variants
_ALIASES = [a for a in BRAND_ALIASES if a.lower() not in {"axis"} and a.strip("@#")]
SEARCH_Q = " OR ".join(f'"{a}"' if " " in a else a for a in dict.fromkeys(
    a.lstrip("@#") for a in _ALIASES))


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def fetch():
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        print("  [reddit] skipped (set REDDIT_CLIENT_ID/SECRET — see fetch/reddit.py header)")
        return []
    try:
        import praw
    except ImportError:
        print("  [reddit] skipped (pip install praw)")
        return []

    r = praw.Reddit(client_id=cid, client_secret=sec,
                    user_agent=os.getenv("REDDIT_USER_AGENT", "axis-sentiment-poc"),
                    check_for_async=False)
    r.read_only = True

    targets = ["all"] + SUBREDDITS          # r/all catches viral spillover into other subs
    per = max(5, FETCH_LIMITS["reddit"] // len(targets))
    seen, out = set(), []

    for sub in targets:
        try:
            for s in r.subreddit(sub).search(SEARCH_Q, sort="new", limit=per):
                if s.id in seen:
                    continue
                seen.add(s.id)
                sr = getattr(s.subreddit, "display_name", sub)
                out.append(dict(
                    source_id=f"reddit:{s.id}", source="reddit",
                    author=str(s.author), author_name=str(s.author),
                    text=f"{s.title}\n{s.selftext or ''}".strip(),
                    url=f"https://reddit.com{s.permalink}",
                    created_at=_iso(s.created_utc), lang="en",
                    engagement=int(s.score or 0), reply_count=int(s.num_comments or 0),
                    conversation_id=s.id,
                    raw_json=json.dumps({"type": "submission", "subreddit": sr,
                                         "upvote_ratio": getattr(s, "upvote_ratio", None)})))

                # top comments under this submission — where complaints actually live
                if REDDIT_COMMENTS_PER > 0:
                    try:
                        s.comment_sort = "top"
                        s.comments.replace_more(limit=0)
                        for c in s.comments[:REDDIT_COMMENTS_PER]:
                            body = (c.body or "").strip()
                            if not body or body in ("[deleted]", "[removed]"):
                                continue
                            cid_ = f"reddit:c_{c.id}"
                            if cid_ in seen:
                                continue
                            seen.add(cid_)
                            out.append(dict(
                                source_id=cid_, source="reddit",
                                author=str(c.author), author_name=str(c.author),
                                text=body,
                                url=f"https://reddit.com{c.permalink}",
                                created_at=_iso(c.created_utc), lang="en",
                                engagement=int(c.score or 0), reply_count=None,
                                conversation_id=s.id,
                                raw_json=json.dumps({"type": "comment", "subreddit": sr,
                                                     "parent_submission": s.id})))
                    except Exception as e:
                        print(f"  [reddit] {sub}:{s.id} comments error: {str(e)[:60]}")
        except Exception as e:
            print(f"  [reddit] {sub} error: {str(e)[:80]}")

    n_com = sum(1 for o in out if o["source_id"].startswith("reddit:c_"))
    n_sub = len(out) - n_com
    print(f"  [reddit] {len(out)} ({n_sub} posts + {n_com} comments) across {len(targets)} targets")
    return out
