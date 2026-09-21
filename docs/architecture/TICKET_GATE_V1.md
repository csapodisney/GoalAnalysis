# Post-Ranking Ticket Gate v1

## Boundary

The ticket gate runs only after deterministic screening, the frozen football ranking, all
Kerekasztal reviews, Dániel's counter-case and Arthur's final football decision.

Prices have zero weight in football selection. They cannot reorder, add or replace fixtures.

## Inputs

- completed `kerekasztal_shadow` output;
- Arthur's market and selection for each supported fixture;
- provider-neutral normalized odds quotes;
- timezone-aware observation time;
- quote-age and maximum-leg policy.

## Deterministic rules

- Fail if prices were attached before the gate.
- Fail if fixture order differs from the frozen ranking.
- Fail if a structurally vetoed fixture is marked selected.
- Consider only Arthur-selected fixtures and markets.
- Preserve their frozen order.
- Apply the maximum leg cap without backfill.
- Reject future-dated and stale quotes.
- Require every leg to be available at one bookmaker.
- Select the complete bookmaker offering the highest combined price.
- Report whether an optional target price is reached; never add a weak leg to reach it.

## Shadow odds adapter

`CsvOddsProvider` validates the provider-neutral odds contract without requiring a paid live
feed. CSV columns are:

```text
fixture_id,bookmaker,market_key,selection_key,decimal_price,quoted_at
```

## Command

```powershell
.venv\Scripts\python.exe scripts\run-ticket-gate.py `
  reports\daily\kerekasztal-openai-shadow.json `
  data\odds\quotes.csv
```

The output is still a shadow artifact. No wager is placed.
