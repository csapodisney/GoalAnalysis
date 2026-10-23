"""Freeze the eligible fixture universe before candidate-specific review can move it."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_player_collection_shadow import setup
from test_player_specialist_shadow import NOW

from goal_analysis.jobs import candidate_context_review as job


def _environment(root, monkeypatch):
    """Keep the real archived-player reader; replace only new acquisitions."""
    from goal_analysis.jobs import (
        calendar_collection_shadow,
        expert_advisory,
        player_collection_shadow,
        rotation_shadow,
        schedule_shadow,
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("No live request or second optional collection is allowed")

    monkeypatch.setattr("socket.socket", forbidden)
    data, _models, synthetic_calls, _factory = setup(root, monkeypatch)
    collections = []

    def collected(_root, report, **kwargs):
        assert kwargs.get("max_requests", 110) == 110
        assert kwargs["context_shadow"] is True
        collections.append(deepcopy(report))
        return {"context_collection_shadow": {"collection": {
            "version": "thestatsapi-context-v1",
            "provider": "the_stats_api",
            "observed_at": NOW.isoformat(),
            "context_by_fixture": {},
            "usage": {"requests": 0, "max_requests": 110},
        }}}

    monkeypatch.setattr(expert_advisory, "enrich_sports_supplement", collected)
    for module in (player_collection_shadow, calendar_collection_shadow):
        monkeypatch.setattr(module, "enabled", lambda _root: False)
        monkeypatch.setattr(module, "collect", forbidden)
    monkeypatch.setattr(schedule_shadow, "inspect_schedule", lambda *_a, **_k: {"features": []})
    monkeypatch.setattr(rotation_shadow, "inspect_rotation", lambda *_a, **_k: {"features": []})
    return data, collections, synthetic_calls


def _candidate(data, fixture):
    return deepcopy(next(row for row in data["candidates"]
                         if row["fixture_id"] == fixture["fixture_id"]))


def test_follow_up_reuses_frozen_sources_outside_initial_review_without_new_collection(
    tmp_path, monkeypatch,
):
    data, collections, synthetic_calls = _environment(tmp_path, monkeypatch)
    first, second, unused = data["fixtures"]
    first_id, second_id = first["fixture_id"], second["fixture_id"]
    weather = {
        "fixture_id": first_id,
        "status": "SOURCED",
        "weather_kind": "FORECAST",
        "observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        "source_url": "https://api.open-meteo.com/v1/forecast?latitude=51&longitude=0",
        "temperature_c": 9.0,
    }
    initial = {**_candidate(data, first), "weather_forecast": deepcopy(weather)}
    packets = [initial, {**deepcopy(initial), "candidate_id": "initial-other-quote"}]
    follow_up = _candidate(data, second)
    eligible = [follow_up, {**deepcopy(follow_up), "candidate_id": "secondary-other-quote"}]
    # Repeated quotes and fixture records must not consume repeated collection slots.
    fixtures = [first, second, deepcopy(second), unused]
    before = deepcopy((fixtures, packets, eligible))

    prepared = job.prepare(tmp_path, fixtures, packets, run_id="scope-regression",
                           clock=lambda: NOW, eligible_candidates=eligible)

    assert len(collections) == 1 and not synthetic_calls
    collected_ids = [row["fixture_id"] for row in collections[0]["fixtures"]]
    assert set(collected_ids) == {first_id, second_id} and len(collected_ids) == 2
    assert set(prepared["contexts"]) == {first_id, second_id}
    assert prepared["initial_review_fixture_ids"] == [first_id]
    scope = {row["fixture_id"]: row for row in prepared["scope_packets"]}
    assert len(prepared["scope_packets"]) == len(scope) == 2
    assert scope[first_id] == {"fixture_id": first_id, "weather_forecast": weather}
    assert scope[second_id] == {"fixture_id": second_id}
    assert prepared["as_of"] == NOW.isoformat()

    rows = {row["category"]: row for row in prepared["contexts"][second_id]["categories"]}
    assert rows["players"]["status"] == "SOURCED"
    assert rows["players"]["source_urls"]
    assert all(row["source_sha256"] and row["available_at"]
               for row in rows["players"]["data"])
    assert rows["weather"]["status"] == "UNKNOWN"
    initial_rows = {row["category"]: row
                    for row in prepared["contexts"][first_id]["categories"]}
    assert initial_rows["weather"]["data"] == weather

    frozen_before_follow_up = deepcopy(prepared)
    attached = job.attach([follow_up], prepared)[0]
    assert attached["decision_context"] == prepared["contexts"][second_id]
    assert rows["players"]["evidence_id"] in attached["evidence_ids"]
    assert prepared == frozen_before_follow_up
    assert len(collections) == 1 and not synthetic_calls
    reconstructed = job.compose(tmp_path, fixtures, prepared["scope_packets"],
                                prepared["collection"], as_of=prepared["as_of"])
    assert reconstructed["contexts"] == prepared["contexts"]
    assert (fixtures, packets, eligible) == before


@pytest.mark.parametrize("excluded", [
    "fixture_at_lockout", "expired_fixture", "invalid_fixture_kickoff",
    "unknown_fixture", "unpriced_without_candidate",
])
def test_scope_excludes_ineligible_or_unrepresented_fixtures(tmp_path, monkeypatch, excluded):
    data, collections, synthetic_calls = _environment(tmp_path, monkeypatch)
    first, second, _unused = data["fixtures"]
    packets = [_candidate(data, first)]
    eligible = [_candidate(data, second)]
    if excluded == "fixture_at_lockout":
        second["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    elif excluded == "expired_fixture":
        second["kickoff"] = (NOW - timedelta(minutes=1)).isoformat()
    elif excluded == "invalid_fixture_kickoff":
        second["kickoff"] = "unknown"
    elif excluded == "unknown_fixture":
        eligible[0]["fixture_id"] = "the_stats_api:mt_99999"
    else:
        eligible = []
    before = deepcopy((data["fixtures"], packets, eligible))

    prepared = job.prepare(tmp_path, data["fixtures"], packets, run_id="scope-exclusion",
                           clock=lambda: NOW, eligible_candidates=eligible)

    assert len(collections) == 1 and not synthetic_calls
    assert [row["fixture_id"] for row in collections[0]["fixtures"]] == [first["fixture_id"]]
    assert set(prepared["contexts"]) == {first["fixture_id"]}
    assert prepared["initial_review_fixture_ids"] == [first["fixture_id"]]
    assert [row["fixture_id"] for row in prepared["scope_packets"]] == [first["fixture_id"]]
    assert (data["fixtures"], packets, eligible) == before


def test_scope_uses_authoritative_fixture_window_instead_of_candidate_copy(tmp_path, monkeypatch):
    data, collections, synthetic_calls = _environment(tmp_path, monkeypatch)
    first, second, _unused = data["fixtures"]
    candidate = _candidate(data, second)
    candidate["kickoff"] = (NOW - timedelta(minutes=1)).isoformat()
    before = deepcopy(candidate)
    prepared = job.prepare(tmp_path, data["fixtures"], [_candidate(data, first)],
                           run_id="scope-fixture-clock", clock=lambda: NOW,
                           eligible_candidates=[candidate])
    assert set(prepared["contexts"]) == {first["fixture_id"], second["fixture_id"]}
    assert len(collections) == 1 and not synthetic_calls
    assert candidate == before


def test_omitting_eligible_candidates_keeps_existing_initial_review_scope(tmp_path, monkeypatch):
    data, collections, synthetic_calls = _environment(tmp_path, monkeypatch)
    initial = _candidate(data, data["fixtures"][0])
    prepared = job.prepare(tmp_path, data["fixtures"], [initial],
                           run_id="scope-default", clock=lambda: NOW)
    assert set(prepared["contexts"]) == {initial["fixture_id"]}
    assert len(collections) == 1 and len(collections[0]["fixtures"]) == 1
    assert not synthetic_calls
