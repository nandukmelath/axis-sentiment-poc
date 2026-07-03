"""VADER cascade — every post gets a real baseline; only non-positive escalate to the LLM."""
from analyze import cascade


def test_clear_positive_no_llm():
    r = cascade.fast_row({"source_id": "1", "text": "Absolutely love the new Axis app, fantastic and fast!"})
    assert r["sentiment"] == "positive"
    assert not cascade.needs_llm(r)
    assert r["model"] == "vader-fast"


def test_negative_needs_llm():
    r = cascade.fast_row({"source_id": "2", "text": "Axis UPI failed, money debited, worst service, refund now"})
    assert r["sentiment"] == "negative"
    assert cascade.needs_llm(r)


def test_fraud_keyword_flagged():
    r = cascade.fast_row({"source_id": "3", "text": "got a call asking for my OTP, scam pretending to be Axis"})
    assert r["fraud_signal"] == 1


def test_neutral_needs_llm():
    r = cascade.fast_row({"source_id": "4", "text": "Is Axis net banking available on weekends"})
    assert cascade.needs_llm(r)          # neutral also gets LLM depth


def test_every_row_has_required_keys():
    r = cascade.fast_row({"source_id": "5", "text": "ok"})
    for k in ("sentiment", "score", "intent", "urgency", "model", "aspects_json"):
        assert k in r
