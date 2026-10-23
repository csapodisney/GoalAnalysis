from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.motivation_expert import analyze_motivation, compact_motivation_context
from goal_analysis.agents.motivation_standings import NAMESPACE, collect_standings
from goal_analysis.providers.base import ProviderError
from goal_analysis.storage import CacheStore, Database

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)
RECEIVED = NOW + timedelta(seconds=3)


def fixture(league=78, fid="f1"):
    return {"fixture_id": fid, "api_football_league_id": league, "season": 2026,
            "api_football_home_team_id": "1", "api_football_away_team_id": "2"}


def payload(league=78):
    return {"errors": [], "paging": {"total": 1}, "response": [{"league": {
        "id": league, "season": 2026, "standings": [[
            {"team": {"id": 1, "name": "First"}, "rank": 1, "points": 21, "goalsDiff": 12,
             "all": {"played": 10}, "form": "WWDWL", "description": "Champions League",
             "group": "League", "update": NOW.isoformat()},
            {"team": {"id": 2, "name": "Second"}, "rank": 5, "points": 16, "goalsDiff": 3,
             "all": {"played": 9}, "form": "LDWWL", "description": "Europa League",
             "group": "League", "update": NOW.isoformat()}]]}}]}


class Client:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
        self.last_usage = SimpleNamespace(remaining_day=20)

    def request(self, endpoint, params):
        self.calls.append((endpoint, params))
        if self.error:
            raise self.error
        return self.response if self.response is not None else payload(params["league"])


@pytest.fixture
def cache(tmp_path):
    db = Database(tmp_path / "data.sqlite3")
    db.initialize()
    return CacheStore(db)


def collect(cache, client=None, fixtures=None, **kwargs):
    return collect_standings(fixtures or [fixture()], client, cache, as_of=NOW,
                             clock=lambda: RECEIVED, **kwargs)


def test_shared_league_is_one_request_with_actual_receipt_and_raw_data(cache):
    client = Client()
    result = collect(cache, client, [fixture(), fixture(fid="f2")])
    assert client.calls == [("standings", {"league": 78, "season": 2026})]
    assert result["requests"] == 1
    assert result["observed_at"] == RECEIVED.isoformat()
    context = result["context_by_fixture"]["f1"]
    assert context["rules_status"] == "UNVERIFIED"
    assert context["teams"][1]["points_gap_to_group_leader"] == 5
    assert all(team["games_remaining"] is None for team in context["teams"])
    assert result["rows"][0]["raw"] == payload()["response"][0]["league"]["standings"][0][0]


def test_cached_data_reuses_original_time_and_never_calls_client(cache):
    collect(cache, Client())
    client = Client(error=AssertionError("must not call"))
    result = collect_standings([fixture()], client, cache, as_of=RECEIVED + timedelta(hours=1))
    assert result["requests"] == 0
    assert result["context_by_fixture"]["f1"]["observed_at"] == RECEIVED.isoformat()


def test_no_client_is_cache_only_and_six_hour_expiry_is_respected(cache):
    assert collect(cache)["deferred"][0]["reason"] == "NO_CLIENT"
    collect(cache, Client())
    result = collect_standings([fixture()], None, cache, as_of=RECEIVED + timedelta(hours=6))
    assert result["context_by_fixture"] == {}
    assert result["requests"] == 0


def test_future_cache_is_never_used_for_earlier_cutoff(cache):
    collect(cache, Client())
    assert collect(cache)["context_by_fixture"] == {}


@pytest.mark.parametrize("message", ["HTTP 403 secret", "HTTP 429 secret", "timeout secret"])
def test_error_stops_subsequent_calls_and_never_exposes_provider_error(cache, message):
    client = Client(error=ProviderError(message))
    result = collect(cache, client, [fixture(78), fixture(39, "f2")])
    assert result["requests"] == 1
    assert len(client.calls) == 1
    assert result["deferred"][0]["reason"] == "STOPPED_ON_ERROR"
    assert "secret" not in str(result)


