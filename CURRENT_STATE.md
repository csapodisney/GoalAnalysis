# Goal Analysis — current state

Updated: 2026-10-20. Release: Arthur portfolio v3.11 (Tipster klub).

## October 20 — autonomous sequential roadmap execution ACTIVE

- Latest user instruction supersedes the earlier per-point approval/stop rule:
  continue the remaining roadmap without questions; notify only if a different
  model is needed. Implement ONE numbered subtask at a time, test and document
  it, then proceed. DAILY_223 protection and all deployment gates still apply.
- Governing plan: C:\Users\HP\Downloads\ARTHUR_DEVELOPMENT_ROADMAP.md.
- Execution evidence: artifacts/roadmap-execution-20261020/. Initial protected
  source/config/test/script/input hashes and ledger fingerprints are recorded.
- 1.1 and 1.2 VERIFIED: canonical entity contracts and append-only evidenced
  provider identity registry. 208 native TheStats competition bindings seeded
  in the existing sports DB after backup, with all 12 prior table hashes intact.
  2185 tests + 3 golden replays pass; DAILY_223 7/7, ticket ledger unchanged.
- 1.3 VERIFIED: exact UTC/name/league matcher wired to TheStats collection;
  80 boundary tests, 13/13 existing real matches retained, full suite 2265 and
  3 golden replays pass. Only prior source changed: jobs/the_stats_context.py.
- 1.4 VERIFIED: evidenced per-field priority in an integrated SHADOW audit;
  143 targeted tests, final full suite 2400 and 3 golden replays pass. No
  decision change, 0 external requests; DAILY_223 7/7 and ledger unchanged.
  TheStats odds remain deferred to 3.1/3.2; no live provider promotion.
- 1.5 VERIFIED: all source observations retained with stable hashes, freshness
  order and conflict diagnostics; 175 targeted/2432 full tests and 3 golden
  replays pass. All 613 real-data audit fields/325 observations preserved.
- 1.6 VERIFIED: full default25/conditional3/manual scope inventory, 208 real
  catalogue records; 17 mappings (10 existing/7 SHADOW proposals), 11 explicit
  identity gaps. Integrated without extra API calls or route activation.
  98 targeted/2457 full tests and 3 golden replays pass; ledger/DAILY intact.
- 1.7 VERIFIED: source/provider matrix and expandable league map in the Szent
  Gral panel. Read-only archived-run audit at original cutoff, cached without
  future leakage. 164 targeted/2469 full Python tests, 109 new+318 existing
  frontend assertions and 3 golden replays pass; 390px component/desktop QA.
  No live server restart; its existing loaded Python needs a normal restart.
- Phase 1 complete (1.1–1.7), each separately verified/documented.
- Active: 2.1 unified deduplicated historical results. No later task is marked done
  without implementation and verification. Time-based 7/30-day gates cannot
  be represented as complete from a single day's work.

## October 20 — odds-history usage reporting fix completed; STOP here

- User authorized only the coverage-label fix, testing and report, then stop.
  See `artifacts/odds-history-usage-fix-20261020/REPORT.md`.
- Current quote snapshots no longer prove historical movement/ranking use.
  Verified comparisons plus existing ranking/selected-leg evidence determine
  MISSING / STORED_ONLY / USED_RANKING. No decision rule changed.
- 143 tests pass. Three full offline golden replays preserve all ticket
  decisions and numeric outputs, including DAILY_223. Only coverage reporting
  and code/report provenance differ. Zero external requests/forbidden attempts.
- DAILY_223 pre/post 7/7. Only two source files and one test file changed;
  511 other protected files and 3,639 rows in 15 ledger tables are intact.
- Original golden inputs and 0.5 baselines remain immutable. The global
  strategy hash changes because jobs/portfolio.py now passes telemetry;
  ticket decision hashes separately prove unchanged selections.
- No server restart or live activation verified. The updated reporting applies
  to runs loading the new code; historical reports stay frozen.
- Do not start another roadmap task without the user's next instruction.

## October 20 — roadmap 0.5 completed; STOP here

- The user authorized 0.5 separately and requires a report and stop afterward.
  Do not start another numbered task or change strategy/publication rules.
- `artifacts/golden-baseline-20261020/REPORT.md` records the current coordinator,
  current local model fits and current effective settings (1 EUR, 10–50, 30
  profiles) on all three immutable golden input packages, with historical
  team restrictions and cached Astra answers. No later data was backfilled.
- Six offline integration runs: all three pairs have identical full-report,
  ticket, DAILY_223 and numerical hashes. Four adapter safety tests pass.
  No external calls or forbidden attempts; runtime and coverage are recorded.
  All outputs are separate artifacts, not live ledger publications.
- DAILY_223 pre/post 7/7; 464 protected files and all 3,639 rows in 15 ledger
  tables unchanged. Historical usage counters are separate from replay's zero.
- Finding at 0.5: decision_data_usage called the new quote snapshot
  USED_RANKING without previous odds snapshots. It was left unchanged in
  the immutable baseline and fixed in the separately authorized task above.
- Historical data/review gaps remain; READY, PAPER_AUDITION and draft outputs
  are counted separately. Do not interpret the baseline as a profit backtest
  or a full live-provider latency benchmark.

## October 20 — prior roadmap 0.4 delivery

- User requires STRICT SINGLE-TASK execution. The governing supplied roadmap is
  `C:\Users\HP\Downloads\ARTHUR_DEVELOPMENT_ROADMAP.md`. The prior 0.4 delivery
  stopped there; 0.5 was subsequently authorized and is recorded above.
- Three frozen input packages are under `artifacts/golden-inputs-20261020/`:
  October 17 busy weekend (87 fixtures), October 12 sparse weekday (5), and
  October 3 cup/national day (75). Read `REPORT.md` for provenance and gaps.
- Source captures, exact IDs, odds, history, raw historical provider responses,
  archived numerical/Astra results and settings are retained with SHA-256
  manifests. The offline loader supplies a UTC view and explicit frozen clock;
  it is not yet wired to a ticket-generation replay.
- Validation: 3/3 packages; 12/12 negative/offline tests. DAILY_223 matches the
  October 16 preservation proof, 7/7 files. All 343 preexisting protected
  source/config/test/script files and 984 rows in five ledger tables unchanged.
- No external API/extra LLM calls or live application changes were made.
  Historical missing xG/player/weather/Grok inputs and unknown full code
  revisions remain explicit. Do not backfill them with later-known information.

## Latest user direction and October 18 delivery

- The user explicitly selected SUNDAY OCTOBER 18 for the current regenerated
  package, not Monday October 19. Preserve DAILY_223 exactly. Finish the current
  package and verification before any additional feature development.
- TheStatsAPI is NOW ACTIVE and works. Older 403/subscription notes below are
  historical. Twelve detailed live probes passed; the daily supplement used
  another bounded 40 calls and matched 13/35 fixtures with 43 available facts.
- Latest historical-import preference: FIVE YEARS, newest first, no LLM.
  `data/arthur/the-stats-history/active-plan.json` covers 20 competitions from
  2021-10-18 through 2026-10-17. Today belongs to daily collection; historical
  import uses finished earlier matches. Existing older archives remain intact.
- Daily work has priority. The existing nightly after-publication importer uses
  at most 20 requests, a time limit, quota reserve and durable checkpoints. Do
  not silently raise budgets or create another paid/model-based background loop.
- Delivery report: `analysis/2026-10-18-experts/NAPI_RIPORT.md`. Its final section
  and `final-delivery-proof.json` describe the actual publication when complete.
- DELIVERED: run `2026-10-18-911a43ba0c35`, frozen revision 3, 24 cards, 29 distinct
  fixtures, max exposure 7/24, odds 6.048–39.2925. DAILY_223 objects in all five
  groups and all six protected files exactly preserved. Effective freeze hash:
  `8c4174041032518558a69e16d2354150fb91f6cbc69b18fc4026bd957eb2ba22`.
- Live dashboard and German TXT both serve the new 24-card package. Latest Grok
  whole-package attempt is UNAVAILABLE / TRANSPORT_UNAVAILABLE, request ID
  `60f8b940138a4bde82ad240c1d7a45cb`. Do not claim it reviewed the fresh package or
  automatically repeat the paid request. Zero-token model-list diagnostic passed
  HTTP 200 and confirmed grok-4.7; paid response/cost remain unknown.
- First five-year backfill batch completed with 20 calls. TheStats history now
  has 400 provider-scoped matches; monthly remaining 99,919. Football-Data still
  has 78,235; do not add these as cross-provider-deduplicated unique matches.
- Dashboard static changes load successfully. Restarting the running server was
  blocked by automatic operation review, with no detailed reason. Do not work
  around that rejection using another process-kill mechanism. A normal later
  restart is needed to activate all loaded backend changes.

## October 18 sports, news and weather archive

- Main database remains `data/goal_analysis.sqlite3` (about 364 MiB after import),
  separate from the immutable ticket ledger. Backup before normalized import:
  `data/backups/pre-sports-archive-20261018.sqlite3`.
- Imported 78,235 historical matches from Football-Data.co.uk: 22 competition
  codes, 11 seasons (2016/17 through partial 2026/27), 242 CSV files from only 11
  public ZIP requests. Three malformed rows were quarantined; no whole file lost.
  Every named source column and raw CSV/ZIP is retained. Empty trailing headers
  are tolerated only when their values are empty; one ambiguous extra value was
  quarantined without inventing a column name.
- New sports_archive_* tables retain source-scoped identities, immutable data
  revisions, source URLs, actual receipt times, content hashes and import errors.
  Historical pre-closing/closing odds are explicitly NOT current executable
  quotes; pre-closing is not relabelled opening. Unknown CSV kickoff timezones
  remain unknown. Conservative as-of queries exclude same-day match results and
  every observation collected after the cutoff. Reimport reused 1,596 records
  with zero new records/imports; SQLite integrity and foreign keys pass.
- Existing raw API-Football and The Odds API snapshots remain in this database's
  source registry and data/raw. No paid sport or Grok requests were made during
  this archive phase. TheStatsAPI still requires an active subscription; do not
  retry the previously rejected key without a relevant account-state change.
- NewsStore now archives accepted, cited Grok news/X claim summaries after both
  new and cached reviews, with original publication/fetch/completion timestamps,
  source, author, request/model and verification status. Revised assertions are
  immutable; source material remains UNVERIFIED, not established fact. Two older
  Grok results were imported, containing ZERO accepted news claims. Do not claim
  we have an X-news dataset already. Optional before/after measurement links are
  explicitly OBSERVATIONAL_NOT_CAUSAL; no automatic causal coefficient exists.
  Nightly/gate reviews now sample an advancing completion clock after the response,
  so newly fetched claims are not rejected against the earlier request-start time.
  Cached responses retain their original completion time. Clock failure is explicit
  and cannot discard an already paid response. 188 Grok/provider/integration tests
  passed in the final regression group (overlaps the archive group below).
- WeatherStore separates FORECAST, OBSERVATION and REANALYSIS, stores full provider
  payloads when available, target kickoff/location, receipt/issue times and units.
  Imported 404 weather records from 74 saved reports: 46 sourced forecasts and
  358 explicit missing-data records. Their exact original retrieval time is
  unknown: report completion is labelled REPORT_AVAILABLE_AT_UPPER_BOUND, never
  falsely described as a precise fetch timestamp. Original reports are untouched.
- Future manual and nightly generation automatically archives weather via
  make_weather_provider; failed lookups are timestamped UNKNOWN, while archive
  failures preserve the forecast and expose archive_status. The live Karlsruhe
  probe correctly kept its ambiguous city location UNKNOWN instead of guessing.
- Open-Meteo historical ERA5 works through the free, keyless archive endpoint.
  One live probe plus a bounded batch saved 3 sourced reanalyses. The batch stopped
  on a later provider timeout (1 UNKNOWN, 12 deferred); no retries. Forecast city
  coordinates are retained at city precision, not called exact stadium locations.
  Five supported variables: temperature, humidity, precipitation, weather code,
  wind; ERA5 wind gusts are unsupported and explicitly unknown. Require >=6 days
  age for ERA5's publication lag. Persistent cache avoids repeated downloads.
- Existing nightly workflow performs at most TWO historical-weather HTTP requests
  after successful ticket archival and only with >=45 seconds spare window.
  Durable pre-call claim prevents replay after uncertain crashes. Failures cannot
  modify or invalidate published tickets. Manual bounded runners:
  scripts/import-arthur-football-history.py,
  scripts/archive-arthur-context.py (local-only),
  scripts/import-arthur-weather-history.py --max-requests 5.
- Proof and source metadata: analysis/2026-10-18-football-data/; runtime backfill
  reports: data/arthur/context-backfill.json and weather-history-import.json.
  252 combined archive/weather/nightly regression tests passed; focused lint clean.
  Six protected source/config/report hashes and the October 18 freeze hash remain
  unchanged: 8ce91d44e2389145c750c2876ba00531498a2a2e2bff57da65722f1ca1e616af.
- Next steps remain separate: reconcile cross-provider team/fixture identities,
  measure fixture-level lineup/injury/xG coverage, then validate historical model
  features and plausibility simulations with frozen data/model versions. These
  newly imported CSVs have NOT silently retrained live models or revised tickets.
  Commercial simulator/API is a future goal, not implemented. Source usage rights
  are stored as UNREVIEWED where not established; Open-Meteo free hosted API is
  for noncommercial/prototyping use, paid commercial access must be resolved before
  selling the future API. Preserve weather/news provenance in that future design.

