"""Transport failure must never masquerade as an explicit recommendation withdrawal."""
from copy import deepcopy

import pytest

from goal_analysis.storage.recommendation_ledger import abstention_profiles


@pytest.mark.parametrize('status', ['DATA_BLOCKED', 'FAILED', 'ERROR', 'UNAVAILABLE', 'TEAM_PAUSED'])
@pytest.mark.parametrize('field', ['status', 'construction_status'])
def test_failed_run_does_not_withdraw_previous_recommendations(status, field):
    value = {field: status, 'recommendations': [
        {'profile_id': 'gauss', 'status': 'ABSTAINED', 'legs': []}]}
    assert abstention_profiles(value) == set()


def test_only_explicit_empty_leg_abstention_withdraws_and_input_is_unchanged():
    value = {'recommendations': [
        {'profile_id': 'gauss', 'status': 'ABSTAINED', 'legs': []},
        {'profile_id': 'laplace', 'status': 'UNAVAILABLE', 'legs': []},
        {'profile_id': 'kepler', 'status': 'ABSTAINED'},
        {'profile_id': 'daily223', 'status': 'ABSTAINED', 'legs': None},
        {'profile_id': '', 'status': 'ABSTAINED', 'legs': []},
        None,
    ]}
    original = deepcopy(value)
    assert abstention_profiles(value) == {'gauss'}
    assert value == original


def test_selected_profile_wins_over_conflicting_abstention_in_same_run():
    value = {'recommendations': [
        {'profile_id': 'gauss', 'status': 'ABSTAINED', 'legs': []},
        {'profile_id': 'gauss', 'status': 'READY', 'legs': [{'fixture_id': 'mt_1'}]},
    ]}
    assert abstention_profiles(value) == set()


@pytest.mark.parametrize('records', [None, {}, 'ABSTAINED'])
def test_malformed_recommendation_container_is_not_a_withdrawal(records):
    assert abstention_profiles({'recommendations': records}) == set()
