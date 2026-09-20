from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fact_packet import canonical_sha256
from .kerekasztal import KerekasztalError, Role

Transport = Callable[[str, Mapping[str, str], bytes], Mapping[str, Any]]


def _default_transport(
    url: str, headers: Mapping[str, str], body: bytes
) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise KerekasztalError(f"OpenAI HTTP {error.code}: {detail[:300]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise KerekasztalError(f"OpenAI request failed: {error}") from error


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    role: Role
    version: str
    text: str
    sha256: str


class PromptRegistry:
    def __init__(self, root: Path, version: str = "kerekasztal-v1") -> None:
        self.root = Path(root)
        self.version = version

    def load(self, role: Role) -> PromptTemplate:
        path = self.root / self.version / f"{role.value}.txt"
        try:
            text = path.read_text("utf-8").strip()
        except OSError as error:
            raise KerekasztalError(f"cannot load prompt for {role.value}: {path}") from error
        if not text:
            raise KerekasztalError(f"empty prompt for {role.value}: {path}")
        return PromptTemplate(role, self.version, text, canonical_sha256(text))


class OpenAIResponsesRoleRunner:
    """OpenAI Responses API adapter with strict structured role output."""

    ENDPOINT = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        prompts: PromptRegistry,
        model: str | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.prompts = prompts
        self.model = model or os.environ.get("ARTHUR_OPENAI_MODEL")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.transport = transport or _default_transport
        if not self.model:
            raise KerekasztalError("ARTHUR_OPENAI_MODEL is not configured")
        if not self.api_key:
            raise KerekasztalError("OPENAI_API_KEY is not configured")

    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = self.prompts.load(role)
        request_body = {
            "model": self.model,
            "instructions": prompt.text,
            "input": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kerekasztal_role_opinion",
                    "strict": True,
                    "schema": _response_schema(),
                }
            },
        }
        encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        response = self.transport(
            self.ENDPOINT,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            encoded,
        )
        output_text = _extract_output_text(response)
        try:
            opinion = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise KerekasztalError("OpenAI response was not valid structured JSON") from error
        if not isinstance(opinion, dict):
            raise KerekasztalError("OpenAI structured output must be an object")
        usage = response.get("usage") or {}
        opinion["_meta"] = {
            "provider": "openai",
            "model": response.get("model", self.model),
            "response_id": response.get("id"),
            "prompt_version": prompt.version,
            "prompt_sha256": prompt.sha256,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        return opinion


def _extract_output_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise KerekasztalError("OpenAI response did not contain output text")


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "role": {"type": "string", "enum": [role.value for role in Role]},
            "fixture_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["support", "oppose", "unknown", "veto"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "thesis": {"type": "string", "minLength": 1},
            "evidence_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            "structural_veto": {"type": "boolean"},
        },
        "required": [
            "role",
            "fixture_id",
            "verdict",
            "confidence",
            "thesis",
            "evidence_sha256",
            "structural_veto",
        ],
    }
