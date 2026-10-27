"""New actual adapter inputs; only explicit outside-ROOT SQLite fixtures."""
from copy import deepcopy
from datetime import timedelta

import pytest

from goal_analysis.learning._metrics import measure
from goal_analysis.storage.monetary_projection import GROSS, LEGACY, project_recorded_wager
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _financial, _summary
from goal_analysis.storage.rolling_ticket_kpis import build_rolling_ticket_kpis

from .conftest import ROOT, allowed_root, original


def partial(root):
    ledger = PortfolioLedger(root / "ledger.sqlite3", now=lambda: original.NOW)
    ledger.save_run(original.report(original.ticket()))
    ledger.record_wager("ticket-1", actual_combined_price=12.6)
    original.finish(ledger, {1: original.fixture(1, status="PST"), 2: original.fixture(2), 3: original.fixture(3)})
    view = ledger.record_void("ticket-1", "api_football:1", "TESTONLY actual void")
    return ledger, view["settlement"]["settlement_hash"]


def test_strict_all_consumers_keep_estimated_win_unknown_loss_refund_and_typed_return(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: original.NOW)
    items = [original.ticket("estimated", profile="A", fixture_ids=(1, 2, 3)),
             original.ticket("loss", profile="B", fixture_ids=(4, 5, 6)),
             original.ticket("refund", profile="C", fixture_ids=(7, 8, 9)),
             original.ticket("partial", profile="D", fixture_ids=(10, 11, 12))]
    ledger.save_run(original.report(*items))
    for item in items:
        ledger.record_wager(item["ticket_id"])
    rows = {n: original.fixture(n, fulltime=(0, 0) if n in (4, 5, 6) else (2, 1),
                                status="PST" if n in (7, 8, 9, 10) else "FT") for n in range(1, 13)}
    original.finish(ledger, rows)
    for fixture in (7, 8, 9):
        ledger.record_void("refund", f"api_football:{fixture}", "TESTONLY full refund")
    view = ledger.record_void("partial", "api_football:10", "TESTONLY partial refund")
    ledger.confirm_bookmaker_return("partial", 2, "TESTONLY gross", expected_settlement_hash=view["settlement"]["settlement_hash"], payout_basis=GROSS)
    actual = ledger.list_tickets(mode="actual", monetary_contract="CURRENT_HASH_V2")
    values = {item["ticket_id"]: item["financial"]["net_eur"] for item in actual}
    assert values == {"estimated": None, "loss": -1, "refund": 0, "partial": 1}
    assert all(item["financial"]["monetary_contract"] == "CURRENT_HASH_V2" for item in actual)
    stats = ledger.performance(month="2026-09", monetary_contract="CURRENT_HASH_V2")
    assert stats["summary"]["net_eur"] is None
    assert stats["summary"]["known_subset_net_eur"] == 0
    rolling = stats["rolling_kpis"]["windows"][0]["financial"]
    assert rolling["settled_net_eur_to_date"] is None
    assert rolling["known_subset_settled_net_eur"] == 0
    measured = measure(ledger.path, original.NOW + timedelta(hours=8), include_records=True)
    money = measured["metrics"]["actual"]["financial"]
    assert money["net_eur"] is None and money["known_subset_net_eur"] == 0
    assert money["monetary_projection"]["unique_wager_keys"] == 4
    assert stats["summary"]["monetary_projection"]["stake_eur"] == 4
    original.write_fixture_evidence(tmp_path, ledger, "money-consumer-proof.json", {
        "status": "TESTONLY_ACTUAL_SOURCE_LEDGER_LEARNING_ROLLING_FOUR_WAGER_READBACK",
        "actual_net_by_ticket": values, "ledger_summary": stats["summary"],
        "rolling_financial": rolling, "learning_financial": money,
    })