## October 18 Grok sports-data handoff completed

- The Grok packet now includes exact structured 20/10/5-match hit/sample counts,
  full saved GAUSS/KEPLER/LAPLACE market predictions and training metadata,
  available goal means/form sequences/workload, fixture venue/season/provider IDs,
  bookmaker and sanitized source URLs with observation times. No new sports fetch.
- Explicit FULL_TIME scope prevents applying full-match probabilities to halves;
  each model's stability meaning is supplied. Shared models are labelled related,
  unvalidated evidence. Unknown statistics remain unknown. Available per-leg form
  is shared by fixture/period; the full historical archive is not sent to an LLM.
- All nonempty cards go in one request. Lossless column tables, common values,
  repeated text and compact W/D/L sequences keep the existing 100,000-character
  input limit. No probability, count, time or ticket identity is discarded by
  this transport encoding. Oversized packages reject explicitly, never send a
  partial selection of tickets. Budget/search/output limits remain unchanged.
- Source query IDs survive credential stripping. Grok-bound future/invalid quote
  timestamps cannot be silently removed while retaining their prices; such a
  review packet rejects. Future model fields are omitted. These checks do not
  alter core ticket eligibility or DAILY_223. Helper/codec changes now invalidate
  the review cache together with provider changes.
- Validation: 181 focused Grok tests and changed-code lint pass. The actual saved
  revision-2 package passes a full offline transport/response roundtrip: 25 cards,
  30 fixtures, 91 distinct selections, 103 structured historical observations,
  all 30 fixtures' model predictions, 23 fixtures' available form, 71 quote sources.
  Provider input: 96,941 characters; no additional prose truncation was needed.
  No new paid call was made; this is not a new live Grok opinion.
- Proof: `analysis/2026-10-18-grok-review/sports-packet-verification.json` and
  `audit_packet_offline.py`. Protected file hashes and effective freeze hash are
  unchanged. New dashboard/scheduled analysis runs start fresh child processes,
  so they load this code without changing today's frozen tickets.
- Next user-requested work: TheStatsAPI.txt supplies another sports provider.
  Prioritize fresh relevant data for daily analysis; use only spare quota for
  historical database backfill. Historical API subscription/provider was initially
  deferred; identify the new service and its limits before planning downloads.
- TheStatsAPI access checked: free health returned HTTP 200; two bounded
  authenticated probes returned HTTP 403. The second explicitly reported
  `KEY_REVOKED`: `API key has no active subscription plan`. The supplied key is
  saved under Windows-user DPAPI as THE_STATS_API_KEY. Its root handoff file is
  Git-ignored and remains in place. Other credentials are unchanged. No data
  collection or background task is enabled; user activation is pending. Evidence:
  `analysis/2026-10-18-thestatsapi/access-check.json`. Do not repeatedly probe an
  inactive subscription. Official API: https://api.thestatsapi.com/api ; quota
  headers are per-minute key limits and per-account monthly limits, not assumed
  plan allowances. Three real Windows synthetic DPAPI tests pass.
- Prepared `providers/the_stats_api.py`: health, coverage and match list/detail,
  bounded pagination and request count, numeric quota headers, fixed official
  host, no redirects/retries, controlled errors and source/time/hash provenance.
  Environment name matches the saved THE_STATS_API_KEY. 27 offline provider tests
  and lint pass. It is deliberately not yet connected to scheduled collection:
  actual account limits and coverage cannot be read until subscription activation.
  Daily enrichment and historical backfill remain outstanding after that step.

## October 18 independently audited Grok correction — recorded revision 2

- At the user's explicit request, reviewed Grok's saved whole-package opinions
  against the immutable original history, model predictions and quote records.
  No new paid Grok, football, odds or model-fitting request was made.
- Effective frozen run: `2026-10-18-grok-reviewed-r2`, revision 2, saved at
  `2026-10-17T22:42:09.341508+00:00` (00:42 Berlin). Snapshot hash:
  `8ce91d44e2389145c750c2876ba00531498a2a2e2bff57da65722f1ca1e616af`.
  Original `nightly-2026-10-18-a1` and every previous immutable ledger row remain.
- Exactly six one-leg substitutions, unchanged profile styles and leg counts:
  Forma Soma Paris FC home -> Verl away (24.03 combined);
  Hazai Márk KSC home -> Lyon home (14.90);
  Vándor Zóra Alavés away -> Atalanta away (32.72);
  Góltérkép Emese Le Havre under -> Forest under (10.12);
  Pajzs Dorka Le Havre under -> Juve Stabia under (12.71);
  Rövid Lotti Le Havre under -> Milan under (6.51).
- All 25 nonempty cards remain; maximum fixture concentration is 8/25 (32%).
  DAILY_223 payloads in every report group, its source/config files, all other
  profile cards, and every retained leg within the changed cards are unchanged.
  No actual wagers were recorded or modified. SQLite backup and isolated
  publication rehearsal passed, plus 39 repair/freeze regression tests.
- Independent audit corrected Grok's Leeds sample claim (3/5 each, not 1/5)
  and Hoffenheim/Verl away-team label. Retained numerical GAUSS: stable fits are
  not disproved by the history conflict. Retained Verl, Nóra, Mira and Vera
  where the full evidence did not substantiate forced removal.
- Merlin and Párharcmester concerns remain conditional: no available replacement
  improves their own evidence while meeting the review's other checks. Márk
  still retains Paris FC under his fixed Kepler-led style: no eligible complete
  option excludes both Paris and KSC without changing the profile. Soma's Verl
  swap improves model agreement, not away-venue form. Lyon clears the price in
  all three models but their estimates are not tightly clustered.
- Evidence and operation scripts: `analysis/2026-10-18-grok-review/`;
  permanent report/audits: `reports/arthur/2026-10-18-grok-reviewed-r2/`.
  This is an explicit saved-data audit, not a new web/Astra validation. Source
  observation and quote times remain intact; the publication clock is new.
- Both localhost and the phone's Tailscale HTTPS endpoint returned revision 2,
  all 25 current recommendations, and the updated German TXT export.
- Following the user's xAI documentation reference, tightened future batch
  responses: every exact ticket ID is a required unique JSON object key; profile
  and flagged fixture identities are derived locally from bounded leg indices.
  Nonempty pro/contra text is capped at 160 characters. Duplicate JSON keys
  reject; legacy saved array replies remain supported. Missing/invalid coverage
  is still reported honestly as partial. 97 Grok regression tests and lint pass;
  no paid test call, budget, search, reasoning-effort or cache-policy changes.
  Documentation: https://docs.x.ai/developers/model-capabilities/text/structured-outputs

## October 18 Grok whole-package review and token policy

- Persistent user preference: send ALL nonempty tickets together in ONE compact
  Grok request, never one request per tipster. Include the portfolio objective:
  at least three daily winners and winning decimal odds summing to at least 30,
  EUR1 per ticket. Preserve fixed tipster styles and protected DAILY_223.
  The user understands uncertainty; do not repeat generic disclaimers in chat.
- Use the existing collected data. Shared fixtures, selections, history evidence,
  model estimates, model calibration labels, quote provenance, weather and prior
  sourced context are deduplicated. Do not fetch odds/history/weather/models
  again for Grok. X search is only for a concrete, important missing or conflicting
  news item; at most two focused searches are requested for the entire package.
- Official xai-sdk 1.20.0, Client(api_key=XAI_API_KEY), chat.create(model="grok-4.7")
  and chat.sample(). One request, no automatic SDK retries, two tool turns,
  6500 output tokens, no image/video research. Persistent semantic packet cache:
  unchanged selections/data reuse the saved result, regardless of elapsed time.
- Local cost policy: at most two new Grok requests/day, USD1/day and USD20/month.
  An uncertain request reserves the remaining daily allowance. These are local
  stopping thresholds, not provider-side hard caps on an in-flight request.
- Whole-pack review includes READY, REVIEWED, DRAFT and MODEL_READY cards with
  complete valid legs. Empty/invalid/locked cards are explicitly accounted for.
  Every supplied ticket must receive concise pro/contra/verdict/flagged-leg
  output for full coverage. Daily is reviewed but never repaired.
- New generation invokes jobs/grok_gate.py before report persistence. Only
  QUESTIONABLE exact legs go into one independent Codex batch. A corroborating
  primary source observed through completed native web research is required;
  high odds alone do not authorize a change. At most one scoped repair pass
  uses existing same-profile options, unchanged persona gates, duplicate and
  exposure rules, with a fresh 15-minute publication check. No valid replacement
  means the original is retained and explicitly marked unresolved.
- Never send the corrected package back into another Grok dialogue. Post-save
  and nightly learning reuse the completed gate summary. Model failures do not
  discard an otherwise completed package. Previous frozen/played tickets remain
  immutable. Standalone scripts/run-arthur-grok.py is read-only toward tickets.
- Credential imported from the user's root Grok_API.txt into user-bound Windows
  DPAPI data/secrets/XAI_API_KEY.txt, verified, and plaintext source removed.
  The saved key is loaded by both dashboard and scheduled launchers. Neither
  credentials nor raw provider errors belong in prompts, logs or source control.
- Grok reports and measured usage appear on the dashboard's Operations page.
  The SDK job stores separate append-only audit rows; independent checks also
  have durable reservations/results, preventing duplicate requests after restart.
- Live verification on October 18: all 25 nonempty saved tickets were sent
  together (30 fixtures, 91 selections, 110 historical evidence records). First
  response had an empty list; the schema now requires exactly one critique per
  ticket. The second returned 23 accepted critiques; Kronikas and Bastya Bence
  failed validation and remain explicitly missing, not fabricated as reviewed.
  No fresh X search was performed; the model used supplied information.
  Total measured cost of both attempts: USD0.236652. Daily two-call cap reached;
  no third paid retry is authorized by this implementation's local policy.
  The two audit records and their costs remain immutable.
- New generation is wired to the prepublication gate; the current frozen
  October 18 package was only reviewed, not regenerated or revised. Protected
  DAILY engine, shared options, persona catalogue, both settings files and
  the frozen report still match the pre-change hashes. The dashboard was
  restarted; loopback and Tailscale HTTPS both expose the same Grok result.
- Final offline compression reduces that same 25-ticket packet from 99,093 to
  77,702 characters (77,780 after provider normalization), without losing any
  ticket, leg, price, probability or full source provenance. A 40-ticket,
  160-selection synthetic package also fits the 100,000-character bound and
  passes provider validation. Invalid flag references now preserve the valid
  pro/contra opinion but make the entire card non-actionable and visibly partial.
  This cannot recover the two discarded opinions from the earlier live call.

## October 17 opportunity coverage and club search improvement

- Pre-event league audit: 87 configured fixtures, 84 with prices, 41 eligible
  for at least one club, 24 selected by clubs / 36 across all 26 tickets.
  Premier League, Championship, Bundesliga, Dutch and French leagues were
  present. Bundesliga had two club-eligible fixtures but zero club selections;
  three Bundesliga fixtures appeared in nonclub recommendations.
- Czech topflight (five raw-calendar games) and Iceland topflight (three)
  were outside configured scope. They are not enabled by this change. Adding
  supplementary club data must preserve DAILY inputs and check data coverage
  and existing request budgets. Several promoted-team fixtures lack the eight
  same-league historical matches required by numeric models; this evidence
  floor is not relaxed.
- Expanded only club combination search from beam 32 / options 128 to beam
  128 / options 512. Persona gates, scoring, odds bands, shared search defaults,
  numerical profiles, catalogue, settings and DAILY remain unchanged.
  New diagnostics show eligible fixtures versus fixtures in retained options.
- Saved six-profile pre-event replay: Nora 19->25 represented fixtures,
  Reka 16->21, Mira 14->18, Vera 15->15, Soma 23->31, Lotti 24->33.
  All alternatives obey unchanged contracts; best scores unchanged for these
  six profiles. Runtime 4.34->16.81 seconds in the comparison. This remains a
  bounded search, not an exhaustive optimum or evidence of higher profit.
- Correction to the prior conversational explanation: the October 17 clubs
  ALREADY used weak-leg-aware utility v2; they did not use the shared sqrt
  score. The saved 82.33%, 87.23%, 88.42% own-quality retention ratios remain
  correct. Diagnose current code and saved strategy before changing scoring.
- No provider/LLM calls or live ticket generation. Codex stylist still sees
  at most eight alternatives per editor and three editors; a regression covers
  512 local options. Existing October 17/18 recommendations (185 saved rows)
  and the frozen October 18 report remain byte-identical.
- 129 distinct focused tests passed (116 + 31 runs, 18 overlapping), affected
  Ruff clean. Evidence and replay scripts are in
  `analysis/2026-10-17-ticket-review/league-funnel.json`,
  `club-search-budget-replay.json`, `search-preservation-after.json`.
- User's desired three daily winners / winning odds sum >=30 is a measurement
  target, not a guaranteed construction constraint. At EUR1 each on 30 tickets,
  a winning-odds sum of 30 only breaks even before deductions and API costs.

## October 17 end-of-day review and Grok proposal

