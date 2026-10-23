> October 12 addition: **Nemotron second opinion** audits saved ticket runs and
> ranks evidence-linked research-learning proposals. It uses the existing local
> encrypted NVIDIA credential, with no automatic strategy/team/stake changes.
> Open **Működés** for saved results and usage; refreshing does not call a model.
> [Operation, request limits and verification](docs/NEMOTRON_ADVISORY.md).

> Arthur v3.11: **Tipster klub** adds twenty fictional personalities alongside
> the ten existing profiles. Different preferences, shared data/models, a separate
> dashboard and Szent Grál supervision. Up to thirty profile recommendations,
> not thirty guaranteed tickets or automatic bets. New entrants start on probation.
> [v3.11 operation, verification and limits](docs/RELEASE_V3_11.md).

> Arthur v3.10: **A Szent Grál** supervises the live recommendation team with
> audited promotion/suspension, a separate audition area, earned recognition,
> verified training plans and bounded GitHub source scouting. LAPLACE joins on
> probation. No automatic wagers or arbitrary downloaded-code execution.
> [v3.10 policy, operation and limits](docs/RELEASE_V3_10.md).

> Arthur v3.9: a durable 22:00 Europe/Berlin nightly workflow, immutable daily
> archives and full-history advisory learning. Task registration and release
> verification are separate deployment steps, not implied by this document.
> [Windows v3.9 operation and limits](docs/windows-release-v3.9.md).

> Arthur v3.6.1: all found API prices are used independently of timestamp and
> strictness. Old or missing times are informational only.
> [Release and update details](docs/RELEASE_V3_6_1.md).

> Arthur v3.6: API-Football odds recovery with existing credentials and budgets,
> per-fixture coverage and original quote timestamps.
> [Release and update details](docs/RELEASE_V3_6.md).

> Arthur v3.5: automatic Astra research, all enabled daily profile recommendations,
> and persistent results for priced and unpriced selections. See
> [release and update details](docs/RELEASE_V3_5.md).

# GoalAnalysis — Arthur

The v3.4 empty-odds fix remains: real fixtures stay visible and unpriced,
clearly labelled previews can be generated at low veto. The older
`Arthur-v3.4-frissites.ps1` is a historical updater, not the v3.9 release.
Use the [current in-place update instructions](docs/windows-release-v3.9.md).


Arthur is a local football research dashboard with independent ticket profiles,
Astra analysis, a fixed EUR 5 manual wager ledger and actual/research performance
charts. The selected priced portfolio defaults to two tickets, with a maximum
of five including the independent 2×2×3 ticket. Daily recommendations separately
cover enabled, non-suspended profiles; nine existing active members, LAPLACE
and twenty new tipster personalities are available when all are enabled.
The twenty new personas and LAPLACE start in the separate probation cohort.
The application never places a wager.

## Windows setup

