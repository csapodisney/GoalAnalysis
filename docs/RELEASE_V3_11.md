# Arthur v3.11 — Tipster klub

Implemented in `C:\AI-Work\GoalAnalysis` on 2026-10-11, verification completed
after midnight on 2026-10-12. No new project,
subscription, cloud hosting, paid feed or model package was introduced.

## What changed

The existing ten profiles remain. Twenty new fictional tipster personalities
have separate names, portraits, preferences, selection weights, target odds and
ticket lengths. They reuse shared evidence and numerical predictions; they are
not twenty independently trained models. The catalogue and selection logic are
versioned in `src/goal_analysis/tipsters/` and included in strategy provenance.

Examples: Bástya Bence prefers stronger individual estimates and a 7–12 total;
Gólvadász Lili prioritizes goal markets; Remi Doma prefers draws; Archív Nóra
emphasizes sourced history; Konszenzus Vera needs two reasonably close model
estimates; Érték Milán requires positive estimated individual value; Rövid Lotti
uses two or three legs; Merész Hunor targets 25–50. These are experimental
preferences, not demonstrated edges or promises of profit.

Each enabled, non-suspended character produces one daily decision: a valid
combination or an explicit abstention. All twenty can therefore be assessed
without inventing twenty tickets. Reruns retain immutable versions; the current
profile/day view follows existing pre-kickoff selection rules. The 30-profile
roster does not raise the selected-portfolio maximum of five or the existing
fixed EUR 5 manual-wager amount. No bookmaker bet is placed automatically.

## Generation and data integrity

- The coordinator calls the persona selector once after the existing numerical
  model batch. It makes no extra provider, web or LLM calls. Available enabled
  GAUSS/KEPLER/LAPLACE results are reused; disabling a source model can reduce
  persona coverage. Sourced historical evidence can be used without inventing
  a model probability. Consensus/value profiles still require model evidence.
- Each selection requires a real, unstarted fixture on the selected Berlin day,
  a supported full-time market and a valid sourced quote matching the event,
  market, bookmaker, provider and any provider cross-reference. Region/currency
  consistency and one bookmaker are required for each combination. A fixture
  appears at most once within a ticket. Original prices and timestamps remain.
- Quote age is **not** a veto: the owner's existing timestamp policy is unchanged.
  Provider provenance checks do not guarantee that a bookmaker will still accept
  the displayed quote. The displayed product is not a verified accepted wager.
- The bounded search uses persona preferences, two to six legs and each persona's
  own odds range within 7–50. It does not promise a globally optimal combination.
  Overlap is disclosed; twenty shared-data opinions are not twenty independent
  observations. Heuristic ranking scores are not probabilities. Individual
  model estimates are not calibrated guarantees; no ticket win probability is
  fabricated by multiplying correlated estimates.
- Missing fixtures, valid quotes, evidence or compliant combinations produce
  distinct abstention reasons. A final publication-time guard removes a ticket
  if any fixture started while the analysis was running. The dashboard only
  exposes legged recommendations accepted by the ledger; it cannot resurrect
  rejected report-only selections.
- Existing immutable reports, quote provenance, strategy manifests, freezes,
  result observations, full-history learning and duplicate protection are reused.
  New abstentions do not erase a valid previously published daily recommendation.
  Past freezes and completed nightly checkpoints are never backfilled.

## Szent Grál and settings

Registry v4 adds the twenty missing profiles when old settings are loaded;
previously disabled legacy profiles stay disabled. Explicit v4 disables stay
disabled. Validating an old saved retry does not silently expand its roster.
Dashboard participation edits merge only changed persona IDs into freshly read
settings, preserving legacy selections. The main settings form also preserves
the profile list.

The twenty entrants begin PROBATION. Enrollment is explicit, idempotent and
protected by the existing run lock. It checks the existing audit chain and never
changes prior enrollment dates or daily evaluations. The production enrollment
at 2026-10-11 23:58:54 Europe/Berlin created twenty rows; its immediate repeat
created zero. Team state is 9 ACTIVE / 21 PROBATION / 0 SUSPENDED.

