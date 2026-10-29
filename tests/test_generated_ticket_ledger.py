"""TESTONLY physical SQLite: generated publication and actual wager limits differ."""

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.config.portfolio import PROFILE_IDS
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
DAY = "2026-10-28"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = Path("C:/AI-Work/GoalAnalysis").resolve()


@pytest.fixture(autouse=True)
def testonly_database_boundary(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    assert not root.is_relative_to(SOURCE_ROOT)
    assert not root.is_relative_to(PRODUCTION_ROOT)
    marker = root / "TESTONLY-GENERATED-LEDGER.json"
    marker.write_text(json.dumps({"TESTONLY": True, "scope": "generated-ticket-ledger"}),
                      encoding="utf-8")
    connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        path = Path(database).resolve()
        assert path.is_relative_to(root)
        assert marker.exists()
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", isolated_connect)


def cards(count=30, *, run="one", observed=NOW):
    result = []
    for index, profile in enumerate(PROFILE_IDS[:count]):
        live = index < 6
        result.append({
            "ticket_id": f"{run}-{profile}", "profile_id": profile, "date": DAY,
            "created_at": observed.isoformat(), "bookmaker": "TESTONLY-bookmaker",
            "currency": "EUR", "combined_price": 16.0,
            "status": "READY" if live else "DRAFT", "playable": live,
            "publication_scope": "LIVE" if live else "PAPER_AUDITION",
            "stake_eur": 1.0 if live else None,
            "quality_warnings": [] if live else ["Optional review unavailable"],
            "legs": [{
                "fixture_id": f"TESTONLY:{index}:{leg}",
                "kickoff": (NOW + timedelta(hours=4)).isoformat(),
                "market_key": "totals_2_5", "selection_key": "over",
                "period": "FULL_TIME", "decimal_price": price,
                "quoted_at": observed.isoformat(),
            } for leg, price in enumerate((2.0, 2.0, 4.0))],
        })
    return result


def report(items=None, *, run="one", observed=NOW):
    items = cards(run=run, observed=observed) if items is None else items
    return {
        "run_id": run, "date": DAY, "observed_at": observed.isoformat(),
        "settings": {"operating_contract_version": 2},
        "generated_tickets": items,
        "tickets": [deepcopy(item) for item in items if item["publication_scope"] == "LIVE"],
    }


@pytest.fixture
def ledger(tmp_path):
    return PortfolioLedger(tmp_path / "TESTONLY-ledger.sqlite3", now=lambda: NOW)


def test_thirty_canonical_cards_save_read_and_hash_exactly_once(ledger):
    original = report()
    before = deepcopy(original)
    result = ledger.save_run(original)
    assert result == {"run_id": "one", "saved": True, "ticket_count": 30}
    assert ledger.list_runs() == [original] and original == before
    assert ledger.save_run(deepcopy(original)) == {**result, "saved": False}
    listed = {item["ticket_id"]: item for item in ledger.list_tickets()}
    assert len(listed) == 30
    assert len(ledger.list_tickets(mode="research")) == 30
    assert ledger.list_tickets(mode="actual") == []
    for source in original["generated_tickets"]:
        view = listed[source["ticket_id"]]
        assert {key: view[key] for key in source} == source
        assert view["active"] and not view["played"]
        if source["stake_eur"] is None:
            assert view["financial"]["basis"] == "RESEARCH_NO_STAKE"
            assert view["financial"]["open_stake_eur"] == 0
    with ledger._connection() as db:
        rows = db.execute("SELECT * FROM portfolio_tickets ORDER BY rowid").fetchall()
        assert len(rows) == 30
        for row, source in zip(rows, original["generated_tickets"], strict=True):
            assert row["snapshot_json"] == _json(source)
            assert row["snapshot_hash"] == _hash(source)
        assert db.execute("SELECT count(*) FROM portfolio_wagers").fetchone()[0] == 0
    reopened = PortfolioLedger(ledger.path, now=lambda: NOW)
    assert reopened.list_runs() == [original]
    assert len(reopened.list_tickets()) == 30


@pytest.mark.parametrize("actual_cap", [2, 5])
def test_generation_thirty_does_not_raise_real_wager_cap(tmp_path, actual_cap):
    ledger = PortfolioLedger(tmp_path / "TESTONLY-finance.sqlite3", now=lambda: NOW,
                             max_daily_tickets=actual_cap)
    value = report()
    ledger.save_run(value)
    live = value["tickets"]
    for item in live[:actual_cap]:
        result = ledger.record_wager(item["ticket_id"], actual_combined_price=16)
        assert result["wager"]["stake_eur"] == 1
    with pytest.raises(ValueError, match="maximum.*played tickets"):
        ledger.record_wager(live[actual_cap]["ticket_id"], actual_combined_price=16)
    assert len(ledger.list_tickets()) == 30
    assert len(ledger.list_tickets(mode="actual")) == actual_cap
    assert len(ledger.list_tickets(mode="research")) == 30 - actual_cap
    assert ledger.performance()["summary"]["open_stake_eur"] == actual_cap
    with ledger._connection() as db:
        assert db.execute("SELECT count(*) FROM portfolio_wagers").fetchone()[0] == actual_cap


@pytest.mark.parametrize("scope", ["PAPER", "PAPER_AUDITION", "RESEARCH"])
def test_stored_research_cannot_become_wager_by_confirming_price(ledger, scope):
    item = cards(1)[0]
    item.update(publication_scope=scope, status="READY", playable=False, stake_eur=None)
    ledger.save_run(report([item]))
    with pytest.raises(ValueError, match="PAPER/research|nonplayable"):
        ledger.record_wager(item["ticket_id"], actual_combined_price=20)
    assert not ledger.get_ticket(item["ticket_id"])["played"]
    with ledger._connection() as db:
        assert db.execute("SELECT count(*) FROM portfolio_wagers").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM portfolio_events WHERE "
                          "event_type='MANUAL_WAGER_CONFIRMED'").fetchone()[0] == 0


