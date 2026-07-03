"""Lenient LLM-output parser — must salvage imperfect Groq/OpenAI JSON into a valid record."""
from analyze.openai_compat import _coerce, _items, _enum
from analyze.schema import Sentiment


def test_coerce_fills_defaults():
    pa = _coerce({"source_id": "x1", "sentiment": "negative"}, "x1")
    assert pa.source_id == "x1"
    assert pa.sentiment.value == "negative"
    assert pa.intent.value == "other"          # default
    assert pa.urgency.value == "low"           # default
    assert pa.aspects == []


def test_coerce_clamps_invalid_enum():
    pa = _coerce({"source_id": "x", "urgency": "SUPER_URGENT", "intent": "raging"}, "x")
    assert pa.urgency.value == "low"           # clamped from invalid
    assert pa.intent.value == "other"


def test_coerce_score_and_confidence_clamped():
    pa = _coerce({"source_id": "x", "score": 5.0, "confidence": -2}, "x")
    assert -1.0 <= pa.score <= 1.0
    assert 0.0 <= pa.confidence <= 1.0


def test_coerce_bad_aspects_dropped_or_fixed():
    pa = _coerce({"source_id": "x", "aspects": [{"aspect": "nonsense", "sentiment": "bad"},
                                                "not-a-dict"]}, "x")
    assert len(pa.aspects) == 1
    assert pa.aspects[0].aspect.value == "other"
    assert pa.aspects[0].sentiment.value == "neutral"


def test_coerce_uses_fallback_sid():
    pa = _coerce({"sentiment": "positive"}, "fallback9")
    assert pa.source_id == "fallback9"


def test_items_under_various_keys():
    assert _items({"results": [{"a": 1}]}) == [{"a": 1}]
    assert _items({"posts": [{"a": 1}]}) == [{"a": 1}]
    assert _items([{"a": 1}]) == [{"a": 1}]
    assert _items({"source_id": "x"}) == [{"source_id": "x"}]   # single object
    assert _items({"nope": 1}) == []


def test_enum_helper():
    assert _enum("negative", Sentiment, "neutral") == "negative"
    assert _enum("garbage", Sentiment, "neutral") == "neutral"
