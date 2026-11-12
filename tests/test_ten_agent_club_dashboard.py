"""TESTONLY saved club projection; no constructor, SQLite, provider or host."""

from copy import deepcopy

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.config.portfolio import validate_settings
from goal_analysis.portfolio_dashboard import ArthurState
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS


def _report():
    cards = [{
        'ticket_id': f'TESTONLY-{owner}-{variant}', 'profile_id': owner,
        'agent_option_id': f'{owner}-variant-{variant}', 'date': '2026-11-12',
        'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
        'combined_price': 16.0, 'legs': [{'fixture_id': f'{owner}-{variant}'}],
    } for owner in AGENT_IDS for variant in range(2)]
    return {
        'ten_agent_decisions': {'version': 'ten-agent-decision-run-v1', 'agent_count': 10},
        'generated_tickets': cards,
        'profiles': [{'profile_id': owner, 'status': 'COMPLETE', 'ticket_count': 2}
                     for owner in AGENT_IDS],
    }


def test_all_ten_owners_keep_every_saved_variant_and_inputs_are_detached():
    report = _report()
    settings = validate_settings({})
    original = deepcopy((report, settings))
    view = ArthurState.tipster_snapshot(settings, [report], [], {})
    assert [row['profile_id'] for row in view['catalogue']] == list(AGENT_IDS)
    assert len(view['recommendations']) == 20
    assert [row['ticket_id'] for row in view['recommendations']] == [
        row['ticket_id'] for row in report['generated_tickets']
    ]
    assert all(row['status'] == 'DRAFT' and row['playable'] is False
               for row in view['recommendations'])
    assert view['max_daily'] == 30 and view['agent_count'] == 10
    assert view['decision_mode'] == 'TEN_INDEPENDENT_OWNERS'
    assert view['independent_models'] is False
    view['recommendations'][0]['legs'].clear()
    view['owner_states'][0]['status'] = 'FORGED'
    assert (report, settings) == original


def test_empty_canonical_agent_output_never_resurrects_legacy_recommendations():
    report = _report()
    stale = deepcopy(report['generated_tickets'])
    report['generated_tickets'] = []
    report['recommendations'] = stale
    view = ArthurState.tipster_snapshot(validate_settings({}), [report], stale, {})
    assert view['recommendations'] == []
    assert view['overlap'] == []
    assert len(view['catalogue']) == 10


def test_missing_agent_cards_preserve_separate_abstain_incomplete_and_no_card_states():
    report = _report()
    report['generated_tickets'] = []
    report['profiles'] = [
        {'profile_id': AGENT_IDS[0], 'status': 'ABSTAINED', 'reason': 'No own option'},
        {'profile_id': AGENT_IDS[1], 'status': 'PARTIAL', 'reason': 'Host interrupted'},
        {'profile_id': AGENT_IDS[2], 'status': 'NO_COMPLIANT_TICKET',
         'reason': 'Final canonical option rejected'},
    ]
    view = ArthurState.tipster_snapshot(validate_settings({}), [report], [], {})
    assert view['owner_states'] == report['profiles']
    assert view['recommendations'] == []
    assert len(view['owner_states']) == 3  # No fabricated completion for missing owners.


def test_agent_projection_preserves_human_settings_and_filters_only_displayed_owners():
    report = _report()
    settings = validate_settings({'enabled_profiles': [AGENT_IDS[0], 'daily223', 'gauss']})
    before = deepcopy(settings)
    outsider = next(owner for owner in TIPSTER_PROFILE_IDS if owner not in AGENT_IDS)
    report['generated_tickets'].append({'profile_id': outsider, 'legs': []})
    report['profiles'].append({'profile_id': outsider, 'status': 'COMPLETE'})
    team = {'members': [{'profile_id': AGENT_IDS[0], 'status': 'PROBATION'},
                        {'profile_id': outsider, 'status': 'ACTIVE'}]}
    view = ArthurState.tipster_snapshot(settings, [report], [], team)
    assert view['enabled_profiles'] == [AGENT_IDS[0]]
    assert len(view['recommendations']) == 20
    assert len(view['owner_states']) == 10
    assert view['members'] == [team['members'][0]]
    view['members'][0]['status'] = 'FORGED'
    assert team['members'][0]['status'] == 'PROBATION'
    assert settings == before


def test_legacy_club_keeps_twenty_roster_and_original_profile_projection():
    owner = TIPSTER_PROFILE_IDS[0]
    first = {'profile_id': owner, 'recommendation_id': 'original',
             'legs': [{'fixture_id': 'first'}]}
    last = {'profile_id': owner, 'recommendation_id': 'last',
            'legs': [{'fixture_id': 'last'}]}
    report = {'recommendations': [{'profile_id': owner, 'status': 'ABSTAINED', 'legs': []}]}
    view = ArthurState.tipster_snapshot(validate_settings({}), [report], [first, last], {})
    assert len(view['catalogue']) == 20
    assert view['recommendations'] == [last]
    assert 'decision_mode' not in view and view['max_daily'] == 20


def test_unknown_agent_marker_is_not_silently_reclassified_as_ten_agent_mode():
    report = _report()
    report['ten_agent_decisions']['version'] = 'unsupported-future-version'
    view = ArthurState.tipster_snapshot(validate_settings({}), [report], [], {})
    assert len(view['catalogue']) == 20
    assert 'decision_mode' not in view
