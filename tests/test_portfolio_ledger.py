from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage.portfolio_ledger import PortfolioLedger

NOW = datetime(2026, 9, 20, 10, tzinfo=UTC)


def ticket(identifier="ticket-1", profile="DAILY_223", fixture_ids=(1, 2, 3), **updates):
    item = {
        "ticket_id": identifier,
        "profile_id": profile,
        "profile_name": profile,
        "date": "2026-09-20",
        "created_at": NOW.isoformat(),
        "bookmaker": "test-book",
        "region": "eu",
        "currency": "EUR",
        "combined_price": 12.0,
        "stake_eur": 5,
        "status": "READY",
        "legs": [
            {
                "fixture_id": f"api_football:{fixture_id}",
                "kickoff": (NOW + timedelta(hours=3)).isoformat(),
                "market_key": "totals_2_5",
                "selection_key": "over",
                "period": "FULL_TIME",
                "settlement": "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
                "decimal_price": price,
                "quoted_at": NOW.isoformat(),
            }
            for fixture_id, price in zip(fixture_ids, (2, 2, 3), strict=True)
        ],
    }
    item.update(updates)
    return item


def report(*tickets, identifier="run-1", observed_at=NOW):
    return {
        "run_id": identifier,
        "date": "2026-09-20",
        "observed_at": observed_at.isoformat(),
        "tickets": list(tickets),
        "usage": {"football_calls": 2, "odds_calls": 1},
        "astra": {"usage": {"input_tokens": 100, "output_tokens": 20}},
    }


def fixture(identifier, fulltime=(2, 1), halftime=(1, 0), status="FT", goals=None):
    return {
        "fixture": {"id": identifier, "status": {"short": status}},
        "score": {
            "fulltime": {"home": fulltime[0], "away": fulltime[1]},
            "halftime": {"home": halftime[0], "away": halftime[1]},
        },
        "goals": {"home": (goals or fulltime)[0], "away": (goals or fulltime)[1]},
    }


class Football:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def request(self, endpoint, params):
        self.calls.append((endpoint, params))
        item = self.rows[params["id"]]
        if isinstance(item, Exception):
            raise item
        return {"response": [item]}


@pytest.fixture
def ledger(tmp_path):
    instance = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    return instance


def finish(ledger, rows=None):
    ledger.now = lambda: NOW + timedelta(hours=6)
    client = Football(rows or {i: fixture(i) for i in (1, 2, 3)})
    return ledger.refresh_results(client), client


def test_round_trip_and_idempotent_snapshots_are_immutable(ledger):
    original = report(ticket())
    assert ledger.save_run(original)["saved"]
    assert not ledger.save_run(deepcopy(original))["saved"]
    assert ledger.list_runs(date="2026-09-20") == [original]
    changed = deepcopy(original)
    changed["tickets"][0]["combined_price"] = 13
    with pytest.raises(ValueError, match="immutable snapshot"):
        ledger.save_run(changed)
    changed["run_id"] = "different-run"
    with pytest.raises(ValueError, match="ticket_id already exists"):
        ledger.save_run(changed)
    assert len(ledger.list_runs()) == 1
    loaded = PortfolioLedger(ledger.path).get_ticket("ticket-1")
    assert loaded["legs"] == original["tickets"][0]["legs"]
    assert loaded["active"] and not loaded["played"]
    assert loaded["outcome"] == "PENDING"


def test_manual_wager_is_explicit_idempotent_and_separated_from_research(ledger):
    ledger.save_run(report(ticket(), ticket("research", "MERLIN")))
    assert ledger.performance()["summary"]["tickets"] == 0
    booked = ledger.record_wager("ticket-1", actual_combined_price=11.9)
    assert booked["played"] and booked["wager"]["actual_combined_price"] == 11.9
    assert ledger.record_wager("ticket-1", actual_combined_price=11.9) == booked
    with pytest.raises(ValueError, match="different amounts"):
        ledger.record_wager("ticket-1", actual_combined_price=12)
    assert ledger.performance()["summary"]["tickets"] == 1
    assert ledger.performance(mode="research")["summary"]["tickets"] == 1
    assert ledger.performance()["summary"]["open_stake_eur"] == 5
    assert ledger.performance()["summary"]["net_eur"] == 0
    assert ledger.performance()["summary"]["cash_flow_eur"] == -5


