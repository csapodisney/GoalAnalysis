"""Adversarial contracts for the additive, point-in-time SHADOW result store."""
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.normalization.canonical import canonical_id
from goal_analysis.storage.db import Database
from goal_analysis.storage.historical_results import TABLES, HistoricalResults

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIPT = datetime(2026, 10, 10, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 9, 18, tzinfo=UTC)
EXACT = "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE"
MAPPING = "VERIFIED_PROVIDER_MAPPING"


def result(provider="the_stats_api", native_id="mt_1", **changes):
    return {
        "provider": provider, "provider_id": native_id,
        "competition_id": provider + ":league:1", "season_id": provider + ":season:1",
        "home_id": provider + ":team:1", "away_id": provider + ":team:2",
        "home_team": "Home FC", "away_team": "Away FC",
        "kickoff": KICKOFF.isoformat(), "match_date": KICKOFF.date().isoformat(),
        "kickoff_local_time": None, "status": "FT", "score_scope": "REGULATION",
        "home_goals": 2, "away_goals": 1, "half_home_goals": 1, "half_away_goals": 0,
        "observed_at": RECEIPT.isoformat(),
        "source_url": "https://football.example.test/" + provider + "/" + native_id,
        "source_sha256": "a" * 64, "raw": {"original": {"retained": True}},
        **changes,
    }


def fixture_id(record):
    return canonical_id("fixture", record["provider"] + ":" + record["provider_id"])


def proof(record=None, **changes):
    record = record or result()
    return {**{key: record[key] for key in ("source_url", "source_sha256", "observed_at")},
            **changes}


def setup_store(tmp_path, *, now=NOW):
    store = HistoricalResults(Database(tmp_path / "results.sqlite3"), clock=lambda: now)
    store.initialize()
    return store


def link(store, left, right, *, method=EXACT, evidence=None):
    return store.link(fixture_id(left), fixture_id(right), method=method,
                      evidence=evidence or [proof(left), proof(right)],
                      competition_pair=sorted([[left["provider"], left["competition_id"]],
                                               [right["provider"], right["competition_id"]]]))


def records_count(store):
    with store.database.connect() as db:
        return {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in TABLES}


def test_distinct_native_homonyms_are_stored_but_need_authoritative_orientation(tmp_path):
    store = setup_store(tmp_path)
    first = result(home_team="United", away_team="UNITED")
    second = result("api_football", "1", home_team="United", away_team="UNITED")
    store.ingest([first, second])
    assert store.view(as_of=NOW)["counts"]["native_matches"] == 2
    with pytest.raises(ValueError, match="EXACT_EVENT"):
        link(store, first, second)
    link(store, first, second, method=MAPPING)
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 1


def test_additive_initialization_native_scope_idempotence_and_integrity(tmp_path):
    store = setup_store(tmp_path)
    with store.database.transaction() as db:
        db.execute("CREATE TABLE legacy_records (value TEXT)")
        db.execute("INSERT INTO legacy_records VALUES ('must remain')")
    first, second = result(), result("api_football", "mt_1")
    assert store.ingest([first, second]) == {"matches_created": 2, "versions_created": 2}
    store.initialize()
    assert store.ingest([deepcopy(second), deepcopy(first)]) == {
        "matches_created": 0, "versions_created": 0}
    view = store.view(as_of=NOW)
    assert view["mode"] == "SHADOW"
    assert view["effective_data_path"] == "LEGACY_UNCHANGED"
    assert view["counts"]["unified_matches"] == 2
    assert {row["representative_id"] for row in view["matches"]} == {
        fixture_id(first), fixture_id(second)}
    assert all(row["link_status"] == "UNLINKED" for row in view["matches"])
    with store.database.connect() as db:
        assert db.execute("SELECT value FROM legacy_records").fetchone()[0] == "must remain"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_original_receipt_cannot_backdate_actual_ingestion(tmp_path):
    store = setup_store(tmp_path)
    item = result()
    store.ingest([item])
    assert store.view(as_of=NOW - timedelta(microseconds=1))["matches"] == []
    row = store.view(as_of=NOW)["matches"][0]["source_records"][0]
    assert datetime.fromisoformat(row["observed_at"]) == RECEIPT
    assert datetime.fromisoformat(row["ingested_at"]) == NOW
    item["raw"]["original"]["retained"] = False
    assert row["record"]["raw"]["original"]["retained"] is True


