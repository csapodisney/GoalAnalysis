"""Retain witnessed season facts without refreshing their clocks or other caches."""

import hashlib
import json
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.the_stats_context import (
    TEAM_SEASON_STATS_TTL,
    VERSION,
    Resources,
    _collect_fact,
)
from goal_analysis.providers.the_stats_api import TheStatsApiClient

NOW = datetime(2026, 10, 23, 19, 43, tzinfo=UTC)
ENDPOINT = "/football/teams/tm_15641/stats"
PARAMS = {"season_id": "sn_0835912"}
KEY = json.dumps([ENDPOINT, PARAMS], sort_keys=True, separators=(",", ":"))
MAPPING = {
    "match_id": "mt_733746321",
    "competition_id": "comp_4643",
    "season_id": "sn_0835912",
    "home_team_id": "tm_15641",
    "away_team_id": "tm_37885",
}
DATA = {
    "team_id": "tm_15641",
    "season_id": "sn_0835912",
    "competition_id": "comp_4643",
    "matches_played": 6,
    "expected_goals_for": 12.96,
    "expected_goals_against": 19.22,
    "xg_matches": 6,
}
TABLES = (
    "cache_entries",
    "raw_snapshots",
    "the_stats_http_observations",
    "the_stats_http_witnesses",
    "temporal_receipts",
)


class WireTransport:
    def __init__(self, data):
        self.raw = json.dumps({"data": data}).encode()
        self.calls = []

    def __call__(self, url, headers, **kwargs):
        self.calls.append(url)
        return self.raw, {}, 200


def seed(root, *, data=None, endpoint=ENDPOINT, params=None):
    current = [NOW]
    wire = WireTransport(DATA if data is None else data)
    client = TheStatsApiClient("safe-test-key", wire, clock=lambda: current[0])
    resource = Resources(root, client, lambda: current[0], 1)
    original = resource.get(endpoint, PARAMS if params is None else params)
    assert original[2] is None
    assert original[1]["http_observation_status"] == "VERIFIED"
    return current, wire, client, resource, original


def persistent_state(resource):
    with closing(resource.database.connect()) as db:
        rows = {table: sorted(tuple(row) for row in db.execute("SELECT * FROM " + table))
                for table in TABLES}
    files = {str(path.relative_to(resource.root)): path.read_bytes()
             for path in (resource.root / "data/raw").rglob("*.bin")}
    return rows, files


def retained(resource, *, cache_only=False):
    return resource.get(ENDPOINT, PARAMS, ttl=TEAM_SEASON_STATS_TTL,
                        retained_team_stats=True, cache_only=cache_only)


