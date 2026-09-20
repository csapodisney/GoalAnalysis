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
- Live The Odds API adapter with explicit event mapping: COMPLETE
- One-command end-to-end shadow bundle: COMPLETE
- Local tests after Phase 13: 66 passed
- API key authentication: CONFIRMED
- API-Football Free current-season access: BLOCKED BY PLAN

## Current objective

Build the token-efficient data layer in shadow mode without changing Arthur's existing analysis logic.

## Active work

Phase 13 complete: live post-ranking odds adapter and auditable end-to-end shadow command.

## Decisions

- API-Football is the primary MVP football provider.
- Odds remain a separate data channel and have 0% weight in football ranking.
- Do not buy the Pro plan until the cached pipeline is ready for a live current-season trial.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Import the exact legacy Arthur prompt as a new immutable prompt version when supplied.
2. Configure the live model, odds key, and explicit cross-provider event map.
3. Upgrade API-Football only when the first current-season shadow run is ready.
4. Run and evaluate the complete pipeline in shadow mode before any cutover decision.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