Close the previous Arthur server's PowerShell window before starting the new
version. In PowerShell, from the updated repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\install-arthur.ps1"
```

The installer preserves existing provider settings, installs into `.venv`, runs
tests, loads the two sports-service keys, verifies Codex/ChatGPT access, updates
the desktop shortcut and registers the v3.9 nightly checker. Its business-time
gate is 22:00 Europe/Berlin, not the older 07:30 Windows-local schedule. Use the
`Arthur Goal Analysis` desktop shortcut for the dashboard. Python 3.11+ is needed.

Arthur v3.2 uses the official **Codex CLI with Sign in with ChatGPT**. It consumes
the existing plan's Codex allowance. No OpenAI Platform API key, API credit top-up
or API fallback is used. The configured model remains `gpt-6-astra`; availability
must be verified for the signed-in account. Node.js LTS/npm is required to install
Codex; the installer handles CLI installation and opens its browser login.
The connection check must succeed before the new schedule is registered.
The PC must be awake, online and the same Windows user logged in for scheduled
runs. Automatic wake-up is off by default. See the current
[v3.9 schedule and recovery instructions](docs/windows-release-v3.9.md), alongside
the [Windows setup and daily-use guide](docs/ARTHUR_WINDOWS.md).

## Nightly operation and advisory learning

The Windows checker runs every 15 minutes and at the configured user's login.
Arthur's local gate handles Berlin daylight-saving time: at 22:00 it targets the
following Berlin calendar day. A missed or unfinished job can resume only before
06:00 the following morning; older days are not automatically backfilled.
Outside the due window, timer checks do not make provider or model requests.

The durable workflow refreshes past results, generates all configured profiles,
archives the report and freezes an eligible daily package, then produces an
advisory learning snapshot. Retryable stages have at most three attempts with
15- and 30-minute delays, still bounded by the overnight cutoff and request
budgets. Authentication/quota blocks and uncertain interrupted model execution
are not permission to repeat model spending. An existing manual daily freeze
always wins and cannot be overwritten by the scheduled run.

The dashboard's **Működés** page shows the next Berlin slot, target date,
last success/retry/block state, stage progress and the registration snapshot's
verification time. That snapshot is not a fresh Windows Task Scheduler query on
every page refresh. The **Hibaminták és ellenőrizhető javaslatok** panel adds cutoff-aware
statistics and evidence-linked proposals over the complete saved history; the
existing monthly performance views remain available.

Actual-money wagers, unplayed research and frozen packages are separate cohorts.
Frozen packages can overlap the others and must not be added to them. Ticket and
individual-selection hit rates remain distinct; small samples and missing data
are disclosed. Only actual wagers have financial returns in the learning report.
The LLM proposals never rewrite strategy code, settings or stakes. In v3.10 the
separate **A Szent Grál** controller can automatically change live team membership
using prospective, price-aware evidence. See the Szent Grál tab for its gates,
decision log and reversible owner overrides. This is not a profit guarantee.

The learning reviewer may make at most one Codex model request per Berlin day;
a durable reservation prevents a second request after an uncertain crash. This
limit is specific to learning, not the existing ticket-generation review.
It uses the existing ChatGPT/Codex allowance, with no separate OpenAI Platform
API billing or fallback. No-data or unavailable-AI days retain a deterministic
report. Unknown token/cost values remain unknown, not fabricated zeros.

## Daily workflow

The **Tipster klub** tab shows all twenty new characters, their preferences,
individual combined-odds targets within 7–50, and their saved daily recommendation
or explicit reason to abstain. Use each card's participation switch and save the
selection for the next analysis; this does not rewrite an archived day or change
the member's Szent Grál status. A character gives at most one current recommendation
per date, only when a valid combination exists. Different personalities may share
selections: their agreement is not independent corroboration. The catalogue is
fictional and does not copy paid tipsters or claim their past returns.

New persona output begins with a new analysis after upgrading/restarting Arthur;
old days are not populated retrospectively. The existing selected-portfolio
maximum of five and fixed EUR 5 manual-wager rule are unchanged. The club itself
does not place or automatically record bets.

The club's **1 € próbamérleg · a teljes csapat** panel tracks all daily profiles,
not only the twenty newcomers. Every canonical daily recommendation has a
hypothetical EUR 1 assumption; only original, verifiable prices enter the priced
financial sample. The complete daily log retains missing-price records with
their exclusion reason. Monthly/all-time summaries separate amount committed,
returned money, settled net result and pending exposure. A negative cash balance
while tips are pending is not a settled loss. Incomplete price coverage is not
presented as the whole team's profitability. Original tickets/odds remain in
SQLite and the view can be downloaded as JSON; actual wagers remain separate.

1. Select today or a date up to seven days ahead, ticket count and strictness.
   Choose the leagues, cups and national competitions shown for that day.
2. Start analysis. Arthur collects real fixtures/history/odds, constructs the
   independent profiles and requests sourced Astra review.
3. Inspect tickets, arguments, risks, source links and any coverage issues.
4. If you manually play a ticket, record it with the actual accepted combined
   price. Only these records count toward actual EUR 5 profit and loss.
5. Refresh results or let the next scheduled run update them. Performance shows
   actual and hypothetical outcomes separately; uncertain settlement remains
   visible for review. Exported feedback can also be shared in chat.

After a completed run, **Teljes csomag rögzítése** saves that day's full set of
profile recommendations before kickoff. The frozen package appears in Results
with later outcomes and has a separate monthly hit-rate view in Performance.
Only one frozen package is allowed per day; reruns retain the original snapshot.

The five specialist profiles cover FT over 2.5, BTTS yes, home/away winner,
under 2.5 and draw. DAILY_223 uses separate 2/2/3 price roles. H1 over 0.5 is an
experimental additional profile. GAUSS (goal models) and KEPLER (rating/form
models) are independent local numerical paper-test profiles; their estimates
are not calibrated guarantees and do not create additional automatic stakes.
Invalid or unavailable data produces visible
coverage diagnostics; it cannot produce a fabricated complete ticket.

The local dashboard uses only bundled assets, with no CDN dependency.

Arthur v3.3: set the data-veto slider to **0–30** to keep real priced selections
when history or context is missing. Tickets show prominent warnings and remain
drafts when supporting evidence is insufficient. Quote timestamps never veto
a price or downgrade a ticket. If the target odds
cannot be reached, an explicitly labeled fallback or single selection can be
shown. DAILY_223 keeps its own 2/2/3 definition.

Existing saved keys, ChatGPT login, settings, ledgers and frozen packages are
preserved during an in-place program update and restart of
`scripts/start-arthur.ps1`. The v3.9 timing change additionally requires inspecting
and registering the new Windows task definition; replacing source files alone
does not replace an old trigger. Do not repeat key/login setup unless a diagnostic
identifies a problem. Quote timestamp handling still works at every veto level.

## Development and manual commands

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/run-arthur.py --check-config
python scripts/run-arthur.py --check-codex-login
python scripts/run-arthur.py --check-openai
python scripts/run-arthur-nightly.py --check-schedule
python scripts/run-arthur.py --live --settle
python scripts/arthur-dashboard.py --open
```

Manual runs expect `API_FOOTBALL_KEY` and `THE_ODDS_API_KEY` in their environment,
plus a saved `codex login` using ChatGPT. Windows launchers load the saved
DPAPI sports credentials. Old encrypted OpenAI keys are not loaded or used.
Local settings live in `config/arthur-settings.json` and
`config/daily223-live.json`; examples are versioned, private values are not.

- [Current implementation state](CURRENT_STATE.md)
- [Windows v3.9 nightly operation and learning](docs/windows-release-v3.9.md)
- [Release validation and limits](docs/RELEASE_V3.md)
- [ChatGPT/Codex migration](docs/RELEASE_V3_2.md)
- [Recovered strategy research](docs/research/STRATEGY_RESEARCH.md)
- [Windows installation](docs/ARTHUR_WINDOWS.md)

Historical research is evidence to evaluate, not proof of future profit.
Displayed ticket net results exclude API and other service costs.
