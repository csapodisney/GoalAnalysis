# End-to-end shadow pipeline v1

Phase 13 connects the completed stages into one auditable shadow run:

1. API-Football fixture collection and immutable raw snapshots.
2. Deterministic, odds-free feature ranking.
3. Compact evidence packet and Kerekasztal role execution.
4. Arthur freezes the football selections and requested markets.
5. The Odds API prices only those frozen selections.
6. The deterministic ticket gate and token report are written to one bundle.

No component places a wager. Every bundle manifest records `real_wager_placed: false`.

## Credentials

Keep these values in environment variables and never commit them:

- `API_FOOTBALL_KEY`
- `OPENAI_API_KEY`
- `ARTHUR_OPENAI_MODEL`
- `THE_ODDS_API_KEY`

## Explicit event mapping

The football and odds providers use different event identifiers. Fuzzy team-name matching is
deliberately forbidden. Supply a JSON list such as:

```json
[
  {
    "fixture_id": "123456",
    "sport_key": "soccer_germany_bundesliga",
    "event_id": "odds-provider-event-id"
  }
]
```

## Run

```powershell
.venv\Scripts\python.exe scripts\run-end-to-end-shadow.py `
  --date 2026-09-20 `
  --history-csv data\history.csv `
  --odds-event-map config\odds-event-map.json
```

The output contains the screening result, fact packet, Kerekasztal result, ticket-gate result,
token summary, and a manifest of SHA-256 hashes. Live current-season execution still requires
the appropriate API-Football plan and valid event mappings.

The live odds request follows The Odds API v4 sports-odds endpoint and records its quota headers:
<https://the-odds-api.com/liveapi/guides/v4/>.
