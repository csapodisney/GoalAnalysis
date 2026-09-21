# Goal Analysis — current state

Updated: 2026-09-21. Release: Arthur portfolio v3.6 (existing-subscription odds recovery).

## Source and objective

The working baseline was verified against GitHub commit
`6fb7bc940f19d5466839761d9dae5c3ca46e692d` on
`build/token-efficient-pipeline`. The v3 work is prepared on
`build/arthur-dashboard-v3`; the older pipeline remains available.

The user approved a modern local dashboard with independent ticket strategies,
real Astra review, selectable dates, adjustable evidence strictness, recorded
results and a daily Windows run. Profit is an evaluation objective, not a
promised outcome. The application does not place bets.

## Approved portfolio rules

- Default two tickets per day; at most five total, including DAILY_223.
- Actual wagers are manually confirmed at a fixed EUR 5 per ticket.
- Independent profiles: Krónikás (FT over 2.5), Ritmusőr (FT BTTS yes),
  Párharcmester (FT home/away win), Őrszem (FT under 2.5), Merlin (FT draw).
- DAILY_223 independently builds three distinct fixtures at 2/2/3 minimum odds.
  Its existing maximum 2% tolerance remains; minimum combined odds 11.76.
- H1 over 0.5 is experimental. At low veto, missing half-time samples are
  explicit warnings; actual H1 market quotes are always required.
- Other profiles target combined odds 10–40. Incomplete data cannot be replaced
  by invented fixtures, odds, results or motivation claims.
- Betano is preferred when available; a complete alternative bookmaker is valid.
- Dates from today through seven days ahead can be researched. Earlier dates
  show archived records only. Match kickoff and quote freshness are rechecked
  after Astra review.
- At strictness 0–33, soft evidence gaps do not veto construction. Lower-odds
  and single-selection fallback drafts may be published with visible warnings.
  Old provider prices remain indicative, with unchanged timestamps and refresh
  warnings. Invalid dates, missing/invalid prices and started events remain out.

## Implemented in v3

- Shared collection and historical enrichment, separate profile builders,
  bounded ticket search and exact duplicate-ticket exclusion.
- Official Codex CLI integration using ChatGPT login and requested
  `gpt-6-astra`, medium reasoning, source-linked web context and structured
  candidate reviews. Inputs, candidates and per-job runtime are bounded;
  unchanged review packets use a separate cache. Token/search counts are
  measured, not hard-capped. No Platform API fallback is available.
- Versioned `arthur-portfolio-v3` prompts alongside existing prompt versions.
- Localhost dashboard: daily tickets, date picker, settings, actual/research
  results, performance charts, feedback export and Codex connection diagnostics.
- Immutable report and ticket snapshots, explicit actual wager recording,
  aggregate profile feedback reused in later Astra packets.
- Outcome refresh with FT/half-time semantics, bounded correction checks,
  review of uncertain results, explicit bookmaker void/payout confirmation.
  Actual and hypothetical financial performance are kept separate.
- API and service costs are not included in displayed ticket net profit; no
  fabricated currency estimate is shown when cost data is unavailable.
- New Windows installer, encrypted user-scoped credentials, desktop shortcut
  and daily 07:30 Windows-local schedule. The PC must be awake, online and the
  same user logged in. The new schedule includes settlement and ticket creation.

## Prior research recovered

The recovered Arthur report describes 20,248 matches, not 27,000. Its reported
O2.5 triple results are hypothetical and contain substantial losing periods.
The original raw-data ZIP could not be downloaded successfully, so the study
was not rerun or imported as verified training data. No recovered H1 over 0.5
backtest supports a claim of proven profitability. See
`docs/research/STRATEGY_RESEARCH.md` for figures, limitations and source links.

## Validation and remaining live checks

Validation: 365 Python tests, Ruff, JavaScript syntax and Chromium desktop/mobile
checks passed, including visible warning/fallback cards and the zero-veto slider.
Tests use controlled provider and model
responses; no live Codex login is available in the development environment.
The migration details are in `docs/RELEASE_V3_2.md`; the new data-veto behavior
and update procedure are in `docs/RELEASE_V3_3.md`.
The browser interface is also exercised against the real local HTTP server with
isolated, explicitly synthetic test records.

