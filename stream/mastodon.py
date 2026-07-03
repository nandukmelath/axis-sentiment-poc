"""Mastodon public firehose — free real-time stream over SSE.
Second free streaming source (with Bluesky) to replace the dead free Twitter API.

Some instances allow anonymous public streaming; many now require a token — set
MASTODON_TOKEN (and optionally MASTODON_INSTANCE) if you get a 401.
"""
import json, re
import requests
from config import MASTODON_INSTANCE, MASTODON_TOKEN, BRAND_ALIASES

_TAG = re.compile(r"<[^>]+>")


def _strip(h):
    return _TAG.sub("", h or "").replace("&amp;", "&").strip()


def stream_posts(on_post, stop_event, aliases=None, match_all=False):
    aliases = [a.lower() for a in (aliases or BRAND_ALIASES)]
    url = f"https://{MASTODON_INSTANCE}/api/v1/streaming/public"
    headers = {"Authorization": f"Bearer {MASTODON_TOKEN}"} if MASTODON_TOKEN else {}
    with requests.get(url, headers=headers, stream=True, timeout=(10, 90)) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if stop_event.is_set():
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                st = json.loads(line[5:].strip())
            except Exception:
                continue
            text = _strip(st.get("content", ""))
            if not text:
                continue
            if not match_all and not any(a in text.lower() for a in aliases):
                continue
            acct = (st.get("account") or {}).get("acct", "")
            on_post(dict(
                source_id=f"masto:{st.get('id')}", source="mastodon", author=acct, text=text,
                url=st.get("url", ""), created_at=st.get("created_at", ""),
                engagement=int(st.get("favourites_count", 0) or 0), lang=st.get("language") or "en"))