- Reconciled current provider results with active immutable recommendations.
  Two bounded manual settlement passes used 20 + 18 API-Football requests,
  returned no errors, and drained the current queue. Dunkerque won 3–2 away;
  original stored odds are 4.65–5.00 across four losing tickets.
- The latest 26-ticket revision has one winner (Archív Nóra, 11.6255015),
  producing EUR11.63 returned / EUR26 hypothetical stake, net -14.37. This is
  not the user's actual-money balance. Revision 3 has two winners, but one is
  the old 1.11 Krónikás single; the user's reported two winners/minimal loss
  therefore still needs reconciliation with the actually played package.
- Fixed result-refresh priority: current/played unfinished fixtures precede
  archive and terminal correction checks. One in five request slots preserves
  background progress. Existing call budgets unchanged. Queue diagnostics show
  active unfinished vs background backlog. Nightly still has a once-per-job
  settlement budget; this patch does not add periodic result polling.
- Added selection diagnostics: own-profile best score and selected/best ratio.
  No gates, penalties, selection ordering or DAILY_223 logic changed. Saved
  input replay found 82.33%–100% score retention across club profiles. This
  motivates comparison, not immediate outcome-fitted retuning.
- Grok plan: optional X-news research and blind critique of club alternatives,
  initially separately recorded before kickoff without modifying selections.
  DAILY inputs and output must remain isolated. No Grok credential requested,
  integration enabled or paid Grok call made. Budget remains a proposal.
- Reproducible local evidence and design: `analysis/2026-10-17-ticket-review/`
  (`result-audit.json`, `selection-audit.json`, `grok-integration-plan.json`).
  Focused validation: 65 ledger/nightly and 41 diversification tests passed;
  affected source/test lint passed.
- Restarted the dashboard through its registered `Arthur-Dashboard` task;
  task is Running and both loopback and Tailscale HTTPS returned HTTP 200.
  The protected DAILY source hash still matches the recorded value.
- Follow-up receipt reconciliation resolves the two reported winners: Archív
  Nóra matches all four selections (different bookmaker prices); Őrszem matches
  all five fixtures but the user played Under 3.5 instead of Under 2.5 on
  Padova, Lille and Valencia. Padova's 1–2 result is the decisive difference.
  The user confirmed occasionally editing selections. These are not the old
  Krónikás single plus Nóra; that earlier explanation does not match receipts.
  Preserve original recommendation outcomes and separate actual played variants.
  Receipt gross odds products: 9.400914432 and 10.0645254. Full actual-day net
  remains unknown. See `receipt-reconciliation.json` beside the original audit.

## October 17 Krónikás correction — published revision 4

- The previous club-only correction intentionally preserved legacy specialists,
  leaving Krónikás as a 1.11 single. Only that candidate had Astra coverage;
  the READY-only main selector preferred it over unreviewed accumulators.
- Krónikás now has its own fixed full-time Over 2.5 construction: 3–6 distinct
  fixtures, total odds 10–40, one bookmaker/region/currency. Existing evidence
  gates and normalized evidence ranking remain. Apply minimums before option
  quotas, retain low-price prefixes while growing combinations, and reject
  invalid alternates/research fallbacks at publication. No singleton fallback.
- The normal review batch reserves the whole constructed Góllánc after the
  unchanged DAILY priority and equivalent-quote cohort. Contrarian packets
  cannot displace these reserved legs; the existing batch budget remains.
- Published run `2026-10-17-kronikas-93765f34a908`, revision 4, hash
  `2a3a7ad67c5a18daa49eeb6bd0f89271f4ccfa045e429f488ecaa5db218938a0`.
  Krónikás: six fixtures, total 12.01201000916, REVIEWED, all six candidate-bound
  assessments completed (three support, three neutral), with sourced research.
  Source quotes/history retain original timestamps; no fresh odds are claimed.
- All other profile objects, including DAILY_223, are exactly preserved across
  tickets, previews, proposals, recommendations and profile diagnostics. The
  protected DAILY code hash remains `d7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c`.
  The package still has 26 recommendations; maximum fixture use is 8/26.
- Krónikás remains a separately recorded recommendation/proposal. Its overlap
  with DAILY does not satisfy the separate two-ticket READY main-portfolio cap,
  so it is not forced into that main slot. No actual wager or old immutable
  record changed. Backups and ledger-copy rehearsal precede publication.
- New `scripts/rebuild-arthur-kronikas.py` defaults to dry-run, validates an
  optional candidate-hash-bound review, preserves unrelated profiles, checks
  lockout/exposure and appends a freeze revision with compare-and-swap.
- Verification: full-suite failures from the new JSON metadata/abstention
  schema and outdated generic assumptions were repaired; all 20 remaining
  failed checks passed, followed by 112 focused tests and clean affected lint.
  Earlier full run passed 1,345 tests. Targeted review needed two actual CLI
  requests; completed web research was reused after a console callback encoding
  error, rather than repeated. Costs and provenance are retained in the report.

## October 17 club accumulator correction — published revision 3

- Owner rejected the unintended low-odds singles/doubles. Root cause: v5 capped
  11 club profiles at one leg, eight at two and one at three, while lower odds
  bands were merely descriptive. v6 assigns each persona a fixed accumulator
  shape and actual total-odds band, keeping its per-leg model/value gates.
- Primary and alternative construction obey the same bounds. The final
  allocator validates the current catalogue even if caller metadata is absent.
  Bounded search retains price-diverse prefixes so lower-price partial tickets
  can grow into compliant combinations. Exact duplicates remain excluded;
  whole-ticket similarity and soft individual-fixture reuse penalties apply.
- Final run `2026-10-17-club-872e5e473566`, frozen revision 3, snapshot hash
  `e72c1102fcce775483114a55cf2baf2482e214856b289f491f4126fdc24b5c23`.
  16 club recommendations, 3–5 legs, odds 6.036074–33.022704 (13 at least 10).
  Four clubs abstain: three contrarians and BTTS specialist Dupla Áron, which
  has only three individually eligible fixtures for its four-leg minimum.
- Full frozen package: 26 nonempty recommendations with existing mixed
  review statuses. Clubs cover 24 distinct fixtures. Maximum fixture exposure
  across the full package is 7/26 (26.92%); no exact ticket duplicates.
- EVERY nonclub report object, including the entire DAILY_223 ticket/card and
  its provenance, is exactly preserved. Protected core SHA-256 still
  `d7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c`.
  Original immutable ledger rows and actual wagers are unchanged. Backup and
  rehearsal database precede publication; previous freezes remain recoverable.
- Rebuild reuses the prior saved input/model snapshots and original quote
  times. No new provider, Astra or Nemotron requests. UI explicitly identifies
  the inherited Astra review; new combinations were not freshly web-reviewed.
- Verification: full suite 1,330 passed, then 62 focused tests after the final
  construction/fixture-reuse fixes; affected Ruff checks and 162 frontend
  assertions passed. Restarted dashboard PID10164 serves v6 catalogue; local
  and private HTTPS status expose revision 3. German TXT contains 26 blocks,
  byte-identical locally/remotely. Audit and deployment checks are beside the
  final report in `reports/arthur/2026-10-17-club-872e5e473566/`.

## October 17 diverse tipster package — generated and frozen

- Final run `2026-10-17-1761eb70d61a`: COMPLETE, Astra COMPLETE, 27 nonempty
  recommendations from 30 profiles; three evidence-gated contrarians abstained.
  Freeze revision 2 was appended with snapshot hash
  `f9636fc8edc5f5469ed64c7d8da23a4b1337e42d01d6fd634b87105ad08edcdb`.
- The 20 club personas now keep distinct, versioned open-source-inspired styles.
  Published club tickets contain 1–3 legs; one dedicated Over 2.5 ticket is
  present. Fixture overlap is allowed, exact duplicates are removed, and the
  largest fixture exposure is 5/27 (18.5%).
- The protected DAILY_223 remains unchanged at SHA-256
  `d7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c`.
  Its October 17 ticket has three legs and product 15.4176.
- Every ticket uses a fixed EUR 1 paper stake. No actual wager was placed.
- Nemotron's bounded selector accepts only prevalidated positive-edge options,
  but the live NVIDIA endpoint timed out three times on this final run. The
  deterministic persona selections remained in place; transient failures are
  not cached and a later run can retry.
- All selected fixtures for this date are league matches. Competition context
  is retained; sourced motivation and rotation can become inputs. Weekday
  effects stay measurement-only until Arthur's own stratified data supports
  them. Research note: `docs/MATCH_CONTEXT_RESEARCH_2026_10_16.md`.
- Acceptance: 1,303 Python tests, affected-file Ruff, 104 tipster/accounting
  frontend assertions and 58 Nemotron frontend assertions passed. The private
  gateway's upstream timeout is now 30 seconds with a 45-second absolute cap,
  because the sourced October 17 status payload is 43 MB and takes about ten
  seconds to assemble. Dashboard and gateway scheduled tasks were restarted.
  Local/private status and the 3,260-byte German TXT return HTTP 200; the TXT
  is byte-identical and contains 27 blocks separated by blank lines.

## German Results-page ticket TXT — deployed 2026-10-16

- The Results page now has **Német TXT megnyitása**. It opens a date-specific
  UTF-8 plain-text file in a new tab through `/api/tickets.txt?date=YYYY-MM-DD`.
- The export matches the Results-page ticket set and omits empty abstentions.
  Each ticket starts with the profile/tipster name, then has one match per line
  with only the German bet type. Tickets are separated by one blank line. Odds,
  status, stake, result and explanatory text are excluded.
- The October 16 live export contains 27 ticket blocks / 170 lines / 5,304
  bytes. Local and private Tailscale responses are byte-identical (SHA-256
  `8c8e63aec818686c89ea45ed7dbc622df45ed8cfe244cdccada564453e81104c`),
  return HTTP 200 and use an inline `.txt` filename.
- The duplicate dashboard processes were stopped and the registered
  `Arthur-Dashboard` task restarted as the single loopback listener. Acceptance:
  80 Python endpoint/gateway tests, Ruff, JavaScript syntax and 198 frontend
  assertions passed.

## October 16 expanded-league regeneration and freeze revision 3 — complete

- Final run **`2026-10-16-8e3d1eb4acf2`** used all 25 default leagues and
  found 19 valid fixtures. NL2, DE3, IT2 and FR2 were active; the conditional
  Serie C reserve was not needed because the calendar exceeded the 15-fixture
  threshold.
- Two main tickets are **READY**. DAILY_223 remains the protected three-leg
  2×2×3 construction: FC Eindhoven–Vitesse away **2.00**, Kiel–Hertha away
  **2.30**, Grenoble–Boulogne draw **3.00**, theoretical product **13.80** at
  BetVictor/API-Football 36. Ritmusőr: Frankfurt–Köln BTTS yes **1.32** at
  1xBet/API-Football 11.
- Astra is COMPLETE / COMPLETE_WITH_SOURCES with 31 candidate reviews. Every
  final main-ticket leg has a candidate-specific review. A bounded follow-up
  review now closes the case where Astra caution changes the final DAILY quote
  or product after the initial 30-candidate batch. The protected DAILY role
  solver, odds bands and tolerance logic were not changed; its core file still
  matches baseline SHA-256
  `d7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c`.
- Review packet ordering now reserves selected main legs first and includes
  equivalent DAILY bookmaker quotes before broad profile alternatives. It no
  longer lets 30 broad profile suggestions starve selected-ticket review.
- Final validation passed: report hash, source review coverage, 15-minute
  publication lockout, exact 2×2×3 shape/product, at-most-50% READY fixture
  exposure, 27 nonempty profile recommendations, and no-wager flag.
- **Freeze revision 3** was appended at 17:40 Berlin for the final run with 27
  recommendations. Snapshot hash
  `15167e9a26f09a61abf2c85a98d2fb688c17dd0194d1821e5914abc82f3356ae`;
  parent revision 2 hash remains
  `aa12a7b1e79e0d73808d931979d6a8c1cac702180e91d148991838176b487397`.
  No wager was placed or marked.
- Targeted Python checks: 34 portfolio/review-flow tests and 75 private-gateway
  tests passed; affected files are Ruff clean. The private gateway response cap
  is now 64 MiB because the fully sourced status payload grew to 36.7 MB. The
  scheduled gateway was restarted; private root and status both return HTTP 200
  and expose the exact final run and revision-3 hash.
- Evidence: `analysis/regenerate-expanded-20261016/final-validation.json`.
  Report: `reports/arthur/2026-10-16-8e3d1eb4acf2/report.md`.

## Expanded default leagues and sparse-day Serie C — deployed 2026-10-16

- Owner authorized four permanent defaults: **NL2 Eerste Divisie (89), DE3
  3. Liga (80), IT2 Serie B (136), FR2 Ligue 2 (62)**. Both the active and
  installation-example configuration now contain 25 competitions. Existing
  settings, bookmaker preferences, seasons, credentials and API allowances
  remain unchanged (60 football calls, 40 Odds API credits per generation).
- Automatic Arthur scope adds available **Serie C Girone A/B/C (138/942/943)**
  only below **15 unique, valid NS fixtures strictly beyond the 15-minute
  lockout** in the default calendar. Invalid, duplicated, wrong-day, started
  and locked fixtures do not fill the threshold. Provider seasons determine
  history routing. Conflicting seasons are excluded; 40-league maximum holds.
