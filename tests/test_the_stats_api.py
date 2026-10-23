import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from goal_analysis.providers.the_stats_api import (
    MAX_RESPONSE_BYTES,
    TheStatsApiClient,
    TheStatsApiError,
    _NoRedirect,
    usage_from_headers,
)

KEY = "dummy-provider-key-never-real"
NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
HEADERS = {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "119",
           "X-Monthly-Quota-Limit": "100000", "X-Monthly-Quota-Remaining": "99999",
           "X-Monthly-Quota-Reset": "1800000000"}


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, *, timeout):
        self.calls.append((url, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(transport, **kwargs):
    return TheStatsApiClient(KEY, transport, clock=lambda: NOW, **kwargs)


def test_documented_probe_and_usage_preserve_ids_and_never_store_authorization():
    transport = Transport(({"data": [{"id": "comp_0001"}]}, HEADERS, 200))
    api = client(transport)
    assert api.request("/coverage/leagues", {"per_page": 1})["data"][0]["id"] == "comp_0001"
    url, headers, timeout = transport.calls[0]
    assert url == "https://api.thestatsapi.com/api/coverage/leagues?per_page=1"
    assert headers["Authorization"] == "Bearer " + KEY and timeout == 20
    assert api.last_usage.monthly_remaining == 99999
    assert api.last_response["observed_at"] == NOW.isoformat()
    assert len(api.last_response["payload_sha256"]) == 64
    assert KEY not in json.dumps(api.last_response)
    assert KEY not in json.dumps(asdict(api.last_usage))


def test_environment_default_uses_existing_dpapi_key_name(monkeypatch):
    monkeypatch.setenv("THE_STATS_API_KEY", KEY)
    monkeypatch.setenv("THESTATSAPI_API_KEY", "other-docs-alias")
    transport = Transport(({"data": []}, {}, 200))
    TheStatsApiClient(transport=transport).request("/coverage/leagues", {"per_page": 1})
    assert transport.calls[0][1]["Authorization"] == "Bearer " + KEY


def test_health_needs_no_secret_and_does_not_erase_quota():
    transport = Transport(({"data": []}, HEADERS, 200), ({"status": "healthy"}, {}, 200))
    api = client(transport)
    api.request("/coverage/leagues", {"per_page": 1})
    api.health()
    assert "Authorization" not in transport.calls[-1][1]
    assert api.last_usage.monthly_remaining == 99999
    assert api.authenticated_request_count == 1 and api.request_count == 2
    assert TheStatsApiClient(api_key="", transport=Transport(({"status": "healthy"}, {}, 200))).health()


@pytest.mark.parametrize("endpoint,params", [
    ("https://evil.example/steal", {}), ("//evil.example/steal", {}),
    ("/football/matches?api_key=secret", {}), ("/football/matches/../health", {}),
    ("/football/matches", {"api_key": KEY}), ("/football/matches", {"date": "2026-10-18"}),
    ("/football/matches", {"per_page": 101}), ("/football/matches", {"page": True}),
    ("/football/matches", {"date_from": "2026-10-19", "date_to": "2026-10-18"}),
    ("/football/teams/tm_1/stats", {}), ("/football/competitions/comp_1/seasons", {"per_page": 10}),
])
def test_invalid_request_never_spends_quota(endpoint, params):
    transport = Transport()
    with pytest.raises(TheStatsApiError):
        client(transport).request(endpoint, params)
    assert not transport.calls


def test_date_filters_and_utc_offset_encoding():
    transport = Transport(({"data": []}, {}, 200))
    api = client(transport)
    api.request("football/matches", {"team_id": "tm_001", "status": "finished",
        "date_from": "2026-10-18", "date_to": "2026-10-18", "utc_offset": "+02:00"})
    assert "utc_offset=%2B02%3A00" in transport.calls[0][0]


@pytest.mark.parametrize("error_code", ["RATE_LIMITED", "USAGE_LIMIT_EXCEEDED", KEY])
def test_http_errors_are_redacted_capture_quota_and_never_retry(error_code):
    transport = Transport(({"error": {"code": error_code, "message": KEY}},
                           {**HEADERS, "Retry-After": "60"}, 429))
    api = client(transport)
    with pytest.raises(TheStatsApiError) as caught:
        api.request("/coverage/leagues", {"per_page": 1})
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)
    assert caught.value.code == (error_code if error_code != KEY else "HTTP_ERROR")
    assert caught.value.usage.retry_after == 60
    assert len(transport.calls) == 1


def test_transport_exception_and_successful_credential_echo_do_not_escape():
    for response in (RuntimeError("authorization " + KEY), ({"data": KEY}, {}, 200)):
        with pytest.raises(TheStatsApiError) as caught:
            client(Transport(response)).request("/coverage/leagues")
        assert KEY not in str(caught.value)
        assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_request_budget_applies_even_to_transport_failures():
    transport = Transport(TimeoutError(KEY))
    api = client(transport, max_requests=1)
    with pytest.raises(TheStatsApiError):
        api.request("/coverage/leagues")
    with pytest.raises(TheStatsApiError, match="LOCAL_REQUEST_LIMIT"):
        api.request("/coverage/leagues")
    assert len(transport.calls) == 1


def test_numeric_quota_headers_unlimited_and_unknown_remain_distinct():
    usage = usage_from_headers({"x-monthly-quota-limit": "-1", "x-monthly-quota-remaining": "-1",
                               "x-ratelimit-remaining": "unknown", "Retry-After": KEY})
    assert usage.monthly_remaining == usage.monthly_limit == -1
    assert usage.minute_remaining is None and usage.retry_after is None


def page(number, total=5):
    return {"data": [{"id": f"mt_{number:03}"}],
            "meta": {"page": number, "per_page": 100, "total_pages": total}}, HEADERS, 200


def test_pagination_obeys_explicit_bound_and_retains_partial_metadata():
    transport = Transport(page(1), page(2))
    pages = list(client(transport).iter_pages("/football/matches", {"team_id": "tm_1"}, max_pages=2))
    assert len(transport.calls) == len(pages) == 2
    assert pages[-1]["meta"]["total_pages"] == 5


def test_pagination_stops_at_last_page_and_rejects_invalid_meta_without_extra_call():
    transport = Transport(page(1, total=1))
    assert len(list(client(transport).iter_pages("/football/matches", max_pages=10))) == 1
    invalid = Transport(({"data": [], "meta": {"page": 1}}, HEADERS, 200))
    with pytest.raises(TheStatsApiError, match="INVALID_PAGINATION_RESPONSE"):
        list(client(invalid).iter_pages("/football/matches", max_pages=10))
    assert len(invalid.calls) == 1


@pytest.mark.parametrize("body", [
    b"not JSON " + KEY.encode(), b"x" * (MAX_RESPONSE_BYTES + 1), b'{"data":NaN}',
], ids=["malformed", "oversized", "nan"])
def test_invalid_or_oversized_json_is_safely_rejected(body):
    with pytest.raises(TheStatsApiError, match="INVALID_RESPONSE") as caught:
        client(Transport((body, HEADERS, 200))).request("/coverage/leagues")
    assert KEY not in str(caught.value)


def test_redirect_handler_never_forwards_authorization():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None
