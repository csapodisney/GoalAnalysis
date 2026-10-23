import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from goal_analysis.jobs import grok_advisory as job
from goal_analysis.jobs.sports_context_archive import archive_grok_result
from goal_analysis.storage import Database
from goal_analysis.storage.sports_news import NewsStore

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)
PUBLISHED = NOW - timedelta(hours=1)
POST_ID = (int(PUBLISHED.timestamp() * 1000) - 1288834974657) << 22
SOURCE = f"https://x.com/ClubNews/status/{POST_ID}"


def configure(root):
    path = root / "config/grok.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"enabled": True, "mode": "SHADOW"}), "utf-8")


def report():
    return {"run_id": "news-archive-test", "date": "2026-10-18", "observed_at": NOW.isoformat(),
            "recommendations": [{"profile_id": "daily223", "recommendation_id": "protected",
                "status": "READY", "legs": [{"fixture_id": "api_football:123",
                    "home_team": "Home", "away_team": "Away",
                    "kickoff": (NOW + timedelta(hours=5)).isoformat(), "market_key": "totals_2_5",
                    "selection_key": "over", "period": "FULL_TIME", "decimal_price": 2.3,
                    "model_probability": .55}]}]}


def accepted_result():
    return {"status": "COMPLETE", "model": "grok-4.7", "citations": [SOURCE],
            "usage": {"request_count": 1, "cost_usd": .1}, "critiques": [],
            "claims": [{"claim_id": "claim-1", "fixture_id": "api_football:123",
                "kind": "INJURY", "claim": "A report says the goalkeeper may be absent.",
                "source_url": SOURCE, "author": "ClubNews", "published_at": PUBLISHED.isoformat(),
                "fetched_at": (NOW - timedelta(seconds=1)).isoformat(),
                "content_status": "UNVERIFIED",
                "provenance_status": "CITATION_AND_TIMESTAMP_CHECKED"}]}


class Advisor:
    def __init__(self):
        self.calls = 0

    def review(self, packet):
        self.calls += 1
        return accepted_result()


def news_store(root):
    return NewsStore(Database(root / "data/goal_analysis.sqlite3"))


def test_fresh_result_archives_after_persist_with_exact_time_and_no_ticket_mutation(tmp_path):
    configure(tmp_path)
    data, advisor = report(), Advisor()
    original = deepcopy(data)
    outcome = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert outcome["status"] == "COMPLETE"
    assert outcome["automatic_changes"] is False
    assert outcome["news_archive"]["status"] == "ARCHIVED"
    assert outcome["news_archive"]["assertions_created"] == 1
    assert advisor.calls == 1 and data == original
    assert not (tmp_path / "data/arthur/portfolio.sqlite3").exists()
    saved = news_store(tmp_path).claims()[0]
    assert saved["observed_at"] == NOW.isoformat(timespec="microseconds")
    assert saved["request_id"] == outcome["request_id"]
    assert saved["model"] == outcome["result"]["model"]
    with sqlite3.connect(tmp_path / "data/arthur/grok.sqlite3") as connection:
        row = connection.execute("SELECT completed_at, result_json FROM grok_results").fetchone()
    assert row[0] == outcome["completed_at"]
    assert json.loads(row[1]) == outcome["result"]


def test_cache_backfills_previously_unarchived_result_without_advisor_call(tmp_path, monkeypatch):
    configure(tmp_path)
    advisor = Advisor()
    # Simulate an older version that persisted the result before the archive existed.
    with monkeypatch.context() as patch:
        patch.setattr(job, "archive_grok_result", lambda *args: {"status": "NOT_RUN"})
        first = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    assert not (tmp_path / "data/goal_analysis.sqlite3").exists()
    cached = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=10))
    assert cached["status"] == "CACHED" and advisor.calls == 1
    assert cached["news_archive"]["status"] == "ARCHIVED"
    assert cached["request_id"] == first["request_id"]
    assert news_store(tmp_path).claims()[0]["observed_at"] == NOW.isoformat(timespec="microseconds")
    repeated = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=20))
    assert repeated["news_archive"]["status"] == "ALREADY_ARCHIVED"
    assert advisor.calls == 1
    assert news_store(tmp_path).summary()["total_imports"] == 1


