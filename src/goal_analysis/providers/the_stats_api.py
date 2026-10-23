"""Bounded TheStatsAPI GET client. Callers own caching, persistence and scheduling.

Contract: https://www.thestatsapi.com/docs (checked 2026-10-18).
No retries, redirects, credential storage or implicit historical collection.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from .base import ProviderError

BASE_URL = "https://api.thestatsapi.com/api"
MAX_RESPONSE_BYTES = 2_000_000
_PAGING = {"page", "per_page", "limit"}
_ERROR_CODES = {
    "UNAUTHORIZED", "KEY_REVOKED", "FORBIDDEN", "ADDON_REQUIRED", "RATE_LIMITED",
    "USAGE_LIMIT_EXCEEDED", "BAD_REQUEST", "UNKNOWN_PARAMETER", "NOT_FOUND",
    "PAGE_OUT_OF_RANGE", "SEASON_COMPETITION_MISMATCH", "MATCH_IS_LIVE",
    "STATS_NOT_AVAILABLE_AT_SOURCE",
}
_ROUTES = (
    (r"/health", set(), 0, set()),
    (r"/coverage/summary", set(), 0, set()),
    (r"/coverage/leagues", _PAGING | {"data_type", "search"}, 200, set()),
    (r"/coverage/leagues/comp_\d+", set(), 0, set()),
    (r"/football/competitions", _PAGING | {"country", "country_code", "type", "search", "sort"}, 100, set()),
    (r"/football/competitions/comp_\d+", set(), 0, set()),
    (r"/football/competitions/comp_\d+/seasons", set(), 0, set()),
    (r"/football/competitions/comp_\d+/seasons/sn_\d+/standings", {"group"}, 0, set()),
    (r"/football/teams/tm_\d+", set(), 0, set()),
    (r"/football/teams/tm_\d+/players", set(), 0, set()),
    (r"/football/teams/tm_\d+/injuries-suspensions", {"current"}, 0, set()),
    (r"/football/teams/tm_\d+/stats", {"season_id"}, 0, {"season_id"}),
    (r"/football/players/pl_\d+/stats", {"season_id", "competition_id", "stage"}, 0, {"season_id"}),
    (r"/football/matches", _PAGING | {"competition_id", "season_id", "team_id", "date_from",
        "date_to", "utc_offset", "status", "sort", "matchday", "stage", "group"}, 100, set()),
    (r"/football/matches/mt_\d+", set(), 0, set()),
    (r"/football/matches/mt_\d+/(?:stats|lineups)", set(), 0, set()),
    (r"/football/matches/mt_\d+/odds", {"bookmaker"}, 0, set()),
    (r"/v2/football/matches/mt_\d+/odds/players", {"bookmaker"}, 0, set()),
)


@dataclass(frozen=True, slots=True)
class TheStatsApiUsage:
    minute_limit: int | None = None
    minute_remaining: int | None = None
    minute_reset: int | None = None
    monthly_limit: int | None = None
    monthly_remaining: int | None = None
    monthly_reset: int | None = None
    retry_after: int | None = None


class TheStatsApiError(ProviderError):
    """Only controlled codes and status escape; never provider messages or auth."""

    def __init__(self, code, *, status=None, usage=None):
        self.code = code
        self.status = status
        self.usage = usage or TheStatsApiUsage()
        super().__init__(f"TheStatsAPI {code}" + (f" (HTTP {status})" if status else ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(url, headers, *, timeout):
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(MAX_RESPONSE_BYTES + 1), dict(response.headers.items()), response.status
    except urllib.error.HTTPError as error:
        # Read quota headers even on errors, but never expose the raw error body.
        with error:
            return error.read(MAX_RESPONSE_BYTES + 1), dict(error.headers.items()), error.code


def _header_int(value, *, unlimited=False):
    if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,16}", value):
        return None
    number = int(value)
    return number if number >= 0 or unlimited and number == -1 else None


def usage_from_headers(headers):
    """Return only documented numeric quotas; -1 monthly means unlimited."""
    if not isinstance(headers, Mapping):
        return TheStatsApiUsage()
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    fields = {}
    for prefix, names in (("minute", "x-ratelimit"), ("monthly", "x-monthly-quota")):
        for suffix in ("limit", "remaining", "reset"):
            fields[f"{prefix}_{suffix}"] = _header_int(
                normalized.get(f"{names}-{suffix}"),
                unlimited=prefix == "monthly" and suffix != "reset")
    fields["retry_after"] = _header_int(normalized.get("retry-after"))
    return TheStatsApiUsage(**fields)


def _route(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 200:
        raise TheStatsApiError("INVALID_ENDPOINT")
    path = "/" + endpoint.lstrip("/")
    for pattern, keys, page_limit, required in _ROUTES:
        if re.fullmatch(pattern, path):
            return path, keys, page_limit, required
    raise TheStatsApiError("INVALID_ENDPOINT")


def _parameters(params, allowed, page_limit, required):
    if params is None:
        params = {}
    if not isinstance(params, Mapping) or not set(params) <= allowed or not required <= set(params):
        raise TheStatsApiError("INVALID_PARAMETERS")
    if "limit" in params and "per_page" in params:
        raise TheStatsApiError("INVALID_PARAMETERS")
    result = {}
    for key, value in params.items():
        if key in {"page", "per_page", "limit", "matchday"}:
            maximum = page_limit if key in {"per_page", "limit"} else 1_000_000
            if type(value) is not int or not 1 <= value <= maximum:
                raise TheStatsApiError("INVALID_PARAMETERS")
            result[key] = value
        elif key in {"current", "is_mens_team"}:
            if type(value) is not bool:
                raise TheStatsApiError("INVALID_PARAMETERS")
            result[key] = str(value).lower()
        else:
            if (not isinstance(value, str) or not 1 <= len(value) <= 200
                    or any(ord(char) < 32 for char in value)):
                raise TheStatsApiError("INVALID_PARAMETERS")
            if key in {"date_from", "date_to"}:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                except ValueError:
                    raise TheStatsApiError("INVALID_PARAMETERS") from None
            if key.endswith("_id"):
                prefix = {"competition_id": "comp", "season_id": "sn", "team_id": "tm", "player_id": "pl"}[key]
                if not re.fullmatch(prefix + r"_\d{1,30}", value):
                    raise TheStatsApiError("INVALID_PARAMETERS")
            if key == "player_ids" and not re.fullmatch(r"pl_\d+(?:,pl_\d+){0,99}", value):
                raise TheStatsApiError("INVALID_PARAMETERS")
            if key == "status" and value not in {"scheduled", "live", "finished", "postponed", "cancelled"}:
                raise TheStatsApiError("INVALID_PARAMETERS")
            if key == "utc_offset" and not re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", value):
                raise TheStatsApiError("INVALID_PARAMETERS")
            result[key] = value
    if result.get("date_from", "") > result.get("date_to", "9999-12-31"):
        raise TheStatsApiError("INVALID_PARAMETERS")
    return result


class TheStatsApiClient:
    BASE_URL = BASE_URL

    def __init__(self, api_key=None, transport=None, *, timeout=20, max_requests=20, clock=None,
                 quota_observer=None):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60
                or type(max_requests) is not int or not 1 <= max_requests <= 10000):
            raise TheStatsApiError("INVALID_CONFIGURATION")
        self._api_key = api_key if api_key is not None else (
            os.environ.get("THE_STATS_API_KEY") or os.environ.get("THESTATSAPI_API_KEY"))
        self._transport = transport or _default_transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self.timeout = timeout
        self.max_requests = max_requests
        self.request_count = 0
        self.authenticated_request_count = 0
        self.last_usage = TheStatsApiUsage()
        self.last_response = None
        self.last_raw_response = None
        self.quota_observer = quota_observer

    def health(self):
        return self.request("/health")

    def request(self, endpoint, params=None):
        self.last_raw_response = None
        path, allowed, page_limit, required = _route(endpoint)
        query = _parameters(params, allowed, page_limit, required)
        authenticated = path != "/health"
        headers = {"Accept": "application/json", "User-Agent": "Arthur-GoalAnalysis/TheStatsAPI"}
        if authenticated:
            if (not isinstance(self._api_key, str) or not self._api_key
                    or len(self._api_key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in self._api_key)):
                raise TheStatsApiError("MISSING_OR_INVALID_API_KEY")
            if any(self._api_key in str(value) for value in query.values()):
                raise TheStatsApiError("UNSAFE_PARAMETERS")
            headers["Authorization"] = "Bearer " + self._api_key
        if self.request_count >= self.max_requests:
            raise TheStatsApiError("LOCAL_REQUEST_LIMIT", usage=self.last_usage)
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
        attempt = None
        if authenticated and self.quota_observer is not None:
            try:
                attempt = self.quota_observer.begin()
            except TheStatsApiError:
                raise
            except Exception:  # noqa: BLE001 - sanitize persistence/observer details
                raise TheStatsApiError('QUOTA_TRACKING_UNAVAILABLE') from None
        self.request_count += 1
        self.authenticated_request_count += int(authenticated)
        try:
            body, response_headers, status = self._transport(url, headers, timeout=self.timeout)
        except Exception:  # noqa: BLE001 - transport errors must never expose authorization
            raise TheStatsApiError("TRANSPORT_UNAVAILABLE", usage=self.last_usage) from None
        if type(status) is not int or not 100 <= status <= 599:
            raise TheStatsApiError("INVALID_RESPONSE_STATUS", usage=self.last_usage)
        # A public health check must not erase the latest authenticated quota snapshot.
        usage = usage_from_headers(response_headers)
        if authenticated:
            self.last_usage = usage
        observed = self._clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
            raise TheStatsApiError("INVALID_CLOCK", status=status, usage=usage)
        self.last_response = {"provider": "the_stats_api", "source_url": url,
            "observed_at": observed.astimezone(UTC).isoformat(), "http_status": status,
            "authenticated": authenticated, "payload_sha256": None,
            "response_id": uuid4().hex}
        if attempt is not None:
            try:
                self.quota_observer.finish(attempt, self.last_response, usage)
            except Exception:  # noqa: BLE001 - sanitize persistence/observer details
                raise TheStatsApiError('QUOTA_TRACKING_UNAVAILABLE', status=status, usage=usage) from None
        try:
            raw = (json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
                   if isinstance(body, dict) else body)
            if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            self.last_response["payload_sha256"] = hashlib.sha256(raw).hexdigest()
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(payload, dict):
                raise TypeError
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise TheStatsApiError("INVALID_RESPONSE", status=status, usage=usage) from None
        if not 200 <= status < 300 or payload.get("error"):
            error = payload.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            safe_code = code if isinstance(code, str) and code in _ERROR_CODES else "HTTP_ERROR"
            raise TheStatsApiError(safe_code, status=status, usage=usage)
        if isinstance(self._api_key, str) and self._api_key and self._api_key.encode("utf-8") in raw:
            raise TheStatsApiError("RESPONSE_CONTAINS_CREDENTIAL", status=status, usage=usage)
        if authenticated and "data" not in payload:
            raise TheStatsApiError("INVALID_RESPONSE", status=status, usage=usage)
        if not authenticated and payload.get("status") != "healthy":
            raise TheStatsApiError("UNHEALTHY", status=status, usage=usage)
        self.last_raw_response = raw
        return payload

    def iter_pages(self, endpoint, params=None, *, max_pages=1, per_page=100):
        """Yield at most max_pages responses, stopping on documented metadata.

        A bounded partial archive remains visibly partial in each page's meta.
        This iterator does not retry failures or turn missing meta into more calls.
        """
        _, allowed, page_limit, required = _route(endpoint)
        if (not page_limit or type(max_pages) is not int or not 1 <= max_pages <= 100
                or type(per_page) is not int or not 1 <= per_page <= page_limit):
            raise TheStatsApiError("INVALID_PAGINATION")
        if params is not None and not isinstance(params, Mapping):
            raise TheStatsApiError("INVALID_PAGINATION")
        query = dict(params or {})
        if set(query) & _PAGING:
            raise TheStatsApiError("INVALID_PAGINATION")
        _parameters({**query, "page": 1, "per_page": per_page}, allowed, page_limit, required)
        for page in range(1, max_pages + 1):
            payload = self.request(endpoint, {**query, "page": page, "per_page": per_page})
            meta, rows = payload.get("meta"), payload.get("data")
            if (not isinstance(meta, dict) or not isinstance(rows, list)
                    or type(meta.get("page")) is not int or meta["page"] != page
                    or type(meta.get("total_pages")) is not int or meta["total_pages"] < 0
                    or type(meta.get("per_page")) is not int or meta["per_page"] != per_page
                    or len(rows) > per_page
                    or meta["total_pages"] == 0 and rows):
                raise TheStatsApiError("INVALID_PAGINATION_RESPONSE", usage=self.last_usage)
            yield payload
            if page >= meta["total_pages"]:
                break
