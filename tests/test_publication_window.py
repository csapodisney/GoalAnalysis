from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.publication_window import allows_new_leg, allows_new_ticket

NOW = datetime(2026, 10, 15, 16, tzinfo=UTC)


@pytest.mark.parametrize("seconds,allowed", [(901, True), (900, False), (899, False), (0, False), (-1, False)])
def test_strict_fifteen_minute_boundary(seconds, allowed):
    leg = {"kickoff": (NOW + timedelta(seconds=seconds)).isoformat()}
    assert allows_new_leg(leg, NOW) is allowed


def test_whole_ticket_is_blocked_if_analysis_consumes_the_buffer():
    ticket = {"legs": [{"kickoff": (NOW + timedelta(minutes=20)).isoformat()},
                       {"kickoff": (NOW + timedelta(hours=2)).isoformat()}]}
    assert allows_new_ticket(ticket, NOW)
    assert not allows_new_ticket(ticket, NOW + timedelta(minutes=5))


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-10-15T19:00:00"])
def test_missing_or_ambiguous_kickoff_is_closed(stamp):
    assert not allows_new_leg({"kickoff": stamp}, NOW)


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        allows_new_leg({}, NOW.replace(tzinfo=None))