- `jobs/league_scope.py` consumes the already fetched date-wide calendar;
  it adds no calendar request. Explicit manual league selection is authoritative.
  Dashboard initial/default selection follows `default_selected`; conditional
  Serie C entries are marked as reserve competitions. Manual options remain.
- Confirmed official Odds API keys for DE3/IT2/FR2; NL2 and Serie C use existing
  API-Football recovery via internal `soccer_arthur_<id>` routing, without
  invented external sport keys. Existing partial-history, quote/source checks,
  request budgets and publication lockouts continue to apply.
- **DAILY rules unchanged:** all six selection, scoring, normalization and odds
  core files match their original hashes. The shared collection coordinator
  has only an optional scope hook in its nonlegacy automatic branch; independent
  DAILY and explicit manual collection bypass it. The entire DAILY ticket and
  recommendation reproduce exactly on the same saved input.
- Cached October 16 calendar: old defaults **8 fixtures → new defaults 19**;
  four new leagues contribute 11. Serie C is unnecessary today under the new
  default threshold. These counts are calendar coverage, not qualified tips.
  **No generation, freeze revision or wager occurred. Every ledger row remains
  identical**, including today's revision 2 / 27 recommendations and hash
  `aa12a7b1e79e0d73808d931979d6a8c1cac702180e91d148991838176b487397`.
- Acceptance: **275 selected Python test executions passed**, including the
  38 new scope/helper/integration cases (one existing unpriced group was
  repeated in that invocation); affected source/test Ruff clean; JS syntax and
  **198 frontend assertions** passed. `run-arthur.py --check-config` confirms
  25 competitions with no provider/model request. Independent review found no
  blocking issue. This is focused verification, not a new full-suite run.
- Runtime refresh found both a manual dashboard PID11816 and an older hidden
  PID15372. Both verified old listeners were stopped; the port was verified
  free, and existing `Arthur-Dashboard` starts the new single loopback listener
  **PID8092**. Live authenticated calendar lists all four as defaults. Local
  and private HTTPS8443 status return HTTP200, retain today's exact revision,
  and confirm saved sports credentials loaded. Project2030 HTTPS443 stays 200.
  Calendar endpoint checks reused the cache during verification retries.
- Backup: `backups/league-scope-20261016T142431Z/`. Operational evidence and
  offline DAILY/ledger preservation proof: `analysis/league-scope-20261016/`.

## October 16 freshly regenerated and frozen revision 2 — complete

- Owner explicitly requested regeneration with the new rules and recording.
  Fresh run **`2026-10-16-b9c2862f6004`**, 16:06:07–16:13:13 Berlin:
  COMPLETE, new `arthur-soft-ticket-similarity-v2`, **27 published profile
  suggestions, two READY main tickets**, 17 club suggestions. Saved at 16:13:49.
- Main DAILY_223: Heerenveen–Excelsior over 3.5 **2.10**, Kiel–Hertha away
  **2.49**, Frankfurt–Köln draw **4.30**, product **22.4847**, Pinnacle4.
  These are the SAME three economic selections as the ORIGINAL frozen nightly
  package; prices/bookmaker refreshed. The intervening fcf run had different
  selections. Nyitány: Cottbus–Nürnberg H1 over 0.5 **1.15**, 1xbet11.
  Earliest kickoff 18:30 Berlin. Four main legs reviewed; zero main overlap,
  50% final READY exposure cap satisfied without singleton exception.
- Across all research/alternative profiles: **102 legs on six fixtures**,
  lengths 1–6; maximum shared fixture 24/27 profiles (88.89%). This remains
  concentrated research, despite zero EXACT economic ticket duplicates; do
  not describe 27 suggestions as independent bets or all as READY main tickets.
  Statuses: 4 REVIEWED, 3 DRAFT, 3 MODEL_READY, 17 READY, 3 ABSTAINED.
  All three contrarian personas honestly abstain without qualifying evidence.
- Astra COMPLETE, COMPLETE_WITH_SOURCES research: four web searches, seven
  accepted sources, 30 final candidate reviews. Contexts: 7 SOURCED,
  3 PROVISIONAL, 32 UNKNOWN; this is not complete lineup verification.
  Generation used 22 football calls, 13 Odds API calls; last Odds API balance
  22 credits. Monitor's configured generation reserve must remain in force.
- **Freeze revision 2 saved 16:16:28 Berlin**, 27 recommendations, run above;
  hash `aa12a7b1e79e0d73808d931979d6a8c1cac702180e91d148991838176b487397`.
  Parent/original `2e821fbd1e52bbf48cf04ed079012f54afe851ad8d733dd9d857ba682349e7b5`
  and original `nightly-2026-10-16-a1` row remain unchanged. The explicit
  prospective correction API appended the revision; no ordinary freeze overwrite.
- Independent SAME-fresh-input verification: full DAILY ticket and card match
  both current and provenance-anchored old reference algorithms. Saved objects
  reproduce exactly with their actual processing timestamps and annotations.
  All seven DAILY core hashes AND all pre-operation strategy files unchanged.
- Preservation audit: every previous immutable ledger row retained (only
  permitted active flags excluded); runs 61→62, tickets 48→50, recommendations
  421→448, original freezes 16→16, revision rows 4→5. Actual wagers **4→4**,
  exact original rows; no October 16 wager and no automatic wager placement.
- Local AND private HTTPS `/api/status?date=2026-10-16` return HTTP200 and
  the exact new run/revision-2 hash/27 frozen recommendations; runtime idle.
  Existing dashboard is current; no restart, source, settings or credential
  changes were needed for this operation.
- Eight consistent SQLite backups: `backups/regenerate-20261016-20261016T140553Z/`.
  Operational evidence: `analysis/regenerate-20261016/` (baseline, DAILY and
  publication proof, freeze view, full preservation proof). Export:
  [October 16 report](reports/arthur/2026-10-16-b9c2862f6004/report.md).

## Soft ticket policy and independent odds monitor deployed — 2026-10-16

- **Non-DAILY selection:** fixed-scale unit normalization, mean support × sqrt
  distinct legs, bounded multi-leg alternatives and singles. Exact economic
  ticket duplicates try another alternative; shared fixtures are allowed.
  More than half of the candidate's fixtures overlapping a higher-ranked
  selected ticket applies one 0.70 factor. True fixture/selection Jaccard is
  reported separately. Old global fixture exclusivity and 90% retention removed.
- **Risk:** the final READY main package limits a fixture to floor(N × 0.50)
  tickets, minimum one. The mathematically unavoidable single-READY exception
  is explicit. Paper/research/DRAFT cards and aliases cannot dilute the denominator.
  Only READY non-DAILY options enter the main package; stronger unreviewed
  combinations remain research. Protected DAILY objects retain their own state.
- **Contrarian:** three existing personas (Érték Milán, Remi Doma, Merész Hunor)
  require comparable 24–48h quote history, at least 10% rise (declared heuristic),
  valid GAUSS/LAPLACE support, strictly p × price > 1.15, and actual structured
  Astra price/math explanations. Missing history or explanations means abstention.
  Price movement alone never establishes retail-money direction or its cause.
- **Veto override:** named HIGH_ODDS/LEAGUE_BASE_RATE soft concerns only, bound
  to the exact candidate/current quote, a COMPLETE supporting review and retrieved
  cited sources. No free-standing boolean authority or invented probability.
  Source/identity/time/minimum-sample/model/contrarian/exposure/team/DAILY hard
  gates remain. A specific above-band club leg can be an approved singleton;
  the exception does not authorize an unlimited product of odds.
- **Timing:** existing native `Arthur Goal Analysis Daily` now verified **PT1M**
  for cheap local checks; provider queries use a separate persistent **1800s**
  successful-fetch cache. No Astra or ticket generation from odds polling.
  Prospective publications and price observations lock **15 minutes** before
  kickoff, before and after slow work. These wrappers do not alter DAILY core.
  Minute/day budgets, remaining quota, a generation reserve, persistent single
  flight and 429/backoff apply. An unexpected real provider cost is charged
  and halts that provider across fresh processes. Existing task XML backed up
  by the checked registration script under `data/arthur/schedule-backups/`.
- **Storage:** only the new monitor's own append-only quote snapshots and
  throttle/cache state use `data/arthur/odds-monitor.sqlite3`. Generation reads
  those observations as-of its cutoff. Existing saved reports, freezes, stakes
  and credential files were not rewritten by this change. No new generation
  or freeze was manually launched. Already saved October 16 runs still carry
  the old policy; the new selector applies to subsequent generation, not a
  retrospective rewrite of those packages.
- **DAILY proof:** complete same-input ticket and recommendation equality and
  seven unchanged protected core hashes, rechecked after all changes. Evidence:
  `analysis/soft-overlap-20261015/protected-daily-verification.json`.
- **Same-input offline comparison:** original 27 → strict 2 → new **16 profile
  suggestions**, ten club profiles, four fixtures, 1–4 legs. Only protected DAILY
  is a READY main ticket on that saved input; remaining suggestions retain
  honest research/DRAFT/model status. Three contrarians abstain without the
  required history and Astra narrative. This is not a fresh market forecast.
  Evidence: `analysis/soft-overlap-20261015/saved-input-replay.json`.
- **Acceptance:** **1,255 Python tests passed**, 142.80s, 32 upstream NumPy
  deprecation warnings; affected source Ruff clean; JS syntax and **192 frontend
  assertions** passed (98 club/paper, 36 team, 58 Nemotron). Full log:
  `analysis/soft-overlap-20261015/pytest-accepted.txt`. Windows DPAPI tests used
  isolated synthetic credentials; the test subprocess inherited only Windows
  PowerShell module paths, avoiding unrelated PS7/PS5 module-autoload conflicts.
  Unrelated legacy repository-wide lint findings were not changed.
- **Loaded runtime:** verified old owned dashboard PID19036 stopped after idle
  check; existing `Arthur-Dashboard` task starts one loopback listener PID15372.
  Local/private HTTPS Arthur roots HTTP200; new backend exposes three contrarian
  personas, read-only monitor state and both existing sports credentials.
  Gateway PID19668 and Project2030 HTTPS443 HTTP200 preserved. No new physical
  phone test or Windows reboot performed. Evidence:
  `analysis/soft-overlap-20261015/deployment-check.json`.
- [Code guide and excerpts](docs/SOFT_TICKET_POLICY_2026_10_15.md),
  [odds monitor details](docs/ODDS_MONITOR.md). Pre-edit source copies:
  `backups/soft-overlap-20261015/`.

## Owner clarification and Gemini review brief — 2026-10-15

- The owner rejected the last overlap policy as too restrictive. Individual
  tickets may share matches; repeated near-identical ticket constructions are
  the concern. Global fixture exclusivity and the 90% retention threshold were
  implementation choices, not explicit owner requirements. The protected
  DAILY_223 method remains unchanged.
- This follow-up is documentation only: no operational policy, generation,
  freeze, settings or data changes. The deployed strict policy described below
  is still the observed implementation, pending review. The 27-to-2 decline
  concerns profile suggestions; READY main tickets were two in both runs.
- Shareable Hungarian handoff: [Gemini review brief](docs/GEMINI_REVIEW_BRIEF_2026_10_15.md).
  It also flags singleton dominance in the preceding evidence-first ranking
  and asks for graded overlap controls without forcing weak filler tickets.

## Minimal-overlap publication deployed — 2026-10-15

- **Policy:** at most one non-DAILY profile per fixture globally. Prefer distinct
  main tickets; allow only one shared DAILY fixture, only in a LIVE main ticket.
  Paper profiles cannot use the exception. Alternatives retain at least 90% of
  their own original best score, before exclusions; this is a heuristic score
  ratio, not a probability. Original gates remain; unsupported duplicates are
  explicit ABSTAINED. Failed reports cannot withdraw earlier valid evidence.
- **DAILY unchanged:** complete ticket/recommendation equality on both the old
  5669 and fresh d4c4 inputs; seven original DAILY pipeline hashes unchanged.
  Fresh data changed the daily first pick and prices; the old unchanged
  algorithm gives exactly the same fresh choices. Settings/stake/roster,
  numerical fits, team state and credentials remain unchanged.
- **Fresh run `2026-10-15-d4c4dde8cb97`, 18:03:01–18:07:12 Berlin:** COMPLETE,
  two READY recommendations, four reviewed legs. DAILY: Brann–Lincoln BTTS-yes
  2.12, FTC–Plzeň BTTS-no 2.30, Dinamo–Anderlecht away 3.70; product 18.0412.
  Nyitány: FTC–Plzeň H1 over 0.5 at 1.36. All kickoffs 21:00 Berlin.
  Published club tips **18→0**; 27 ABSTAINED/one KEPLER insufficient-history
  card. No duplicate fixture among non-DAILY profiles; only DAILY/H1 share FTC.
  Active ledger and private phone view contain only the new two recommendations.
- **Recorded 18:08:16, revision 5**, two recommendations, snapshot hash
  `c14b714263703e50d8561dcb686e40c7328a0299863434e5e2ed86a7bcf6e108`.
  All original rows across six stores preserved except allowed active flags;
  actual wagers still four. Original/r2/r3/r4/r5 learning cutoffs select
  24/19/19/27/2 records, 15 effective packages/zero invalid records.
  Backups: `backups/minimal-overlap-20261015/` (13 pre-edit files/six stores,
  plus the wrapper original byte-verified against the immutable 5669 manifest).
