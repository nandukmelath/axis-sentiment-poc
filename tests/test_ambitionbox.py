"""AmbitionBox employee-review source — offline parse tests.

Everything here runs against a hand-built __NEXT_DATA__ fixture, so the suite stays offline
and deterministic. The one thing these tests deliberately pin is the IST->UTC conversion:
AmbitionBox prints naive wall-clock IST, and reading it as UTC would push every employee
review 5h30m into the future and quietly corrupt the recency/window logic downstream.
"""
import json


def _page(reviews):
    """Wrap review dicts in the page shape the fetcher actually parses."""
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"reviewsData": reviews}}})
            + "</script></body></html>")


REVIEW = {
    "id": 79661811,
    "companyName": "Axis Bank",
    "created": "2026-05-10 14:06:46",          # IST wall-clock, no offset
    "reviewTitle": "rated by an Assistant Manager in Mumbai",
    "likesText": "Strong exposure to wholesale banking operations.",
    "disLikesText": "Promotions are slow and the process is bureaucratic.",
    "overallCompanyRating": 4,
    "helpfulCount": 3,
    "isEmployerResponded": True,
    "url": "/reviews/axis-bank-reviews?rid=79661811",
    "userName": "Anonymous",
}


def test_ist_is_converted_to_utc():
    from fetch.scrapling_sources import _ab_iso
    # 14:06:46 IST == 08:36:46 UTC. Off-by-5h30m here means every row lands in the future.
    assert _ab_iso("2026-05-10 14:06:46") == "2026-05-10T08:36:46+00:00"


def test_ab_iso_rejects_garbage():
    from fetch.scrapling_sources import _ab_iso
    # '' (not a fabricated date) so run_fetch's --window drops the row instead of passing it.
    for bad in ("", None, "10 May 2026", "not-a-date", 12345):
        assert _ab_iso(bad) == ""


def test_reviews_extracted_from_next_data():
    from fetch.scrapling_sources import _ab_reviews
    assert _ab_reviews(_page([REVIEW]))[0]["id"] == 79661811


def test_reviews_degrade_on_layout_change():
    from fetch.scrapling_sources import _ab_reviews
    # No script tag / renamed key / malformed JSON must yield [] rather than raise: a site
    # redesign should cost us this source for a run, not crash the whole fetch.
    assert _ab_reviews("<html>redesigned</html>") == []
    assert _ab_reviews('<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>') == []
    assert _ab_reviews('<script id="__NEXT_DATA__">{not json</script>') == []
    assert _ab_reviews("") == []


def _run(monkeypatch, pages):
    """Drive fetch_ambitionbox against canned pages instead of the network."""
    import fetch.scrapling_sources as ss
    seq = list(pages)

    class FakeResp:
        status = 200

        def __init__(self, html):
            self.body = html.encode("utf-8")

    monkeypatch.setattr(ss, "_get", lambda url, timeout=30: FakeResp(seq.pop(0)))
    monkeypatch.setattr(ss, "AMBITIONBOX_PAGES", len(seq))
    monkeypatch.setattr(ss, "AMBITIONBOX_SLEEP", 0)
    return ss.fetch_ambitionbox()


def test_row_mapping(monkeypatch):
    rows = _run(monkeypatch, [_page([REVIEW])])
    assert len(rows) == 1
    r = rows[0]
    assert r["source_id"] == "ambitionbox:79661811"
    assert r["source"] == "ambitionbox"
    assert r["created_at"] == "2026-05-10T08:36:46+00:00"
    assert r["url"] == "https://www.ambitionbox.com/reviews/axis-bank-reviews?rid=79661811"
    assert r["engagement"] == 3
    assert r["reply_count"] == 1               # employer responded
    assert r["text"].startswith("[4★]")
    assert "Assistant Manager in Mumbai" in r["text"]
    assert "Pros: Strong exposure" in r["text"]
    assert "Cons: Promotions are slow" in r["text"]


def test_featured_review_pinned_on_every_page_is_deduped(monkeypatch):
    # The site pins one featured review to the top of every page, so without id-dedup a
    # 3-page harvest would double-count it and skew the employee-sentiment mix.
    other = dict(REVIEW, id=999, reviewTitle="rated by a Branch Manager in Pune")
    rows = _run(monkeypatch, [_page([REVIEW]), _page([REVIEW, other])])
    assert [r["source_id"] for r in rows] == ["ambitionbox:79661811", "ambitionbox:999"]


def test_non_axis_and_textless_reviews_dropped(monkeypatch):
    rows = _run(monkeypatch, [_page([
        dict(REVIEW, id=1, companyName="HDFC Bank"),                  # wrong employer
        dict(REVIEW, id=2, likesText="", disLikesText=""),            # ratings-only, nothing to score
        dict(REVIEW, id=3, likesText="", disLikesText="Targets are unrealistic."),
    ])])
    # id=3 survives on cons alone — a cons-only review is exactly the signal we want.
    assert [r["source_id"] for r in rows] == ["ambitionbox:3"]
    assert "Cons: Targets are unrealistic." in rows[0]["text"]


def test_max_items_caps_the_harvest(monkeypatch):
    import fetch.scrapling_sources as ss
    monkeypatch.setattr(ss, "AMBITIONBOX_MAX_ITEMS", 2)
    rows = _run(monkeypatch, [_page([dict(REVIEW, id=i) for i in range(1, 6)])])
    assert len(rows) == 2


def test_http_error_degrades_to_empty(monkeypatch):
    import fetch.scrapling_sources as ss

    class Dead:
        status = 403
        body = b""

    monkeypatch.setattr(ss, "_get", lambda url, timeout=30: Dead())
    monkeypatch.setattr(ss, "AMBITIONBOX_SLEEP", 0)
    assert ss.fetch_ambitionbox() == []        # degrade, never raise


def test_registered_in_pipeline():
    from fetch.run_fetch import SOURCES
    assert "ambitionbox" in [n for n, _ in SOURCES]