def test_partial_void_cached_view_does_not_create_stale_or_legacy_gross_refund(tmp_path):
    ledger, _ = partial(tmp_path)
    legacy = ledger.confirm_bookmaker_return("ticket-1", 1, "TESTONLY legacy amount")
    assert legacy["outcome"] == "VOID"
    value = project_recorded_wager(legacy)
    assert value["sports_outcome"] == "WON" and value["net_eur"] == 0
    assert value["payout_basis"] == LEGACY and value["gross_sports_return_eur"] is None
    stale = deepcopy(legacy)
    stale["settlement"]["bookmaker_return"]["settlement_hash"] = "b" * 64
    rejected = project_recorded_wager(stale)
    assert rejected["net_eur"] is rejected["gross_sports_return_eur"] is None
    assert rejected["sports_outcome"] == "WON"
    another, token = partial(tmp_path / "typed")
    typed = another.confirm_bookmaker_return("ticket-1", 1, "TESTONLY typed refund", expected_settlement_hash=token, payout_basis=GROSS)
    result = project_recorded_wager(typed)
    assert (result["sports_outcome"], result["net_eur"], result["gross_sports_return_eur"]) == ("WON", 0, 1)


def test_public_legacy_unbound_arithmetic_vs_explicit_strict_current_hash_contract():
    from ._money_fixtures import END, item
    row = item(actual=True)
    row["wager"]["actual_price_source"] = "ESTIMATED"
    row["settlement"]["legs"][1].update(outcome="VOID", reason="BOOKMAKER_VOID_CONFIRMED")
    row["settlement"]["bookmaker_return"] = {"payout_eur": 20}
    row["financial"] = _financial(row)
    legacy = build_rolling_ticket_kpis([row], _summary, mode="actual", as_of=END)["windows"][0]["financial"]
    assert legacy["settled_net_eur_to_date"] == 15
    assert legacy["basis"] == "LEGACY_UNBOUND_REPORTED_ARITHMETIC" and not legacy["verified_cash"]
    strict = build_rolling_ticket_kpis([row], _summary, mode="actual", as_of=END,
        monetary_contract="CURRENT_HASH_V2")["windows"][0]["financial"]
    assert strict["settled_net_eur_to_date"] is None
    row["settlement"]["settlement_hash"] = "a" * 64
    row["settlement"]["bookmaker_return"].update(settlement_hash="a" * 64, payout_basis=GROSS,
        contract_version="bookmaker-gross-return-v1", fee_tax_inclusion="EXCLUDED",
        provenance_confirmed_at="2026-01-01T00:00:00Z")
    row["financial"] = _financial(row, monetary_contract="CURRENT_HASH_V2")
    bound = build_rolling_ticket_kpis([row], _summary, mode="actual", as_of=END,
        monetary_contract="CURRENT_HASH_V2")["windows"][0]["financial"]
    assert bound["settled_net_eur_to_date"] == 15
    assert bound["monetary_projection"]["records"][0]["payout_basis"] == GROSS


def test_actual_combined_native_payloads_and_constructor_root_guard(tmp_path):
    with pytest.raises(AssertionError):
        PortfolioLedger(ROOT / "data/arthur/ledger.sqlite3")
    allowed_root(tmp_path)
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: original.NOW)
    data = original.report(original.ticket())
    data["recommendations"] = [deepcopy(data["tickets"][0])]
    ledger.save_run(data)
    ledger.record_wager("ticket-1", actual_combined_price=11.9)
    ledger.freeze_run("run-1")
    original.finish(ledger)
    payload = {"TESTONLY": True, "constructor_production_reject_before_connections": True,
               "forbidden_attempts": [], "payloads": {mode: ledger.performance(month="2026-09", mode=mode, monetary_contract="CURRENT_HASH_V2")
                   for mode in ("actual", "research", "recommendations", "freezes")}}
    original.write_fixture_evidence(tmp_path, ledger, "combined-native-payload.json", payload)
    assert payload["payloads"]["actual"]["summary"]["net_eur"] == 10.9


