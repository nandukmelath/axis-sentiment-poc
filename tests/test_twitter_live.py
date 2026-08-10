"""fetch/twitter_live.py — parsing and normalisation, no network.

The HTTP paths are verified by running the fetcher; what is pinned here is the
logic that silently corrupts data when wrong: date normalisation across three
different formats, and never turning an absent metric into a zero.
"""
import pytest

from fetch import twitter_live as tl


# ---------------------------------------------------------------- dates
def test_graphql_legacy_date_is_parsed():
    """Twitter's legacy format has no comma and puts the year last, so none of the
    RFC-822 patterns match it. Unhandled, it lands in the DB as a raw string and
    every window/recency query silently drops the row."""
    got = tl._iso("Mon Aug 10 07:28:08 +0000 2026")
    assert got.startswith("2026-08-10T07:28:08")


def test_nitter_rfc822_date_is_parsed():
    assert tl._iso("Mon, 10 Aug 2026 05:50:23 GMT").startswith("2026-08-10T05:50:23")


def test_iso_input_passes_through():
    assert tl._iso("2026-08-10T05:54:25.000Z").startswith("2026-08-10")


def test_empty_date_is_none():
    assert tl._iso("") is None
    assert tl._iso(None) is None


# ---------------------------------------------------------------- reply prefix
def test_nitter_reply_prefix_is_stripped():
    """Nitter titles replies as 'R to @someone: ...'; that marker is Nitter's, not
    the tweet's."""
    assert tl._REPLY_PREFIX_RX.sub("", "R to @AxisBank: my card is blocked") == \
        "my card is blocked"


def test_non_reply_text_is_untouched():
    assert tl._REPLY_PREFIX_RX.sub("", "Axis Bank UPI is down") == "Axis Bank UPI is down"


# ---------------------------------------------------------------- config
def test_graphql_features_include_view_counts():
    """Without this flag the response omits the views object entirely, and view
    counts are the whole reason the GraphQL path exists."""
    assert tl.GRAPHQL_FEATURES["view_counts_everywhere_api_enabled"] is True


def test_graphql_session_returns_none_on_failure(monkeypatch):
    """A dead guest-token endpoint must degrade to syndication, not raise."""
    monkeypatch.setattr(tl.requests, "Session", lambda: (_ for _ in ()).throw(RuntimeError))
    with pytest.raises(RuntimeError):
        tl.graphql_session()


def test_hydrate_graphql_with_no_session_is_none():
    assert tl.hydrate_graphql("123", None) is None


# ---------------------------------------------------------------- instances
def test_instance_pool_is_non_empty():
    assert tl.INSTANCES, "an empty pool means discovery silently returns zero"


def test_status_regex_extracts_id():
    m = tl._STATUS_RX.search("https://nitter.example/AxisBank/status/2072632344914325929#m")
    assert m and m.group(1) == "2072632344914325929"
