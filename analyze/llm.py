"""LLM dispatcher with automatic PROVIDER FAILOVER.
Tries LLM_PROVIDER, then each provider in LLM_FALLBACKS, flipping on any failure
(rate/daily limit, missing key, transient error). This multiplies free-tier quota and
survives one provider hitting its cap. Embeddings stay on Gemini.

Providers: gemini | ollama | (openai-compatible: groq/openai/openrouter/deepseek/together/cerebras)
Set e.g.  LLM_PROVIDER=groq  LLM_FALLBACKS=cerebras,openrouter,gemini,ollama
"""
from config import LLM_PROVIDER, LLM_FALLBACKS


def _chain():
    out = []
    for p in [LLM_PROVIDER] + LLM_FALLBACKS:
        if p and p not in out:
            out.append(p)
    return out


def analyze_batch(posts):
    last = None
    for prov in _chain():
        try:
            if prov == "gemini":
                from analyze.gemini_client import analyze_batch as f
                return f(posts)
            if prov == "ollama":
                from analyze.ollama_client import analyze_batch as f
                return f(posts)
            from analyze.openai_compat import analyze_batch as f
            return f(posts, provider=prov)
        except Exception as e:
            last = e
            print(f"  [llm] provider '{prov}' unavailable ({str(e)[:60]}) -> next")
    raise last or RuntimeError("no LLM provider available")


def generate_text(prompt, model=None):
    last = None
    for prov in _chain():
        try:
            if prov == "gemini":
                from analyze.gemini_client import generate_text as f
                return f(prompt)
            if prov == "ollama":
                from analyze.ollama_client import generate_text as f
                return f(prompt)
            from analyze.openai_compat import generate_text as f
            # model override (e.g. BRIEF_MODEL) only applies to the primary provider
            return f(prompt, model=(model if prov == LLM_PROVIDER else None), provider=prov)
        except Exception as e:
            last = e
            print(f"  [llm] provider '{prov}' generate failed ({str(e)[:60]}) -> next")
    raise last or RuntimeError("no LLM provider available")


def embed_texts(texts):
    from analyze.gemini_client import embed_texts as f
    return f(texts)
