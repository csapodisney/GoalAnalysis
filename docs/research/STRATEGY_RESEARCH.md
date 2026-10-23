# Strategy research and recovered Arthur evidence

Research date: 2026-09-20. This document separates recovered historical claims,
new hypotheses, and implementation choices. It does not certify a betting edge.

## Recovered earlier research

The user's recollection of a large earlier simulation led to two existing files:

- `Arthur_strategia_es_szimulacio.html`, dated 2026-09-10. Its complete text was
  read through the authenticated file service on 2026-09-20.
- `Arthur_kutatas_ujrafuttathato.zip`, dated 2026-09-10, recorded size 7,164,911
  bytes. The archive exists, but both materialization attempts returned HTTP 502.
  Its code, data, checksums, and results have **not** been independently rerun in
  this release. No raw dataset was recovered into the repository.

The recovered report describes **20,248 real match records**, not 27,000. This
does not rule out another research run; it is the count supported by the located
report. It also describes 44 Monte Carlo scenarios with 100,000 annual paths each.
Those simulated paths are hypothetical draws, not additional observed matches.

Its documented model uses goals over at most twelve preceding months, recency
and venue weights, and opponent attack/defence comparisons. It does not contain
complete historical lineup, coach, tactical, xG, or first-goal-time data.

| Reported historical result | Value |
| --- | --- |
| Universe | English tiers 1–4, German tiers 1–2, Italian/Spanish/French/Dutch top divisions |
| History / calibration / tests | 2021/22–2022/23 / 2023/24 / 2024/25 and 2025/26 |
| Accumulator rule | Three Over 2.5 selections from three leagues, combined odds above 10 |
| Accumulators / winners | 211 / 22 |
| Average combined odds | 11.73 |
| Hypothetical flat-stake ROI | +25.1%, before the current application's costs |
| Weekly-block bootstrap 95% ROI interval | −25.9% to +80.4% |
| Profitable / losing active months | 8 / 12 |
| Longest losing sequence | 42 accumulators |
| Predicted / observed ticket success rate | 14.2% / 10.4% |

These are **reported historical outputs**, not freshly verified outputs. The
positive aggregate return and negative months must travel together whenever the
research is displayed. The single-selection Over 2.5 tests were negative in both
test seasons (−8.0% and −1.8%); this is an additional reason to recheck selection,
combination effects, and calibration before carrying a profitability claim over.

The report says the archive contains 50 source files, hashes, modeling code,
simulation code, and all test tickets. Recover that archive and check these
claims before adding its trained parameters to a live model registry. Its old
stake-sizing and publication rules do not override the user's subsequently
confirmed €5 per ticket and maximum five daily tickets.

No first-half Over 0.5 experiment was identified in this particular report.
That strategy remains a new candidate until its own evidence is recovered or
produced. A modified strategy does not inherit the old Over 2.5 ROI.

## Open-source components reviewed

