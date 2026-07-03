"""Gemini wrapper: batched structured extraction + embeddings.
Free-tier safe: batches posts, low temperature, exponential backoff, JSON schema."""
import os, re, json, time
from typing import List
from google import genai
from google.genai import types


def _retry_secs(msg: str, attempt: int) -> float:
    """Respect the server's retryDelay on 429; else exponential backoff. Capped."""
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", msg) or \
        re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
    server = float(m.group(1)) if m else 0.0
    return min(max(server, 2 ** attempt) + 1.5, 65.0)

from config import GEMINI_MODEL, EMBED_MODEL, MAX_RETRIES
from analyze.schema import PostAnalysis
from analyze.prompt import SYSTEM, USER_TEMPLATE

_client = None
def client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set (put it in .env)")
        _client = genai.Client(api_key=key)
    return _client


def analyze_batch(posts: List[dict]) -> List[PostAnalysis]:
    """posts: [{source_id, text, source?, engagement?}] -> list[PostAnalysis]."""
    payload = json.dumps(
        [{"source_id": p["source_id"], "source": p.get("source", ""),
          "engagement": p.get("engagement", 0), "text": p.get("text", "")[:4000]} for p in posts],
        ensure_ascii=False,
    )
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=list[PostAnalysis],
    )
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client().models.generate_content(
                model=GEMINI_MODEL,
                contents=USER_TEMPLATE.format(payload=payload),
                config=cfg,
            )
            items = getattr(resp, "parsed", None)
            if not items:  # fallback: parse raw JSON
                items = [PostAnalysis(**d) for d in json.loads(resp.text)]
            return items
        except Exception as e:  # noqa
            last = e
            wait = _retry_secs(str(e), attempt)
            print(f"  gemini retry {attempt+1}/{MAX_RETRIES} in {wait:.0f}s ({type(e).__name__}: {str(e)[:100]})")
            time.sleep(wait)
    raise RuntimeError(f"analyze_batch failed after {MAX_RETRIES} retries: {last}")


def generate_text(prompt: str, model: str = None) -> str:
    """Plain text generation with the same retry/backoff as analyze_batch."""
    from config import GEMINI_MODEL
    model = model or GEMINI_MODEL
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            return client().models.generate_content(model=model, contents=prompt).text
        except Exception as e:  # noqa
            last = e
            wait = _retry_secs(str(e), attempt)
            print(f"  gemini retry {attempt+1}/{MAX_RETRIES} in {wait:.0f}s ({type(e).__name__})")
            time.sleep(wait)
    raise RuntimeError(f"generate_text failed after {MAX_RETRIES} retries: {last}")


def verify_fraud(text: str) -> bool:
    """Second-pass precision check for flagged posts (fraud/critical)."""
    cfg = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema={"type": "object", "properties": {"is_fraud": {"type": "boolean"}},
                         "required": ["is_fraud"]},
    )
    try:
        r = client().models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Is this post an actual fraud/scam/impersonation signal about a bank? "
                     f"Answer strictly.\n\nPOST: {text[:2000]}",
            config=cfg,
        )
        return bool(json.loads(r.text).get("is_fraud"))
    except Exception:
        return True  # fail-safe: keep the flag


def embed_texts(texts: List[str]) -> List[List[float]]:
    out = []
    for i in range(0, len(texts), 100):  # embed in chunks
        chunk = [t[:2000] for t in texts[i:i+100]]
        r = client().models.embed_content(model=EMBED_MODEL, contents=chunk)
        out.extend([e.values for e in r.embeddings])
    return out
