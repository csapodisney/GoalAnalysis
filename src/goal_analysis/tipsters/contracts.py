"""Club-only construction checks; never applied to protected DAILY_223."""

import math

from .catalog import CATALOG_VERSION, TIPSTER_BY_ID


def valid_construction(ticket, now=None):
    """Validate the live catalogue, not a caller-supplied relaxed persona."""
    persona = TIPSTER_BY_ID.get(ticket.get("profile_id"))
    if persona is None:
        return True
    if ticket.get("catalog_version") != CATALOG_VERSION:
        return False
    legs = ticket.get("legs", [])
    prices = [leg.get("decimal_price") for leg in legs]
    if not prices or any(type(p) not in (int, float) or not math.isfinite(p) or p <= 1 for p in prices):
        return False
    total = math.prod(prices)
    quoted = ticket.get("combined_price")
    if type(quoted) not in (int, float) or not math.isclose(total, quoted, rel_tol=1e-10):
        return False
    band = persona["target_odds_band"]
    # A source-bound high-odds waiver already verified by the builder may
    # authorize that exact SINGLE selection only; the model gates still hold.
    exceptional = False
    if len(legs) == 1 and total > band["max"]:
        from goal_analysis.engine.veto_overrides import verified_veto_override

        original = {**legs[0], "candidate_id": legs[0].get("source_candidate_id")}
        proof = verified_veto_override(original, now=now)
        exceptional = "HIGH_ODDS" in (proof or {}).get("gates", [])
    if not exceptional and not (persona["min_legs"] <= len(legs) <= persona["max_legs"]
                               and band["min"] <= total <= band["max"]):
        return False
    fixtures = [leg.get("fixture_id") for leg in legs]
    cohorts = {(leg.get("bookmaker"), leg.get("region"), leg.get("currency")) for leg in legs}
    return (all(fixtures) and len(set(fixtures)) == len(fixtures) and len(cohorts) == 1
            and all(next(iter(cohorts))))
