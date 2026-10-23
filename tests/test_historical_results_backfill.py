"""Offline reconciliation of three archived sources; no real provider or DB."""
import copy
import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.jobs import historical_results as job
from goal_analysis.jobs.the_stats_history import _record as stats_record
from goal_analysis.providers.football_data_csv import parse_csv
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_archive import SportsArchive

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIVED = NOW - timedelta(hours=1)
KICKOFF = "2026-10-17T15:00:00Z"
API_URL = "https://v3.football.api-sports.io/fixtures?league=39&season=2026"
STATS_URL = "https://api.thestatsapi.com/api/football/matches?competition_id=comp_4643"
FD_URL = "https://www.football-data.co.uk/mmz4281/2627/E0.csv"
CSV = (b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR\r\n"
       b"E0,17/10/26,15:00,Home,Away,2,1,H,1,0,H\r\n")


def api_match(number=1, **updates):
    value = {"fixture": {"id": number, "date": KICKOFF, "status": {"short": "FT"}},
        "league": {"id": 39, "season": 2026},
        "teams": {"home": {"id": 1, "name": "Home"},
                  "away": {"id": 2, "name": "Away"}},
        "goals": {"home": 2, "away": 1},
        "score": {"fulltime": {"home": 2, "away": 1},
                  "halftime": {"home": 1, "away": 0}}}
    value.update(updates)
    return value


def stats_match():
    return {"id": "mt_1", "competition_id": "comp_4643", "season_id": "sn_1",
        "status": "finished", "utc_date": KICKOFF,
        "home_team": {"id": "tm_1", "name": "Home"},
        "away_team": {"id": "tm_2", "name": "Away"},
        "score": {"regulation": {"home": 2, "away": 1}}, "xg_available": True}


def synthetic_archives(root, *, api_rows=None, include_api=True, include_stats=True,
                       include_fd=True):
    database = Database(root / "data/goal_analysis.sqlite3")
    database.initialize()
    archive = SportsArchive(database)
    archive.initialize()
    snapshots = SnapshotStore(database, root / "data/raw")
    if include_fd:
        snapshot = snapshots.save("football_data_co_uk", "historical_csv", CSV,
            "text/csv", RECEIVED, FD_URL)
        archive.ingest(parse_csv(CSV, "E0", "2627", NOW.date()), source_url=FD_URL,
            fetched_at=RECEIVED, content_hash=snapshot.content_hash)
    if include_stats:
        raw = stats_match()
        content = json.dumps({"data": [raw]}).encode()
        snapshot = snapshots.save("the_stats_api", "historical_matches", content,
            "application/json", RECEIVED, STATS_URL)
        row = stats_record(raw, {"competition_id": "comp_4643",
            "date_from": "2026-10-17", "date_to": "2026-10-17"}, RECEIVED)
        archive.ingest({"provider": "the_stats_api", "league_code": "comp_4643",
            "season_code": "sn_1", "records": [row], "columns": sorted(raw)},
            source_url=STATS_URL, fetched_at=RECEIVED, content_hash=snapshot.content_hash)
    if include_api:
        content = json.dumps({"errors": [], "response": api_rows if api_rows is not None
                             else [api_match()]}).encode()
        snapshots.save("api_football", "fixtures", content, "application/json",
            RECEIVED, API_URL)
    return database


def proof(url, sha):
    return {"source_url": url, "evidence_sha256": sha * 64,
            "observed_at": RECEIVED.isoformat()}


def coverage(method="EXISTING_VERIFIED_MAPPING"):
    return {"leagues": [{"mapping_status": "MAPPED", "mapping_method": method,
        "api_football_id": 39, "api_identity": {"source": proof(API_URL, "a")},
        "the_stats": {"id": "comp_4643", "source": proof(STATS_URL, "b")}}]}


def old_tables(database):
    with database.connect() as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'historical_result_%' ORDER BY name")]
        return {name: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"'))
                for name in names}