def test_public_legacy_estimated_win_and_placeholder_are_never_active_strict_profit(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: original.NOW)
    ledger.save_run(original.report(original.ticket()))
    ledger.record_wager("ticket-1")
    original.finish(ledger)
    legacy = ledger.performance()
    assert legacy["summary"]["net_eur"] == 11
    row = legacy["summary"]["monetary_projection"]["records"][0]
    assert row["payout_basis"] == "LEGACY_RECORDED_PRICE_ARITHMETIC" and not row["verified_cash"]
    assert row["gross_sports_return_eur"] is None
    strict = ledger.performance(monetary_contract="CURRENT_HASH_V2")
    assert strict["summary"]["net_eur"] is strict["summary"]["max_drawdown_eur"] is None
    assert strict["summary"]["known_subset_net_eur"] == 0
    assert ledger.feedback_summary()["actual"]["summary"]["net_eur"] is None
    another, _ = partial(tmp_path / "partial")
    value = another.get_ticket("ticket-1")["financial"]
    assert value["payout_eur"] == 0 and not value["settled"]
    assert value["payout_basis"] == "LEGACY_DISPLAY_PLACEHOLDER_NOT_ACTUAL_PAYOUT"
    assert another.get_ticket("ticket-1", monetary_contract="CURRENT_HASH_V2")["financial"]["payout_eur"] is None


def test_display_allowance_cannot_enable_strict_profit_or_default_rolling_estimate():
    from goal_analysis.storage.monetary_projection import project_recorded_cohort

    from .test_monetary_projection import record
    estimated = record(source="RESEARCH_QUOTE_ESTIMATE", price=12)
    legacy_default = project_recorded_wager(estimated, contract="LEGACY_RECORDED_V1")
    assert legacy_default["net_eur"] is None
    legacy_display = project_recorded_wager(estimated, contract="LEGACY_RECORDED_V1", allow_estimated_price_arithmetic=True)
    assert legacy_display["net_eur"] == 11 and not legacy_display["verified_cash"]
    assert legacy_display["payout_basis"] == "LEGACY_RECORDED_PRICE_ARITHMETIC"
    for flag in (False, True, "true", 1):
        strict = project_recorded_wager(estimated, contract="CURRENT_HASH_V2", allow_estimated_price_arithmetic=flag)
        assert strict["net_eur"] is strict["gross_sports_return_eur"] is None
        assert strict["estimated_price_display_allowance"] is False
    loss = record("LOST", source="RESEARCH_QUOTE_ESTIMATE", key="loss")
    cohort = project_recorded_cohort([estimated, loss], contract="CURRENT_HASH_V2", allow_estimated_price_arithmetic=True)
    assert cohort["net_eur"] is None and cohort["known_subset_net_eur"] == -1
    assert cohort["stake_eur"] == 2 and cohort["unique_wager_keys"] == 2


def test_feedback_recent_actual_money_uses_same_strict_projection(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: original.NOW)
    ledger.save_run(original.report(original.ticket()))
    ledger.record_wager("ticket-1")  # Existing public API retains estimated source truth.
    original.finish(ledger)
    public = ledger.get_ticket("ticket-1")["financial"]
    assert public["net_eur"] == 11 and public["payout_basis"] == "LEGACY_RECORDED_PRICE_ARITHMETIC"
    assert not public["verified_cash"]
    feedback = ledger.feedback_summary()
    recent = next(row for row in feedback["recent_settled"] if row["ticket_id"] == "ticket-1")
    money = recent["financial"]
    assert money["monetary_contract"] == "CURRENT_HASH_V2"
    assert money["payout_eur"] is money["net_eur"] is money["gross_sports_return_eur"] is None
    assert money["stake_eur"] == 1 and money["sports_outcome"] == "WON"
    assert feedback["actual"]["summary"]["net_eur"] is None
    assert feedback["actual"]["summary"]["known_subset_net_eur"] == 0
    current = ledger.get_ticket("ticket-1", monetary_contract="CURRENT_HASH_V2")["financial"]
    assert {key: money[key] for key in ("payout_eur", "net_eur", "actual_price_source")} == {
        key: current[key] for key in ("payout_eur", "net_eur", "actual_price_source")}
