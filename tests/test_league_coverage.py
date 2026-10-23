"""Strict catalogue identity and read-only archive coverage boundaries."""

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.jobs.league_coverage import build_league_coverage, inspect_league_coverage
from goal_analysis.storage import Database, SnapshotStore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
LEAGUES = [{"competition_id": "EN2", "api_football_id": 40}]
FLAGS = {"has_team_stats": True, "has_player_stats": False, "xg_available": None,
         "odds_available": True, "live_odds_available": False}


@pytest.fixture(autouse=True)
def keep_diagnostic_examples_unregistered(monkeypatch):
    # Exercise proposal rejection independently of the production registry.
    from goal_analysis.jobs import the_stats_context

    monkeypatch.setattr(the_stats_context, 'LEAGUES', {
        key: value for key, value in the_stats_context.LEAGUES.items()
        if key in {'39', '78', '79', '80', '140', '61', '135', '136', '88', '89'}})


def source(provider="the_stats_api"):
    return {"provider": provider, "source_url": "https://api.example.test/catalogue",
            "observed_at": NOW.isoformat(), "evidence_sha256": "a" * 64}


def entry(**changes):
    return {"id": "comp_8321", "name": "Championship", "country": "England",
            "advertised": dict(FLAGS), "source": source(), **changes}


def pure(entries=None, identities=None, report=None, leagues=None):
    return build_league_coverage(LEAGUES if leagues is None else leagues,
        catalogue={"status": "AVAILABLE", "entries": [entry()] if entries is None else entries},
        api_identities={40: {"name": "Championship", "country": "England",
                             "source": source("api_football")}} if identities is None else identities,
        report={} if report is None else report, as_of=NOW)


def league(result, identifier=40):
    return next(row for row in result["leagues"] if row["api_football_id"] == identifier)


def test_default_twenty_five_conditional_three_and_current_manual_union_are_complete():
    config = Path(__file__).resolve().parents[1] / "config/daily223-live.example.json"
    defaults = json.loads(config.read_text(encoding="utf-8"))["leagues"]
    assert len(defaults) == 25
    report = {"selected_competition_ids": [39, 777],
              "fixtures": [{"fixture_id": "api_football:999", "api_football_league_id": "888"}]}
    result = pure(entries=[], identities={}, report=report, leagues=defaults)
    assert result["active_competitions"] == 30
    assert league(result, 39)["scope"] == "DEFAULT"
    assert league(result, 138)["scope"] == "CONDITIONAL"
    assert league(result, 942)["scope"] == "CONDITIONAL"
    assert league(result, 943)["scope"] == "CONDITIONAL"
    assert league(result, 777)["scope"] == "SELECTED_EXTRA"
    assert league(result, 888)["scope"] == "SELECTED_EXTRA"


def test_exact_mapping_is_diagnostic_only_and_preserves_false_and_unknown_capabilities():
    from goal_analysis.jobs.the_stats_context import LEAGUES as collector_map

    before = deepcopy(collector_map)
    result = pure()
    row = league(result)
    assert row["mapping_status"] == "MAPPED"
    assert row["mapping_method"] == "EXACT_NAME_COUNTRY_PROPOSAL"
    assert row["collector_enabled"] is False
    assert row["the_stats"]["advertised"] == FLAGS
    assert row["available_fact_categories"] == {}
    assert row["capability_interpretation"] == "ADVERTISED_NOT_ACCESS_OR_DATA_VERIFIED"
    assert result["new_collector_mappings_activated"] == 0
    assert collector_map == before


@pytest.mark.parametrize("api_name,api_country,provider_name,provider_country", [
    ("League Cup", "England", "EFL Cup", "England"),
    ("Primeira Liga", "Portugal", "Liga Portugal Betclic", "Portugal"),
    ("Serie C - Girone A", "Italy", "Serie C, Girone A", "Italy"),
    ("UEFA Champions League", "World", "UEFA Champions League", None),
    ("Championship", "England", "Championship", "Scotland"),
])
def test_branding_punctuation_unknown_country_and_wrong_country_do_not_create_links(
        api_name, api_country, provider_name, provider_country):
    result = pure(entries=[entry(name=provider_name, country=provider_country)],
                  identities={40: {"name": api_name, "country": api_country,
                                    "source": source("api_football")}})
    assert league(result)["mapping_status"] == "MISSING_LINK"
    assert league(result)["the_stats"] is None


