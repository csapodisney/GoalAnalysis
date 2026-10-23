# Arthur v3.10 — A Szent Grál

In-place update of `C:\AI-Work\GoalAnalysis`, 2026-10-07. The owner explicitly
authorized autonomous **live recommendation-team** management. This replaces
the v3.9 advisory-only boundary for team membership, not for wagers, source-code
execution, bankroll or stake changes. No subscription was added.

## What the supervisor actually controls

- Existing nine approved profiles start ACTIVE; this preserves availability,
  not a claim that their profitability has been proven.
- LAPLACE, a locally reviewed independent Poisson adapter using the already
  installed penaltyblog library, starts in PROBATION. It uses a fixed 730-day
  history window, at least 80 usable league matches, strict temporal filtering,
  real numerical fitting and no heuristic or remote-code fallback.
- ACTIVE profiles may publish live recommendations. PROBATION profiles publish
  immutable PAPER_AUDITION recommendations in the separate trial area, never
  playable portfolio tickets. SUSPENDED profiles produce no new selections.
- A qualified probationer is promoted automatically; convincingly poor active
  profiles are suspended automatically. This includes DAILY_223 despite its
  legacy mandatory settings entry. Generic fallback cannot bypass suspension.
- Suspension preserves history and is reversible. A suspended member does not
  continue collecting predictions automatically; the owner can return it to
  PROBATION or explicitly override it to ACTIVE with a recorded reason.
- Explicitly disabled user settings remain disabled. Automatic membership
  does not overwrite settings, create wagers or increase the five-ticket cap
  or fixed EUR 5 manual stake. Numerical live recommendations remain uncalibrated
  suggestions, not automatically selected or wagered portfolio tickets.

## Evidence and decision policy

The initial, versioned policy is `grail-prospective-unit-v1`:

| Gate | Requirement |
| --- | --- |
| Decision sample | At least 260 resolved prospective tickets |
| Coverage | At least 90 publication days and 300 different fixtures |
| Maturity | At least 72 hours after the last kickoff in a ticket |
| Identity | Current code/settings manifest, known original publication and source prices |
| Cooldown | 30 days after enrollment or the latest membership change |
| Promotion | Estimated lower paper-ROI bound above +5% |
| Suspension | Estimated upper paper-ROI bound below −5% |

These are conservative operating rules, not a validated profit guarantee.
At the current one-profile/day counting rate, 260 tickets require at least 260
eligible match days; this is deliberately not a quick winner-picking scheme.
Old history remains descriptive, but cannot retrospectively pass a new audition.
New code/settings versions and manual overrides require a fresh relevant sample.

The paper cohort includes all eligible published recommendations, irrespective
of whether the owner later wagered them. Unit stake is explicitly hypothetical;
return uses original, source-identified, same-bookmaker leg prices. No missing
odds are reconstructed. Unresolved mature results, uncertain voids, incomplete
price provenance and repeated fixtures across day clusters block decisions
instead of silently selecting a favorable subset. Quote timestamps remain
informational: old/future/missing quote times are **not** new price vetoes.

Uncertainty uses 10,000 deterministic day-cluster bootstrap draws and a
ten-profile Bonferroni tail adjustment. Repeated daily examination and residual
dependencies mean this is an estimated range, not a guaranteed statistical
error bound. It excludes service costs and does not establish executable
bookmaker-combination prices. Actual-money outcomes are a separate cohort
requiring confirmed actual prices or payouts; unavailable ROI stays unknown.

Only qualified positive ACTIVE members earn badges and up to three podium
places. Ranking is descriptive by estimated lower paper-return bound, not proof
that one member statistically beats another. Badges confer no real stake increase.
Agents are not represented as having feelings, hunger or welfare needs.

## Training, discovery and admissions

The existing daily learning reviewer still cannot rewrite code or strategy
parameters. A Szent Grál attaches its verified, evidence-linked proposals to the
appropriate member's training plan. Report hashes, original evidence and the
canonical proposal text are rechecked against the ledger. LLM priority labels
cannot overrule the membership thresholds. This is a supervised improvement
workflow; it is **not** LLM fine-tuning. The numerical models do actually refit
their parameters to available, closed historical matches on each generation.

