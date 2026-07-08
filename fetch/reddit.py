"""Reddit — two paths, best available wins:

  KEYED   official API via PRAW (free tier, ~100 req/min OAuth) — richest: submissions +
          top comments with scores. Setup: https://www.reddit.com/prefs/apps -> "script"
          app -> REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (+ REDDIT_USER_AGENT) in .env.

  KEYLESS RSS fallback (no credentials at all) — Reddit hard-403s the .json endpoints for
          scripts but SERVES .rss with a browser UA (verified live 2026-07-07). We pull
          per-subreddit search.rss for the brand query, plus each fresh thread's .rss for
          its comments. No scores in RSS (engagement=0), but full text + author + date.

Both land as separate rows in raw_posts so the AI layer scores each independently.
One dead subreddit never kills the rest.
"""
import os, json, re, time, datetime
from config import BRAND_ALIASES, SUBREDDITS, FETCH_LIMITS, REDDIT_COMMENTS_PER

# OR-query across aliases; drop bare "Axis" (too noisy) and punctuation-only variants
_ALIASES = [a for a in BRAND_ALIASES if a.lower() not in {"axis"} and a.strip("@#")]
SEARCH_Q = " OR ".join(f'"{a}"' if " " in a else a for a in dict.fromkeys(
    a.lstrip("@#") for a in _ALIASES))


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


# ---------- KEYLESS RSS path ----------

def _rss_text(entry):
    from bs4 import BeautifulSoup
    html = entry.get("summary", "")
    if entry.get("content"):
        html = entry["content"][0].get("value", html)
    body = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    # Reddit RSS bodies end with "submitted by /u/x [link] [comments]" boilerplate — strip it
    body = re.sub(r"submitted by\s+/u/\S+.*$", "", body).strip()
    return body


def _post_id(link):
    m = re.search(r"/comments/([a-z0-9]+)/", link or "")
    return m.group(1) if m else ""


def _fetch_rss():
    import feedparser
    from fetch.webutil import get

    q = " OR ".join(f'"{a}"' if " " in a else a for a in dict.fromkeys(
        a.lstrip("@#") for a in _ALIASES))
    # scale with FETCH_LIMITS (env FETCH_MULT) like the PRAW path, so a max-harvest run
    # actually pulls more from the keyless Reddit path too (search.rss still caps ~25/page).
    per_sub = max(10, FETCH_LIMITS.get("reddit", 40) // max(1, len(SUBREDDITS)))
    seen, out = set(), []

    throttled = 0
    for sub in SUBREDDITS:
        url = (f"https://www.reddit.com/r/{sub}/search.rss?"
               f"q={q.replace(' ', '+')}&restrict_sr=1&sort=new&limit={per_sub}")
        try:
            r = get(url)
            if r.status_code == 429:
                throttled += 1
                if throttled >= 3:      # IP is rate-limited — stop hammering, next run recovers
                    print("  [reddit] rss rate-limited (429 x3) — stopping early this run")
                    break
                time.sleep(10)
                continue
            if r.status_code != 200:
                continue
            for e in feedparser.parse(r.text).entries:
                pid = _post_id(e.get("link", ""))
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(dict(
                    source_id=f"reddit:{pid}", source="reddit",
                    author=(e.get("author") or "").replace("/u/", "@"),
                    author_name=(e.get("author") or "").replace("/u/", ""),
                    text=f"{e.get('title', '')}\n{_rss_text(e)}".strip()[:4000],
                    url=e.get("link", ""), created_at=e.get("updated", e.get("published", "")),
                    engagement=0, lang="en", conversation_id=pid,
                    raw_json=json.dumps({"type": "submission", "subreddit": sub, "via": "rss"})))
        except Exception as ex:
            print(f"  [reddit] r/{sub} rss error: {str(ex)[:60]}")
        time.sleep(2.0)     # polite — unauthenticated RSS has a small per-IP budget

    # comments for the freshest threads — thread .rss lists comment entries after the post
    for post in list(out)[:12]:
        if REDDIT_COMMENTS_PER <= 0:
            break
        try:
            r = get(post["url"].rstrip("/") + "/.rss")
            if r.status_code != 200:
                continue
            n = 0
            for e in feedparser.parse(r.text).entries:
                link = e.get("link", "")
                if _post_id(link) != post["conversation_id"]:
                    continue
                # comment permalink = /comments/<pid>/<slug>/<cid>/ — the post itself has no <cid>
                m = re.search(r"/comments/[a-z0-9]+/[^/]+/([a-z0-9]+)/?$", link)
                if not m:
                    continue
                cid_ = f"reddit:c_{m.group(1)}"
                if cid_ in seen:
                    continue
                body = _rss_text(e)
                if not body or body in ("[deleted]", "[removed]"):
                    continue
                seen.add(cid_)
                out.append(dict(
                    source_id=cid_, source="reddit",
                    author=(e.get("author") or "").replace("/u/", "@"),
                    author_name=(e.get("author") or "").replace("/u/", ""),
                    text=body[:4000], url=link,
                    created_at=e.get("updated", ""), engagement=0, lang="en",
                    conversation_id=post["conversation_id"],
                    raw_json=json.dumps({"type": "comment", "via": "rss"})))
                n += 1
                if n >= REDDIT_COMMENTS_PER:
                    break
        except Exception:
            continue
        time.sleep(1.2)

    n_com = sum(1 for o in out if o["source_id"].startswith("reddit:c_"))
    print(f"  [reddit] {len(out)} via KEYLESS RSS ({len(out) - n_com} posts + {n_com} comments) "
          f"— add REDDIT_CLIENT_ID/SECRET for scores + deeper comments")
    return out


# ---------- KEYED PRAW path ----------

def fetch():
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        return _fetch_rss()          # keyless fallback — no more silent skip
    try:
        import praw
    except ImportError:
        return _fetch_rss()

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
