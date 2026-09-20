import json

import pytest

from goal_analysis.agents import KerekasztalError, OpenAIResponsesRoleRunner, PromptRegistry, Role


def write_prompt(tmp_path, role=Role.KRONIKAS):
    directory = tmp_path / "kerekasztal-v1"
    directory.mkdir()
    (directory / f"{role.value}.txt").write_text("Fixed role instructions.", "utf-8")
    return PromptRegistry(tmp_path)


def role_payload():
    return {"fixture_id": "fixture-1", "evidence_sha256": "a" * 64, "facts": {"x": 1}}


def opinion():
    return {
        "role": "kronikas",
        "fixture_id": "fixture-1",
        "verdict": "support",
        "confidence": 0.75,
        "thesis": "Evidence supports further review.",
        "evidence_sha256": "a" * 64,
        "structural_veto": False,
    }


def test_runner_uses_strict_schema_and_keeps_key_out_of_body(tmp_path) -> None:
    observed = {}

    def transport(url, headers, body):
        observed.update(url=url, headers=headers, body=body)
        return {
            "id": "resp_1",
            "model": "configured-model",
            "output_text": json.dumps(opinion()),
            "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130},
        }

    runner = OpenAIResponsesRoleRunner(
        write_prompt(tmp_path),
        model="configured-model",
        api_key="secret-key",
        transport=transport,
    )

    result = runner.run(Role.KRONIKAS, role_payload())
    request = json.loads(observed["body"])

    assert observed["url"].endswith("/v1/responses")
    assert observed["headers"]["Authorization"] == "Bearer secret-key"
    assert b"secret-key" not in observed["body"]
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["additionalProperties"] is False
    assert result["_meta"]["total_tokens"] == 130
    assert len(result["_meta"]["prompt_sha256"]) == 64


def test_runner_extracts_nested_output_text(tmp_path) -> None:
    def transport(url, headers, body):
        return {
            "id": "resp_2",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(opinion())}]}
            ],
        }

    runner = OpenAIResponsesRoleRunner(
        write_prompt(tmp_path), "configured-model", "secret", transport
    )

    assert runner.run(Role.KRONIKAS, role_payload())["verdict"] == "support"


def test_model_and_key_are_required(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ARTHUR_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(KerekasztalError, match="ARTHUR_OPENAI_MODEL"):
        OpenAIResponsesRoleRunner(write_prompt(tmp_path))


def test_prompt_registry_rejects_missing_role_file(tmp_path) -> None:
    registry = PromptRegistry(tmp_path)

    with pytest.raises(KerekasztalError, match="cannot load prompt"):
        registry.load(Role.ARTHUR)
