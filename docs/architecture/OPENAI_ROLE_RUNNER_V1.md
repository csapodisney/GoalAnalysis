# OpenAI Role Runner v1

## Purpose

The runner connects the provider-neutral Kerekasztal orchestration contract to the OpenAI
Responses API. It uses strict structured output so role responses can be validated before
Arthur sees them.

Official implementation reference:
https://developers.openai.com/api/docs/guides/structured-outputs

## Configuration

Secrets and model choice are environment variables:

```powershell
$env:OPENAI_API_KEY = "your-local-key"
$env:ARTHUR_OPENAI_MODEL = "your-chosen-model-id"
```

Neither value is committed to Git. No default model is hard-coded.

## Prompt versioning

Role prompts live under:

```text
config/prompts/<version>/<role>.txt
```

The initial `kerekasztal-v1` files are concise role-contract prompts derived from the preserved
baseline responsibilities. The complete legacy Arthur prompt was not present in the repository
and was therefore not reconstructed or silently changed. When supplied, it must be imported as
a new immutable version.

Every call records the prompt version and SHA-256 prompt hash.

## Output and usage metadata

Each role response records:

- provider and returned model ID;
- response ID;
- prompt version and prompt hash;
- input, output and total tokens reported by the API.

The API key is sent only in the authorization header and never enters the request body or output.

## Shadow command

```powershell
.venv\Scripts\python.exe scripts\run-kerekasztal-openai.py `
  reports\daily\arthur-screening-2026-09-20.json
```

This remains a shadow run. It does not place bets and does not attach prices.
