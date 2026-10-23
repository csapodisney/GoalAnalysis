"""Research-stage provenance survives review/cache without exposing backend logs."""
import json

import pytest
from test_astra_review import KEY, SOURCE, Transport, candidate, researched, response, review

from goal_analysis.agents.astra_review import AstraReviewer, _research_diagnostics


def diagnostics(status, *, completed=0, accepted=0):
    return {
        "status": status, "attempted_searches": 2, "completed_searches": completed,
        "returned_source_count": 1, "accepted_source_count": accepted, "cli_version": "0.159.2",
    }


def research_response(stage, *, with_sources=False):
    output = researched() if with_sources else response(text="Research not verified.")
    output["arthur_research"] = {"diagnostics": stage, "fixture_context": [], "web_odds": []}
    return output


@pytest.mark.parametrize("stage,expected", [
    ("NO_COMPLETED_SEARCH", "nem fejeződött be igazolható keresés"),
    ("NO_ACCEPTED_SOURCES", "nincs elfogadott, ellenőrizhető forrás"),
    ("INVALID_RESEARCH_JSON", "strukturált válasza nem volt feldolgozható"),
])
def test_completed_data_review_retains_specific_unavailable_research_stage(tmp_path, stage, expected):
    metadata = diagnostics(stage)
    transport = Transport(research_response(metadata), response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport, cache_dir=tmp_path).review(
        [candidate()], "2026-09-21",
    )
    assert result["status"] == "COMPLETE"
    assert result["research_status"] == "UNAVAILABLE"
    assert result["research_diagnostics"] == metadata
    assert expected in result["message"]
    assert result["usage"]["requests"] == len(transport.calls) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("invalid_final", [
    response(text="not valid final JSON"),
    response({"reviews": [review(identity="unknown-candidate")]}),
])
def test_research_diagnostics_survive_final_review_failure(invalid_final):
    metadata = diagnostics("NO_COMPLETED_SEARCH")
    transport = Transport(research_response(metadata), invalid_final)
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert result["research_status"] == "UNAVAILABLE"
    assert result["research_diagnostics"] == metadata
    assert result["reviews"] == []
    assert result["usage"]["requests"] == len(transport.calls) == 2


def test_research_diagnostics_survive_a_research_text_contract_failure():
    metadata = diagnostics("NO_COMPLETED_SEARCH")
    failed = research_response(metadata)
    failed["status"] = "failed"
    transport = Transport(failed)
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert result["research_status"] == "UNAVAILABLE"
    assert result["research_diagnostics"] == metadata
    assert len(transport.calls) == 1


def test_verified_diagnostics_survive_cache_and_cache_sanitizes_unknown_fields(tmp_path):
    metadata = diagnostics("VERIFIED", completed=1, accepted=1)
    transport = Transport(research_response(metadata, with_sources=True),
                          response({"reviews": [review(urls=[SOURCE])]}))
    reviewer = AstraReviewer(api_key=KEY, transport=transport, cache_dir=tmp_path)
    first = reviewer.review([candidate()], "2026-09-21")
    assert first["research_status"] == "COMPLETE_WITH_SOURCES"
    assert first["research_diagnostics"] == metadata
    cache = next(tmp_path.glob("*.json"))
    persisted = json.loads(cache.read_text(encoding="utf-8"))
    assert persisted["research_diagnostics"] == metadata
    persisted["research_diagnostics"]["stderr"] = "synthetic-private-backend-log"
    cache.write_text(json.dumps(persisted), encoding="utf-8")
    second = reviewer.review([candidate()], "2026-09-21")
    assert second["research_diagnostics"] == metadata
    assert second["usage"]["cache_hit"] is True
    assert second["usage"]["requests"] == 0
    assert len(transport.calls) == 2
    assert "synthetic-private-backend-log" not in json.dumps(second)


def test_diagnostic_metadata_does_not_establish_search_verification_or_add_review_input():
    metadata = diagnostics("VERIFIED", completed=1, accepted=1)
    metadata.update(stderr="synthetic-private-backend-log", api_token="synthetic-private-token")
    transport = Transport(research_response(metadata), response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "COMPLETE" and result["research_status"] == "UNAVAILABLE"
    assert result["usage"]["web_search_calls"] == 0
    assert result["research_diagnostics"] == diagnostics("VERIFIED", completed=1, accepted=1)
    assert "synthetic-private" not in json.dumps(result)
    assert "synthetic-private" not in transport.calls[1][2]["input"]


@pytest.mark.parametrize("metadata", [None, [], "raw backend error", {}, {"status": []},
                                      {"status": "raw private status"}])
def test_invalid_or_unknown_diagnostics_are_ignored_without_breaking_review(metadata):
    transport = Transport(research_response(metadata), response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "COMPLETE"
    assert "research_diagnostics" not in result


def test_diagnostics_allow_only_bounded_counts_and_version_metadata():
    metadata = diagnostics("NO_COMPLETED_SEARCH")
    metadata.update(attempted_searches=True, completed_searches=-1,
                    returned_source_count=1.5, accepted_source_count=1_000_001,
                    cli_version="private-token", command_line="private command", nested={"token": "secret"})
    assert _research_diagnostics(metadata) == {"status": "NO_COMPLETED_SEARCH"}


def test_absent_diagnostics_keep_original_review_contract():
    transport = Transport(response(text="No search."), response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "COMPLETE" and result["research_status"] == "UNAVAILABLE"
    assert "research_diagnostics" not in result