def test_duplicate_exact_candidates_are_ambiguous_instead_of_selecting_first():
    result = pure(entries=[entry(), entry(id="comp_9999")])
    assert league(result)["mapping_status"] == "AMBIGUOUS"
    assert league(result)["the_stats"] is None
    assert league(result)["collector_enabled"] is False


@pytest.mark.parametrize("changes", [{"id": "comp_9999"}, {"name": "Premier League 2"},
                                    {"country": "Scotland"}, {"country": None},
                                    {"country": ""}])
def test_existing_verified_id_requires_matching_catalogue_name_and_country(changes):
    accepted = entry(id="comp_3039", name="Premier League")
    defaults = [{"competition_id": "EN1", "api_football_id": 39}, *LEAGUES]
    result = pure(entries=[{**accepted, **changes}, entry()], leagues=defaults)
    assert league(result, 39)["mapping_status"] == "MISSING_LINK"
    assert league(result, 39)["collector_enabled"] is True
    assert league(result, 40)["mapping_status"] == "MAPPED"
    valid = pure(entries=[accepted], identities={}, leagues=defaults)
    assert league(valid, 39)["mapping_method"] == "EXISTING_VERIFIED_MAPPING"
    assert league(valid, 39)["collector_enabled"] is True


def archive(tmp_path):
    database = Database(tmp_path / "data/goal_analysis.sqlite3")
    database.initialize()
    return database, SnapshotStore(database, tmp_path / "data/raw")


def save_page(store, *, page=1, pages=1, total=1, entries=None, observed=NOW):
    raw_entries = [{"id": "comp_8321", "name": "Championship", "country": "England", **FLAGS}]
    payload = {"data": raw_entries if entries is None else entries,
               "meta": {"page": page, "total_pages": pages, "total": total, "per_page": 100}}
    return store.save("the_stats_api", "competition_catalogue", json.dumps(payload).encode(),
                      "application/json", observed,
                      f"https://api.thestatsapi.com/api/football/competitions?page={page}&per_page=100")


def save_api(store, payload=None, *, observed=NOW):
    if payload is None:
        payload = {"response": [{"league": {"id": 40, "name": "Championship", "country": "England"}}]}
    return store.save("api_football", "fixtures", json.dumps(payload).encode(), "application/json",
                      observed, "https://v3.football.api-sports.io/fixtures?league=40&season=2026")


def inspect(tmp_path):
    return inspect_league_coverage(tmp_path, {}, as_of=NOW, leagues=LEAGUES)


def database_dump(database):
    with sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True) as connection:
        return list(connection.iterdump())


def test_complete_paginated_archive_keeps_raw_proof_without_db_or_provider_writes(tmp_path, monkeypatch):
    database, store = archive(tmp_path)
    saved = save_page(store, page=1, pages=2, total=2)
    save_page(store, page=2, pages=2, total=2,
              entries=[{"id": "comp_3039", "name": "Premier League", "country": "England"}])
    save_api(store)
    before, files = database_dump(database), sorted(str(p) for p in tmp_path.rglob("*.bin"))

    def forbidden_request(*args, **kwargs):
        pytest.fail("coverage inventory must not call a provider")

    monkeypatch.setattr("goal_analysis.providers.the_stats_api.TheStatsApiClient.request", forbidden_request)
    monkeypatch.setattr("goal_analysis.providers.api_football.ApiFootballClient.request", forbidden_request)
    result = inspect(tmp_path)
    row = league(result)
    assert result["catalogue"]["status"] == "AVAILABLE"
    assert result["catalogue"]["pages"] == 2 and result["catalogue_competitions"] == 2
    assert row["mapping_status"] == "MAPPED"
    assert row["the_stats"]["source"]["evidence_sha256"] == saved.content_hash
    assert row["the_stats"]["advertised"] == FLAGS
    assert row["api_identity"]["source"]["provider"] == "api_football"
    assert result["external_requests"] == 0
    assert database_dump(database) == before
    assert sorted(str(p) for p in tmp_path.rglob("*.bin")) == files


