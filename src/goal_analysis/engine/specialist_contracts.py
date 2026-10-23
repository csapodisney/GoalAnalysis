"""Fixed specialist constructions, independent of DAILY_223 and club personas."""

import math
from copy import deepcopy

SPECIALIST_CONTRACTS = {
    "kronikas": {
        "version": "kronikas-ft-over25-accumulator-v1",
        "min_legs": 3, "max_legs": 6,
        "min_price": 10.0, "max_price": 50.0,
        "market": ("totals_2_5", "over", "FULL_TIME"),
    },
}


def contract_metadata(profile):
    contract = SPECIALIST_CONTRACTS.get(profile)
    if contract is None:
        return {}
    snapshot = deepcopy(contract)
    snapshot["market"] = list(snapshot["market"])
    return {
        "construction_contract": snapshot,
        "selection_mode": "FIXED_SPECIALIST_ACCUMULATOR",
        "minimum_combined_price": contract["min_price"],
        "maximum_combined_price": contract["max_price"],
        "minimum_legs": contract["min_legs"], "maximum_legs": contract["max_legs"],
    }


def valid_specialist_construction(ticket):
    """Validate against the live policy, never caller-supplied relaxed metadata."""
    contract = SPECIALIST_CONTRACTS.get(ticket.get("profile_id"))
    if contract is None:
        return True
    try:
        legs = ticket["legs"]
        if not contract["min_legs"] <= len(legs) <= contract["max_legs"]:
            return False
        if len({leg["fixture_id"] for leg in legs}) != len(legs):
            return False
        if len({(leg["bookmaker"], leg["region"], leg["currency"]) for leg in legs}) != 1:
            return False
        if any(tuple(leg[key] for key in ("market_key", "selection_key", "period"))
               != contract["market"] for leg in legs):
            return False
        prices = [leg["decimal_price"] for leg in legs]
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 1
               for value in prices):
            return False
        product = math.prod(prices)
        return (contract["min_price"] <= product <= contract["max_price"]
                and math.isclose(product, ticket["combined_price"], rel_tol=1e-9, abs_tol=1e-9))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