Weekly source scouting runs within the existing nightly learning stage, or can
be requested from the dashboard. It makes at most nine unauthenticated public
GitHub API GET requests per Berlin Monday-based week: known seeds plus a bounded
two-result discovery query. Reservations and each request debit are durable
before network access. An uncertain interruption is not retried that week.
Every source records its current commit pin, license metadata, timestamp and
read-only provenance; metadata is not legal or security certification.

Source pipeline: source review → local adapter implementation/review → paper
audition → threshold-based autonomous live admission. Arbitrary discovered
repositories are not executable agents. This release does **not** autonomously
download, install, run or write adapters for unknown Internet code. Such a source
needs a separately reviewed implementation before it can compete. LAPLACE is
the first new implemented entrant, not a renamed search result.

Verified upstream references:

- [penaltyblog repository](https://github.com/martineastwood/penaltyblog)
- [penaltyblog model documentation](https://penaltyblog.readthedocs.io/en/latest/models/overview.html)
- [choix repository](https://github.com/lucasmaystre/choix), a source candidate,
  not an installed football prediction agent.

## Persistence, scheduling and recovery

- `data/team/team.sqlite3`: append-only enrollment, first daily evaluation,
  membership events and earned recognition; hash-checked and update/delete guarded.
- `data/team/scouting.sqlite3`: weekly reservations, request debits and immutable
  public metadata snapshots. Dashboard reads never initiate a scout or model call.
- The existing ledger, freezes, actual wagers, settings and credentials stay
  in their existing locations and are not rewritten by this release.
- At job creation the effective roster and its hash are frozen into the nightly
  job. Retries reuse that roster, not a newly selected team. Corrupt team state
  blocks publication; it is not silently replaced by all-ACTIVE defaults.
- Nightly order remains results → generation → archive → learning. The learning
  stage now also evaluates the team and invokes the bounded scout. Membership
  changes affect the next run, never the already archived one. Completed v3.9
  jobs are not replayed just to add v3.10 metadata.
- The existing native 15-minute checker and Berlin 22:00 business-time gate
  remain unchanged. No second scheduled task or cloud heartbeat is created.

Local commands, using the existing project environment:

```powershell
.venv\Scripts\python.exe scripts\run-arthur-team.py --snapshot
.venv\Scripts\python.exe scripts\run-arthur-team.py --review
.venv\Scripts\python.exe scripts\run-arthur-team.py --scout
```

`--snapshot` is read-only and offline. `--review` changes the local team under the
shared Arthur lock but makes no new AI request. `--scout` consumes the remaining
weekly public metadata allowance, never a sports API or model allowance.
The dashboard's token/origin-protected actions expose the same workflow.

## Deployment and verification

Source backup: `backups/arthur-v3.10-start-20261007`, without credential or database
copies. Validation: 693 Python tests and 36 frontend contract/safety assertions
passed, plus JavaScript syntax, changed-file Ruff and real-browser checks.
Twelve upstream penaltyblog/NumPy deprecation warnings remain. The initial real
team evaluation and nine-request GitHub scan are stored locally; repeated calls
return their same immutable hashes without extra calls. All 43 pre-existing run
hashes and 10 freeze hashes remain identical; no actual wager was added.
See CURRENT_STATE.md for IDs, times and live initialization details.

The old port-8765 dashboard process must be restarted using the existing Arthur
shortcut; a browser refresh alone does not replace its loaded Python modules.
Stopping that old process was rejected by the tool environment, so the owner
must close its original server window. The native scheduler already loads the
updated files independently. A temporary QA server does not replace production.

Remaining operational validation: the first newly generated live fixture run,
LAPLACE availability under actual history coverage, and a future nightly job
including team evaluation. Synthetic promotion/suspension tests demonstrate
control flow, not a claim that any member has already earned promotion or profit.
