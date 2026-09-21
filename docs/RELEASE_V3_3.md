# Arthur v3.3 — make the data veto effective

The user found priced candidates but received no tickets, including for a busy
Saturday. Inspection identified three unconditional gates: historical and venue
support was mandatory before the slider applied; prices older than five minutes
were dropped during collection; one failed history query blocked all collection.
The user's actual Windows run artifacts were not available here.

## Changed behavior

- Strictness 0–33 disables soft evidence vetoes. A zero score remains zero and
  missing evidence remains absent; it is not converted into confidence.
- History failures are isolated by league/season for the portfolio. Successful
  records survive, budgets are respected and fixture-query capacity is reserved.
  An unavailable league's fixtures do not discard other leagues' fixtures.
- Old provider prices and aged fixture snapshots may be shown in permissive
  mode, with original timestamps and a refresh warning. Future timestamps,
  invalid prices, started/cancelled events and ambiguous fixture matches remain
  excluded. Quotes are never invented, rebased or represented as fresh.
- Below-target combinations and single selections become explicit fallback
  drafts when a normal specialist ticket cannot be built. An additional named
  fallback handles an available market outside specialist profiles. There is
  no mixing of bookmakers, regions or duplicate fixtures in one ticket.
- DAILY_223 retains its original 2/2/3 definition; other fallback tickets are
  not called DAILY_223. The total publication cap remains five.
- The review packet uses the same permissive preparation, so newly admitted
  candidates also reach Codex. AI failure or incomplete review leaves drafts.
- Data warnings appear directly on ticket cards and persist in the ledger and
  JSON export. Half-time sample gaps are explicit experimental warnings at low
  veto and remain exclusions at higher veto.
- Strict legacy DAILY_223 commands retain their default behavior. No API keys,
  login settings, dependencies or scheduled-task definitions changed.

## Verification and limits

365 Python tests passed; Ruff, JavaScript syntax and diff checks passed. Chromium
desktop/mobile checks passed with no JavaScript or CSP errors.

Regression scenarios include missing all history, old quotes, today and Saturday,
below-target combinations, an available single, H1 sample gaps, partial history,
exhausted history budget, invalid/future data, an empty odds feed, and persistence
of actual generated warning fields. Browser checks exercise the zero-veto label,
visible warning cards, fallback labels and mobile layout using synthetic data.

These checks use controlled service responses; they do not establish current
coverage of the user's sports plans or guarantee bookmaker availability.
An actual fixture and a valid provider quote are still required for a ticket.

Update existing installations by replacing program files and restarting Arthur.
Then set the slider to 0–30 and rerun the desired date. No installer rerun needed.
