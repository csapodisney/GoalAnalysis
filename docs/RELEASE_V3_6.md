# Arthur v3.6 — odds recovery with existing subscriptions

Arthur can now recover missing bookmaker prices through the existing
API-Football key. No new account, subscription, package dependency or OpenAI API
billing is introduced. Existing local config automatically receives the new
default; credentials and saved results stay unchanged.

## Collection

The Odds API supplies its existing base and optional markets. When a pending
fixture lacks any requested product, API-Football `/odds` is queried for its
league, provider season and Berlin date. Pagination runs across active leagues
in turn, inside the existing total football budget. Fixture IDs, competition,
season and kickoff must agree with the calendar before a price is usable.

The new optional `api_football_odds_max_calls` setting defaults to 12, permits
0–30, and is always constrained by `max_football_calls` and the returned daily
quota. Setting it to zero disables recovery. History reserves up to one quarter
of the total football allowance for this work, capped by the recovery limit.
Cached history remains usable even when its request allowance is zero.

Successful odds pages are cached locally for 15 minutes; empty pages for 30.
Cache reads do not change the provider's quote timestamp. Failures do not trigger
a new subscription or a paid-service substitute. Provider-confirmed zero-cost
The Odds API responses now release their unused local credit reservation.

## Markets and price integrity

Supported pre-match products are full-time 1X2, half-goal totals, BTTS, first-half
1X2, first-half totals and first-half BTTS when explicitly named by the provider.
Only requested markets enter the shared universe. Coverage checks require the
actual strategy lines: FT 2.5 and H1 0.5, not merely some totals market.
Unknown market names, qualification markets, integer/Asian split lines,
invalid/future timestamps, quotes over 24 hours old and conflicting duplicate
markets are excluded. No model is used to turn missing data into a price.

API-Football bookmaker IDs are kept separate from The Odds API bookmaker keys,
even when display names match. A ticket still uses one bookmaker identity;
cross-feed names alone cannot establish matching regions/account products.
Betano remains preferred where a complete ticket is available.

The [official API-Football guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide)
documents pre-match odds, pagination and an approximately three-hour update
cycle. Such quotes are useful indicative research data. The existing five-minute
freshness rule still controls ready portfolio tickets; old prices remain visible
in daily recommendations with warnings. Low-veto drafts require explicit actual
price confirmation. Retrieval time never masquerades as bookmaker update time.

## Report and dashboard

Each run records both providers' query results, cache use, recovery call counts,
recovered candidate counts and fixture-level missing products. Expand
**Szorzóforrások és hiányok** in the fixture list to inspect sources and gaps.
Quote provider, bookmaker, source URL, market ID and original timestamp persist
with selected legs and immutable run/ticket snapshots. Sources may each provide
different markets; complete aggregate coverage does not verify an account's bet
availability.

Astra's existing bounded web research and context review remain in place. Web
quotes remain indicative; the new deterministic API recovery adds no model
calls. The existing wager dialog records the actual combined bookmaker price
and EUR 5 stake separately from research prices. No bet is placed automatically.

## Update

Close the Arthur server window, download `Arthur-v3.6-frissites.ps1`, and run it
from its download directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Arthur-v3.6-frissites.ps1
```

The updater verifies program files, keeps a rollback backup, preserves local
settings/login/data, and starts Arthur. Start a new analysis to see recovery;
archived reports retain their original data.

## Verification boundaries

Automated tests use explicitly synthetic provider responses, including missing
Odds API credentials, exact fixture matching, first-half markets, duplicate
quotes, pagination, caching, exhausted budgets, stale prices, provider failures
and persistence. There are no live sports credentials in the development
environment, so actual coverage of the user's selected fixtures/bookmakers must
be confirmed on their next local run. No guaranteed coverage or profit is claimed.

Validation result: 424 tests passed, one Windows-only test skipped. Ruff on
changed Python files, JavaScript syntax, and Chromium desktop/mobile checks
against the local HTTP/SQLite dashboard passed.
