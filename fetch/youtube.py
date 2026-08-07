"""YouTube — comment-level brand sentiment. Two paths, best available wins.

  KEYED    Data API v3 (free key, 10,000 units/day). search.list costs 100 units and
           commentThreads.list costs 1, so one search + N videos is ~100 + N units.
           Preferred when YOUTUBE_API_KEY is set: exact RFC-3339 timestamps and exact
           like/reply counts, and nothing to break when YouTube reshuffles its HTML.
           Setup: https://console.cloud.google.com/apis/library/youtube.googleapis.com
           -> Enable -> Credentials -> Create credentials -> API key -> YOUTUBE_API_KEY=... in .env

  KEYLESS  no key, no credits, no browser (verified live 2026-08-05). Three keyless legs:
             1. per-channel RSS  youtube.com/feeds/videos.xml?channel_id=... for Axis Bank's
                own channel — dated, structured, and never rate-limited.
             2. youtube.com/results?...&sp=CAI%3D (sort: upload date) parsed out of the
                page's ytInitialData blob — finds THIRD-PARTY videos about the brand, which
                is where the real complaints are.
             3. comments for each of those videos through the same unauthenticated
                /youtubei/v1/next endpoint the watch page itself calls. The continuation
                token is lifted from the watch page; the reliable way to pick the right one
                out of the ~4 tokens on the page is to base64-decode each and keep the one
                whose protobuf carries "comments-section" (layout-independent, so it
                survives YouTube's frequent A/B'd DOM shuffles).

Comments are the point: video titles are the bank's own marketing copy, while the comment
threads under third-party "Axis Bank credit card" videos carry the actual customer anger
("Axis cards are trash now", "going to close all your axis credit card").

BRAND GATE, three stages, because one stage is not enough here:
  1. VIDEO   title + channel + description must brand_match, so nothing off-brand is opened.
  2. COMMENT >= MIN_WORD_CHARS real word characters, which drops the emoji-only replies that
             make up nearly all of the bank's own promo-video threads.
  3. THREAD  see _thread_gate() — a brand-matched video can still have an off-brand thread
             (the Axis CEO interview's comments are mostly about the interviewer), so the
             thread's own brand density decides whether its non-naming comments count.

NOTE the keyless comment path reads a public, unauthenticated endpoint with no login and no
paid API, but it is an internal endpoint rather than a documented one — it can change shape
without notice. That is why the keyed path stays preferred, and why every keyless leg
degrades to [] on its own instead of taking the source down.
"""
import base64
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

import config
from fetch.webutil import HEADERS, brand_match

NAME = "youtube"

WATCH_URL = "https://www.youtube.com/watch?v={vid}&hl=en&gl=IN"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
# sp=CAI%253D -> the server sees sp=CAI%3D -> "CAI=" -> sort by upload date (freshest first).
SEARCH_URL = "https://www.youtube.com/results?search_query={q}&sp=CAI%253D"
SEARCH_URL_TOP = "https://www.youtube.com/results?search_query={q}"   # relevance order
NEXT_URL = "https://www.youtube.com/youtubei/v1/next?key={key}&prettyPrint=false"
API_URL = "https://www.googleapis.com/youtube/v3/{endpoint}"

# Axis Bank's official channel. VERIFIED, not guessed: axisbank.com (which 301s to the
# RBI-mandated official domain www.axis.bank.in) links out to youtube.com/axisbank, that
# handle resolves to /@AxisBank, and that channel's channelMetadataRenderer reports
# externalId=UCZQlZW2OWTWPjcQUTtCD-Vg with a verified-badge header. Checked 2026-08-05.
DEFAULT_CHANNELS = ["UCZQlZW2OWTWPjcQUTtCD-Vg"]
DEFAULT_QUERIES = ["Axis Bank", "Axis Bank credit card", "Axis Bank customer care"]

