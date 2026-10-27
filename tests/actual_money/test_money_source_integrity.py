"""Source-local financial negatives; sports/forecast learning still continues."""
from copy import deepcopy
from datetime import timedelta

import pytest

from goal_analysis.learning._metrics import measure
from goal_analysis.storage.monetary_projection import (
    project_recorded_cohort,
    project_recorded_wager,
)
from goal_analysis.storage.portfolio_ledger import (
    PortfolioLedger,
    _ensure_bookmaker_return_provenance,
)

from . import _money_fixtures as original
from .test_monetary_projection import record, returned

TABLE = "portfolio_bookmaker_return_provenance"


@pytest.mark.parametrize("outcome", ["WON", "LOST", "VOID"])
@pytest.mark.parametrize("digest", [None, "", "malformed", "A" * 64])
def test_current_terminal_requires_exact_stored_hash_without_losing_stake(outcome, digest):
    bad = record(outcome, source="RESEARCH_QUOTE_ESTIMATE", key="bad")
    bad["settlement"]["settlement_hash"] = digest
    result = project_recorded_wager(bad)
    assert result["status"] == "INVALID"
    assert result["reason"] == "TERMINAL_SETTLEMENT_HASH_MISSING_OR_INVALID"
    assert result["payout_eur"] is result["net_eur"] is result["gross_sports_return_eur"] is None
    assert result["stake_eur"] == 1
    good = record("LOST", source="RESEARCH_QUOTE_ESTIMATE", key="known-loss")
    cohort = project_recorded_cohort([bad, good])
    assert cohort["stake_eur"] == 2 and cohort["unique_wager_keys"] == 2
    assert cohort["net_eur"] is None and cohort["known_subset_net_eur"] == -1


@pytest.mark.parametrize("outer,inner,research", [
    ("WON", "LOST", None), ("LOST", "WON", None),
    ("WON", "WON", "LOST"), ("WON", "LOST", "WON"), ("VOID", "WON", None),
])
def test_conflicting_terminal_identities_are_not_favorable_money(outer, inner, research):
    item = record(outer)
    item["settlement"]["outcome"] = inner
    if research is not None:
        item["settlement"]["research_outcome"] = research
    result = project_recorded_wager(item)
    assert result["status"] == "INVALID"
    assert result["payout_eur"] is result["net_eur"] is result["gross_sports_return_eur"] is None
    assert "IDENTITY" in result["reason"]


@pytest.mark.parametrize("view", ["REVIEW", "VOID"])
def test_legitimate_partial_void_view_is_bound_to_current_return(view):
    item = returned(record(), 1, typed=True)
    item["outcome"] = view
    item["settlement"].update(outcome=view, research_outcome="WON",
        legs=[{"outcome": "WON"}, {"outcome": "VOID"}])
    value = project_recorded_wager(item)
    assert (value["status"], value["sports_outcome"], value["net_eur"]) == ("KNOWN", "WON", 0)
    assert value["gross_sports_return_eur"] == 1
    stale = deepcopy(item)
    stale["settlement"]["bookmaker_return"]["settlement_hash"] = "b" * 64
    rejected = project_recorded_wager(stale)
    assert rejected["payout_eur"] is rejected["net_eur"] is rejected["gross_sports_return_eur"] is None


def test_known_loss_with_pending_other_legs_and_true_full_void_remain_known():
    loss = record("LOST", source="RESEARCH_QUOTE_ESTIMATE", price=None)
    loss["settlement"]["legs"].append({"outcome": "PENDING"})
    assert project_recorded_wager(loss)["net_eur"] == -1
    refund = record("VOID", source="RESEARCH_QUOTE_ESTIMATE", price=None)
    assert project_recorded_wager(refund)["net_eur"] == 0


def physical_partial(root, *, confirm=True):
    ledger = PortfolioLedger(root / "ledger.sqlite3", now=lambda: original.NOW)
    research = original.ticket("research", profile="RESEARCH", fixture_ids=(13, 14, 15))
    for leg in research["legs"]:
        leg.update(model_probability=0.7, model_details={"version": "gauss-TESTONLY-v5"},
                   confidence_kind="UNCALIBRATED_MODEL_ESTIMATE")
    ledger.save_run(original.report(original.ticket(),
        original.ticket("known-loss", profile="LOSS", fixture_ids=(4, 5, 6)), research))
    ledger.record_wager("ticket-1", actual_combined_price=12.6)
    ledger.record_wager("known-loss")
    rows = {n: original.fixture(n, fulltime=(0, 0) if n in (4, 5, 6) else (2, 1),
                               status="PST" if n == 1 else "FT")
            for n in (1, 2, 3, 4, 5, 6, 13, 14, 15)}
    original.finish(ledger, rows)
    view = ledger.record_void("ticket-1", "api_football:1", "TESTONLY confirmed void")
    if confirm:
        ledger.confirm_bookmaker_return("ticket-1", 30, "TESTONLY original reported return")
    return ledger, view["settlement"]["settlement_hash"]


