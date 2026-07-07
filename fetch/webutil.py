"""Shared helpers for keyless web fetchers (browser UA, https guard, brand filter)."""
import re
import requests

# Plain browser UA — several sources (Technofino/Cloudflare, Reddit RSS) 403 script UAs.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

# In banking/finance contexts a bare "axis" is Axis Bank with negligible false positives.
BRAND_RE = re.compile(r"\baxis\b", re.IGNORECASE)


def brand_match(text):
    return bool(BRAND_RE.search(text or ""))


def get(url, timeout=20):
    """https-only GET with browser headers, redirects followed (bandit B310-safe)."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https URL: {url[:60]}")
    return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
