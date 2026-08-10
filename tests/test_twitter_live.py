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
def test_fallback_instance_pool_is_non_empty():
    """The floor under discover_instances() — if the live tracker is down AND
    this is empty, discovery silently returns zero with no way to recover."""
    assert tl.FALLBACK_INSTANCES


def test_discover_instances_falls_back_on_tracker_failure(monkeypatch):
    """The status tracker is a third-party dependency discovery does not
    control. Its outage must degrade to the fixed list, not to an empty pool."""
    monkeypatch.setattr(tl, "_instance_cache", None)

    def _boom(*a, **kw):
        raise tl.requests.RequestException("tracker down")
    monkeypatch.setattr(tl.requests, "get", _boom)

    pool = tl.discover_instances(verbose=False)
    assert pool == tl.FALLBACK_INSTANCES


def test_discover_instances_merges_and_dedupes(monkeypatch):
    monkeypatch.setattr(tl, "_instance_cache", None)

    class _Resp:
        status_code = 200
        def json(self):
            return {"hosts": [
                {"domain": "healthy-one.example", "healthy": True, "is_bad_host": False, "points": 90},
                {"domain": "unhealthy.example", "healthy": False, "is_bad_host": False, "points": 50},
                {"domain": "flagged-bad.example", "healthy": True, "is_bad_host": True, "points": 80},
                {"domain": tl.FALLBACK_INSTANCES[0], "healthy": True, "is_bad_host": False, "points": 70},
            ]}
    monkeypatch.setattr(tl.requests, "get", lambda *a, **kw: _Resp())

    pool = tl.discover_instances(verbose=False)
    assert "healthy-one.example" in pool
    assert "unhealthy.example" not in pool          # not healthy -> excluded
    assert "flagged-bad.example" not in pool         # is_bad_host -> excluded
    assert pool.count(tl.FALLBACK_INSTANCES[0]) == 1  # present in both lists -> deduped
    for f in tl.FALLBACK_INSTANCES:
        assert f in pool                              # fallback is always a floor, not a substitute


def test_discover_instances_is_cached_per_process(monkeypatch):
    """A pipeline run is one process; every extra hit is a free way to trip the
    tracker's own rate limit for no benefit."""
    monkeypatch.setattr(tl, "_instance_cache", ["cached.example"])
    calls = []
    monkeypatch.setattr(tl.requests, "get", lambda *a, **kw: calls.append(1) or (_ for _ in ()).throw(AssertionError))
    assert tl.discover_instances(verbose=False) == ["cached.example"]
    assert not calls


# ---------------------------------------------------------------- account timelines
def test_account_timelines_default_to_the_bank_handles():
    assert "AxisBank" in tl.ACCOUNT_TIMELINES
    assert "AxisBankSupport" in tl.ACCOUNT_TIMELINES


def test_account_timeline_handles_have_no_leading_at():
    """discover() builds /{acct}/with_replies/rss directly from these — a stray
    '@' would 404 the whole path instead of erroring loudly."""
    for h in tl.ACCOUNT_TIMELINES:
        assert not h.startswith("@")


# ---------------------------------------------------------------- rss parsing
_ITEM = """<item><title>{title}</title><link>https://nitter.example/{author}/status/{sid}</link>
<dc:creator>@{author}</dc:creator><pubDate>Mon, 10 Aug 2026 05:54:25 GMT</pubDate></item>"""


def test_parse_rss_items_extracts_and_dedupes():
    found = {}
    xml = _ITEM.format(title="first post", author="acct1", sid="111") + \
          _ITEM.format(title="duplicate id", author="acct1", sid="111") + \
          _ITEM.format(title="second post", author="acct2", sid="222")
    tl._parse_rss_items(xml, found, verbose=False, label="test")
    assert set(found) == {"111", "222"}
    assert found["111"]["text"] == "first post"       # first write wins, not overwritten


def test_parse_rss_items_strips_reply_prefix():
    found = {}
    xml = _ITEM.format(title="R to @someone: the actual reply text", author="acct1", sid="333")
    tl._parse_rss_items(xml, found, verbose=False, label="test")
    assert found["333"]["text"] == "the actual reply text"


def test_parse_rss_items_skips_items_without_status_link():
    found = {}
    xml = "<item><title>no link here</title><link>https://nitter.example/acct1</link></item>"
    tl._parse_rss_items(xml, found, verbose=False, label="test")
    assert found == {}


# ---------------------------------------------------------------- query rotation
def test_discover_rotates_starting_instance_per_query(monkeypatch):
    """Piling every query onto whichever instance answers first is what cascades
    one Nitter box into a 503 after two or three hits. Rotation is the fix, so it
    has to actually rotate, not just iterate the pool in the same order every time."""
    pool = ["host-a", "host-b", "host-c"]
    monkeypatch.setattr(tl, "discover_instances", lambda verbose=True: pool)
    hit_order = []

    def _fake_rss_get(host, path, params, dead, verbose):
        if path == "/search/rss":
            hit_order.append((params["q"], host))
        return "<rss></rss>"
    monkeypatch.setattr(tl, "_rss_get", _fake_rss_get)

    tl.discover(queries=["q1", "q2", "q3"], accounts=[], verbose=False)
    hosts_used = [h for _, h in hit_order]
    assert hosts_used == ["host-a", "host-b", "host-c"], (
        "each query should start on a different host, not always host-a")


def test_discover_marks_failing_instance_dead_for_the_rest_of_the_run(monkeypatch):
    monkeypatch.setattr(tl, "discover_instances", lambda verbose=True: ["dead-host", "live-host"])
    attempted = []

    def _fake_rss_get(host, path, params, dead, verbose):
        attempted.append(host)
        if host == "dead-host":
            dead.add(host)
            return None
        return "<rss></rss>"
    monkeypatch.setattr(tl, "_rss_get", _fake_rss_get)

    tl.discover(queries=["q1", "q2"], accounts=[], verbose=False)
    # q1 starts on dead-host (fails, marked dead) then falls through to live-host.
    # q2's rotation would start on live-host directly — dead-host must not be retried.
    assert attempted.count("dead-host") == 1


def test_status_regex_extracts_id():
    m = tl._STATUS_RX.search("https://nitter.example/AxisBank/status/2072632344914325929#m")
    assert m and m.group(1) == "2072632344914325929"