- **Deployment:** two verified old Arthur instances (6476 and manual 23972)
  stopped; existing login task runs one listener PID19036, verified with netstat.
  Local/private HTTPS8443 root/status 200; matching 16,629,854-byte bodies show
  revision 5/two READY/zero club tips, new UI and loaded keys. Gateway19668 and
  Project2030 PID11800/HTTPS443 200 preserved; Tailscale Automatic/Running.
  URL unchanged: `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`.
  No Windows reboot or new physical phone-browser test was performed.
- **Limits and acceptance:** four completed web searches, 61 returned URLs/five
  accepted sources; 18 contexts: three sourced/three provisional/12 unknown.
  This is not complete lineup verification or evidence of calibrated profit.
  The single FTC overlap remains dependent. 1,103 Python tests passed in 96.61s,
  32 upstream warnings; Ruff, JavaScript syntax, 92 frontend assertions and
  independent review passed, including 500 small matching-graph checks.
  [Details](docs/MINIMAL_OVERLAP_2026_10_15.md) and observed artifacts:
  `analysis/minimal-overlap-20261015/`.

## Evidence-first non-DAILY selection deployed — 2026-10-15

- **Owner policy:** no minimum combined odds or forced leg count outside
  DAILY_223. Base specialists, all twenty club personas, numeric recommendation
  selection and the emergency fallback use complete evidence-first singleton
  scans. Existing identity/price/history/H1/quality and upper-odds gates remain;
  unsupported research and emergency fallback stay DRAFT/LOW. Numeric fits and
  probability/edge/stability thresholds remain unchanged. UI says no minimum.
  Legacy global `target_min_odds: 10` is retained only for snapshot compatibility;
  non-DAILY selection explicitly records it as unapplied.
- **DAILY unchanged:** entire same-input ticket and recommendation equal the
  pre-change baseline, including IDs; seven core hashes match the original
  frozen run. Settings, stake, coordination and credentials remain unchanged.
- **Fresh run `2026-10-15-5669f0838071`:** 07:02:23–07:07:13 Europe/Berlin,
  **COMPLETE, two READY tickets**: original DAILY_223 choices at **18.54375**
  and Nyitány **1.35**, Ferencváros–Plzeň first-half over 0.5 single. All four
  selected legs reviewed. Further qualified specialist alternatives are
  Krónikás 1.67, Ritmusőr 1.57 and Őrszem 2.25. No quality gate was lowered.
- **Evidence limits:** four completed web searches/93 returned URLs/seven accepted
  sources; thirty non-weather contexts: 1 sourced/2 provisional/27 unknown;
  six weather contexts unknown. This is working web access with incomplete
  fixture context, not complete lineup verification. There are 27 published
  profile entries/three without legs, 33 legs/six fixtures; numeric coverage
  1/0/1. All eighteen published club singles share FTC–Plzeň and remain paper
  alternatives, not eighteen independent opportunities. Optional Nemotron
  ticket audit is UNAVAILABLE. Lower odds do not establish calibrated profit.
- **Recorded 07:07:57: revision 4**, 27 recommendations, hash
  `8d97d8943b5b0f0d4236e426a1e67264a0cb440d565403c4e9cce42f28ddd4ef`.
  Original and revisions 2/3 retained; every original row across all six backups
  preserved except allowed active flags. One run/two tickets/27 recommendations
  added; actual wagers still four. Original, r2, r3 and r4 learning cutoffs all
  select their own immutable packages, with 15 effective packages/zero invalid
  records. Backups: `backups/evidence-first-20261015/`.
- **Deployed and checked:** existing login task running, server PID 6476;
  localhost and private HTTPS8443 root/status 200, 16,975,988-byte status bodies
  show revision 4/27 and two READY tickets, new catalogue/UI and loaded keys.
  Gateway PID 19668 and Project 2030 PID 11800/HTTPS443 200 preserved; Tailscale
  Automatic/Running. URL: `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`.
  No full reboot or new physical phone-browser test was performed.
- **Acceptance:** 1,072 Python tests passed in 112.94 s, 32 upstream warnings;
  Ruff, JavaScript syntax, 88 frontend assertions and independent final review
  passed. [Details](docs/EVIDENCE_FIRST_2026_10_15.md) and observed artifacts:
  `analysis/evidence-first-20261015/`.

## DAILY_223 readiness and live web research repaired — 2026-10-15

- **Verified causes and bounded repair:** DAILY_223 inherited the generic
  specialist score floor, removing all qualifying third-slot prices despite
  real history-supported 2/2/3 products. Its adapter now uses its independently
  validated pool before that floor; existing ranking, core solver and seven
  DAILY_223 pipeline files are unchanged. A valid construction survives partial
  optional review; an unconstructed fallback is `TARGET_PROPOSAL`/LOW. Global
  strictness and specialist targets remain unchanged. Research now enables its
  required code-mode host while supplied-data review keeps it disabled; only
  completed searches and actual returned URLs can establish cited web evidence.
- **Fresh run `2026-10-15-cb385563a836`:** 06:30:17–06:35:43 Europe/Berlin;
  one **READY DAILY_223**, all three legs reviewed, one Betano32 price source:
  Benfica–Celtic over 3.5 **2.15**, Ferencváros–Plzeň BTTS-no **2.30**, Dinamo
  Zagreb–Anderlecht away **3.75**; product **18.54375**, all kickoffs 21:00 Berlin.
  Target two gives honest overall **PARTIAL**: specialist upper bounds
  **4.42/8.25/3.05** remain below their minimum 10, with no search bug found.
  No threshold was loosened to manufacture a second ticket.
- **Evidence limits:** four completed searches, 105 returned URLs, eight accepted
  web sources; among 35 non-weather contexts, 10 sourced/four provisional/21
  unknown. Full lineup verification is not claimed. There are 19 published
  profiles/11 without legs, 60 legs/eight fixtures, 75% of legs on four fixtures;
  model coverage remains 1/0/1. Optional new Nemotron ticket audit is UNAVAILABLE.
- **Recorded at 06:38:29:** effective revision 3, 19 recommendations, hash
  `8fbfdf542cb8e2627ead2152014c0701e95c7b9cc29ea7c33c741d66d89aceb3`.
  Original and revision 2 remain intact. Comparison across all six backed-up
  stores preserves every original row except allowed active flags; one run,
  one ticket and 19 recommendations added, actual wagers still four. Backups:
  `backups/readiness-repair-20261015/`; settings, secrets, team and nightly state
  preserved. No wager was placed.
- **Verified deployment:** hidden login task started updated server PID 25080;
  localhost and private HTTPS8443 root/status 200; both status bodies contain
  15,136,647 bytes and matching READY DAILY_223, revision 3/19 and idle fields with
  credentials loaded. Project 2030 HTTPS443 stays 200; gateway PID 19668 preserved.
  Phone URL remains `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`.
  No full reboot or new physical phone-browser test was performed.
- **Acceptance:** 1,049 Python tests passed in 107.97s, 32 upstream warnings;
  Ruff, JavaScript syntax, 85 frontend assertions and independent DAILY/web
  checks passed. Detail and observed artifacts:
  [readiness repair](docs/READINESS_REPAIR_2026_10_15.md).

## Authorized strategy review and Oct 15 corrected package — 2026-10-15

- **Verified issue:** Oct 14's 107 leg occurrences include 100 on four fixtures;
  eleven personas share the same four-fixture set. Partial optional review
  coverage narrowed specialist candidates, model-promising personas allowed
  history fallbacks, and one sparse historical team vetoed whole numerical
  competitions. One settled sample per new persona does not support tuning
  performance weights or claiming a profitable edge.
- **Bounded correction:** preserve valid non-DAILY specialist proposals with
  explicit missing-review warnings; enforce declared persona market/model
  eligibility; abstain when unsupported. Numerical fits use an iterative
  four-appearance team core per original window, retaining all original 80/120
  competition, target-team-eight, connectivity, cutoff/closure and validation
  gates. Record actual coverage/overlap; no forced diversity or outcome tuning.
  DAILY_223's seven source files and complete same-input selection output are
  unchanged. Offline GAUSS/KEPLER/LAPLACE coverage is 4/4/4→6/4/6 for Oct 14
  and 0/0/0→1/0/1 for Oct 15; healthy-cohort probabilities and hashes match.
- **Fresh run:** `2026-10-15-3b9678e368c3`, observed **00:01:08**, completed
  **00:06:18 Europe/Berlin**. There are **19 published profile recommendations,
  11 entries without legs, 60 legs and eight fixtures**; the top four fixtures
  account for 75% of legs and one fixture is in all nineteen recommendations.
  Models cover **1/0/1** fixtures. Concentration and shared evidence remain;
  these are preliminary research recommendations, **zero constructed playable
  portfolio tickets**, status `CONSTRUCTION_INCOMPLETE`. DAILY_223 is a labelled
  target proposal whose 2/2/3 conditions are not verified in this fresh run.
- **Evidence limits:** Astra supplied-data review is `COMPLETE`, external
  research `UNAVAILABLE`, with zero observed web-search calls. The new Nemotron
  ticket audit is `UNAVAILABLE`. Earlier saved Nemotron learning was complete;
  the two additional manual software audits were output-truncated **partial**
  reviews (known 891 input / 1,536 output tokens), without automatic retry.
- **Recorded prospectively:** appended effective **revision 2 at 00:07:04**,
  hash `d5e27bd79f25ee461aca9dc88bc218ce3e22a74d6dcac0f304a57e1022f51d71`.
  Original Oct 15 nightly freeze/hash remain intact. Revisions are explicit,
  pre-kickoff, hash-chained, append-only and blocked on wagered days; current
  views select one effective package/day, learning selects as of its cutoff.
  Read-only comparison preserves original contents of 55 runs, 15 freezes,
  four wager rows, 36 tickets and 340 recommendations, apart from allowed
  prospective active flags. Added one run and nineteen recommendations; no
  wager or ticket row added. Sources and six consistent SQLite backups are
  retained under `backups/strategy-review-20261014/`; settings, secrets, team
  membership and the completed nightly job are preserved.
- **Verification/deployment:** **1,023 Python tests passed in 112.83s**, with
  32 upstream deprecation warnings; initial three environment failures were
  resolved by process-only UTF-8/native PowerShell module-path settings.
  Ruff, JS syntax and 85 tipster/paper-ledger, 36 team, 58 Nemotron frontend
  assertions passed. `Arthur-Dashboard` cold-started the updated loopback8765
  runtime at **00:07:54**, PID21348, with existing keys loaded. Localhost and
  private HTTPS8443 root/status are200, both status bodies12,965,493 bytes,
  showing the new run, revision2/19 recommendations and idle state. Project2030
  HTTPS443 remains200; gatewayPID19668 is preserved. A full machine reboot and
  a new phone-browser test were not performed.
- Detail and observed artifacts: [review and acceptance](docs/REVIEW_2026_10_14.md).
  Phone URL: `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`.

## Dashboard starts after Windows login — 2026-10-14

- **Configured and read back at 23:37 Europe/Berlin:** new `Arthur-Dashboard`
  Windows task starts ten seconds after HP login, Interactive/Limited, hidden,
  with no execution time limit, IgnoreNew, and three one-minute restart attempts.
  It runs the new `scripts/start-arthur-on-login.ps1` guard, which delegates to
  the existing `start-arthur.ps1 -NoBrowser` launcher. Existing saved keys are
  read through the existing loader, never rewritten or logged.
- **Duplicate protection:** listener enumeration and an Arthur HTTP identity
  check precede any secret load or dashboard initialization. A named startup
  mutex prevents concurrent starts; an occupied unknown/unhealthy port fails
  closed without stopping any process. Logs contain only fixed event codes,
  UTC timestamps and exit numbers; child output/exception details are suppressed.
- **Direct Windows verification:** manual invocation of the registered task
  completed with exit0 and `already_running`; original dashboard PID6860 and
  gateway PID19668 are preserved. HTTPS root and status on8443 and Project2030
  on443 still return200 with normal TLS verification.
- **Isolated cold-start verification:** 15 disposable-root Windows PowerShell5.1
  tests passed, independently rerun; parser check passed. Coverage includes the
  production no-argument `-File` invocation, missing launcher, unknown/broad/
  unhealthy listener, no duplicate child, NoBrowser, child failures/exit codes,
  and suppression of synthetic secrets on every output stream. No production
  ArthurState constructor or second live server was invoked for testing.
- **Evidence boundary:** the task's real Windows registration and idempotent
  live execution are verified; a complete machine reboot/login was not performed.
  After reboot, HP must log in and the computer must stay awake/online. Tailscale
  remains Automatic; the existing login gateway and background Serve mapping
  provide the same phone URL: `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`.
- The daily-analysis schedule, Project2030 tasks, Tailscale gateway definition,
  application/strategy source, settings, saved credentials and business data
  were preserved. Operation: [Arthur Tailscale access](docs/ARTHUR_TAILSCALE.md).

## Private Tailscale dashboard access — 2026-10-14