def test_three_archived_sources_backfill_offline_without_legacy_changes(tmp_path, monkeypatch):
    database = synthetic_archives(tmp_path)
    original = old_tables(database)
    raw_bytes = {path: path.read_bytes() for path in (tmp_path / "data/raw").rglob("*.bin")}
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("offline result backfill must not request external data")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr(job, "inspect_league_coverage", lambda *args, **kwargs: coverage())
    first = job.backfill_existing_results(tmp_path, clock=lambda: NOW)
    assert first["status"] == "COMPLETE"
    assert first["mode"] == "SHADOW" and first["effective_data_path"] == "LEGACY_UNCHANGED"
    assert first["preparation"]["accepted_snapshots"] == {
        "the_stats_api": 1, "football_data_co_uk": 1, "api_football": 1}
    assert first["native_matches_by_provider"] == {
        "the_stats_api": 1, "api_football": 1, "football_data_co_uk": 1}
    assert first["counts"]["native_matches"] == 3
    assert first["counts"]["unified_matches"] == 2
    assert first["counts"]["verified_multi_source_matches"] == 1
    assert first["date_only_matches"] == 1 and first["external_requests"] == 0
    assert first["reconciliation"]["links_created"] == 1
    assert Path(first["backup_path"]).is_file()
    assert old_tables(database) == original
    assert {path: path.read_bytes() for path in raw_bytes} == raw_bytes
    view = HistoricalResults(database).view(as_of=NOW)
    merged = next(row for row in view["matches"] if len(row["member_ids"]) == 2)
    assert merged["source_records"][0]["record"]["provider"] == "the_stats_api"
    fd = next(row for match in view["matches"] for row in match["source_records"]
              if row["record"]["provider"] == "football_data_co_uk")["record"]
    assert fd["kickoff"] is None and fd["kickoff_local_time"] == "15:00"
    assert fd["score_scope"] == "SOURCE_FULL_TIME_UNVERIFIED"
    second = job.backfill_existing_results(tmp_path, clock=lambda: NOW)
    assert second["created"] == {"matches_created": 0, "versions_created": 0}
    assert second["reconciliation"]["links_created"] == 0
    assert second["counts"] == first["counts"] and old_tables(database) == original
    assert not calls


def test_backfill_does_not_create_missing_legacy_database(tmp_path):
    with pytest.raises(ValueError, match="EXISTING_SPORTS_DATABASE_REQUIRED"):
        job.backfill_existing_results(tmp_path, clock=lambda: NOW)
    assert not (tmp_path / "data").exists()