@pytest.mark.parametrize("changes", [
    {"observed_at": (NOW + timedelta(microseconds=1)).isoformat()},
    {"home_goals": True}, {"home_goals": -1}, {"away_goals": None},
    {"half_home_goals": 3}, {"kickoff": RECEIPT.isoformat()},
    {"status": "scheduled"}, {"away_id": "the_stats_api:team:1"},
    {"kickoff": None, "match_date": (RECEIPT + timedelta(days=1)).date().isoformat()},
    {"source_url": "https://football.example.test/matches?api_key=secret"},
    {"source_sha256": "not-a-hash"}, {"raw": {"nonfinite": float("nan")}},
])
def test_invalid_batch_is_atomic_and_preserves_existing_rows(tmp_path, changes):
    store = setup_store(tmp_path)
    store.ingest([result(native_id="existing")])
    before = records_count(store)
    with pytest.raises((ValueError, TypeError)):
        store.ingest([result(native_id="would-be-valid"), result(native_id="bad", **changes)])
    assert records_count(store) == before


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("mutation", ("UPDATE", "DELETE"))
def test_every_store_table_is_append_only(tmp_path, table, mutation):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    link(store, first, second)
    before = records_count(store)
    key = {TABLES[0]: "match_id", TABLES[1]: "version_id", TABLES[2]: "event_id"}[table]
    command = ("UPDATE " + table + " SET " + key + "=" + key
               if mutation == "UPDATE" else "DELETE FROM " + table)
    with store.database.connect() as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(command)
    assert records_count(store) == before


def test_score_correction_is_a_revision_and_late_old_receipt_does_not_win(tmp_path):
    store = setup_store(tmp_path)
    original = result()
    store.ingest([original])
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    corrected = result(home_goals=3, observed_at=later.isoformat(), source_sha256="b" * 64)
    assert store.ingest([corrected])["versions_created"] == 1
    store.ingest([result(home_goals=1, observed_at=(RECEIPT - timedelta(hours=1)).isoformat())])
    assert store.view(as_of=NOW)["matches"][0]["score_resolutions"]["REGULATION"]["score"] == [2, 1]
    assert store.view(as_of=later)["matches"][0]["score_resolutions"]["REGULATION"]["score"] == [3, 1]
    assert records_count(store)[TABLES[0]] == 1
    assert records_count(store)[TABLES[1]] == 3


@pytest.mark.parametrize("changes", [{"home_goals": 3}, {"home_team": "Different FC"}])
def test_same_receipt_conflicting_score_or_identity_is_quarantined(tmp_path, changes):
    store = setup_store(tmp_path)
    first, conflicting = result(), result(source_sha256="b" * 64, **changes)
    store.ingest([first, conflicting])
    view = store.view(as_of=NOW)
    assert view["matches"] == []
    assert view["counts"]["same_time_conflicts"] == 1
    assert len(view["same_time_conflicts"][fixture_id(first)]) == 2
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    store.ingest([result(observed_at=later.isoformat())])
    assert store.view(as_of=later)["counts"]["same_time_conflicts"] == 0
    assert store.view(as_of=later)["counts"]["unified_matches"] == 1


def test_raw_only_difference_is_retained_without_a_false_decision_conflict(tmp_path):
    store = setup_store(tmp_path)
    store.ingest([result(), result(raw={"another_source_snapshot": True}, source_sha256="b" * 64)])
    assert records_count(store)[TABLES[1]] == 2
    assert store.view(as_of=NOW)["counts"]["same_time_conflicts"] == 0
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 1


def test_old_source_proofs_link_newly_ingested_endpoints_without_backdating(tmp_path):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    created = link(store, first, second)
    assert created["links_created"] == 1
    assert link(store, first, second) == {"link_id": created["link_id"], "links_created": 0}
    view = store.view(as_of=NOW)
    assert view["counts"]["verified_multi_source_matches"] == 1
    assert view["matches"][0]["member_ids"] == sorted([fixture_id(first), fixture_id(second)])
    assert store.view(as_of=NOW - timedelta(microseconds=1))["matches"] == []


