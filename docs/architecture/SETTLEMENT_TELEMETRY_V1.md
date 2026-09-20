# Settlement and Telemetry v1

## Frozen settlement

Settlement hashes the complete ticket-gate artifact before evaluating any result. The resulting
record keeps the ranking-integrity hash, frozen ticket hash, bookmaker, quoted prices and final
regular-time scores.

Supported v1 markets:

- total goals, using keys such as `totals_2_5` and `totals_3_0`;
- both teams to score (`btts`);
- regular-time match result (`match_result`).

An unsupported market, missing score, duplicate score or mutable/incomplete ticket fails closed.
Integer total pushes are settled as void legs. One losing leg loses the combined ticket.

All results use one research stake unit. The output explicitly remains shadow research and does
not claim real betting profit.

## Append-only performance log

Every settlement can be appended to `logs/performance.jsonl`. Each entry contains the previous
entry hash and its own SHA-256 hash. Reading or summarizing the log verifies the complete chain;
edited historical lines cause an error.

The shadow summary reports ticket counts, win/loss/void counts, stake units, return units, net
units and unit-based ROI.

## Token telemetry

OpenAI usage metadata is aggregated:

- across the whole Kerekasztal run;
- by role;
- by returned model ID;
- with missing usage reported explicitly.

No monetary cost is invented. Cost remains `null` until a dated, versioned model-price table is
added.

## Commands

```powershell
.venv\Scripts\python.exe scripts\settle-shadow-ticket.py `
  reports\daily\ticket-gate-shadow.json data\results\scores.csv

.venv\Scripts\python.exe scripts\summarize-shadow-performance.py

.venv\Scripts\python.exe scripts\summarize-token-usage.py `
  reports\daily\kerekasztal-openai-shadow.json
```
