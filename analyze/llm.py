"""LLM dispatcher — routes to the configured provider (LLM_PROVIDER).
gemini | ollama | (openai-compatible: groq/openai/openrouter/deepseek/together/cerebras).
Embeddings stay on Gemini (cheap, separate quota)."""
from config import LLM_PROVIDER


def analyze_batch(posts):
    if LLM_PROVIDER == "gemini":
        from analyze.gemini_client import analyze_batch as f
    elif LLM_PROVIDER == "ollama":
        from analyze.ollama_client import analyze_batch as f
    else:
        from analyze.openai_compat import analyze_batch as f
    return f(posts)


def generate_text(prompt, model=None):
    if LLM_PROVIDER == "gemini":
        from analyze.gemini_client import generate_text as f
        return f(prompt)
    elif LLM_PROVIDER == "ollama":
        from analyze.ollama_client import generate_text as f
        return f(prompt)
    from analyze.openai_compat import generate_text as f   # supports a per-call model override
    return f(prompt, model=model)


def embed_texts(texts):
    from analyze.gemini_client import embed_texts as f
    return f(texts)
