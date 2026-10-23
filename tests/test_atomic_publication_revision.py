"""Final publication is atomic across run, active index and freeze CAS."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from test_portfolio_ledger import NOW, report, ticket

from goal_analysis.agents import canonical_sha256
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


@pytest.fixture
def frozen(tmp_path):
    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    ledger.save_run(report(ticket()))
    ledger.freeze_run("run-1")
    original = ledger.list_freezes()[0]
    clock[0] += timedelta(minutes=2)
    return ledger, clock, original


def fresh(number=2):
    value = report(ticket(f"ticket-{number}"), identifier=f"run-{number}",
                   observed_at=NOW + timedelta(minutes=1))
    value["recommendations"] = deepcopy(value["tickets"])
    return value


def rows(ledger):
    with ledger._connection() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: sorted(map(tuple, db.execute(f'SELECT * FROM "{table}"')), key=repr)
                for table in tables}


def guard(value, stamp):
    for card in value["recommendations"]:
        for leg in card["legs"]:
            if leg["kickoff"] <= (stamp + timedelta(minutes=15)).isoformat():
                raise ValueError("publication lockout")


def test_atomic_success_preserves_originals_and_advances_active_index(frozen):
    ledger, _, original = frozen
    before = rows(ledger)
    result = ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=guard)
    assert result["revision"] == 2 and result["saved"]
    assert ledger.list_freezes()[0]["run_id"] == "run-2"
    assert [row["ticket_id"] for row in ledger.list_tickets()] == ["ticket-2"]
    after = rows(ledger)
    for table in ("portfolio_runs", "portfolio_freezes", "portfolio_wagers"):
        assert all(row in after[table] for row in before[table])


@pytest.mark.parametrize("failure_call", [1, 2])
def test_validator_failure_rolls_back_every_row_including_active_index(frozen, failure_call):
    ledger, _, original = frozen
    before = rows(ledger)
    calls = []
    def validator(value, stamp):
        calls.append(stamp)
        if len(calls) == failure_call:
            raise ValueError("publication lockout")
    with pytest.raises(ValueError, match="lockout"):
        ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=validator)
    assert len(calls) == failure_call
    assert rows(ledger) == before


def test_compare_and_swap_failure_does_not_leave_a_new_run(frozen):
    ledger, _, original = frozen
    ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=guard)
    before = rows(ledger)
    with pytest.raises(ValueError, match="közben megváltozott"):
        ledger.publish_revision(fresh(3), original["snapshot_hash"], "Review", validator=guard)
    assert rows(ledger) == before


def test_clock_crossing_fifteen_minute_boundary_before_commit_rolls_back(frozen):
    ledger, clock, original = frozen
    before = rows(ledger)
    calls = []
    def advancing_guard(value, stamp):
        calls.append(stamp)
        guard(value, stamp)
        if len(calls) == 1:
            clock[0] = NOW + timedelta(hours=2, minutes=46)
    with pytest.raises(ValueError, match="publication lockout"):
        ledger.publish_revision(fresh(), original["snapshot_hash"], "Review",
                                validator=advancing_guard)
    assert len(calls) == 2 and calls[1] > calls[0]
    assert rows(ledger) == before


def test_wager_conflict_rolls_back_new_run_and_recommendations(frozen):
    ledger, _, original = frozen
    ledger.record_wager("ticket-1")
    before = rows(ledger)
    with pytest.raises(ValueError, match="Megjátszott"):
        ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=guard)
    assert rows(ledger) == before


def test_exact_retry_after_unknown_commit_outcome_is_read_only_even_after_kickoff(frozen):
    ledger, clock, original = frozen
    result = ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=guard)
    before = rows(ledger)
    clock[0] += timedelta(days=1)
    def forbidden(*args):
        raise AssertionError("Retry must not be a new publication")
    retry = ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=forbidden)
    assert retry == {**result, "saved": False}
    assert rows(ledger) == before
    bad = fresh()
    bad["usage"]["football_calls"] = 999
    with pytest.raises(ValueError, match="immutable retry"):
        ledger.publish_revision(bad, original["snapshot_hash"], "Review", validator=forbidden)
    assert rows(ledger) == before


def test_concurrent_cas_has_one_fully_saved_winner_and_no_loser_run(frozen):
    ledger, _, original = frozen
    def publish(number):
        try:
            return ledger.publish_revision(fresh(number), original["snapshot_hash"],
                                           "Review", validator=guard)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (2, 3)))
    winner = next(result for result in results if result)
    assert sum(result is not None for result in results) == 1
    assert {r["run_id"] for r in ledger.list_runs()} == {"run-1", winner["run_id"]}
    assert len(ledger.list_freeze_history()) == 2


def test_callback_cannot_mutate_the_immutable_publication(frozen):
    ledger, _, original = frozen
    before = rows(ledger)
    def mutate(value, stamp):
        value["extra"] = "altered"
    with pytest.raises(ValueError, match="validator changed"):
        ledger.publish_revision(fresh(), original["snapshot_hash"], "Review", validator=mutate)
    assert rows(ledger) == before


def protected_script():
    path = Path(__file__).resolve().parents[1] / "scripts/regenerate-arthur-protected.py"
    spec = spec_from_file_location("test_protected_publication_script", path)
    script = module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_script_resume_recovers_committed_revision_without_new_validation_or_grok(frozen, tmp_path):
    ledger, clock, original = frozen
    script = protected_script()
    value = fresh()
    value["report_sha256"] = canonical_sha256(value)
    ledger.publish_revision(value, original["snapshot_hash"], script.REASON, validator=guard)
    stage = tmp_path / "stage"
    script.write(stage / "reviewed-report.json", value)
    clock[0] += timedelta(days=1)
    before = rows(ledger)
    first = script.completed_resume(ledger, stage, day=value["date"],
        source_run="run-1", expected_hash=original["snapshot_hash"])
    first_evidence = Path(first["audit_file"]).read_bytes()
    second = script.completed_resume(ledger, stage, day=value["date"],
        source_run="run-1", expected_hash=original["snapshot_hash"])
    assert first["already_published"] and first["additional_grok_requests"] == 0
    assert not first["revision"]["saved"]
    assert first["audit_file"] != second["audit_file"]
    assert Path(first["audit_file"]).read_bytes() == first_evidence
    assert rows(ledger) == before


def test_complete_bundle_retry_checks_existing_bytes_and_preserves_audit(tmp_path, monkeypatch):
    script = protected_script()
    def writer(directory, bundle):
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(bundle["report"], "utf-8")
    monkeypatch.setattr(script, "write_portfolio_bundle", writer)
    directory = tmp_path / "reports/run"
    script.write_bundle_once(directory, {"report": "same"}, tmp_path / "attempt1")
    (directory / "prior-audit.json").write_text("immutable", "utf-8")
    script.write_bundle_once(directory, {"report": "same"}, tmp_path / "attempt2")
    assert (directory / "prior-audit.json").read_text() == "immutable"
    with pytest.raises(ValueError, match="evidence was preserved"):
        script.write_bundle_once(directory, {"report": "different"}, tmp_path / "attempt3")
    assert (directory / "report.json").read_text() == "same"