def test_cached_scopes_are_still_available_after_other_scope_error(cache):
    collect(cache, Client())
    result = collect_standings([fixture(39, "f2"), fixture()], Client(error=ProviderError("error")),
                              cache, as_of=RECEIVED + timedelta(minutes=1))
    assert "f1" in result["context_by_fixture"]
    assert result["requests"] == 1


def test_request_cap_and_exhausted_daily_quota(cache):
    client = Client()
    result = collect(cache, client, [fixture(78), fixture(39, "f2")], max_requests=1)
    assert result["requests"] == 1
    assert result["deferred"][0]["reason"] == "REQUEST_CAP"
    client.last_usage.remaining_day = 0
    result = collect(cache, client)
    assert result["requests"] == 0
    assert result["deferred"][0]["reason"] == "QUOTA_EXHAUSTED"


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(errors={"quota": "bad"}),
    lambda data: data.update(paging={"total": 2}),
    lambda data: data["response"][0]["league"].update(season=2025),
    lambda data: data["response"][0]["league"].update(id=79),
    lambda data: data["response"][0]["league"]["standings"][0][0].update(points=True),
])
def test_malformed_or_wrong_scope_is_not_cached(cache, mutate):
    response = payload()
    mutate(response)
    result = collect(cache, Client(response))
    assert result["context_by_fixture"] == {}
    assert result["errors"][0]["code"] == "STANDINGS_UNAVAILABLE"
    assert cache.get(NAMESPACE, "78:2026", now=RECEIVED) is None


def test_multiple_competition_phases_are_not_arbitrarily_selected(cache):
    response = payload()
    response["response"][0]["league"]["standings"].append(
        deepcopy(response["response"][0]["league"]["standings"][0]))
    context = collect(cache, Client(response))["context_by_fixture"]["f1"]
    assert context["teams"] == []
    assert context["ambiguous_teams"] == ["1", "2"]


def test_missing_season_is_not_inferred_from_current_date(cache):
    row = fixture()
    row.pop("season")
    result = collect(cache, Client(), [row])
    assert result["requests"] == 0
    assert result["errors"] == [{"fixture_id": "f1", "code": "MISSING_SCOPE"}]


def test_cache_hash_mismatch_is_rejected(cache):
    collect(cache, Client())
    value = cache.get(NAMESPACE, "78:2026", now=RECEIVED)
    value["payload_sha256"] = canonical_sha256({"wrong": True})
    cache.set(NAMESPACE, "78:2026", value, timedelta(hours=6), now=RECEIVED)
    result = collect_standings([fixture()], None, cache, as_of=RECEIVED)
    assert result["context_by_fixture"] == {}


def test_official_context_in_expert_stays_descriptive_and_goes_to_grok(cache):
    context = collect(cache, Client())["context_by_fixture"]
    report = analyze_motivation([fixture()], as_of=RECEIVED, standings_context=context)
    row = report["fixtures"][0]
    assert row["stakes"] == ["UNKNOWN"]
    assert row["probability_adjustment"] == 0
    assert "21 pont" in row["summary_hu"]
    assert compact_motivation_context(report)["fixtures"][0]["standings_context"] == context["f1"]
    early = analyze_motivation([fixture()], as_of=NOW, standings_context=context)
    assert "standings_context" not in early["fixtures"][0]


def test_invalid_cap_is_rejected(cache):
    for cap in (True, -1, 41, 2.5):
        with pytest.raises(ValueError):
            collect(cache, Client(), max_requests=cap)


@pytest.mark.parametrize("change", [{"league_id": "79"}, {"season": 2025}, {"teams": [{}]}])
def test_descriptive_context_is_scope_checked_and_malformed_rows_do_not_crash(cache, change):
    context = collect(cache, Client())["context_by_fixture"]
    context["f1"].update(change)
    report = analyze_motivation([fixture()], as_of=RECEIVED, standings_context=context)
    assert "standings_context" not in report["fixtures"][0]
    assert report["skipped"] == {"INVALID_OR_UNAVAILABLE_CONTEXT": 1}
