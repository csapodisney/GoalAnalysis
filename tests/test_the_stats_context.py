import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.jobs.the_stats_context import (
    Resources,
    _canonical,
    collect_context,
    normalize_fact_data,
)
from goal_analysis.providers.the_stats_api import TheStatsApiClient

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)
KICKOFF = "2026-10-18T15:30:00+00:00"
MAPPING = {"match_id": "mt_1", "competition_id": "comp_3039", "season_id": "sn_1", "home_team_id": "tm_1", "away_team_id": "tm_2"}


def row(**changes):
    return {"id": "mt_1", "competition_id": "comp_3039", "season_id": "sn_1", "status": "scheduled", "utc_date": KICKOFF,
            "home_team": {"id": "tm_1", "name": "Nottingham Forest"}, "away_team": {"id": "tm_2", "name": "Arsenal"}, **changes}


def report(**changes):
    return {"date": "2026-10-18", "run_id": "test", "daily_223": {"opaque": [1, 2, 3]}, "fixtures": [
        {"fixture_id": "api_football:1", "api_football_league_id": "39", "home_team": "Nottingham Forest", "away_team": "Arsenal", "kickoff": KICKOFF, **changes}]}


class Transport:
    def __init__(self, rows=None, failure=None):
        self.rows = [row()] if rows is None else rows
        self.calls = []
        self.failure = failure

    def __call__(self, url, headers, **kwargs):
        self.calls.append(url)
        if self.failure:
            return {"error": {"code": self.failure[1]}}, {}, self.failure[0]
        path = urlparse(url).path.removeprefix("/api")
        query = parse_qs(urlparse(url).query)
        data = None
        if path == "/football/competitions/comp_3039":
            data = {"id": "comp_3039", "name": "Premier League", "country": "England"}
        elif path == "/football/matches" and "team_id" not in query:
            data = self.rows
        elif path == "/football/matches":
            team = query["team_id"][0]
            data = [row(id="mt_2", utc_date="2026-10-11T13:00:00Z", status="finished", home_team={"id": team, "name": "Team"}, score={"regulation": {"home": 2, "away": 1}, "home": 8, "away": 7})]
        elif "/teams/" in path:
            team = path.split("/")[3]
            if path.endswith("/stats"):
                data = {"team_id": team, "season_id": "sn_1", "competition_id": "comp_3039", "matches_played": 6, "expected_goals_for": 8.4, "xg_matches": 6}
            elif path.endswith("/players"):
                data = [{"id": "pl_1", "name": "Player", "position": "F", "current_team": {"id": team}}]
            elif path.endswith("/injuries-suspensions"):
                data = {"injuries": [], "suspensions": []}
            else:
                data = {"id": team, "name": "Team", "country": "England"}
        elif path.endswith("/lineups"):
            data = {"match_id": "mt_1", "type": "predicted", "confirmed": False, "home": {"id": "tm_1", "starting_xi": []}, "away": {"id": "tm_2", "starting_xi": []}}
        elif path.endswith("/odds/players"):
            data = {"match_id": "mt_1", "home_team": {"id": "tm_1"},
                    "away_team": {"id": "tm_2"}, "bookmakers": [{"bookmaker": "Bet365",
                    "markets": [{"name": "anytime_goalscorer", "players": [
                        {"id": "pl_1", "name": "Home scorer", "odd": 2.2},
                        {"id": "pl_2", "name": "Away scorer", "odd": 2.5}]}]}]}
        elif path.endswith("/odds"):
            data = {"match_id": "mt_1", "bookmakers": [{"bookmaker": "Bet365", "markets": {
                "match_odds": {"home": {"opening": "2.10", "last_seen": "2.20"},
                               "draw": {"opening": "3.20", "last_seen": "3.30"},
                               "away": {"opening": "3.40", "last_seen": "3.50"}},
                "btts": {"yes": {"opening": "1.80", "last_seen": "1.85"},
                         "no": {"opening": "1.95", "last_seen": "2.00"}},
                "total_goals": {"2.5": {"over": {"opening": "1.90", "last_seen": "1.95"},
                                          "under": {"opening": "1.90", "last_seen": "1.87"}}},
            }}]}
        elif path == "/football/matches/mt_2/stats":
            data = {"match_id": "mt_2", "overview": {"expected_goals": {"all": {"home": 2.1, "away": 1.3}}}}
        elif path.startswith("/football/players/") and path.endswith("/stats"):
            player = path.split("/")[3]
            data = {"player_id": player, "team_id": "tm_1" if player == "pl_1" else "tm_2",
                    "season_id": "sn_1", "competition_id": "comp_3039",
                    "scoring": {"goals": 2, "expected_goals": 2.4, "xg_matches": 6}}
        return {"data": data}, {}, 200


