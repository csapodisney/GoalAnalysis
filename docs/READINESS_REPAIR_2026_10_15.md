# Arthur readiness repair — 2026-10-15

Owner-authorized follow-up to the Oct 15 package that contained profile research
recommendations but no constructed portfolio ticket. This repair addresses two
verified implementation faults: DAILY_223 inherited an unrelated specialist
score gate, and the research subprocess could not execute web searches with its
code-mode host disabled. Recording a package does not place a wager.

## DAILY_223: exact blocker

The saved run `2026-10-15-3b9678e368c3` contains **6,569 valid priced products
across 36 fixtures**. All 36 fixtures have raw prices at least 2 and at least 3.
The strict historical/venue eligibility boundary retains **1,059 products across
seven fixtures**, still including both price roles for every retained fixture.

The portfolio adapter then applies the generic specialist floor at strictness 35:

- Minimum adjusted support: `3 + 35 × 0.12 = 7.2`.
- Missing competition context, squad/load and motivation total 45 weight points.
- Their uncertainty deduction is `45 / 100 × 35 × 0.30 = 4.725`.
- With no additional caution deduction, this requires raw historical/venue
  support of at least **11.925** before a product can reach the DAILY_223 solver.

That second filter retains **308 products across seven fixtures**. Only one
fixture has any surviving price at least 2; none has a surviving price at least
2.94, the relaxed third-slot floor. The maximum surviving price is 2.38.
Consequently, even a relaxed 2/2/3 combination cannot exist in the adapter's
filtered pool. Astra does not cause this disappearance: the pre-review and
post-review eligible counts are both 308.

The original DAILY_223 core, run offline against the exact same saved snapshot,
constructs a compliant ticket from its 1,059 historically supported products.
The failure is therefore the adapter's extra eligibility gate, rather than an
absence of provider prices or a failure of the original price-role solver.

The old daily recommendation fallback also obscures the distinction: it displays
three mixed-bookmaker selections with combined price **1.082118**, status
`REVIEWED` and medium confidence, although its warning admits that the 2/2/3
conditions are unverified.

## Bounded DAILY_223 correction

`engine/portfolio.py` now supplies DAILY_223 with a separate eligible pool after
the existing identity, quote, date, kickoff, history and first-half checks, but
before the specialist absolute-support floor. The specialist pool and global
strictness setting are unchanged. Existing uncertainty/caution ranking,
bookmaker preference, bounded search, slot floors, tolerance and required
combined price remain in force. No evidence, odds or model probabilities are
manufactured.

`engine/recommendations.py` preserves a constructed DAILY_223 proposal even when
optional Astra coverage is partial; partial review no longer replaces its
verified legs with underpriced reviewed alternatives. Actual slot annotations
are retained. An unconstructed fallback is explicitly `TARGET_PROPOSAL`, with
low confidence and `daily223_constraints_verified=false`; review alone cannot
upgrade it into a verified combination.

The adapter is versioned `arthur-portfolio-v3-independent-daily223`; daily
recommendations use `arthur-daily-recommendations-v2-verified-construction`.
Historical run and frozen-package snapshots are not rewritten.

The seven DAILY_223 core/pipeline files remain unchanged:

- `config/daily_223_live.py`
- `engine/daily_223.py`
- `features/daily_223_history.py`
- `jobs/daily_223_candidates.py`
- `jobs/daily_223_history.py`
- `jobs/daily_223_live.py`
- `providers/daily_223_odds.py`

The initial source inventory is recorded in
[`source-change-audit.json`](../analysis/strategy-review-20261014/source-change-audit.json).
Pre-repair adapter and regression-test copies are preserved under
[`backups/daily223-repair-20261015/source/`](../backups/daily223-repair-20261015/source/).

## Offline counterfactual

The repaired adapter, using the original saved decision cutoff
`2026-10-14T22:01:08.348143+00:00`, constructs one strict DAILY_223 ticket:

| Fixture | Selection | Original decimal price |
|---|---|---:|
| Benfica–Celtic | Full-time over 3.5 | 2.15 |
| Ferencváros–Plzeň | Both teams to score: no | 2.27 |
| Dinamo Zagreb–Anderlecht | Away win | 3.75 |