- **Verified Windows runtime, 18:35 Europe/Berlin:** the phone URL is
  `https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/`. The existing Project 2030
  HTTPS443 proxy to loopback8001 is preserved. Arthur uses background Tailscale
  Serve on HTTPS8443, then a separate loopback8770 transport, then the original
  loopback8765 dashboard. Original Arthur PID6860 was not restarted or rebound.
- **Access boundary:** the new standalone gateway requires the exact HTTPS
  Host, Tailscale-verified owner login and matching Origin for POST. Only the
  validated Origin is translated to loopback; the existing Arthur session
  token and backend checks remain intact. Wrong identities/origins/tokens fail
  closed. No Funnel, public port forwarding, new credentials or firewall rule
  was enabled. All Windows firewall profiles remain enabled with inbound Block.
- **Persistence:** `Arthur-Tailscale-Gateway` runs as HP with Interactive/Limited
  login, directly through windowless Python, with no execution time limit and
  three one-minute restart attempts. It only runs the transport, never Arthur
  analysis or its secret loader. The separate `Arthur-Dashboard` login task
  above now starts the dashboard through its existing launcher; the computer
  must be awake, online and HP logged in.
- **Validation:** 75 isolated network/security tests and Ruff passed; independent
  review found no material bypass. All 23 live HTTP checks passed: verified TLS,
  root/assets/status, identical static assets, original localhost and Project
  2030 endpoints, authentication failures and a token-authenticated non-existent
  POST returning404 without business actions. The live status response is
  12,280,960 bytes; a realistic large-response regression covers the 32MiB cap.
- **Phone evidence:** the existing Android S24+ peer is online and responds to
  Tailscale ping; the current effective ACL allows its TCP8443 access. The owner
  subsequently confirmed successful phone browser loading at the exact URL
  above ("Betöltött"). This is user-confirmed phone access, in addition to the
  direct Windows HTTPS checks; cellular-network access was not separately tested.
- **Preservation:** the 1,226 existing protected source/config/data files matched
  their pre-change SHA256 hashes after live verification. No business action,
  bet, credential, model request or data migration was initiated. Existing
  unrelated working-tree changes were preserved; no commit/push was made.
- Operation and rollback: [Arthur Tailscale access](docs/ARTHUR_TAILSCALE.md).

## Nemotron advisory addition — 2026-10-12

The owner requested NVIDIA Nemotron assistance wherever useful. The existing
encrypted Windows credential and shared helper are used; the key pasted into
chat was not copied to source, configuration, logs or prompts. Rotation of the
exposed credential is recommended. No subscription or dependency was added.

Local `config/nemotron.json` enables the extension. New manual runs receive up
to two parallel risk/data-quality reviews after persistence; nightly runs do so
after archive/freeze, then one research-learning review per saved learning day.
Each worker is bounded to 768 output tokens and 30 seconds, thinking off, no
automatic retries. Existing evidence-linked proposals are ranked, not new odds
or predictions. This is advisory evaluation, not model-weight training. It does
not alter the Astra workflow, strategies, team membership, stakes or wagers.

Claims and results are immutable in `data/arthur/nemotron.sqlite3`, separate from
the original report/learning stores. Read-only `GET /api/nemotron` powers a new
**Működés → Nemotron** panel and a Tipster klub pointer. Refreshing/polling never
calls a model. Actual-money financial details are excluded from NVIDIA inputs.
The fixed local UTF-8 bootstrap uses the existing launchers' process-scoped
PowerShell policy; machine/user policies and global helpers are unchanged. Its
hash is included in advisory metadata and new strategy source manifests.

Live verification at 19:12 Berlin: a review of the existing October 11 learning
snapshot completed successfully, selecting four verified proposals. Provider
usage: **10,335 input / 262 output tokens, 1 request**. The earlier 19:07 audit
attempt for run `2026-10-12-eda206cd219d` failed at helper startup; its original
UNAVAILABLE result and two unknown-usage reservations remain recorded. It was
not retried or relabelled successful. The startup and UTF-8 issues were fixed
and separately checked without API calls. Cached replay was verified to create
no new requests or records. No fresh sports/Astra generation was triggered.

Verification: **870 Python tests passed**, 12 pre-existing numerical-library
deprecation warnings; **58 Nemotron + 85 club/accounting + 36 team frontend
assertions passed**. Changed Python files pass Ruff and JavaScript syntax is
valid. Independent review covered privacy, claim/retry boundaries and output
validation. All **52 run rows, 12 freezes and 4 actual wagers** preserve their
complete pre-change row-content hashes.

Dashboard QA used a temporary local port-8766 process: both the failed run audit
and successful learning audit, four expandable proposals, token completeness,
refresh without closing details, no horizontal overflow at the natural 1265px
viewport and no console errors were verified. Screenshot:
`reports/arthur-nemotron-20261012.png`. The normal port-8765 process still returns
404 for the new endpoint. Its restart was blocked by the tool environment;
owner restart through the existing Arthur shortcut is required. Source updates
are complete, but do not call the normal dashboard deployment verified yet.
The temporary QA tab was closed and its owned server process stopped; port 8766
has no remaining listener. The original port-8765 application was preserved.

Native task readback: Ready, 15-minute and login triggers enabled; October 12
19:00 pass exit 0. The next business-time generation remains 22:00 Berlin for
October 13. Its first complete run with the advisory extension is still an
operational follow-up. Details: [Nemotron operation](docs/NEMOTRON_ADVISORY.md).

## First live Tipster klub generation — 2026-10-12

The owner explicitly approved a one-off current-day run after the blank club
was traced to the October 11 nightly package predating the twenty entrants.
The normal port-8765 dashboard started one analysis at 17:37 Berlin; run
`2026-10-12-eda206cd219d` finished at 17:42:33 with exit 0. All thirty enabled
profiles participated. Sixteen new persona tickets (56 legs) and four explicit
abstentions were persisted. Konszenzus Vera, Érték Milán, Fűző Oszkár and Stabil
Irma found no compliant combination; their decisions remain in the run report.

All sixteen persona tickets enter the EUR 1 paper ledger with original quotes,
reasoning and strategy versions: EUR 16 pending exposure, EUR 0 returned, no
settled wins/losses yet. The day's complete canonical log has 26 recommendations,
19 financially eligible and 7 excluded. The existing 51 report rows, 12 freezes
and 4 actual wagers retain their original complete row-content hashes. The
October 12 frozen ten-profile package was not replaced. No real wager was placed.

Astra completed through two shared requests; its web-background check remained
incomplete and is disclosed in the report (zero observed web-search calls).
No extra per-persona LLM requests were added. Browser verification confirmed
16 saved combinations / 4 abstentions and original match odds on the live club.
Next normal generation remains October 12 at 22:00 Berlin, for October 13.

## Latest change — 2026-10-11 twenty tipster personalities

Implemented in the existing Windows source root, `C:\AI-Work\GoalAnalysis`.
The ten existing profiles remain, with twenty new fictional personalities:
30 enabled profiles by default, not 30 guaranteed daily tickets or bets.

- Dedicated Tipster klub tab with twenty locally drawn portraits, character
  preferences, individual odds bands within 7–50, daily participation switches,
  date-specific archived recommendations/abstentions and separate paper/actual
  metrics. Missing history is not labelled a loss or an abstention.
- Owner's follow-up adds an explicit **EUR 1 per daily recommendation** paper
  ledger for the complete team, with monthly/all-time totals, original prices,
  returned money, closed net, pending exposure and cash balance. All logged
  tickets' assumed exposure is shown; only source-verified, single-bookmaker
  prices enter the financial subset. Excluded original rows remain visible, and
  incomplete coverage cannot be presented as a complete-team profit. It reads
  the existing immutable archive, never changes the four real wagers, and
  supports a read-only JSON export through `/api/paper-performance`.
- Shared existing fixture/history/quote inputs and one existing numerical-model
  batch; no twenty extra Astra calls, subscription, dependency or downloaded code.
  Personality preferences change market selection, evidence/model weighting and
  ticket length. They are not independent models or independently trained agents.
- Exact event/market/provider/bookmaker checks, distinct fixtures per ticket,
  one bookmaker/region/currency and a final pre-publication kickoff guard. A
  missing valid combination yields an explicit abstention, not fabricated picks.
  Quote timestamps remain informational under the owner's v3.6.1 rule.
- Settings registry v4 preserves disabled legacy profiles and frozen old job
  settings. New source/version manifests include the persona catalogue. Existing
  archive/freeze/result/learning pipelines accept the new profiles without
  rewriting past reports. Shared-pick overlap remains disclosed.
- Szent Grál now supervises 30 members; policy `grail-prospective-unit-v2-roster30`
  uses a 30-member multiple-comparison correction. Existing admission thresholds
  stay unchanged. Twenty new entrants were enrolled at 23:58:54 Berlin; there
  are 9 ACTIVE and 21 PROBATION members including Laplace. Repeat enrollment
  created zero records; old membership/evaluation/event records are unchanged.

Verification: **811 Python tests passed**, twelve existing upstream numerical
library deprecation warnings; **85 club/accounting + 36 existing frontend
assertions passed**. The EUR 1 calculator/HTTP/SQLite cases were additionally
independently reviewed. Changed Python files pass Ruff; JavaScript syntax is valid. Browser QA
verified search, unsaved toggle/reset, separate unknown paper/actual metrics,
archived date navigation and the 30-member team at 429px without horizontal
overflow or console errors. The 1 EUR view was checked at the natural desktop
viewport and the live 429px tab, including monthly/all-time switching, original
legs and 20-row paging. The temporary QA server was stopped; only the original
port-8765 application remains running.
Screenshots: `reports/arthur-v311-tipster-klub.png`,
`reports/arthur-v311-paper-ledger.png`.
Repository-wide whitespace checking still reports two unrelated old documentation
blank lines; those user changes were not rewritten.

All **51 run rows, 12 freeze rows and 4 actual-wager rows** retain their complete
row-content hashes from the start of this change. Source backup:
`backups/arthur-v3.11-start-20261011`. No live generation, provider request,
learning-model request or wager was triggered by this upgrade.

Deployment: port 8765 initially still reported v3.8, so the owner was asked to
restart Arthur. By October 12 00:28 Berlin, the normal port 8765 was verified as
v3.11 with the 20-character catalogue and the functional EUR 1 endpoint; its old
browser page was reloaded to load the new accounting UI. The source/runtime
deployment gap is resolved. Temporary port-8766 QA is stopped after verification.
The native timer loads the current source independently. The already completed
October 11 nightly job for October 12 retains its original ten-profile package;
it is not replayed or backfilled. Next new automatic slot: October 12, 22:00
Europe/Berlin, for October 13. First new-fixture production generation remains
an operational check. The last nightly pass left 20 fixture checks; a later
manual refresh reported zero remaining. Neither is a continuous queue estimate.
The existing 20-request nightly settlement cap was not increased.
The October 11 23:45 checker encountered a transient import gap during this
in-place source update; the next natural tick, October 12 00:00:01 Berlin,
returned **exit 0**. Native readback confirms the 15-minute trigger remains active.
See [v3.11 operation, tests and limits](docs/RELEASE_V3_11.md).

Read-only baseline for the new paper view: 109 original daily recommendations,
30 eligible priced tickets (26 closed, 4 pending); hypothetical EUR 30 committed,
EUR 2.62 returned, EUR -23.38 closed net, EUR -27.38 cash balance. The 79 excluded
rows include missing quotes or incompatible bookmaker combinations. These are
the OLD profiles' historical data, not results of the new twenty characters.
The whole-team financial result remains unknown at incomplete coverage.

## Previous release — 2026-10-07 A Szent Grál


The owner explicitly authorized live recommendation-team management, replacing
the prior advisory-only membership boundary. Implemented in the same project:

- New Szent Grál dashboard tab: live/probation/suspended roster, separate actual
  and prospective paper metrics, qualified-only podium, evidence-linked training
  plans, reversible audited owner controls and GitHub source-admission pipeline.
- Durable daily deterministic team evaluation and weekly bounded public GitHub
  scouting join the existing nightly learning stage; no extra AI subscription,
  automatic wagers, stake changes or arbitrary remote-code execution.
- Real publication enforcement, including suspension of the historically
  mandatory DAILY_223. Probation is archived but stays outside live tickets;
  suspension cannot be bypassed by generic fallback. Retry rosters are frozen.
- First new entrant LAPLACE: reviewed local independent Poisson baseline using
  the installed penaltyblog library. Starts on probation, not as a proven winner.
- Prospective decisions require current-version, original-price evidence and
  minimum 260 tickets / 90 publication days / 300 unique fixtures. Conservative
  day-cluster uncertainty estimates, maturity and cooldown gates are explicit.
  Existing historical records remain descriptive; actual profit is not inferred.
- New local stores under data/team. Old runs, frozen packages, settings and
  credentials remain in place. Source backup: backups/arthur-v3.10-start-20261007.

Verification on 2026-10-07:

- **693 Python tests passed**, including actual penaltyblog numerical fitting,
  prospective promotion/suspension, wrong-fixture/market price rejection,
  cutoff/version isolation, duplicate prevention, corrupted-store blocking,
  authenticated HTTP actions and complete nightly/learning/team integration.
  Twelve upstream penaltyblog/NumPy deprecation warnings remain, not failures.