def test_empty_archives_do_not_create_result_tables_or_backup(tmp_path):
    database = synthetic_archives(tmp_path, include_api=False, include_stats=False, include_fd=False)
    result = job.backfill_existing_results(tmp_path, clock=lambda: NOW)
    assert result["status"] == "MISSING" and result["external_requests"] == 0
    with database.connect() as connection:
        assert not list(connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'historical_result_%'"))
    assert not (tmp_path / "data/backups").exists()


@pytest.mark.parametrize(("damage", "reason"), [
    ("bytes", "CORRUPT_RAW_SNAPSHOT"),
    ("path", "RAW_PATH_ESCAPES_ARCHIVE"),
    ("future", "FUTURE_SOURCE_PROOF"),
])
def test_bad_raw_source_quarantined_without_losing_other_provider(tmp_path, damage, reason):
    database = synthetic_archives(tmp_path)
    with database.transaction() as connection:
        source = connection.execute(
            "SELECT * FROM raw_snapshots WHERE provider='api_football'").fetchone()
        if damage == "bytes":
            (tmp_path / "data/raw" / source["relative_path"]).write_bytes(b"corrupt")
        elif damage == "path":
            connection.execute("UPDATE raw_snapshots SET relative_path='../outside.json' "
                "WHERE content_hash=?", (source["content_hash"],))
        else:
            connection.execute("UPDATE raw_snapshots SET fetched_at=? WHERE content_hash=?",
                ((NOW + timedelta(seconds=1)).isoformat(), source["content_hash"]))
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert {row["provider"] for row in rows} == {"the_stats_api", "football_data_co_uk"}
    assert report["quarantined_sources"] == {"API_SNAPSHOT:" + reason: 1}
    assert report["external_requests"] == 0


def test_normalized_archive_hash_mismatch_quarantines_entire_import(tmp_path):
    database = synthetic_archives(tmp_path)
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER sports_archive_observations_no_update")
        connection.execute("UPDATE sports_archive_observations SET home_team='Tampered' "
                           "WHERE provider='the_stats_api'")
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert {row["provider"] for row in rows} == {"api_football", "football_data_co_uk"}
    assert report["quarantined_sources"] == {"ARCHIVE:CORRUPT_ARCHIVE_OBSERVATION": 1}


def test_archive_scope_cannot_cross_competitions(tmp_path):
    database = synthetic_archives(tmp_path)
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER sports_archive_imports_no_update")
        connection.execute("UPDATE sports_archive_imports SET league_code='comp_999' "
                           "WHERE provider='the_stats_api'")
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert all(row["provider"] != "the_stats_api" for row in rows)
    assert report["quarantined_sources"] == {"ARCHIVE:ARCHIVE_SCOPE_CONFLICT": 1}


def test_future_raw_receipt_cannot_hide_behind_past_archive_import(tmp_path):
    database = synthetic_archives(tmp_path)
    with database.transaction() as connection:
        connection.execute("UPDATE raw_snapshots SET fetched_at=? WHERE provider='the_stats_api'",
            ((NOW + timedelta(seconds=1)).isoformat(),))
    original = old_tables(database)
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert {row["provider"] for row in rows} == {"api_football", "football_data_co_uk"}
    assert report["quarantined_sources"] == {
        "ARCHIVE:ARCHIVE_RECEIPT_PRECEDES_RAW_RECEIPT": 1}
    assert report["external_requests"] == 0 and old_tables(database) == original


def test_archive_import_cannot_precede_raw_receipt_even_when_both_are_past(tmp_path):
    database = synthetic_archives(tmp_path)
    later_receipt = RECEIVED + timedelta(minutes=30)
    assert RECEIVED < later_receipt < NOW
    with database.transaction() as connection:
        connection.execute("UPDATE raw_snapshots SET fetched_at=? WHERE provider='the_stats_api'",
            (later_receipt.isoformat(),))
    original = old_tables(database)
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert {row["provider"] for row in rows} == {"api_football", "football_data_co_uk"}
    assert report["quarantined_sources"] == {
        "ARCHIVE:ARCHIVE_RECEIPT_PRECEDES_RAW_RECEIPT": 1}
    assert report["external_requests"] == 0 and old_tables(database) == original


def test_finished_api_regulation_scores_never_include_extra_time_or_unfinished_rows(tmp_path):
    extra = api_match(1)
    extra["fixture"]["status"]["short"] = "AET"
    extra["goals"] = {"home": 5, "away": 4}
    extra["score"]["fulltime"] = {"home": 1, "away": 1}
    unknown = api_match(2, score=None)
    upcoming = api_match(3)
    upcoming["fixture"]["status"]["short"] = "NS"
    synthetic_archives(tmp_path, api_rows=[extra, unknown, upcoming],
        include_stats=False, include_fd=False)
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert [row["provider_id"] for row in rows] == ["1", "2"]
    assert (rows[0]["home_goals"], rows[0]["away_goals"]) == (1, 1)
    assert rows[0]["score_scope"] == "REGULATION"
    assert rows[1]["home_goals"] is rows[1]["away_goals"] is None
    assert not report["quarantined_sources"]


def test_bad_finished_api_row_is_quarantined_without_discarding_valid_sibling(tmp_path):
    broken = api_match(2)
    broken["teams"]["home"]["id"] = True
    synthetic_archives(tmp_path, api_rows=[api_match(), broken], include_stats=False, include_fd=False)
    rows, report = job.prepare_existing_results(tmp_path, as_of=NOW)
    assert [row["provider_id"] for row in rows] == ["1"]
    assert not report["quarantined_sources"]
    assert sum(report["quarantined_records"].values()) == 1
    assert report["accepted_snapshots"] == {"api_football": 1}


def test_partial_backfill_exposes_rejected_rows_and_keeps_valid_results(tmp_path, monkeypatch):
    broken = api_match(2)
    broken["teams"]["home"]["id"] = True
    database = synthetic_archives(tmp_path, api_rows=[api_match(), broken],
        include_stats=False, include_fd=False)
    original = old_tables(database)
    monkeypatch.setattr(job, "inspect_league_coverage", lambda *args, **kwargs: {"leagues": []})
    report = job.backfill_existing_results(tmp_path, clock=lambda: NOW)
    assert report["status"] == "PARTIAL"
    assert report["counts"]["native_matches"] == report["counts"]["unified_matches"] == 1
    assert sum(report["preparation"]["quarantined_records"].values()) == 1
    assert report["external_requests"] == 0 and old_tables(database) == original


def prepared_view(tmp_path, api_rows=None):
    database = synthetic_archives(tmp_path, api_rows=api_rows)
    rows, _ = job.prepare_existing_results(tmp_path, as_of=NOW)
    store = HistoricalResults(database, clock=lambda: NOW)
    store.initialize()
    store.ingest(rows)
    return store.view(as_of=NOW)


def test_new_name_country_league_proposals_cannot_activate_reconciliation(tmp_path):
    view = prepared_view(tmp_path)
    links, _ = job.exact_link_requests(view, coverage("EXACT_NAME_COUNTRY_PROPOSAL"))
    assert links == []
    existing = coverage()
    existing["leagues"][0]["api_identity"]["source"] = None
    assert job.exact_link_requests(view, existing)[0] == []
    links, metrics = job.exact_link_requests(view, coverage())
    assert len(links) == metrics["exact_link_candidates"] == 1
    assert links[0]["competition_pair"] == [["api_football", "39"], ["the_stats_api", "comp_4643"]]
    assert len(links[0]["evidence"]) == 4


def test_distinct_same_provider_fixture_ids_are_ambiguous_never_merged(tmp_path):
    view = prepared_view(tmp_path, [api_match(1), api_match(2)])
    links, metrics = job.exact_link_requests(view, coverage())
    assert links == [] and metrics["ambiguous_candidates"] == 1
    assert view["counts"]["native_matches"] == view["counts"]["unified_matches"] == 4


def test_conflicted_native_identity_still_blocks_a_false_unique_exact_link(tmp_path):
    view = prepared_view(tmp_path)
    api_row = next(row for match in view["matches"] for row in match["source_records"]
                   if row["record"]["provider"] == "api_football")
    first, corrected = copy.deepcopy(api_row), copy.deepcopy(api_row)
    for row in (first, corrected):
        row["match_id"] = "conflicting-native-api-fixture-2"
        row["record"]["provider_id"] = "2"
    corrected["record"]["home_goals"] = first["record"]["home_goals"] + 1
    view["same_time_conflicts"] = {first["match_id"]: [first, corrected]}
    original = copy.deepcopy(view)
    links, metrics = job.exact_link_requests(view, coverage())
    assert links == [] and metrics["exact_link_candidates"] == 0
    assert metrics["ambiguous_candidates"] == 1
    assert view == original


def test_link_requires_oriented_teams_exact_utc_and_competition(tmp_path):
    view = prepared_view(tmp_path)
    original = copy.deepcopy(view)
    api_row = next(row for match in view["matches"] for row in match["source_records"]
                   if row["record"]["provider"] == "api_football")
    for updates in ({"home_team": "Away", "away_team": "Home"},
                    {"kickoff": "2026-10-17T15:01:00.000000+00:00"},
                    {"competition_id": "40"}, {"kickoff": None}):
        prior = copy.deepcopy(api_row["record"])
        api_row["record"].update(updates)
        assert job.exact_link_requests(view, coverage())[0] == []
        api_row["record"] = prior
    assert view == original


def test_homonymous_team_names_cannot_prove_cross_source_orientation(tmp_path):
    view = prepared_view(tmp_path)
    for match in view["matches"]:
        for row in match["source_records"]:
            row["record"].update(home_team="United", away_team="UNITED")
    links, metrics = job.exact_link_requests(view, coverage())
    assert links == []
    assert metrics["ambiguous_team_orientation"] == 2