@pytest.mark.parametrize("fault", ["missing_page", "wrong_count", "future", "naive",
                                  "corrupt", "escape"])
def test_incomplete_future_corrupt_or_escaping_catalogue_cannot_claim_coverage(tmp_path, fault):
    database, store = archive(tmp_path)
    saved = save_page(store, pages=2 if fault == "missing_page" else 1,
                      total=2 if fault in {"missing_page", "wrong_count"} else 1,
                      observed=NOW + timedelta(seconds=1) if fault == "future" else NOW)
    save_api(store)
    raw_path = tmp_path / "data/raw" / saved.relative_path
    if fault == "corrupt":
        raw_path.write_bytes(b"tampered")
    elif fault == "escape":
        (tmp_path / "escape.bin").write_bytes(raw_path.read_bytes())
        with database.transaction() as connection:
            connection.execute("UPDATE raw_snapshots SET relative_path=? WHERE content_hash=?",
                               ("../../escape.bin", saved.content_hash))
    elif fault == "naive":
        with database.transaction() as connection:
            connection.execute("UPDATE raw_snapshots SET fetched_at=? WHERE content_hash=?",
                               (NOW.replace(tzinfo=None).isoformat(), saved.content_hash))
    before = database_dump(database)
    result = inspect(tmp_path)
    assert result["catalogue"]["status"] == "MISSING"
    assert result["catalogue_competitions"] == 0
    assert league(result)["mapping_status"] == "MISSING_LINK"
    assert database_dump(database) == before


def test_malformed_api_payload_isolated_from_valid_identity_and_catalogue(tmp_path):
    _, store = archive(tmp_path)
    save_page(store)
    save_api(store)
    save_api(store, [], observed=NOW - timedelta(minutes=1))
    result = inspect(tmp_path)
    assert result["catalogue"]["status"] == "AVAILABLE"
    assert league(result)["mapping_status"] == "MAPPED"


def test_missing_database_stays_missing_without_initializing_storage(tmp_path):
    result = inspect(tmp_path)
    assert result["catalogue"]["status"] == "MISSING"
    assert not (tmp_path / "data").exists()
    assert result["external_requests"] == 0


def test_collection_attaches_inventory_after_collection_without_extra_clock_or_requests(tmp_path, monkeypatch):
    from test_the_stats_context import NOW as COLLECTION_NOW
    from test_the_stats_context import Transport, report

    from goal_analysis.jobs.the_stats_context import collect_context
    from goal_analysis.providers.the_stats_api import TheStatsApiClient

    transport, calls = Transport(), []

    class Clock:
        count = 0

        def __call__(self):
            self.count += 1
            return COLLECTION_NOW + timedelta(microseconds=self.count)

    clock = Clock()
    marker = {"mode": "SHADOW", "status": "PARTIAL", "external_requests": 0}

    def inventory(root, value, *, as_of, **kwargs):
        calls.append((as_of, value["sports_supplement"]["the_stats_api"]["coverage"],
                      len(transport.calls), clock.count))
        return marker

    monkeypatch.setattr("goal_analysis.jobs.league_coverage.inspect_league_coverage", inventory)
    client = TheStatsApiClient("safe-test-key", transport, clock=clock, max_requests=100)
    result = collect_context(tmp_path, report(), client=client, clock=clock, max_requests=40)
    assert result["league_coverage"] == marker
    assert len(calls) == 1
    as_of, coverage, requests, clock_count = calls[0]
    assert as_of == result["observed_at"]
    assert coverage == result["coverage"] and coverage["matched"] == 1
    assert requests == len(transport.calls)
    assert clock_count == clock.count
