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
- Kerekasztal orchestration contract and shadow replay: COMPLETE
- Versioned prompt registry and OpenAI Responses runner: COMPLETE
- Post-ranking odds channel and deterministic ticket gate: COMPLETE
- Settlement, hash-chain performance log and token telemetry: COMPLETE
- Local tests after Phase 12: 61 passed
- API key authentication: CONFIRMED
- API-Football Free current-season access: BLOCKED BY PLAN

## Current objective

Build the token-efficient data layer in shadow mode without changing Arthur's existing analysis logic.

## Active work

Phase 12 complete: immutable shadow settlement, performance and token accounting.

## Decisions

- API-Football is the primary MVP football provider.
- Odds remain a separate data channel and have 0% weight in football ranking.
- Do not buy the Pro plan until the cached pipeline is ready for a live current-season trial.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Add a live odds-provider adapter behind the validated provider-neutral contract.
2. Import the exact legacy Arthur prompt as a new immutable prompt version when supplied.
3. Add one end-to-end command joining every completed stage.
4. Run the complete pipeline with fixtures and prices in shadow mode.
5. Upgrade API-Football only when that first current-season shadow run is ready.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
