# Arthur Architecture and Token-Usage Audit

Date: 2026-09-19  
Scope: read-only audit of the available Arthur working package

## Conclusion

Arthur's reasoning remains intact. The primary optimization target is the data-hunting stage before the Kerekasztal: fixtures, statistics, team news, lineups and prices should be collected once, normalized and screened deterministically.

## Current architecture

### Deterministic side

- Football-Data historical CSV ingestion and reuse
- StatsBomb open-event processing
- home/away profiles
- common and similar opponent research
- game-state reconstruction
- Pi-rating and historical validation
- deterministic ticket gate
- evidence hashes and source manifests
- technical tests

### Daily operational side

The current daily process depends on long prompts, web fixture discovery, repeated source opening, LLM interpretation, five profile passes, ad-hoc odds lookup and repeated report generation.

No maintained live-fixture adapter, odds adapter, normalized daily database or in-repository agent orchestrator was found.

## Kerekasztal baseline

| Role | Primary market | Responsibility |
|---|---|---|
| Krónikás | Over 2.5 | Goal chain and path to the third goal |
| Ritmusőr | BTTS | Two-sided scoring and response behavior |
| Párharcmester | 1X2 | Strength and tactical matchup |
| Őrszem | Under 2.5 | Chance restriction and control |
| Merlin | Draw | Strength parity and equalizing behavior |
| Dániel | Audit | Strongest counter-case |
| Arthur | Integration | Final evidence-based decision |

## Largest token bottlenecks

1. LLM exploration of the full daily fixture universe.
2. Recollection of the same facts by several roles.
3. Repetition of the full Pentagram prompt.
4. LLM extraction from large raw HTML/JSON pages.
5. Textual recalculation of opponent graphs and game states already supported by code.
6. Full regeneration when only lineups, news or odds changed.
7. Missing call-level cost telemetry.

## Deterministic replacements

Daily fixtures, timezone checks, competition filters, provider IDs, home/away profiles, coach spells, opponent graphs, game states, event-quality flags, rest/travel, weather, lineup deltas, odds freshness, overlap resolution, ticket arithmetic and settlement can move out of the LLM.

## LLM responsibilities retained

Tactical interpretation, qualitative comparability, official news interpretation, structural objections, Dániel's adversarial case, Arthur's synthesis and readable reporting remain with the LLM.

## Target architecture

```text
football / odds / weather providers
-> adapters and normalization
-> SQLite + snapshots + TTL cache
-> deterministic eligibility and ranking
-> odds-free shortlist of 15-30 matches
-> compact fact packet
-> existing Kerekasztal
-> frozen football ranking
-> odds adapter and existing ticket gate
-> report, settlement and performance log
```

## Planned components

- `config/`: competitions and versioned profiles
- `providers/`: football, odds, weather and official-news adapters
- `normalization/`: models, team aliases and market keys
- `storage/`: SQLite, TTL cache and immutable snapshots
- `features/`: history, opponent graph, game states, coach spells and data quality
- `screening/`: hard rules, explainable ranking and shortlist pipeline
- `agents/`: compact fact packet and Kerekasztal orchestration
- `engine/ticket_gate.py`: preserved deterministic gate
- `reports/`, `settlement/`, `jobs/`: daily operation and audit
- `logs/token_usage.jsonl`: token and web-call telemetry

## Implementation order

1. Preserve inventory and baseline.
2. Add provider-neutral models and interfaces.
3. Wrap existing historical components.
4. Add cache and SQLite.
5. Build deterministic fixtures and screening.
6. Generate fact packets.
7. Connect the existing Kerekasztal.
8. Attach odds after ranking freeze.
9. Add settlement and telemetry.
10. Validate in shadow mode before cutover.

## Profile versioning

Pentagram v1 remains historical. The later mixed top-league profile—Bundesliga, Premier League, Serie A, La Liga and Ligue 1; up to six legs; approximately 20x only when genuinely available—must be added as a separate versioned profile.
