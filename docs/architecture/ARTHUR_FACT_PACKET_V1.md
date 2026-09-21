# Arthur Fact Packet v1

## Purpose

The fact packet is the compact, immutable handoff between deterministic football processing
and the Kerekasztal. It prevents every role from reopening the same raw sources.

## Guarantees

- Accepted fixtures retain the frozen football ranking order.
- Prices and odds fields are rejected before packet creation.
- Missing evidence remains `null`.
- Every fixture has a SHA-256 evidence hash.
- The complete ranked screening input has a canonical SHA-256 hash.
- The packet records source generation time and the historical evidence window.
- Screening rejection reasons remain available as aggregate audit counts.
- Output is written atomically as compact UTF-8 JSON.

## Role routing

The packet only routes facts; it does not replace role judgment:

- Krónikás receives total-goal and Over 2.5 evidence.
- Ritmusőr receives BTTS and two-sided scoring evidence.
- Párharcmester receives attacking and defensive home/away profiles.
- Őrszem receives defensive and evidence-quality fields.
- Merlin receives parity-relevant scoring fields.
- Dániel receives sample and coverage weaknesses for the mandatory counter-case.
- Arthur still integrates the independent role conclusions.

## Command

```powershell
.venv\Scripts\python.exe scripts\build-arthur-fact-packet.py `
  reports\daily\screening-2026-09-20.json
```

The default output is placed beside the screening file with an `arthur-` prefix.
