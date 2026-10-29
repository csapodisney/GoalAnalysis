# V2 club construction validation repair

`tipsters/contracts.py::valid_construction` currently always reads the pinned
historical persona odds band. The v2 builder already uses 15–40, and canonical
`generated_tickets._invalid` checks that same range. Therefore a valid v2 Lotti
alternative at 32.768 is rejected against her historical maximum14 before
`diversification._eligible` or the bounded ticket review menu can retain it.
Nóra's historical maximum22 has the same boundary problem. This does not explain
the seventeen missing club outputs in the completed run; their own-method
minimum-leg constraints remain unchanged.

The narrow repair is limited to `valid_construction`: an absent version or an
explicit integer1 retains the existing historical path, including its existing
verified singleton HIGH_ODDS waiver. Integer2 uses the operating policy's fixed
15–40 range. A named cautious persona may use5–15 only with the same APPLIED,
full-day, zero-primary-options, exact-reason proof already required by
`generated_tickets._fallback`. This rechecks existing builder authority; it does
not create a fallback or make an unproven exception valid. Invalid/noninteger
club versions reject. DAILY and other non-club profiles retain their existing
early return and independent validators.

The pinned catalogue remains the source of identity, catalogue version and
minimum/maximum legs. Caller-supplied persona/band/leg relaxations are ignored.
Price multiplication, distinct fixtures and one bookmaker/region/currency
cohort remain unchanged. V2 cannot inherit the historical high-odds waiver to
exceed40 or bypass minimum legs. No model, edge, probability, history, source,
quota, budget, publication or financial rule changes.

Prepared tests cover old-max14/22 cards at30–40, v2 rejection outside15–40,
unchanged v1 and DAILY, typed versions, cautious-proof parity and forgery,
immutable catalogue/leg/cohort gates, and real v2 builder alternatives passing
through `_eligible` into `_menus`. Tests are prepared only; Root owns the single
integration execution. No production edits, provider/DB/model or generation run.