The complete suite and browser checks were rerun successfully on 2026-09-21.
The focused strategy follow-up is complete in `docs/research/STRATEGY_RESEARCH.md`:
opponent-adjusted Over 2.5, Dixon–Coles draw benchmarking, then experimental H1
Over 0.5. No new backtest or profitable strategy certification is claimed.

The user's Windows installation must verify Codex ChatGPT login and native
sandbox execution, DPAPI, Task Scheduler, the shortcut and Astra access. The
installer performs a real Codex model probe and stops on failure. A live run must confirm
that the configured football and odds feeds supply that day's markets.

Secrets, local configs, raw data, reports and the ledger are excluded from Git.
Never copy API keys into chat, logs, reports or repository files.

## Delivery

The cumulative Arthur v3.5 source is published on GitHub branch
`build/arthur-dashboard-v3`. The first published release commit is
`fad7453a3057482d4d0f4459ffd6e0c7a0c89418`. All 188 tracked files were verified
against the local committed source by the Git tree hash. The existing main and
build/token-efficient-pipeline branches were preserved.

The tested Windows installer remains Arthur-v3.5-frissites.ps1. Local settings,
credentials, reports and SQLite databases are excluded from source publication.

## Latest decision: use the existing ChatGPT/Codex allowance

The user confirmed encrypted OpenAI-key storage and config validation, but the
first Platform model probe returned HTTP 429. The user then declined separate
API billing and the v3.1 diagnostic download. Do not ask them to top up API
credits or return to API authentication. Actual Astra access remains unverified.

Arthur now launches `codex exec` with forced ChatGPT login, read-only sandbox,
isolated temporary working directory, disabled command tools/integrations and
no API/service keys in the child environment. It never reads the CLI auth
store. Exact requested model completion is distinguished from server-resolved
model metadata, which the JSONL stream does not provide. Citations come from
Codex after an observed web-search event; Arthur does not independently fetch
those source contents. Unknown failures produce static messages without raw logs.

The Windows installer installs the official CLI when needed, opens ChatGPT
login, checks the model through the subscription and only then schedules the
job. Saved sports keys/config/data remain intact. A local subscription limit,
expired login or unavailable Astra model stops the job without substitution.
The CLI requires Node.js/npm for installation. The previous encrypted Platform
key is no longer loaded by Arthur's launchers.

## Latest user feedback: the dashboard produced no tickets

The user has the dashboard running but received no tickets even for Saturday.
They explicitly requested that lowering the veto must admit missing background
data and show warnings rather than stop. The prior unconditional history/venue
gate, five-minute quote removal and all-or-nothing history collection prevented
that behavior. These were reproduced and changed; the user's exact live run
artifacts have not been inspected.

Portfolio collection now retains successful league history and fixture results
when other leagues fail. At low veto, valid priced candidates survive missing
history and old quote timestamps; Codex receives them using the same eligibility
rules. Warnings stay in the ticket, ledger and export and appear openly on cards.
Targets remain preferred, with explicit lower-odds/single fallback drafts when
necessary. DAILY_223 naming and its 2/2/3 price roles remain intact. The cap is
still five. No automatic wagers or separate API billing were added.

Existing installations need only program-file replacement and a server restart,
then a fresh run at slider 0–30. Do not make the user repeat key/login setup.

## Startup credential recovery

The user then reported that launch fails decrypting THE_ODDS_API_KEY. The old
writer used Set-Content (with a trailing newline), while readers forwarded raw
text directly to ConvertTo-SecureString. This is a concrete defect candidate;
the user's encrypted bytes and Windows profile have not been inspected.

v3.3.1 introduces a shared DPAPI helper: trim legacy encrypted input, never
rewrite saved credentials during launch, write encrypted data without a newline,
verify disk read-back, then atomically replace the selected key. Blank or failed
new input preserves the old file. The installer asks again only for the key it
cannot read. The scheduler uses the same reader without interactive prompts.