Policy `grail-prospective-unit-v2-roster30` corrects its uncertainty threshold for
thirty candidates instead of ten. Admission/automatic suspension still requires
the established prospective sample, including 260 eligible tickets, 90 observed
days and 300 distinct fixtures, maturity and cooldown checks. With one ticket per
day, 260 eligible tickets generally means at least 260 publication days, and
possibly much longer. There is no accelerated admission just for new characters.

The supervisor may promote/suspend membership under the existing owner authority,
with reversible, recorded owner controls. Suspended profiles cannot bypass this
through fallback tickets. Its evidence-linked training plans do not edit code,
weights, stakes or wagers. Recognition requires sufficient evidence; the UI
does not invent a podium. Fictional characters do not need threats or simulated
suffering; the competition is implemented as measured eligibility and recognition.

## Dashboard

The local **Tipster klub** tab includes twenty code-native SVG portraits,
personality cards, search, daily status filters, batch-saved participation,
archived date navigation, selection explanations, odds and shared-pick overlap.
Paper and actual metrics are separate. Unknown money/ROI remains unknown, not
zero. A date with no generated recommendation is explicitly different from an
abstention or a losing ticket. Szent Grál's roster and source labels support all
thirty profiles. The club has no automatic wager action.

The Sites skill was used only for the existing local-web implementation and
verification workflow; nothing was uploaded or hosted. Computer-use checks ran
against the real local dashboard without saving test participation changes.

## Verification and preservation

### Added owner request — EUR 1 daily paper ledger

The Tipster klub includes a complete-team daily recommendation log and a
monthly/all-time EUR 1 paper view. The existing immutable original recommendation
snapshots are its durable source; there is no second bet-placement system or
duplicate database. `/api/paper-performance` reads the canonical one-profile/day
recommendations, including profiles with separately recorded actual wagers;
it does not add frozen copies or inactive rerun versions again. The optional
`month=YYYY-MM` filter and `download=1` JSON export use the same calculation.

The fixed `paper-eur1-v1` calculation retains the original per-leg and combined
price, ticket/profile/run identity, publication time and current settlement
observations. Source-backed, complete prices qualify for the financial sample.
Every logged ticket's EUR 1 assumption is separately disclosed, including the
excluded assumed exposure. An unavailable original price or uncertain source
stays visible and cannot become a made-up return. Coverage is shown explicitly;
if any records are excluded, the priced sample is not described as the full
team's complete profit. Unknown full-sample results remain null.

For eligible tickets, money is rounded once per settled ticket using decimal
arithmetic. A winning return includes the stake; a loss returns zero. Pending or
review stakes stay open and do not contribute to settled net profit. Cash balance
is returned money minus all eligible committed stakes; settled net is returned
money minus only closed eligible stakes. These differ while bets are pending.
Daily/monthly totals and cumulative balances are grouped by recommendation date,
with current known outcomes, not represented as bookmaker payment dates.

This is hypothetical EUR 1 per ticket, not actual money, and it does not change
the existing EUR 5 real-wager records. A month reports that month's observations,
not proof of a durable edge. Shared fixtures and the high-odds variance remain.

- Full suite: **811 Python tests passed**, with twelve existing penaltyblog/NumPy
  deprecation warnings. Real numerical-library tests are included. The 56 paper
  accounting/integration cases were also independently rerun. Both pure-selector
  and real-coordinator/ledger/freeze/settlement tests
  cover the new profiles, provenance, suspension, migration and duplication.
- Frontend: **85 club/accounting + 36 existing assertions passed**; JavaScript syntax valid.
  Changed Python modules/tests pass Ruff. Existing unrelated trailing blank lines
  in two older documentation files were left untouched.
