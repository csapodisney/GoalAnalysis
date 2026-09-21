import copy
import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.astra_review import AstraReviewer
from goal_analysis.agents.master_prompt import load_master_prompt

KEY = "test-secret-astra-key-123456"
SOURCE = "https://www.bundesliga.com/en/bundesliga/news/team-preview"


def candidate(identity="candidate-1", evidence_id="ev-1"):
    return {
        "candidate_id": identity,
        "fixture_id": "api_football:123",
        "home_team": "Home",
        "away_team": "Away",
        "market_key": "totals_2_5",
        "selection_key": "over",
        "decimal_price": 2.1,
        "quoted_at": "2026-09-20T10:00:00Z",
        "support_score": 72,
        "evidence": [
            {
                "evidence_id": evidence_id,
                "category": "historical",
                "statement": "Home scored in its last three home matches.",
                "source_id": "api_football:123",
                "observed_at": "2026-09-20T10:00:00Z",
            }
        ],
        "historical_context": {"profiles": {"home": {"matches": 12}, "away": {"matches": 12}}},
    }


def review(identity="candidate-1", evidence_id="ev-1", urls=None):
    return {
        "candidate_id": identity,
        "assessment": "support",
        "support_arguments": ["A hazai csapat közelmúltbeli gólszerzése támogatja a tippet."],
        "risk_notes": ["A kezdőcsapat még nem ismert."],
        "evidence_ids": [evidence_id],
        "source_urls": urls or [],
    }


def response(payload=None, text=None):
    return {
        "id": "resp_test",
        "model": "gpt-6-astra",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": text if text is not None else json.dumps(payload),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


def researched():
    result = response(text="Klubbeszámoló: a csapat három napot pihent; a kezdő még ismeretlen.")
    result["output"].insert(
        0,
        {
            "type": "web_search_call",
            "status": "completed",
            "action": {
                "type": "search",
                "sources": [{"type": "url", "url": SOURCE, "title": "Team preview"}],
            },
        },
    )
    result["output"][1]["content"][0]["annotations"] = [
        {"type": "url_citation", "url": SOURCE, "title": "Team preview"}
    ]
    return result


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, dict(headers), json.loads(body)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)


def test_two_bounded_calls_use_astra_references_and_never_send_key_in_body():
    transport = Transport(researched(), response({"reviews": [review(urls=[SOURCE])]}))
    data = [candidate()]
    before = copy.deepcopy(data)
    result = AstraReviewer(api_key=KEY, transport=transport).review(data, "2026-09-21")
    assert result["status"] == "COMPLETE"
    assert result["research_status"] == "COMPLETE_WITH_SOURCES"
    assert result["sources"] == [{"url": SOURCE, "title": "Team preview"}]
    assert data == before
    assert result["usage"]["requests"] == 2
    assert result["usage"]["input_tokens"] == 240
    assert result["usage"]["output_tokens"] == 80
    assert result["usage"]["cached_tokens"] == 60
    assert result["usage"]["reasoning_tokens"] == 20
    assert result["usage"]["web_search_calls"] == 1
    for url, headers, body in transport.calls:
        assert url == "https://api.openai.com/v1/responses"
        assert headers["Authorization"] == f"Bearer {KEY}"
        assert KEY not in json.dumps(body)
        assert body["model"] == "gpt-6-astra"
        assert body["reasoning"] == {"effort": "medium"}
        assert body["store"] is False
        assert body["max_output_tokens"] <= 8000
    research, structured = [call[2] for call in transport.calls]
    assert research["max_tool_calls"] == 4
    assert research["include"] == ["web_search_call.action.sources"]
    assert "tools" not in structured
    assert structured["text"]["format"]["strict"] is True
    assert structured["text"]["format"]["schema"]["additionalProperties"] is False
    assert "historical_context" in json.loads(structured["input"])["candidates"][0]


