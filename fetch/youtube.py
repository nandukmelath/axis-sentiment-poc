"""YouTube comments — Data API v3 (free key, 10k units/day). Needs YOUTUBE_API_KEY.
search.list costs 100 units each, so we search once and pull comments from top videos."""
import os
from config import BRAND, FETCH_LIMITS


def fetch():
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        print("  [youtube] skipped (set YOUTUBE_API_KEY)")
        return []
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("  [youtube] skipped (pip install google-api-python-client)")
        return []
    yt = build("youtube", "v3", developerKey=key, cache_discovery=False)
    out = []
    try:
        vids = yt.search().list(q=BRAND, part="id", type="video", order="date", maxResults=5).execute()
        video_ids = [i["id"]["videoId"] for i in vids.get("items", []) if i["id"].get("videoId")]
    except Exception as e:
        print(f"  [youtube] search error: {str(e)[:100]}")
        return []
    per = max(5, FETCH_LIMITS["youtube"] // max(1, len(video_ids)))
    for vid in video_ids:
        try:
            c = yt.commentThreads().list(part="snippet", videoId=vid, maxResults=min(per, 100),
                                         order="time", textFormat="plainText").execute()
            for it in c.get("items", []):
                sn = it["snippet"]["topLevelComment"]["snippet"]
                out.append(dict(
                    source_id=f"yt:{it['id']}", source="youtube", author=sn.get("authorDisplayName", ""),
                    text=sn.get("textOriginal", ""), url=f"https://youtu.be/{vid}",
                    created_at=sn.get("publishedAt", ""),
                    engagement=int(sn.get("likeCount", 0) or 0), lang="en"))
        except Exception as e:
            print(f"  [youtube] {vid} error: {str(e)[:80]}")
    print(f"  [youtube] {len(out)}")
    return out
