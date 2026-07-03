"""Apple App Store reviews — free iTunes RSS JSON (no key).
Auto-resolves the numeric app id from APPSTORE_SEARCH via the iTunes Search API,
so it works keyless without hardcoding an id."""
import json, urllib.request, urllib.parse
from config import APPSTORE_APP_ID, APPSTORE_SEARCH, FETCH_LIMITS


def _resolve_id():
    if APPSTORE_APP_ID:
        return APPSTORE_APP_ID
    try:
        u = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": APPSTORE_SEARCH, "country": "in", "entity": "software", "limit": 1})
        with urllib.request.urlopen(u, timeout=20) as r:
            res = json.load(r).get("results", [])
        return str(res[0]["trackId"]) if res else ""
    except Exception:
        return ""


def fetch():
    app_id = _resolve_id()
    if not app_id:
        print("  [appstore] skipped (could not resolve app id)")
        return []
    url = (f"https://itunes.apple.com/in/rss/customerreviews/id={app_id}"
           f"/sortBy=mostRecent/json")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        entries = data.get("feed", {}).get("entry", [])[1:]  # first entry = app meta
    except Exception as e:
        print(f"  [appstore] error: {str(e)[:100]}")
        return []
    out = []
    for e in entries[: FETCH_LIMITS["appstore"]]:
        try:
            rating = e.get("im:rating", {}).get("label", "")
            out.append(dict(
                source_id=f"appstore:{e['id']['label'].split('/')[-1]}", source="appstore",
                author=e.get("author", {}).get("name", {}).get("label", ""),
                text=f"[{rating}★] {e.get('title',{}).get('label','')}. {e.get('content',{}).get('label','')}",
                url=e.get("link", {}).get("attributes", {}).get("href", ""),
                created_at=e.get("updated", {}).get("label", ""), engagement=0, lang="en"))
        except Exception:
            continue
    print(f"  [appstore] {len(out)}")
    return out