- **36 reproducible frontend contract/safety assertions passed** using
  `node tests/team_frontend_check.cjs`; JavaScript syntax and changed-file Ruff
  checks are clean. Real-browser checks passed at the existing 388px viewport:
  no horizontal overflow or console errors, distinct actual/paper views,
  populated plans/source metadata and explicit owner-override warning.
- Initial real team evaluation saved at 23:00 Berlin:
  `team-2026-10-07-23e159e82bcab0b9`, hash
  `7dee199c8ed75da7408124e433e93481aae43d392f18f63612f8915d0ab669a3`.
  Nine members ACTIVE, LAPLACE PROBATION, no suspension/promotion. NO_DATA refers
  to the new prospective cohort, not missing old history: 87 research tickets
  remain measured; 52 plan entries include 12 verified learning proposals.
- Initial real GitHub scan COMPLETE: `github-scout-2026-10-05`, nine requests,
  four repository snapshots (one existing integration, three unreviewed sources),
  no download/install/model call. Repeating both commands kept the identical
  hashes, one evaluation, one scout snapshot and nine total request debits.
- All **43 run hashes and 10 freeze hashes are unchanged**, with zero actual
  wagers. Existing learning report/attempt counts remain one; no extra Astra
  request was made. Native checker remains enabled; 23:00 check returned 0.

Deployment limitation: port 8765 still reports backend v3.8; the environment
previously rejected stopping that user's process. Owner must close the original
Arthur server window and reopen the existing shortcut. The populated v3.10 UI
was verified on our temporary port-8766 server, not claimed as permanent rollout.
That QA server was stopped after verification; no second dashboard is left running.
Screenshot: `reports/arthur-v310-szent-gral.jpg`. The native scheduler loads the
updated files independently. First new-fixture production run and next natural
nightly team cycle remain operational checks; old completed jobs were not replayed.
See docs/RELEASE_V3_10.md. The v3.9 verification below is historical.

## Previous release — 2026-10-07 nightly automation and advisory learning

Implemented directly in `C:\AI-Work\GoalAnalysis`, preserving existing settings,
keys, SQLite history and manually frozen packages. Source backup:
`backups/arthur-v3.9-start-20261007` (no credentials or databases copied).

- The native `Arthur Goal Analysis Daily` task now checks every 15 minutes and
  on user logon. Python runs each target-date job only once: 22:00 Europe/Berlin,
  next calendar day's enabled profiles, catch-up strictly before 06:00. DST is
  handled by the Berlin clock, not the host's chosen time zone.
- Durable stages: result refresh, generation, archive/freeze, learning. Only
  incomplete retryable stages resume (three attempts, 15/30-minute delays).
  Completed generation checkpoints are reused after local save/export errors.
  Unknown or corrupt interrupted AI checkpoints require review, not blind replay.
- Result refresh: 20 requests per nightly job, pending outcomes of any age and
  seven-day correction window for completed matches; disclosed backlog rolls over.
  Generation retries share the configured football-call budget. The Odds API
  feed is used only on the first generation attempt under its existing cap.
- Immutable snapshots retain original odds, reasons, timestamps, model estimates
  and a source/prompt/settings/library strategy manifest. Existing daily freezes
  are never replaced. No fixture, price, stake or result is invented to fill gaps.
- `src/goal_analysis/learning/` reads the complete cutoff-aware SQLite history.
  Actual-money, unplayed research and frozen cohorts are distinct; frozen can
  overlap the others. Legacy estimated wager prices cannot become verified ROI.
  Post-kickoff publications and future observations are excluded.
- Rule-based evidence/findings/proposals are deterministic. Astra may prioritize
  verified proposals, never invent statistics or apply changes. At most one
  learning CLI analysis attempt per current Berlin day, five-minute timeout,
  existing ChatGPT login, tools disabled, no paid Platform API fallback.
- New Működés panels show native registration provenance, per-stage status,
  request backlog and immutable daily learning reports with full-history groups.

Verification on 2026-10-07:

- **615 Python tests passed**, including real numerical-library fits and new
  scheduling, crash/retry, cutoff, cohort, provenance and end-to-end contracts.
  Changed Python files pass Ruff; JavaScript syntax and browser QA passed.
  Eight upstream penaltyblog/NumPy deprecation warnings remain. Unrelated
  pre-existing repository lint/whitespace changes were not rewritten.
- Native PowerShell 5.1 registration and real Task Scheduler readback succeeded
  at 20:26:02 UTC. Windows normalizes `00:00Z` to `02:00+02:00`; validation compares
  the actual UTC instant. PT15M, InteractiveToken, IgnoreNew, PT2H,
  StartWhenAvailable=true and WakeToRun=false verified. Legacy Daily 223 disabled;
  original and rollback XML definitions preserved in `data/arthur/schedule-backups/`.
- First real Task Scheduler run, 22:26 Berlin, target 2026-10-08:
  `nightly-2026-10-08-a1`, COMPLETED_WITH_WARNINGS. Twenty result requests succeeded,
  34 further result/correction checks remained. Generation made one football
  request; no eligible fixtures were returned for the configured competitions.
  All nine profile statuses and the report were saved, but no ticket or new
  freeze was fabricated. No odds or ticket-generation model call was made.
- The real learning review completed through requested `gpt-6-astra` on the
  existing subscription: one CLI attempt, 16,827 input / 414 output tokens.
  Report `learning-2026-10-07-ef313b095e32`, hash
  `73e894720c0410aae96cba2445d241a9f3c8be69bbde501838fcd36515415784`.
  Research: 87 recorded / 70 decided tickets; actual recorded wagers: zero.
  These research statistics do not establish actual profit.
- A second real Task Scheduler start at 22:28 returned exit 0 without any new
  generation, learning request or API call. All stage attempts stayed at one.
  The natural 22:30:01 Windows timer tick also returned 0; next tick 22:45.
  All 42 previous run hashes and all 10 frozen-package hashes are unchanged;
  there are now 43 immutable runs, 10 freezes and still zero actual wagers.

Deployment follow-up: the existing dashboard process on port 8765 was still v3.8
at verification. This environment rejected stopping that process. The user was
asked to close the old Arthur server and reopen the existing shortcut. The v3.9
backend and populated panels were separately verified on temporary port 8766;
that QA server was stopped after verification and is not the permanent launch
configuration. Browser checks covered research/actual/frozen switching, zero
console errors and a 386 px narrow layout without page overflow. Screenshot:
`reports/arthur-v39-learning-metrics.jpg`. The registered native
nightly task already executes the new v3.9 files independently of the dashboard.
The machine must remain awake, online and logged into the same Windows account.
Next newly scheduled analysis: 2026-10-08 22:00 Berlin, for 2026-10-09.
Positive-fixture live generation/freeze and the next natural 22:00 boundary remain
operational checks; synthetic tests are not a claim of live data availability.
See `docs/windows-release-v3.9.md`, `README.md` and `WORKSPACE.md`.

## Previous release — Arthur portfolio v3.8

## Latest change — 2026-09-29 independent statistical profiles

The user requested GAUSS (penaltyblog bivariate Poisson with Dixon–Coles controls)
and KEPLER (Pi ratings + CPU CatBoost) from the referenced research conversation.
Implementation was validated in an isolated copy before local deployment.
Both profiles use the full selected fixture calendar, not Arthur's shortlist.
They are experimental prospective paper recommendations, with immutable original
model probabilities and no automatic additional wagers. Five-year simulation
database work is deferred. Existing data, frozen packages and settings are preserved.

Validation: 533 Python tests passed, including real penaltyblog/CatBoost fits,
full coordinator/ledger/freeze/settlement checks and unchanged quote-timestamp
invariants. Eight upstream NumPy deprecation warnings do not fail the models.
The modified Python files pass Ruff; unrelated legacy lint findings remain.
Dashboard syntax/render tests and browser checks cover model cards and statuses.
Dependencies: penaltyblog 1.12.2, CatBoost 1.2.10 and threadpoolctl 3.7.0.
No extra AI calls or real wagers are introduced by either numerical profile.
Old settings gain both profiles on read; previously disabled legacy profiles stay off.
New predictions require a new run and enough usable history; old runs are never backfilled.
See docs/RELEASE_V3_8.md for methods, data limits and deployment safeguards.

## Source and objective

The working baseline was verified against GitHub commit
`6fb7bc940f19d5466839761d9dae5c3ca46e692d` on
`build/token-efficient-pipeline`. The v3 work is prepared on
`build/arthur-dashboard-v3`; the older pipeline remains available.

The user approved a modern local dashboard with independent ticket strategies,
real Astra review, selectable dates, adjustable evidence strictness, recorded
results and a daily Windows run. Profit is an evaluation objective, not a
promised outcome. The application does not place bets.

## Approved portfolio rules

- Default two tickets per day; at most five total, including DAILY_223.
- Actual wagers are manually confirmed at a fixed EUR 5 per ticket.
- Independent profiles: Krónikás (FT over 2.5), Ritmusőr (FT BTTS yes),
  Párharcmester (FT home/away win), Őrszem (FT under 2.5), Merlin (FT draw).
- DAILY_223 independently builds three distinct fixtures at 2/2/3 minimum odds.
  Its existing maximum 2% tolerance remains; minimum combined odds 11.76.
- H1 over 0.5 is experimental. At low veto, missing half-time samples are
  explicit warnings; actual H1 market quotes are always required.
- Other profiles target combined odds 10–40. Incomplete data cannot be replaced
  by invented fixtures, odds, results or motivation claims.
- Betano is preferred when available; a complete alternative bookmaker is valid.
- Dates from today through seven days ahead can be researched. Earlier dates
  show archived records only. Match kickoff is rechecked after Astra review; provider quote timestamps are
  informational only and never exclude prices.
- At strictness 0–33, soft evidence gaps do not veto construction. Lower-odds
  and single-selection fallback drafts may be published with visible warnings.
  All found API prices are used at every strictness, including old, future or
  absent quote timestamps. Timestamp information never changes readiness or
  confidence. Invalid event dates, invalid prices and started events remain out.

## Implemented in v3

- Shared collection and historical enrichment, separate profile builders,
  bounded ticket search and exact duplicate-ticket exclusion.
- Official Codex CLI integration using ChatGPT login and requested
  `gpt-6-astra`, medium reasoning, source-linked web context and structured
  candidate reviews. Inputs, candidates and per-job runtime are bounded;
  unchanged review packets use a separate cache. Token/search counts are
  measured, not hard-capped. No Platform API fallback is available.
- Versioned `arthur-portfolio-v3` prompts alongside existing prompt versions.
- Localhost dashboard: daily tickets, date picker, settings, actual/research
  results, performance charts, feedback export and Codex connection diagnostics.
- Immutable report and ticket snapshots, explicit actual wager recording,
  aggregate profile feedback reused in later Astra packets.
- Outcome refresh with FT/half-time semantics, bounded correction checks,
  review of uncertain results, explicit bookmaker void/payout confirmation.
  Actual and hypothetical financial performance are kept separate.
- API and service costs are not included in displayed ticket net profit; no
  fabricated currency estimate is shown when cost data is unavailable.
- Windows installer, encrypted user-scoped credentials and desktop shortcut.
  The original 07:30 local schedule was replaced by v3.9's Berlin 22:00 workflow
  described above. The PC must be awake, online and the same user logged in.

## Prior research recovered

The recovered Arthur report describes 20,248 matches, not 27,000. Its reported
O2.5 triple results are hypothetical and contain substantial losing periods.
The original raw-data ZIP could not be downloaded successfully, so the study
was not rerun or imported as verified training data. No recovered H1 over 0.5
backtest supports a claim of proven profitability. See
`docs/research/STRATEGY_RESEARCH.md` for figures, limitations and source links.

## Validation and remaining live checks

Validation: 365 Python tests, Ruff, JavaScript syntax and Chromium desktop/mobile
checks passed, including visible warning/fallback cards and the zero-veto slider.
Tests use controlled provider and model
responses; no live Codex login is available in the development environment.
The migration details are in `docs/RELEASE_V3_2.md`; the new data-veto behavior
and update procedure are in `docs/RELEASE_V3_3.md`.
The browser interface is also exercised against the real local HTTP server with
isolated, explicitly synthetic test records.

The complete suite and browser checks were rerun successfully on 2026-09-21.
The focused strategy follow-up is complete in `docs/research/STRATEGY_RESEARCH.md`:
opponent-adjusted Over 2.5, Dixon–Coles draw benchmarking, then experimental H1
Over 0.5. No new backtest or profitable strategy certification is claimed.

The user's Windows installation must verify Codex ChatGPT login and native
sandbox execution, DPAPI, Task Scheduler, the shortcut and Astra access. The
installer performs a real Codex model probe and stops on failure. A live run must confirm
that the configured football and odds feeds supply that day's markets.

Secrets, local configs, raw data, reports and the ledger are excluded from Git.
Never copy API keys into chat, logs, reports or repository files.

## Delivery

The cumulative Arthur v3.5 source is published on GitHub branch
`build/arthur-dashboard-v3`. The first published release commit is
`fad7453a3057482d4d0f4459ffd6e0c7a0c89418`. All 188 tracked files were verified
against the local committed source by the Git tree hash. The existing main and
build/token-efficient-pipeline branches were preserved.

The tested Windows installer remains Arthur-v3.5-frissites.ps1. Local settings,
credentials, reports and SQLite databases are excluded from source publication.

## Latest decision: use the existing ChatGPT/Codex allowance