MIN_WORD_CHARS = 10          # emoji-only / one-word spam gate — counts letters+digits only
# Thread-level brand gate — see _thread_gate(). Measured live 2026-08-05:
#   "BIG NEWS: Axis Bank Credit Cards Massive Changes"   18/39 = 0.46 brand-dense -> keep thread
#   "Axis Bank CEO ... On India's Banking Growth" (podcast) 6/28 = 0.21 -> keep only explicit rows
BRAND_THREAD_RATIO = 0.30
MIN_BRAND_COMMENTS = 3       # guards tiny threads where 1-of-2 would look "dense"
DEVANAGARI = re.compile("[\u0900-\u097F]")   # kept as escapes so this file stays pure ASCII
_REL_AGO = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
            "week": 604800, "month": 2629800, "year": 31557600}
_TOKEN_RE = re.compile(r'"token":\s*"([A-Za-z0-9_\-%=+/]{40,})"')


# ------------------------------------------------------------------ config helpers
# config.py is owned centrally; until these constants land there we read env, then default.
def _cfg_list(name, default):
    v = getattr(config, name, None)
    if v:
        return list(v)
    env = os.getenv(name, "")
    return [x.strip() for x in env.split(";") if x.strip()] or list(default)


def _cfg_int(name, default):
    v = getattr(config, name, None)
    if v is None:
        v = os.getenv(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


CHANNEL_IDS = _cfg_list("YOUTUBE_CHANNEL_IDS", DEFAULT_CHANNELS)
QUERIES = _cfg_list("YOUTUBE_QUERIES", DEFAULT_QUERIES)
# Watch-page budget, not a target: the loop stops as soon as FETCH_LIMITS["youtube"] rows are
# in hand, so a productive run still opens ~6-8. The headroom matters because comment yield per
# video is wildly uneven — one "Axis Bank credit card devaluation" video gave 39 usable
# comments while six of the bank's own promo videos gave 2 between them — so a run that draws a
# lean video set needs to keep digging rather than stop at an arbitrary 8 and return 17 rows.
MAX_VIDEOS = _cfg_int("YOUTUBE_VIDEOS", 12)
COMMENT_PAGES = _cfg_int("YOUTUBE_COMMENT_PAGES", 2)  # continuation pages per video (~20 each)
LIMIT = config.FETCH_LIMITS.get(NAME, 60)


# ------------------------------------------------------------------ small utilities
def _now():
    return datetime.now(timezone.utc)


def _rel_to_iso(rel):
    """'7 days ago (edited)' -> ISO-8601 UTC. Relative text is the ONLY timestamp the
    keyless path gets; converting it is an approximation, not an invention, and it is what
    makes run_fetch's --window filter work at all (an empty created_at is dropped outright).
    Flagged as created_at_approx in raw_json so downstream can tell the two apart."""
    m = _REL_AGO.search(rel or "")
    if not m:
        return ""
    return (_now() - timedelta(seconds=int(m.group(1)) * _SECONDS[m.group(2).lower()])).isoformat()


def _num(s):
    """'1.2K' / '17' / '' -> int."""
    s = (s or "").strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMB])?$", s, re.I)
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1))


def _word_chars(text):
    return len(re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE))


def _lang(text):
    return "hi" if DEVANAGARI.search(text or "") else "en"


def _keep_comment(text):
    """Drop emoji-only / one-word noise. Real complaints always clear this."""
    return _word_chars(text) >= MIN_WORD_CHARS


def _thread_gate(rows):
    """Second brand gate, at the thread level. Rows carry a private `_brand` flag.

    A comment under an Axis Bank video is usually on-topic even when it never types the word
    "Axis" ("I am cancelling my myzone card", "even 1 card it not worth of hold"), so gating
    every comment on brand_match would throw away half the real complaints. But a video can
    be brand-matched and still have a thread that is mostly about something else — the Axis
    CEO interview's comments are overwhelmingly about the host and Indian banking generally,
    and landing those would score podcast praise as Axis sentiment.

    So: measure the thread instead of guessing. Keep every comment that names the brand; keep
    the non-naming remainder only when the thread demonstrably is about the brand.
    """
    # pop() runs over every row, so no `_brand` key can survive into db.RAW_COLS.
    named = [r for r in rows if r.pop("_brand", False)]
    if len(named) >= MIN_BRAND_COMMENTS and rows and len(named) / len(rows) >= BRAND_THREAD_RATIO:
        return rows           # brand-dense thread — its off-word comments are still on-topic
    return named              # diluted thread — keep only what is explicitly about Axis


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    # Pre-accept the consent interstitial some regions serve instead of the page.
    s.cookies.set("SOCS", "CAI", domain=".youtube.com")
    s.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
    return s


