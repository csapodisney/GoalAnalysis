# Controlled end-to-end shadow v1

Phase 17 connects the typed run control and approval gates to the complete shadow coordinator.

Global run-input or model-calibration failures stop the LLM and odds stages. Fixture-specific
team-news or lineup failures preserve Arthur's football analysis but prevent that fixture from
entering live price requests. The original Arthur decision is retained as `arthur_selected`, while
the effective `selected` flag is closed for the ticket gate.

Every bundle stores a `run_control.json` artifact and copies its decisive status and issue codes to
the manifest. This makes a blocked run understandable without reading role output. All paths retain
`real_wager_placed: false`.

The legacy `complete_shadow_run` entry point remains available for replay compatibility. New live
shadow execution must use `complete_controlled_shadow_run`.