All three original quotes identify `betano_api_football_32`, with the same
region/currency. The theoretical product is **18.301875**, on three distinct
fixtures, satisfying the unchanged 2/2/3 minimum price roles and combined-price
constraint. The generic specialist count remains 308; DAILY_223 now sees 1,059
eligible products.

This is an **offline counterfactual**, not a fresh publication or backtest. None
of these three source candidates was covered by the old saved Astra review, so
both the rebuilt ticket and its recommendation honestly remain `DRAFT`. No
provider/model request or production database write was made for this replay.
Evidence: [`daily223-repair-20261015-offline.json`](../analysis/daily223-repair-20261015-offline.json).

## Web research: isolated cause and correction

Three bounded probes separate a configuration failure from a model declining to
search. These are observed tool events, rather than a model's unsupported claim
that it browsed.

| Probe | Research configuration | Observed result |
|---|---|---|
| Standalone search enabled, code-mode host disabled | Previous host restriction retained | Zero web-search calls |
| Code-mode host enabled | Isolated host comparison | Two completed web-search calls |
| Actual repaired adapter, host enabled | Updated adapter and result validation | Two completed calls, 26 returned source URLs, one accepted UEFA source |

The repaired research subprocess enables the code-mode host needed for the
available web tool. The separate supplied-data review subprocess keeps that host
disabled. Other existing restrictions remain: subscription authentication,
read-only sandbox, disabled command/integration capabilities, credential-free
child environment and no Platform API fallback.

The adapter accepts source references only from completed search events and
binds citations to URLs actually returned by those events. Started, failed or
interrupted events and merely model-written URLs do not establish verified
research. Safe stage diagnostics expose attempted/completed search counts and
returned/accepted source counts without raw environment or credential-bearing
errors. The review cache namespace/key includes the changed research policy so
an old restricted-host result cannot masquerade as a repaired execution.

The successful probe verifies web-tool operation and one returned official UEFA
page. It does **not** verify every fixture's lineup, injury, motivation or odds.
Fresh-run research acceptance is recorded separately below.
Probe evidence:
[`standalone flag only`](../analysis/readiness-repair-20261015/probe-web-result.json),
[`host comparison`](../analysis/readiness-repair-20261015/probe-web-host-result.json),
[`repaired adapter`](../analysis/readiness-repair-20261015/probe-web-repaired-result.json).

## Verification and observed operational acceptance

The full Python suite passed **1,049 tests in 107.97 seconds**, with 32 existing
upstream deprecation warnings. Ruff and JavaScript syntax checks passed; the
tipster/paper-ledger frontend check passed **85 assertions**. Independent DAILY
verification passed **105 tests plus 11 boundary checks**; the web-focused set
passed **37 tests**. These checks cover truthful target/draft status, missing
review, strict history/quote/fixture boundaries, unchanged specialist filtering,
source/result binding and the research/data subprocess separation.

Fresh run **`2026-10-15-cb385563a836`** started at **06:30:17**, finished at
**06:35:43** and was saved at **06:35:45 Europe/Berlin**. It constructs one
**`READY`**, strict DAILY_223 ticket at `betano_api_football_32`; all three legs
were included in the completed Astra review:

| Fixture | Selection | Saved decimal price | Berlin kickoff |
|---|---|---:|---|
| Benfica–Celtic | Full-time over 3.5 | 2.15 | Oct 15, 21:00 |
| Ferencváros–Plzeň | Both teams to score: no | 2.30 | Oct 15, 21:00 |
| Dinamo Zagreb–Anderlecht | Away win | 3.75 | Oct 15, 21:00 |

The saved theoretical product is **18.54375**. The 2.30 quote is the fresh run's
saved provider price; the earlier offline comparison remains correctly recorded
at 2.27. `READY` describes the application's constructed/reviewed state, not a
placed wager or demonstrated positive expected return.