| Component | Verified license / capability | Decision for GoalAnalysis |
| --- | --- | --- |
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | [MIT license](https://github.com/martineastwood/penaltyblog/blob/master/LICENCE); Poisson, bivariate Poisson, Dixon–Coles, team ratings, and bookmaker-margin calculations | Strong optional offline model benchmark. Do not add a Cython/modeling dependency to the daily UI merely to obtain another agent name. |
| [soccerdata](https://github.com/probberechts/soccerdata) | [Apache-2.0 with the included upstream notice](https://github.com/probberechts/soccerdata/blob/master/LICENSE.rst); cached scrapers returning aligned tabular data | Optional research adapter. Its maintainers explicitly note scraper breakage when websites change; keep the existing contracted APIs as the operational path. |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | [MIT](https://github.com/openai/openai-agents-python/blob/main/LICENSE); multi-agent orchestration | Useful when tool routing and traces justify it. Existing structured Responses API integration can support bounded specialist reviews without a second orchestration framework. |

These licenses concern the code, not the rights or commercial availability of
every underlying dataset. No downloaded third-party strategy code was executed
and no new runtime dependency was installed during this review.

## Candidate strategies and data requirements

The five original specialist profiles and independent DAILY_223 remain the
publication core. New research profiles compete within the same daily maximum;
adding a profile does not automatically add a sixth daily ticket.

1. **First-half Over 0.5.** Fit and evaluate on first-half goals, not full-time
   scores divided by two. Start with venue-specific, recency-weighted first-half
   scoring/conceding frequencies with shrinkage towards league averages. The
   binary result is at least one first-half goal. Include 45+ stoppage time and
   use the provider's half-time score to avoid period-boundary mistakes.
2. **Opponent-adjusted Over 2.5.** Recreate the recovered model's comparison of
   home attack against away defence and away attack against home defence. Compare
   against a simple rolling baseline and a fitted Poisson/Dixon–Coles benchmark;
   the more complicated model must demonstrate an improvement on unseen dates.
3. **Balanced-match draw.** Benchmark the existing Merlin profile using a
   low-score-aware score model. Avoid estimating a draw by subtracting two
   unrelated win percentages or treating recent draws as an independent edge.
4. **Schedule/context variants.** Rest days, recent minutes, venue, competition
   type, and verified absences are candidate features. These must be known before
   the selection timestamp. Motivation derived only from a losing streak is a
   hypothesis, not an observed fact. Cups need correct 90-minute settlement and
   separate treatment of extra time and two-leg aggregate state.

Each strategy needs dated fixtures, stable team/event identifiers, the exact
settlement period, and pre-selection feature timestamps. A hit-rate experiment
needs outcomes. A profit experiment additionally needs **historical prices
available at the selection time**, the same market/line, the chosen bookmaker,
and the frozen ticket construction rule.

Football-Data defines `HTHG`/`HTAG` as half-time home/away goals and `B365>2.5`
as a full-time Over 2.5 price. It distinguishes pre-closing and closing columns;
these must not be conflated. The provider also flags Pinnacle odds after
2025-07-23 as systematically stale, so that portion needs exclusion or separate
quality treatment. The source documentation was fetched successfully as text
after the web reader returned errors. [Column definitions](https://www.football-data.co.uk/notes.txt),
[dataset and collection notes](https://www.football-data.co.uk/data.php).

The Odds API documents `totals_h1`, `alternate_totals_h1`, and `btts`. Additional
markets are requested per event; availability for a particular football league
and bookmaker must be observed in the response. A documented market key is not a
coverage guarantee. [Market definitions](https://the-odds-api.com/sports-odds-data/betting-markets.html).

Historical period-market snapshots are documented from 2023-05-03, subject to
when the provider began covering the sport/market, and require a paid usage plan.
Snapshots must be selected at or before the simulated decision time. No historical
purchase or quota expenditure was made during this research.
[Historical odds documentation](https://the-odds-api.com/historical-odds-data/).

First-half goals alone cannot establish First-half Over 0.5 ROI. Do not substitute
full-time Over 2.5 prices, today's odds, or an assumed constant price and then
label the result historical profit. An assumed-price sensitivity study is valid
only when explicitly labeled a simulation.

The high-total-price requirement also matters: six 1.40 selections yield only
7.53. A first-half profile must not silently increase the existing maximum of six
legs or pretend it meets a 10+ target. Report the feasible construction and the
constraint that prevented a target-price ticket.

## Reproducible validation protocol

1. Save a manifest with source URLs, retrieval timestamps, content hashes,
   league/season coverage, missing rows, and the meaning of each odds column.
   Distinguish opening, decision-time, and closing prices.
2. Sort by decision timestamp. Use earlier seasons for fitting, a later period
   for calibration and tuning, and an untouched later period for evaluation.
   Keep same-day fixtures out of the history if their completion time is unknown.
3. Freeze feature definitions, screening, strictness, bookmaker choice, ticket
   count, overlap policy, odds target, and €5 stakes **before** the test. Record
   all tested variants so selecting a lucky winner is visible.
4. Simulate the full daily publication process, including days with scarce
   fixtures, missing prices, cups, postponements, and repeated matches across
   profiles. A profitable subset found after viewing results is a new hypothesis.
5. Report sample counts, Brier/log loss and calibration for probabilities;
   settled stake, payouts, net profit, ROI, drawdown, longest losing run, and
   profitable-month fraction for tickets. Include API/data/model costs separately
   and in a cost-adjusted total.
6. Estimate uncertainty with day/week blocks so shared football conditions and
   overlapping tickets are not incorrectly treated as independent samples.
   Compare to simpler baselines on the same eligible events and available prices.
7. Record prospective tickets and their evidence before kickoff. Use subsequent
   results to propose versioned changes; assess those changes on a new period.
   Dashboard feedback provides recorded evidence, not automatic model-weight
   training or proof that yesterday's losing choices should be reversed.

The next evidence milestone is recoverable data and a replayable evaluation, not
an invented sample size or a promised profitable month.

## Focused follow-up: implementation priority, 2026-09-21

This short review is complete; it did not run a new historical backtest. Priority
reflects data availability, implementation cost and the approved 10+ ticket target,
not an established profit ranking.

| Priority | Strategy | Concrete next experiment | Release status |
| --- | --- | --- | --- |
| 1 | Opponent-adjusted Over 2.5 | Compare a recency-weighted attack/defence Poisson baseline against the existing Krónikás heuristic on identical chronological holdouts. Use only pre-selection prices. | Existing Over 2.5 builder is operational; the fitted benchmark is not installed or validated. |
| 2 | Low-score-aware draws | Benchmark Dixon–Coles against Poisson for Merlin, examining draw calibration and net returns rather than selecting by recent draw count. | Existing Merlin builder is operational; Dixon–Coles remains a research benchmark. |
| 3 | First-half Over 0.5 | Evaluate actual H1 scoring/conceding samples and exact H1 0.5 prices; measure how often a 2–6-leg ticket can meet the odds target. | Nyitány builder is included as experimental, with H1 evidence and quote checks. |

For these benchmarks, the maintainer's [penaltyblog model guide](https://penaltyblog.readthedocs.io/en/latest/models/overview.html)
documents Poisson, Dixon–Coles, shared prediction interfaces and time weighting.
This makes it a useful optional offline comparison tool. Its existence and model
features do not demonstrate an edge in our markets. No additional agent framework
is required for these numerical experiments.

The provider's [market catalogue](https://the-odds-api.com/sports-odds-data/betting-markets.html)
documents `totals_h1`. The live feed must still return the exact 0.5 line and an
eligible bookmaker. A 1.5 half-time line must never be relabelled as 0.5.
With six legs, the geometric mean price needed to reach total odds 10 is about
1.468; six 1.40 prices yield only 7.53. This is why H1 Over 0.5 is third in the
research order despite often sounding attractive as a high-hit-rate market.

The current release uses evidence scores, not fitted calibrated probabilities.
Later model experiments should compute each selection's expected return from
its estimated probability and actual price, compare against a margin-adjusted
market baseline, and evaluate the complete ticket construction on held-out dates.
Do not multiply heuristic support scores and call the result a ticket probability.
Record overlap between profiles and include service costs when evaluating the
user's monthly profitability objective.
