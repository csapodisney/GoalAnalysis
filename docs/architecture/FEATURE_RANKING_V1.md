# Deterministic Goal Shortlist v1

## Purpose

This stage ranks research candidates before any LLM or odds lookup. It is a shortlist score,
not a betting recommendation and not Arthur's final decision.

## Input contract

Historical CSV columns:

```text
id,competition_id,kickoff,home_team_id,away_team_id,home_goals,away_goals
```

Kickoff must include a timezone offset. Goals are regular-time, non-negative integers.

## Evidence window

- Maximum lookback: 365 days.
- Current-coach start dates shorten that window when supplied.
- Only the home team's home matches and away team's away matches are used.
- Future matches and other competitions are excluded.
- Each side needs at least five matches by default.
- Insufficient evidence remains `null`; it is never treated as zero.

## Ranking components

The versioned `goal-shortlist-v1` score uses:

- average total-goal level: 35%
- Over 2.5 frequency: 25%
- BTTS frequency: 20%
- both teams' scoring reliability: 10%
- sample coverage: 10%

The score is deterministic, bounded by 100, explainable in the JSON output, and contains no
price or odds field. The football ordering must be frozen before prices are attached later.
