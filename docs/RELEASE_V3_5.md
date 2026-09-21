# Arthur v3.5

The September 22 export showed a real Astra request that stopped at research
validation. Missing web evidence now produces a disclosed data-only review;
source-free material cannot become verified research. Usage is recorded even
when content validation fails. Authentication/quota errors remain visible and
never trigger a paid Platform API fallback.

Every enabled profile now has a daily recommendation, independent of the selected
priced portfolio count. Missing prices/history affect disclosure and evidence
strength, not profile visibility. Real upcoming fixtures are still required.
DAILY_223 target proposals clearly disclose unverified 2/2/3 slots.

The same automatic research stage looks for missing market quotes, kickoff
weather, coach statements, lineups, absences, workload and motivation. It groups
shared fixtures and uses at most 30 review candidates, a four-search instruction
budget, two normal model jobs and the existing 300-second timeout per job. Search
and token caps are not mechanically enforced by the CLI. Actual usage is logged.
Unchanged complete research can use the existing 30-minute cache. Unavailable
research is not cached as a complete result.

Web prices are indicative extractions from cited pages. The application checks
exact candidate/fixture/market/period identity, timestamps, finite odds and source
membership. It does not independently fetch bookmaker pages. These prices do
not silently become live provider prices, placed wagers or financial returns.

All recommendations, original reasoning and later match results live in
`C:\AI-Work\GoalAnalysis\data\arthur\ledger.sqlite3`. Existing recorded previews
are imported automatically from their immutable snapshots. The database and
`reports\arthur` should be included in the user's normal local backup; neither
contains an automatically executable bet and neither is pushed to public GitHub.

Monthly **Minden profilajánlat** reports hit counts and rates, including no-odds
recommendations. The latest pre-kickoff version per profile/day counts; started
recommendations remain counted. Every earlier/later version remains in the export.
This avoids inflating hit rates by rerunning the same day. Actual-money results
continue to require manual recording of the accepted EUR 5 bet and real price.
Cancelled/postponed matches stay under review, not automatically won or lost.

Run a new analysis after updating. Astra starts automatically; no separate
connection-test button is required. A visible stage message distinguishes data
collection, Astra research, final review and saving. Each new dashboard analysis
and the existing 07:30 Windows task refresh previous results. A sleeping/offline
PC cannot execute the scheduled request; the next analysis catches up.

Update using the cumulative `Arthur-v3.5-frissites.ps1` with Arthur's server
closed. Existing keys, login, settings and data remain in place. The updater
backs up program files, validates payload and installed hashes, then launches.
Native Windows and live user-account execution still need confirmation locally.

Official CLI contracts used:
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://learn.chatgpt.com/docs/config-file/config-reference

Validation uses synthetic providers/model outputs and SQLite round trips, plus
real local HTTP/Chromium desktop/mobile UI checks. No live betting profitability
or reliable future yield is asserted by the test results.
