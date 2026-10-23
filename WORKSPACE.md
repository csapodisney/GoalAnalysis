# Goal Analysis Workspace

This directory is the persistent local workspace for Goal Analysis.
The existing Windows installation and source root is
`C:\AI-Work\GoalAnalysis`; v3.11 updates it in place, without creating a new
project or relocating user data.

## Directory convention

- src/         application/source code
- dashboard/   dashboard UI and launcher
- data/        persistent project data
- reports/     generated reports
- analysis/    research and analysis outputs
- logs/        runtime logs
- docs/        architecture and documentation
- artifacts/   generated deliverables
- config/      non-secret configuration
- scripts/     maintenance/deployment scripts
- archive/     deprecated or historical material

## Canonical state

Always read and update CURRENT_STATE.md before and after major work.
Implementation and local deployment/verification status are different: source
presence alone does not prove that the native Windows task is registered or that
the complete release test suite has passed. See
[v3.9 operation and limits](docs/windows-release-v3.9.md).

## Runtime state and immutable records

- `data/arthur/ledger.sqlite3`: existing run, recommendation, actual-wager,
  result-observation and frozen-package records. Preserve it across upgrades.
- `data/arthur/automation.sqlite3`: durable nightly job/stage state, retry
  counters, budget reservations and events. One nightly job per target date.
- `data/arthur/nightly-staging/`: durable completed generation bundles used to
  recover local persistence/export failures without repeating completed work.
- `reports/arthur/`: exported run artifacts; the saved ledger report is canonical.
- `data/learning/learning.sqlite3`: immutable first learning report per Berlin
  day and the pre-request reservation preventing duplicate daily AI reviews.
- `reports/learning/`: optional learning-report exports; export failure must not
  cause another model request.
- `data/arthur/schedule-status.json`: last registration/verification snapshot,
  not continuously refreshed proof of the native task's current state.
- `data/arthur/schedule-backups/`: preserved Windows task definitions from an
  in-place schedule change.
- `data/team/team.sqlite3`: immutable prospective evaluations and reversible,
  append-only live membership decisions by A Szent Grál.
- `data/team/scouting.sqlite3`: bounded weekly GitHub metadata reservations,
  request debits and source-candidate snapshots; never executable downloads.
- `data/arthur/nemotron.sqlite3`: separate immutable advisory claims/results for
  saved runs and daily research-learning reports; never a replacement ledger.
  `config/nemotron.json` enables this local add-on and is ignored by Git; it holds
  no credential. Temporary minimized prompts under `data/arthur/nemotron-tmp/`
  are removed after the helper exits. The encrypted NVIDIA credential remains
  in its existing user-scoped location outside the project.

Do not rewrite past reports with current settings, strategy versions, numerical
probabilities or later results. Provenance identifies the code/policy, settings,
prompts and relevant library versions used by new runs. Legacy unknown versions
remain unknown. Settlement observations and display overlays do not replace the
original immutable report or frozen recommendation snapshot.

## Nightly and learning boundaries

The v3.11 Tipster klub adds twenty fictional personas to the existing ten
profiles. Their catalogue and deterministic selector live under
`src/goal_analysis/tipsters/`; archive, results and team state use the existing
SQLite stores above. No separate project, cloud host or duplicate database was
created. Old settings migrate effectively on read without silently re-enabling
previously disabled old profiles; saved retries retain their original roster.
Enrollment is an explicit locked write before generation, never a dashboard
GET side effect. New personas begin on probation at actual enrollment time,
not a backdated research epoch. See [v3.11 details](docs/RELEASE_V3_11.md).

The native checker is local, every minute plus user login. Odds-only monitoring
uses a separate persistent 30-minute successful-fetch cache and never calls Astra
or regenerates tickets. New publications stop 15 minutes before kickoff.
Python applies
22:00 Europe/Berlin and the next-day 06:00 catch-up cutoff, including DST.
The same Windows user must be logged in and the computer awake and online;
automatic wake-up is disabled by default. This is not a cloud service.

Every enabled profile is considered using the nightly job's saved configuration.
Retryable stages allow three attempts, with 15/30-minute waits and bounded
provider budgets; a missing credential or exhausted quota is a visible block.
Never rerun an uncertain interrupted model request merely to recover output.
Preserve any existing manually frozen daily package.

Learning reads the complete local history as of its explicit cutoff, not just
the dashboard's selected month. Keep actual-money, research and frozen cohorts
separate; disclose overlap, small samples, unknown prices and unknown usage.
The language-model reviewer can prioritize evidence-linked proposals but cannot
modify code, strategy parameters, configuration, stake or wagers. The separately
versioned A Szent Grál controller now **can** promote/suspend live recommendation
profiles under the owner's explicit authorization and strict prospective gates.
Probation stays separate; unknown GitHub code requires an implemented, reviewed
local adapter. See [v3.10 boundaries](docs/RELEASE_V3_10.md). At most one learning model
request is permitted per Berlin day through the existing ChatGPT/Codex login;
no Platform API key or billing fallback is allowed. This does not cap the
separate ticket-generation review to one request.

Nemotron is a separate NVIDIA advisory channel, not a replacement for Astra,
the numerical models, odds feeds or the Szent Grál membership controller. New
manual runs are audited after persistence; nightly runs are audited only after
the archive/freeze stage. At most two parallel ticket workers and one worker
per saved learning day are permitted. A pre-request SQLite claim prevents a
second request after failure, crash or repeated invocation. The model only ranks
provided deterministic proposals and verified evidence IDs; this is not model
retraining or automatic strategy tuning. Actual-money financial details are not
sent to NVIDIA. See [Nemotron operation](docs/NEMOTRON_ADVISORY.md).

## Secrets

Never commit API keys, broker credentials, tokens, passwords or .env files containing secrets.
Windows launchers retain user-scoped DPAPI sports credentials. Do not include
raw provider/CLI errors, credentials or private authentication stores in public
job status, documentation, reports or chat. Read-only schedule diagnostics must
not decrypt credentials or invoke providers/models.