def test_missing_or_explicitly_blank_key_never_uses_transport(monkeypatch):
    transport = Transport()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (
        AstraReviewer(transport=transport).review([candidate()], "2026-09-21")["status"]
        == "UNAVAILABLE"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "real-environment-key")
    reviewer = AstraReviewer(api_key="", transport=transport)
    assert reviewer.review([candidate()], "2026-09-21")["status"] == "UNAVAILABLE"
    assert reviewer.check_connection()["verified"] is False
    assert not transport.calls


def test_disabled_search_one_call_and_explicit_status():
    transport = Transport(response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, transport=transport, web_search=False).review(
        [candidate()], "2026-09-21", feedback={"settled_tickets": 3, "warning": "Small sample"}
    )
    assert result["status"] == "COMPLETE"
    assert result["research_status"] == "DISABLED"
    assert result["usage"]["requests"] == 1
    assert result["sources"] == []
    assert "tools" not in transport.calls[0][2]


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
        lambda r: r.update(status="failed", error={"message": "secret provider details"}),
        lambda r: r["output"][0]["content"].append({"type": "refusal", "refusal": "No"}),
        lambda r: r["output"][0].update(status="incomplete"),
        lambda r: r["output"][0]["content"][0].update(text="```json\n{}\n```"),
        lambda r: r["output"][0]["content"][0].update(text=""),
    ],
)
def test_incomplete_refused_and_invalid_json_outputs_are_not_accepted(change):
    output = response({"reviews": [review()]})
    change(output)
    result = AstraReviewer(api_key=KEY, transport=Transport(output), web_search=False).review(
        [candidate()], "2026-09-21"
    )
    assert result["status"] == "FAILED"
    assert result["reviews"] == []
    assert "secret provider details" not in json.dumps(result)
    assert result["usage"]["requests"] == 1


@pytest.mark.parametrize(
    "reviews",
    [
        [],
        [review("invented-id")],
        [review(), review()],
        [{**review(), "decimal_price": 9.99}],
        [{**review(), "evidence_ids": ["invented-evidence"]}],
        [{**review(), "evidence_ids": [], "source_urls": []}],
        [{**review(), "support_arguments": []}],
        [{**review(), "assessment": "sure_win"}],
        [{**review(), "risk_notes": "not a list"}],
        [{**review(), "assessment": {"malformed": True}}],
    ],
)
def test_unknown_ids_evidence_invalid_fields_and_unsupported_claims_fail(reviews):
    result = AstraReviewer(
        api_key=KEY, web_search=False, transport=Transport(response({"reviews": reviews}))
    ).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert result["reviews"] == []


def test_evidence_cannot_be_borrowed_from_another_candidate():
    transport = Transport(
        response({"reviews": [review(evidence_id="ev-2"), review("candidate-2", "ev-2")]})
    )
    result = AstraReviewer(api_key=KEY, web_search=False, transport=transport).review(
        [candidate(), candidate("candidate-2", "ev-2")], "2026-09-21"
    )
    assert result["status"] == "FAILED"


@pytest.mark.parametrize(
    "url",
    [
        "https://invented.example.com/news",
        "javascript:alert(1)",
        "http://127.0.0.1/a",
        "http://169.254.169.254/metadata",
        "https://localhost/",
        "https://example.com/?api_key=secret",
        "https://user:pass@example.com/x",
        "https://example.com/\n<script>",
    ],
)
def test_invented_or_unsafe_source_urls_fail(url):
    transport = Transport(researched(), response({"reviews": [review(urls=[url])]}))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert result["reviews"] == []


def test_research_without_actual_tool_sources_is_not_claimed_complete():
    transport = Transport(response(text="Trust me, I searched."))
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert len(transport.calls) == 2
    assert result["reviews"] == []


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(f"could not connect: {KEY}"),
        urllib.error.HTTPError("https://api.openai.com", 401, KEY, {}, io.BytesIO(KEY.encode())),
        urllib.error.HTTPError("https://api.openai.com", 429, KEY, {}, io.BytesIO(KEY.encode())),
    ],
)
def test_provider_errors_and_credentials_do_not_leak(error):
    result = AstraReviewer(api_key=KEY, transport=Transport(error), web_search=False).review(
        [candidate()], "2026-09-21"
    )
    assert result["status"] == "FAILED"
    assert KEY not in json.dumps(result)
    assert result["usage"]["requests"] == 1