`configure-arthur-sports.ps1 -Launch` first tries the saved Odds key, asks for
only that key if necessary, and opens Arthur. It does not contact sports services
or OpenAI and does not change Codex login or scheduling. The dashboard itself
now opens even when a sports credential cannot be loaded, with a console warning
and missing-credential status in the UI; new live analysis still needs the key.

Delivery: Arthur-v3.3.1-inditas.zip is a small overlay for the existing v3.3
installation. The existing 365 Python tests pass. One new real-Windows DPAPI
test is skipped on Linux; actual decrypt/encrypt is verified by the repair
command on the user's Windows account. See docs/RELEASE_V3_3_1.md.


## v3.4 — empty odds response no longer hides matches

The user's September 27 export showed five fixtures, zero priced candidates,
18 football requests and one odds request (provider last_cost=0, remaining=500).
Four league queries failed, but the old report discarded their causes. Their
precise live cause is not established by that export.

Portfolio collection now requests the full Berlin-date calendar once, filters
configured leagues locally and retains the provider season. History is requested
only for active leagues, within the remaining call budget. Legacy DAILY_223
collection remains unchanged. Missing/exhausted odds credentials or an empty
response no longer blocks calendar collection.

Reports include fixture names, source IDs, seasons, calendar metadata and odds
query event counts. The dashboard always renders this calendar. When prices are
missing, independent specialist profiles can provide ODDS_PENDING previews;
at strictness 0–33 missing historical support is visibly disclosed. These previews
have null prices, no bookmaker, no recorded stake and no financial ticket ID.
They are retained in run exports, not in the wager or research-return ledger.
DAILY_223 is never claimed without its required actual price slots. Preview
candidates use the same bounded Codex review, and started legs are removed after
review. All quote requirements remain enforced for priced tickets.

Validation: 372 tests pass; one native Windows DPAPI test is skipped on Linux.
Browser checks cover empty, priced and unpriced runs, export, disabled unpriced
wager controls, and desktop/mobile layout. The five-fixture / zero-cost response
and missing Odds key both produce five disclosed previews at veto zero. These
are synthetic provider tests; no live user credentials or paid service calls were
used here. Native Windows updater execution still requires the user's machine.

The GitHub connector was retried on September 21 and again returned HTTP 403
Resource not accessible by integration for create-tree. No remote branch or
commit was created. The local Windows folder is not mounted in this environment.
The self-contained Arthur-v3.4-frissites.ps1 applies the tested release directly
to C:\AI-Work\GoalAnalysis with per-file verification, backups and rollback.
It does not install packages, alter credentials/configuration, schedule tasks,
call model services, or push via another authentication route.


## v3.5 — every profile, automatic research and persistent recommendation outcomes

The attached Arthur-2026-09-22 export establishes the actual failure: 13 fixtures,
zero priced candidates, and one Codex request. Research completed without a
validated search/source payload, which aborted the second review request. This
was not an absent model invocation. Old token accounting also discarded usage
on this validation failure.

The current user request supersedes limiting the *visible profile recommendations*
to two/five: all seven enabled profiles now publish independently whenever valid
upcoming fixtures exist. The selected priced portfolio retains its separate
two/default and five/maximum cap and explicit EUR 5 manual wager recording.
Missing history and prices do not suppress profile recommendations at any slider
setting. Strictness still governs the selected priced portfolio. DAILY_223 with
unverified 2/2/3 slots is explicitly a target proposal, not a verified DAILY_223.
The empty-calendar case shows NO_FIXTURES; no events are invented.

Astra runs automatically on a bounded, round-robin shortlist covering all profiles.
The existing research request now includes missing-odds lookup and weather, coach,
lineup, absences, workload and motivation coverage. Missing/malformed research
evidence leads to a disclosed supplied-data review instead of cancelling it.
Unobserved web research cannot validate citations or quotes. Quota/auth/model
failures do not trigger API fallback or repeated charged attempts. Actual stage
progress and the model outcome are visible. Confidence is LOW/MEDIUM/HIGH evidence
strength, never a calibrated win probability.

