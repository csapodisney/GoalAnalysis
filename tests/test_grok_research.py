import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from goal_analysis.agents.grok_research import GrokAdvisor

PROFILE = "tipster_archiv_nora"
CUTOFF = datetime(2026, 10, 17, 12, tzinfo=UTC)
PUBLISHED = CUTOFF - timedelta(hours=1)


def post_url(when=PUBLISHED, author="ClubNews"):
    snowflake = (int(when.timestamp() * 1000) - 1288834974657) << 22
    return f"https://x.com/{author}/status/{snowflake}"


def packet():
    return {"date": "2026-10-18", "cutoff": CUTOFF.isoformat(),
            "fixtures": [{"fixture_id": "123", "home_team": "Home FC", "away_team": "Away FC",
                          "kickoff": (CUTOFF + timedelta(hours=24)).isoformat()}],
            "recommendations": [{"profile_id": PROFILE, "recommendation_id": "rec-1",
                                 "legs": [{"fixture_id": "123", "market_key": "totals",
                                           "selection_key": "over25", "period": "FT",
                                           "decimal_price": 2.0, "model_probability": .55}]}]}


def answer():
    return {"claims": [{"claim_id": "claim-1", "fixture_id": "123", "kind": "INJURY",
                        "claim": "A beszámoló szerint a kezdő kapus kérdéses.",
                        "source_url": post_url(), "author": "ClubNews",
                        "published_at": PUBLISHED.isoformat()}],
            "critiques": [{"recommendation_id": "rec-1", "profile_id": PROFILE,
                           "verdict": "QUESTIONABLE", "strengths": "A modell pozitív értéket jelez.",
                           "risks": "A kezdőcsapatot ellenőrizni kell.",
                           "flagged_legs": [{"leg_index": 0, "fixture_id": "123",
                                             "reason": "A kezdő kapus kérdéses.",
                                             "claim_ids": ["claim-1"]}],
                           "claim_ids": ["claim-1"]}]}


def keyed_answer():
    out = answer()
    row = out["critiques"][0]
    row.pop("profile_id")
    rid = row.pop("recommendation_id")
    for flag in row["flagged_legs"]:
        flag.pop("fixture_id")
    out["critiques"] = {rid: row}
    return out


def response(output=None, citations=None):
    return {"status": "completed", "citations": [post_url()] if citations is None else citations,
            "usage": {"cost_in_usd_ticks": 25_000_000, "input_tokens": 100, "output_tokens": 50,
                      "server_side_tool_usage_details": {"x_posts_fetched": 2}},
            "output": [{"type": "message", "content": [{"type": "output_text", "text":
                         json.dumps(answer() if output is None else output)}]}]}


def review(output=None, *, citations=None):
    return GrokAdvisor(api_key="dummy-key", transport=lambda *a, **kw:
                       response(output, citations)).review(packet())


def test_request_bounds_and_blind_whitelist_preserve_input():
    data = packet()
    data["secret"] = "do-not-send"
    data["recommendations"][0]["astra_analysis"] = "do-not-send"
    original = copy.deepcopy(data)
    captured = []

    def transport(payload, **kwargs):
        captured.append((payload, kwargs))
        return response()

    result = GrokAdvisor(api_key="dummy-key", transport=transport).review(data)
    assert result["status"] == "COMPLETE"
    assert data == original
    payload, kwargs = captured[0]
    assert "do-not-send" not in json.dumps(payload)
    assert "dummy-key" not in json.dumps(payload)
    assert kwargs == {"api_key": "dummy-key", "timeout": 90}
    assert payload["store"] is False
    assert payload["max_turns"] == 2
    assert payload["max_output_tokens"] == 6500
    assert payload["tools"] == [{"type": "x_search", "from_date": "2026-10-15",
                                  "to_date": "2026-10-18", "enable_image_understanding": False,
                                  "enable_video_understanding": False}]
    assert payload["text"]["format"]["strict"] is True
    critique_schema = payload["text"]["format"]["schema"]["properties"]["critiques"]
    assert critique_schema["type"] == "object"
    assert critique_schema["required"] == ["rec-1"]
    assert critique_schema["additionalProperties"] is False
    fields = critique_schema["properties"]["rec-1"]["properties"]
    assert "profile_id" not in fields and "recommendation_id" not in fields
    assert fields["strengths"]["minLength"] == fields["risks"]["minLength"] == 1
    assert fields["strengths"]["maxLength"] == fields["risks"]["maxLength"] == 160
    flag = fields["flagged_legs"]["items"]["properties"]
    assert flag["leg_index"]["enum"] == [0]
    assert "fixture_id" not in flag
    assert result["usage"]["cost_usd"] == .0025
    assert result["claims"][0]["content_status"] == "UNVERIFIED"
    assert result["claims"][0]["provenance_status"] == "CITATION_AND_TIMESTAMP_CHECKED"


