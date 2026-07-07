"""New keyless sources (technofino / rssnews / gdelt / reddit-RSS) — offline parse tests."""
import time
import feedparser


TECHNOFINO_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Axis Bank Credit Card</title>
<item>
  <title>Axis Airtel Devaluation w.e.f 12 April 2026</title>
  <link>https://www.technofino.in/community/threads/axis-airtel-devaluation.47422/</link>
  <pubDate>Tue, 07 Jul 2026 11:37:04 +0000</pubDate>
  <dc:creator>SSV</dc:creator>
  <guid isPermaLink="false">47422</guid>
  <content:encoded><![CDATA[<div class="bbWrapper">Axis Airtel CC is devalued now. They basically killed the card.</div>]]></content:encoded>
</item></channel></rss>"""


def test_technofino_rss_row_mapping(tmp_path, monkeypatch):
    import fetch.technofino as tf
    p = tmp_path / "tf.xml"
    p.write_text(TECHNOFINO_RSS, encoding="utf-8")
    rows = tf._rss_rows(str(p), require_brand=True)
    assert len(rows) == 1
    r = rows[0]
    assert r["source_id"] == "technofino:47422"
    assert r["author"] == "SSV"
    assert "devalued" in r["text"] and "killed the card" in r["text"]
    assert r["conversation_id"] == "tf_47422"
    assert r["url"].startswith("https://www.technofino.in/")


def test_technofino_brand_filter(tmp_path):
    import fetch.technofino as tf
    xml = TECHNOFINO_RSS.replace("Axis Airtel", "HDFC Infinia").replace(
        "Axis Airtel CC is devalued now. They basically killed the card.", "HDFC thread body")
    p = tmp_path / "tf2.xml"
    p.write_text(xml, encoding="utf-8")
    assert tf._rss_rows(str(p), require_brand=True) == []      # non-Axis dropped from firehose


def test_rssnews_staleness_guard():
    from fetch.rss_news import _fresh
    fresh = feedparser.FeedParserDict(entries=[feedparser.FeedParserDict(
        published_parsed=time.gmtime(time.time() - 3600))])
    stale = feedparser.FeedParserDict(entries=[feedparser.FeedParserDict(
        published_parsed=time.gmtime(time.time() - 400 * 86400))])   # the Moneycontrol trap
    empty = feedparser.FeedParserDict(entries=[])
    assert _fresh(fresh) is True
    assert _fresh(stale) is False
    assert _fresh(empty) is False


def test_gdelt_row_mapping():
    from fetch.gdelt import _row
    a = {"title": "Axis Bank cuts MCLR", "url": "https://x.example/a1",
         "domain": "example.in", "seendate": "20260707T123000Z", "language": "English"}
    r = _row(a)
    assert r["source"] == "gdelt" and r["source_id"].startswith("gdelt:")
    assert r["author"] == "example.in"
    assert r["created_at"].startswith("2026-07-07T12:30:00")


def test_reddit_rss_comment_id_regex():
    import re
    pat = r"/comments/[a-z0-9]+/[^/]+/([a-z0-9]+)/?$"
    post = "https://www.reddit.com/r/CreditCardsIndia/comments/1uq4xvu/switch_salary/"
    comment = "https://www.reddit.com/r/CreditCardsIndia/comments/1uq4xvu/switch_salary/ow5ap4o/"
    assert re.search(pat, post) is None            # the post itself must NOT match
    assert re.search(pat, comment).group(1) == "ow5ap4o"


def test_reddit_rss_boilerplate_strip():
    from fetch.reddit import _rss_text
    e = {"summary": '<p>Real complaint text here</p> submitted by <a>/u/someone</a> [link] [comments]'}
    assert _rss_text(e) == "Real complaint text here"


def test_webutil_rejects_non_https():
    import pytest
    from fetch.webutil import get
    with pytest.raises(ValueError):
        get("http://insecure.example.com")


def test_new_sources_registered():
    from fetch.run_fetch import SOURCES
    names = [n for n, _ in SOURCES]
    for s in ("technofino", "rssnews", "gdelt"):
        assert s in names
