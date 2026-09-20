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
- Raw-response snapshots and cached daily screening: COMPLETE
- Deterministic goal features and odds-free ranking: COMPLETE
- Compact Arthur fact packet and evidence hashes: COMPLETE
- Local tests after Phase 8: 34 passed
- API key authentication: CONFIRMED
- API-Football Free current-season access: BLOCKED BY PLAN

## Current objective

Build the token-efficient data layer in shadow mode without changing Arthur's existing analysis logic.

## Active work

Phase 8 complete: compact, frozen and auditable Arthur fact packets.

## Decisions

- API-Football is the primary MVP football provider.
- Odds remain a separate data channel and have 0% weight in football ranking.
- Do not buy the Pro plan until the cached pipeline is ready for a live current-season trial.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Connect the existing Kerekasztal roles without changing their responsibilities.
2. Preserve Dániel's mandatory adversarial review and Arthur's final authority.
3. Attach prices only after the frozen football ranking.
4. Add settlement, performance and token-usage telemetry.
5. Upgrade API-Football only for the first real current-season shadow run.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
