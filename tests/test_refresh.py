"""Scheduling policy for fetch/refresh.py.

is_due is pure on purpose — the 2h x 12 policy is the part that must be right,
and it should be provable without touching the network.
"""
import datetime as dt

from fetch import refresh


def hours_ago(n):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=n)


def test_never_refreshed_post_is_due_immediately():
    assert refresh.is_due(hours_ago(1), None, 0) is True


def test_not_due_before_the_interval_elapses():
    assert refresh.is_due(hours_ago(3), hours_ago(1), 1) is False


def test_due_once_the_interval_has_elapsed():
    assert refresh.is_due(hours_ago(5), hours_ago(2.5), 1) is True


def test_retires_at_the_iteration_cap():
    """12 passes is the contract; a 13th must not fire even if time allows."""
    assert refresh.is_due(hours_ago(1), hours_ago(9), refresh.MAX_ITERATIONS) is False


def test_retires_after_the_24h_window():
    """Engagement plateaus — polling a three-day-old post forever is pure waste."""
    assert refresh.is_due(hours_ago(30), hours_ago(5), 3) is False


def test_window_boundary_is_the_full_24h():
    assert refresh.is_due(hours_ago(23), hours_ago(3), 5) is True


def test_missing_first_seen_does_not_crash():
    """fetched_at is null on some backfilled rows; they should still schedule."""
    assert refresh.is_due(None, None, 0) is True


def test_unsupported_sources_are_declared_not_silently_dropped():
    """A refresh system that silently no-ops looks identical to one that works,
    so every skipped source must carry a stated reason."""
    for src in ("play", "appstore"):
        assert src not in refresh.REFRESHERS
        assert refresh.UNSUPPORTED_REASON.get(src)


def test_twitter_refreshes_via_syndication():
    """Twitter was first written off as unrefreshable because live X search is
    dead. Search and per-tweet lookup are different problems — the syndication
    endpoint serves any tweet by id, keyless."""
    assert "twitter" in refresh.REFRESHERS
    assert "twitter" not in refresh.UNSUPPORTED_REASON


def test_registered_refreshers_are_callable():
    for fn in refresh.REFRESHERS.values():
        assert callable(fn)


def test_empty_id_list_makes_no_requests():
    """Guards against a batch of zero turning into a full-table scrape."""
    assert refresh._mastodon([]) == {}
    assert refresh._reddit([]) == {}
