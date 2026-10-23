"""Source-only dashboard facts; no ticket mutation, providers or archive rewrites."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_the_stats_context import NOW, report

from goal_analysis.jobs.source_health import source_health
from goal_analysis.jobs.source_resolution import build_source_resolution
from goal_analysis.portfolio_dashboard import ArthurState, compact_status_snapshot


def old_report():
    return {**report(), "run_id": "frozen-run", "observed_at": NOW.isoformat(),
            "finished_at": (NOW + timedelta(minutes=1)).isoformat()}


def candidate(value=2.1):
    return {"fixture_id": "api_football:1", "quote_provider": "the_odds_api", "bookmaker": "test",
        "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
        "settlement": "REGULATION_TIME", "region": "eu", "currency": "EUR",
        "quote_available": True, "decimal_price": value, "quoted_at": NOW.isoformat(),
        "quote_source_id": "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"}


def input_artifact(rows):
    return {"candidate_input": {"date": "2026-10-18", "observed_at": NOW.isoformat(), "candidates": rows}}


def test_per_field_matrix_counts_missing_primary_and_actual_fallback_separately():
    result = build_source_resolution(old_report(), input_artifact([candidate()]), as_of=NOW)
    rows = {(r["domain"], r["provider"]): r for r in result["summary"]["provider_matrix"]}
    assert rows["odds", "the_stats_api"]["missing_fields"] == 1
    fallback = rows["odds", "the_odds_api"]
    assert fallback["expected_fields"] == fallback["available_fields"] == fallback["selected_fields"] == 1
    assert fallback["fallback_fields"] == 1 and fallback["missing_fields"] == 0
    assert rows["odds", "api_football"]["selected_fields"] == 0
    for row in rows.values():
        assert row["available_fields"] + row["missing_fields"] == row["expected_fields"]


def test_same_time_conflicting_prices_do_not_count_as_usable_source_coverage():
    result = build_source_resolution(old_report(), input_artifact([candidate(), candidate(2.4)]), as_of=NOW)
    row = next(r for r in result["summary"]["provider_matrix"] if r["domain"] == "odds"
               and r["provider"] == "the_odds_api")
    assert row["conflicting_fields"] == row["missing_fields"] == 1
    assert row["available_fields"] == row["selected_fields"] == row["fallback_fields"] == 0


def test_old_archive_is_projected_at_original_cutoff_without_modifying_any_input(tmp_path):
    original = old_report()
    path = tmp_path / "reports/arthur/frozen-run/candidate_input.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(input_artifact([candidate()])["candidate_input"]), encoding="utf-8")
    raw, before = path.read_bytes(), deepcopy(original)
    result = source_health(tmp_path, original, leagues=[], now=NOW + timedelta(days=1))
    assert result["status"] == "AVAILABLE" and result["origin"] == "ARCHIVED_INPUT_AUDIT"
    assert result["as_of"] == original["finished_at"]
    assert result["candidate_input_sha256"]
    assert result["external_requests"] == 0 and result["effective_data_path"] == "LEGACY_UNCHANGED"
    assert path.read_bytes() == raw and original == before
    assert any(r["fallback_fields"] == 1 for r in result["rows"])


def test_recorded_matrix_is_read_without_recomputing_or_overwriting_report(tmp_path, monkeypatch):
    original = old_report()
    original["source_resolution"] = build_source_resolution(original, {}, as_of=NOW)["summary"]
    before = deepcopy(original)

    def forbidden(*args, **kwargs):
        pytest.fail("recorded audit must not be recomputed")

    monkeypatch.setattr("goal_analysis.jobs.source_health.build_source_resolution", forbidden)
    result = source_health(tmp_path, original, leagues=[], now=NOW + timedelta(days=1))
    assert result["origin"] == "RECORDED_RUN" and result["rows"]
    assert original == before
    result["rows"][0]["available_fields"] = 999
    assert original == before


@pytest.mark.parametrize("field", ["run", "audit", "league"])
def test_future_run_or_audit_is_not_visible_as_past_coverage(tmp_path, field):
    original = old_report()
    if field != "run":
        original["source_resolution"] = build_source_resolution(original, {}, as_of=NOW)["summary"]
        if field == "audit":
            original["source_resolution"]["as_of"] = (NOW + timedelta(days=2)).isoformat()
        else:
            original["sports_supplement"] = {"the_stats_api": {"league_coverage": {
                "as_of": (NOW + timedelta(days=2)).isoformat(), "leagues": []}}}
    with pytest.raises(ValueError, match="FUTURE"):
        source_health(tmp_path, original, leagues=[], now=NOW if field == "run" else NOW + timedelta(days=1))


def test_unscoped_archive_or_wrong_run_cannot_supply_a_past_coverage_matrix(tmp_path):
    original = old_report()
    with pytest.raises(ValueError, match="INVALID_RUN_ID"):
        source_health(tmp_path, {**original, "run_id": "../other"}, leagues=[], now=NOW + timedelta(days=1))
    path = tmp_path / "reports/arthur/frozen-run/candidate_input.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**input_artifact([])["candidate_input"], "date": "2026-10-19"}), encoding="utf-8")
    with pytest.raises(ValueError, match="ARCHIVE_RUN_MISMATCH"):
        source_health(tmp_path, original, leagues=[], now=NOW + timedelta(days=1))


def test_dashboard_caches_read_only_projection_and_failure_keeps_it_separate(tmp_path, monkeypatch):
    state, calls = ArthurState(tmp_path), []

    def audit(*args, **kwargs):
        calls.append(1)
        return {"rows": [{"missing_fields": 3}], "status": "AVAILABLE"}

    monkeypatch.setattr("goal_analysis.jobs.source_health.source_health", audit)
    first = state.source_health(old_report(), {}, NOW + timedelta(days=1))
    first["rows"][0]["missing_fields"] = 999
    assert state.source_health(old_report(), {}, NOW + timedelta(days=1))["rows"][0]["missing_fields"] == 3
    assert len(calls) == 1
    state.source_health_cache.clear()

    def broken(*args, **kwargs):
        raise ValueError("test")

    monkeypatch.setattr("goal_analysis.jobs.source_health.source_health", broken)
    assert state.source_health(old_report(), {}, NOW + timedelta(days=1))["status"] == "UNAVAILABLE"


def test_clock_rollback_cannot_serve_a_future_run_from_dashboard_cache(tmp_path):
    state = ArthurState(tmp_path)
    assert state.source_health(old_report(), {}, NOW + timedelta(minutes=2))["status"] == "AVAILABLE"
    assert state.source_health(old_report(), {}, NOW)["status"] == "UNAVAILABLE"
    assert state.source_health(old_report(), {}, NOW + timedelta(minutes=2))["status"] == "AVAILABLE"


def test_clock_rollback_cannot_serve_future_no_run_league_inventory(tmp_path):
    state = ArthurState(tmp_path)
    later = state.source_health(None, {}, NOW + timedelta(days=1))
    earlier = state.source_health(None, {}, NOW)
    assert later["league_coverage"]["as_of"] != earlier["league_coverage"]["as_of"]
    assert earlier["league_coverage"]["as_of"] == NOW.isoformat()


def test_phone_projection_preserves_matrix_and_original_ticket_body():
    ticket = {"profile_id": "daily223", "legs": [{"fixture_id": "api_football:1"}]}
    snapshot = {"latest": {"tickets": [ticket]}, "source_health": {"rows": [{"missing_fields": 1}]}}
    before = deepcopy(snapshot)
    small = compact_status_snapshot(snapshot)
    assert small["source_health"] == snapshot["source_health"]
    assert small["latest"]["tickets"] == [ticket] and snapshot == before