def test_current_draft_is_not_playable_even_with_actual_price(ledger):
    item = cards(1)[0]
    item.update(status="DRAFT", playable=False)
    ledger.save_run(report([item]))
    with pytest.raises(ValueError, match="nonplayable"):
        ledger.record_wager(item["ticket_id"], actual_combined_price=16)


def test_legacy_cap_and_explicit_draft_price_confirmation_stay_compatible(ledger):
    legacy = cards(6)
    for item in legacy:
        for key in ("publication_scope", "playable"):
            item.pop(key)
    legacy[0]["status"] = "DRAFT"
    value = {"run_id": "legacy", "date": DAY, "observed_at": NOW.isoformat(),
             "tickets": legacy, "generated_tickets": cards(),
             "settings": {"operating_contract_version": 1}}
    ledger.save_run(value)
    assert len(ledger.list_tickets()) == 5
    assert len(ledger.list_tickets(mode="archive")) == 6
    assert ledger.record_wager(legacy[0]["ticket_id"], actual_combined_price=16)["played"]
    assert ledger.list_runs() == [value]


def test_unmarked_extra_field_does_not_activate_current_contract(ledger):
    legacy = cards(6)
    value = {"run_id": "unmarked", "date": DAY, "observed_at": NOW.isoformat(),
             "tickets": legacy, "generated_tickets": cards()}
    ledger.save_run(value)
    assert len(ledger.list_tickets()) == 5
    assert len(ledger.list_tickets(mode="archive")) == 6


@pytest.mark.parametrize("count", [0, 14])
def test_honest_partial_or_empty_run_is_saved_without_filler(ledger, count):
    value = report(cards(count))
    assert ledger.save_run(value)["ticket_count"] == count
    assert len(ledger.list_tickets()) == count
    assert ledger.list_runs() == [value]


@pytest.mark.parametrize("invalid", [True, 0, 31, 30.0])
def test_generation_capacity_parameter_is_validated_before_db_creation(tmp_path, invalid):
    path = tmp_path / "must-not-be-created.sqlite3"
    with pytest.raises(ValueError, match="Generated ticket cap"):
        PortfolioLedger(path, max_generated_tickets=invalid)
    assert not path.exists()


def test_generation_capacity_is_a_hard_independent_bound(tmp_path):
    ledger = PortfolioLedger(tmp_path / "TESTONLY-bounded.sqlite3", now=lambda: NOW,
                             max_generated_tickets=20)
    with pytest.raises(ValueError, match="capacity"):
        ledger.save_run(report(cards(21)))
    assert ledger.list_runs() == []
    assert ledger.save_run(report(cards(20)))["ticket_count"] == 20
    assert len(ledger.list_tickets()) == 20


@pytest.mark.parametrize("mutation,message", [
    (lambda value: value.pop("generated_tickets"), "generated_tickets"),
    (lambda value: value["generated_tickets"][0].update(status="MODEL_READY"), "DRAFT or READY"),
    (lambda value: value["generated_tickets"][0].update(status="DRAFT"), "can be playable"),
    (lambda value: value["generated_tickets"][0].update(playable=1), "explicit playable"),
    (lambda value: value["generated_tickets"][0].update(stake_eur=2), "fixed 1 EUR"),
    (lambda value: value["generated_tickets"][0].update(legs=[]), "must have legs"),
    (lambda value: value["generated_tickets"][1].update(profile_id=PROFILE_IDS[0]), "one ticket per profile"),
])
def test_invalid_current_publication_is_atomic(ledger, mutation, message):
    value = report(cards(2))
    value["tickets"] = []
    mutation(value)
    with pytest.raises(ValueError, match=message):
        ledger.save_run(value)
    assert ledger.list_runs() == [] and ledger.list_tickets(mode="archive") == []


def test_compatibility_view_cannot_smuggle_changed_or_nonlive_ticket(ledger):
    value = report()
    value["tickets"][0]["combined_price"] = 40
    with pytest.raises(ValueError, match="exact LIVE"):
        ledger.save_run(value)
    value = report()
    value["tickets"].append(deepcopy(value["generated_tickets"][-1]))
    with pytest.raises(ValueError, match="exact LIVE"):
        ledger.save_run(value)
    assert ledger.list_runs() == []


