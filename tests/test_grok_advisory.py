"""Offline tests for isolated Grok research, reservations and the blind packet."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from goal_analysis.jobs import grok_advisory as job

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)


def configure(root, **changes):
    path = root / "config/grok.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": True, "mode": "SHADOW", **changes}), "utf-8")


def leg(identifier=1, **changes):
    return {"fixture_id": f"api_football:{identifier}", "home_team": f"Home {identifier}",
            "away_team": f"Away {identifier}", "kickoff": (NOW + timedelta(hours=5)).isoformat(),
            "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME",
            "decimal_price": 2.3, "model_probability": .55, "selection_reason": "SECRET_CODEX",
            "evidence": {"confidence": "SECRET_CONFIDENCE"}, **changes}


def recommendation(profile="tipster_szines_reka", fixtures=(1, 2), **changes):
    return {"profile_id": profile, "recommendation_id": profile + "-id", "status": "READY",
            "legs": [leg(identifier) for identifier in fixtures], "reasoning": "SECRET_REASONING",
            "diversification": {"quality": .7}, **changes}


def report(*rows, **changes):
    return {"run_id": "run-1", "date": "2026-10-18", "observed_at": NOW.isoformat(),
            "recommendations": list(rows or [recommendation()]),
            "astra": {"text": "SECRET_ASTRA"}, "api_key": "SECRET_KEY", **changes}


class Advisor:
    def __init__(self, cost=.1, status="COMPLETE"):
        self.packets = []
        self.cost, self.status = cost, status

    def review(self, packet):
        self.packets.append(deepcopy(packet))
        return {"status": self.status, "model": "grok-4.7", "claims": [], "critiques": [],
                "citations": [], "diagnostics": {},
                "usage": {"request_count": 1, "cost_usd": self.cost}}


def test_disabled_and_readonly_snapshot_create_no_store(tmp_path):
    advisor = Advisor()
    assert job.analyze(tmp_path, report(), advisor=advisor, now=NOW)["status"] == "DISABLED"
    assert job.snapshot(tmp_path, "2026-10-18")["status"] == "DISABLED"
    assert not (tmp_path / "data").exists()
    assert not advisor.packets


@pytest.mark.parametrize("config", [
    {"mode": "LIVE"}, {"daily_budget_usd": 2}, {"monthly_budget_usd": 21},
    {"max_requests_per_day": 3}, {"model": "other-model"}, {"daily_budget_usd": True},
])
def test_unsafe_or_unintended_config_stays_disabled(tmp_path, config):
    configure(tmp_path, **config)
    assert job.analyze(tmp_path, report(), advisor=Advisor(), now=NOW)["status"] == "DISABLED"
    assert not (tmp_path / "data").exists()


def test_blind_packet_deduplicates_fixtures_and_never_changes_report(tmp_path):
    configure(tmp_path)
    daily = recommendation("daily223", (90, 91, 92))
    data = report(recommendation("tipster_archiv_nora", (8, 9)),
                  recommendation("tipster_fuzo_oszkar", (2, 3)), daily,
                  recommendation("tipster_szines_reka", (1, 2)))
    before = deepcopy(data)
    advisor = Advisor()
    outcome = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert outcome["status"] == "COMPLETE" and outcome["automatic_changes"] is False
    assert data == before
    packet = advisor.packets[0]
    assert [row["profile_id"] for row in packet["recommendations"]] == [
        "daily223", "tipster_archiv_nora", "tipster_fuzo_oszkar", "tipster_szines_reka"]
    assert len(packet["fixtures"]) == 8
    assert len(packet["selections"]) == 8
    assert packet["recommendations"][0]["protected"] is True
    assert "SECRET" not in json.dumps(packet)
    assert not (tmp_path / "data/arthur/portfolio.sqlite3").exists()
    assert job.snapshot(tmp_path, "2026-10-18")["latest"]["coverage"]["fixture_count"] == 8


def test_every_ready_profile_is_included_without_partial_ticket_truncation(tmp_path):
    configure(tmp_path)
    data = report(recommendation("tipster_szines_reka", range(1, 9)),
                  recommendation("tipster_fuzo_oszkar", range(9, 13)),
                  recommendation("tipster_archiv_nora", (1, 9), diversification={"quality": .9}),
                  recommendation("tipster_forma_soma", (1, 10), diversification={"quality": .8}))
    advisor = Advisor()
    job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    packet = advisor.packets[0]
    assert [row["profile_id"] for row in packet["recommendations"]] == [
        "tipster_archiv_nora", "tipster_forma_soma", "tipster_fuzo_oszkar", "tipster_szines_reka"]
    assert [len(row["legs"]) for row in packet["recommendations"]] == [2, 2, 4, 8]
    assert len(packet["fixtures"]) == 12
    assert len(packet["selections"]) == 12


@pytest.mark.parametrize("offset", [0, 14, 15])
def test_any_locked_leg_excludes_entire_profile(tmp_path, offset):
    configure(tmp_path)
    data = report(recommendation(legs=[leg(1), leg(2, kickoff=(
        NOW + timedelta(minutes=offset)).isoformat())]))
    advisor = Advisor()
    assert job.analyze(tmp_path, data, advisor=advisor, now=NOW)["status"] == "NO_ELIGIBLE_FIXTURES"
    assert not advisor.packets
    assert not (tmp_path / "data").exists()


def test_locked_profile_does_not_prevent_other_eligible_clubs(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    data = report(recommendation(legs=[leg(99, kickoff=NOW.isoformat())]),
                  recommendation("tipster_fuzo_oszkar"))
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert result["coverage"]["profile_ids"] == ["tipster_fuzo_oszkar"]


def test_cache_survives_restart_and_cutoff_is_not_in_cache_key(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    first = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    second = job.analyze(tmp_path, report(), advisor=Advisor(), now=NOW + timedelta(minutes=59))
    assert first["request_id"] == second["request_id"]
    assert second["status"] == "CACHED"
    assert job.snapshot(tmp_path, "2026-10-18")["usage"]["day_requests"] == 1


def test_identical_packet_is_never_resent_after_sixty_minutes(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    assert job.analyze(tmp_path, report(), advisor=advisor, now=NOW)["status"] == "COMPLETE"
    cached = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(hours=2))
    assert cached["status"] == "CACHED" and cached["cache_stale"] is True
    assert cached["cache_age_seconds"] == 7200
    assert len(advisor.packets) == 1


def test_semantic_cache_ignores_new_run_and_recommendation_timestamps(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    data = report(recommendation(recommendation_id="new-id"), run_id="run-2",
                  observed_at=(NOW + timedelta(minutes=1)).isoformat())
    cached = job.analyze(tmp_path, data, advisor=advisor, now=NOW + timedelta(minutes=1))
    assert cached["status"] == "CACHED" and len(advisor.packets) == 1
    assert cached["cached_from_run_id"] == "run-1"
    assert list(cached["current_coverage"]["current_source_ids"].values()) == ["new-id"]


def test_changed_odds_invalidate_cache_but_daily_limit_stops_third_changed_packet(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    for price, expected in ((2.4, "COMPLETE"), (2.5, "BUDGET")):
        data = report(recommendation(legs=[leg(1, decimal_price=price), leg(2)]))
        assert job.analyze(tmp_path, data, advisor=advisor,
                           now=NOW + timedelta(minutes=1))["status"] == expected
    assert len(advisor.packets) == 2


def test_changed_provider_prompt_or_validator_invalidates_cache(tmp_path, monkeypatch):
    configure(tmp_path)
    advisor = Advisor()
    job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    monkeypatch.setattr(job, "_provider_version", lambda: "new-provider-version")
    result = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=1))
    assert result["status"] == "COMPLETE" and len(advisor.packets) == 2


def test_future_cache_is_never_returned(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    data = report(observed_at=(NOW - timedelta(hours=2)).isoformat())
    job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW - timedelta(minutes=1))
    assert result["status"] == "BUDGET" and result["error_code"] == "CLOCK_ROLLBACK"
    assert len(advisor.packets) == 1


def test_unknown_cost_consumes_full_remaining_reservation(tmp_path):
    configure(tmp_path)
    first = job.analyze(tmp_path, report(), advisor=Advisor(.2), now=NOW)
    second = job.analyze(tmp_path, report(recommendation(legs=[leg(3), leg(4)]), run_id="run-2"), advisor=Advisor(None),
                         now=NOW + timedelta(minutes=1))
    assert first["charged_usd"] == .2
    assert second["charged_usd"] == pytest.approx(.8) and not second["cost_known"]
    usage = job.snapshot(tmp_path, "2026-10-18")["usage"]
    assert usage["day_charged_usd"] == pytest.approx(1)
    assert usage["day_known_cost_usd"] == .2
    assert usage["day_reserved_unknown_usd"] == pytest.approx(.8)


def test_provider_exception_is_sanitized_and_no_automatic_retry(tmp_path):
    configure(tmp_path)

    class Failed:
        calls = 0

        def review(self, packet):
            self.calls += 1
            raise RuntimeError("api-key-SECRET upstream content")

    advisor = Failed()
    result = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    assert result["status"] == "UNAVAILABLE" and result["charged_usd"] == 1
    assert "SECRET" not in json.dumps(result)
    assert job.analyze(tmp_path, report(), advisor=advisor,
                       now=NOW + timedelta(minutes=5))["status"] == "BUDGET"
    assert advisor.calls == 1


def test_no_network_response_does_not_charge_unknown_cost(tmp_path):
    configure(tmp_path)

    class MissingKey:
        def review(self, packet):
            return {"status": "UNAVAILABLE", "error_code": "MISSING_API_KEY",
                    "usage": {"request_count": 0, "cost_usd": None}}

    result = job.analyze(tmp_path, report(), advisor=MissingKey(), now=NOW)
    assert result["charged_usd"] == 0 and result["cost_known"] is True


def test_concurrent_second_caller_cannot_send_and_network_has_no_db_lock(tmp_path):
    configure(tmp_path)
    entered, release = Event(), Event()

    class Blocking(Advisor):
        def review(self, packet):
            # Another write transaction is possible during network I/O.
            with sqlite3.connect(job._path(tmp_path), timeout=.2) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            entered.set()
            assert release.wait(5)
            return super().review(packet)

    advisor = Blocking()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(job.analyze, tmp_path, report(), advisor=advisor, now=NOW)
        assert entered.wait(5)
        second = executor.submit(job.analyze, tmp_path, report(run_id="run-2"),
                                 advisor=advisor, now=NOW)
        try:
            assert second.result(timeout=3)["status"] == "BUSY"
        finally:
            release.set()
        assert first.result(timeout=3)["status"] == "COMPLETE"
    assert len(advisor.packets) == 1


def test_crashed_request_keeps_full_budget_after_restart(tmp_path):
    configure(tmp_path)
    packet, coverage, run_id, digest = job._packet(report(), NOW)
    reserved = job._reserve(tmp_path, packet, coverage, run_id, digest, job._config(tmp_path), NOW)
    assert "request_id" in reserved
    advisor = Advisor()
    result = job.analyze(tmp_path, report(), advisor=advisor, now=NOW + timedelta(minutes=4))
    assert result["status"] == "BUDGET" and not advisor.packets
    assert job.snapshot(tmp_path, "2026-10-18")["usage"]["day_reserved_unknown_usd"] == 1


def test_monthly_budget_applies_across_berlin_calendar_days(tmp_path):
    configure(tmp_path, monthly_budget_usd=.5)
    result = job.analyze(tmp_path, report(), advisor=Advisor(.5), now=NOW)
    assert result["charged_usd"] == .5
    tomorrow = NOW + timedelta(days=1)
    data = report(date="2026-10-19", observed_at=tomorrow.isoformat(), run_id="tomorrow",
                  recommendations=[recommendation(legs=[leg(
                      kickoff=(tomorrow + timedelta(hours=4)).isoformat())])])
    assert job.analyze(tmp_path, data, advisor=Advisor(), now=tomorrow)["status"] == "BUDGET"


def test_daily_budget_uses_berlin_day_not_utc(tmp_path):
    configure(tmp_path)
    late = datetime(2026, 10, 17, 22, 30, tzinfo=UTC)
    data = report(observed_at=late.isoformat())
    job.analyze(tmp_path, data, advisor=Advisor(.1), now=late)
    assert job.snapshot(tmp_path, "2026-10-18")["usage"]["day_requests"] == 1
    assert job.snapshot(tmp_path, "2026-10-17")["usage"]["day_requests"] == 0


def test_actual_cost_above_reservation_is_recorded_without_capping(tmp_path):
    configure(tmp_path)
    result = job.analyze(tmp_path, report(), advisor=Advisor(1.5), now=NOW)
    assert result["charged_usd"] == 1.5
    assert job.analyze(tmp_path, report(recommendation(legs=[leg(3), leg(4)]), run_id="run-2"), advisor=Advisor(),
                       now=NOW + timedelta(minutes=1))["status"] == "BUDGET"


def test_immutable_request_audit_and_snapshot_is_read_only(tmp_path):
    configure(tmp_path)
    job.analyze(tmp_path, report(), advisor=Advisor(), now=NOW)
    path = job._path(tmp_path)
    before = path.read_bytes()
    snapshot = job.snapshot(tmp_path, "2026-10-18")
    assert path.read_bytes() == before
    assert snapshot["latest"]["run_id"] == "run-1"
    assert "SECRET" not in json.dumps(snapshot)
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT packet_json FROM grok_requests").fetchone()[0]
        assert "SECRET" not in stored
        assert connection.execute("SELECT count(*) FROM grok_results").fetchone()[0] == 1
        for table in ("grok_requests", "grok_results"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(f"DELETE FROM {table}")


def test_job_and_real_provider_contract_with_mocked_transport(tmp_path):
    from goal_analysis.agents.grok_research import GrokAdvisor

    configure(tmp_path)
    sent = []
    rid = job._packet(report(), NOW)[0]["recommendations"][0]["recommendation_id"]

    def transport(payload, **kwargs):
        sent.append(payload)
        return {"status": "completed", "citations": [],
                "usage": {"cost_in_usd_ticks": 10_000_000},
                "output": [{"type": "message", "content": [{"type": "output_text",
                    "text": json.dumps({"claims": [], "critiques": [{
                        "recommendation_id": rid, "profile_id": "tipster_szines_reka",
                        "verdict": "INSUFFICIENT_EVIDENCE", "strengths": "Shared numerical estimate.",
                        "risks": "No independent news.", "flagged_legs": [], "claim_ids": []}]})}]}]}

    advisor = GrokAdvisor(api_key="mock-only", transport=transport)
    outcome = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    assert outcome["status"] == "COMPLETE"
    assert outcome["charged_usd"] == .001
    assert len(outcome["result"]["critiques"]) == 1
    assert len(sent) == 1 and "SECRET" not in json.dumps(sent)


def test_invalid_clock_and_future_report_do_not_call_provider(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    assert job.analyze(tmp_path, report(), advisor=advisor,
                       now=NOW.replace(tzinfo=None))["error_code"] == "INVALID_CLOCK"
    assert job.analyze(tmp_path, report(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
                       advisor=advisor, now=NOW)["error_code"] == "INVALID_REPORT"
    assert not advisor.packets


def test_all_actual_report_statuses_and_ticket_mirrors_are_covered(tmp_path):
    configure(tmp_path)
    daily = recommendation("daily223", (1, 2, 3), status="REVIEWED")
    mirrored = {**deepcopy(daily), "status": "READY", "ticket_id": "daily-ticket"}
    data = report(daily, recommendation("kronikas", (4, 5), status="REVIEWED"),
                  recommendation("orszem", (6, 7), status="DRAFT"),
                  recommendation("gauss", (8, 9), status="MODEL_READY"),
                  recommendation("tipster_szines_reka", (10, 11)),
                  recommendation("tipster_golvadasz_lili", (), status="ABSTAINED"),
                  tickets=[mirrored])
    before, advisor = deepcopy(data), Advisor()
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert result["status"] == "COMPLETE" and len(advisor.packets) == 1
    assert data == before
    rows = advisor.packets[0]["recommendations"]
    assert len(rows) == 5 and {row["status"] for row in rows} == {
        "REVIEWED", "DRAFT", "MODEL_READY", "READY"}
    assert next(row for row in rows if row["profile_id"] == "daily223")["protected"] is True
    assert result["coverage"]["excluded"] == [{"profile_id": "tipster_golvadasz_lili",
                                               "reason_code": "NO_COMPLETE_TICKET"}]
    assert advisor.packets[0]["objective"] == {
        "target_win_count": 3, "target_winning_odds_sum": 30, "stake_eur": 1,
        "total_ticket_count": 5, "total_stake_eur": 5, "breakeven_winning_odds_sum": 5,
        "aspiration_not_guarantee": True}


@pytest.mark.parametrize("oversize", ["tickets", "fixtures", "legs", "text"])
def test_oversized_packet_is_explicitly_rejected_never_partially_sent(tmp_path, monkeypatch, oversize):
    configure(tmp_path)
    if oversize == "tickets":
        profiles = [f"test_{number}" for number in range(41)]
        monkeypatch.setattr(job, "PROFILE_IDS", profiles)
        data = report(*(recommendation(profile, (1,)) for profile in profiles))
    elif oversize == "fixtures":
        data = report(*(recommendation(profile, range(index * 20, index * 20 + 20))
                        for index, profile in enumerate(job.PROFILE_IDS[:9])))
    elif oversize == "legs":
        data = report(recommendation(fixtures=range(21)))
    else:
        # A wider otherwise valid field set must also respect the prompt byte policy.
        monkeypatch.setattr(job, "_fixture_extra", lambda *_: {
            "api_football:1": {"existing_context": [{"summary": "x" * 100_001}]}})
        data = report()
    advisor = Advisor()
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    if oversize == "text":
        assert result["status"] == "COMPLETE"
        assert result["coverage"]["compaction"]["additional_truncated_text_fields"] == 1
        assert len(job._json(advisor.packets[0])) < 100_000
        return
    assert result["status"] == "PACKET_TOO_LARGE" and not advisor.packets
    assert not (tmp_path / "data").exists()


def test_cached_full_package_is_not_resent_when_one_ticket_becomes_locked(tmp_path):
    configure(tmp_path)
    data = report(recommendation(legs=[leg(1, kickoff=(NOW + timedelta(minutes=40)).isoformat())]),
                  recommendation("tipster_fuzo_oszkar", (2, 3)))
    advisor = Advisor()
    job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    cached = job.analyze(tmp_path, data, advisor=advisor, now=NOW + timedelta(minutes=30))
    assert cached["status"] == "CACHED" and len(advisor.packets) == 1
    assert cached["current_coverage"]["excluded"] == [
        {"profile_id": "tipster_szines_reka", "reason_code": "PREMATCH_LOCKOUT"}]


def test_shared_evidence_quotes_and_numeric_data_are_compact_and_preserved(tmp_path):
    from goal_analysis.agents.grok_research import _packet as validate

    configure(tmp_path)
    historical = {"category": "historical", "claim_type": "INFERENCE",
                  "statement": "Historical 12/20 home, 10/20 away. " + "description " * 20,
                  "source_id": "history-sha256:" + "a" * 64,
                  "observed_at": NOW.isoformat(), "strength": .6, "reliability": 1}
    first = leg(1, evidence=[historical], quote_provider="api_football", quoted_at=NOW.isoformat(),
                model_stability=.123456789, estimated_edge=.1122334455,
                model_details={"shared_model_probabilities": {"gauss": .6, "laplace": .5},
                               "calibration": "unvalidated", "fits": {"SECRET": "debug"}})
    second = leg(2, evidence=[historical], quote_provider="api_football", quoted_at=NOW.isoformat())
    data = report(recommendation(legs=[first, second]))
    advisor = Advisor()
    job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    packet = advisor.packets[0]
    assert len(packet["evidence"]) == len(packet["evidence_sources"]) == len(packet["quote_sources"]) == 1
    assert packet["evidence"][0]["statement"] == historical["statement"].strip()
    assert not packet["evidence"][0].get("statement_truncated", False)
    assert packet["evidence_sources"][0]["source_id"] == historical["source_id"]
    assert "SECRET" not in json.dumps(packet)
    numbers = next(row for row in packet["selections"] if row["fixture_id"] == "api_football:1")
    assert numbers["model_stability"] == .123457
    assert numbers["model_probabilities"] == {"gauss": .6, "laplace": .5}
    assert numbers["calibration"] == "unvalidated"
    assert len(validate(packet)[0]["evidence"]) == 1


def test_future_or_sensitive_existing_evidence_never_crosses_boundary(tmp_path):
    configure(tmp_path)
    evidence = {"category": "historical", "claim_type": "INFERENCE", "statement": "Facts",
                "source_id": "history:123", "observed_at": (NOW + timedelta(seconds=1)).isoformat()}
    data = report(recommendation(legs=[leg(1, evidence=[evidence]), leg(2, evidence=[{
        **evidence, "observed_at": NOW.isoformat(), "statement": "Bearer secret-token-value"}])]))
    advisor = Advisor()
    job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert advisor.packets[0]["evidence"] == []
    assert "secret-token" not in json.dumps(advisor.packets[0])


def test_short_references_are_order_stable_and_preserve_leg_identity_and_numbers():
    first = recommendation("tipster_szines_reka", (1, 2))
    second = recommendation("tipster_fuzo_oszkar", (2, 3))
    packet, coverage, *_ = job._packet(report(first, second), NOW)
    reordered, next_coverage, *_ = job._packet(report(second, first), NOW)
    assert packet == reordered
    assert coverage["semantic_packet_sha256"] == next_coverage["semantic_packet_sha256"]
    assert {row["selection_id"] for row in packet["selections"]} == {"s1", "s2", "s3"}
    selections = {row["selection_id"]: row for row in packet["selections"]}
    originals = {row["profile_id"]: row for row in (first, second)}
    for row in packet["recommendations"]:
        resolved = [selections[leg["selection_id"]] for leg in row["legs"]]
        for actual, original in zip(resolved, originals[row["profile_id"]]["legs"], strict=True):
            for field in ("fixture_id", "market_key", "selection_key", "period",
                          "decimal_price", "model_probability"):
                assert actual[field] == original[field]


def test_historical_boilerplate_compaction_preserves_every_observed_count():
    source = "history-sha256:" + "b" * 64
    statement = ("FULL_TIME totals_2_5 / over; venue_10: hazai oldal 7/10, vendég oldal 6/10. "
                 "Utolsó 5: hazai oldal 4/5, vendég oldal 3/5; helyszíni/aktuális arány: 60/40. "
                 "Megfigyelt piaci mintázat, nem becsült nyerési esély.")
    item = leg(evidence=[{"category": "venue_form", "claim_type": "INFERENCE",
                          "source_id": source, "observed_at": NOW.isoformat(),
                          "statement": statement, "strength": .6, "reliability": 1}])
    packet, *_ = job._packet(report(recommendation(legs=[item])), NOW)
    compact = packet["evidence"][0]["statement"]
    assert len(compact) < len(statement) / 2
    counts = {"venue_10": {"home": [7, 10], "away": [6, 10]},
              "last5": {"home": [4, 5], "away": [3, 5]},
              "venue_current_weights_pct": [60, 40]}
    assert packet["evidence"][0]["history_counts"] == counts
    from goal_analysis.agents.grok_research import _packet as validate
    assert validate(packet)[0]["evidence"][0]["history_counts"] == counts
    assert packet["evidence_sources"][0]["source_id"] == source


def test_forty_rich_tickets_fit_one_strict_provider_packet(monkeypatch):
    from goal_analysis.agents import grok_research as provider

    profiles = ["daily223", *[f"tipster_test_{index}" for index in range(39)]]
    monkeypatch.setattr(job, "PROFILE_IDS", profiles)
    monkeypatch.setattr(provider, "PROFILE_IDS", profiles)
    rows = []
    for index, profile in enumerate(profiles):
        legs = []
        for offset in range(4):
            fixture = (index + offset) % 40
            history = [{"category": category, "claim_type": "INFERENCE",
                        "source_id": "history-sha256:" + "a" * 64,
                        "observed_at": NOW.isoformat(), "strength": .6, "reliability": 1,
                        "statement": f"FULL_TIME totals_2_5 / over; {category}: "
                        f"hazai oldal {fixture % 20}/20, vendég oldal {fixture % 15}/20. "
                        "Megfigyelt piaci mintázat, nem becsült nyerési esély."}
                       for category in ("historical", "venue_form")]
            legs.append(leg(fixture, evidence=history, model_probability=.5 + index / 1000,
                            quote_provider="api_football", quoted_at=NOW.isoformat(),
                            estimated_edge=.1234, model_stability=.05,
                            confidence_kind="UNCALIBRATED_MODEL_ESTIMATE",
                            model_details={"calibration": "unvalidated",
                                           "shared_model_probabilities": {"gauss": .55, "laplace": .56}}))
        rows.append(recommendation(profile, legs=legs, persona={"min_legs": 3, "max_legs": 5,
            "target_odds_band": {"min": 10, "max": 30}}))
    packet, coverage, *_ = job._packet(report(*rows), NOW)
    validated, *_ = provider._packet(packet)
    assert len(validated["recommendations"]) == 40
    assert all(len(row["legs"]) == 4 for row in validated["recommendations"])
    assert len(validated["fixtures"]) == 40 and len(validated["selections"]) == 160
    assert len(job._json(validated)) < 100_000
    assert coverage["compaction"]["symbolic_references"] is True
