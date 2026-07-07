"""Industrial hardening: config validation, logging, backup, API readiness/rate-limit/pagination."""
import os
import db
from analytics.features import ensure_tables


def test_config_validate_returns_list():
    import config
    assert isinstance(config.validate(), list)


def test_appstore_rejects_non_https():
    # _open_https guards urlopen against file:// / custom-scheme injection (bandit B310)
    import pytest
    from fetch.appstore import _open_https
    with pytest.raises(ValueError):
        _open_https("file:///etc/passwd")
    with pytest.raises(ValueError):
        _open_https("http://insecure.example.com")


def test_scrapebadger_credits_exhausted_degrades(monkeypatch):
    # 402 (credits out) must skip the source cleanly, not crash the fetch run
    import fetch.scrapebadger as sb
    monkeypatch.setenv("SCRAPEBADGER_API_KEY", "sb_test_dummy")

    def boom(*a, **k):
        raise sb.CreditsExhausted("ScrapeBadger credits exhausted (HTTP 402)")

    monkeypatch.setattr(sb, "search", boom)
    assert sb.fetch() == []


def test_logging_setup_idempotent():
    from logging_setup import get_logger
    log = get_logger("axis_test")
    assert log.handlers
    assert get_logger("axis_test") is log


def test_backup_creates_consistent_snapshot(fresh_db, tmp_path):
    from tools import backup
    out = backup.backup(str(tmp_path))
    if db.DIALECT == "sqlite":
        assert out and os.path.exists(out) and os.path.getsize(out) > 0
    else:
        assert out is None      # Postgres path prints the pg_dump command instead of a file


def test_api_ready(fresh_db):
    from fastapi.testclient import TestClient
    from api.main import app
    assert TestClient(app).get("/ready").json()["status"] == "ready"


def test_api_pagination(fresh_db):
    ensure_tables()
    for i in range(5):
        db.upsert_cluster({"cluster_id": i, "title": f"c{i}", "size": 10 - i, "recent_share": 0.1,
                           "avg_score": -0.1, "top_team": "none", "sample_ids": "[]"})
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    p1 = c.get("/clusters?limit=2&offset=0").json()
    p2 = c.get("/clusters?limit=2&offset=2").json()
    assert len(p1) == 2 and len(p2) == 2 and p1[0] != p2[0]


def test_api_rate_limit(fresh_db, monkeypatch):
    import api.main as m
    monkeypatch.setattr(m, "_RATE", 3)
    m._hits.clear()
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    codes = [c.get("/health").status_code for _ in range(5)]
    assert 429 in codes
