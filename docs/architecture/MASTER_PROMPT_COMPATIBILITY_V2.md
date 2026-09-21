# Arthur Pentagram 2.0 compatibility gate

The original `Arthur_Pentagram_Master_Prompt_v2 (1).md` is imported byte-for-byte as
`config/prompts/arthur-pentagram-v2/master.md`.

Its immutable identity is:

- SHA-256: `f29de06a751fc1283343083771d2877ef512e0b73be28c3de6891d641ae72fe8`
- Size: 61,565 bytes
- Status: `imported_not_activated`

The prompt is deliberately not substituted for the current per-fixture Arthur role prompt yet.
It defines a complete run protocol, while the existing runner expects one strict role opinion per
fixture. Blind substitution would both break the interface and resend the full master prompt for
every fixture.

## Compatibility findings

| Master-prompt contract | Current implementation | Status |
| --- | --- | --- |
| Odds-free football preselection | deterministic screening and ranking | compatible |
| Five Pentagram specialists | independent specialist calls | compatible |
| Dániel counter-case and structural VETO | mandatory reviewer and fail-closed VETO | compatible extension |
| Odds only after eligibility freeze | post-ranking odds provider and ticket gate | compatible |
| PREMATCH / FINALIZE / AUDIT modes | separate partial jobs | orchestration pending |
| Required run-input object | command arguments and configuration | typed contract pending |
| Probability model and calibrated value gate | no calibrated probability model | blocking |
| Line-up and team-news freshness gates | not implemented | blocking |
| Correlation-aware ticket construction | same-bookmaker deterministic gate only | blocking |

Activation remains fail-closed until the pending and blocking contracts are represented explicitly.
The next implementation phase should add the typed run-input/status state machine without changing
the imported prompt bytes.
