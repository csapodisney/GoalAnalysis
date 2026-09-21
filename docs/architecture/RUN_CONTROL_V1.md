# Arthur run control v1

Phase 15 represents the master prompt's run modes and status precedence as deterministic Python
types. It does not activate or rewrite the imported master prompt.

## Run modes

- `PREMATCH`: a new decision; parent and audit timestamp are forbidden.
- `FINALIZE`: a new version of a prior run; parent is required.
- `AUDIT`: post-result evaluation; parent and a later audit timestamp are required.

The run gate returns exactly one status: `COMPLETE`, `PARTIAL_OUTPUT`, `INPUT_REQUIRED`,
`MODEL_INPUT_REQUIRED`, `DATA_ACCESS_BLOCKED`, or `BLOCKED_VALIDATION`. Executable approval is
blocked without an identified model artifact and calibration period.

## Candidate precedence

The decision function applies the master prompt order without allowing dimensions to hide one
another: withdrawal, structural VETO, incomplete/conflicted evidence, conditional evidence,
unresolved football thesis, failed price gate, then approval. Only `COMPLETE + PASS + VALUE_OK`
can produce `APPROVED`.

An absent fact produces `INCOMPLETE`, never `VETO`. A placed wager is never silently withdrawn;
new information preserves its original decision and records `POST_PLACEMENT_RISK_CHANGED`.
