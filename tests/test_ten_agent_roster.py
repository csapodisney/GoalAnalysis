"""New ten-owner boundaries; no host, DB, provider, fit or generation calls."""
from dataclasses import FrozenInstanceError, replace

import pytest

from goal_analysis.agents.profile_roster import (
    ACTIVE_AGENTS,
    AGENT_COUNT,
    AGENT_IDS,
    DATA_ACCESS_POLICY,
    get_agent_definition,
)


def test_ten_owner_registry_retains_selected_identities_and_distinct_methods():
    assert AGENT_COUNT == 10
    assert AGENT_IDS == (
        'kronikas', 'ritmusor', 'parharcmester', 'orszem', 'merlin',
        'tipster_archiv_nora', 'tipster_forma_soma', 'tipster_ertek_milan',
        'tipster_szines_reka', 'tipster_fek_tade',
    )
    assert len({agent.method_version for agent in ACTIVE_AGENTS}) == AGENT_COUNT
    assert len({agent.selection_path for agent in ACTIVE_AGENTS}) == AGENT_COUNT
    assert all(get_agent_definition(agent.agent_id) is agent for agent in ACTIVE_AGENTS)


def test_focus_never_restricts_any_agents_full_calendar_or_shared_data():
    for agent in ACTIVE_AGENTS:
        value = agent.as_dict()
        assert value['data_access_policy'] == DATA_ACCESS_POLICY
        assert DATA_ACCESS_POLICY == 'FULL_DAILY_CALENDAR_AND_ALL_SHARED_FAMILIES'
        assert value['focus_restricts_data_access'] is False
        assert value['method_version_scope'] == 'ORCHESTRATION_NOT_BASELINE_MATH'
        assert value['profit_validation'] == 'NOT_ESTABLISHED_BY_ROSTER'
        assert len(value['selection_path']) >= 4


def test_maximum_variants_is_capacity_and_not_a_minimum_generation_quota():
    assert all(agent.max_variants == 3 for agent in ACTIVE_AGENTS)
    assert sum(agent.max_variants for agent in ACTIVE_AGENTS) == 30
    assert all('minimum_variants' not in agent.as_dict() for agent in ACTIVE_AGENTS)


def test_definition_cannot_be_mutated_across_owners_or_calls():
    agent = get_agent_definition('kronikas')
    with pytest.raises(FrozenInstanceError):
        agent.name = 'Changed'
    with pytest.raises(TypeError):
        agent.selection_path[0] = 'Changed'
    assert get_agent_definition('kronikas').name == 'Krónikás'


def test_prompt_dictionary_is_detached_including_nested_lists():
    agent = get_agent_definition('tipster_archiv_nora')
    original = agent.as_dict()
    changed = agent.as_dict()
    changed['selection_path'].clear()
    changed['focus_families'].append('invented_family')
    changed['agent_id'] = 'kronikas'
    assert agent.as_dict() == original


@pytest.mark.parametrize('profile_id', ['daily223', 'gauss', 'tipster_csendes_mira', 'unknown'])
def test_inactive_legacy_or_model_identity_cannot_acquire_active_agent_definition(profile_id):
    with pytest.raises(KeyError, match='UNKNOWN_ACTIVE_AGENT'):
        get_agent_definition(profile_id)


def test_mutable_definition_sequences_are_rejected():
    agent = get_agent_definition('orszem')
    with pytest.raises(ValueError, match='IMMUTABLE_DISTINCT'):
        replace(agent, selection_path=list(agent.selection_path))
    with pytest.raises(ValueError, match='IMMUTABLE_DISTINCT'):
        replace(agent, focus_families=list(agent.focus_families))


@pytest.mark.parametrize('capacity', [True, 0, 4])
def test_variants_reject_bool_zero_and_unbounded_capacity(capacity):
    with pytest.raises(ValueError, match='ONE_TO_THREE'):
        replace(get_agent_definition('merlin'), max_variants=capacity)