The user confirmed encrypted OpenAI-key storage and config validation, but the
first Platform model probe returned HTTP 429. The user then declined separate
API billing and the v3.1 diagnostic download. Do not ask them to top up API
credits or return to API authentication. Actual Astra access remains unverified.

Arthur now launches `codex exec` with forced ChatGPT login, read-only sandbox,
isolated temporary working directory, disabled command tools/integrations and
no API/service keys in the child environment. It never reads the CLI auth
store. Exact requested model completion is distinguished from server-resolved
model metadata, which the JSONL stream does not provide. Citations come from
Codex after an observed web-search event; Arthur does not independently fetch
those source contents. Unknown failures produce static messages without raw logs.

The Windows installer installs the official CLI when needed, opens ChatGPT
login, checks the model through the subscription and only then schedules the
job. Saved sports keys/config/data remain intact. A local subscription limit,
expired login or unavailable Astra model stops the job without substitution.
The CLI requires Node.js/npm for installation. The previous encrypted Platform
key is no longer loaded by Arthur's launchers.

## Latest user feedback: the dashboard produced no tickets

The user has the dashboard running but received no tickets even for Saturday.
They explicitly requested that lowering the veto must admit missing background
data and show warnings rather than stop. The prior unconditional history/venue
gate, five-minute quote removal and all-or-nothing history collection prevented
that behavior. These were reproduced and changed; the user's exact live run
artifacts have not been inspected.

Portfolio collection now retains successful league history and fixture results
when other leagues fail. At low veto, valid priced candidates survive missing
history and old quote timestamps; Codex receives them using the same eligibility
rules. Warnings stay in the ticket, ledger and export and appear openly on cards.
Targets remain preferred, with explicit lower-odds/single fallback drafts when
necessary. DAILY_223 naming and its 2/2/3 price roles remain intact. The cap is
still five. No automatic wagers or separate API billing were added.

Existing installations need only program-file replacement and a server restart,
then a fresh run at slider 0–30. Do not make the user repeat key/login setup.

## Startup credential recovery

The user then reported that launch fails decrypting THE_ODDS_API_KEY. The old
writer used Set-Content (with a trailing newline), while readers forwarded raw
text directly to ConvertTo-SecureString. This is a concrete defect candidate;
the user's encrypted bytes and Windows profile have not been inspected.

v3.3.1 introduces a shared DPAPI helper: trim legacy encrypted input, never
rewrite saved credentials during launch, write encrypted data without a newline,
verify disk read-back, then atomically replace the selected key. Blank or failed
new input preserves the old file. The installer asks again only for the key it
cannot read. The scheduler uses the same reader without interactive prompts.

`configure-arthur-sports.ps1 -Launch` first tries the saved Odds key, asks for
only that key if necessary, and opens Arthur. It does not contact sports services
or OpenAI and does not change Codex login or scheduling. The dashboard itself
now opens even when a sports credential cannot be loaded, with a console warning
and missing-credential status in the UI; new live analysis still needs the key.

Delivery: Arthur-v3.3.1-inditas.zip is a small overlay for the existing v3.3
installation. The existing 365 Python tests pass. One new real-Windows DPAPI
test is skipped on Linux; actual decrypt/encrypt is verified by the repair
command on the user's Windows account. See docs/RELEASE_V3_3_1.md.


## v3.4 — empty odds response no longer hides matches

The user's September 27 export showed five fixtures, zero priced candidates,
18 football requests and one odds request (provider last_cost=0, remaining=500).
Four league queries failed, but the old report discarded their causes. Their
precise live cause is not established by that export.

Portfolio collection now requests the full Berlin-date calendar once, filters
configured leagues locally and retains the provider season. History is requested
only for active leagues, within the remaining call budget. Legacy DAILY_223
collection remains unchanged. Missing/exhausted odds credentials or an empty
response no longer blocks calendar collection.

Reports include fixture names, source IDs, seasons, calendar metadata and odds
query event counts. The dashboard always renders this calendar. When prices are
missing, independent specialist profiles can provide ODDS_PENDING previews;
at strictness 0–33 missing historical support is visibly disclosed. These previews
have null prices, no bookmaker, no recorded stake and no financial ticket ID.
They are retained in run exports, not in the wager or research-return ledger.
DAILY_223 is never claimed without its required actual price slots. Preview
candidates use the same bounded Codex review, and started legs are removed after
review. All quote requirements remain enforced for priced tickets.

Validation: 372 tests pass; one native Windows DPAPI test is skipped on Linux.
Browser checks cover empty, priced and unpriced runs, export, disabled unpriced
wager controls, and desktop/mobile layout. The five-fixture / zero-cost response
and missing Odds key both produce five disclosed previews at veto zero. These
are synthetic provider tests; no live user credentials or paid service calls were
used here. Native Windows updater execution still requires the user's machine.

The GitHub connector was retried on September 21 and again returned HTTP 403
Resource not accessible by integration for create-tree. No remote branch or
commit was created. The local Windows folder is not mounted in this environment.
The self-contained Arthur-v3.4-frissites.ps1 applies the tested release directly
to C:\AI-Work\GoalAnalysis with per-file verification, backups and rollback.
It does not install packages, alter credentials/configuration, schedule tasks,
call model services, or push via another authentication route.


## v3.5 — every profile, automatic research and persistent recommendation outcomes

The attached Arthur-2026-09-22 export establishes the actual failure: 13 fixtures,
zero priced candidates, and one Codex request. Research completed without a
validated search/source payload, which aborted the second review request. This
was not an absent model invocation. Old token accounting also discarded usage
on this validation failure.

The current user request supersedes limiting the *visible profile recommendations*
to two/five: all seven enabled profiles now publish independently whenever valid
upcoming fixtures exist. The selected priced portfolio retains its separate
two/default and five/maximum cap and explicit EUR 5 manual wager recording.
Missing history and prices do not suppress profile recommendations at any slider
setting. Strictness still governs the selected priced portfolio. DAILY_223 with
unverified 2/2/3 slots is explicitly a target proposal, not a verified DAILY_223.
The empty-calendar case shows NO_FIXTURES; no events are invented.

Astra runs automatically on a bounded, round-robin shortlist covering all profiles.
The existing research request now includes missing-odds lookup and weather, coach,
lineup, absences, workload and motivation coverage. Missing/malformed research
evidence leads to a disclosed supplied-data review instead of cancelling it.
Unobserved web research cannot validate citations or quotes. Quota/auth/model
failures do not trigger API fallback or repeated charged attempts. Actual stage
progress and the model outcome are visible. Confidence is LOW/MEDIUM/HIGH evidence
strength, never a calibrated win probability.

Web prices require exact candidate, teams, kickoff, market, period, source and
observation-cutoff matching. They remain visibly indicative Codex extractions,
not independently fetched bookmaker confirmations or provider quotes; no financial
ticket is fabricated from them. Results, quotes, timestamps and sources remain
in immutable run snapshots. An empty web result is allowed and disclosed.

Every recommendation is now stored in data/arthur/ledger.sqlite3, including
unpriced ideas. Existing frozen v3.4 previews are indexed using original
publication timestamps. Reruns retain all versions; the latest pre-kickoff
recommendation per profile/day counts, and started recommendations cannot be
replaced by a later run. Full-time and first-half results use the existing
provider-result observations. Shared fixture queries are deduplicated and bounded.
The dashboard defaults the monthly view to all recommendation hit rates, with
per-profile breakdown and match-level scores; actual-money reporting stays separate.
Dashboard analysis now refreshes past results as well as the existing 07:30
scheduled run. Windows must be on and the configured user logged in.

Validation: 392 Python tests pass, one native-Windows test skipped. Changed Python
files pass Ruff; JavaScript syntax, real HTTP/SQLite desktop/mobile browser checks
pass, including seven unpriced profiles, result history, export and existing wager
controls. The repository-wide strict Ruff invocation also reports existing style
issues in unchanged legacy files; those were not modified. Live user Codex access,
web coverage and native Windows installation remain local verification items.

Delivery: cumulative Arthur-v3.5-frissites.ps1 with verified file payload, backup
and rollback. Preserves keys, login, local settings, database and reports.

## Verified GitHub publication

After the user explicitly approved public code/documentation disclosure and
updated the GitHub installation, repository writes succeeded. The cumulative
release was committed as fad7453a3057482d4d0f4459ffd6e0c7a0c89418 and branch
build/arthur-dashboard-v3 was created and read back successfully. Its complete
source tree, 1983569336a5a0f81ac1f14a1db765f03b984cf7, exactly matched the
local committed tree (188 tracked files). This follow-up updates only the
publication record. No code, settings, credentials or local runtime data changed.


## v3.6 — existing-subscription odds recovery

The user approved API-Football odds integration with no new subscription.
Recovery is now enabled by default with the existing key. The new optional
api_football_odds_max_calls cap defaults to 12 and stays within the existing
football budget and provider quota. History reserves a bounded share for odds;
local caching and round-robin pagination avoid redundant calls.

Recovery covers exact pre-match products and validates fixture ID, league,
season, kickoff, decimal price and provider update time. Cross-provider bookmaker
identities are never guessed from equal names. Stale indicative quotes remain
visible in recommendations with warnings; the existing ready-ticket and actual
price confirmation gates are preserved. The Odds API's confirmed unused credits
are released. No additional LLM calls, account setup or paid feed was added.

Reports/dashboard disclose query results, source, original time, recovered prices
and remaining product gaps. Provider provenance survives ticket/SQLite storage.
The existing actual-price wager dialog and Astra web/context branch remain.
The newly attached v3.3.1 files were older reference material; current v3.5 code
was the implementation baseline. See docs/RELEASE_V3_6.md.

Validation: 424 Python tests pass; one native-Windows DPAPI test is skipped on
Linux. Changed Python files pass Ruff, JavaScript syntax and Git whitespace checks
pass. Chromium checks against the real local HTTP server/SQLite pass for source
and gap disclosure, export, optional Odds API credential labels and mobile layout.
Synthetic provider responses verify new behavior; no live sports keys are present
here, so actual day/bookmaker coverage remains a local-run check.

Delivery: cumulative Arthur-v3.6-frissites.ps1 and the existing
build/arthur-dashboard-v3 branch. The updater verifies file hashes and preserves
local keys, settings, login, data and reports. No new subscription was created.


## v3.6.1 — use found API prices regardless of timestamp

The user explicitly superseded quote freshness restrictions. All valid matched
API prices now enter collection, arithmetic, ticket construction, Astra packets,
recommendations and SQLite storage at every strictness. Old, future, malformed
or missing quote timestamps are metadata only: no rejection, refresh requirement,
confidence penalty or readiness downgrade. Original source values are preserved;
missing or invalid timestamps remain null, never manufactured as the current time.

Removed the independent quote gates in API-Football normalization, shared odds
assembly, DAILY_223 preparation, post-Astra delivery and dashboard refresh. Updated
Astra's versioned runtime prompt and hash so quote age is not an analytical veto.
The dashboard displays a neutral per-price timestamp note. Existing historical
reports remain immutable; a new analysis uses the corrected rules. Provider
fallback, source attribution, event/market matching and budget caps still apply.

Delivery: Arthur-v3.6.1-frissites.ps1, with backup/rollback and preservation of
local keys, login, settings, reports and SQLite. No new subscription or model
request was added. See docs/RELEASE_V3_6_1.md for validation and update instructions.

Fully priced profile recommendations also calculate/display their combined price
when they are not selected portfolio tickets. Missing prices still remain null;
source attribution and the theoretical-product label remain explicit.

Validation: 453 Python tests pass, one native-Windows DPAPI test skipped on Linux.
Changed Python files pass Ruff; JavaScript syntax and Git whitespace checks pass.
Chromium desktop/mobile checks against real HTTP/SQLite confirm visible API odds
with old/future/missing timestamp notes, unchanged READY status, export and layout.

## v3.7 — date-specific competition selection and daily freezes

The dashboard now requests the real date-wide API-Football fixture calendar and
shows its leagues, cups and international competitions with match counts and
searchable selection. Existing configured competitions and national matches are
selected initially. The submitted IDs are validated against a fresh calendar
inside the analysis run; only their fixtures feed history, odds recovery, Astra
and profile recommendations. Unconfigured competitions use a provider-only
sport identifier: The Odds API is not queried with an invented key, while the
existing API-Football fixture odds recovery remains available. The default
scheduled scope adds international friendlies, Nations League and World Cup
qualifiers. No odds completeness or profitability is inferred from selection.

The user can explicitly freeze one completed daily run before any of its
recommended fixtures start. SQLite stores the complete original report and all
published recommendation snapshots with a hash; a second freeze for the same day
is rejected. Reruns cannot overwrite the frozen package. Existing bounded result
refresh attaches match outcomes to the frozen recommendation IDs. The Results
view shows the archived package and its legs; Performance has a separate
monthly freeze hit-rate view. It never treats an unpriced recommendation as a
real wager or calculates fictitious monetary returns.

The missing user-local Python 3.12 installation was restored with the signed
official installer. The Windows credential writer also now passes an explicit
backup path to File.Replace, fixing a real-Windows failure exposed by the DPAPI
test. All 457 Python tests pass on Windows, including that isolated DPAPI test;
changed Python files pass Ruff and the dashboard JavaScript passes syntax check.
