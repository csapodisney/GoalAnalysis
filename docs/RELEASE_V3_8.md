# Arthur v3.8 — GAUSS and KEPLER local statistical agents

## Scope and provenance

The user approved the two methods from “GitHub futballelemző modell keresése”.
GAUSS uses penaltyblog's bivariate Poisson goal model with Dixon–Coles controls
over fixed 365/730-day windows. KEPLER uses penaltyblog Pi ratings followed by
a fixed-seed CPU CatBoost 1X2 classifier. The latter is an adaptation of the
published rating/ML research direction, not an imported pretrained tipster or
a reproduction of its reported research scores.

References:
- https://github.com/martineastwood/penaltyblog (MIT)
- https://penaltyblog.readthedocs.io/en/latest/models/overview.html
- https://github.com/calvinyeungck/Soccer-Prediction-Challenge-2023 (research reference)
- https://github.com/catboost/catboost (Apache-2.0)

No third-party data scraping, external source-code execution, new subscription,
additional LLM call, automatic wager or five-year simulation import was added.
The numerical dependencies are pinned in the `models` optional dependency group.
Their installed license notices remain with the installed distributions.

## Independent prospective paper profiles

Both agents receive the complete selected daily fixture universe and the shared
existing API-Football history, not Arthur's chosen candidates. League/season
coverage is unchanged and remains within the existing collection budget.
Missing data/dependencies and failed fits produce explicit empty status cards.
They never fall back to chronological, manually weighted or AI-invented picks.

GAUSS supports full-time 1X2, over/under 2.5 and BTTS yes. KEPLER supports only
full-time 1X2. The original numerical estimates, model provenance and diagnostics
are retained in immutable run reports and recommendation snapshots. Astra can
supply context, but cannot modify the estimates or numerical ranking.

The initial candidates and selection thresholds are experimental, not optimized
or calibrated. GAUSS checks variation across fixed models/windows. Price-based
selection requires actual matching provider quotes and a positive estimated
edge. An estimated edge is not a proven advantage. Missing-price ideas remain
explicitly unpriced; no payout, profit or product of model probabilities is
invented. At most three distinct fixtures form a profile's paper recommendation.

These profiles do not enter the real-money portfolio automatically. Its existing
ticket cap, manual EUR 5 wager recording and legacy strategy behavior remain.
Model quotes retain the user-requested informational-only timestamp policy;
this is not an executable historical odds backtest.

## Time integrity and limits

Only completed regular-time match outcomes preceding the prediction cutoff
are eligible. Features for training examples use earlier completed games,
including a conservative kickoff-plus-three-hours availability approximation.
Observed-at import timestamps are not misrepresented as original publication
timestamps. Conflicting duplicates, insufficient team history and invalid input
are excluded or diagnosed. The fixed temporal holdout measures predictive error,
not betting profitability, and is not a substitute for future blind validation.

Cup/national-team coverage may be insufficient. The data-veto slider does not
relax model mathematical input requirements. The reserved five-year simulation
database project is deliberately separate and deferred.

## Local persistence and user interface

Original predictions and numerical diagnostics are written with the existing
report bundle and ledger under the project directory. Freezing a new run includes
its numerical recommendations; old frozen packages and reports are not rewritten.
The dashboard distinguishes model estimates from heuristic confidence and reports
selection-level and whole-ticket outcomes separately. Shared fixture exposure and
shared losses are descriptive counts, not evidence of statistical independence.

Old settings are read-migrated to enable GAUSS/KEPLER without changing disabled
legacy profiles, stakes or ticket count. Saving the new registry version records
the user's later model-profile choices; disabled numerical profiles stay disabled.

## Deployment

Use an isolated tested checkout. Preserve local settings, keys, data and reports.
Install the `models` extra into the project runtime. Verify source hashes before
replacing changed files and retain a backup of those files. Restart the local
dashboard only when no analysis is running. A new run is necessary to produce
new model cards; never fabricate retrospective recommendations for old runs.

Validation results and local deployment status are recorded in CURRENT_STATE.md.
