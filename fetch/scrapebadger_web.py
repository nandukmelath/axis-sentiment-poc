"""Shared ScrapeBadger UNIVERSAL WEB SCRAPE helper — reused by every fetcher that has no
native ScrapeBadger endpoint (consumercomplaints, mouthshut, trustpilot, googlereviews, ...).

Endpoint: POST https://scrapebadger.com/v1/web/scrape   header x-api-key
Also exposes a thin native-GET helper (sb_get) for the structured endpoints
(linkedin/*, tiktok/*) so those fetchers don't have to hand-roll their own requests plumbing.

Auth: SCRAPEBADGER_API_KEY in .env.

CREDIT PRE-FLIGHT: every paid call below is fronted by credit_preflight() from
fetch/scrapebadger.py. It reads the balance ONCE per process from the FREE (zero-credit)
GET /v1/account/me, memoises it, and short-circuits with CreditsExhausted when the balance is
provably 0 — so a broke account costs one free request for the whole harvest instead of one
wasted HTTP 402 per source. A positive or unknown balance is a complete no-op. The pre-flight
lives in scrapebadger.py because this module imports FROM it (the reverse would be circular).
"""
import os
import time

import requests

from fetch.scrapebadger import CreditsExhausted, credit_preflight, note_402

BASE = "https://scrapebadger.com/v1"
SCRAPE_URL = f"{BASE}/web/scrape"

# Raised on a live HTTP 402 (ran dry mid-run). The pre-flight raises its own, shorter message.
_402_MSG = ("ScrapeBadger credits exhausted (HTTP 402) — top up at scrapebadger.com; "
            "remaining paid sources will skip for free this run")


def has_key():
    return bool(os.getenv("SCRAPEBADGER_API_KEY"))


def _key():
    k = os.getenv("SCRAPEBADGER_API_KEY")
    if not k:
        raise RuntimeError("SCRAPEBADGER_API_KEY not set (.env)")
    return k


def _headers():
    return {"x-api-key": _key()}


def _post_with_backoff(url, json_body, timeout, retries=4):
    """POST with 429 backoff; raise CreditsExhausted on 402; return {} on any other failure
    (never raises on network errors)."""
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(), json=json_body, timeout=timeout)
        except requests.RequestException as e:
            print(f"  [scrapebadger_web] network error: {e}")
            return {}
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print("  [scrapebadger_web] non-JSON 200 response")
                return {}
        if r.status_code == 429:
            wait = min(2 ** attempt + 2, 30)
            print(f"    rate-limited (429), waiting {wait}s ...")
            time.sleep(wait)
            continue
        if r.status_code == 402:
            note_402()   # pin balance to 0 so the other paid sources skip for free
            raise CreditsExhausted(_402_MSG)
        print(f"  [scrapebadger_web] HTTP {r.status_code}: {r.text[:160]}")
        return {}
    return {}


def _get_with_backoff(url, params, timeout, retries=4):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        except requests.RequestException as e:
            print(f"  [scrapebadger_web] network error: {e}")
            return {}
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print("  [scrapebadger_web] non-JSON 200 response")
                return {}
        if r.status_code == 429:
            wait = min(2 ** attempt + 2, 30)
            print(f"    rate-limited (429), waiting {wait}s ...")
            time.sleep(wait)
            continue
        if r.status_code == 402:
            note_402()   # pin balance to 0 so the other paid sources skip for free
            raise CreditsExhausted(_402_MSG)
        print(f"  [scrapebadger_web] HTTP {r.status_code}: {r.text[:160]}")
        return {}
    return {}


def web_scrape(url, ai_prompt=None, fmt="markdown", render_js=True, anti_bot=False,
               country="in", wait_after_load=2500, max_cost=None, timeout=90):
    """POST /v1/web/scrape. Returns the parsed JSON dict, or {} on any failure (never raises
    except CreditsExhausted on HTTP 402, which callers should catch and degrade to [])."""
    if not has_key():
        print("  [scrapebadger_web] SCRAPEBADGER_API_KEY not set — skipping.")
        return {}
    credit_preflight()   # free + cached: raises CreditsExhausted on a known-zero balance
    body = {
        "url": url,
        "format": fmt,
        "render_js": render_js,
        "anti_bot": anti_bot,
        "country": country,
        "wait_after_load": wait_after_load,
    }
    if ai_prompt:
        body["ai_extract"] = True
        body["ai_prompt"] = ai_prompt[:2000]
    if max_cost is not None:
        body["max_cost"] = max_cost
    return _post_with_backoff(SCRAPE_URL, body, timeout)


def sb_get(path, params=None, timeout=60):
    """Native GET helper for structured endpoints, e.g. sb_get('/linkedin/companies/axis-bank').
    Returns the parsed JSON dict, or {} on any failure (CreditsExhausted still raised on 402)."""
    if not has_key():
        print("  [scrapebadger_web] SCRAPEBADGER_API_KEY not set — skipping.")
        return {}
    credit_preflight()   # free + cached: raises CreditsExhausted on a known-zero balance
    url = BASE + path
    return _get_with_backoff(url, params or {}, timeout)


def extract_items(resp, ai_prompt_used):
    """Return resp['ai_extraction'] if it's a non-empty list, else []. Callers fall back to
    parsing resp['content'] (markdown/text) themselves when this returns []."""
    if not ai_prompt_used or not resp:
        return []
    items = resp.get("ai_extraction")
    if isinstance(items, list) and items:
        return items
    return []
