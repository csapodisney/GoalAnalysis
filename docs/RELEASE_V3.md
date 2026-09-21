# Arthur portfolio v3 — release verification

Baseline: GitHub `6fb7bc940f19d5466839761d9dae5c3ca46e692d`.
Date: 2026-09-21.

## Changes

The former dashboard exposed the DAILY_223 path only. The new entry point
collects shared facts once and builds independent specialist profiles plus the
reserved DAILY_223 ticket. It then requests sourced Astra review, persists
immutable reports and exposes the outcome through the new dashboard.

The user can select a date, adjust soft evidence strictness, request 1–5 tickets,
record fixed EUR 5 wagers and compare actual and hypothetical results. The daily
scheduler refreshes results and generates the day's portfolio. Empty failed
reruns preserve existing suggestions; stale prices remain visibly marked.

## Automated and browser verification

Final validation: **315 Python tests passed**. Ruff passed on all changed
Python files, JavaScript syntax validation passed, and `git diff --check` passed. Focused tests cover provider collection, historical evidence,
independent construction, Astra coverage, bounded retries/cache, quote expiry,
started matches, date restrictions, cross-process run locking, HTTP request
protection, secret redaction and feedback export.

Ledger tests include final-score corrections, regulation-time versus extra-time
settlement, H1 results, stable drawdown ordering, daily caps across reruns,
started research preservation and explicit partial-void bookmaker payout.

A real Chromium browser was run against the local HTTP server using an isolated
test database. Empty and populated states, four tabs, date restrictions,
settings persistence, manual EUR 5 recording, JSON export, settled wins/losses,
performance charts and 390 px mobile layout were exercised. Test teams and
results were explicitly synthetic and never inserted into production data.
No JavaScript or Content Security Policy errors were observed.

## What these checks do not establish

- Provider and Astra integration tests use controlled responses. They do not
  establish the user's live account entitlement, current market coverage or
  profitability. The installer makes a real bounded Astra connection request.
- The PowerShell scripts were inspected but could not be executed in an actual
  Windows desktop session here. User-scoped DPAPI, the shortcut and Task
  Scheduler must be verified by the Windows installation.
- The recovered 20,248-match historical study was read, but its raw-data archive
  could not be downloaded. No new reproduction or H1 historical-odds backtest
  is claimed.
- Odds feeds do not prove a bookmaker account will accept the displayed price.
  Actual wagers record the user's accepted combined price. Partial voids may
  require confirmation of the bookmaker's payout.
- Service costs are recorded as unknown when unavailable and are excluded from
  displayed ticket net profit. The app does not train the OpenAI model; it
  supplies accumulated portfolio feedback to later review requests.

## Installation acceptance

Close the older dashboard server, update the repository and run
`scripts/install-arthur.ps1`. It preserves existing configurations and secrets,
runs the Python suite, validates configuration and checks actual Astra access.
Only after these succeed does it install the daily schedule and launch the new
panel. Then run the selected date once to verify live football/odds coverage.