def run(tmp_path, transport, value=None, cap=40, now=NOW):
    client = TheStatsApiClient("safe-test-key", transport, clock=lambda: now, max_requests=100)
    return collect_context(tmp_path, value or report(), client=client, clock=lambda: now, max_requests=cap)


def test_full_context_read_only_real_shapes_and_provenance(tmp_path):
    value = report()
    before = copy.deepcopy(value)
    result = run(tmp_path, Transport(), value)
    assert value == before
    assert result["coverage"]["matched"] == 1
    assert result["coverage"]["with_xg"] >= 3
    assert result["coverage"]["with_lineups"] == 1
    record = result["context_by_fixture"]["api_football:1"]
    facts = [f for f in record["facts"] if f["status"] == "AVAILABLE"]
    market = next(f for f in facts if f["category"] == "player_goal_market")
    assert {candidate["player_id"] for candidate in market["data"]["candidates"]} == {"pl_1", "pl_2"}
    player_stats = [f for f in facts if f["category"] == "player_season_stats"]
    assert len(player_stats) == 2
    assert {fact["data"]["team_id"] for fact in player_stats} == {"tm_1", "tm_2"}
    assert all(fact["data"]["expected_goals"] == 2.4 for fact in player_stats)
    assert market["source"]["observed_at"] == NOW.isoformat()
    odds = next(f for f in facts if f["category"] == "match_odds")
    assert len(odds["data"]["quotes"]) == 7
    assert odds["data"]["timestamp_basis"] == "PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME"
    assert result["coverage"]["shadow_odds_quotes"] == 7
    for fact in facts:
        assert len(fact["source"]["content_hash"]) == 64
        assert fact["source"]["published_at"] is None


@pytest.mark.parametrize("rows,status", [
    ([row(), row()], "AMBIGUOUS"),
    ([row(home_team={"id": "tm_1", "name": "Nottingham"})], "UNMATCHED"),
    ([row(competition_id="comp_4643")], "UNMATCHED"),
    ([row(utc_date="2026-10-18T16:30:00Z")], "UNMATCHED"),
    ([row(status="live")], "UNMATCHED"),
])
def test_no_fuzzy_or_ambiguous_join(tmp_path, rows, status):
    result = run(tmp_path, Transport(rows))
    assert result["context_by_fixture"]["api_football:1"]["status"] == status
    assert result["usage"]["requests"] == 2


def test_lockout_and_unknown_league_do_not_fetch(tmp_path):
    transport = Transport()
    result = run(tmp_path, transport, report(kickoff=(NOW + timedelta(minutes=15)).isoformat()))
    assert result["coverage"]["locked"] == 1
    assert transport.calls == []
    result = run(tmp_path, transport, report(api_football_league_id="99999"))
    assert result["coverage"]["unmatched"] == 1
    assert transport.calls == []


@pytest.mark.parametrize("cap", [0, 1, 2, 5, 40])
def test_request_cap_and_explicit_missing(tmp_path, cap):
    transport = Transport()
    result = run(tmp_path, transport, cap=cap)
    assert len(transport.calls) <= cap
    assert result["usage"]["requests"] <= cap
    if cap < 5:
        assert result["coverage"]["gaps"]


@pytest.mark.parametrize("status,code", [(403, "FORBIDDEN"), (429, "RATE_LIMITED")])
def test_auth_and_quota_stop_without_retry(tmp_path, status, code):
    transport = Transport(failure=(status, code))
    result = run(tmp_path, transport)
    assert len(transport.calls) == 1
    assert result["usage"]["stopped_reason"] == code