@pytest.mark.parametrize("field,value", [
    ("source_url", "https://x.com/ClubNews/status/1111111111111111111"),
    ("source_url", "https://example.com/ClubNews/status/1111111111111111111"),
    ("fixture_id", "999"), ("author", "Different"),
    ("published_at", (PUBLISHED + timedelta(minutes=1)).isoformat()),
    ("published_at", "2026-10-17T11:00:00"),
    ("claim", "xai-aaaaaaaaaaaaaaaaaaaaaaaa"),
])
def test_forged_claim_excluded_and_valid_pro_contra_cannot_trigger_action(field, value):
    out = answer()
    out["claims"][0][field] = value
    result = review(out)
    assert result["status"] == "PARTIAL"
    assert result["claims"] == []
    card = result["critiques"][0]
    assert card["strengths"] == out["critiques"][0]["strengths"]
    assert card["risks"] == out["critiques"][0]["risks"]
    assert card["actionability"] is False
    assert card["review_validation"] == "PARTIAL"
    assert card["claim_ids"] == card["flagged_legs"] == []
    assert result["diagnostics"]["claims_rejected"] == 1
    assert result["diagnostics"]["critiques_partial"] == 1
    assert sum(result["diagnostics"]["claim_rejection_reasons"].values()) == 1


@pytest.mark.parametrize("when", [CUTOFF + timedelta(seconds=1),
                                  CUTOFF - timedelta(hours=49),
                                  CUTOFF + timedelta(days=300)])
def test_real_snowflake_after_cutoff_or_outside_window_is_excluded(when):
    out = answer()
    out["claims"][0].update(source_url=post_url(when), published_at=when.isoformat())
    assert review(out, citations=[post_url(when)])["claims"] == []


def test_citation_missing_or_injecting_extra_override_is_rejected():
    assert review(citations=[])["claims"] == []
    out = answer()
    out["claims"][0]["veto_override"] = True
    assert review(out)["claims"] == []
    out = answer()
    out["critiques"][0]["probability"] = .9
    assert review(out)["critiques"] == []


def test_critique_references_only_supplied_profile_and_fixture():
    out = answer()
    out["critiques"][0]["profile_id"] = "DAILY_223"
    assert review(out)["critiques"] == []
    out["critiques"][0]["profile_id"] = PROFILE
    out["critiques"][0]["flagged_legs"] = []
    card = review(out)["critiques"][0]
    assert card["actionability"] is False
    assert card["validation_warnings"] == ["NO_VALID_FLAG"]


def test_annotations_accepted_but_not_claim_embedded_url_without_metadata():
    body = response(citations=[])
    body["output"][0]["content"][0]["annotations"] = [
        {"type": "url_citation", "url": post_url()}]
    result = GrokAdvisor(api_key="dummy-key", transport=lambda *a, **k: body).review(packet())
    assert len(result["claims"]) == 1