def test_archive_failure_preserves_valid_result_cost_and_can_retry_from_cache(tmp_path, monkeypatch):
    configure(tmp_path)
    advisor = Advisor()

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("secret failure details must not escape")

    with monkeypatch.context() as patch:
        patch.setattr(NewsStore, "ingest_grok_result", fail)
        first = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    assert first["status"] == "COMPLETE" and first["charged_usd"] == .1
    assert first["news_archive"] == {"status": "FAILED",
                                     "error_code": "NEWS_ARCHIVE_STORE_UNAVAILABLE"}
    assert first["result"]["claims"] == accepted_result()["claims"]
    assert "secret" not in json.dumps(first)
    with sqlite3.connect(tmp_path / "data/arthur/grok.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM grok_results").fetchone()[0] == 1
    cached = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=1))
    assert cached["status"] == "CACHED" and advisor.calls == 1
    assert cached["news_archive"]["status"] == "ARCHIVED"


def test_helper_accepts_persisted_iso_timestamp_and_rejects_naive_input(tmp_path):
    first = archive_grok_result(tmp_path, accepted_result(), "original-request", NOW.isoformat())
    assert first["status"] == "ARCHIVED"
    assert archive_grok_result(tmp_path, accepted_result(), "original-request", NOW)["status"] == (
        "ALREADY_ARCHIVED")
    invalid = archive_grok_result(tmp_path, accepted_result(), "invalid", NOW.replace(tzinfo=None))
    assert invalid == {"status": "FAILED", "error_code": "NEWS_ARCHIVE_INVALID_INPUT"}
    assert news_store(tmp_path).summary()["total_imports"] == 1


def test_unknown_archive_failure_is_sanitized_and_preserves_grok_result(tmp_path, monkeypatch):
    configure(tmp_path)

    def fail(*args):
        raise RuntimeError("secret upstream details")

    monkeypatch.setattr(NewsStore, "initialize", fail)
    outcome = job.analyze(tmp_path, report(), advisor=Advisor(), now=NOW)
    assert outcome["status"] == "COMPLETE"
    assert outcome["news_archive"] == {"status": "FAILED", "error_code": "NEWS_ARCHIVE_FAILED"}
    assert "secret" not in json.dumps(outcome)


def test_advancing_completion_clock_archives_claim_received_after_request_start(tmp_path):
    configure(tmp_path)
    fetched = NOW + timedelta(seconds=20)
    completed = NOW + timedelta(seconds=21)

    class SlowAdvisor(Advisor):
        def review(self, packet):
            self.calls += 1
            value = accepted_result()
            value["claims"][0]["fetched_at"] = fetched.isoformat()
            return value

    advisor = SlowAdvisor()
    first = job.analyze(tmp_path, report(), advisor=advisor, now=NOW,
                        completion_clock=lambda: completed)
    assert first["status"] == "COMPLETE"
    assert first["completed_at"] == completed.isoformat()
    assert first["completion_clock_status"] == "PROVIDED_CLOCK"
    assert first["news_archive"]["status"] == "ARCHIVED"
    saved = news_store(tmp_path).claims()[0]
    assert saved["source_fetched_at"] == fetched.isoformat(timespec="microseconds")
    assert saved["observed_at"] == completed.isoformat(timespec="microseconds")
    assert news_store(tmp_path).claims(as_of=fetched) == []
    with sqlite3.connect(tmp_path / "data/arthur/grok.sqlite3") as connection:
        persisted = connection.execute("SELECT completed_at FROM grok_results").fetchone()[0]
    assert persisted == completed.isoformat()
    cached = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=1),
                         completion_clock=lambda: NOW + timedelta(minutes=2))
    assert cached["status"] == "CACHED" and advisor.calls == 1
    assert cached["completed_at"] == completed.isoformat()
    assert cached["news_archive"]["status"] == "ALREADY_ARCHIVED"


def test_completion_clock_failure_keeps_paid_result_and_reports_fallback(tmp_path):
    configure(tmp_path)

    def broken_clock():
        raise RuntimeError("secret clock detail")

    outcome = job.analyze(tmp_path, report(), advisor=Advisor(), now=NOW,
                          completion_clock=broken_clock)
    assert outcome["status"] == "COMPLETE"
    assert outcome["charged_usd"] == .1
    assert outcome["completion_clock_status"] == "SYSTEM_CLOCK_FALLBACK"
    assert "secret" not in json.dumps(outcome)
    with sqlite3.connect(tmp_path / "data/arthur/grok.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM grok_results").fetchone()[0] == 1
