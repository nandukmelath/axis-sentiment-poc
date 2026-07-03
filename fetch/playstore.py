"""Google Play reviews — free, no key (google-play-scraper)."""
from config import PLAY_APP_ID, PLAY_COUNTRY, PLAY_LANG, FETCH_LIMITS


def fetch():
    try:
        from google_play_scraper import reviews, Sort
    except ImportError:
        print("  [play] skipped (pip install google-play-scraper)")
        return []
    try:
        res, _ = reviews(PLAY_APP_ID, lang=PLAY_LANG, country=PLAY_COUNTRY,
                         sort=Sort.NEWEST, count=FETCH_LIMITS["play"])
    except Exception as e:
        print(f"  [play] error: {str(e)[:100]}")
        return []
    out = []
    for r in res:
        out.append(dict(
            source_id=f"play:{r['reviewId']}", source="play", author=r.get("userName", ""),
            text=f"[{r.get('score','')}★] {r.get('content','')}", url=PLAY_APP_ID,
            created_at=r["at"].isoformat() if r.get("at") else "",
            engagement=int(r.get("thumbsUpCount", 0) or 0), lang="en"))
    print(f"  [play] {len(out)}")
    return out