def alter_source(resource, transform, *, rehash=True):
    with resource.database.transaction() as db:
        value = json.loads(db.execute(
            "SELECT value_json FROM cache_entries WHERE namespace=? AND cache_key=?",
            (VERSION, KEY),
        ).fetchone()[0])
        transform(value["source"])
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if rehash:
            db.execute("UPDATE cache_entries SET value_json=?,content_hash=?",
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        else:
            db.execute("UPDATE cache_entries SET value_json=?", (encoded,))


def fixture_record():
    return {"fixture_id": "the_stats_api:mt_733746321", "mapping": deepcopy(MAPPING),
            "kickoff": (NOW + timedelta(days=1)).isoformat(), "status": "MATCHED",
            "facts": [], "missing": []}


def test_old_30_minute_entry_reused_at_89_minutes_without_http_or_persistent_changes(tmp_path):
    current, wire, client, initial, original = seed(tmp_path)
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    assert retained(resource, cache_only=True) == original
    assert retained(resource) == original
    assert original[1]["observed_at"] == NOW.isoformat()
    assert original[1]["published_at"] is None and "quoted_at" not in original[1]
    assert resource.usage["requests"] == 0 and resource.usage["cache_hits"] == 1
    assert len(wire.calls) == 1
    assert persistent_state(resource) == baseline


@pytest.mark.parametrize("age,accepted", [
    (timedelta(minutes=30), True),
    (timedelta(hours=6) - timedelta(microseconds=1), True),
    (timedelta(hours=6), False),
    (timedelta(hours=6, microseconds=1), False),
    (timedelta(seconds=-1), False),
])
def test_original_observation_age_controls_strict_retained_boundary(tmp_path, age, accepted):
    current, wire, client, initial, original = seed(tmp_path)
    baseline = persistent_state(initial)
    current[0] = NOW + age
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    result = retained(resource, cache_only=True)
    assert result == (original if accepted else (None, None, "CACHE_MISS"))
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


@pytest.mark.parametrize("fault", [
    "cache_hash", "raw_bytes", "raw_metadata", "source_hash", "source_url",
    "provider", "observation_id", "receipt_id", "missing_observation_id",
    "missing_receipt_id", "missing_witness", "future_witness", "future_observed",
    "future_created", "shifted_created", "reversed_expiry",
])
def test_expired_entry_requires_original_unmodified_witness_and_metadata(tmp_path, fault):
    current, wire, client, initial, original = seed(tmp_path)
    current[0] += timedelta(minutes=89)
    source = original[1]
    fields = {
        "source_hash": ("content_hash", "f" * 64),
        "source_url": ("source_url", source["source_url"].replace("tm_15641", "tm_37885")),
        "provider": ("provider", "api_football"),
        "observation_id": ("http_observation_id", "f" * 32),
        "receipt_id": ("http_receipt_id", "f" * 64),
        "future_observed": ("observed_at", (current[0] + timedelta(seconds=1)).isoformat()),
    }
    if fault in fields:
        field, value = fields[fault]
        alter_source(initial, lambda item: item.update({field: value}))
    elif fault in {"missing_observation_id", "missing_receipt_id"}:
        field = "http_observation_id" if fault == "missing_observation_id" else "http_receipt_id"
        alter_source(initial, lambda item: item.pop(field))
    elif fault == "cache_hash":
        alter_source(initial, lambda item: item.update(resource_type="tampered"), rehash=False)
    elif fault == "raw_bytes":
        path = next((tmp_path / "data/raw").rglob(source["content_hash"] + ".bin"))
        path.write_bytes(path.read_bytes() + b" ")
    else:
        with initial.database.transaction() as db:
            if fault == "raw_metadata":
                db.execute("UPDATE raw_snapshots SET resource_type='tampered'")
            elif fault == "missing_witness":
                db.execute("DROP TRIGGER the_stats_http_witnesses_no_delete")
                db.execute("DELETE FROM the_stats_http_witnesses")
            elif fault == "future_witness":
                db.execute("DROP TRIGGER the_stats_http_witnesses_no_update")
                times = json.loads(db.execute(
                    "SELECT times_json FROM the_stats_http_witnesses").fetchone()[0])
                times["available_at"] = (current[0] + timedelta(seconds=1)).isoformat()
                db.execute("UPDATE the_stats_http_witnesses SET available_at=?,times_json=?",
                           (times["available_at"], json.dumps(times)))
            elif fault in {"future_created", "shifted_created"}:
                created = current[0] + timedelta(seconds=1) if fault == "future_created" else (
                    NOW + timedelta(seconds=1))
                db.execute("UPDATE cache_entries SET created_at=?", (created.isoformat(),))
            elif fault == "reversed_expiry":
                db.execute("UPDATE cache_entries SET expires_at=?",
                           ((NOW - timedelta(seconds=1)).isoformat(),))
            else:
                raise AssertionError(fault)
    baseline = persistent_state(initial)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    assert retained(resource, cache_only=True) == (None, None, "CACHE_MISS")
    assert retained(resource) == (None, None, "REQUEST_BUDGET")
    assert len(wire.calls) == 1 and resource.usage["cache_hits"] == 0
    assert persistent_state(resource) == baseline


@pytest.mark.parametrize("endpoint,params,ttl", [
    ("/football/matches/mt_1/stats", PARAMS, TEAM_SEASON_STATS_TTL),
    ("/football/players/pl_1/stats", PARAMS, TEAM_SEASON_STATS_TTL),
    ("/football/teams/tm_15641/injuries-suspensions", PARAMS, TEAM_SEASON_STATS_TTL),
    ("/football/teams/tm_invalid/stats", PARAMS, TEAM_SEASON_STATS_TTL),
    (ENDPOINT, {}, TEAM_SEASON_STATS_TTL),
    (ENDPOINT, {"season_id": "sn_invalid"}, TEAM_SEASON_STATS_TTL),
    (ENDPOINT, {"season_id": 1}, TEAM_SEASON_STATS_TTL),
    (ENDPOINT, {**PARAMS, "competition_id": "comp_4643"}, TEAM_SEASON_STATS_TTL),
    (ENDPOINT, PARAMS, timedelta(minutes=30)),
    (ENDPOINT, PARAMS, timedelta(hours=5)),
])
def test_retained_opt_in_cannot_expand_to_other_endpoints_queries_or_lifetimes(
        tmp_path, endpoint, params, ttl):
    current, wire, client, initial, _ = seed(tmp_path)
    baseline = persistent_state(initial)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    with pytest.raises(ValueError, match="Retained reuse is limited"):
        resource.get(endpoint, params, ttl=ttl, retained_team_stats=True)
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


@pytest.mark.parametrize("value", [1, "true", None])
def test_retained_opt_in_requires_boolean(tmp_path, value):
    current, wire, client, initial, _ = seed(tmp_path)
    baseline = persistent_state(initial)
    with pytest.raises(ValueError, match="retained_team_stats must be boolean"):
        Resources(tmp_path, client, lambda: current[0], 0).get(
            ENDPOINT, PARAMS, ttl=TEAM_SEASON_STATS_TTL, retained_team_stats=value)
    assert len(wire.calls) == 1 and persistent_state(initial) == baseline


def test_other_valid_season_does_not_borrow_same_team_cache(tmp_path):
    current, wire, client, initial, _ = seed(tmp_path)
    current[0] += timedelta(minutes=89)
    baseline = persistent_state(initial)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    assert resource.get(ENDPOINT, {"season_id": "sn_999"}, ttl=TEAM_SEASON_STATS_TTL,
                        retained_team_stats=True) == (None, None, "REQUEST_BUDGET")
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


def test_retained_memo_does_not_leak_to_30_minute_policy_and_rechecks_age(tmp_path):
    current, wire, client, initial, original = seed(tmp_path)
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    assert retained(resource) == original
    assert resource.get(ENDPOINT, PARAMS, cache_only=True) == (None, None, "CACHE_MISS")
    assert resource.get(ENDPOINT, PARAMS) == (None, None, "REQUEST_BUDGET")
    assert resource.get(ENDPOINT, PARAMS, ttl=TEAM_SEASON_STATS_TTL,
                        cache_only=True) == (None, None, "CACHE_MISS")
    current[0] = NOW + TEAM_SEASON_STATS_TTL
    assert retained(resource, cache_only=True) == (None, None, "CACHE_MISS")
    assert retained(resource) == (None, None, "REQUEST_BUDGET")
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


def test_collector_uses_old_witnessed_stats_without_changing_source(tmp_path):
    current, wire, client, initial, original = seed(tmp_path)
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    record = fixture_record()
    assert _collect_fact(resource, record, "team_season_stats", "tm_15641", cache_only=True)
    fact = record["facts"][0]
    assert fact["status"] == "AVAILABLE" and fact["data"] == DATA
    assert fact["source"] == original[1] and not record["missing"]
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


@pytest.mark.parametrize("field,value", [
    ("team_id", "tm_999"), ("season_id", "sn_999"), ("competition_id", "comp_999"),
])
def test_witnessed_bytes_with_wrong_canonical_identity_never_become_available(
        tmp_path, field, value):
    current, wire, client, initial, _ = seed(tmp_path, data={**DATA, field: value})
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    record = fixture_record()
    assert _collect_fact(resource, record, "team_season_stats", "tm_15641", cache_only=True)
    fact = record["facts"][0]
    assert fact["status"] == "UNAVAILABLE" and fact["data"] is None
    assert fact["reason"] == "INVALID_PROVIDER_DATA"
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


def test_retained_stats_cannot_bypass_prematch_lockout(tmp_path):
    current, wire, client, initial, _ = seed(tmp_path)
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    record = fixture_record()
    record["kickoff"] = (current[0] + timedelta(minutes=15)).isoformat()
    assert _collect_fact(resource, record, "team_season_stats", "tm_15641", cache_only=True)
    assert record["status"] == "LOCKED"
    assert record["facts"][0]["reason"] == "PREMATCH_LOCKOUT"
    assert resource.usage["cache_hits"] == 0
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


def test_collector_does_not_retain_expired_injury_context(tmp_path):
    endpoint = "/football/teams/tm_15641/injuries-suspensions"
    current, wire, client, initial, _ = seed(
        tmp_path, data={"injuries": [], "suspensions": []},
        endpoint=endpoint, params={"current": True})
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=89)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    record = fixture_record()
    assert not _collect_fact(resource, record, "injuries_suspensions", "tm_15641",
                             cache_only=True)
    assert not record["facts"]
    assert _collect_fact(resource, record, "injuries_suspensions", "tm_15641")
    assert record["facts"][0]["reason"] == "REQUEST_BUDGET"
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline


@pytest.mark.parametrize("opt_in", [False, True])
def test_unexpired_legacy_cache_keeps_existing_behavior_without_new_witness(tmp_path, opt_in):
    current, wire, client, initial, original = seed(tmp_path)
    legacy = {key: value for key, value in original[1].items() if not key.startswith("http_")}
    initial.cache.set(VERSION, KEY, {"source": legacy}, timedelta(minutes=30), now=NOW)
    baseline = persistent_state(initial)
    current[0] += timedelta(minutes=5)
    resource = Resources(tmp_path, client, lambda: current[0], 0)
    result = retained(resource) if opt_in else resource.get(ENDPOINT, PARAMS)
    assert result == (original[0], legacy, None)
    assert len(wire.calls) == 1 and persistent_state(resource) == baseline
