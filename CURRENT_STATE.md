# Goal Analysis — CURRENT STATE

Last updated: 2026-09-20

## System status

- Phase 18 recovery: user confirmed successful installation; remote GitHub state not reverified
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
- Controlled end-to-end shadow coordinator: COMPLETE as a separate function; live CLI migration pending
- Gate results and reason codes in shadow manifests: COMPLETE
- Local tests after Phase 17: 94 passed (baseline)
- Local tests after Phase 18: 133 passed; Ruff passed on all new Python files
- Phase 19 independent history collector and market-specific evidence integration: IMPLEMENTED; live authenticated API trial pending
- Local tests after Phase 19: 167 passed; Ruff passed on all six new Python files
- Phase 19 installation: user reported complete
- Phase 20 independent daily fixture/odds mapping and live coordinator: IMPLEMENTED; authenticated live run not yet verified
- Local tests after Phase 20: 204 passed; Ruff passed on all six new Python files
- Phase 21 Betano-preferred, same-bookmaker fallback selection: IMPLEMENTED; authenticated live run not yet verified
- Local tests after Phase 21: 208 passed; Ruff passed on changed Python files
- Phase 22 expanded 18-competition league/cup/UEFA universe: IMPLEMENTED
- Local tests after Phase 22: 209 passed; Ruff passed on the changed test file
- Phase 22.1 explicit-empty odds credential isolation: FIXED after Windows test exposed environment leakage
- Phase 23 local control panel, encrypted Windows secrets, desktop shortcut and 07:30 scheduler: IMPLEMENTED
- Local tests after Phase 23: 212 passed; Ruff passed on changed Python files
- API-Football Pro current-season fixture access: VERIFIED by the user's first live run
- The Odds API key/configuration: PRESENT; live odds response still pending because the first run had no unstarted fixtures
- Phase 18 recovery package: cumulative Phase 14–18 files, including the master-prompt verifier and its dependencies; local installation must still be verified on the user's PC
- Arthur Pentagram v2.1 DAILY_223 appendix: VERSIONED; not activated in the legacy live pipeline
- Independent DAILY_223 research builder and CLI: IMPLEMENTED; live data collection pending
- Separate daily ChatGPT research task: ENABLED from 2026-09-21, around 08:00 Europe/Berlin
- API key authentication: previously confirmed; not retested in Phase 18
- API-Football Pro subscription: ACTIVE; current-season access confirmed

## Current objective

Preserve the existing five-ticket analysis logic and add an independent,
evidence-ranked daily 2×2×3 research branch in shadow mode.

## Active work

Phase 23: the independent live coordinator connects cached history,
current football fixtures, fresh prices, deterministic provider matching,
historical evidence and DAILY_223 construction. Each run produces JSON and
Markdown reports. Betano is preferred when returned by the configured EU feed;
otherwise one complete alternative bookmaker is selected by evidence strength.
The default universe now covers 18 supported domestic league, cup and UEFA
competitions rather than one Bundesliga date.
The localhost dashboard can start runs, render the latest ticket and history,
and copy a compact Astra review packet. A Windows desktop shortcut and a daily
07:30 scheduled run are available through the installer.
Context/news review and actual account-level availability remain pending. The
separate daily ChatGPT task does not run Python on the PC.

## Decisions

- API-Football is the primary MVP football provider.
- Existing blocks keep odds-free football ranking. DAILY_223 explicitly filters by price roles before positive-evidence ranking; higher prices do not add evidence points.
- DAILY_223 uses 2/2/3 floors, or at most 2% individual and total shortfall (minimum combined price 11.76), only if no strict construction exists.
- DAILY_223 is independent of other tickets' selections, vetoes and outcomes; it can reuse their matches but uses three distinct matches internally.
- Evidence points are heuristics, not probabilities or approved value bets. Facts and inferences remain labeled; no fabricated motivation or odds.
- Daily search and reporting are mandatory; data availability and a complete real ticket cannot be guaranteed.
- Collect history before refreshing the decision/price snapshot. Historical sample frequencies are descriptive, not calibrated probabilities.
- Phase 19 venue/form evidence uses 60% venue and 40% last-five frequency with explicit sample coverage; workload counts do not imply fatigue or motivation.
- Phase 20 matches only unique sport/home/away/kickoff identities, with explicit aliases and at most 60 seconds of kickoff difference; it does not guess fuzzy identities.
- Optional market failure preserves valid base markets; conflicting event identity does not. Requested and actual market coverage are reported separately.
- Region/currency are user-configured context; feed quotes do not prove bookmaker account availability or executable combined odds.
- Betano preference is nonblocking; all three final legs must still come from one bookmaker, and odds differences do not affect evidence scores.
- Phase 22 includes every requested national cup that has an explicit The Odds API sport key; Dutch and Portuguese cups await an additional verified odds source.
- Local API credentials are stored with Windows user-scoped DPAPI encryption; dashboard responses expose presence flags only.
- The local scheduled pipeline prepares the deterministic ticket; full automatic Astra/news review is a later integration step.
- Secrets remain in environment variables and never enter Git.
- Windows installs the `tzdata` package for Europe/Berlin support.

## Next actions

1. Install the Phase 23 control panel and verify tomorrow morning's scheduled DAILY_223 run.
2. Add sourced team news, cup/UEFA context, player load and independent refresh/outcome logging for DAILY_223.
3. Activate the versioned master through a token-efficient staged adapter; migrate the legacy live CLI to the controlled coordinator.
4. Configure and test live model/odds credentials and explicit cross-provider event mapping without exposing secrets.
5. Consider an API-Football upgrade only when the current-season shadow trial is ready.
6. Evaluate the complete pipeline in shadow mode before any cutover decision.

## Important rule

This file is the canonical human-readable current state of the project and must be updated after meaningful changes.
