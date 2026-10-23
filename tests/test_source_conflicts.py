"""Preserve source evidence and make shadow conflict diagnostics deterministic."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations

import pytest

from goal_analysis.jobs.source_resolution import build_source_resolution
from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.source_priority import Observation, resolve_field

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
ENTITY_ID = canonical_id("fixture", "source-conflict-test:1")
FRESH = NOW - timedelta(minutes=1)


def observation(**changes):
    return Observation.from_value(**{
        "entity_id": ENTITY_ID, "field": "home_score", "provider": "the_stats_api",
        "value": 2, "observed_at": FRESH, "available_at": FRESH,
        "source_url": "https://api.example.test/fixtures/1", "evidence_sha256": "a" * 64,
        "identity_verified": True, **changes,
    })


def resolve(observations, **changes):
    return resolve_field(observations, **{"domain": "sports", "as_of": NOW,
                                         "max_age": timedelta(minutes=30), **changes})


def observation_id(item):
    return item.to_record()["observation_id"]


def freshness_by_id(result):
    return {item["observation_id"]: item for item in result["freshness_order"]}


def test_observation_id_hashes_the_complete_canonical_record_without_self_reference():
    item = observation(value={"nested": [0, False, "caf\u00e9"]})
    record = item.to_record()
    identity = record.pop("observation_id")
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode()
    assert identity == hashlib.sha256(encoded).hexdigest()
    assert item.to_record()["observation_id"] == identity


@pytest.mark.parametrize("changes", [
    {"value": 3}, {"provider": "api_football"}, {"field": "away_score"},
    {"entity_id": canonical_id("fixture", "source-conflict-test:2")},
    {"observed_at": NOW - timedelta(minutes=2)},
    {"available_at": NOW}, {"quoted_at": NOW - timedelta(minutes=2)},
    {"source_url": "https://second.example.test/fixtures/1"},
    {"evidence_sha256": "b" * 64}, {"identity_verified": False},
])
def test_each_identity_value_time_and_provenance_component_changes_observation_id(changes):
    assert observation_id(observation(**changes)) != observation_id(observation())


def test_equivalent_json_key_order_and_timezone_have_the_same_observation_id():
    offset = timezone(timedelta(hours=2))
    first = observation(value={"a": 1, "b": [2]}, quoted_at=FRESH)
    second = observation(value={"b": [2], "a": 1}, observed_at=FRESH.astimezone(offset),
                         available_at=FRESH.astimezone(offset), quoted_at=FRESH.astimezone(offset))
    assert observation_id(first) == observation_id(second)


def test_returned_record_mutation_cannot_change_observation_id_or_preserved_value():
    item = observation(value={"players": [{"id": "pl_1"}]})
    original = item.to_record()
    changed = item.to_record()
    changed["value"]["players"][0]["id"] = "annotation"
    changed["source_url"] = "https://annotation.example.test"
    assert item.to_record() == original


def test_freshness_metadata_preserves_all_observations_without_changing_primary_choice():
    primary = observation(value=1, observed_at=NOW - timedelta(minutes=20),
                          available_at=NOW - timedelta(minutes=20))
    fallback = observation(provider="api_football", value=2)
    stale = observation(provider="football_data_co_uk", value=9,
                        observed_at=NOW - timedelta(hours=1), available_at=NOW - timedelta(hours=1))
    result = resolve([stale, primary, fallback])
    assert result["status"] == "RESOLVED"
    assert result["resolution_reason"] == "PRIMARY"
    assert result["selected"]["provider"] == "the_stats_api"
    assert [row["observation_id"] for row in result["freshness_order"]] == [
        observation_id(fallback), observation_id(primary), observation_id(stale)]
    metadata = freshness_by_id(result)
    required = {"observation_id", "provider", "effective_at", "observed_at", "available_at",
                "age_seconds", "eligible", "rejection_reason", "source_priority",
                "freshness_rank", "selected"}
    assert all(required <= set(row) for row in metadata.values())
    assert metadata[observation_id(primary)]["selected"] is True
    assert metadata[observation_id(fallback)]["selected"] is False
    assert metadata[observation_id(primary)]["eligible"] is True
    assert metadata[observation_id(stale)]["eligible"] is False
    assert metadata[observation_id(stale)]["rejection_reason"] == "STALE_OBSERVATION"
    assert metadata[observation_id(primary)]["age_seconds"] == 1200
    assert metadata[observation_id(primary)]["source_priority"] == 1
    assert metadata[observation_id(fallback)]["source_priority"] == 2
    assert metadata[observation_id(stale)]["source_priority"] == 3
    assert [row["freshness_rank"] for row in result["freshness_order"]] == [1, 2, 3]
    assert utc(metadata[observation_id(primary)]["effective_at"]) == primary.observed_at
    assert utc(metadata[observation_id(primary)]["available_at"]) == primary.available_at


def test_freshness_order_is_deterministic_across_input_permutations_including_ties():
    items = [observation(value=2), observation(provider="api_football", value=2),
             observation(evidence_sha256="b" * 64, source_url="https://second.example.test/1"),
             observation(provider="football_data_co_uk", observed_at=NOW - timedelta(minutes=5))]
    baseline = resolve(items)
    for ordering in permutations(items):
        result = resolve(ordering)
        assert result["freshness_order"] == baseline["freshness_order"]
        assert result["conflict"] == baseline["conflict"]
        assert result["selected"] == baseline["selected"]
        assert result["resolution_reason"] == baseline["resolution_reason"]


def test_equal_odds_quote_and_receipt_ties_choose_a_stable_observation_identity():
    earlier_observed = observation(value=2.1, quoted_at=NOW - timedelta(minutes=5),
                                   observed_at=NOW - timedelta(minutes=2), available_at=NOW)
    later_observed = observation(value=2.1, quoted_at=NOW - timedelta(minutes=5),
                                 observed_at=FRESH, available_at=NOW)
    forward = resolve([earlier_observed, later_observed], domain="odds")
    reverse = resolve([later_observed, earlier_observed], domain="odds")
    assert forward["selected"]["value"] == reverse["selected"]["value"] == 2.1
    assert forward["resolution_reason"] == reverse["resolution_reason"] == "PRIMARY"
    assert forward["freshness_order"] == reverse["freshness_order"]
    assert forward["selected"]["observation_id"] == reverse["selected"]["observation_id"]


def test_odds_freshness_uses_quote_time_and_puts_missing_quote_time_last():
    newer_quote = observation(value=2.1, quoted_at=NOW - timedelta(minutes=2),
                              observed_at=FRESH, available_at=FRESH)
    newer_receipt = observation(value=2.5, quoted_at=NOW - timedelta(minutes=20),
                                observed_at=NOW, available_at=NOW, evidence_sha256="b" * 64)
    missing_quote = observation(value=2.9, observed_at=NOW, available_at=NOW,
                                evidence_sha256="c" * 64)
    result = resolve([missing_quote, newer_receipt, newer_quote], domain="odds")
    ordered = result["freshness_order"]
    assert [row["observation_id"] for row in ordered] == [observation_id(newer_quote),
                                                         observation_id(newer_receipt),
                                                         observation_id(missing_quote)]
    assert utc(ordered[0]["effective_at"]) == newer_quote.quoted_at
    assert ordered[0]["age_seconds"] == 120
    assert ordered[-1]["effective_at"] is None
    assert ordered[-1]["age_seconds"] is None
    assert ordered[-1]["eligible"] is False
    assert ordered[-1]["rejection_reason"] == "MISSING_QUOTE_TIME"
    assert result["selected"]["value"] == 2.1
    assert result["resolution_reason"] == "PRIMARY"
    assert result["conflict"]["status"] == "SINGLE_SOURCE"
    assert result["conflict"]["distinct_values"] == 1


@pytest.mark.parametrize("changes", [
    {"identity_verified": False}, {"value": None},
    {"observed_at": NOW + timedelta(seconds=1), "available_at": NOW + timedelta(seconds=1)},
    {"available_at": NOW + timedelta(seconds=1)},
    {"provider": "open_meteo"},
    {"observed_at": NOW - timedelta(hours=1), "available_at": NOW - timedelta(hours=1)},
])
def test_ineligible_values_remain_in_freshness_audit_but_do_not_create_conflict(changes):
    good = observation()
    bad = observation(**{"value": "different", **changes})
    result = resolve([bad, good])
    metadata = freshness_by_id(result)
    assert len(metadata) == 2
    assert metadata[observation_id(bad)]["eligible"] is False
    assert metadata[observation_id(bad)]["rejection_reason"]
    assert metadata[observation_id(bad)]["selected"] is False
    if bad.provider == "open_meteo":
        assert metadata[observation_id(bad)]["source_priority"] is None
    assert result["conflict"]["status"] == "SINGLE_SOURCE"
    assert result["conflict"]["observation_ids"] == [observation_id(good)]


def test_empty_or_entirely_rejected_input_has_no_eligible_conflict_sources():
    for rows in ([], [observation(value=None)]):
        result = resolve(rows)
        assert result["status"] == "MISSING" and result["selected"] is None
        assert result["conflict"]["status"] == "NO_ELIGIBLE_SOURCE"
        assert result["conflict"]["provider_count"] == 0
        assert result["conflict"]["distinct_values"] == 0
        assert result["conflict"]["observation_ids"] == []


def test_equal_latest_values_from_multiple_providers_are_agreement():
    items = [observation(value={"a": 1, "b": False}),
             observation(provider="api_football", value={"b": False, "a": 1})]
    result = resolve(items)
    assert result["conflict"]["status"] == "AGREEMENT"
    assert result["conflict"]["provider_count"] == 2
    assert result["conflict"]["distinct_values"] == 1
    assert set(result["conflict"]["observation_ids"]) == {observation_id(item) for item in items}
    assert result["selected"]["provider"] == "the_stats_api"


@pytest.mark.parametrize("primary_value,fallback_value", [(1, 2), (0, False)])
def test_cross_provider_conflict_does_not_change_the_existing_primary_decision(primary_value,
                                                                              fallback_value):
    primary = observation(value=primary_value)
    fallback = observation(provider="api_football", value=fallback_value,
                           observed_at=NOW, available_at=NOW)
    result = resolve([primary, fallback])
    assert result["conflict"]["status"] == "CONFLICT"
    assert result["conflict"]["provider_count"] == 2
    assert result["conflict"]["distinct_values"] == 2
    assert result["status"] == "RESOLVED"
    assert result["selected"]["value"] == primary_value
    assert type(result["selected"]["value"]) is type(primary_value)
    assert result["resolution_reason"] == "PRIMARY"


def test_old_differing_revisions_do_not_create_a_false_current_conflict():
    old = observation(value=1, observed_at=NOW - timedelta(minutes=10),
                      available_at=NOW - timedelta(minutes=10))
    latest = observation(value=2)
    fallback = observation(provider="api_football", value=2)
    result = resolve([old, latest, fallback])
    assert result["conflict"]["status"] == "AGREEMENT"
    assert set(result["conflict"]["observation_ids"]) == {observation_id(latest),
                                                         observation_id(fallback)}
    assert freshness_by_id(result)[observation_id(old)]["eligible"] is True
    assert result["selected"]["value"] == 2


def test_same_provider_conflict_at_latest_actual_time_remains_unresolved():
    first = observation(value=2)
    second = observation(value=3, evidence_sha256="b" * 64)
    old = observation(value=1, observed_at=NOW - timedelta(minutes=10))
    result = resolve([old, first, second])
    assert result["conflict"]["status"] == "CONFLICT"
    assert result["conflict"]["provider_count"] == 1
    assert result["conflict"]["distinct_values"] == 2
    assert set(result["conflict"]["observation_ids"]) == {observation_id(first), observation_id(second)}
    assert result["status"] == "MISSING" and result["selected"] is None
    assert result["resolution_reason"] == "CONFLICTING_SAME_TIME"
    assert all(not row["selected"] for row in result["freshness_order"])


def test_odds_conflict_uses_latest_quote_per_provider_instead_of_latest_receipt():
    primary = observation(value=2.2, quoted_at=NOW - timedelta(minutes=2))
    old_quote = observation(provider="the_odds_api", value=3.5,
                            quoted_at=NOW - timedelta(minutes=20), observed_at=NOW,
                            available_at=NOW)
    new_quote = observation(provider="the_odds_api", value=2.2,
                            quoted_at=NOW - timedelta(minutes=2), evidence_sha256="b" * 64)
    result = resolve([old_quote, primary, new_quote], domain="odds")
    assert result["conflict"]["status"] == "AGREEMENT"
    assert set(result["conflict"]["observation_ids"]) == {observation_id(primary),
                                                         observation_id(new_quote)}
    assert result["selected"]["value"] == 2.2


def audit_candidates(*, same_time_conflict):
    fixture = {"fixture_id": "api_football:1", "kickoff": (NOW + timedelta(hours=5)).isoformat()}
    quote = {"fixture_id": fixture["fixture_id"], "quote_provider": "the_odds_api",
             "bookmaker": "test", "market_key": "h2h", "selection_key": "home",
             "period": "FULL_TIME", "settlement": "REGULATION_TIME", "region": "DE",
             "currency": "EUR", "quote_available": True, "decimal_price": 2.1,
             "quoted_at": FRESH.isoformat(),
             "quote_source_id": "https://api.the-odds-api.com/v4/sports/soccer_epl/events/test/odds"}
    second = {**quote, "decimal_price": 2.3,
              "quoted_at": (FRESH if same_time_conflict else FRESH - timedelta(minutes=1)).isoformat()}
    future = {**quote, "decimal_price": 4.5, "quoted_at": (NOW + timedelta(seconds=1)).isoformat()}
    return {"fixtures": [fixture]}, {"candidate_input": {"observed_at": NOW.isoformat(),
                                                           "candidates": [quote, second, future]}}


@pytest.mark.parametrize("same_time_conflict", [True, False])
def test_pipeline_preserves_all_source_values_and_summarizes_conflicts(same_time_conflict):
    report, artifacts = audit_candidates(same_time_conflict=same_time_conflict)
    before_report, before_artifacts = deepcopy(report), deepcopy(artifacts)
    result = build_source_resolution(report, artifacts, as_of=NOW)
    odds = [item for item in result["fields"] if item["domain"] == "odds"]
    assert len(odds) == 1
    field = odds[0]
    assert [record["value"] for record in field["observations"]] == [2.1, 2.3, 4.5]
    identities = {record["observation_id"] for record in field["observations"]}
    assert len(identities) == 3
    assert {row["observation_id"] for row in field["resolution"]["freshness_order"]} == identities
    expected_count = 1 if same_time_conflict else 0
    assert result["summary"]["conflict_fields"] == expected_count
    assert result["summary"]["unresolved_conflict_fields"] == expected_count
    if same_time_conflict:
        assert field["resolution"]["selected"] is None
        assert field["resolution"]["resolution_reason"] == "CONFLICTING_SAME_TIME"
    else:
        assert field["resolution"]["selected"]["value"] == 2.1
    assert report == before_report and artifacts == before_artifacts
    assert result["summary"]["mode"] == "SHADOW"
    assert result["summary"]["external_requests"] == 0