def _get(sess, url, timeout=25):
    if not url.startswith("https://"):
        return ""
    try:
        r = sess.get(url, timeout=timeout)
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def _yt_initial_data(html):
    """The watch/search page ships its whole state as one JSON blob."""
    for pat in (r"ytInitialData\s*=\s*(\{.*?\});\s*</script>",
                r'window\["ytInitialData"\]\s*=\s*(\{.*?\});'):
        m = re.search(pat, html or "", re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                continue
    return {}


def _find_renderers(node, key, out):
    """Depth-first collect of every dict stored under `key` anywhere in the blob."""
    if isinstance(node, dict):
        v = node.get(key)
        if isinstance(v, dict):
            out.append(v)
        for x in node.values():
            _find_renderers(x, key, out)
    elif isinstance(node, list):
        for x in node:
            _find_renderers(x, key, out)
    return out


def _runs(node):
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []) if isinstance(r, dict))


# ------------------------------------------------------------------ KEYLESS: discovery
def _videos_from_rss(sess):
    """Axis Bank's own channel — dated, structured, zero risk of a layout change."""
    import feedparser

    out = []
    for cid in CHANNEL_IDS:
        xml = _get(sess, RSS_URL.format(cid=cid))
        if not xml:
            print(f"  [{NAME}] rss {cid[:12]}... unavailable")
            continue
        for e in feedparser.parse(xml).entries:
            vid = e.get("yt_videoid")
            if not vid:
                continue
            out.append(dict(vid=vid, title=e.get("title", ""), channel=e.get("author", ""),
                            desc=(e.get("summary") or "")[:600],
                            published=e.get("published", ""), via="rss"))
    return out


def _videos_from_search(sess, query, recent=True):
    """Third-party videos about the brand — where the complaints actually are.

    recent=True sorts by upload date (freshest, but a brand-new video may have no comments
    yet); recent=False is relevance order, which surfaces the high-traffic explainer videos
    whose threads carry hundreds of comments. Both legs feed the same round-robin.
    """
    tmpl = SEARCH_URL if recent else SEARCH_URL_TOP
    html = _get(sess, tmpl.format(q=urllib.parse.quote_plus(query)), timeout=30)
    data = _yt_initial_data(html)
    if not data:
        return []
    out = []
    for v in _find_renderers(data, "videoRenderer", []):
        vid = v.get("videoId")
        if not vid:
            continue
        desc = _runs(v.get("descriptionSnippet") or {})
        if not desc:
            snips = v.get("detailedMetadataSnippets") or []
            if snips:
                desc = _runs((snips[0] or {}).get("snippetText") or {})
        out.append(dict(
            vid=vid, title=_runs(v.get("title") or {}),
            channel=_runs(v.get("ownerText") or v.get("longBylineText") or {}),
            desc=desc, published=_rel_to_iso(_runs(v.get("publishedTimeText") or {})),
            views=_num(_runs(v.get("viewCountText") or {}).replace(" views", "")), via="search"))
    return out


def _discover(sess):
    """Search + RSS, brand-gated at the video level, deduped, then ROUND-ROBINed.

    Round-robin matters: we only open MAX_VIDEOS watch pages, and the bank's own channel
    publishes daily, so a naive concat lets the promo feed monopolise every slot — and the
    official videos' comments are almost entirely emoji, which the content gate then drops
    (measured live: 6 official videos -> 2 usable comments). Interleaving the search legs
    first guarantees the third-party complaint threads get opened.
    """
    legs = [_videos_from_search(sess, QUERIES[0], recent=False)] if QUERIES else []
    legs += [_videos_from_search(sess, q, recent=True) for q in QUERIES]
    legs += [_videos_from_rss(sess)]

    seen, ordered = set(), []
    for i in range(max((len(x) for x in legs), default=0)):
        for leg in legs:
            if i >= len(leg):
                continue
            v = leg[i]
            if v["vid"] in seen:
                continue
            if not brand_match(f"{v.get('title', '')} {v.get('channel', '')} {v.get('desc', '')}"):
                continue
            seen.add(v["vid"])
            ordered.append(v)
    return ordered


