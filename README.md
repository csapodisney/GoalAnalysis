> Arthur v3.6: API-Football odds recovery with existing credentials and budgets,
> per-fixture coverage and original quote timestamps.
> [Release and update details](docs/RELEASE_V3_6.md).

> Arthur v3.5: automatic Astra research, all enabled daily profile recommendations,
> and persistent results for priced and unpriced selections. See
> [release and update details](docs/RELEASE_V3_5.md).

# GoalAnalysis — Arthur

Arthur v3.4 fixes the empty-odds path: real fixtures stay visible and unpriced,
clearly labelled previews can be generated at low veto. The self-contained
`Arthur-v3.4-frissites.ps1` updates the existing Windows folder with backups.
See [Windows update instructions](docs/ARTHUR_WINDOWS.md).


Arthur is a local football research dashboard with independent ticket profiles,
Astra analysis, a fixed EUR 5 manual wager ledger and actual/research performance
charts. The default is two tickets per day, with a maximum of five including the
independent 2×2×3 ticket. The application never places a wager.

## Windows setup

Close the previous Arthur server's PowerShell window before starting the new
version. In PowerShell, from the updated repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\install-arthur.ps1"
```

The installer preserves existing provider settings, installs into `.venv`, runs
tests, loads the two sports-service keys, verifies Codex/ChatGPT access, updates
the desktop shortcut and registers the daily 07:30 Windows-local task. Use the
`Arthur Goal Analysis` desktop shortcut for the dashboard. Python 3.11+ is needed.

Arthur v3.2 uses the official **Codex CLI with Sign in with ChatGPT**. It consumes
the existing plan's Codex allowance. No OpenAI Platform API key, API credit top-up
or API fallback is used. The configured model remains `gpt-6-astra`; availability
must be verified for the signed-in account. Node.js LTS/npm is required to install
Codex; the installer handles CLI installation and opens its browser login.
The connection check must succeed before the new schedule is registered.
The PC must be awake, online and the same Windows user logged in for scheduled
runs. See [Windows setup and daily use](docs/ARTHUR_WINDOWS.md).

## Daily workflow

1. Select today or a date up to seven days ahead, ticket count and strictness.
2. Start analysis. Arthur collects real fixtures/history/odds, constructs the
   independent profiles and requests sourced Astra review.
3. Inspect tickets, arguments, risks, source links and any coverage issues.
4. If you manually play a ticket, record it with the actual accepted combined
   price. Only these records count toward actual EUR 5 profit and loss.
5. Refresh results or let the next scheduled run update them. Performance shows
   actual and hypothetical outcomes separately; uncertain settlement remains
   visible for review. Exported feedback can also be shared in chat.

The five specialist profiles cover FT over 2.5, BTTS yes, home/away winner,
under 2.5 and draw. DAILY_223 uses separate 2/2/3 price roles. H1 over 0.5 is an
experimental additional profile. Invalid or unavailable data produces visible
coverage diagnostics; it cannot produce a fabricated complete ticket.

The local dashboard uses only bundled assets, with no CDN dependency.

Arthur v3.3: set the data-veto slider to **0–30** to keep real priced selections
when history or context is missing. Tickets show prominent warnings and remain
drafts when evidence or quote freshness is insufficient. If the target odds
cannot be reached, an explicitly labeled fallback or single selection can be
shown. DAILY_223 keeps its own 2/2/3 definition.

Updating an existing working installation requires only replacing the program
files and restarting `scripts/start-arthur.ps1`; no new login or key setup is
required for this update. Then rerun the selected date with the lower veto.

## Development and manual commands

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/run-arthur.py --check-config
python scripts/run-arthur.py --check-codex-login
python scripts/run-arthur.py --check-openai
python scripts/run-arthur.py --live --settle
python scripts/arthur-dashboard.py --open
```

Manual runs expect `API_FOOTBALL_KEY` and `THE_ODDS_API_KEY` in their environment,
plus a saved `codex login` using ChatGPT. Windows launchers load the saved
DPAPI sports credentials. Old encrypted OpenAI keys are not loaded or used.
Local settings live in `config/arthur-settings.json` and
`config/daily223-live.json`; examples are versioned, private values are not.

- [Current implementation state](CURRENT_STATE.md)
- [Release validation and limits](docs/RELEASE_V3.md)
- [ChatGPT/Codex migration](docs/RELEASE_V3_2.md)
- [Recovered strategy research](docs/research/STRATEGY_RESEARCH.md)
- [Windows installation](docs/ARTHUR_WINDOWS.md)

Historical research is evidence to evaluate, not proof of future profit.
Displayed ticket net results exclude API and other service costs.