@pytest.mark.parametrize("default_transport", [False, True])
@pytest.mark.parametrize(
    "code,kind,expected",
    [
        ("credit_balance_exhausted", "insufficient_quota", "No prepaid API credits"),
        ("organization_spend_limit_exceeded", "insufficient_quota", "organization API spend"),
        ("project_spend_limit_exceeded", "insufficient_quota", "project API spend"),
        ("organization_usage_limit_exceeded", "insufficient_quota", "OpenAI-assigned usage"),
        ("insufficient_quota", "insufficient_quota", "API quota is unavailable"),
        (None, "insufficient_quota", "API quota is unavailable"),
        ("rate_limit_exceeded", "rate_limit_error", "Retry after at least 17 seconds"),
        ("slow_down", "rate_limit_error", "Retry after at least 17 seconds"),
        (KEY, KEY, "quota or rate limit reached"),
    ],
)
def test_http429_classifies_without_leaking_or_retrying(
    monkeypatch, default_transport, code, kind, expected
):
    body = io.BytesIO(json.dumps({"error": {
        "code": code, "type": kind, "message": KEY, "param": KEY,
    }}).encode())
    error = urllib.error.HTTPError(
        "https://api.openai.com", 429, KEY, {"Retry-After": "17"}, body
    )
    transport = Transport(error)
    if default_transport:
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *args, **kwargs: transport(None, {}, b"{}")
        )
    result = AstraReviewer(
        api_key=KEY, transport=None if default_transport else transport
    ).check_connection()
    assert result["status"] == "FAILED" and result["verified"] is False
    assert expected in result["message"]
    assert KEY not in json.dumps(result)
    assert len(transport.calls) == 1
    assert result["usage"]["requests"] == 1
    assert body.closed


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"error":null}', b"x" * 17000])
def test_unreadable_http429_keeps_generic_safe_diagnostic(body):
    error = urllib.error.HTTPError("https://api.openai.com", 429, KEY, {}, io.BytesIO(body))
    result = AstraReviewer(api_key=KEY, transport=Transport(error)).check_connection()
    assert result["status"] == "FAILED"
    assert "quota or rate limit reached" in result["message"]
    assert KEY not in json.dumps(result)


def test_reflected_secret_is_discarded_and_not_cached(tmp_path):
    output = response({"reviews": [{**review(), "risk_notes": [KEY]}]})
    result = AstraReviewer(
        api_key=KEY, transport=Transport(output), web_search=False, cache_dir=tmp_path
    ).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert KEY not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def test_cache_saves_calls_but_changed_facts_and_expired_news_force_refresh(tmp_path):
    transport = Transport(*(response({"reviews": [review()]}) for _ in range(3)))
    reviewer = AstraReviewer(api_key=KEY, transport=transport, web_search=False, cache_dir=tmp_path)
    first = reviewer.review([candidate()], "2026-09-21")
    second = reviewer.review([candidate()], "2026-09-21")
    assert first["usage"]["requests"] == 1
    assert second["usage"]["requests"] == 0
    assert second["usage"]["input_tokens"] == 0
    assert second["usage"]["cache_hit"] is True
    assert second["original_usage"]["requests"] == 1
    changed = {**candidate(), "decimal_price": 2.2}
    assert reviewer.review([changed], "2026-09-21")["usage"]["requests"] == 1
    for path in tmp_path.glob("*.json"):
        content = json.loads(path.read_text())
        content["reviewed_at"] = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
        path.write_text(json.dumps(content))
    assert reviewer.review([candidate()], "2026-09-21")["usage"]["requests"] == 1
    assert len(transport.calls) == 3


