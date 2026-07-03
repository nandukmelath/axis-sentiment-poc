"""Ollama local LLM client — free, unlimited, offline (RBI data-residency story).
Setup: install Ollama, `ollama pull llama3.1`, then LLM_PROVIDER=ollama."""
import json
import requests
from config import OLLAMA_MODEL, OLLAMA_HOST
from analyze.schema import PostAnalysis
from analyze.prompt import SYSTEM, USER_TEMPLATE


def _chat(messages, fmt=None, timeout=600):
    body = {"model": OLLAMA_MODEL, "messages": messages, "stream": False, "options": {"temperature": 0.1}}
    if fmt:
        body["format"] = fmt
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


def analyze_batch(posts):
    payload = json.dumps(
        [{"source_id": p["source_id"], "source": p.get("source", ""), "text": (p.get("text") or "")[:4000]}
         for p in posts], ensure_ascii=False)
    schema = {"type": "object", "properties": {"results": {"type": "array", "items": PostAnalysis.model_json_schema()}}}
    content = _chat([{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TEMPLATE.format(payload=payload)}], fmt=schema)
    try:
        items = json.loads(content).get("results", [])
    except Exception:
        return []
    out = []
    for d in items:
        try:
            out.append(PostAnalysis(**d))
        except Exception:
            pass
    return out


def generate_text(prompt):
    return _chat([{"role": "user", "content": prompt}])
