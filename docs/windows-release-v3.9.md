# Arthur v3.9 — Windows nightly workflow and advisory learning

This is an in-place update of `C:\AI-Work\GoalAnalysis`, not a new project.
Existing credentials, ChatGPT login, settings, SQLite history, reports and
manually frozen packages are preserved. The application still never places bets.

This document describes the implemented workflow and deployment procedure. It
does not certify that a real Windows task has been registered, that the complete
test suite has passed, or that a live nightly provider/model run has succeeded.
The current verification record belongs in [CURRENT_STATE.md](../CURRENT_STATE.md).

## 1. Timing, availability and recovery

The native task uses a UTC-aligned 15-minute checker and a current-user login
trigger. The Python gate, not the Windows host's local clock setting, determines
whether **22:00 Europe/Berlin** is due. Berlin summer/winter time is handled
automatically. The target is the following Berlin calendar day's fixtures.

An unfinished or missed job may resume during the overnight window, strictly
before **06:00 Europe/Berlin**. At the cutoff it expires; old days are not
automatically regenerated. A 15-minute checker is best-effort scheduling, not
an exact-second or always-on execution guarantee. Checks outside the due window
do not call football, odds or model services.

The same Windows user must be logged in, with saved sports credentials and valid
ChatGPT/Codex access. The computer must be awake and online. The default task
does **not** wake the computer. Explicit `-WakeToRun` registration permits wake
timers subject to Windows power policy; because the timer repeats every 15
minutes, this permission applies during the day as well. It does not remove the
login/network requirement or make a powered-off machine available. No cloud
scheduler or unattended password-bearing Windows account is installed.

## 2. Durable nightly stages

1. Refresh outcomes for previously recorded matches, deduplicating shared
   fixtures and retaining uncertain/missing results for review. Pending matches
   of any age and completed matches within the seven-day correction window are
   eligible; the 20-call budget can leave a disclosed backlog for later nights.
2. Generate the following day's recommendations for every configured enabled
   profile using a saved settings/configuration snapshot. All nine profiles can
   be present; this is distinct from the selected priced portfolio's five-ticket
   maximum. Missing data produces an explicit status, not invented selections.
3. Archive the completed report with provenance and freeze an eligible daily
   recommendation package before kickoff. A previously frozen manual package
   is preserved, even if the scheduled run has a different run ID.
4. Build the full-history learning snapshot and, when eligible, request a bounded
   evidence-based priority review through the existing Codex subscription access.

The job record is durable and unique per target date. The shared Arthur run lock
prevents concurrent manual/scheduled execution. Completed stages are not replayed;
already finished jobs return without generating a second package. Completed
generation saved in staging or the ledger is reused when local export/persistence
needs recovery. An interrupted generation with no trustworthy completed checkpoint
is stopped for review instead of automatically spending again.

Retryable stage failures have at most **three attempts in total**, with 15- and
30-minute retry delays. Retries remain subject to the 06:00 cutoff and remaining
budgets. Authentication failures, quota blocks and configuration errors are not
unbounded retry loops. A partially covered or blocked day remains visible; a
successful workflow does not guarantee that every profile found a valid ticket.

The existing configured football-generation call cap is shared across that job's
generation retries; result refresh has a separate bounded allowance of 20 calls.
These are nightly-workflow limits, not an account-wide cap covering independent
manual runs. Provider quota limits still apply. Requests are reserved before
external calls so crashes cannot silently replenish the allowance. The Odds API
feed is used only on the first generation attempt under its configured credit
limit; retrying the job does not authorize replaying its paid feed quota.

## 3. Archive and strategy provenance

The original report is retained in `data/arthur/ledger.sqlite3`; exported files
are under `reports/arthur/`. Durable intermediate bundles are under
`data/arthur/nightly-staging/`. The workflow records stages, attempts, reservations
and events in `data/arthur/automation.sqlite3`.

New reports include a strategy manifest and hashes identifying relevant program
logic, runtime prompts, settings and model-library versions. Recommendations
retain their strategy version and original model probabilities. A hash identifies
the source/version; it is not itself a complete source-code backup or proof of
predictive quality. Historical records are never relabelled using today's
settings; unknown legacy versions stay unknown.

Only one frozen package is permitted per target date. A rerun cannot overwrite
it, and a package containing a started selection is not silently frozen after
the fact. Missing selections or passed kickoffs produce a disclosed partial
archive outcome. Later settlement is displayed against the original recommendation
IDs; it does not rewrite the frozen inputs, odds or probabilities.

## 4. Full-history learning, not automatic strategy changes

