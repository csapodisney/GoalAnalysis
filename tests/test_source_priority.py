"""Offline boundaries for evidenced, point-in-time shadow field resolution."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.source_priority import Observation, resolve_field

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
ENTITY = canonical_id("fixture", "verified-test:1")
FRESH = NOW - timedelta(minutes=1)
MAX_AGE = timedelta(minutes=30)


def observation(**changes):
    return Observation.from_value(**{
        "entity_id": ENTITY, "field": "home_score", "provider": "the_stats_api",
        "value": 2, "observed_at": FRESH, "available_at": FRESH,
        "source_url": "https://api.example.test/fixtures/1", "evidence_sha256": "a" * 64,
        "identity_verified": True, **changes,
    })


def resolve(observations, **changes):
    return resolve_field(observations, **{"domain": "sports", "as_of": NOW,
                                         "max_age": MAX_AGE, **changes})


def assert_missing(result):
    assert result["mode"] == "SHADOW"
    assert result["status"] == "MISSING"
    assert result["selected"] is None
    assert isinstance(result["resolution_reason"], str) and result["resolution_reason"]
    assert isinstance(result["rejected"], list)


@pytest.mark.parametrize("domain,providers", [
    ("sports", ("the_stats_api", "api_football", "football_data_co_uk")),
    ("odds", ("the_stats_api", "the_odds_api", "api_football")),
    ("weather", ("open_meteo",)),
    ("news", ("grok",)),
])
def test_every_domain_uses_its_explicit_provider_priority(domain, providers):
    observations = [observation(provider=provider, value=2 + index, quoted_at=FRESH)
                    for index, provider in enumerate(providers)]
    for first in range(len(providers)):
        candidates = observations[first:]
        result = resolve(list(reversed(candidates)), domain=domain)
        assert result["mode"] == "SHADOW"
        assert result["status"] == "RESOLVED"
        assert result["selected"]["provider"] == providers[first]
        assert result["selected"]["value"] == 2 + first


def test_selected_value_retains_required_evidence_and_utc_timestamps():
    result = resolve([observation()])
    selected = result["selected"]
    assert result["status"] == "RESOLVED"
    assert selected["provider"] == "the_stats_api"
    assert selected["source_url"] == "https://api.example.test/fixtures/1"
    assert selected["evidence_sha256"] == "a" * 64
    assert utc(selected["observed_at"]) == FRESH
    assert utc(selected["available_at"]) == FRESH


def test_fresh_primary_wins_over_newer_fallback():
    primary = observation(observed_at=NOW - timedelta(minutes=20), available_at=FRESH, value=1)
    fallback = observation(provider="api_football", observed_at=NOW, available_at=NOW, value=2)
    for candidates in ([primary, fallback], [fallback, primary]):
        assert resolve(candidates)["selected"]["value"] == 1


def test_latest_observation_within_selected_provider_wins():
    old = observation(observed_at=NOW - timedelta(minutes=10), value=1)
    latest = observation(value=2)
    for candidates in ([old, latest], [latest, old]):
        assert resolve(candidates)["selected"]["value"] == 2


def test_newer_receipt_cannot_override_a_newer_quote_from_the_same_provider():
    old_quote_new_receipt = observation(value=2.4, observed_at=NOW, available_at=NOW,
                                        quoted_at=NOW - timedelta(minutes=20))
    new_quote_old_receipt = observation(value=2.1, observed_at=FRESH, available_at=FRESH,
                                        quoted_at=NOW - timedelta(minutes=2))
    for candidates in ([old_quote_new_receipt, new_quote_old_receipt],
                       [new_quote_old_receipt, old_quote_new_receipt]):
        assert resolve(candidates, domain="odds")["selected"]["value"] == 2.1


def test_conflicting_quotes_at_same_quote_time_are_missing_despite_receipt_order():
    first = observation(value=2.1, quoted_at=FRESH)
    second = observation(value=2.4, observed_at=NOW, available_at=NOW, quoted_at=FRESH)
    assert resolve([first, second], domain="odds")["resolution_reason"] == "CONFLICTING_SAME_TIME"


@pytest.mark.parametrize("value", [False, True, 0, 0.0, "", [], {}, {"goals": 0, "flag": False}])
def test_valid_falsy_or_boolean_json_values_are_not_treated_as_missing(value):
    result = resolve([observation(value=value)])
    assert result["status"] == "RESOLVED"
    assert result["selected"]["value"] == value
    assert type(result["selected"]["value"]) is type(value)


@pytest.mark.parametrize("changes", [
    {"value": None},
    {"identity_verified": False},
    {"observed_at": NOW + timedelta(microseconds=1)},
    {"available_at": NOW + timedelta(microseconds=1)},
    {"observed_at": NOW - MAX_AGE - timedelta(microseconds=1)},
    {"quoted_at": NOW + timedelta(microseconds=1)},
    {"quoted_at": NOW - MAX_AGE - timedelta(microseconds=1)},
])
def test_missing_unverified_future_or_stale_primary_uses_eligible_fallback(changes):
    fallback = observation(provider="api_football", value="fallback")
    result = resolve([observation(**changes), fallback])
    assert result["status"] == "RESOLVED"
    assert result["selected"]["provider"] == "api_football"
    assert result["selected"]["value"] == "fallback"
    assert result["rejected"]


@pytest.mark.parametrize("changes", [
    {"value": None}, {"identity_verified": False},
    {"observed_at": NOW + timedelta(seconds=1)},
    {"available_at": NOW + timedelta(seconds=1)},
    {"observed_at": NOW - MAX_AGE - timedelta(seconds=1)},
])
def test_no_eligible_source_returns_explicit_missing(changes):
    assert_missing(resolve([observation(**changes)]))


def test_empty_input_is_missing():
    assert_missing(resolve([]))


def test_observation_and_availability_at_cutoff_are_visible():
    result = resolve([observation(observed_at=NOW, available_at=NOW)])
    assert result["status"] == "RESOLVED"


def test_observation_at_exact_maximum_age_remains_fresh():
    result = resolve([observation(observed_at=NOW - MAX_AGE,
                                  available_at=NOW - MAX_AGE)])
    assert result["status"] == "RESOLVED"


def test_fresh_receipt_alone_is_not_a_quote_timestamp():
    assert_missing(resolve([observation(quoted_at=None)], domain="odds"))


def test_stale_quote_cannot_be_refreshed_by_a_new_observation_or_receipt():
    primary = observation(observed_at=NOW, available_at=NOW,
                          quoted_at=NOW - MAX_AGE - timedelta(microseconds=1))
    fallback = observation(provider="the_odds_api", value=3.2, quoted_at=FRESH)
    result = resolve([primary, fallback], domain="odds")
    assert result["selected"]["provider"] == "the_odds_api"


@pytest.mark.parametrize("quoted_at", [NOW + timedelta(microseconds=1), None])
def test_future_or_absent_quote_is_missing_even_with_fresh_receipt(quoted_at):
    assert_missing(resolve([observation(observed_at=NOW, available_at=NOW,
                                        quoted_at=quoted_at)], domain="odds"))


def test_quote_at_exact_maximum_age_remains_eligible():
    result = resolve([observation(quoted_at=NOW - MAX_AGE)], domain="odds")
    assert result["status"] == "RESOLVED"


@pytest.mark.parametrize("changes", [
    {"observed_at": NOW - timedelta(minutes=10), "available_at": FRESH},
    {"observed_at": FRESH, "available_at": NOW - timedelta(minutes=10)},
])
def test_quote_cannot_postdate_observation_or_receipt_even_before_cutoff(changes):
    primary = observation(quoted_at=NOW - timedelta(minutes=5), **changes)
    fallback = observation(provider="api_football", value="fallback")
    result = resolve([primary, fallback])
    assert result["status"] == "RESOLVED"
    assert result["selected"]["provider"] == "api_football"
    assert result["rejected"]


@pytest.mark.parametrize("value", [0, 1, True, False, "2.5", {}, []])
def test_odds_requires_a_numeric_decimal_price_strictly_above_one(value):
    assert_missing(resolve([observation(value=value, quoted_at=FRESH)], domain="odds"))


def test_fresh_quote_cannot_refresh_stale_observation():
    assert_missing(resolve([observation(observed_at=NOW - MAX_AGE - timedelta(seconds=1),
                                        quoted_at=FRESH)], domain="odds"))


@pytest.mark.parametrize("domain,outsider", [("sports", "the_odds_api"),
                                             ("odds", "football_data_co_uk"),
                                             ("weather", "grok"),
                                             ("news", "open_meteo")])
def test_provider_from_another_domain_cannot_fill_a_field(domain, outsider):
    assert_missing(resolve([observation(provider=outsider, quoted_at=FRESH)], domain=domain))


@pytest.mark.parametrize("left,right", [(1, 2), ({"a": 1}, {"a": 2}), (0, False)])
def test_conflicting_values_at_newest_same_provider_time_are_not_arbitrarily_selected(left, right):
    candidates = [observation(value=left), observation(value=right),
                  observation(provider="api_football", value="fallback")]
    for rows in (candidates, list(reversed(candidates))):
        result = resolve(rows)
        assert_missing(result)
        assert result["resolution_reason"] == "CONFLICTING_SAME_TIME"


def test_equal_json_objects_with_different_key_order_do_not_create_conflict():
    result = resolve([observation(value={"a": 1, "b": [2]}),
                      observation(value={"b": [2], "a": 1}, evidence_sha256="b" * 64)])
    assert result["status"] == "RESOLVED"
    assert result["selected"]["value"] == {"a": 1, "b": [2]}


def test_old_same_time_conflict_does_not_override_a_newer_unique_value():
    older = NOW - timedelta(minutes=10)
    result = resolve([observation(value=1, observed_at=older),
                      observation(value=2, observed_at=older), observation(value=3)])
    assert result["status"] == "RESOLVED"
    assert result["selected"]["value"] == 3


@pytest.mark.parametrize("changes", [{"entity_id": canonical_id("fixture", "verified-test:2")},
                                    {"field": "away_score"}])
def test_mixed_entity_or_field_input_raises_instead_of_joining_scopes(changes):
    with pytest.raises(ValueError):
        resolve([observation(), observation(**changes)])


def test_values_are_defensively_copied_on_input_and_output():
    source = {"players": [{"id": "pl_1", "available": False}], "scores": [0, 1]}
    original = deepcopy(source)
    observed = observation(value=source)
    source["players"][0]["available"] = True
    source["scores"].append(9)
    result = resolve([observed])
    assert result["selected"]["value"] == original
    result["selected"]["value"]["players"][0]["id"] = "annotation"
    result["selected"]["value"]["scores"].append(99)
    assert resolve([observed])["selected"]["value"] == original


def test_observation_metadata_is_immutable_and_timestamps_are_normalized_to_utc():
    offset = timezone(timedelta(hours=2))
    stamp = FRESH.astimezone(offset)
    observed = observation(observed_at=stamp, available_at=stamp, quoted_at=stamp)
    for name in ("observed_at", "available_at", "quoted_at"):
        assert getattr(observed, name) == FRESH
        assert getattr(observed, name).tzinfo is UTC
    with pytest.raises(AttributeError):
        observed.provider = "api_football"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                  {"nested": [float("nan")]},
                                  {"nested": {"bad": float("inf")}},
                                  object(), b"bytes", {1, 2}])
def test_nonfinite_or_non_json_values_are_rejected(value):
    with pytest.raises(ValueError):
        observation(value=value)


@pytest.mark.parametrize("field", ["observed_at", "available_at", "quoted_at"])
@pytest.mark.parametrize("bad", [NOW.replace(tzinfo=None), "2026-10-20T12:00:00",
                                "not-a-timestamp", True])
def test_observation_timestamp_metadata_requires_an_explicit_timezone(field, bad):
    with pytest.raises(ValueError):
        observation(**{field: bad})


@pytest.mark.parametrize("changes", [
    {"entity_id": "mt_1"}, {"entity_id": None},
    {"field": ""}, {"provider": ""},
    {"source_url": ""}, {"source_url": None},
    {"evidence_sha256": ""}, {"evidence_sha256": "a" * 63},
    {"evidence_sha256": "z" * 64}, {"evidence_sha256": 1},
    {"identity_verified": "true"}, {"identity_verified": 1},
])
def test_malformed_identity_or_evidence_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        observation(**changes)


@pytest.mark.parametrize("mode", ["LIVE", "live", "shadow", "", None])
def test_only_shadow_mode_is_authorized(mode):
    with pytest.raises(ValueError):
        resolve([observation()], mode=mode)


@pytest.mark.parametrize("bad", [NOW.replace(tzinfo=None), "2026-10-20T12:00:00", None])
def test_as_of_requires_an_explicit_timezone(bad):
    with pytest.raises(ValueError):
        resolve([observation()], as_of=bad)


def test_unknown_domain_is_rejected():
    with pytest.raises(ValueError):
        resolve([observation()], domain="unknown")


@pytest.mark.parametrize("max_age", [timedelta(0), -timedelta(seconds=1), 30, None])
def test_freshness_window_requires_a_positive_duration(max_age):
    with pytest.raises(ValueError):
        resolve([observation()], max_age=max_age)
