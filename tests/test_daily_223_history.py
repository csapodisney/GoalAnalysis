import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.jobs.daily_223_history import build_daily223_from_history
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.api_football_history import (
    ApiFootballHistoryCollector,
    normalize_history,
)
from goal_analysis.providers.base import ProviderError
from goal_analysis.storage import CacheStore, Database

NOW = datetime(2026, 9, 20, 10, tzinfo=UTC)
FETCHED = NOW - timedelta(minutes=1)


def raw_fixture(identifier=1, home=1, away=2, days=1, status="FT", league=78):
    return {
        "fixture": {
            "id": identifier,
            "date": (NOW - timedelta(days=days)).isoformat(),
            "status": {"short": status},
        },
        "league": {"id": league, "season": 2026, "type": "League"},
        "teams": {"home": {"id": home}, "away": {"id": away}},
        "score": {"fulltime": {"home": 2, "away": 1}, "halftime": {"home": 0, "away": 0}},
        "goals": {"home": 9, "away": 8},  # Deliberately not the regular-time score.
    }


def response(rows):
    return {"response": rows, "errors": [], "paging": {"current": 1, "total": 1}}


def seal(snapshot):
    snapshot["snapshot_sha256"] = canonical_sha256(
        {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    )
    return snapshot


def history(count=6):
    rows = []
    for team in range(1, 7):
        for index in range(count):
            home, away = (team, 100 + team) if team % 2 else (100 + team, team)
            rows.append(raw_fixture(team * 100 + index, home, away, index * 3 + 1))
    payload = response(rows)
    return seal(
        {
            "schema_version": 1,
            "kind": "DAILY223_HISTORY",
            "provider": "api_football",
            "collected_at": FETCHED.isoformat(),
            "api_calls": 1,
            "cache_hits": 0,
            "sources": [
                {
                    "league_id": "78",
                    "season": 2026,
                    "fetched_at": FETCHED.isoformat(),
                    "payload_sha256": canonical_sha256(payload),
                    "record_count": len(rows),
                    "from_cache": False,
                }
            ],
            "records": normalize_history(payload, "78", 2026, FETCHED.isoformat()),
        }
    )


def input_payload():
    candidates = []
    for index, price in enumerate((2, 2, 3)):
        candidates.append(
            {
                "candidate_id": f"c{index}",
                "fixture_id": f"canonical-{index}",
                "api_football_fixture_id": str(9000 + index),
                "api_football_home_team_id": str(2 * index + 1),
                "api_football_away_team_id": str(2 * index + 2),
                "api_football_league_id": "78",
                "home_team": f"Test Home {index}",
                "away_team": f"Test Away {index}",
                "competition": "DE1",
                "competition_type": "LEAGUE",
                "kickoff": (NOW + timedelta(hours=6)).isoformat(),
                "event_status": "SCHEDULED",
                "market_key": "totals_2_5",
                "selection_key": "over",
                "period": "FULL_TIME",
                "settlement": "90 minutes plus stoppage; no extra time",
                "bookmaker": "synthetic-book",
                "region": "DE",
                "currency": "EUR",
                "decimal_price": price,
                "quoted_at": NOW.isoformat(),
                "quote_source_id": "synthetic-test-only",
                "quote_available": True,
                "sensitivity_note": "Synthetic data; no actual wager.",
                "evidence": [],
            }
        )
    return {
        "schema_version": 1,
        "date": NOW.date().isoformat(),
        "observed_at": NOW.isoformat(),
        "candidates": candidates,
    }


def collector(tmp_path, rows=None):
    database = Database(tmp_path / "history.sqlite3")
    database.initialize()
    requests = []
    payload = response([raw_fixture()] if rows is None else rows)

    def transport(url, headers):
        requests.append((url, headers))
        return payload, {"x-ratelimit-requests-remaining": "99"}

    client = ApiFootballClient("synthetic-not-a-key", transport=transport)
    clock = [NOW]
    instance = ApiFootballHistoryCollector(client, CacheStore(database), clock=lambda: clock[0])
    return instance, requests, payload, clock


@pytest.mark.parametrize("status", ["FT", "AET", "PEN"])
def test_regular_time_does_not_use_total_goals_or_penalties(status):
    rows = normalize_history(
        response([raw_fixture(status=status)]), "78", 2026, FETCHED.isoformat()
    )
    assert rows[0]["full_time_home"] == 2
    assert rows[0]["full_time_away"] == 1
    assert rows[0]["first_half_home"] == 0


def test_missing_scores_stay_unknown_and_unfinished_games_are_not_history():
    item = raw_fixture()
    item["score"]["halftime"] = None
    rows = normalize_history(
        response([item, raw_fixture(2, status="2H")]), "78", 2026, FETCHED.isoformat()
    )
    assert len(rows) == 1
    assert rows[0]["first_half_home"] is None


@pytest.mark.parametrize("bad", [-1, 1.5, True, "2"])
def test_malformed_scores_are_not_silently_coerced(bad):
    item = raw_fixture()
    item["score"]["fulltime"]["home"] = bad
    with pytest.raises(ProviderError, match="score"):
        normalize_history(response([item]), "78", 2026, FETCHED.isoformat())


def test_duplicate_history_deduplicated_but_conflicts_fail():
    first = raw_fixture()
    assert len(normalize_history(response([first, first]), "78", 2026, FETCHED.isoformat())) == 1
    other = deepcopy(first)
    other["score"]["fulltime"]["home"] = 3
    with pytest.raises(ProviderError, match="conflicting"):
        normalize_history(response([first, other]), "78", 2026, FETCHED.isoformat())


def test_wrong_league_season_pagination_or_future_fixture_fails():
    with pytest.raises(ProviderError, match="mismatch"):
        normalize_history(response([raw_fixture()]), "39", 2026, FETCHED.isoformat())
    with pytest.raises(ProviderError, match="mismatch"):
        normalize_history(response([raw_fixture()]), "78", 2025, FETCHED.isoformat())
    paged = response([])
    paged["paging"]["total"] = 2
    with pytest.raises(ProviderError, match="pagination"):
        normalize_history(paged, "78", 2026, FETCHED.isoformat())
    with pytest.raises(ProviderError, match="observation"):
        normalize_history(response([raw_fixture(days=0)]), "78", 2026, FETCHED.isoformat())


def test_collector_cache_budget_query_and_no_secret_in_output(tmp_path):
    instance, requests, _, _ = collector(tmp_path)
    first = instance.collect([(78, 2026), (78, 2026)], max_calls=1)
    second = instance.collect([(78, 2026)], max_calls=0)
    assert len(requests) == 1
    assert first["api_calls"] == 1
    assert second["api_calls"] == 0 and second["cache_hits"] == 1
    assert first["records"] == second["records"]
    assert parse_qs(urlparse(requests[0][0]).query) == {
        "league": ["78"],
        "season": ["2026"],
        "status": ["FT-AET-PEN"],
        "timezone": ["UTC"],
    }
    assert "synthetic-not-a-key" not in json.dumps(first)


def test_budget_preflight_spends_no_calls(tmp_path):
    instance, requests, _, _ = collector(tmp_path)
    with pytest.raises(ProviderError, match="requires 2"):
        instance.collect([(78, 2025), (78, 2026)], max_calls=1)
    assert not requests


def test_cache_ttl_refresh_and_provider_error_not_cached(tmp_path):
    instance, requests, payload, clock = collector(tmp_path)
    instance.collect([(78, 2026)])
    clock[0] += timedelta(hours=6)
    instance.collect([(78, 2026)])
    assert len(requests) == 2
    payload["errors"] = {"plan": "season unavailable"}
    with pytest.raises(ProviderError, match="plan"):
        instance.collect([(78, 2026)], force_refresh=True)
    # A failed refresh does not replace the previously valid snapshot.
    assert instance.collect([(78, 2026)], max_calls=0)["records"]


def test_no_empty_provider_response_fabrication(tmp_path):
    instance, _, _, _ = collector(tmp_path, rows=[])
    report = instance.collect([(78, 2026)])
    assert report["records"] == []
    assert report["sources"][0]["record_count"] == 0


def test_measured_history_builds_ticket_with_auditable_profiles():
    payload, snapshot = input_payload(), history()
    before = deepcopy(payload)
    report = build_daily223_from_history(payload, snapshot)
    assert payload == before
    assert report["construction_status"] == "COMPLETE"
    assert report["combined_price"] == 12
    assert report["betting_approved"] is False
    assert len(report["history_analysis"]["candidates"]) == 3
    profile = report["history_analysis"]["candidates"][0]["profiles"]["home"]
    assert profile["historical_20"]["sample_size"] == 6
    assert profile["recent_5"]["sample_size"] == 5
    assert profile["venue_10"]["market_hits"] == 6
    assert all(leg["score_components"]["documented_motivation"] == 0 for leg in report["legs"])
    assert report["report_sha256"] == canonical_sha256(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )


@pytest.mark.parametrize(
    ("market", "selection", "period", "complete"),
    [
        ("h2h", "home", "FULL_TIME", True),
        ("h2h", "away", "FULL_TIME", False),
        ("h2h", "draw", "FIRST_HALF", True),
        ("totals_0_5", "under", "FIRST_HALF", True),
        ("totals_0_5", "over", "FIRST_HALF", False),
        ("btts", "yes", "FULL_TIME", True),
        ("btts", "no", "FULL_TIME", False),
    ],
)
def test_selection_team_perspective_and_period_are_respected(market, selection, period, complete):
    payload = input_payload()
    for item in payload["candidates"]:
        item.update(market_key=market, selection_key=selection, period=period)
    report = build_daily223_from_history(payload, history())
    assert (report["construction_status"] == "COMPLETE") is complete


def test_missing_half_time_is_not_replaced_by_full_time():
    payload, snapshot = input_payload(), history()
    for item in payload["candidates"]:
        item.update(period="FIRST_HALF", market_key="totals_0_5", selection_key="under")
    for record in snapshot["records"]:
        record["first_half_home"] = None
        record["first_half_away"] = None
    report = build_daily223_from_history(payload, seal(snapshot))
    assert not report["legs"]
    profile = report["history_analysis"]["candidates"][0]["profiles"]["home"]
    assert profile["venue_10"]["sample_size"] == 0
    assert len(profile["venue_10"]["missing_period_scores"]) == 6
    assert profile["venue_10"]["descriptive_hit_rate"] is None


def test_bad_or_missing_provider_mapping_does_not_guess_by_name():
    payload = input_payload()
    del payload["candidates"][0]["api_football_home_team_id"]
    report = build_daily223_from_history(payload, history())
    assert not report["legs"]
    assert report["history_analysis"]["candidates"][0]["status"] == "DATA_REQUIRED"


def test_unsupported_market_not_substituted():
    payload = input_payload()
    payload["candidates"][0]["market_key"] = "qualification"
    report = build_daily223_from_history(payload, history())
    assert not report["legs"]
    assert "unsupported" in report["history_analysis"]["candidates"][0]["reason"]


def test_insufficient_samples_do_not_gain_fake_evidence():
    payload = input_payload()
    payload["candidates"][0]["evidence"] = [
        {"category": "historical", "strength": 1, "reliability": 1}
    ]
    report = build_daily223_from_history(payload, history(count=2))
    assert not report["legs"]
    assert "historical" in report["history_analysis"]["candidates"][0]["missing_support"]


def test_no_backdating_and_snapshot_tampering_detected():
    payload, snapshot = input_payload(), history()
    snapshot["records"][0]["full_time_home"] = 8
    with pytest.raises(ValueError, match="hash mismatch"):
        build_daily223_from_history(payload, snapshot)
    snapshot = history()
    snapshot["collected_at"] = (NOW + timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="after the decision"):
        build_daily223_from_history(payload, seal(snapshot))


def test_scope_venue_and_optional_coach_cutoff():
    payload, snapshot = input_payload(), history()
    for record in snapshot["records"]:
        if record["home_team_id"] == "1":
            record["home_team_id"], record["away_team_id"] = (
                record["away_team_id"],
                record["home_team_id"],
            )
    report = build_daily223_from_history(payload, seal(snapshot))
    assert not report["legs"]
    p = report["history_analysis"]["candidates"][0]["profiles"]["home"]
    assert p["historical_20"]["sample_size"] == 6
    assert p["venue_10"]["sample_size"] == 0
    payload["candidates"][0]["home_history_since"] = (NOW - timedelta(days=2)).isoformat()
    report = build_daily223_from_history(payload, history())
    assert (
        report["history_analysis"]["candidates"][0]["profiles"]["home"]["historical_20"][
            "sample_size"
        ]
        == 1
    )


def test_workload_counts_other_competitions_without_inventing_fatigue():
    snapshot = history()
    cup = normalize_history(
        response([raw_fixture(8888, home=1, away=9, days=2, league=81)]),
        "81",
        2026,
        FETCHED.isoformat(),
    )[0]
    snapshot["records"].append(cup)
    snapshot["sources"].append(
        {"league_id": "81", "season": 2026, "fetched_at": FETCHED.isoformat()}
    )
    report = build_daily223_from_history(input_payload(), seal(snapshot))
    p = report["history_analysis"]["candidates"][0]["profiles"]["home"]
    assert p["historical_20"]["sample_size"] == 6
    assert p["observed_workload"]["observed_matches_7d"] == 3
    assert p["observed_workload"]["hours_between_kickoffs"] == 30
    assert p["observed_workload"]["actual_rest_hours"] is None
    assert p["observed_workload"]["fatigue_assessment"] is None


def test_history_enrichment_is_idempotent_and_preserves_context_evidence():
    payload = input_payload()
    context = {"category": "documented_motivation", "statement": "Source-based manual context"}
    payload["candidates"][0]["evidence"] = [context]
    enriched = enrich_daily223_input(payload, history())
    assert enrich_daily223_input(enriched, history()) == enriched
    assert enriched["candidates"][0]["evidence"][0] == context


def test_cli_complete_incomplete_and_no_overwrite(tmp_path):
    source, archive, output = [
        tmp_path / name for name in ("input.json", "history.json", "report.json")
    ]
    source.write_text(json.dumps(input_payload()), "utf-8")
    archive.write_text(json.dumps(history()), "utf-8")
    args = [
        sys.executable,
        "scripts/run-daily-223-history.py",
        "--input",
        str(source),
        "--history",
        str(archive),
        "--output",
        str(output),
    ]
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    original = output.read_bytes()
    assert subprocess.run(args, env=env, capture_output=True, check=False).returncode == 1
    assert output.read_bytes() == original
    archive.write_text(json.dumps(history(count=2)), "utf-8")
    args[-1] = str(tmp_path / "incomplete.json")
    assert subprocess.run(args, env=env, capture_output=True, check=False).returncode == 2


def test_recent_five_results_change_the_venue_form_weight():
    snapshot = history(count=20)
    for item in snapshot["records"]:
        if datetime.fromisoformat(item["kickoff"]) >= NOW - timedelta(days=13):
            item["full_time_home"] = 0
            item["full_time_away"] = 0
    report = build_daily223_from_history(input_payload(), seal(snapshot))
    assert report["construction_status"] == "COMPLETE"
    assert report["legs"][0]["score_components"]["historical"] == 22.5
    assert report["legs"][0]["score_components"]["venue_form"] == 7.5


def test_future_cache_entry_cannot_be_replayed_as_earlier_knowledge(tmp_path):
    instance, requests, _, clock = collector(tmp_path)
    instance.collect([(78, 2026)])
    clock[0] -= timedelta(hours=1)
    with pytest.raises(ProviderError, match="budget is 0"):
        instance.collect([(78, 2026)], max_calls=0)
    assert len(requests) == 1


def test_collection_cli_fails_before_network_and_removes_partial_output(tmp_path):
    script = Path("scripts/collect-daily-223-history.py").resolve()
    output = tmp_path / "history.json"
    args = [
        sys.executable,
        str(script),
        "--league-season",
        "78:2026",
        "--max-api-calls",
        "0",
        "--output",
        str(output),
    ]
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    failed = subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert failed.returncode == 1
    assert "budget is 0" in failed.stdout
    assert not output.exists()
    output.write_text("preserved snapshot", "utf-8")
    rerun = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert rerun.returncode == 1
    assert output.read_text("utf-8") == "preserved snapshot"
