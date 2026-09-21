# Kerekasztal Orchestration v1

## Execution order

For every fixture in the frozen football ranking:

1. Krónikás, Ritmusőr, Párharcmester, Őrszem and Merlin receive independent compact inputs.
2. None of the five specialists can see another specialist's conclusion.
3. Dániel receives all five views and performs the mandatory adversarial review.
4. Arthur receives all specialist views plus Dániel's review and decides last.
5. The deterministic guard applies every structural veto even if Arthur returns support.

## Preserved responsibilities

- Krónikás: total-goal chain and path to the third goal.
- Ritmusőr: two-sided scoring and response behaviour.
- Párharcmester: relative attacking and defensive strength.
- Őrszem: chance restriction and structural Under risk.
- Merlin: parity and equalising potential.
- Dániel: strongest counter-case and structural veto.
- Arthur: final evidence synthesis.

## Response contract

Every response must contain:

```text
role, fixture_id, verdict, confidence, thesis, evidence_sha256, structural_veto
```

Allowed verdicts are `support`, `oppose`, `unknown` and `veto`. Confidence is bounded from
zero to one. Role, fixture and evidence-hash mismatches fail the run.

## Safety and audit

- The run is explicitly marked `kerekasztal_shadow`.
- Prices are not attached.
- Odds and price fields in role responses are rejected.
- The frozen ranking order is preserved.
- The output records every specialist, Dániel, Arthur and the enforced final state.
- The model runner is a replaceable interface; external model credentials are not stored.

## Offline replay

Structured role responses can be validated without calling a model:

```powershell
.venv\Scripts\python.exe scripts\run-kerekasztal-shadow.py `
  reports\daily\arthur-screening-2026-09-20.json `
  reports\daily\role-responses-2026-09-20.json
```