def test_win_uses_actual_price_and_two_refreshes_do_not_duplicate_results(ledger):
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1", actual_combined_price=11.9)
    result, _ = finish(ledger)
    assert result["football_calls"] == 3
    assert result["updated_tickets"] == 1
    second, client = finish(ledger)
    assert second["football_calls"] == second["updated_tickets"] == 0
    assert not client.calls
    view = ledger.get_ticket("ticket-1")
    assert view["outcome"] == "WON"
    assert view["financial"]["payout_eur"] == 59.5
    assert view["financial"]["net_eur"] == 54.5
    stats = ledger.performance(month="2026-09")
    assert stats["summary"]["won"] == 1
    assert stats["summary"]["win_rate"] == 1
    assert stats["summary"]["yield_pct"] == 1090
    assert stats["series"][0]["cumulative_net_eur"] == 54.5
    assert stats["api_usage"]["result_football_calls"] == 3
    assert stats["api_usage"]["input_tokens"] == 100
    assert ledger.performance(month="2026-10")["summary"]["tickets"] == 0


def test_repeated_runs_have_one_profile_version_and_five_total_including_daily223(ledger):
    first = [ticket(f"first-{i}", f"P{i}") for i in range(6)]
    first.append(ticket("daily", "DAILY_223"))
    ledger.save_run(report(*first))
    active = ledger.list_tickets()
    assert len(active) == 5
    assert "daily" in {t["ticket_id"] for t in active}
    ledger.record_wager("first-0")
    second = [ticket(f"second-{i}", f"P{i}") for i in range(5)]
    ledger.save_run(
        report(
            *second,
            ticket("daily-new", "DAILY_223"),
            identifier="run-2",
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    active = ledger.list_tickets()
    assert len(active) == 5
    assert len({t["profile_id"] for t in active}) == 5
    assert {"first-0", "daily-new"} <= {t["ticket_id"] for t in active}
    assert len(ledger.list_tickets(mode="archive")) == 13
    with pytest.raises(ValueError, match="superseded"):
        ledger.record_wager("daily")
    for t in active:
        ledger.record_wager(t["ticket_id"])
    ledger.save_run(
        report(
            ticket("third-new", "NEW"), identifier="run-3", observed_at=NOW + timedelta(minutes=2)
        )
    )
    assert len(ledger.list_tickets(mode="actual")) == 5
    assert not ledger.get_ticket("third-new")["active"]


def test_a_new_run_does_not_remove_started_losing_research_tickets(ledger):
    ledger.save_run(report(ticket()))
    finish(ledger, {i: fixture(i, fulltime=(0, 0)) for i in (1, 2, 3)})
    later = ticket("later", "DAILY_223", created_at=(NOW + timedelta(hours=6)).isoformat())
    for leg in later["legs"]:
        leg["kickoff"] = (NOW + timedelta(hours=8)).isoformat()
    ledger.save_run(report(later, identifier="late-run", observed_at=NOW + timedelta(hours=6)))
    assert ledger.get_ticket("ticket-1")["active"]
    assert not ledger.get_ticket("later")["active"]
    assert ledger.performance(mode="research")["summary"]["lost"] == 1


def test_old_import_never_supersedes_newer_run(ledger):
    ledger.save_run(report(ticket("new"), identifier="new", observed_at=NOW + timedelta(minutes=1)))
    ledger.save_run(report(ticket("old"), identifier="old"))
    assert [t["ticket_id"] for t in ledger.list_tickets()] == ["new"]
    assert ledger.list_runs()[0]["run_id"] == "new"


@pytest.mark.parametrize("stake", [True, -1, 0, float("nan"), float("inf"), 5.005, 10])
def test_invalid_stakes_are_rejected(ledger, stake):
    ledger.save_run(report(ticket()))
    with pytest.raises(ValueError):
        ledger.record_wager("ticket-1", stake_eur=stake)
    assert ledger.performance()["summary"]["tickets"] == 0


def test_wager_after_kickoff_rejected_but_existing_click_remains_idempotent(ledger):
    ledger.save_run(report(ticket(), ticket("other", "MERLIN")))
    ledger.record_wager("ticket-1")
    ledger.now = lambda: NOW + timedelta(hours=3)
    assert ledger.record_wager("ticket-1")["played"]
    with pytest.raises(ValueError, match="already started"):
        ledger.record_wager("other")


def test_draft_requires_explicit_actual_price_and_no_postkickoff_snapshot_is_saved(ledger):
    ledger.save_run(report(ticket(status="DRAFT")))
    with pytest.raises(ValueError, match="draft"):
        ledger.record_wager("ticket-1")
    assert ledger.record_wager("ticket-1", actual_combined_price=12)["played"]
    late_report = report(ticket("new"), identifier="late", observed_at=NOW + timedelta(hours=4))
    with pytest.raises(ValueError, match="after fixture kickoff"):
        ledger.save_run(late_report)
    assert len(ledger.list_runs()) == 1


def test_cup_result_uses_regulation_score_not_extra_time_goals(ledger):
    item = ticket()
    item["legs"][0].update(market_key="h2h", selection_key="draw")
    ledger.save_run(report(item))
    rows = {i: fixture(i) for i in (1, 2, 3)}
    rows[1] = fixture(1, status="AET", fulltime=(1, 1), goals=(3, 1))
    finish(ledger, rows)
    view = ledger.get_ticket("ticket-1")
    assert view["outcome"] == "WON"
    assert view["settlement"]["legs"][0]["home_goals"] == 1


def test_first_half_over_half_settles_only_after_first_half_finishes(ledger):
    item = ticket()
    for leg in item["legs"]:
        leg.update(market_key="totals_0_5", period="FIRST_HALF")
    ledger.save_run(report(item))
    rows = {i: fixture(i, status="1H", halftime=(1, 0)) for i in (1, 2, 3)}
    finish(ledger, rows)
    assert ledger.get_ticket("ticket-1")["outcome"] == "PENDING"
    rows = {i: fixture(i, status="HT", halftime=(1, 0), fulltime=(0, 0)) for i in (1, 2, 3)}
    finish(ledger, rows)
    assert ledger.get_ticket("ticket-1")["outcome"] == "WON"


def test_missing_score_never_becomes_zero_or_a_false_under_win(ledger):
    item = ticket()
    for leg in item["legs"]:
        leg["selection_key"] = "under"
    ledger.save_run(report(item))
    finish(ledger, {i: fixture(i, fulltime=(None, None), goals=(0, 0)) for i in (1, 2, 3)})
    assert ledger.get_ticket("ticket-1")["outcome"] == "REVIEW"
    assert ledger.performance(mode="research")["summary"]["won"] == 0


def test_postponed_fixture_requires_bookmaker_void_confirmation(ledger):
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1", actual_combined_price=12.6)
    finish(ledger, {1: fixture(1, status="PST"), 2: fixture(2), 3: fixture(3)})
    assert ledger.get_ticket("ticket-1")["outcome"] == "REVIEW"
    view = ledger.record_void(
        "ticket-1", "api_football:1", "Bookmaker explicitly refunded this selection"
    )
    assert view["outcome"] == "REVIEW"
    assert view["financial"]["payout_eur"] == 0
    assert not view["financial"]["settled"]
    # Actual accepted first-leg odds were 2.1: total odds alone cannot determine this.
    view = ledger.confirm_bookmaker_return("ticket-1", 30, "Bookmaker confirmed 30 EUR returned")
    assert view["outcome"] == "WON"
    assert view["financial"]["payout_eur"] == 30
    assert view["financial"]["net_eur"] == 25
    ledger.record_void("ticket-1", "api_football:2", "Bookmaker explicitly voided leg 2")
    assert ledger.get_ticket("ticket-1")["outcome"] == "REVIEW"
    view = ledger.record_void("ticket-1", "api_football:3", "Bookmaker explicitly voided leg 3")
    assert view["outcome"] == "VOID"
    assert view["financial"]["payout_eur"] == 5
    assert view["financial"]["net_eur"] == 0


def test_one_loss_settles_ticket_but_later_scores_are_still_refreshed(ledger):
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1")
    rows = {1: fixture(1, fulltime=(0, 0)), 2: fixture(2, status="2H"), 3: fixture(3, status="2H")}
    finish(ledger, rows)
    view = ledger.get_ticket("ticket-1")
    assert view["outcome"] == "LOST"
    assert view["financial"]["net_eur"] == -5
    result, _ = finish(ledger)
    assert result["football_calls"] == 2
    assert all(
        leg["outcome"] in {"WON", "LOST"}
        for leg in ledger.get_ticket("ticket-1")["settlement"]["legs"]
    )
    assert ledger.performance()["summary"]["max_drawdown_eur"] == 5


def test_refresh_request_cap_rotates_unresolved_matches_and_redacts_errors(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW, refresh_call_limit=1)
    ledger.save_run(report(ticket()))
    bad = {i: RuntimeError("https://provider.test?apiKey=SECRET") for i in (1, 2, 3)}
    result, client = finish(ledger, bad)
    assert result["football_calls"] == 1 and result["remaining_fixtures"] == 2
    assert "SECRET" not in str(result)
    result, second = finish(ledger, bad)
    assert second.calls[0][1]["id"] != client.calls[0][1]["id"]


def test_identical_fixture_across_tickets_is_only_requested_once(ledger):
    ledger.save_run(report(ticket(), ticket("second", "MERLIN")))
    result, client = finish(ledger)
    assert result["football_calls"] == len(client.calls) == 3
    assert result["updated_tickets"] == 2
    feedback = ledger.feedback_summary()
    assert len(feedback["recent_settled"]) == 2
    assert feedback["actual"]["summary"]["tickets"] == 0


def test_provider_wrong_fixture_is_not_used_as_result(ledger):
    ledger.save_run(report(ticket()))
    result, _ = finish(ledger, {i: fixture(999) for i in (1, 2, 3)})
    assert len(result["errors"]) == 3
    assert ledger.get_ticket("ticket-1")["outcome"] == "PENDING"


def test_future_fixture_makes_no_network_call(ledger):
    ledger.save_run(report(ticket()))
    client = Football({})
    result = ledger.refresh_results(client)
    assert result["football_calls"] == 0
    assert not client.calls


def test_terminal_results_are_rechecked_and_a_b_a_corrections_preserve_observations(ledger):
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1")
    finish(ledger)
    assert ledger.get_ticket("ticket-1")["outcome"] == "WON"
    corrected = {1: fixture(1, fulltime=(0, 0)), 2: fixture(2), 3: fixture(3)}
    ledger.now = lambda: NOW + timedelta(hours=12)
    assert ledger.refresh_results(Football(corrected))["football_calls"] == 3
    assert ledger.get_ticket("ticket-1")["outcome"] == "LOST"
    ledger.now = lambda: NOW + timedelta(hours=18)
    assert (
        ledger.refresh_results(Football({i: fixture(i) for i in (1, 2, 3)}))["football_calls"] == 3
    )
    assert ledger.get_ticket("ticket-1")["outcome"] == "WON"
    assert ledger.performance()["summary"]["net_eur"] == 55
    with ledger._connection() as db:
        observations = db.execute(
            "SELECT count(*) FROM portfolio_settlement_observations WHERE ticket_id='ticket-1'"
        ).fetchone()[0]
        snapshots = db.execute(
            "SELECT count(*) FROM portfolio_settlements WHERE ticket_id='ticket-1'"
        ).fetchone()[0]
        assert observations == 3 and snapshots == 2
    ledger.now = lambda: NOW + timedelta(hours=60)
    assert ledger.refresh_results(Football({}))["football_calls"] == 0


def test_later_leg_completion_does_not_reorder_realized_loss_drawdown(ledger):
    ledger.save_run(
        report(
            ticket("a", "A"),
            ticket("b", "B", fixture_ids=(4, 5, 6)),
            ticket("c", "C", fixture_ids=(7, 8, 9)),
        )
    )
    for identifier in ("a", "b", "c"):
        ledger.record_wager(identifier)
    rows = {i: fixture(i, status="2H") for i in range(1, 10)}
    rows[1] = fixture(1, fulltime=(0, 0))
    ledger.now = lambda: NOW + timedelta(hours=6)
    ledger.refresh_results(Football(rows))
    loss_time = ledger.get_ticket("a")["settlement"]["settled_at"]
    rows[4] = fixture(4, fulltime=(0, 0))
    ledger.now = lambda: NOW + timedelta(hours=7)
    ledger.refresh_results(Football(rows))
    for i in (7, 8, 9):
        rows[i] = fixture(i)
    ledger.now = lambda: NOW + timedelta(hours=8)
    ledger.refresh_results(Football(rows))
    assert ledger.performance()["summary"]["max_drawdown_eur"] == 10
    for i in (2, 3):
        rows[i] = fixture(i)
    ledger.now = lambda: NOW + timedelta(hours=9)
    ledger.refresh_results(Football(rows))
    assert ledger.performance()["summary"]["max_drawdown_eur"] == 10
    assert ledger.get_ticket("a")["settlement"]["settled_at"] == loss_time


def test_failed_empty_rerun_preserves_existing_pre_match_recommendations(ledger):
    ledger.save_run(report(ticket()))
    ledger.save_run(report(identifier="empty", observed_at=NOW + timedelta(minutes=1)))
    assert ledger.get_ticket("ticket-1")["active"]
    assert len(ledger.list_tickets()) == 1
    assert ledger.list_runs()[0]["tickets"] == []


def test_concurrent_double_click_records_one_immutable_wager(ledger):
    ledger.save_run(report(ticket()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(
            pool.map(lambda _: ledger.record_wager("ticket-1", actual_combined_price=12), range(2))
        )
    assert all(answer["played"] for answer in answers)
    assert ledger.performance()["summary"]["tickets"] == 1
    assert ledger.performance()["summary"]["stake_eur"] == 5
