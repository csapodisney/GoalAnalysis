"""Prospective package corrections preserve every original immutable snapshot."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest
from test_portfolio_ledger import NOW, report, ticket

from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json


@pytest.fixture
def frozen(tmp_path):
    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    ledger.save_run(report(ticket()))
    ledger.freeze_run("run-1")
    original = ledger.list_freezes()[0]
    return ledger, clock, original


def next_run(ledger, clock, number=2, *, kickoff_hours=3):
    clock[0] = NOW + timedelta(minutes=number)
    item = ticket(f"ticket-{number}")
    for leg in item["legs"]:
        leg["kickoff"] = (NOW + timedelta(hours=kickoff_hours)).isoformat()
    value = report(item, identifier=f"run-{number}", observed_at=clock[0])
    ledger.save_run(value)
    return value


def test_append_only_revision_effective_view_and_exact_retry(frozen):
    ledger, clock, original = frozen
    with ledger._connection() as db:
        original_row = tuple(db.execute("SELECT * FROM portfolio_freezes").fetchone())
    next_run(ledger, clock)
    saved = ledger.revise_freeze("run-2", original["snapshot_hash"], "Reviewed selection coverage")
    assert saved["saved"] and saved["revision"] == 2
    assert saved["original_snapshot_hash"] == original["snapshot_hash"]
    retry = ledger.revise_freeze("run-2", original["snapshot_hash"], "Reviewed selection coverage")
    assert retry == {**saved, "saved": False}
    with ledger._connection() as db:
        assert tuple(db.execute("SELECT * FROM portfolio_freezes").fetchone()) == original_row
        assert db.execute("SELECT COUNT(*) FROM portfolio_freeze_revisions").fetchone()[0] == 1
    effective = ledger.list_freezes()[0]
    assert effective["run_id"] == "run-2" and effective["revision_count"] == 1
    assert len(effective["revision_history"]) == 2
    assert all("snapshot" not in item for item in effective["revision_history"])
    history = ledger.list_freeze_history()
    assert [row["run_id"] for row in history] == ["run-1", "run-2"]
    assert history[0]["snapshot"]["recommendations"][0]["portfolio_ticket_id"] == "ticket-1"
    assert ledger.freeze_performance()["summary"]["packages"] == 1
    assert ledger.freeze_performance()["summary"]["tickets"] == 1
    with pytest.raises(ValueError, match="már van rögzített"):
        ledger.freeze_run("run-2")


def test_third_revision_has_expected_parent_and_rejects_rollback(frozen):
    ledger, clock, original = frozen
    next_run(ledger, clock)
    second = ledger.revise_freeze("run-2", original["snapshot_hash"], "Second")
    next_run(ledger, clock, 3)
    with pytest.raises(ValueError, match="közben megváltozott"):
        ledger.revise_freeze("run-3", original["snapshot_hash"], "Third")
    third = ledger.revise_freeze("run-3", second["snapshot_hash"], "Third")
    assert third["revision"] == 3
    with pytest.raises(ValueError, match="Korábbi csomag"):
        ledger.revise_freeze("run-1", third["snapshot_hash"], "Rollback")


def test_wagers_block_correction(frozen):
    ledger, clock, original = frozen
    ledger.record_wager("ticket-1")
    next_run(ledger, clock)
    with pytest.raises(ValueError, match="Megjátszott"):
        ledger.revise_freeze("run-2", original["snapshot_hash"], "Review")
    assert len(ledger.list_freeze_history()) == 1


def test_started_ancestor_blocks_even_future_replacement(frozen):
    ledger, clock, original = frozen
    clock[0] = NOW + timedelta(hours=4)
    item = ticket("later")
    for leg in item["legs"]:
        leg["kickoff"] = (NOW + timedelta(hours=5)).isoformat()
    ledger.save_run(report(item, identifier="run-2", observed_at=clock[0]))
    with pytest.raises(ValueError, match="már elkezdődött"):
        ledger.revise_freeze("run-2", original["snapshot_hash"], "Review")


@pytest.mark.parametrize("scenario", ["future_observation", "stale_observation", "past_day"])
def test_time_safety(frozen, scenario):
    ledger, clock, original = frozen
    value = report(ticket("new"), identifier="run-2")
    if scenario == "future_observation":
        value["observed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    ledger.save_run(value)
    if scenario == "past_day":
        clock[0] = NOW + timedelta(days=1)
    with pytest.raises(ValueError, match="időbélyege|frissebb|Korábbi napi"):
        ledger.revise_freeze("run-2", original["snapshot_hash"], "Review")


def test_no_recommendations_and_other_date_fail_closed(frozen):
    ledger, clock, original = frozen
    clock[0] += timedelta(minutes=1)
    ledger.save_run(report(identifier="empty", observed_at=clock[0]))
    with pytest.raises(ValueError, match="Nincs rögzíthető"):
        ledger.revise_freeze("empty", original["snapshot_hash"], "Review")
    value = report(ticket("other-day"), identifier="other-day", observed_at=clock[0])
    value["date"] = value["tickets"][0]["date"] = "2026-09-21"
    for leg in value["tickets"][0]["legs"]:
        leg["kickoff"] = (NOW + timedelta(days=1, hours=3)).isoformat()
    ledger.save_run(value)
    with pytest.raises(ValueError, match="Előbb az eredeti"):
        ledger.revise_freeze("other-day", original["snapshot_hash"], "Review")


def test_corrupt_original_or_chain_fails_closed(frozen):
    ledger, clock, original = frozen
    next_run(ledger, clock)
    ledger.revise_freeze("run-2", original["snapshot_hash"], "Review")
    with ledger._connection() as db:
        row = db.execute("SELECT snapshot_json FROM portfolio_freeze_revisions").fetchone()
        snapshot = deepcopy(json.loads(row[0]))
        snapshot["parent_snapshot_hash"] = "0" * 64
        db.execute(
            "UPDATE portfolio_freeze_revisions SET parent_snapshot_hash=?,snapshot_json=?,snapshot_hash=?",
            ("0" * 64, _json(snapshot), _hash(snapshot)),
        )
    with pytest.raises(ValueError, match="sérült"):
        ledger.list_freezes()
    with pytest.raises(ValueError, match="sérült"):
        ledger.list_freeze_history()


def test_corrupt_original_cannot_be_revised(frozen):
    ledger, clock, original = frozen
    next_run(ledger, clock)
    with ledger._connection() as db:
        db.execute("UPDATE portfolio_freezes SET snapshot_hash=?", ("0" * 64,))
    with pytest.raises(ValueError, match="sérült"):
        ledger.revise_freeze("run-2", original["snapshot_hash"], "Review")


def test_concurrent_revisions_have_one_winner(frozen):
    ledger, clock, original = frozen
    next_run(ledger, clock)
    next_run(ledger, clock, 3)
    def revise(number):
        try:
            return ledger.revise_freeze(f"run-{number}", original["snapshot_hash"], "Review")
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, (2, 3)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert len(ledger.list_freeze_history()) == 2


@pytest.mark.parametrize("damage", ["missing", "extra", "leg", "provenance"])
def test_revision_requires_complete_unchanged_recommendation_index(frozen, damage):
    ledger, clock, original = frozen
    clock[0] += timedelta(minutes=1)
    value = report(ticket("new-a"), ticket("new-b", "MERLIN"),
                   identifier="new", observed_at=clock[0])
    value["recommendations"] = deepcopy(value["tickets"])
    ledger.save_run(value)
    with ledger._connection() as db:
        rows = db.execute(
            "SELECT * FROM portfolio_recommendations WHERE run_id='new' ORDER BY rowid"
        ).fetchall()
        if damage == "missing":
            db.execute("DELETE FROM portfolio_recommendations WHERE recommendation_id=?",
                       (rows[0]["recommendation_id"],))
        else:
            altered = json.loads(rows[0]["snapshot_json"])
            if damage == "extra":
                altered.update(profile_id="EXTRA", recommendation_id="extra")
                db.execute("INSERT INTO portfolio_recommendations VALUES (?,?,?,?,?,?,?)", (
                    "extra", "new", value["date"], "EXTRA", clock[0].isoformat(), _json(altered), 1,
                ))
            else:
                if damage == "leg":
                    altered["legs"][0]["selection_key"] = "under"
                else:
                    altered["legs"][0]["quote_provider"] = "changed"
                db.execute("UPDATE portfolio_recommendations SET snapshot_json=? WHERE recommendation_id=?",
                           (_json(altered), rows[0]["recommendation_id"]))
    with pytest.raises(ValueError, match="ajánlatindex"):
        ledger.revise_freeze("new", original["snapshot_hash"], "Review")
    assert len(ledger.list_freeze_history()) == 1


def test_revision_accepts_exact_explicit_recommendations_and_skips_abstentions(frozen):
    ledger, clock, original = frozen
    clock[0] += timedelta(minutes=1)
    value = report(identifier="new", observed_at=clock[0])
    idea = ticket("idea", "MERLIN")
    idea.pop("ticket_id")
    value["recommendations"] = [idea, {"profile_id": "abstained", "status": "ABSTAINED", "legs": []}]
    ledger.save_run(value)
    result = ledger.revise_freeze("new", original["snapshot_hash"], "Review")
    assert result["recommendation_count"] == 1


def test_duplicate_profile_in_report_cannot_be_frozen_as_partial_index(frozen):
    ledger, clock, original = frozen
    clock[0] += timedelta(minutes=1)
    value = report(identifier="new", observed_at=clock[0])
    value["recommendations"] = [ticket("first"), ticket("duplicate")]
    ledger.save_run(value)
    with pytest.raises(ValueError, match="ajánlatindex"):
        ledger.revise_freeze("new", original["snapshot_hash"], "Review")