@pytest.mark.parametrize("change", ["unknown_profile", "lockout", "duplicate", "bad_cutoff"])
def test_invalid_packet_never_calls_provider(change):
    data = packet()
    if change == "unknown_profile":
        data["recommendations"][0]["profile_id"] = "invented_tipster"
    elif change == "lockout":
        data["fixtures"][0]["kickoff"] = (CUTOFF + timedelta(minutes=15)).isoformat()
    elif change == "duplicate":
        data["fixtures"].append(data["fixtures"][0])
    else:
        data["cutoff"] = "yesterday"
    calls = []
    result = GrokAdvisor(api_key="dummy", transport=lambda *a, **k: calls.append(1)).review(data)
    assert result["error_code"] == "INVALID_PACKET"
    assert result["usage"]["request_count"] == 0
    assert calls == []


def test_no_key_and_no_data_do_not_call(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert GrokAdvisor().review(packet())["error_code"] == "MISSING_API_KEY"
    data = packet()
    data["recommendations"] = []
    assert GrokAdvisor().review(data)["status"] == "SKIPPED_NO_DATA"


def test_malformed_answer_and_provider_error_preserve_usage_without_raw_body():
    for body in (response({"claims": "bad", "critiques": []}),
                 {**response(), "error": "xai-secret-aaaaaaaaaaaa"}):
        result = GrokAdvisor(api_key="dummy", transport=lambda *a, body=body, **k: body).review(packet())
        assert result["status"] == "UNAVAILABLE"
        assert result["usage"]["cost_usd"] == .0025
        assert "xai-secret" not in json.dumps(result)


def test_exception_never_leaks_key_or_raw_text_and_no_retry():
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Bearer raw-secret xai-test-aaaaaaaaaaaa")

    result = GrokAdvisor(api_key="raw-secret", transport=fail).review(packet())
    assert calls == [1]
    assert result["usage"]["request_count"] == 1
    assert result["usage"]["cost_usd"] is None
    assert "raw-secret" not in json.dumps(result)
    assert "aaaaaaaa" not in json.dumps(result)


def test_sdk_configuration_and_usage_mapping_without_network(monkeypatch):
    xai_sdk = pytest.importorskip("xai_sdk")
    from xai_sdk.proto import usage_pb2

    captured = {}
    usage = usage_pb2.SamplingUsage(prompt_tokens=11, completion_tokens=22, reasoning_tokens=33,
                                    cost_in_usd_ticks=5_000_000)

    class Chat:
        def append(self, message):
            captured.setdefault("messages", []).append(message)

        def sample(self):
            captured["samples"] = captured.get("samples", 0) + 1
            return SimpleNamespace(usage=usage, citations=[post_url()],
                                   server_side_tool_usage={"SERVER_SIDE_TOOL_X_SEARCH": 1},
                                   content=json.dumps(answer()), finish_reason="REASON_STOP")

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = self

        def create(self, **kwargs):
            captured["chat"] = kwargs
            return Chat()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(xai_sdk, "Client", Client)
    result = GrokAdvisor(api_key="dummy-key").review(packet())
    assert result["status"] == "COMPLETE"
    assert captured["samples"] == 1
    assert captured["closed"] is True
    assert captured["client"]["api_host"] == "api.x.ai"
    assert captured["client"]["use_insecure_channel"] is False
    options = dict(captured["client"]["channel_options"])
    assert options["grpc.enable_retries"] == 0
    assert options["grpc.service_config"] == "{}"
    assert options["grpc.max_receive_message_length"] == 1_048_576
    assert captured["chat"]["parallel_tool_calls"] is False
    assert captured["chat"]["store_messages"] is False
    assert captured["chat"]["max_turns"] == 2
    assert captured["chat"]["max_tokens"] == 6500
    assert len(captured["chat"]["tools"]) == 1
    assert captured["chat"]["tools"][0].HasField("x_search")
    assert result["usage"]["cost_usd"] == .0005
    assert result["usage"]["input_tokens"] == 11
    assert result["usage"]["output_tokens"] == 22
    assert result["usage"]["reasoning_tokens"] == 33
    assert result["usage"]["finish_reason"] == "REASON_STOP"
    assert result["usage"]["x_search_calls"] == 1
    assert result["usage"]["x_posts_fetched"] is None


def test_all_real_profiles_including_daily_get_one_complete_read_only_batch():
    from goal_analysis.config.portfolio import PROFILE_IDS

    data, out, calls = packet(), answer(), []
    template, critique = data["recommendations"][0], out["critiques"][0]
    data["recommendations"] = [{**copy.deepcopy(template), "profile_id": profile,
                                 "recommendation_id": f"rec-{i}"}
                                for i, profile in enumerate(PROFILE_IDS)]
    out["critiques"] = [{**copy.deepcopy(critique), "profile_id": profile,
                         "recommendation_id": f"rec-{i}"} for i, profile in enumerate(PROFILE_IDS)]
    original = copy.deepcopy(data)

    def transport(payload, **kwargs):
        constraints = payload["text"]["format"]["schema"]["properties"]["critiques"]
        assert set(constraints["required"]) == {f"rec-{i}" for i in range(len(PROFILE_IDS))}
        assert set(constraints["properties"]) == set(constraints["required"])
        calls.append(json.loads(payload["input"]))
        keyed = copy.deepcopy(out)
        keyed["critiques"] = {}
        for row in out["critiques"]:
            value = copy.deepcopy(row)
            rid = value.pop("recommendation_id")
            value.pop("profile_id")
            for flag in value["flagged_legs"]:
                flag.pop("fixture_id")
            keyed["critiques"][rid] = value
        return response(keyed)

    result = GrokAdvisor(api_key="dummy", transport=transport).review(data)
    assert result["status"] == "COMPLETE"
    assert len(calls) == 1
    assert len(result["critiques"]) == len(PROFILE_IDS)
    daily = next(row for row in result["critiques"] if row["profile_id"] == "daily223")
    assert daily["protected_profile"] is True
    assert daily["advisory_only"] is True
    assert all(row["advisory_only"] for row in result["critiques"])
    assert data == original


def test_partial_ticket_coverage_and_duplicate_critique_never_complete():
    data, out = packet(), answer()
    data["recommendations"].append({**copy.deepcopy(data["recommendations"][0]),
                                    "recommendation_id": "rec-2", "profile_id": "gauss"})
    out["critiques"].append(copy.deepcopy(out["critiques"][0]))
    result = GrokAdvisor(api_key="dummy", transport=lambda *a, **k: response(out)).review(data)
    assert result["status"] == "PARTIAL"
    assert result["diagnostics"]["recommendations_without_critique"] == 1
    assert result["diagnostics"]["critiques_rejected"] == 1


def test_keyed_review_binds_profile_and_fixture_locally():
    result = review(keyed_answer())
    assert result["status"] == "COMPLETE"
    card = result["critiques"][0]
    assert card["recommendation_id"] == "rec-1"
    assert card["profile_id"] == PROFILE
    assert card["flagged_legs"][0]["fixture_id"] == "123"
    assert card["actionability"] is True


@pytest.mark.parametrize("wrong_id", [False, True])
def test_keyed_missing_or_wrong_ticket_identity_is_partial_without_retry(wrong_id):
    data, out, calls = packet(), keyed_answer(), []
    data["recommendations"].append({**copy.deepcopy(data["recommendations"][0]),
                                    "recommendation_id": "rec-2", "profile_id": "gauss"})
    if wrong_id:
        out["critiques"]["unknown-rec"] = copy.deepcopy(out["critiques"]["rec-1"])

    def transport(*args, **kwargs):
        calls.append(1)
        return response(out)

    result = GrokAdvisor(api_key="dummy", transport=transport).review(data)
    assert calls == [1]
    assert result["status"] == "PARTIAL"
    assert [row["recommendation_id"] for row in result["critiques"]] == ["rec-1"]
    assert result["diagnostics"]["recommendations_without_critique"] == 1
    assert result["diagnostics"].get("critiques_rejected", 0) == int(wrong_id)
    assert "unknown-rec" not in json.dumps(result)


def test_duplicate_json_ticket_key_is_rejected_without_silent_overwrite():
    out = keyed_answer()
    value = json.dumps(out["critiques"]["rec-1"])
    body = response(out)
    body["output"][0]["content"][0]["text"] = (
        '{"claims":[],"critiques":{"rec-1":' + value + ',"rec-1":' + value + '}}')
    calls = []

    def transport(*args, **kwargs):
        calls.append(1)
        return body

    result = GrokAdvisor(api_key="dummy", transport=transport).review(packet())
    assert calls == [1]
    assert result["status"] == "UNAVAILABLE"
    assert result["error_code"] == "INVALID_RESPONSE"
    assert result["usage"]["cost_usd"] == .0025


@pytest.mark.parametrize("change", ["profile", "fixture", "index", "blank", "long"])
def test_keyed_schema_bypass_cannot_override_identity_or_text_limits(change):
    out = keyed_answer()
    row = out["critiques"]["rec-1"]
    if change == "profile":
        row["profile_id"] = "gauss"
    elif change == "fixture":
        row["flagged_legs"][0]["fixture_id"] = "999"
    elif change == "index":
        row["flagged_legs"][0]["leg_index"] = 1
    else:
        row["strengths"] = "   " if change == "blank" else "x" * 161
    result = review(out)
    if change in {"fixture", "index"}:
        assert result["status"] == "PARTIAL"
        assert result["critiques"][0]["flagged_legs"] == []
        assert result["critiques"][0]["actionability"] is False
    else:
        assert result["status"] == "UNAVAILABLE"
        assert result["critiques"] == []


@pytest.mark.parametrize("change", ["index", "fixture", "evidence", "strengths", "risks", "secret"])
def test_concrete_flag_and_both_sides_required(change):
    out = answer()
    critique = out["critiques"][0]
    if change == "index":
        critique["flagged_legs"][0]["leg_index"] = 1
    elif change == "fixture":
        critique["flagged_legs"][0]["fixture_id"] = "999"
    elif change == "evidence":
        critique["flagged_legs"][0]["claim_ids"] = ["made-up"]
    elif change == "secret":
        critique["flagged_legs"][0]["reason"] = "dummy-key"
    else:
        critique[change] = ""
    result = review(out)
    if change in {"strengths", "risks"}:
        assert result["status"] == "UNAVAILABLE"
        assert result["error_code"] == "EMPTY_REVIEW"
        assert result["critiques"] == []
        assert result["diagnostics"]["rejected_critiques"] == [{
            "recommendation_id": "rec-1", "profile_id": PROFILE,
            "reason_code": "INVALID_OR_UNSAFE_TEXT"}]
    else:
        assert result["status"] == "PARTIAL"
        card = result["critiques"][0]
        assert card["strengths"] == critique["strengths"]
        assert card["risks"] == critique["risks"]
        assert card["actionability"] is False
        assert card["review_validation"] == "PARTIAL"
        assert card["flagged_legs"] == []
        assert result["diagnostics"]["flags_rejected"] == 1
        assert "dummy-key" not in json.dumps(result)


def test_empty_review_is_unavailable_and_retains_actual_billing():
    result = review({"claims": [], "critiques": []})
    assert result["status"] == "UNAVAILABLE"
    assert result["error_code"] == "EMPTY_REVIEW"
    assert result["usage"]["cost_usd"] == .0025
    assert result["usage"]["request_count"] == 1
    assert result["diagnostics"]["recommendations_without_critique"] == 1


def test_usage_diagnostics_are_bounded_and_do_not_export_unknown_finish_text():
    body = response({"claims": [], "critiques": []})
    body["usage"].update(reasoning_tokens=4200, finish_reason="REASON_MAX_LEN")
    advisor = GrokAdvisor(api_key="dummy", transport=lambda *a, **k: body)
    usage = advisor.review(packet())["usage"]
    assert usage["reasoning_tokens"] == 4200
    assert usage["finish_reason"] == "REASON_MAX_LEN"
    body["usage"].update(reasoning_tokens=-1, finish_reason="raw-secret-finish-text")
    result = advisor.review(packet())
    assert result["usage"]["reasoning_tokens"] is None
    assert result["usage"]["finish_reason"] is None
    assert "raw-secret" not in json.dumps(result)


def test_one_invalid_flag_preserves_valid_flag_and_card_but_disables_action():
    out = answer()
    out["critiques"][0]["flagged_legs"].append({
        "leg_index": 19, "fixture_id": "wrong", "reason": "Bad leg", "claim_ids": []})
    result = review(out)
    assert result["status"] == "PARTIAL"
    card = result["critiques"][0]
    assert len(card["flagged_legs"]) == 1
    assert card["flagged_legs"][0]["fixture_id"] == "123"
    assert card["review_validation"] == "PARTIAL"
    assert card["actionability"] is False
    assert card["validation_warnings"] == ["INVALID_FLAG_IDENTITY"]
    assert result["diagnostics"]["flag_rejection_reasons"] == {"INVALID_FLAG_IDENTITY": 1}
    assert result["diagnostics"]["recommendations_without_critique"] == 0


def test_complete_valid_questionable_card_is_actionable_and_diagnostics_never_echo_unknown_ids():
    result = review()
    assert result["status"] == "COMPLETE"
    assert result["critiques"][0]["review_validation"] == "COMPLETE"
    assert result["critiques"][0]["actionability"] is True
    out = answer()
    out["critiques"][0]["recommendation_id"] = "xai-do-not-expose-aaaaaaaaaa"
    result = review(out)
    assert result["diagnostics"]["rejected_critiques"] == [{"reason_code": "INVALID_IDENTITY"}]
    assert "do-not-expose" not in json.dumps(result)


def test_compact_shared_facts_are_sent_once_and_leg_order_resolves():
    data = packet()
    leg = data["recommendations"][0]["legs"][0]
    data["selections"] = [{"selection_id": "s1", **leg},
                          {"selection_id": "s2", **leg, "selection_key": "under25"}]
    data["recommendations"][0]["legs"] = [{"selection_id": "s2"}, {"selection_id": "s1"}]
    data["recommendations"].append({"profile_id": "daily223", "recommendation_id": "rec-2",
                                    "legs": [{"selection_id": "s1"}]})
    out = answer()
    out["critiques"][0]["flagged_legs"][0]["leg_index"] = 1
    out["critiques"].append({**copy.deepcopy(out["critiques"][0]), "profile_id": "daily223",
                             "recommendation_id": "rec-2", "verdict": "INSUFFICIENT_EVIDENCE",
                             "flagged_legs": []})
    sent = []

    def transport(payload, **kwargs):
        sent.append(json.loads(payload["input"]))
        return response(out)

    result = GrokAdvisor(api_key="dummy", transport=transport).review(data)
    assert result["status"] == "COMPLETE"
    assert len(sent) == 1
    assert len(sent[0]["selections"]) == 2
    assert sent[0]["recommendations"][0]["legs"] == [
        {"selection_id": "s2"}, {"selection_id": "s1"}]
    assert sent[0]["recommendations"][1]["legs"] == [{"selection_id": "s1"}]
    assert result["critiques"][0]["flagged_legs"][0]["leg_index"] == 1


def test_supports_160_fixtures_but_rejects_over_limit_without_paid_call():
    data = packet()
    base = data["fixtures"][0]
    data["fixtures"] += [{**base, "fixture_id": str(i)} for i in range(200, 359)]
    calls = []

    def transport(*a, **k):
        calls.append(1)
        return response()

    advisor = GrokAdvisor(api_key="dummy", transport=transport)
    assert advisor.review(data)["status"] == "COMPLETE"
    data["fixtures"].append({**base, "fixture_id": "999"})
    assert advisor.review(data)["error_code"] == "INVALID_PACKET"
    assert calls == [1]


def test_existing_compact_evidence_sources_objective_and_context_are_preserved_once():
    data = packet()
    data["objective"] = {"target_win_count": 3, "target_winning_odds_sum": 30, "stake_eur": 1,
                         "total_ticket_count": 1, "total_stake_eur": 1,
                         "breakeven_winning_odds_sum": 1, "aspiration_not_guarantee": True}
    data["evidence_sources"] = [{"source_ref": "src1", "source_id": "history-data-123",
                                 "observed_at": PUBLISHED.isoformat(), "claim_type": "INFERENCE"}]
    data["evidence"] = [{"evidence_id": "e1", "category": "history", "statement": "Történeti minta.",
                         "source_ref": "src1", "strength": .5, "reliability": .7,
                         "statement_truncated": False}]
    data["quote_sources"] = [{"quote_ref": "q1", "quote_provider": "api_football",
                              "quoted_at": PUBLISHED.isoformat()}]
    leg = data["recommendations"][0]["legs"][0]
    data["selections"] = [{"selection_id": "s1", **leg, "evidence_ids": ["e1"], "quote_ref": "q1",
                           "model_probabilities": {"gauss": .55, "laplace": .6},
                           "calibration": "unvalidated", "estimated_edge": -.02,
                           "model_stability": .8, "confidence_kind": "MODEL_ESTIMATE"}]
    data["recommendations"][0].update(legs=[{"selection_id": "s1"}], status="MODEL_READY",
                                      style={"min_legs": 1, "max_legs": 4,
                                             "target_odds_min": 8, "target_odds_max": 20})
    data["fixtures"][0].update(competition="Premier League", competition_type="LEAGUE",
        weather={"status": "AVAILABLE", "provider": "Open-Meteo", "temperature_c": 12,
                 "forecast_time": data["fixtures"][0]["kickoff"]},
        existing_context=[{"topic": "lineup", "status": "UNKNOWN", "summary": "Még nincs kezdő.",
                           "source_urls": ["https://club.example/news"],
                           "observed_at": PUBLISHED.isoformat(), "publication_time_verified": False}])
    sent = []

    def transport(payload, **kwargs):
        sent.append(json.loads(payload["input"]))
        return response()

    result = GrokAdvisor(api_key="dummy", transport=transport).review(data)
    assert result["status"] == "COMPLETE"
    clean = sent[0]
    assert clean["objective"] == data["objective"]
    assert clean["evidence_sources"] == data["evidence_sources"]
    assert clean["quote_sources"] == data["quote_sources"]
    assert clean["evidence"][0]["source_ref"] == "src1"
    assert "source_id" not in clean["evidence"][0]
    assert clean["selections"][0]["quote_ref"] == "q1"
    assert "quoted_at" not in clean["selections"][0]
    assert clean["selections"][0]["model_probabilities"] == {"gauss": .55, "laplace": .6}
    assert clean["fixtures"][0]["weather"]["temperature_c"] == 12
    assert clean["fixtures"][0]["existing_context"][0]["publication_time_verified"] is False
    assert clean["recommendations"][0]["style"]["target_odds_min"] == 8
    assert clean["recommendations"][0]["status"] == "MODEL_READY"


def test_unresolved_evidence_reference_or_objective_guarantee_rejected_without_call():
    for variant in ("evidence", "guarantee"):
        data, called = packet(), []
        if variant == "evidence":
            data["recommendations"][0]["legs"][0]["evidence_ids"] = ["missing"]
        else:
            data["objective"] = {"aspiration_not_guarantee": False}
        result = GrokAdvisor(api_key="dummy", transport=lambda *a, called=called, **k:
                             called.append(1)).review(data)
        assert result["error_code"] == "INVALID_PACKET"
        assert called == []