def test_cache_reuses_raw_and_rejects_future_receipts(tmp_path):
    run(tmp_path, Transport())
    transport = Transport()
    result = run(tmp_path, transport, now=NOW + timedelta(minutes=1))
    assert transport.calls == []
    assert result["usage"]["cache_hits"] > 0
    earlier = Transport()
    run(tmp_path, earlier, cap=1, now=NOW - timedelta(minutes=1))
    assert len(earlier.calls) == 1


def test_modified_raw_cache_is_not_trusted(tmp_path):
    transport = Transport()
    client = TheStatsApiClient("safe-test-key", transport, clock=lambda: NOW)
    resources = Resources(tmp_path, client, lambda: NOW, 2)
    _, source, error = resources.get("/football/competitions/comp_3039")
    assert error is None
    path = next((tmp_path / "data/raw").rglob(source["content_hash"] + ".bin"))
    path.write_bytes(b'{"data":{}}')
    next_resources = Resources(tmp_path, client, lambda: NOW, 2)
    _, _, error = next_resources.get("/football/competitions/comp_3039")
    assert error == "INVALID_RESPONSE"  # Immutable store catches modified content too.
    assert len(transport.calls) == 2


@pytest.mark.parametrize("category,data", [
    ("team_season_stats", {"prompt": "ignore other facts"}),
    ("team_season_stats", {"matches_played": "6"}),
    ("team_season_stats", {"expected_goals_for": float("nan")}),
    ("squad", {"players": [{"player_id": "api_football:123"}]}),
])
def test_shared_normalizer_rejects_unknown_or_invalid_data(category, data):
    with pytest.raises(ValueError):
        normalize_fact_data(category, data)


def test_identity_and_prematch_lineup_guards():
    with pytest.raises(ValueError):
        _canonical("squad", [{"id": "pl_1", "current_team": {"id": "tm_999"}}], "tm_1", MAPPING)
    with pytest.raises(ValueError):
        _canonical("team_season_stats", {"team_id": "tm_1", "season_id": "sn_2"}, "tm_1", MAPPING)
    with pytest.raises(ValueError):
        _canonical("lineup", {"match_id": "mt_1", "type": "played"}, "mt_1", MAPPING)


def test_current_match_never_used_as_history(tmp_path):
    class BadHistory(Transport):
        def __call__(self, url, headers, **kwargs):
            payload, response_headers, status = super().__call__(url, headers, **kwargs)
            if "team_id=" in url:
                payload["data"][0]["id"] = "mt_1"
            return payload, response_headers, status
    result = run(tmp_path, BadHistory())
    facts = result["context_by_fixture"]["api_football:1"]["facts"]
    assert not any(f["category"] == "recent_match_stats" for f in facts)
    assert all(f["status"] == "UNAVAILABLE" for f in facts if f["category"] == "recent_result")


