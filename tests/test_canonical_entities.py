from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from goal_analysis.normalization.canonical import (
    CanonicalBookmaker, CanonicalCompetition, CanonicalFixture, CanonicalMarket,
    CanonicalOutcome, CanonicalPlayer, CanonicalSeason, CanonicalTeam,
    canonical_id, entity_from_record, utc, validate_graph,
)


def identity(kind, key="1"):
    return canonical_id(kind, "api_football:" + key)


def graph():
    competition = CanonicalCompetition(entity_id=identity("competition"), name="Bundesliga", country="Germany")
    season = CanonicalSeason(entity_id=identity("season"), competition_id=competition.entity_id, label="2026/27")
    home = CanonicalTeam(entity_id=identity("team"), name="Home")
    away = CanonicalTeam(entity_id=identity("team", "2"), name="Away")
    player = CanonicalPlayer(entity_id=identity("player"), name="Player")
    fixture = CanonicalFixture(entity_id=identity("fixture"), competition_id=competition.entity_id,
        season_id=season.entity_id, home_team_id=home.entity_id, away_team_id=away.entity_id,
        kickoff="2026-10-20T20:00:00+02:00", status="SCHEDULED")
    market = CanonicalMarket(entity_id=identity("market"), fixture_id=fixture.entity_id,
        market_key="totals", period="FULL_TIME", settlement="REGULATION", line="2.50")
    outcome = CanonicalOutcome(entity_id=identity("outcome"), market_id=market.entity_id, selection_key="over")
    bookmaker = CanonicalBookmaker(entity_id=identity("bookmaker"), name="Bet365")
    return [competition, season, home, away, player, fixture, market, outcome, bookmaker]


def test_eight_entity_kinds_roundtrip_graph_and_utc():
    entities = graph()
    assert len({e.KIND for e in entities}) == 8
    assert len(validate_graph(entities)) == 9
    for entity in entities:
        record = entity.to_record()
        original = deepcopy(record)
        assert entity_from_record(record) == entity
        assert record == original
    fixture, market = entities[5:7]
    assert fixture.kickoff == datetime(2026, 10, 20, 18, tzinfo=UTC)
    assert fixture.kickoff.tzinfo is UTC
    assert market.line == "2.5"
    with pytest.raises(FrozenInstanceError):
        fixture.status = "FINISHED"


def test_same_names_or_ids_across_providers_are_not_identity_evidence():
    assert canonical_id("team", "api_football:1") != canonical_id("team", "the_stats_api:1")
    assert canonical_id("team", "api_football:1") != canonical_id("player", "api_football:1")
    first = CanonicalTeam(entity_id=identity("team"), name="United")
    second = CanonicalTeam(entity_id=identity("team", "2"), name="United")
    assert first.entity_id != second.entity_id
    assert canonical_id("team", "source:ABC") != canonical_id("team", "source:abc")


@pytest.mark.parametrize("bad", ["2026-10-20T20:00:00", datetime(2026, 10, 20), None, True])
def test_unknown_timezone_is_not_assumed_utc(bad):
    with pytest.raises(ValueError):
        utc(bad)


def test_unknown_archive_timezone_is_retained_but_not_executable_kickoff():
    fixture = replace(graph()[5], kickoff=None, match_date="2026-10-20", kickoff_local_time="20:00")
    assert fixture.kickoff is None
    assert entity_from_record(fixture.to_record()) == fixture
    with pytest.raises(ValueError):
        replace(fixture, match_date=None)


@pytest.mark.parametrize("line", ["NaN", "Infinity", "garbage", 2.5, True])
def test_nonfinite_or_untyped_market_lines_rejected(line):
    with pytest.raises(ValueError):
        replace(graph()[6], line=line)


def test_graph_rejects_dangling_wrong_scope_and_conflicting_links():
    entities = graph()
    with pytest.raises(ValueError, match="unresolved"):
        validate_graph(entities[1:])
    with pytest.raises(ValueError):
        replace(entities[5], home_team_id=entities[4].entity_id)
    with pytest.raises(ValueError):
        replace(entities[5], away_team_id=entities[2].entity_id)
    with pytest.raises(ValueError, match="conflicting"):
        validate_graph([*entities, replace(entities[2], name="Contradiction")])
    other = replace(entities[0], entity_id=identity("competition", "2"))
    with pytest.raises(ValueError, match="season/competition"):
        validate_graph([*entities[:5], replace(entities[5], competition_id=other.entity_id), *entities[6:], other])


def test_record_rejects_unknown_schema_and_fields():
    record = graph()[0].to_record()
    record["schema_version"] = 2
    with pytest.raises(ValueError):
        entity_from_record(record)


@pytest.mark.parametrize("index,field", [(1, "competition_id"), (5, "season_id"),
                                       (6, "fixture_id"), (7, "market_id")])
def test_required_relationships_cannot_be_missing(index, field):
    with pytest.raises(ValueError):
        replace(graph()[index], **{field: None})


def test_decimal_line_is_lossless_and_record_kind_is_typed():
    line = "1.12345678901234567890123456789"
    assert replace(graph()[6], line=line).line == line
    with pytest.raises(ValueError):
        entity_from_record({"schema_version": 1, "kind": [], "data": {}})
    record = graph()[0].to_record()
    record["data"]["invented"] = "unverified"
    with pytest.raises(ValueError):
        entity_from_record(record)