# ------------------------------------------------------------------ KEYLESS: comments
def _comment_tokens(text):
    """Pick the comments continuation out of the ~4 tokens on a watch page.

    Every continuation token is a base64url protobuf; the comments one carries the literal
    'comments-section' target id. Decoding beats walking the DOM/JSON tree because YouTube
    A/B-tests the renderer nesting (observed live: the same video served both with and
    without an itemSectionRenderer wrapper within minutes) but the token payload is stable.
    """
    out = []
    for t in _TOKEN_RE.findall(text or ""):
        raw = urllib.parse.unquote(t)
        try:
            dec = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False)
        except Exception:
            continue
        if b"comments-section" in dec and raw not in out:
            out.append(raw)
    return out


def _innertube(sess, token, key, ver, vid):
    try:
        r = sess.post(NEXT_URL.format(key=key),
                      json={"context": {"client": {"clientName": "WEB", "clientVersion": ver,
                                                   "hl": "en", "gl": "IN"}},
                            "continuation": token},
                      headers={"Content-Type": "application/json",
                               "X-Youtube-Client-Name": "1",
                               "X-Youtube-Client-Version": ver,
                               "Origin": "https://www.youtube.com",
                               "Referer": WATCH_URL.format(vid=vid)},
                      timeout=30)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def _payloads(resp):
    """Comment bodies now arrive as commentEntityPayload entities, not commentRenderers."""
    muts = ((resp.get("frameworkUpdates") or {}).get("entityBatchUpdate") or {}).get("mutations") or []
    return [p for p in ((m.get("payload") or {}).get("commentEntityPayload") for m in muts) if p]


def _next_token(resp):
    for ep in resp.get("onResponseReceivedEndpoints") or []:
        for cmd in ("reloadContinuationItemsCommand", "appendContinuationItemsCommand"):
            for it in (ep.get(cmd) or {}).get("continuationItems") or []:
                cir = it.get("continuationItemRenderer") or {}
                tok = ((cir.get("continuationEndpoint") or {}).get("continuationCommand") or {}).get("token")
                if tok:
                    return tok
    return ""


def _upload_date(html):
    """Exact ISO-8601 upload date off the watch page.

    The search results' relative "7 days ago" text is not always present — YouTube ships
    videoRenderers both with and without publishedTimeText — and an undated row is silently
    dropped by run_fetch whenever a --window is set. The watch page is fetched anyway for the
    comments, and it always carries an exact timestamp, so take it from there for free.
    """
    m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', html or "")
    return m.group(1) if m else ""


def _comments_keyless(sess, html, video, cap):
    """Comments for one video, given its already-fetched watch page. Returns [] quietly when
    comments are off or the page shape moved."""
    vid = video["vid"]
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    toks = _comment_tokens(html)
    if not (key and ver and toks):
        return []                       # comments disabled, or the page shape moved

    cand, seen, token = [], set(), toks[0]
    for _ in range(max(1, COMMENT_PAGES)):
        resp = _innertube(sess, token, key.group(1), ver.group(1), vid)
        payloads = _payloads(resp)
        if not payloads:
            break
        for p in payloads:
            props = p.get("properties") or {}
            cid = props.get("commentId")
            text = ((props.get("content") or {}).get("content") or "").strip()
            if not cid or cid in seen or not _keep_comment(text):
                continue
            seen.add(cid)
            author = (p.get("author") or {}).get("displayName") or ""
            tb = p.get("toolbar") or {}
            named = brand_match(text)
            cand.append(dict(
                source_id=f"yt:{cid}", source=NAME,
                author=author, author_name=author.lstrip("@"),
                text=text[:4000],
                url=f"https://www.youtube.com/watch?v={vid}&lc={cid}",
                created_at=_rel_to_iso(props.get("publishedTime", "")),
                lang=_lang(text),
                engagement=_num(tb.get("likeCountNotliked") or ""),
                reply_count=_num(tb.get("replyCount") or ""),
                conversation_id=vid,
                raw_json=json.dumps({"type": "comment", "via": "innertube", "video_id": vid,
                                     "video_title": video.get("title", "")[:200],
                                     "channel": video.get("channel", ""),
                                     "published_rel": props.get("publishedTime", ""),
                                     "brand_in_text": named, "created_at_approx": True}),
                _brand=named))
        token = _next_token(resp)
        if not token:
            break
        time.sleep(0.4)
    # gate on the WHOLE thread, then cap — capping first would bias the ratio to page 1
    return _thread_gate(cand)[:cap]


