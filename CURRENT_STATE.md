# Goal Analysis — CURRENT STATE

Last updated: 2026-09-20

## System status

- Workspace and GitHub branch: READY
- Active branch: `build/token-efficient-pipeline`
- Baseline and architecture audit: COMPLETE
- Provider-neutral models and interfaces: COMPLETE
- SQLite, TTL cache and immutable snapshots: COMPLETE
- Deterministic fixture screening: COMPLETE
- API-Football fixture adapter: COMPLETE
- Local tests before Phase 5: 17 passed
- API key authentication: CONFIRMED
- API-Football Free current-season access: BLOCKED BY PLAN

## Current objective

Build the token-efficient data layer in shadow mode without changing Arthur's existing analysis logic.

## Active work

Phase 5: cached daily fixture collection for the five top leagues.

## Decisions

- API-Football is the primary MVP football provider.
- Odds remain a separate data channel and have 0% weight in football ranking.
- Do not buy the Pro plan until the cached pipeline is ready for a live current-season trial.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Validate Phase 5 with a fake provider and local cache.
2. Add raw-response snapshot integration.
3. Feed the cached fixture universe into deterministic screening.
4. Add compact fact-packet generation.
5. Upgrade API-Football only for the first real current-season shadow run.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