- Real browser: all twenty cards, character search, unsaved participation/reset,
  separate missing paper/actual metrics, archived date and 30-member team verified.
  No console errors or horizontal overflow at the existing 429px viewport.
  Screenshot: `reports/arthur-v311-tipster-klub.png`.
  Paper UI verified at the natural desktop viewport: month/all-time filters,
  separate incomplete/verified totals, collapsed full log, original leg detail,
  20-row pagination and no console errors or page overflow. The live 429px tab
  also displays the new summary without horizontal page overflow. Screenshot:
  `reports/arthur-v311-paper-ledger.png`.
- All existing database contents below have identical before/after full-row
  hashes. Old team enrollment/evaluation/event rows are also unchanged; only the
  twenty authorized new enrollments were appended.

| Preserved table | Rows | SHA-256 of ordered row representation |
| --- | ---: | --- |
| portfolio_runs | 51 | a5f17cb9b3fcb7c3117cd7829ef5e1edaed9d310dcd63f4b065344189af12eff |
| portfolio_freezes | 12 | d5ea407885252b7245e0e53d98b9ea6f7e13d2fdd77878356adf99e3c26a58ba |
| portfolio_wagers | 4 | 72506bb7ccab41453f5d40933644281959f9ba17ed5a2396c76dc262c94a1648 |

Source backup: `backups/arthur-v3.11-start-20261011`. Credentials, databases,
original settings and unrelated dirty working-tree changes were preserved.

## Deployment and remaining operational checks

1. The original dashboard initially reported backend v3.8. The environment
   previously rejected stopping that user process; it was not bypassed. After
   asking the owner to restart, normal port 8765 was independently verified at
   October 12 00:28 Berlin as **v3.11**, with the catalogue and working
   `/api/paper-performance` endpoint (`paper-eur1-v1`). The original browser tab
   was reloaded to receive the latest UI. Runtime deployment is now confirmed.
2. The v3.11 dashboard was verified on temporary localhost port 8766 without
   loading sports credentials. This is QA, not a separate installed application.
   The QA server is stopped after verification.
3. Native `Arthur Goal Analysis Daily` remains enabled, every 15 minutes and on
   logon, Interactive user, IgnoreNew, StartWhenAvailable, two-hour maximum and
   no automatic wake-up. Python retains 22:00 Europe/Berlin and the 06:00 catch-up
   cutoff. Computer, login, internet and existing provider/Codex access remain
   necessary. No new scheduler or recurring Codex task was created.
4. The October 11 23:45 checker encountered an import gap during the in-place
   file update: configuration referenced the new package before it existed.
   Current read-only schedule diagnostics return success. The next natural
   Windows timer tick at October 12 00:00:01 Berlin returned **exit 0**; the next
   checker was scheduled for 00:15. No completed job was
   replayed and no extra AI/provider call resulted from that failed check.
5. The October 11 nightly job already completed at 22:04 for October 12 using
   its original ten-profile settings. Its freeze stays intact. The next new
   automatic job is October 12 at 22:00 Berlin for October 13. A manually started
   fresh future-date analysis after restart can use all enabled thirty profiles.
6. No new-fixture production generation, live-price coverage or first natural
   thirty-profile nightly result is claimed from mocked integration tests.
   Check these on the next new analysis. Existing provider quotas were not increased; the
   last nightly result refresh used its 20 requests and left 20 fixture checks
   pending; the later October 11 23:00 manual refresh reported zero remaining.
   Neither is a continuously refreshed queue estimate. More recommendations can
   enlarge backlog despite shared fixtures.

The five-year simulator/database project remains separate future work. This
release enables prospective measurement, not evidence of profitability.

### Existing-history EUR 1 baseline (read-only)

At release verification the old profiles had 109 canonical daily recommendations,
of which 30 satisfied original-price/source/single-bookmaker conditions. In that
subset: EUR 30 assumed committed, EUR 2.62 returned, 26 closed tickets (2 wins,
24 losses), EUR -23.38 closed net and EUR 4 still pending; cash balance EUR -27.38.
The other 79 entries remain visible with missing-price or incompatible-bookmaker
reasons. Full-team profit is unknown; the partial negative result is not hidden
or presented as a complete estimate. None of these historic rows is attributed
to the twenty new characters, which have not yet generated a production ticket.