def _video_row(v):
    """The video itself as a row — keeps the source non-empty when every comment thread is
    off, and the official channel's own posts are legitimate brand signal."""
    text = f"{v.get('title', '')}. {v.get('desc', '')}".strip(". ").strip()
    if not text or not brand_match(text + " " + v.get("channel", "")):
        return None
    return dict(
        source_id=f"yt:v_{v['vid']}", source=NAME,
        author=v.get("channel", ""), author_name=v.get("channel", ""),
        text=text[:4000], url=f"https://www.youtube.com/watch?v={v['vid']}",
        created_at=v.get("published", "") or "", lang=_lang(text),
        engagement=int(v.get("views") or 0), reply_count=0, conversation_id=v["vid"],
        raw_json=json.dumps({"type": "video", "via": v.get("via", ""),
                             "channel": v.get("channel", "")}))


def _fetch_keyless():
    sess = _session()
    videos = _discover(sess)
    if not videos:
        print(f"  [{NAME}] keyless: no brand-matching videos found")
        return []

    opened = videos[:max(1, MAX_VIDEOS)]
    rows, seen = [], set()
    n_comments = 0
    for v in opened:
        if len(rows) >= LIMIT:
            break
        # one watch-page GET serves both the exact upload date and the comment continuation
        html = _get(sess, WATCH_URL.format(vid=v["vid"]), timeout=30)
        if html and not v.get("published"):
            v["published"] = _upload_date(html)
        row = _video_row(v)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            rows.append(row)
        if not html:
            continue
        for c in _comments_keyless(sess, html, v, max(4, LIMIT - len(rows))):
            if c["source_id"] in seen:
                continue
            seen.add(c["source_id"])
            rows.append(c)
            n_comments += 1
            if len(rows) >= LIMIT:
                break
        time.sleep(0.4)

    print(f"  [{NAME}] {len(rows)} via KEYLESS ({n_comments} comments + "
          f"{len(rows) - n_comments} videos, {len(opened)} of {len(videos)} found opened) "
          f"— set YOUTUBE_API_KEY for exact timestamps + like counts")
    return rows


# ------------------------------------------------------------------ KEYED: Data API v3
def _api(sess, endpoint, params, key):
    """One Data API call. Returns (json, error_reason). Never raises."""
    try:
        r = sess.get(API_URL.format(endpoint=endpoint), params=dict(params, key=key), timeout=30)
        body = r.json() if r.content else {}
    except Exception as e:
        return {}, f"transport: {str(e)[:80]}"
    if r.status_code == 200:
        return body, ""
    err = body.get("error") or {}
    # Prefer the google.rpc.ErrorInfo reason (API_KEY_INVALID / RATE_LIMIT_EXCEEDED …) —
    # the legacy errors[].reason collapses all of those to a useless "badRequest".
    reason = ""
    for d in err.get("details") or []:
        if str(d.get("@type", "")).endswith("ErrorInfo") and d.get("reason"):
            reason = d["reason"]
    if not reason:
        for d in err.get("errors") or []:
            reason = d.get("reason") or reason
    return {}, reason or f"HTTP {r.status_code}: {str(err.get('message'))[:80]}"


def _is(reason, *needles):
    r = (reason or "").replace("_", "").lower()
    return any(n in r for n in needles)


