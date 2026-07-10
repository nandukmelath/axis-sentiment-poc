"""Bluesky firehose (Jetstream) — a REAL free real-time stream, no auth, no API key.
This is the live replacement for the dead free Twitter API. Jetstream pushes every
public Bluesky post as JSON; we keep only posts mentioning the brand.

NOTE: Axis Bank volume on Bluesky is low (Western platform), so this proves the
streaming architecture more than it floods data. Widen BRAND_ALIASES or point at a
higher-volume brand to see it pour in.
"""
import json
from config import BRAND_ALIASES

JETSTREAM = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"


def stream_posts(on_post, stop_event, aliases=None, match_all=False):
    """Connect the firehose and call on_post(row) for each matching post.
    match_all=True yields every post (for smoke-testing the stream)."""
    from websocket import create_connection
    aliases = [a.lower() for a in (aliases or BRAND_ALIASES)]
    ws = create_connection(JETSTREAM, timeout=30)
    try:
        while not stop_event.is_set():
            try:
                evt = json.loads(ws.recv())
            except Exception:
                break
            commit = evt.get("commit") or {}
            if commit.get("operation") != "create" or commit.get("collection") != "app.bsky.feed.post":
                continue
            rec = commit.get("record") or {}
            text = rec.get("text", "")
            if not text:
                continue
            if not match_all and not any(a in text.lower() for a in aliases):
                continue
            did, rkey = evt.get("did", ""), commit.get("rkey", "")
            on_post(dict(
                source_id=f"bsky:{did}/{rkey}", source="bluesky", author=did, text=text,
                url=f"https://bsky.app/profile/{did}/post/{rkey}",
                created_at=rec.get("createdAt", ""), engagement=0, lang="en"))
    finally:
        try:
            ws.close()
        except Exception:
            pass
