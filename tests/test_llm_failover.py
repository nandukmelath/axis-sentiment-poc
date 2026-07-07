"""LLM provider failover — flips to the next provider on rate-limit / failure."""
import pytest
import analyze.llm as llm
import analyze.openai_compat as oc


def test_chain_dedup(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm, "LLM_FALLBACKS", ["groq", "gemini", "gemini", "cerebras"])
    assert llm._chain() == ["groq", "gemini", "cerebras"]


def test_failover_on_rate_limit(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm, "LLM_FALLBACKS", ["cerebras"])
    calls = []

    def fake(posts, provider=None):
        calls.append(provider)
        if provider == "groq":
            raise RuntimeError("Error code: 429 - rate limit reached")
        return ["OK"]

    monkeypatch.setattr(oc, "analyze_batch", fake)
    out = llm.analyze_batch([{"source_id": "1", "text": "x"}])
    assert out == ["OK"]
    assert calls == ["groq", "cerebras"]      # tried primary, then failed over


def test_generate_text_failover(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm, "LLM_FALLBACKS", ["cerebras"])

    def fake(prompt, model=None, provider=None):
        if provider == "groq":
            raise RuntimeError("429 quota exceeded")
        return "brief"

    monkeypatch.setattr(oc, "generate_text", fake)
    assert llm.generate_text("hi", model="llama-3.1-8b-instant") == "brief"


def test_freellmapi_provider_registered():
    from config import OPENAI_COMPAT
    assert "freellmapi" in OPENAI_COMPAT
    base, key_env, model = OPENAI_COMPAT["freellmapi"]
    assert "/v1" in base and key_env == "FREELLM_API_KEY"


def test_loads_strips_json_fences():
    # FreeLLMAPI-routed Gemini wraps JSON in ```json fences even under json_object mode.
    from analyze.openai_compat import _loads
    assert _loads('```json\n{"ok": true}\n```') == {"ok": True}
    assert _loads('```\n[{"a":1}]\n```') == [{"a": 1}]
    assert _loads('{"plain": 1}') == {"plain": 1}
    # salvage JSON embedded in prose
    assert _loads('Here you go: {"x": 2} hope that helps') == {"x": 2}


def test_all_providers_fail_raises(monkeypatch):
    monkeypatch.setattr(llm, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm, "LLM_FALLBACKS", [])

    def fail(posts, provider=None):
        raise RuntimeError("429")

    monkeypatch.setattr(oc, "analyze_batch", fail)
    with pytest.raises(Exception):
        llm.analyze_batch([{"source_id": "1", "text": "x"}])