Web prices require exact candidate, teams, kickoff, market, period, source and
observation-cutoff matching. They remain visibly indicative Codex extractions,
not independently fetched bookmaker confirmations or provider quotes; no financial
ticket is fabricated from them. Results, quotes, timestamps and sources remain
in immutable run snapshots. An empty web result is allowed and disclosed.

Every recommendation is now stored in data/arthur/ledger.sqlite3, including
unpriced ideas. Existing frozen v3.4 previews are indexed using original
publication timestamps. Reruns retain all versions; the latest pre-kickoff
recommendation per profile/day counts, and started recommendations cannot be
replaced by a later run. Full-time and first-half results use the existing
provider-result observations. Shared fixture queries are deduplicated and bounded.
The dashboard defaults the monthly view to all recommendation hit rates, with
per-profile breakdown and match-level scores; actual-money reporting stays separate.
Dashboard analysis now refreshes past results as well as the existing 07:30
scheduled run. Windows must be on and the configured user logged in.

Validation: 392 Python tests pass, one native-Windows test skipped. Changed Python
files pass Ruff; JavaScript syntax, real HTTP/SQLite desktop/mobile browser checks
pass, including seven unpriced profiles, result history, export and existing wager
controls. The repository-wide strict Ruff invocation also reports existing style
issues in unchanged legacy files; those were not modified. Live user Codex access,
web coverage and native Windows installation remain local verification items.

Delivery: cumulative Arthur-v3.5-frissites.ps1 with verified file payload, backup
and rollback. Preserves keys, login, local settings, database and reports.

## Verified GitHub publication

After the user explicitly approved public code/documentation disclosure and
updated the GitHub installation, repository writes succeeded. The cumulative
release was committed as fad7453a3057482d4d0f4459ffd6e0c7a0c89418 and branch
build/arthur-dashboard-v3 was created and read back successfully. Its complete
source tree, 1983569336a5a0f81ac1f14a1db765f03b984cf7, exactly matched the
local committed tree (188 tracked files). This follow-up updates only the
publication record. No code, settings, credentials or local runtime data changed.


## v3.6 — existing-subscription odds recovery

The user approved API-Football odds integration with no new subscription.
Recovery is now enabled by default with the existing key. The new optional
api_football_odds_max_calls cap defaults to 12 and stays within the existing
football budget and provider quota. History reserves a bounded share for odds;
local caching and round-robin pagination avoid redundant calls.

Recovery covers exact pre-match products and validates fixture ID, league,
season, kickoff, decimal price and provider update time. Cross-provider bookmaker
identities are never guessed from equal names. Stale indicative quotes remain
visible in recommendations with warnings; the existing ready-ticket and actual
price confirmation gates are preserved. The Odds API's confirmed unused credits
are released. No additional LLM calls, account setup or paid feed was added.

Reports/dashboard disclose query results, source, original time, recovered prices
and remaining product gaps. Provider provenance survives ticket/SQLite storage.
The existing actual-price wager dialog and Astra web/context branch remain.
The newly attached v3.3.1 files were older reference material; current v3.5 code
was the implementation baseline. See docs/RELEASE_V3_6.md.

Validation: 424 Python tests pass; one native-Windows DPAPI test is skipped on
Linux. Changed Python files pass Ruff, JavaScript syntax and Git whitespace checks
pass. Chromium checks against the real local HTTP server/SQLite pass for source
and gap disclosure, export, optional Odds API credential labels and mobile layout.
Synthetic provider responses verify new behavior; no live sports keys are present
here, so actual day/bookmaker coverage remains a local-run check.

Delivery: cumulative Arthur-v3.6-frissites.ps1 and the existing
build/arthur-dashboard-v3 branch. The updater verifies file hashes and preserves
local keys, settings, login, data and reports. No new subscription was created.
