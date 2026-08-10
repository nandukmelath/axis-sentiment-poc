"""Rules for analyze/categories.py — precedence, word boundaries, and the traps."""
import json

import pytest

from analyze.categories import CATEGORY_KEYS, derive, explain


def row(**kw):
    base = {"text": "", "text_masked": None, "intent": "other", "source": "twitter",
            "fraud_type": "none", "fraud_signal": 0, "aspects_json": None}
    return {**base, **kw}


# ---------------------------------------------------------------- precedence
def test_scam_beats_fraud():
    """Scam is a subset the fraud desk handles differently — no chargeback path —
    so it must not be swallowed by the broader fraud rule."""
    r = row(text="got a call from fake Axis executive, total scam", intent="fraud_report")
    assert derive(r) == "scam"


def test_fraud_type_wins_over_text():
    assert derive(row(text="money debited", fraud_type="phishing")) == "scam"


def test_source_is_definitional():
    """An AmbitionBox review is employee feedback even when it discusses the app,
    because the author is staff — the channel decides, not the vocabulary."""
    r = row(text="the mobile app we build keeps crashing", source="ambitionbox")
    assert derive(r) == "employee"


def test_gmaps_is_branch():
    assert derive(row(text="great service", source="gmaps")) == "branch"


# ---------------------------------------------------------------- word boundaries
def test_atm_does_not_match_inside_another_word():
    """Substring matching put every post containing 'format' into Branch & ATM."""
    r = row(text="please share the statement in pdf format")
    assert derive(r) != "branch"


def test_scam_does_not_match_scamper():
    assert derive(row(text="the kids scamper around the lobby")) != "scam"


# ---------------------------------------------------------------- topic, not polarity
def test_positive_branch_review_is_still_branch():
    """Category is a topic. Polarity lives in `sentiment`, so a five-star branch
    review lands in Branch & ATM rather than being mislabelled a complaint."""
    r = row(text="excellent staff at the Whitefield branch", intent="praise")
    assert derive(r) in ("branch", "employee")


# ---------------------------------------------------------------- market gating
def test_broker_note_is_market_news():
    r = row(text="Elara Capital on Axis Bank: maintain Buy, target price 1629", source="news")
    assert derive(r) == "market_news"


def test_complaint_mentioning_shares_is_not_market_news():
    """Gating on intent stops a customer venting about stuck shares being filed
    as investor coverage."""
    r = row(text="worst bank, my shares are stuck and nobody helps", intent="complaint")
    assert derive(r) != "market_news"


# ---------------------------------------------------------------- aspect fallback
def test_aspect_fallback_for_terse_posts():
    """App-store reviews are often three words — too short for any keyword."""
    r = row(text="bad", aspects_json=json.dumps([{"aspect": "branch_atm", "sentiment": "negative"}]))
    assert derive(r) == "branch"


@pytest.mark.parametrize("bad", ["", "not json", "[1,2]", '{"aspect": null}'])
def test_malformed_aspects_json_does_not_raise(bad):
    """aspects_json has taken several shapes across model versions."""
    assert derive(row(text="hello", aspects_json=bad)) in CATEGORY_KEYS


def test_every_row_gets_a_known_category():
    assert derive(row(text="zzzz nothing matches")) == "other"
    assert derive(row(text="")) in CATEGORY_KEYS


def test_explain_returns_a_reason():
    """Routing must be auditable — the rule that fired is part of the output."""
    cat, why = explain(row(text="unauthorised debit of 5000"))
    assert cat == "fraud" and why


def test_masked_text_is_preferred():
    """PII-masked text is what the UI shows; classifying the raw text could route
    on a phone number the operator never sees."""
    r = row(text="call me on 9999999999", text_masked="branch manager was rude")
    assert derive(r) == "employee"
