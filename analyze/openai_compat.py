"""OpenAI-compatible LLM client — works with Groq, OpenAI, OpenRouter, DeepSeek,
Together, Cerebras (any /v1/chat/completions endpoint). Set LLM_PROVIDER + the
matching *_API_KEY in .env. JSON-object output; the VADER baseline is the safety net,
so any post the LLM can't parse simply stays VADER-scored (never lost)."""
import os, json, time, re
from openai import OpenAI
from config import LLM_PROVIDER, LLM_MODEL, OPENAI_COMPAT, MAX_RETRIES
from analyze.schema import (PostAnalysis, Sentiment, Emotion, Urgency, Intent, Aspect,
                            Team, RBICategory)
from analyze.prompt import SYSTEM, USER_TEMPLATE

_FIELDS = ", ".join(PostAnalysis.model_fields.keys())

# Groq/OpenAI json_object output doesn't enforce the pydantic schema, so coerce partial
# output into a valid record (defaults for missing fields, clamp enums) instead of dropping it.
_DEFAULTS = dict(sentiment="neutral", score=0.0, emotion="neutral", emotion_intensity=3,
                 sarcasm=False, aspects=[], intent="other", urgency="low", urgency_reason="",
                 product="unspecified", root_cause="", rbi_category="not_applicable",
                 recommended_team="none", recommended_action="", churn_risk=False,
                 fraud_signal=False, fraud_type="none", pii_present=False, theme="",
                 summary="", confidence=0.5)


def _enum(val, cls, default):
    try:
        cls(val)
        return val
    except Exception:
        return default


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("results", "posts", "analysis", "items", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        if "source_id" in data or "sentiment" in data:
            return [data]
    return []


def _coerce(d, fallback_sid):
    m = {**_DEFAULTS, **{k: v for k, v in d.items() if v is not None}}
    m["source_id"] = str(d.get("source_id") or fallback_sid)
    m["sentiment"] = _enum(m["sentiment"], Sentiment, "neutral")
    m["emotion"] = _enum(m["emotion"], Emotion, "neutral")
    m["urgency"] = _enum(m["urgency"], Urgency, "low")
    m["intent"] = _enum(m["intent"], Intent, "other")
    m["rbi_category"] = _enum(m["rbi_category"], RBICategory, "not_applicable")
    m["recommended_team"] = _enum(m["recommended_team"], Team, "none")
    m["score"] = max(-1.0, min(1.0, _num(m["score"], 0.0)))
    m["confidence"] = max(0.0, min(1.0, _num(m["confidence"], 0.5)))
    try:
        m["emotion_intensity"] = int(m["emotion_intensity"])
    except (TypeError, ValueError):
        m["emotion_intensity"] = 3
    for b in ("sarcasm", "churn_risk", "fraud_signal", "pii_present"):
        m[b] = bool(m.get(b))
    asp = []
    for a in (m.get("aspects") or []):
        if isinstance(a, dict):
            asp.append({"aspect": _enum(a.get("aspect", "other"), Aspect, "other"),
                        "sentiment": _enum(a.get("sentiment", "neutral"), Sentiment, "neutral"),
                        "evidence": str(a.get("evidence") or "")[:200]})
    m["aspects"] = asp
    for s in ("urgency_reason", "product", "root_cause", "recommended_action",
              "fraud_type", "theme", "summary"):
        m[s] = str(m.get(s) or "")
    return PostAnalysis(**m)
SCHEMA_HINT = ('\nReturn a JSON object {"results": [ ... ]} — one item per input post, echoing source_id. '
               f'Each item MUST have these fields: {_FIELDS}. '
               '"aspects" is a list of {aspect, sentiment, evidence}. Use the enums from the instructions.')


def _client_model(provider=None, model=None):
    p = provider or LLM_PROVIDER
    if p not in OPENAI_COMPAT:
        raise RuntimeError(f"LLM_PROVIDER '{p}' not OpenAI-compatible")
    base, key_env, default_model = OPENAI_COMPAT[p]
    key = os.getenv(key_env) or os.getenv("LLM_API_KEY")
    if not key:
        raise RuntimeError(f"{key_env} not set (.env)")
    # LLM_MODEL global override applies only to the primary provider; fallbacks use their default.
    chosen = model or (LLM_MODEL if (provider is None or provider == LLM_PROVIDER) else "") or default_model
    return OpenAI(base_url=base, api_key=key), chosen, p


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _loads(content):
    """Parse model JSON tolerant of ```json fences / prose wrapping. Some providers
    (Gemini via FreeLLMAPI) still wrap output in markdown even under json_object mode."""
    if content is None:
        raise ValueError("empty LLM response")
    s = _FENCE.sub("", content.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # salvage the first {...} or [...] block from surrounding prose
        m = re.search(r"[\{\[].*[\}\]]", s, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _is_rate(e):
    s = str(e).lower()
    return "429" in s or "rate limit" in s or "quota" in s


def _retry_after(e):
    try:
        h = getattr(getattr(e, "response", None), "headers", {}) or {}
        v = h.get("retry-after") or h.get("Retry-After")
        return float(v) if v else None
    except Exception:
        return None


def analyze_batch(posts, provider=None):
    client, model, prov = _client_model(provider)   # fails fast on missing key
    payload = json.dumps(
        [{"source_id": p["source_id"], "source": p.get("source", ""), "text": (p.get("text") or "")[:4000]}
         for p in posts], ensure_ascii=False)
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0.1, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM + SCHEMA_HINT},
                          {"role": "user", "content": USER_TEMPLATE.format(payload=payload)}])
            data = _loads(resp.choices[0].message.content)
            items = _items(data)
            out = []
            for i, d in enumerate(items):
                if not isinstance(d, dict):
                    continue
                fallback = posts[i]["source_id"] if i < len(posts) else ""
                try:
                    out.append(_coerce(d, fallback))
                except Exception:
                    pass
            return out
        except Exception as e:  # noqa
            last = e
            if _is_rate(e):     # rate/daily limit — 1 quick retry, then raise so the dispatcher fails over
                if attempt >= 1:
                    break
                time.sleep(min(_retry_after(e) or 3, 5))
            else:
                time.sleep(min(2 ** attempt + 1, 20))
            print(f"  {prov} retry {attempt+1}/{MAX_RETRIES} ({str(e)[:70]})")
    raise RuntimeError(f"{prov} analyze_batch failed: {last}")


def generate_text(prompt, model=None, provider=None):
    client, chosen, prov = _client_model(provider, model)
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = client.chat.completions.create(model=chosen, messages=[{"role": "user", "content": prompt}])
            return r.choices[0].message.content
        except Exception as e:  # noqa
            last = e
            if _is_rate(e):     # rate/daily limit — 1 quick retry, then raise to fail over
                if attempt >= 1:
                    break
                time.sleep(min(_retry_after(e) or 3, 5))
            else:
                time.sleep(min(2 ** attempt + 1, 15))
            print(f"  {prov} generate retry {attempt+1}/{MAX_RETRIES} ({str(e)[:70]})")
    raise RuntimeError(f"{prov} generate_text failed: {last}")