The requested portfolio count is two, so the overall run remains **`PARTIAL`**
with **one ready ticket**. This remaining shortfall has a different, verified
cause: specialist combinations cannot reach the unchanged minimum combined price
of 10 using their eligible products at one bookmaker. The largest upper bounds
are **4.41882** for Krónikás, **8.253738** for Ritmusőr and **3.04668** for Nyitány.
Őrszem has at most one eligible fixture per bookmaker; Párharcmester and Merlin
have no eligible products. The feasibility check reports no search bug; the
target and global strictness were not loosened to produce a second ticket.

The complete profile roster has **19 published recommendations, 11 entries
without legs, 60 leg occurrences and eight distinct fixtures**. The four most
used fixtures still account for **75%** of legs. Numerical coverage remains
GAUSS/KEPLER/LAPLACE **1/0/1**. These shared research recommendations are distinct
from the single constructed portfolio ticket.

Astra's supplied-data review is `COMPLETE` and research is
`COMPLETE_WITH_SOURCES`: **four completed web-search calls, 105 actually returned
source URLs and eight accepted web sources**. Of the **35 non-weather context
entries**, **10 are sourced, four provisional and 21 unknown**. The complete
context list has 42 entries including seven weather entries. This is verified
web operation and partial evidence coverage, not complete lineup or background
verification. The new optional Nemotron ticket audit remains `UNAVAILABLE` and
is not represented as repaired. Evidence:
[`fresh-generation-validation.json`](../analysis/readiness-repair-20261015/fresh-generation-validation.json)
and [`specialist-feasibility.json`](../analysis/readiness-repair-20261015/specialist-feasibility.json).

## Recorded package, preservation and live access

The completed run was appended as **effective revision 3 at 06:38:29
Europe/Berlin**, with 19 recommendations and hash
`8fbfdf542cb8e2627ead2152014c0701e95c7b9cc29ea7c33c741d66d89aceb3`.
Its parent revision-2 hash is
`d5e27bd79f25ee461aca9dc88bc218ce3e22a74d6dcac0f304a57e1022f51d71`;
the original hash remains
`6c54f2f1f401b0ab9d32943178a871f091f363ea9ce51ee5884797210279a4b1`.
The original package and revision 2 remain intact in the append-only history.
Evidence: [`freeze-revision-result.json`](../analysis/readiness-repair-20261015/freeze-revision-result.json).

Comparison with the six consistent database backups under
[`backups/readiness-repair-20261015/`](../backups/readiness-repair-20261015/)
finds **zero missing or changed original rows**, excluding allowed prospective
active flags. The main ledger adds one run, one ticket and 19 recommendations;
the original 15 freeze rows are unchanged and one correction row is appended.
Actual wagers remain **four**. Existing settings, credentials, nightly-job state
and team membership are preserved. The separate Nemotron store adds its new
claim/result without changing earlier records. Evidence:
[`preservation-verification.json`](../analysis/readiness-repair-20261015/preservation-verification.json).

The hidden `Arthur-Dashboard` login-task path started the updated loopback-8765
server, **PID 25080**, with existing credentials loaded. Both localhost and
private Tailscale HTTPS8443 root/status requests return **200**. Both status
payloads are **15,136,647 bytes**, with matching acceptance fields for the new
run, ready DAILY_223 ticket, effective revision 3 with 19 recommendations,
updated UI and idle runtime.
Project 2030 HTTPS443 still returns **200**; the existing gateway **PID 19668**
is preserved. Phone URL:
**`https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`**. Evidence:
[`live-verification.json`](../analysis/readiness-repair-20261015/live-verification.json).

A full system reboot and a new physical phone-browser test were **not
performed**. The configured startup path and current private HTTPS deployment
were verified directly.

Read-only evaluation of the actual ledger at the original, revision-2 and
revision-3 cutoffs selects the appropriate Oct 15 run with 24, 19 and 19 records,
respectively: fifteen effective packages and zero invalid records at every
cutoff. All seven DAILY_223 core source hashes still match the original frozen
manifest, and actual wagers remain four. Evidence:
[`learning-and-core-verification.json`](../analysis/readiness-repair-20261015/learning-and-core-verification.json).