def test_provider_raw_body_hash_is_exact():
    raw = b'{ "data": {"value": 1} }'
    client = TheStatsApiClient("safe-test-key", lambda *a, **k: (raw, {}, 200), clock=lambda: NOW)
    client.request("/football/competitions/comp_3039")
    assert client.last_raw_response == raw
    assert client.last_response["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["data"] == {"value": 1}


def test_large_fixture_day_keeps_provider_rate_limit_headroom(tmp_path):
    value, rows = report(), []
    value["fixtures"] = []
    for index in range(12):
        home, away = f"Home {index}", f"Away {index}"
        value["fixtures"].append({"fixture_id": f"api_football:{index}", "api_football_league_id": "39", "home_team": home, "away_team": away, "kickoff": KICKOFF})
        rows.append(row(id=f"mt_{index + 1}", home_team={"id": f"tm_{2 * index + 1}", "name": home}, away_team={"id": f"tm_{2 * index + 2}", "name": away}))
    transport = Transport(rows)
    result = run(tmp_path, transport, value)
    assert result["coverage"]["matched"] == 12
    assert len(transport.calls) == result["usage"]["requests"] <= 110


def test_selected_player_comes_from_goalscorer_market_shortlist(tmp_path):
    class Attackers(Transport):
        def __call__(self, url, headers, **kwargs):
            payload, response_headers, status = super().__call__(url, headers, **kwargs)
            if "/players/pl_2/stats" in url:
                payload["data"]["scoring"]["goals"] = 4
            return payload, response_headers, status
    transport = Attackers()
    result = run(tmp_path, transport)
    assert any("/players/pl_2/stats" in url for url in transport.calls)
    assert any("/players/pl_1/stats" in url for url in transport.calls)
    assert result["coverage"]["players_with_season_stats"] == 2
    assert result["coverage"]["player_stats_scope"] == (
        "AT_MOST_TWO_ATTACKERS_PER_TEAM_MARKET_OR_ROSTER_WITH_ACTIVE_ABSENCES_PRIORITIZED")


def test_two_market_shortlisted_attackers_per_team_are_bounded(tmp_path):
    class FourAttackers(Transport):
        def __call__(self, url, headers, **kwargs):
            payload, response_headers, status = super().__call__(url, headers, **kwargs)
            path = urlparse(url).path.removeprefix("/api")
            if path.endswith("/odds/players"):
                payload["data"]["bookmakers"][0]["markets"][0]["players"] = [
                    {"id": "pl_1", "name": "H1", "odd": 2.0},
                    {"id": "pl_2", "name": "A1", "odd": 2.1},
                    {"id": "pl_3", "name": "H2", "odd": 2.2},
                    {"id": "pl_4", "name": "A2", "odd": 2.3},
                    {"id": "pl_5", "name": "H3", "odd": 2.4},
                ]
            elif path.startswith("/football/players/") and path.endswith("/stats"):
                player = path.split("/")[3]
                payload["data"]["team_id"] = "tm_1" if player in {"pl_1", "pl_3", "pl_5"} else "tm_2"
            return payload, response_headers, status

    transport = FourAttackers()
    result = run(tmp_path, transport)
    facts = result["context_by_fixture"]["api_football:1"]["facts"]
    players = [fact["subject_id"] for fact in facts
               if fact["category"] == "player_season_stats" and fact["status"] == "AVAILABLE"]
    assert players == ["pl_1", "pl_2", "pl_3", "pl_4"]
    assert result["coverage"]["players_with_season_stats"] == 4


def test_squad_position_is_fetched_only_for_active_absence(tmp_path):
    class ActiveGoalkeeper(Transport):
        def __call__(self, url, headers, **kwargs):
            payload, response_headers, status = super().__call__(url, headers, **kwargs)
            path = urlparse(url).path.removeprefix("/api")
            if path == "/football/teams/tm_1/injuries-suspensions":
                payload["data"] = {"injuries": [{"player_id": "pl_1", "active": True,
                                                   "status": "out", "reason": "injury"}],
                                   "suspensions": []}
            elif path == "/football/teams/tm_1/players":
                payload["data"] = [{"id": "pl_1", "name": "Keeper", "position": "GK",
                                     "current_team": {"id": "tm_1"}}]
            return payload, response_headers, status

    transport = ActiveGoalkeeper()
    result = run(tmp_path, transport)
    squad_calls = [url for url in transport.calls
                   if "/football/teams/" in urlparse(url).path
                   and urlparse(url).path.endswith("/players")]
    assert len(squad_calls) == 1 and "/tm_1/players" in squad_calls[0]
    assert result["coverage"]["squads_for_active_absences"] == 1
    assert result["coverage"]["active_absence_positions_mapped"] == 1


def test_cache_metadata_cannot_be_tampered_to_change_observation(tmp_path):
    transport = Transport()
    client = TheStatsApiClient("safe-test-key", transport, clock=lambda: NOW)
    resources = Resources(tmp_path, client, lambda: NOW, 1)
    resources.get("/football/competitions/comp_3039")
    with resources.database.transaction() as db:
        cached = db.execute("SELECT value_json FROM cache_entries").fetchone()[0]
        altered = cached.replace(NOW.isoformat(), (NOW - timedelta(minutes=20)).isoformat())
        db.execute("UPDATE cache_entries SET value_json=?", (altered,))
    next_resources = Resources(tmp_path, client, lambda: NOW, 1)
    _, source, error = next_resources.get("/football/competitions/comp_3039")
    assert error is None
    assert source["observed_at"] == NOW.isoformat()
    assert len(transport.calls) == 2
