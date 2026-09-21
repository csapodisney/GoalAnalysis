# Model, team-news and lineup gates v1

Phase 16 implements the remaining deterministic approval prerequisites from Arthur Pentagram 2.0.

## Model calibration

The value gate requires an identified artifact, method and version, frozen output, calibration
period, positive sample size, out-of-sample calibration, time backtesting, and at least one recorded
Brier-score or log-loss value. Narrative judgment cannot open `VALUE_OK`.

## Team news

Team news is complete only when injuries, suspensions, rotation, workload, coach and tactical news
were checked from identified sources. The default maximum age is 48 hours; the exact boundary is
accepted, anything older is stale and `INCOMPLETE`.

## Official lineup

- `EARLY_BET` is restricted to `LOW` sensitivity and documented rotation robustness.
- `HIGH` and `MEDIUM` sensitivity use `WAIT_XI`.
- A confirmed lineup requires a primary official source.
- A pending lineup is `CONDITIONAL` only with a named condition, recheck time and expiry represented
  by the recorded conditional controls; otherwise it is `INCOMPLETE`.
- No lineup state can bypass an incomplete team-news gate.

These gates do not place wagers and do not alter the odds-free ranking.