def _fetch_api(key):
    """Preferred path. ~100 units for the search + 1 unit per video's commentThreads call,
    against a free 10,000/day quota. Returns [] (with a printed reason) on any failure so
    fetch() can fall through to the keyless path."""
    sess = _session()
    since = (_now() - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # part=snippet costs the same 100 units as part=id but returns the title/channel/
    # description the brand gate needs — the old code paid 100 units for bare ids and then
    # landed every comment from every hit, including off-brand ones.
    body, err = _api(sess, "search", {
        "q": config.BRAND, "part": "snippet", "type": "video", "order": "date",
        "maxResults": 10, "regionCode": "IN", "relevanceLanguage": "en",
        "publishedAfter": since}, key)
    if err:
        hint = ""
        if _is(err, "apikeyinvalid", "keyinvalid"):
            hint = " — YOUTUBE_API_KEY is rejected; check the key and that the YouTube Data API v3 is enabled"
        elif _is(err, "quota", "ratelimit", "dailylimit"):
            hint = " — daily 10,000-unit quota is spent; it resets at midnight Pacific"
        print(f"  [{NAME}] Data API search failed ({err}){hint} — falling back to keyless")
        return []

    videos = []
    for it in body.get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        if not brand_match(f"{sn.get('title', '')} {sn.get('channelTitle', '')} {sn.get('description', '')}"):
            continue
        videos.append(dict(vid=vid, title=sn.get("title", ""), channel=sn.get("channelTitle", ""),
                           desc=sn.get("description", ""), published=sn.get("publishedAt", ""),
                           views=0, via="api"))
    if not videos:
        print(f"  [{NAME}] Data API search returned no brand-matching video — falling back to keyless")
        return []

    videos = videos[:max(1, MAX_VIDEOS)]
    rows, seen, n_comments = [], set(), 0
    per = max(10, min(100, LIMIT // max(1, len(videos))))
    for v in videos:
        if len(rows) >= LIMIT:
            break
        row = _video_row(v)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            rows.append(row)
        body, err = _api(sess, "commentThreads", {
            "part": "snippet", "videoId": v["vid"], "maxResults": per,
            "order": "relevance", "textFormat": "plainText"}, key)
        if err:
            # commentsDisabled / videoNotFound are normal per-video outcomes, not failures.
            if not _is(err, "commentsdisabled", "videonotfound"):
                print(f"  [{NAME}] {v['vid']} comments: {err}")
            if _is(err, "quota", "ratelimit", "dailylimit"):
                print(f"  [{NAME}] quota exhausted — keeping what landed so far")
                break
            continue
        cand = []
        for it in body.get("items", []):
            sn = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            cid = it.get("id")
            text = (sn.get("textOriginal") or sn.get("textDisplay") or "").strip()
            if not cid or f"yt:{cid}" in seen or not _keep_comment(text):
                continue
            author = sn.get("authorDisplayName", "")
            named = brand_match(text)
            cand.append(dict(
                source_id=f"yt:{cid}", source=NAME,
                author=author, author_name=author.lstrip("@"),
                text=text[:4000],
                url=f"https://www.youtube.com/watch?v={v['vid']}&lc={cid}",
                created_at=sn.get("publishedAt", ""), lang=_lang(text),
                engagement=int(sn.get("likeCount", 0) or 0),
                reply_count=int((it.get("snippet") or {}).get("totalReplyCount", 0) or 0),
                conversation_id=v["vid"],
                raw_json=json.dumps({"type": "comment", "via": "dataapi", "video_id": v["vid"],
                                     "video_title": v.get("title", "")[:200],
                                     "channel": v.get("channel", ""), "brand_in_text": named}),
                _brand=named))
        for row in _thread_gate(cand):
            if len(rows) >= LIMIT:
                break
            seen.add(row["source_id"])
            rows.append(row)
            n_comments += 1

    print(f"  [{NAME}] {len(rows)} via Data API v3 ({n_comments} comments + "
          f"{len(rows) - n_comments} videos, ~{100 + len(videos)} quota units of 10,000/day)")
    return rows


# ------------------------------------------------------------------ entry point
def fetch():
    """Contract: no args, never raises, returns list[dict] keyed on db.RAW_COLS."""
    try:
        key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
        if key:
            rows = _fetch_api(key)
            if rows:
                return rows
        return _fetch_keyless()
    except Exception as e:
        print(f"  [{NAME}] failed: {type(e).__name__}: {str(e)[:110]}")
        return []


if __name__ == "__main__":
    for r in fetch()[:8]:
        # ascii-fold: comment bodies are full of emoji and a cp1252 console cannot print them
        body = r["text"][:80].replace("\n", " ").encode("ascii", "replace").decode()
        print(f"   {r['source_id']:32} {r['created_at'][:19]:20} {body!r}")