def test_check_connection_actually_checks_model_and_marker():
    transport = Transport(response(text="ARTHUR_OK"), response(text="not the marker"))
    reviewer = AstraReviewer(api_key=KEY, transport=transport)
    result = reviewer.check_connection()
    assert result["status"] == "COMPLETE"
    assert result["verified"] is True
    assert result["usage"]["requests"] == 1
    assert transport.calls[0][2]["reasoning"] == {"effort": "low"}
    assert "tools" not in transport.calls[0][2]
    assert reviewer.check_connection()["verified"] is False


def test_connection_does_not_accept_a_different_model():
    output = response(text="ARTHUR_OK")
    output["model"] = "another-model"
    result = AstraReviewer(api_key=KEY, transport=Transport(output)).check_connection()
    assert result["status"] == "FAILED"
    assert result["verified"] is False


def test_history_is_compact_without_losing_sample_and_workload_context():
    data = candidate()
    data["historical_context"] = {
        "profiles": {
            "home": {
                "historical_20": {
                    "sample_size": 20,
                    "market_hits": 12,
                    "fixture_ids": ["a", "b"],
                    "source_ids": ["source-a"],
                    "results_newest_first": ["W", "D"],
                },
                "observed_workload": {"observed_matches_7d": 2, "actual_rest_hours": None},
            }
        },
        "history_digest": "abc123",
    }
    before = copy.deepcopy(data)
    transport = Transport(response({"reviews": [review()]}))
    result = AstraReviewer(api_key=KEY, web_search=False, transport=transport).review(
        [data], "2026-09-21"
    )
    assert result["status"] == "COMPLETE"
    packed = json.loads(transport.calls[0][2]["input"])["candidates"][0]["historical_context"]
    sample = packed["profiles"]["home"]["historical_20"]
    assert sample["sample_size"] == 20
    assert sample["market_hits"] == 12
    assert sample["results_newest_first"] == ["W", "D"]
    assert "fixture_ids" not in sample and "source_ids" not in sample
    assert packed["profiles"]["home"]["observed_workload"]["observed_matches_7d"] == 2
    assert packed["history_digest"] == "abc123"
    assert data == before


def test_runtime_prompt_manifest_mismatch_prevents_network(tmp_path, monkeypatch):
    import goal_analysis.agents.astra_review as module

    (tmp_path / "research.txt").write_text("Altered prompt", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"prompt_version": "arthur-portfolio-v3", "runtime_prompts": {"research.txt": "wrong"}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROMPT_ROOT", tmp_path)
    transport = Transport()
    result = AstraReviewer(api_key=KEY, transport=transport).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert not transport.calls


@pytest.mark.parametrize(
    "candidates,target,feedback",
    [
        ([], "2026-09-21", None),
        ([candidate(), candidate()], "2026-09-21", None),
        ([candidate()], "not-a-date", None),
        ([candidate()], "2026-09-21", {"password": "secret"}),
        ([candidate()], "2026-09-21", {"note": "x" * 12001}),
        ([{**candidate(), "decimal_price": float("nan")}], "2026-09-21", None),
    ],
)
def test_invalid_or_oversized_input_never_calls_api(candidates, target, feedback):
    transport = Transport()
    result = AstraReviewer(api_key=KEY, transport=transport).review(candidates, target, feedback)
    assert result["status"] == "FAILED"
    assert not transport.calls


def test_portfolio_master_is_versioned_and_old_prompt_still_loads():
    root = Path(__file__).resolve().parents[1] / "config" / "prompts"
    current = load_master_prompt(root, "arthur-portfolio-v3")
    previous = load_master_prompt(root, "arthur-pentagram-v2.1")
    assert current.activation_status == "active_in_portfolio_pipeline"
    assert previous.sha256 == "40a4c6812cbf42375055f8ab5a6a1f1b8ca256d0d974aba1f92ab3bc7ffe594d"
