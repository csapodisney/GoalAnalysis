"""Exact source locators are availability, never fabricated source-read evidence."""
from copy import deepcopy

import pytest
from test_agent_portfolio import inputs

from goal_analysis.agents.profile_input import SOURCE_DETAILS_POLICY, final_option_details
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest, json_text
from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence


def test_exact_all_producer_locators_preserve_raw_unknown_clocks_and_default_details():
    report, artifacts, enriched, club, _, _, proposals = inputs(owners=('kronikas',), variants=2)
    containers = {'candidate_input': artifacts['candidate_input'], 'enriched': enriched, 'club_enriched': club}
    for producer, container in containers.items():
        for row in container['candidates']:
            row['quoted_at'] = None
            row['optional_status'] = 'UNKNOWN'
            row['optional_value'] = None
        container['history_analysis'] = {'candidates': [{
            'candidate_id': row['candidate_id'], 'producer': producer,
            'quoted_at': None, 'observed_at': row['quote_observed_at'], 'status': 'UNKNOWN',
            'profiles': {'merlin': {'historical_20': {
                'count': 2, 'fixture_ids': ['original-a', 'original-b'], 'source_ids': ['original-source'],
                'missing_period_scores': [{'fixture_id': 'original-b', 'reason': 'UNKNOWN'}]}}}}
            for row in container['candidates']]}
        # Same source ID can have distinct producer history records. Do not merge them.
        duplicate = deepcopy(container['history_analysis']['candidates'][0])
        duplicate['producer_note'] = 'Distinct second source row, exact original order.'
        container['history_analysis']['candidates'].append(duplicate)
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=report['observed_at'], artifacts=artifacts, enriched=enriched, club_enriched=club)
    selected = proposals['kronikas'][0]['source_candidate_ids']
    options = [{'agent_id': 'kronikas', 'option_id': 'own-1',
                'legs': [deepcopy(row) for row in artifacts['candidate_input']['candidates']
                         if row['candidate_id'] in selected]}]
    original_snapshot, original_options = snapshot.payload_json, deepcopy(options)
    legacy = final_option_details(snapshot, 'kronikas', options)
    assert final_option_details(snapshot, 'kronikas', options, source_access=False) == legacy
    referenced = final_option_details(snapshot, 'kronikas', options, source_access=True)
    assert referenced['source_details_policy']['version'] == SOURCE_DETAILS_POLICY
    assert referenced['source_details_policy']['all_raw_rows_read_by_host'] is False
    for key in legacy:
        if key not in {'source_candidates_by_producer', 'history_features_by_producer'}:
            assert referenced[key] == legacy[key]
    raw = snapshot.data()
    for group, suffix in [('source_candidates_by_producer', ['candidates']),
                          ('history_features_by_producer', ['history_analysis', 'candidates'])]:
        assert set(referenced[group]) == set(legacy[group]) == set(containers)
        for producer, locators in referenced[group].items():
            expected = raw['families']['odds_and_price_history']['value'][producer]
            for key in suffix:
                expected = expected[key]
            expected = [row for row in expected if row['candidate_id'] in selected]
            restored = []
            for locator in locators:
                assert locator['namespace'] == 'raw' and locator['format'] == SOURCE_DETAILS_POLICY
                assert locator['path'][:4] == ['families', 'odds_and_price_history', 'value', producer]
                assert locator['path'][4:-1] == suffix
                value = raw
                for key in locator['path']:
                    value = value[key]
                assert digest(value) == locator['source_value_sha256']
                assert len(json_text(value)) == locator['total_chars']
                assert value['candidate_id'] == locator['candidate_id']
                assert value['quoted_at'] is None
                restored.append(value)
            assert restored == expected
            assert {row['candidate_id'] for row in restored} == set(selected)
            if group == 'source_candidates_by_producer':
                assert restored == legacy[group][producer]
            else:
                assert restored[-1]['producer_note'].startswith('Distinct second')
                assert restored[0]['profiles']['merlin']['historical_20']['fixture_ids'] == ['original-a', 'original-b']
    assert snapshot.payload_json == original_snapshot and options == original_options


def test_unknown_source_policy_or_locator_policy_without_private_source_transport_rejected():
    owner = {'status': 'DECISION_COMPLETED', 'decision_evidence': {
        'source_details_policy': 'UNREVIEWED_FORMAT', 'version': 'owner-decision-evidence-v3-source-access'}}
    with pytest.raises(ValueError, match='UNKNOWN_SOURCE_DETAILS_POLICY'):
        _owner_evidence(owner, None, {})
    owner['decision_evidence'].update(source_details_policy=SOURCE_DETAILS_POLICY,
        version='owner-decision-evidence-v2-compact-views')
    with pytest.raises(ValueError, match='SOURCE_DETAILS_REQUIRE_SOURCE_ACCESS'):
        _owner_evidence(owner, None, {})