@pytest.mark.parametrize("changes", [
    {"home_team": "Home-FC"}, {"home_team": "Away FC", "away_team": "Home FC"},
    {"kickoff": (KICKOFF + timedelta(minutes=1)).isoformat()},
    {"kickoff": None, "kickoff_local_time": "20:00"},
])
def test_exact_event_link_rejects_fuzzy_reversed_or_unknown_time_identity(tmp_path, changes):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1", **changes)
    store.ingest([first, second])
    with pytest.raises(ValueError):
        link(store, first, second)
    assert records_count(store)[TABLES[2]] == 0
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 2


def test_exact_names_accept_only_safe_case_whitespace_unicode_equivalence(tmp_path):
    store = setup_store(tmp_path)
    first = result(home_team="Hóme FC")
    second = result("api_football", "1", home_team="  HO\u0301ME   fc  ")
    store.ingest([first, second])
    link(store, first, second)
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 1


def test_explicit_competition_pair_is_required_and_must_match_both_endpoints(tmp_path):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    for pair in (None, [], [["api_football", "wrong"], ["the_stats_api", first["competition_id"]]]):
        with pytest.raises(ValueError, match="EXPLICIT_COMPETITION"):
            store.link(fixture_id(first), fixture_id(second), method=EXACT,
                       evidence=[proof(first), proof(second)], competition_pair=pair)
    assert records_count(store)[TABLES[2]] == 0


def test_date_only_football_data_remains_distinct_until_authoritative_id_mapping(tmp_path):
    store = setup_store(tmp_path)
    first = result()
    second = result("football_data_co_uk", "csv:1", kickoff=None, kickoff_local_time="20:00",
                    home_id=None, away_id=None, score_scope="SOURCE_FULL_TIME_UNVERIFIED")
    store.ingest([first, second])
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 2
    with pytest.raises(ValueError):
        link(store, first, second)
    link(store, first, second, method=MAPPING)
    joined = store.view(as_of=NOW)["matches"][0]
    assert joined["source_records"][1]["record"]["kickoff"] is None
    assert set(joined["score_resolutions"]) == {"REGULATION", "SOURCE_FULL_TIME_UNVERIFIED"}


def test_date_only_receipt_is_archived_but_not_usable_before_next_utc_day(tmp_path):
    store = setup_store(tmp_path, now=RECEIPT)
    item = result("football_data_co_uk", "csv:1", kickoff=None,
                  match_date=RECEIPT.date().isoformat(), kickoff_local_time="23:59")
    assert store.ingest([item])["versions_created"] == 1
    assert store.view(as_of=RECEIPT)["matches"] == []
    next_day = (RECEIPT + timedelta(days=1)).replace(hour=0, minute=0)
    assert store.view(as_of=next_day)["counts"]["unified_matches"] == 1
    assert store.view(as_of=next_day)["matches"][0]["source_records"][0]["record"]["kickoff"] is None