Each report measures the entire saved local history available at its explicit
`as_of` cutoff. It is not limited to the selected month and is not the deferred
multi-year external match-database simulator. The dashboard retains the existing
monthly views and adds an all-history panel with recent daily snapshot selection,
profile/market/version groups, findings, proposals and their evidence.

The first report for each Berlin day is immutable. Later results appear in a
later day's report, not by revising the first snapshot. Reports and daily request
reservations are stored in `data/learning/learning.sqlite3`; optional JSON exports
are under `reports/learning/`.

Three cohorts remain separate:

- **Actual:** manually recorded wagers and their accepted stakes/prices; only
  this cohort has financial returns in the learning report.
- **Research:** unplayed recorded recommendations, including unpriced selections;
  no invented stake, missing price or hypothetical financial return is added.
- **Frozen:** selections in immutable daily packages; this cohort can overlap
  actual/research and must not be summed with them.

Ticket hit rates and individual-selection hit rates use their own denominators.
Pending, void and review states remain distinct. Shared fixtures are not
independent observations. Small samples, missing prices, unknown strategy versions
and coverage gaps are disclosed. Model calibration diagnostics are descriptive
and selection-biased; they do not prove calibration, causality or profitability.
Actual net results exclude service costs.

Statistics and proposal text are produced deterministically. The learning model
may only prioritize valid supplied proposals with verified evidence IDs; it may
not invent figures or unrestricted strategy claims. There is no “apply strategy”
control and no automatic change to live code, settings, weights, stakes or wagers.
Changes require a later explicit human decision and implementation.

## 5. AI allowance, privacy and failure behavior

The learning reviewer allows at most **one Codex analysis launch per Berlin day**.
It durably reserves that attempt before launching the reviewer. Missing data can
skip the model entirely. Authentication/quota/model failures, invalid responses
or a crash with uncertain execution do not trigger a second learning request
that day. The CLI may perform its own bounded network recovery; the limit is on
Arthur's analysis launches, not a guarantee about transport-level retries.
The deterministic evidence report is retained. This daily limit applies
to learning, not the existing ticket-generation research/review calls.

The configured Astra request uses official Codex CLI with the existing ChatGPT
login and allowance. There is no separate OpenAI Platform API billing, API-key
fallback or paid model substitution. This is not a promise of unlimited/free
capacity: the signed-in plan's limits and model availability still apply. Measured
usage is shown when available; unknown tokens or costs are not fabricated as zero.

Sports credentials remain in existing Windows user-scoped DPAPI storage. The
learning model receives bounded evidence and allowed proposal IDs, not sports
keys or the CLI authentication store. Its operation is read-only and tool-disabled.
Public job failures and launcher logs use safe status categories/static messages,
not raw provider/CLI responses or secrets.

## 6. Inspect and register the existing Windows task

Use the existing project and user account. A source-file update and server restart
alone do not migrate the old 07:30 task definition.

Read-only inspection of the existing task and proposed v3.9 definition:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\register-arthur-schedule.ps1" -CheckOnly
```

After reviewing the proposal, registration/replacement is an explicit mutation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\register-arthur-schedule.ps1"
```

The registrar targets the exact `Arthur Goal Analysis Daily` task and recognized
Arthur DAILY_223 predecessors in the root task folder. It validates ownership,
refuses unrelated/custom or running tasks, preserves old XML definitions in
`data/arthur/schedule-backups/`, and checks the registered definition by read-back.
Recognized legacy tasks are disabled to avoid duplicate runs. No password, key
replacement, provider request or model probe is part of this registrar.

The dashboard and the following diagnostic read the saved registration/job
snapshot without launching the nightly job or decrypting credentials:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\run-arthur-scheduled.ps1" -CheckOnly
```

`schedule-status.json` includes `verified_at`. It records the last verification;
it does not guarantee that someone has not subsequently changed/disabled the
native Windows task. Inspect Task Scheduler itself when diagnosing that case.
Do not use an actual scheduled/manual run merely to check registration: a due
run can consume provider and Codex allowances.

## 7. Deployment checks still required

Before calling the local rollout verified, record the source/test checks, review
the dashboard with representative empty/partial/completed states, inspect and
read back the actual Windows task, and confirm its current-user/wake settings.
Live availability of sports data, markets, internet, saved credentials and Astra
remains an operational condition, not something a synthetic test can establish.
Keep existing databases and original freezes intact throughout deployment.

See also [README](../README.md), [workspace boundaries](../WORKSPACE.md),
[Windows setup](ARTHUR_WINDOWS.md) and [GAUSS/KEPLER limitations](RELEASE_V3_8.md).
