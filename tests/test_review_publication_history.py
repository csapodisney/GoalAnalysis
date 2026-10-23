"""Historical learning follows prospective withdrawals and package corrections."""

import json
import sqlite3
from copy import deepcopy
from datetime import timedelta

import pytest
from test_portfolio_ledger import NOW, report, ticket

from goal_analysis.learning._metrics import measure
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json


@pytest.fixture
def published(tmp_path):
    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    ledger.save_run(report(ticket()))
    return ledger, clock


def abstain(ledger, clock, *, profile="DAILY_223", status="ABSTAINED", failed=False):
    clock[0] += timedelta(minutes=2)
    value = report(identifier="withdrawal", observed_at=clock[0])
    value["recommendations"] = [{"profile_id": profile, "status": status, "legs": []}]
    if failed:
        value["construction_status"] = "DATA_BLOCKED"
    ledger.save_run(value)
    return value


def records(ledger, when, scope="published"):
    return measure(ledger.path, when, include_records=True)["records"][scope]


def test_explicit_abstention_withdraws_without_mutating_snapshot_or_fake_win(published):
    ledger, clock = published
    with ledger._connection() as db:
        original = tuple(db.execute(
            "SELECT recommendation_id,snapshot_json FROM portfolio_recommendations"
        ).fetchone())
    abstain(ledger, clock)
    assert not ledger.list_recommendations()
    archive = ledger.list_recommendations(archive=True)
    assert len(archive) == 1 and not archive[0]["active"]
    with ledger._connection() as db:
        assert tuple(db.execute(
            "SELECT recommendation_id,snapshot_json FROM portfolio_recommendations"
        ).fetchone()) == original
    assert len(records(ledger, NOW + timedelta(seconds=1))) == 1
    assert not records(ledger, clock[0])
    measured = measure(ledger.path, clock[0])
    assert measured["metrics"]["research"]["tickets"]["total"] == 0
    assert measured["metrics"]["research"]["tickets"]["won"] == 0


@pytest.mark.parametrize("kwargs", [
    {"profile": "other"}, {"status": "FAILED"}, {"failed": True},
])
def test_missing_profile_and_failed_reports_do_not_withdraw(published, kwargs):
    ledger, clock = published
    abstain(ledger, clock, **kwargs)
    assert len(ledger.list_recommendations()) == len(records(ledger, clock[0])) == 1


def test_started_recommendation_survives_abstention(published):
    ledger, clock = published
    clock[0] += timedelta(hours=4)
    abstain(ledger, clock)
    assert len(ledger.list_recommendations()) == len(records(ledger, clock[0])) == 1


def test_wagered_recommendation_survives_abstention(published):
    ledger, clock = published
    ledger.record_wager("ticket-1")
    abstain(ledger, clock)
    assert len(ledger.list_recommendations()) == len(records(ledger, clock[0])) == 1
    assert len(records(ledger, clock[0], "actual")) == 1
    assert not records(ledger, clock[0], "research")


def test_new_selection_after_abstention_is_published(published):
    ledger, clock = published
    abstain(ledger, clock)
    clock[0] += timedelta(minutes=1)
    ledger.save_run(report(ticket("new"), identifier="new", observed_at=clock[0]))
    assert [item["run_id"] for item in records(ledger, clock[0])] == ["new"]


def corrected(published):
    ledger, clock = published
    ledger.freeze_run("run-1")
    original = ledger.list_freezes()[0]
    clock[0] += timedelta(minutes=2)
    ledger.save_run(report(ticket("new"), identifier="new", observed_at=clock[0]))
    clock[0] += timedelta(minutes=1)
    correction = ledger.revise_freeze("new", original["snapshot_hash"], "Review")
    return ledger, clock, correction


def test_learning_uses_one_effective_revision_without_future_knowledge(published):
    ledger, clock, _ = corrected(published)
    before = measure(ledger.path, NOW + timedelta(minutes=1), include_records=True)
    after = measure(ledger.path, clock[0], include_records=True)
    assert [r["run_id"] for r in before["records"]["frozen"]] == ["run-1"]
    assert [r["run_id"] for r in after["records"]["frozen"]] == ["new"]
    assert before["coverage"]["frozen_packages_as_of"] == 1
    assert after["coverage"]["frozen_packages_as_of"] == 1
    assert after["metrics"]["frozen"]["tickets"]["total"] == 1
    assert after["records"]["frozen"][0]["source_refs"][0]["table"] == "portfolio_freeze_revisions"


def test_corrupt_future_revision_does_not_hide_old_historical_package(published):
    ledger, clock, _ = corrected(published)
    with ledger._connection() as db:
        snapshot = json.loads(db.execute(
            "SELECT snapshot_json FROM portfolio_freeze_revisions"
        ).fetchone()[0])
        snapshot["parent_snapshot_hash"] = "0" * 64
        db.execute(
            "UPDATE portfolio_freeze_revisions SET parent_snapshot_hash=?,snapshot_json=?,snapshot_hash=?",
            ("0" * 64, _json(snapshot), _hash(snapshot)),
        )
    before = measure(ledger.path, NOW + timedelta(minutes=1), include_records=True)
    after = measure(ledger.path, clock[0], include_records=True)
    assert [r["run_id"] for r in before["records"]["frozen"]] == ["run-1"]
    assert not after["records"]["frozen"] and after["coverage"]["invalid_records"] == 1


def test_orphan_revision_is_reported_without_double_counting(published):
    ledger, clock, _ = corrected(published)
    with sqlite3.connect(ledger.path) as db:
        row = list(db.execute("SELECT * FROM portfolio_freeze_revisions").fetchone())
        row[0] = "2026-09-21"
        db.execute("DELETE FROM portfolio_freeze_revisions")
        db.execute("INSERT INTO portfolio_freeze_revisions VALUES (?,?,?,?,?,?,?,?)", row)
    result = measure(ledger.path, clock[0], include_records=True)
    assert result["coverage"]["invalid_records"] == 1
    assert [r["run_id"] for r in result["records"]["frozen"]] == ["run-1"]


def test_learning_read_does_not_modify_any_history(published):
    ledger, clock, _ = corrected(published)
    with sqlite3.connect(ledger.path) as db:
        before = list(db.iterdump())
    measure(ledger.path, clock[0])
    with sqlite3.connect(ledger.path) as db:
        assert list(db.iterdump()) == before


def test_equal_created_at_abstention_withdraws_old_record(published):
    ledger, clock = published
    original = deepcopy(report(identifier="equal"))
    original["recommendations"] = [{"profile_id": "DAILY_223", "status": "ABSTAINED", "legs": []}]
    ledger.save_run(original)
    assert not ledger.list_recommendations() and not records(ledger, clock[0])


@pytest.mark.parametrize("new_selection_first", [True, False])
def test_equal_timestamp_republication_follows_immutable_run_order(published, new_selection_first):
    ledger, clock = published
    clock[0] += timedelta(minutes=2)
    selected = report(ticket("new-ticket"), identifier="new", observed_at=clock[0])
    withdrawn = report(identifier="withdraw", observed_at=clock[0])
    withdrawn["recommendations"] = [{"profile_id": "DAILY_223", "status": "ABSTAINED", "legs": []}]
    ordered = (selected, withdrawn) if new_selection_first else (withdrawn, selected)
    for value in ordered:
        ledger.save_run(value)
    expected = [] if new_selection_first else ["new"]
    assert [item["run_id"] for item in ledger.list_recommendations()] == expected
    assert [item["run_id"] for item in records(ledger, clock[0])] == expected
