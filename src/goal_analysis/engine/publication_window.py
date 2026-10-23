"""Prospective publication boundary, independent of protected DAILY modelling."""

from datetime import datetime, timedelta

LOCKOUT = timedelta(minutes=15)


def allows_new_leg(leg, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Publication clock must be timezone-aware")
    try:
        kickoff = datetime.fromisoformat(leg["kickoff"])
        return (kickoff.tzinfo is not None and kickoff.utcoffset() is not None
                and kickoff > now + LOCKOUT)
    except (KeyError, TypeError, ValueError):
        return False


def allows_new_ticket(ticket, now: datetime) -> bool:
    legs = ticket.get("legs", [])
    return bool(legs) and all(allows_new_leg(leg, now) for leg in legs)
