"""Pure exact guard projection; no host, provider, filesystem, DB or model."""
from dataclasses import FrozenInstanceError

import pytest

from goal_analysis.agents.profile_decision import (
    SIMULATOR_ROLES,
    consider_family,
    consider_fixture,
    finalize_decisions,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    SharedSnapshot,
    build_snapshot,
    digest,
    json_text,
)


def snapshot_fixture():
    fixtures = [{'fixture_id': 'TESTONLY-f1'}, {'fixture_id': 'TESTONLY-f2'}]
    ids = [row['fixture_id'] for row in fixtures]
    return build_snapshot(run_id='TESTONLY-cache', target_date='2026-11-13',
        as_of='2026-11-12T18:00:00+00:00', authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-13', 'scope': 'FULL_PROVIDER_DAY',
            'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY:source']},
        fixtures=fixtures, family_values={'team_stats_xg': {'status': 'AVAILABLE',
            'value': {'exact_xg': 1.7, 'unnecessary_guard_blob': 'x' * 100_000},
            'source_refs': ['TESTONLY:source']}}, source_manifest={'TESTONLY:source': {'kind': 'TESTONLY'}},
        model_probabilities={'TESTONLY-p': {'fixture_id': ids[0], 'model_id': 'gauss',
            'market': 'FULL_TIME:h2h', 'outcome': 'home', 'value': .6, 'source_refs': ['TESTONLY:source']}})


def test_decision_projection_is_exact_detached_and_full_snapshot_is_unchanged():
    snapshot = snapshot_fixture()
    before, full, decision = snapshot.payload_json, snapshot.data(), snapshot.decision_data()
    assert set(decision) == {'owner_ids', 'fixture_ids', 'as_of', 'model_probabilities', 'families'}
    for key in ('owner_ids', 'fixture_ids', 'as_of', 'model_probabilities'):
        assert decision[key] == full[key]
    assert decision['families'] == {name: {key: value[key] for key in ('status', 'value_sha256')}
                                    for name, value in full['families'].items()}
    assert 'unnecessary_guard_blob' not in json_text(decision)
    decision['owner_ids'].clear()
    decision['families']['team_stats_xg']['value_sha256'] = 'f' * 64
    decision['model_probabilities']['TESTONLY-p']['value'] = .9
    full['families']['team_stats_xg']['value']['exact_xg'] = 99
    again = snapshot.decision_data()
    assert again['owner_ids'] == list(AGENT_IDS)
    assert again['model_probabilities']['TESTONLY-p']['value'] == .6
    assert snapshot.data()['families']['team_stats_xg']['value']['exact_xg'] == 1.7
    assert snapshot.payload_json == before and digest(snapshot.data()) == snapshot.snapshot_id
    assert snapshot.fixture_ids == ('TESTONLY-f1', 'TESTONLY-f2')


def test_cache_is_not_injectable_and_whole_hash_is_checked_before_projection():
    snapshot = snapshot_fixture()
    with pytest.raises(TypeError):
        SharedSnapshot(snapshot.snapshot_id, snapshot.payload_json, _decision_json='{}')
    with pytest.raises(FrozenInstanceError):
        snapshot._decision_json = '{}'
    with pytest.raises(ValueError, match='SNAPSHOT_HASH_MISMATCH'):
        SharedSnapshot(snapshot.snapshot_id, json_text({'deliberately_missing_cache_fields': True}))
    changed = snapshot.data()
    changed['families']['team_stats_xg']['value']['exact_xg'] = 9
    with pytest.raises(ValueError, match='SNAPSHOT_HASH_MISMATCH'):
        SharedSnapshot(snapshot.snapshot_id, json_text(changed))
    assert '_decision_json' not in repr(snapshot) and '_fixture_ids' not in repr(snapshot)


def test_full_owner_decision_guards_use_cache_without_full_data_parse_or_weaker_checks(monkeypatch):
    snapshot = snapshot_fixture()
    guard_data = snapshot.decision_data()

    def forbidden_full_read(self):
        raise AssertionError('Guard-only transition must not parse the full payload')

    monkeypatch.setattr(SharedSnapshot, 'data', forbidden_full_read)
    state = start_sessions(snapshot, {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})[0]
    leg = {'fixture_id': 'TESTONLY-f1', 'market': 'FULL_TIME:h2h', 'outcome': 'home',
           'model_probability_id': 'TESTONLY-p', 'model_probability': .6}
    option = {'option_id': 'TESTONLY-own', 'agent_id': state.agent_id, 'legs': [leg]}
    with pytest.raises(ValueError, match='MODEL_P_CHANGED_OR_INVENTED'):
        set_options(state, snapshot, [{**option, 'legs': [{**leg, 'model_probability': .9}]}])
    state = set_options(state, snapshot, [option])
    for fixture_id in snapshot.fixture_ids:
        state = consider_fixture(state, snapshot, fixture_id, reason='TESTONLY full-calendar decision.')
    for family in FAMILIES:
        source = guard_data['families'][family]
        with pytest.raises(ValueError, match='STALE_FAMILY_VALUE'):
            consider_family(state, snapshot, family, status='UNUSABLE_FOR_THIS_METHOD',
                reason='TESTONLY forged hash rejected', value_sha256='f' * 64)
        state = consider_family(state, snapshot, family,
            status='AVAILABLE_AND_CONSIDERED' if source['status'] == 'AVAILABLE' else 'MISSING',
            reason='TESTONLY source state considered.', value_sha256=source['value_sha256'],
            option_ids=('TESTONLY-own',) if source['status'] == 'AVAILABLE' else ())
    for role in SIMULATOR_ROLES:
        state, request = request_tool(state, snapshot, role)
        payload = request.data()
        result = {key: payload[key] for key in ('request_id', 'agent_id', 'session_id',
                                               'snapshot_id', 'option_hash', 'own_option_ids')}
        result.update(status='UNKNOWN', value=None, reason='TESTONLY no simulation was executed.')
        state = record_tool_result(state, snapshot, request, result, interpretation='Explicit uncertainty retained.')
    _, decision = finalize_decisions(state, snapshot, option_ids=('TESTONLY-own',),
                                    reason='TESTONLY complete contract, not a profitable wager claim.')
    assert decision.data()['option_ids'] == ['TESTONLY-own']
    assert set(decision.data()['family_uses']) == set(FAMILIES)