def test_transitive_component_cannot_contain_two_native_ids_of_one_provider(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a, api_b = result(), result("api_football", "1"), result("api_football", "2")
    third = result("football_data_co_uk", "csv:1", kickoff=None)
    store.ingest([stats, api_a, api_b, third])
    link(store, stats, api_a, method=MAPPING)
    link(store, stats, third, method=MAPPING)
    with pytest.raises(ValueError, match="TRANSITIVE_PROVIDER"):
        link(store, api_b, third, method=MAPPING)
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 2
    assert records_count(store)[TABLES[2]] == 2


def test_transitive_check_uses_current_links_even_when_new_proof_is_old(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a, api_b = result(), result("api_football", "1"), result("api_football", "2")
    store.ingest([stats, api_a, api_b])
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    link(store, stats, api_a, method=MAPPING, evidence=[proof(observed_at=later.isoformat())])
    with pytest.raises(ValueError, match="TRANSITIVE_PROVIDER"):
        link(store, stats, api_b, method=MAPPING)
    assert store.view(as_of=later)["counts"]["unified_matches"] == 2


def test_reconciliation_batch_rolls_back_earlier_links_when_later_edge_is_ambiguous(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a, api_b = result(), result("api_football", "1"), result("api_football", "2")
    store.ingest([stats, api_a, api_b])
    requests = [{"left_id": fixture_id(stats), "right_id": fixture_id(api),
                 "method": MAPPING, "evidence": [proof(stats), proof(api)],
                 "competition_pair": sorted([[stats["provider"], stats["competition_id"]],
                                              [api["provider"], api["competition_id"]]])}
                for api in (api_a, api_b)]
    with pytest.raises(ValueError, match="TRANSITIVE_PROVIDER"):
        store.link_many(requests)
    assert records_count(store)[TABLES[2]] == 0
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 3
    assert store.link_many(requests[:1])[0]["links_created"] == 1


def test_revocation_is_append_only_and_visible_only_after_actual_ingestion(tmp_path):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    created = link(store, first, second)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    revocation = proof(observed_at=(RECEIPT + timedelta(days=1)).isoformat())
    assert store.revoke(created["link_id"], evidence=revocation) == 1
    assert store.revoke(created["link_id"], evidence=revocation) == 0
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 1
    assert store.view(as_of=later)["counts"]["unified_matches"] == 2
    assert records_count(store)[TABLES[2]] == 2


def test_revoked_edge_does_not_block_a_new_unambiguous_component(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a, api_b = result(), result("api_football", "1"), result("api_football", "2")
    store.ingest([stats, api_a, api_b])
    created = link(store, stats, api_a, method=MAPPING)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    store.revoke(created["link_id"], evidence=proof(observed_at=later.isoformat()))
    link(store, stats, api_b, method=MAPPING)
    view = store.view(as_of=later)
    assert view["counts"]["unified_matches"] == 2
    joined = next(row for row in view["matches"] if len(row["member_ids"]) == 2)
    assert joined["member_ids"] == sorted([fixture_id(stats), fixture_id(api_b)])


def test_later_native_duplicate_suspends_exact_link_without_rewriting_past(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a = result(), result("api_football", "1")
    store.ingest([stats, api_a])
    link(store, stats, api_a)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    api_b = result("api_football", "2", observed_at=later.isoformat())
    store.ingest([api_b])
    assert store.view(as_of=NOW)["counts"]["verified_multi_source_matches"] == 1
    current = store.view(as_of=later)
    assert current["counts"]["unified_matches"] == 3
    assert current["counts"]["verified_multi_source_matches"] == 0
    with pytest.raises(ValueError):
        link(store, stats, api_a, evidence=[proof(observed_at=later.isoformat())])
    with pytest.raises(ValueError):
        link(store, stats, api_b)


@pytest.mark.parametrize("conflict", [{"home_goals": 3}, {"home_team": "Alternative identity FC"}])
def test_quarantined_second_native_identity_still_invalidates_exact_uniqueness(tmp_path, conflict):
    store = setup_store(tmp_path)
    stats, api_a = result(), result("api_football", "1")
    store.ingest([stats, api_a])
    link(store, stats, api_a)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    api_b = result("api_football", "2", observed_at=later.isoformat())
    store.ingest([api_b, {**api_b, **conflict, "source_sha256": "b" * 64}])
    current = store.view(as_of=later)
    assert current["counts"]["same_time_conflicts"] == 1
    assert fixture_id(api_b) in current["same_time_conflicts"]
    assert current["counts"]["unified_matches"] == 2
    assert current["counts"]["verified_multi_source_matches"] == 0
    assert store.view(as_of=NOW)["counts"]["verified_multi_source_matches"] == 1
    with pytest.raises(ValueError):
        link(store, stats, api_a, evidence=[proof(observed_at=later.isoformat())])


def test_authoritative_mapping_survives_later_name_time_ambiguity(tmp_path):
    store = setup_store(tmp_path)
    stats, api_a = result(), result("api_football", "1")
    store.ingest([stats, api_a])
    link(store, stats, api_a, method=MAPPING)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    store.ingest([result("api_football", "2", observed_at=later.isoformat())])
    current = store.view(as_of=later)
    assert current["counts"]["unified_matches"] == 2
    assert current["counts"]["verified_multi_source_matches"] == 1
    joined = next(row for row in current["matches"] if len(row["member_ids"]) == 2)
    assert joined["member_ids"] == sorted([fixture_id(stats), fixture_id(api_a)])


def test_goal_correction_retains_link_but_identity_correction_invalidates_it(tmp_path):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    link(store, first, second)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    store.ingest([result(home_goals=3, observed_at=later.isoformat())])
    view = store.view(as_of=later)
    assert view["counts"]["unified_matches"] == 1
    assert view["counts"]["score_conflicts"] == 1
    assert view["matches"][0]["score_resolutions"]["REGULATION"]["score"] == [3, 1]
    last = later + timedelta(hours=1)
    store.clock = lambda: last
    store.ingest([result(home_team="Corrected identity FC", observed_at=last.isoformat())])
    assert store.view(as_of=last)["counts"]["unified_matches"] == 2
    assert store.view(as_of=NOW)["counts"]["unified_matches"] == 1
    with pytest.raises(ValueError):
        link(store, first, second)


def test_score_priority_preserves_conflicting_sources_and_separate_period_scope(tmp_path):
    store = setup_store(tmp_path)
    stats = result()
    api = result("api_football", "1", home_goals=4, status="PEN")
    csv = result("football_data_co_uk", "csv:1", home_goals=5, kickoff=None,
                 score_scope="SOURCE_FULL_TIME_UNVERIFIED")
    store.ingest([csv, api, stats])
    link(store, stats, api)
    link(store, stats, csv, method=MAPPING)
    row = store.view(as_of=NOW)["matches"][0]
    assert len(row["source_records"]) == 3
    regular = row["score_resolutions"]["REGULATION"]
    assert regular["status"] == "CONFLICT"
    assert regular["score"] == [2, 1]
    assert regular["resolution_reason"] == "PRIMARY_SOURCE_PRIORITY"
    assert row["score_resolutions"]["SOURCE_FULL_TIME_UNVERIFIED"]["score"] == [5, 1]


def test_missing_primary_regulation_score_uses_api_not_unverified_csv_score(tmp_path):
    store = setup_store(tmp_path)
    stats = result(home_goals=None, away_goals=None, half_home_goals=None, half_away_goals=None)
    api = result("api_football", "1", home_goals=3)
    csv = result("football_data_co_uk", "csv:1", home_goals=7, kickoff=None,
                 score_scope="SOURCE_FULL_TIME_UNVERIFIED")
    store.ingest([stats, api, csv])
    link(store, stats, api)
    link(store, stats, csv, method=MAPPING)
    regular = store.view(as_of=NOW)["matches"][0]["score_resolutions"]["REGULATION"]
    assert regular["score"] == [3, 1]
    assert regular["resolution_reason"] == "FALLBACK_PRIMARY_UNAVAILABLE"


@pytest.mark.parametrize("evidence", [
    [], [{"source_url": "https://football.example.test/key", "source_sha256": "a" * 64}],
    [proof(source_url="https://user:secret@football.example.test/mapping")],
    [proof(observed_at=(NOW + timedelta(seconds=1)).isoformat())],
])
def test_bad_link_evidence_cannot_create_partial_edges(tmp_path, evidence):
    store = setup_store(tmp_path)
    first, second = result(), result("api_football", "1")
    store.ingest([first, second])
    with pytest.raises((ValueError, TypeError)):
        store.link(fixture_id(first), fixture_id(second), method=MAPPING, evidence=evidence)
    assert records_count(store)[TABLES[2]] == 0


def test_same_provider_native_ids_and_unknown_endpoints_cannot_be_linked(tmp_path):
    store = setup_store(tmp_path)
    first, second = result(), result(native_id="mt_2")
    store.ingest([first, second])
    with pytest.raises(ValueError, match="SAME_PROVIDER"):
        link(store, first, second, method=MAPPING)
    with pytest.raises(ValueError, match="MISSING_LINK_ENDPOINT"):
        store.link(fixture_id(first), fixture_id(result("api_football", "unknown")),
                   method=MAPPING, evidence=[proof()])
    assert records_count(store)[TABLES[2]] == 0