def test_conflicting_ticket_snapshot_rolls_back_entire_new_run(ledger):
    original = report()
    ledger.save_run(original)
    with ledger._connection() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM portfolio_tickets ORDER BY rowid")]
    changed = deepcopy(original["generated_tickets"][0])
    changed["combined_price"] = 17
    new = deepcopy(original["generated_tickets"][1])
    new["ticket_id"] = "must-rollback"
    value = report([new, changed], run="conflict", observed=NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="immutable snapshot"):
        ledger.save_run(value)
    with ledger._connection() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM portfolio_tickets ORDER BY rowid")] == before
    assert ledger.list_runs() == [original]


def test_freeze_and_revision_bind_all_thirty_canonical_cards(ledger):
    original = report()
    original["recommendations"] = [deepcopy(original["tickets"][0])]
    ledger.save_run(original)
    assert ledger.freeze_run("one")["recommendation_count"] == 30
    initial = ledger.list_freezes()[0]
    assert {item["portfolio_ticket_id"] for item in initial["recommendations"]} == {
        item["ticket_id"] for item in original["generated_tickets"]
    }
    ledger.now = lambda: NOW + timedelta(minutes=2)
    update = report(run="two", observed=NOW + timedelta(minutes=1))
    ledger.save_run(update)
    with pytest.raises(ValueError, match="közben megváltozott"):
        ledger.revise_freeze("two", "0" * 64, "TESTONLY stale CAS")
    result = ledger.revise_freeze("two", initial["snapshot_hash"], "TESTONLY canonical correction")
    assert result["recommendation_count"] == 30 and result["revision"] == 2
    history = ledger.list_freeze_history()
    assert history[0]["snapshot"]["report"] == original
    assert history[1]["snapshot"]["report"] == update
    assert len(history[1]["snapshot"]["recommendations"]) == 30


def test_revision_rejects_a_materialized_index_missing_one_canonical_card(ledger):
    ledger.save_run(report())
    ledger.freeze_run("one")
    initial = ledger.list_freezes()[0]
    ledger.now = lambda: NOW + timedelta(minutes=2)
    ledger.save_run(report(run="two", observed=NOW + timedelta(minutes=1)))
    with ledger._connection() as db:
        db.execute("DELETE FROM portfolio_recommendations WHERE rowid=(SELECT min(rowid) "
                   "FROM portfolio_recommendations WHERE run_id='two')")
    with pytest.raises(ValueError, match="ajánlatindex"):
        ledger.revise_freeze("two", initial["snapshot_hash"], "TESTONLY missing canonical card")
    assert len(ledger.list_freeze_history()) == 1


def test_v2_final_cutoff_accepts_later_created_card_without_rewriting_source_clocks(ledger):
    finished = NOW + timedelta(minutes=3)
    items = cards(1)
    items[0]['created_at'] = (NOW + timedelta(minutes=2)).isoformat()
    value = report(items)
    value['finished_at'] = finished.isoformat()
    original = deepcopy(value)
    ledger.now = lambda: finished
    assert ledger.save_run(value)['ticket_count'] == 1
    assert value == original and ledger.list_runs() == [original]
    with ledger._connection() as db:
        assert db.execute('SELECT observed_at FROM portfolio_runs').fetchone()[0] == NOW.isoformat()
    stored = ledger.get_ticket(items[0]['ticket_id'])
    assert stored['created_at'] == items[0]['created_at']
    assert stored['legs'][0]['quoted_at'] == NOW.isoformat()


@pytest.mark.parametrize('boundary', ['creation_after_finish', 'kickoff_before_finish'])
def test_v2_final_cutoff_rejects_late_creation_or_started_fixture_atomically(ledger, boundary):
    finished = NOW + timedelta(minutes=3)
    items = cards(1)
    if boundary == 'creation_after_finish':
        items[0]['created_at'] = (finished + timedelta(seconds=1)).isoformat()
        message = 'later than final publication'
    else:
        items[0]['legs'][0]['kickoff'] = (NOW + timedelta(minutes=2)).isoformat()
        message = 'after fixture kickoff'
    value = report(items)
    value['finished_at'] = finished.isoformat()
    ledger.now = lambda: finished
    with pytest.raises(ValueError, match=message):
        ledger.save_run(value)
    assert ledger.list_runs() == [] and ledger.list_tickets(mode='archive') == []


def test_v1_final_cutoff_field_does_not_change_original_observation_validation(ledger):
    item = cards(1)[0]
    item['created_at'] = (NOW + timedelta(minutes=2)).isoformat()
    value = {'run_id': 'TESTONLY-legacy-cutoff', 'date': DAY, 'observed_at': NOW.isoformat(),
             'finished_at': (NOW + timedelta(minutes=3)).isoformat(),
             'settings': {'operating_contract_version': 1}, 'tickets': [item]}
    with pytest.raises(ValueError, match='later than the run observation'):
        ledger.save_run(value)
    assert ledger.list_runs() == []
