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
- Arthur Pentagram 2.0 source import and compatibility gate: COMPLETE
- Typed PREMATCH / FINALIZE / AUDIT run control: COMPLETE
- Deterministic candidate-status precedence: COMPLETE
- Model-calibration approval gate: COMPLETE
- Team-news and official-lineup freshness gates: COMPLETE
- Local tests after Phase 16: 91 passed
- API key authentication: CONFIRMED
- API-Football Free current-season access: BLOCKED BY PLAN

## Current objective

Build the token-efficient data layer in shadow mode without changing Arthur's existing analysis logic.

## Active work

Phase 16 complete: calibrated-model, team-news and official-lineup approval gates.

## Decisions

- API-Football is the primary MVP football provider.
- Odds remain a separate data channel and have 0% weight in football ranking.
- Do not buy the Pro plan until the cached pipeline is ready for a live current-season trial.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Connect run control and approval gates to the end-to-end shadow coordinator and manifests.
2. Add master-prompt activation through a token-efficient staged adapter.
3. Configure the live model, odds key, and explicit cross-provider event map.
4. Upgrade API-Football only when the first current-season shadow run is ready.
5. Run and evaluate the complete pipeline in shadow mode before any cutover decision.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
