# Nemotron advisory integration — 2026-10-12

This extends the existing Arthur installation at `C:\AI-Work\GoalAnalysis`.
No new project, subscription, SDK dependency or hosted application was created.
Existing NVIDIA access is used; request limits are not a monetary cost cap.

## What runs automatically

- After a new manual run is saved: up to two independent tasks run in parallel,
  assessing shared fixture exposure/model-calibration caveats and data/quote
  coverage. They inspect the saved publication, not current market conditions.
- In the existing nightly workflow: the same run audit happens after the
  archive/freeze stage, followed by at most one research-learning review of the
  saved daily learning report. The 22:00 Europe/Berlin schedule is unchanged.
- The learning review receives research counts, calibration evidence and
  deterministic proposals from the historical report. It does not receive the
  actual-money ledger or arbitrary free-form report content.

The model prioritizes existing evidence-linked proposals. No model-generated
prediction, price, stake, membership change or code change is accepted. Numerical
calculations remain local and verifiable. Agreement among workers of the same
model is not independent validation. This add-on is not training model weights,
not a profit guarantee and not a replacement for Astra or A Szent Grál.

## Access and bounds

Local opt-in: `config/nemotron.json`, with `schema_version: 1` and boolean
`enabled: true`. Missing/invalid configuration disables requests. The committed
example defaults to false. Set enabled to false to stop new Nemotron requests;
saved evaluations remain readable. Never put a key in this file.

The existing `C:\Users\HP\.codex\nemotron\Invoke-NemotronAgents.ps1` helper
uses the Windows-encrypted credential already stored for the same user/machine.
Python, the dashboard and prompts never read the plaintext credential. Child
processes do not inherit sports, OpenAI, NVIDIA or proxy environment secrets.
The versioned local `src/goal_analysis/agents/nemotron_bootstrap.ps1` establishes
UTF-8 output before calling the fixed shared helper. Only its child process uses
the same execution-policy launch option as existing Arthur scripts; no machine
or user policy is changed. The bootstrap hash is archived with advisory results
and included in new strategy source manifests.

- Exact model: `nvidia/nemotron-3.5-lightning-30b-a3b`.
- Up to 2 ticket workers per saved run; 1 worker per saved learning day.
- 768 output tokens per worker; thinking disabled; 30-second request timeout.
- Bounded helper runtime/output and prompt size; zero automatic retries.
- Authentication failures, timeouts, invalid JSON, wrong evidence IDs and
  incomplete responses remain visible failures, never invented success.
- Known token totals and unknown usage are displayed separately; unknown is not
  zero. An interrupted/failed request may still have incurred provider usage.

See the [official NVIDIA endpoint reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-5-lightning-30b-a3b-infer).

## Storage, integrity and recovery

`data/arthur/nemotron.sqlite3` contains two append-only tables:
`advisory_claims` and `advisory_results`. A unique `(scope, subject_id)` claim is
committed before any helper invocation. A run uses its run ID; learning uses the
report's Berlin date. Content hashes detect changed inputs or stored results.
Repeated invocation returns the stored result; an uncertain claim does not
launch another request. A failed attempt is intentionally not silently retried.

This store does not alter `ledger.sqlite3`, frozen packages, recommendations,
actual wagers or the original learning report. Supervisor overlays are removed
before identifying the original learning report. Saved ticket hashes are checked
before nightly reuse and standalone review. Transient minimized prompt files
are removed after a helper call; raw model prose and provider errors are not
persisted in public status or reports.

Standalone review of an existing immutable artifact (no sports/Astra calls):

```powershell
.venv\Scripts\python.exe scripts/run-arthur-nemotron.py --run-id <saved-run-id>
.venv\Scripts\python.exe scripts/run-arthur-nemotron.py --learning
```

These commands can initiate NVIDIA requests for an unclaimed artifact. They use
the existing application lock. Do not remove reservations to retry a failure.
There is no bulk historical backfill or replay of completed nightly jobs.

## Dashboard

**Működés → NVIDIA Nemotron · Második vélemény** displays run and learning
results, evidence cutoffs, timestamps, policy/model IDs, priorities and usage.
**Tipster klub** links to that panel. `GET /api/nemotron` is local-only, read-only,
no-store and never creates a database, claims work or calls a model. Page polling
and the saved-results refresh button only read existing results.

Local configuration, credential-file presence and a successful past API reply
are separate signals. A past success is not proof of continuous availability.
An old running dashboard process needs an ordinary restart to load the new
endpoint; a browser reload alone only updates static frontend files.

## Verification

Mocked tests cover bounded transport, durable duplicate prevention, redaction,
strict output schemas, actual-money data exclusion, immutable snapshots, learning
identity, failure accounting and read-only HTTP behavior. Frontend assertions
cover successful, partial, unavailable, disabled and unknown-usage states, text
escaping, refresh throttling and stable expanded details. Production evidence
and the exact final test counts are recorded in `CURRENT_STATE.md`.

Operational follow-up: observe the first new 22:00 nightly pass with this add-on.
The PC must remain awake/online with the same Windows user logged in. Existing
service quotas and Codex/Astra availability remain separate dependencies.
