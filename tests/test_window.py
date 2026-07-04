"""Time-window fetch logic (used by the dashboard RUN button)."""
import datetime
from fetch import run_fetch


def test_cutoff_none_for_all_or_unknown():
    assert run_fetch.cutoff(None) is None
    assert run_fetch.cutoff("bogus") is None


def test_cutoff_window_sizes():
    now = datetime.datetime.now(datetime.timezone.utc)
    for w, max_sec in [("1h", 3700), ("1d", 90000), ("1m", 2_620_000)]:
        c = run_fetch.cutoff(w)
        assert c is not None
        assert 0 < (now - c).total_seconds() <= max_sec + 5


def test_within_window():
    now = datetime.datetime.now(datetime.timezone.utc)
    cut = run_fetch.cutoff("1d")
    recent = (now - datetime.timedelta(hours=2)).isoformat()
    old = (now - datetime.timedelta(days=5)).isoformat()
    assert run_fetch._within(recent, cut) is True
    assert run_fetch._within(old, cut) is False
    assert run_fetch._within("not-a-date", cut) is False   # unparseable dropped when window set
    assert run_fetch._within(None, None) is True           # no window keeps everything


def test_within_handles_rfc822_news_dates():
    cut = run_fetch.cutoff("1m")
    now = datetime.datetime.now(datetime.timezone.utc)
    recent_rfc = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    assert run_fetch._within(recent_rfc, cut) is True


def test_run_window_importable():
    import run_window
    assert hasattr(run_window, "run")
