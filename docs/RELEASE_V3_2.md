# Arthur v3.2 — ChatGPT subscription migration

Date: 2026-09-21. Cumulative release; v3.1 is not required separately.

## Reason and behavior

The user declined separate OpenAI Platform billing. The active Arthur entry
point now uses the official Codex CLI with saved ChatGPT authentication.
There is no Platform API fallback. The old HTTP reviewer remains for existing
tests/legacy code, but the Arthur dashboard and daily runner instantiate only
`CodexReviewer`. Configurations without a backend migrate to `codex_chatgpt`.
An explicitly configured API backend is rejected.

The installer retains the sports credentials and local data, installs the CLI
if needed, opens official ChatGPT login, and runs a real model probe before
registering the schedule. The old OpenAI configuration script redirects to it.

## Execution and evidence

- CLI version 0.155.1 or newer; exact requested model `gpt-6-astra`.
- Saved ChatGPT login must be confirmed; API-key login is rejected. API and
  sports credentials are removed from the child environment. Arthur never
  reads the Codex auth store or uses private authentication endpoints.
- Each job runs in its own temporary directory, using stdin, read-only sandbox,
  no interactive approvals and no shell interpolation. User custom integrations
  are not loaded; shell tools, apps, plugins, hooks and delegation are disabled.
  Managed rules and sandbox controls are not bypassed.
- Research requires an observed completed web-search event and public source
  URLs returned in Codex's JSON. These are model-reported citations after a
  search, not independently fetched or verified source contents by Arthur.
  Existing candidate/evidence/reference validators still apply.
- The JSONL stream does not independently identify the server-resolved model.
  Reports identify the requested CLI model and successful completion; they do
  not fabricate `resolved_model` metadata.
- At most two Arthur-launched jobs per uncached review, 300 seconds each. There
  is no application retry or model substitution. CLI internal recovery may
  reconnect. Inputs, accepted answer size and processed log size are limited;
  the CLI's generated token count and web-search count are not hard-capped by
  Arthur. The four-search instruction is a target. Legacy `max_output_tokens`
  is retained for settings compatibility, not enforced by this backend.
- A separate `data/arthur/codex-cache` prevents reusing the previous API cache.
  Freshness and kickoff checks still run after review; slow or incomplete work
  cannot create a fresh READY ticket.

## Verification

Validation passed: **349 Python tests**, Ruff, JavaScript syntax and
`git diff --check`. Chromium desktop/mobile checks passed against the real local
HTTP handler using isolated synthetic records, with no JavaScript or CSP errors.
Subscription tests cover two-stage structured output, citations, cache reuse,
API-login rejection, quota failure without fallback, secret isolation, literal
stdin transport, Windows npm shim resolution and settings migration.

The official CLI 0.155.1 was installed locally to inspect available commands
and feature flags. No real model request or ChatGPT login was made here.
Windows login, native CLI sandbox execution, account-level Astra availability,
DPAPI and scheduling remain live installation checks on the user's PC.