def state(ledger):
    with ledger._connection() as db:
        return ([tuple(row) for row in db.execute("SELECT name,type,sql FROM sqlite_master ORDER BY name")],
                [tuple(row) for row in db.execute("SELECT * FROM portfolio_bookmaker_returns")])


def schema(kind):
    columns = "ticket_id TEXT NOT NULL,settlement_hash TEXT NOT NULL,payout_basis TEXT NOT NULL,fee_tax_inclusion TEXT NOT NULL,confirmed_at TEXT NOT NULL,contract_version TEXT NOT NULL"
    if kind == "missing_columns":
        columns = "junk TEXT"
    elif kind == "wrong_type":
        columns = columns.replace("confirmed_at TEXT", "confirmed_at INTEGER")
    suffix = "" if kind in {"missing_columns", "missing_pk"} else ",PRIMARY KEY(ticket_id,settlement_hash)"
    return f"CREATE TABLE {TABLE} ({columns}{suffix})"


def test_absent_provenance_remains_honest_legacy_without_ddl(tmp_path):
    ledger, _ = physical_partial(tmp_path)
    before = state(ledger)
    result = measure(ledger.path, original.NOW + timedelta(hours=8), include_records=True)
    assert result["coverage"]["optional_read_quality"][TABLE]["status"] == "ABSENT_TABLE"
    partial = next(row for row in result["records"]["actual"] if row["ticket_id"] == "ticket-1")
    assert partial["financial"]["net_eur"] == 29
    assert partial["financial"]["payout_basis"] == "LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED"
    assert partial["financial"]["gross_sports_return_eur"] is None
    assert state(ledger) == before


@pytest.mark.parametrize("kind", ["missing_columns", "missing_pk", "wrong_type"])
def test_corrupt_present_schema_is_local_invalid_not_healthy_legacy(tmp_path, kind):
    ledger, _ = physical_partial(tmp_path)
    before_quality = measure(ledger.path, original.NOW + timedelta(hours=8), include_records=True)
    with ledger._connection() as db:
        db.execute(schema(kind))
    before = state(ledger)
    result = measure(ledger.path, original.NOW + timedelta(hours=8), include_records=True)
    quality = result["coverage"]["optional_read_quality"][TABLE]
    assert quality["status"] == "INVALID_SCHEMA" and quality["physical_table_present"]
    actual = {row["ticket_id"]: row for row in result["records"]["actual"]}
    assert actual["ticket-1"]["financial"]["net_eur"] is None
    assert actual["ticket-1"]["financial"]["payout_basis"] != "LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED"
    assert "INVALID_RETURN_PROVENANCE" in actual["ticket-1"]["financial"]["excluded_evidence"]
    assert actual["known-loss"]["financial"]["net_eur"] == -1
    assert result["metrics"]["actual"]["financial"]["known_subset_net_eur"] == -1
    assert result["metrics"]["research"]["tickets"]["total"] == before_quality["metrics"]["research"]["tickets"]["total"] == 1
    assert result["metrics"]["research"]["forecast_quality"]["eligible_selections"] == before_quality["metrics"]["research"]["forecast_quality"]["eligible_selections"] == 3
    assert state(ledger) == before


@pytest.mark.parametrize("field,value", [
    ("payout_basis", "NET_CASH_RETURN"), ("fee_tax_inclusion", "INCLUDED"),
    ("contract_version", "unknown-version"), ("confirmed_at", "2026-09-20T16:00:00"),
])
def test_corrupt_matching_provenance_row_is_not_legacy_fallback(tmp_path, field, value):
    ledger, digest = physical_partial(tmp_path)
    with ledger._connection() as db:
        _ensure_bookmaker_return_provenance(db)
        row = {"payout_basis": "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN", "fee_tax_inclusion": "EXCLUDED",
               "confirmed_at": ledger.now().isoformat(), "contract_version": "bookmaker-gross-return-v1"}
        row[field] = value
        db.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?)", ("ticket-1", digest,
            row["payout_basis"], row["fee_tax_inclusion"], row["confirmed_at"], row["contract_version"]))
    before = state(ledger)
    result = measure(ledger.path, original.NOW + timedelta(hours=8), include_records=True)
    actual = {row["ticket_id"]: row["financial"] for row in result["records"]["actual"]}
    assert actual["ticket-1"]["net_eur"] is None
    assert actual["ticket-1"]["payout_basis"] != "LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED"
    assert actual["known-loss"]["net_eur"] == -1
    assert result["metrics"]["research"]["forecast_quality"]["eligible_selections"] == 3
    assert state(ledger) == before


@pytest.mark.parametrize("kind", ["missing_columns", "missing_pk", "wrong_type"])
def test_legacy_writer_validates_existing_schema_before_any_durable_return(tmp_path, kind):
    ledger, _ = physical_partial(tmp_path, confirm=False)
    with ledger._connection() as db:
        db.execute(schema(kind))
    before = state(ledger)
    assert before[1] == []
    with pytest.raises(ValueError, match="PROVENANCE_SCHEMA"):
        ledger.confirm_bookmaker_return("ticket-1", 30, "TESTONLY must reject before durable insert")
    assert state(ledger) == before
