"""Bounded, source-linked portfolio review through the OpenAI Responses API.

Only explicit candidate facts and short feedback are sent. Credentials remain in
the Authorization header; provider error bodies are never returned or persisted.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

Transport = Callable[[str, Mapping[str, str], bytes], Mapping[str, Any]]
PROMPT_VERSION = "arthur-portfolio-v3"
_PROMPT_ROOT = Path(__file__).resolve().parents[3] / "config" / "prompts" / PROMPT_VERSION
_ENDPOINT = "https://api.openai.com/v1/responses"
_CACHE_TTL = timedelta(minutes=30)
_REVIEW_KEYS = {
    "candidate_id",
    "assessment",
    "support_arguments",
    "risk_notes",
    "evidence_ids",
    "source_urls",
}
_CANDIDATE_KEYS = {
    "odds_pending",
    "candidate_id",
    "fixture_id",
    "home_team",
    "away_team",
    "competition",
    "competition_type",
    "kickoff",
    "market_key",
    "selection_key",
    "period",
    "settlement",
    "bookmaker",
    "region",
    "currency",
    "decimal_price",
    "quoted_at",
    "quote_source_id",
    "support_score",
    "score_components",
    "missing_support",
    "evidence",
    "evidence_ids",
    "sensitivity_note",
    "profile",
    "profile_id",
    "history_analysis",
    "historical_context",
    "workload",
}


class _ReviewError(Exception):
    """An error whose message is safe to expose to the dashboard."""


def _default_transport(url: str, headers: Mapping[str, str], body: bytes) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise _ReviewError("OpenAI response exceeded the size limit.")
        payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _ReviewError(_safe_http_message(error)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _ReviewError("OpenAI connection failed or timed out; try again later.") from None
    except (ValueError, UnicodeError):
        raise _ReviewError("OpenAI returned an unreadable response.") from None
    if not isinstance(payload, dict):
        raise _ReviewError("OpenAI returned an invalid response object.")
    return payload


def _http_message(code: int) -> str:
    return {
        401: "OpenAI authentication failed. Check the saved API key.",
        403: "This OpenAI project does not have access to the requested model or tool.",
        404: "The requested OpenAI model is unavailable for this project.",
        429: "OpenAI quota or rate limit reached. Check project billing and limits.",
    }.get(code, f"OpenAI request failed (HTTP {int(code)}); no provider details were exposed.")


def _safe_http_message(error: urllib.error.HTTPError) -> str:
    """Classify allowlisted error codes without exposing the response body or key."""
    messages = {
        "credit_balance_exhausted": "No prepaid API credits remain. Check API billing and credits.",
        "organization_spend_limit_exceeded": "The organization API spend limit was reached. Review organization limits.",
        "project_spend_limit_exceeded": "The project API spend limit was reached. Review this key's project limits.",
        "organization_usage_limit_exceeded": "The OpenAI-assigned usage limit was reached. Check approved organization limits.",
        "insufficient_quota": "API quota is unavailable. Check API billing, credits and organization/project limits.",
        "rate_limit_exceeded": "Temporary API request/token rate limit. Wait before retrying and check model rate limits.",
        "slow_down": "API traffic increased too quickly. Wait before retrying and reduce the request rate.",
    }
    try:
        if error.code != 429:
            return _http_message(error.code)
        raw = error.read(16385)
        if len(raw) > 16384:
            return _http_message(error.code)
        payload = json.loads(raw)
        details = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(details, dict):
            # New billing subcodes are more specific than type=insufficient_quota.
            for field in ("code", "type"):
                code = details.get(field)
                if isinstance(code, str) and code in messages:
                    message = f"OpenAI HTTP 429 [{code}]: {messages[code]}"
                    delay = error.headers.get("Retry-After", "") if error.headers else ""
                    if (
                        code in {"rate_limit_exceeded", "slow_down"}
                        and isinstance(delay, str)
                        and re.fullmatch(r"[0-9]{1,6}", delay)
                    ):
                        message += f" Retry after at least {int(delay)} seconds."
                    return message
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        pass
    finally:
        error.close()
    return _http_message(error.code)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _usage() -> dict[str, Any]:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "web_search_calls": 0,
        "cache_hit": False,
        "cost_amount": None,
        "cost_currency": "USD",
    }


def _add_usage(total: dict[str, Any], response: Mapping[str, Any]) -> None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(name, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total[name] += value
        for category, source_name, destination in (
            ("input_tokens_details", "cached_tokens", "cached_tokens"),
            ("output_tokens_details", "reasoning_tokens", "reasoning_tokens"),
        ):
            detail = usage.get(category)
            value = detail.get(source_name, 0) if isinstance(detail, dict) else 0
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total[destination] += value
    for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
        if isinstance(item, dict) and item.get("type") == "web_search_call":
            total["web_search_calls"] += 1


def _text(response: Mapping[str, Any]) -> str:
    if response.get("status") != "completed" or response.get("error"):
        raise _ReviewError("OpenAI did not finish the analysis; no partial review was accepted.")
    output = response.get("output", [])
    if not isinstance(output, list):
        raise _ReviewError("OpenAI returned an invalid output list.")
    parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("status") in {"incomplete", "failed", "cancelled", "in_progress"}:
            raise _ReviewError("An OpenAI output step did not finish.")
        content = item.get("content", [])
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise _ReviewError("OpenAI declined the requested analysis.")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
    direct = response.get("output_text")
    result = "\n".join(parts) or (direct if isinstance(direct, str) else "")
    if not result.strip() or len(result) > 150_000:
        raise _ReviewError("OpenAI returned empty or oversized analysis text.")
    return result


def _public_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s<>\\\x00-\x1f]", value):
        raise _ReviewError("A source contained an invalid public URL.")
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"https", "http"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
            or "." not in host
        ):
            raise ValueError
        if host.lower().endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        for key, _ in urllib.parse.parse_qsl(parsed.query):
            if key.lower() in {"api_key", "apikey", "access_token", "authorization", "token"}:
                raise ValueError
    except (ValueError, UnicodeError):
        raise _ReviewError("A source contained an invalid public URL.") from None
    return value


def _sources(response: Mapping[str, Any]) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        references = []
        if item.get("type") == "web_search_call":
            action = item.get("action", {})
            if isinstance(action, dict) and isinstance(action.get("sources"), list):
                references.extend(action["sources"])
        content = item.get("content", [])
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and isinstance(part.get("annotations"), list):
                references.extend(
                    a
                    for a in part["annotations"]
                    if isinstance(a, dict) and a.get("type") == "url_citation"
                )
        for reference in references:
            if not isinstance(reference, dict) or "url" not in reference:
                continue
            url = _public_url(reference["url"])
            title = reference.get("title", urllib.parse.urlsplit(url).hostname)
            if not isinstance(title, str):
                title = urllib.parse.urlsplit(url).hostname or "Source"
            found[url] = {"url": url, "title": title[:300]}
    return list(found.values())


def _schema(candidate_ids: list[str]) -> dict[str, Any]:
    string_list = {"type": "array", "items": {"type": "string"}}
    fields = {
        "candidate_id": {"type": "string", "enum": candidate_ids},
        "assessment": {"type": "string", "enum": ["support", "neutral", "caution"]},
        "support_arguments": string_list,
        "risk_notes": string_list,
        "evidence_ids": string_list,
        "source_urls": string_list,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": fields,
                    "required": list(fields),
                },
            }
        },
        "required": ["reviews"],
    }


def _evidence_ids(candidate: dict[str, Any]) -> set[str]:
    result = {value for value in candidate.get("evidence_ids", []) if isinstance(value, str)}
    for item in candidate.get("evidence", []):
        if isinstance(item, dict):
            value = item.get("evidence_id", item.get("id"))
            if isinstance(value, str):
                result.add(value)
    return result


def _compact_history(value: Any) -> Any:
    """Keep useful sample/form/load summaries; repetitive provider IDs stay locally."""
    if isinstance(value, dict):
        return {
            key: _compact_history(item)
            for key, item in value.items()
            if key not in {"fixture_ids", "source_ids"}
        }
    if isinstance(value, list):
        return [_compact_history(item) for item in value]
    return value


def _validate_reviews(
    payload: Any, candidates: list[dict[str, Any]], sources: list[dict[str, str]]
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"reviews"}:
        raise _ReviewError("OpenAI review did not match the required schema.")
    reviews = payload["reviews"]
    if not isinstance(reviews, list) or len(reviews) != len(candidates):
        raise _ReviewError("OpenAI did not review every requested candidate.")
    allowed_ids = {item["candidate_id"]: _evidence_ids(item) for item in candidates}
    allowed_urls = {source["url"] for source in sources}
    seen = set()
    for review in reviews:
        if not isinstance(review, dict) or set(review) != _REVIEW_KEYS:
            raise _ReviewError("OpenAI review fields did not match the required schema.")
        identity = review["candidate_id"]
        if not isinstance(identity, str) or identity not in allowed_ids or identity in seen:
            raise _ReviewError("OpenAI returned an unknown or duplicated candidate ID.")
        seen.add(identity)
        if review["assessment"] not in {"support", "neutral", "caution"}:
            raise _ReviewError("OpenAI returned an invalid assessment.")
        for name in ("support_arguments", "risk_notes", "evidence_ids", "source_urls"):
            values = review[name]
            if (
                not isinstance(values, list)
                or len(values) > 12
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > 2500
                    or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)
                    for value in values
                )
            ):
                raise _ReviewError("OpenAI returned invalid or oversized review details.")
        if not set(review["evidence_ids"]) <= allowed_ids[identity]:
            raise _ReviewError("OpenAI cited evidence that does not belong to the candidate.")
        for url in review["source_urls"]:
            if _public_url(url) not in allowed_urls:
                raise _ReviewError("OpenAI cited a source that was not retrieved or supplied.")
        if review["assessment"] == "support" and (
            not review["support_arguments"] or not (review["evidence_ids"] or review["source_urls"])
        ):
            raise _ReviewError("OpenAI returned support without traceable evidence.")
    return reviews


class AstraReviewer:
    """Two requests at most: sourced context research, then structured assessment.

    ``transport(url, headers, body_bytes)`` can be injected for offline tests.
    The cache is local and expires after 30 minutes; changed facts invalidate it.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-6-astra",
        reasoning_effort: str = "medium",
        web_search: bool = True,
        max_output_tokens: int = 8000,
        transport: Transport | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.api_key = (
            os.environ.get("OPENAI_API_KEY", "") if api_key is None else api_key
        ).strip()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.web_search = bool(web_search)
        self.max_output_tokens = max_output_tokens
        self.transport = transport or _default_transport
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", model)
            or reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}
            or isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1000 <= max_output_tokens <= 16000
        ):
            raise ValueError("Invalid Astra model, reasoning effort or output token limit.")

    def _result(
        self, status: str, message: str, usage: dict[str, Any], **extra: Any
    ) -> dict[str, Any]:
        return {
            "status": status,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "reviewed_at": _utcnow().isoformat(),
            "reviews": [],
            "sources": [],
            "usage": usage,
            "message": message,
            **extra,
        }

    def _request(self, body: dict[str, Any], usage: dict[str, Any]) -> Mapping[str, Any]:
        body = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": self.reasoning_effort},
            **body,
        }
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if self.api_key and self.api_key.encode("utf-8") in encoded:
            raise _ReviewError("Input unexpectedly contained a credential; request was stopped.")
        usage["requests"] += 1
        try:
            response = self.transport(
                _ENDPOINT,
                {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                encoded,
            )
        except urllib.error.HTTPError as error:
            raise _ReviewError(_safe_http_message(error)) from None
        except _ReviewError:
            raise
        except Exception:  # noqa: BLE001 - arbitrary injected transports may include secrets in errors
            raise _ReviewError(
                "OpenAI connection failed; no provider details were exposed."
            ) from None
        if not isinstance(response, dict):
            raise _ReviewError("OpenAI returned an invalid response object.")
        _add_usage(usage, response)
        # A reflected credential must never reach reports, sources or the cache.
        if self.api_key in json.dumps(response, ensure_ascii=False):
            raise _ReviewError("OpenAI returned unsafe content; the response was discarded.")
        resolved = response.get("model")
        if not isinstance(resolved, str) or not (
            resolved == self.model or resolved.startswith(self.model + "-")
        ):
            raise _ReviewError(
                "OpenAI did not confirm the requested model; the response was discarded."
            )
        return response

    def check_connection(self) -> dict[str, Any]:
        """Actually verify project authentication and model access with one request."""
        usage = _usage()
        if not self.api_key:
            return self._result(
                "UNAVAILABLE", "OPENAI_API_KEY is not configured.", usage, verified=False
            )
        try:
            response = self._request(
                {
                    "instructions": "This is a connection check. Return exactly ARTHUR_OK.",
                    "input": "Confirm the connection.",
                    "max_output_tokens": 1000,
                    "reasoning": {"effort": "low"},
                },
                usage,
            )
            if _text(response).strip() != "ARTHUR_OK":
                raise _ReviewError(
                    "OpenAI responded, but the model connection check was not valid."
                )
            return self._result(
                "COMPLETE",
                "OpenAI authentication and model access verified.",
                usage,
                verified=True,
                resolved_model=response.get("model", self.model),
            )
        except _ReviewError as error:
            return self._result("FAILED", str(error), usage, verified=False)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            return self._result(
                "FAILED",
                "OpenAI returned an invalid connection-check response.",
                usage,
                verified=False,
            )

    def review(
        self,
        candidates: list[dict[str, Any]],
        target_date: str,
        feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        usage = _usage()
        sources, research_details = [], {}
        research_status = "NOT_RUN"
        if not self.api_key:
            return self._result("UNAVAILABLE", "OPENAI_API_KEY is not configured.", usage)
        try:
            packed, prompt_hash = self._prepare(candidates, target_date, feedback)
            cache_key = hashlib.sha256(
                json.dumps(
                    {
                        "input": packed,
                        "prompt_sha256": prompt_hash,
                        "model": self.model,
                        "effort": self.reasoning_effort,
                        "web_search": self.web_search,
                        "max_output_tokens": self.max_output_tokens,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            cached = self._read_cache(cache_key, packed["candidates"])
            if cached is not None:
                return cached
            observed_at = _utcnow().isoformat()
            packed["observed_at"] = observed_at
            sources = self._supplied_sources(packed["candidates"])
            research_text = "Web research disabled; use supplied facts only."
            research_status = "DISABLED"
            research_details = {}
            if self.web_search:
                response = self._request(
                    {
                        "instructions": self._prompt("research.txt"),
                        "input": json.dumps(packed, ensure_ascii=False),
                        "tools": [{"type": "web_search", "search_context_size": "low"}],
                        "tool_choice": "required",
                        "max_tool_calls": 4,
                        "include": ["web_search_call.action.sources"],
                        "max_output_tokens": min(4000, self.max_output_tokens),
                    },
                    usage,
                )
                research_text = _text(response)
                searched_sources = _sources(response)
                research_details = response.get("arthur_research", {})
                verified = bool(usage["web_search_calls"] and searched_sources)
                if not verified:
                    research_text = "Web research is unavailable or unverified. Assess supplied candidate evidence only. Disclose unknown weather, coaching, lineups, injuries, workload and motivation."
                    research_status = "UNAVAILABLE"
                else:
                    research_status = "COMPLETE_WITH_SOURCES"
                sources = list(
                    {source["url"]: source for source in sources + searched_sources}.values()
                )
            response = self._request(
                {
                    "instructions": self._prompt("review.txt"),
                    "input": json.dumps(
                        {
                            **packed,
                            "research_notes": research_text,
                            "allowed_sources": sources,
                            "fixture_context": research_details.get("fixture_context", []),
                            "indicative_web_odds": research_details.get("web_odds", []),
                        },
                        ensure_ascii=False,
                    ),
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "arthur_portfolio_review",
                            "strict": True,
                            "schema": _schema(
                                [item["candidate_id"] for item in packed["candidates"]]
                            ),
                        }
                    },
                    "max_output_tokens": self.max_output_tokens,
                },
                usage,
            )
            try:
                payload = json.loads(_text(response))
            except json.JSONDecodeError:
                raise _ReviewError(
                    "OpenAI did not return valid structured analysis JSON."
                ) from None
            reviews = _validate_reviews(payload, packed["candidates"], sources)
            result = self._result(
                "COMPLETE",
                "Astra review completed with traceable references.",
                usage,
                reviews=reviews,
                sources=sources,
                research_status=research_status,
                research_notes=research_text,
                **{
                    k: research_details[k]
                    for k in ("web_odds", "fixture_context", "rejected_web_quotes")
                    if k in research_details
                },
                observed_at=observed_at,
                prompt_sha256=prompt_hash,
                resolved_model=response.get("model", self.model),
            )
            if research_status == "UNAVAILABLE":
                result["message"] = (
                    "Astra értékelése elkészült a rendelkezésre álló adatokból; a webes háttér ellenőrzése hiányos."
                )
            else:
                self._write_cache(cache_key, result)
            return result
        except _ReviewError as error:
            return self._result(
                "FAILED",
                str(error),
                usage,
                sources=sources,
                research_status=research_status,
                **{
                    k: research_details[k]
                    for k in ("web_odds", "fixture_context")
                    if k in research_details
                },
            )
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            return self._result(
                "FAILED",
                "OpenAI analysis could not be validated; no review was accepted.",
                usage,
                sources=sources,
                research_status=research_status,
                **{
                    k: research_details[k]
                    for k in ("web_odds", "fixture_context")
                    if k in research_details
                },
            )

    def _prompt(self, name: str) -> str:
        try:
            raw = (_PROMPT_ROOT / name).read_bytes()
            manifest = json.loads((_PROMPT_ROOT / "manifest.json").read_text("utf-8"))
            if (
                manifest.get("prompt_version") != PROMPT_VERSION
                or manifest["runtime_prompts"][name] != hashlib.sha256(raw).hexdigest()
            ):
                raise _ReviewError(
                    "The Astra runtime prompt does not match its versioned manifest."
                )
            return raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            raise _ReviewError(
                "The versioned Astra prompt files are missing or unreadable."
            ) from None

    def _prepare(
        self, candidates: Any, target_date: Any, feedback: Any
    ) -> tuple[dict[str, Any], str]:
        try:
            if (
                not isinstance(target_date, str)
                or date.fromisoformat(target_date).isoformat() != target_date
            ):
                raise ValueError
        except ValueError:
            raise _ReviewError("Astra review requires a valid target date (YYYY-MM-DD).") from None
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 40:
            raise _ReviewError("Astra review requires between 1 and 40 selected candidates.")
        packed, seen = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise _ReviewError("Astra candidates must be objects.")
            identity = candidate.get("candidate_id")
            if (
                not isinstance(identity, str)
                or not identity.strip()
                or len(identity) > 300
                or identity in seen
            ):
                raise _ReviewError("Astra candidates need unique immutable candidate IDs.")
            seen.add(identity)
            for field in ("evidence", "evidence_ids"):
                if field in candidate and not isinstance(candidate[field], list):
                    raise _ReviewError("Candidate evidence must be a list.")
            item = {key: value for key, value in candidate.items() if key in _CANDIDATE_KEYS}
            for history_field in ("historical_context", "history_analysis"):
                if history_field in item:
                    item[history_field] = _compact_history(item[history_field])
            packed.append(item)
        if feedback is not None and not isinstance(feedback, dict):
            raise _ReviewError("Historical feedback must be an object.")
        try:
            encoded_feedback = json.dumps(feedback or {}, ensure_ascii=False, allow_nan=False)
            content = {
                "target_date": target_date,
                "candidates": packed,
                "historical_feedback": feedback or {},
                "prompt_version": PROMPT_VERSION,
            }
            raw = json.dumps(content, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise _ReviewError("Astra input must contain finite JSON values only.") from None
        if len(raw.encode("utf-8")) > 180_000 or len(encoded_feedback) > 12_000:
            raise _ReviewError("Astra input exceeded the compact review budget.")
        if re.search(
            r'"(?:api[_-]?key|authorization|access[_-]?token|password)"\s*:', raw, re.IGNORECASE
        ):
            raise _ReviewError("Astra input contains a credential field; request was stopped.")
        digest = hashlib.sha256(
            (PROMPT_VERSION + self._prompt("research.txt") + self._prompt("review.txt")).encode(
                "utf-8"
            )
        ).hexdigest()
        return content, digest

    def _supplied_sources(self, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
        sources = {}
        for candidate in candidates:
            for evidence in candidate.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                value = evidence.get("source_url", evidence.get("url"))
                if value:
                    url = _public_url(value)
                    sources[url] = {
                        "url": url,
                        "title": str(evidence.get("title", "Supplied evidence"))[:300],
                    }
        return list(sources.values())

    def _read_cache(self, key: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self.cache_dir is None:
            return None
        try:
            result = json.loads((self.cache_dir / f"{key}.json").read_text("utf-8"))
            timestamp = datetime.fromisoformat(result["reviewed_at"])
            if (
                result.get("status") != "COMPLETE"
                or timestamp.tzinfo is None
                or not timedelta(0) <= _utcnow() - timestamp < _CACHE_TTL
            ):
                return None
            for source in result["sources"]:
                _public_url(source["url"])
            _validate_reviews({"reviews": result["reviews"]}, candidates, result["sources"])
            if self.api_key in json.dumps(result):
                return None
            result = copy.deepcopy(result)
            result["original_usage"] = result["usage"]
            result["usage"] = {**_usage(), "cache_hit": True}
            result["message"] = (
                "Using the unchanged candidate review cached within the last 30 minutes."
            )
            return result
        except (OSError, ValueError, TypeError, KeyError, _ReviewError):
            return None

    def _write_cache(self, key: str, result: dict[str, Any]) -> None:
        if self.cache_dir is None:
            return
        temporary: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.cache_dir, suffix=".tmp", delete=False
            ) as handle:
                temporary = handle.name
                json.dump(result, handle, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, self.cache_dir / f"{key}.json")
        except OSError:
            result["cache_warning"] = "The completed review could not be cached locally."
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
