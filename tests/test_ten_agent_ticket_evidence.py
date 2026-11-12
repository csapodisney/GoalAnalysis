"""Pure saved-envelope fixtures; no host, collector, tool execution, DB or fit."""
from copy import deepcopy

import pytest
import test_agent_portfolio as product_fixture
import test_ten_agent_output_integration as output_fixture
import test_ticket_evidence as legacy_fixture

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
from goal_analysis.agents.profile_input import (
    decision_view,
    final_option_details,
    final_shared_reference,
    public_tool_results,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder, publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.ten_agent_ticket_evidence import attach_ten_agent_ticket_evidence
from goal_analysis.jobs.ticket_evidence import (
    _build_ticket_evidence,
    build_ticket_evidence,
    validate_ticket_evidence,
)
from goal_analysis.providers.the_stats_odds import bookmaker_key


def _native_product_frame():
    """Real pure constructors over explicit fixture values, not handbuilt club cards.

    The existing product fixture supplies legal model values and the Milan rise.
    Its unused first snapshot/builder are inert fixtures, not fits or tool calls.
    Milan uses receipt clocks, so every provider quote clock can remain UNKNOWN.
    """
    report, artifacts, enriched, club, _, _, proposals = product_fixture.inputs()
    report['run_id'] = 'TESTONLY-consumer-evidence'
    milan = {key for row in proposals['tipster_ertek_milan'] for key in row['source_candidate_ids']}
    renamed = {}
    for container in (artifacts['candidate_input'], enriched, club):
        for quote in container['candidates']:
            quote['quoted_at'] = None
            if quote['candidate_id'] not in milan:
                continue
            old = quote['fixture_id']
            number = old.split(':')[1]
            native = 'mt_' + number
            renamed[old] = 'the_stats_api:' + native
            quote.update(fixture_id=renamed[old], source_provider='the_stats_api',
                provider_fixture_id=native, provider_league_id='comp_1', provider_season_id='sn_1',
                provider_home_team_id='tm_' + str(int(number) * 2),
                provider_away_team_id='tm_' + str(int(number) * 2 + 1),
                quote_provider='the_stats_api', provider_bookmaker='TESTONLY',
                bookmaker=bookmaker_key('TESTONLY'),
                quote_source_id=f'https://api.thestatsapi.com/api/football/matches/{native}/odds',
                quote_time_basis='PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME')
            for key in ('api_football_fixture_id', 'api_football_home_team_id', 'api_football_away_team_id'):
                quote.pop(key)
            movement = quote['odds_movement']
            movement['baseline_observed_at'] = movement.pop('baseline_quoted_at')
            movement['current_observed_at'] = movement.pop('current_quoted_at')
            movement['identity'] = {key: quote[key] for key in movement['identity']}
    fields = ('fixture_id', 'source_provider', 'api_football_fixture_id', 'api_football_home_team_id',
        'api_football_away_team_id', 'provider_fixture_id', 'provider_league_id', 'provider_season_id',
        'provider_home_team_id', 'provider_away_team_id', 'home_team', 'away_team',
        'competition', 'competition_type', 'provider_status', 'kickoff')
    fixtures = [{key: quote[key] for key in fields if key in quote}
                for quote in artifacts['candidate_input']['candidates']]
    artifacts['fixtures']['records'] = deepcopy(fixtures)
    receipt = artifacts['calendar']['scope_receipt']
    receipt.update(provider_fixture_ids=[row['fixture_id'] for row in fixtures], events=deepcopy(fixtures))
    club['fixtures'] = deepcopy(fixtures)
    for model in artifacts['numeric_models'].values():
        for prediction in model['predictions']:
            prediction['fixture_id'] = renamed.get(prediction['fixture_id'], prediction['fixture_id'])
    artifacts['numeric_models']['gauss']['competition_results'] = {'TESTONLY league': {
        'training_datasets': {'dataset_id': 'TESTONLY-metadata-not-physical',
                              'cutoff': report['observed_at']}}}
    artifacts['candidate_analysis'] = enriched
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=report['observed_at'], artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, report['settings'], output_fixture.NOW)
    options = {owner: [builder(owner, proposal, snapshot) for proposal in proposals[owner]] for owner in AGENT_IDS}
    return report, artifacts, snapshot, options


def saved_frame(*, scenario_available=False):
    report, artifacts, snapshot, own_options = _native_product_frame()
    data, owners = snapshot.data(), []
    view = decision_view(snapshot)
    view_hash = digest(view)
    final_shared_hash = digest(final_shared_reference(snapshot, view))
    states = start_sessions(snapshot, {key: get_agent_definition(key).method_version for key in AGENT_IDS})
    tools = ProfileToolService(snapshot)  # Envelope creation only; evaluate() is never called.
    for state in states:
        options = own_options[state.agent_id]
        identity = {key: getattr(state, key) for key in ('agent_id', 'session_id', 'snapshot_id')}
        plan = {**identity, 'phase': 'PLAN', 'options': [{
            'option_id': row['option_id'], 'reason': row['reason'],
            'source_candidate_ids': row['canonical_card']['agent_product_binding']['source_candidate_ids']}
            for row in options], 'fixture_reviews': [{'fixture_id': key,
                'reason': 'TESTONLY full-calendar declared consideration', 'rejected': False}
                for key in data['fixture_ids']], 'tool_requests': [
                    {'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
        state = set_options(state, snapshot, options)
        for review in plan['fixture_reviews']:
            state = consider_fixture(state, snapshot, review['fixture_id'], reason=review['reason'])
        pairs, interpretations = [], []
        for role in SIMULATOR_ROLES:
            state, request = request_tool(state, snapshot, role)
            payload = request.data()
            result = tools._unknown(payload, 'TESTONLY historical evaluation unavailable; not zero ROI')
            if scenario_available and role == 'candidate_scenarios':
                result = tools._available(payload, {'TESTONLY': True, 'recorded_value': .123,
                    'assumption': 'Fixture number, not a fresh simulation or forecast'},
                    {'source_manifest_sha256': digest(data['source_manifest'])})
            interpretation = 'TESTONLY own interpretation; UNKNOWN remains a limitation'
            pairs.append({'request': payload, 'result': deepcopy(result)})
            interpretations.append({'request_id': payload['request_id'], 'interpretation': interpretation})
            state = record_tool_result(state, snapshot, request, result, interpretation=interpretation)
        uses = []
        for name, family in data['families'].items():
            use = {'family': name, 'status': 'AVAILABLE_AND_CONSIDERED'
                   if family['status'] == 'AVAILABLE' else 'MISSING',
                   'reason': 'TESTONLY declared source consideration, not causal proof',
                   'value_sha256': family['value_sha256'],
                   'option_ids': [row['option_id'] for row in options], 'risk': None}
            state = consider_family(state, snapshot, name, **{key: value for key, value in use.items()
                                                              if key != 'family'})
            uses.append(use)
        final = {**identity, 'phase': 'FINAL', 'option_ids': [row['option_id'] for row in options],
            'reason': 'TESTONLY own final choice', 'family_uses': uses, 'tool_interpretations': interpretations}
        _, decision = finalize_decisions(state, snapshot, option_ids=final['option_ids'], reason=final['reason'])
        owners.append({**identity, 'status': 'DECISION_COMPLETED', 'decision': decision.data(),
            'decision_evidence': {'version': 'owner-decision-evidence-v2-compact-views',
                'snapshot_id': snapshot.snapshot_id, 'input_views': {
                    'PLAN': {'kind': 'profile_input.decision_view', 'sha256': view_hash},
                    'FINAL': {'kind': 'profile_input.final_shared_reference+own_details',
                        'shared_sha256': final_shared_hash,
                        'own_details_sha256': digest(final_option_details(snapshot, state.agent_id, options)),
                        'public_tools_sha256': digest(public_tool_results(pairs))}}, 'plan': plan,
                'validated_own_options': options, 'tool_results': pairs, 'final': final}})
    result = {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot.snapshot_id,
        'agent_count': 10, 'status': 'COMPLETE', 'owners': owners, 'canonical_ticket_count': None}
    artifacts['independent_agent_snapshot'] = data
    artifacts['independent_agent_decisions'] = deepcopy(result)
    publish_agent_decisions(report, result, report['settings'], output_fixture.NOW)
    return report, artifacts


def test_whole_canonical_cohort_and_original_quote_model_sources_are_bound_without_mutation():
    report, artifacts = saved_frame()
    report['tickets'] = deepcopy(report['generated_tickets'])  # An audit never counts compatibility copies twice.
    before = deepcopy((report, artifacts))
    audit = build_ticket_evidence(report, artifacts)
    assert (report, artifacts) == before and audit['status'] == 'COMPLETE'
    assert audit['accounting']['canonical_card_count'] == audit['accounting']['audited_card_count'] == 20
    assert [row['location'] for row in audit['cards']] == [['generated_tickets', index] for index in range(20)]
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND' for row in audit['cards'])
    assert all(row['owner_evidence']['family_count'] == 12 for row in audit['cards'])
    for row in audit['cards']:
        assert all(leg['sections']['odds']['status'] == 'SELECTED_QUOTE_BOUND' for leg in row['legs'])
        assert all(leg['sections']['odds']['provider_quote_clock_known'] is False for leg in row['legs'])
        models = row['snapshot_sources']['admitted_selected_fixture_models']
        assert models and any('TESTONLY league/training_datasets' in source['producer_path']
                             for model in models for source in model['sources'])
        assert row['snapshot_sources']['historical_pit_or_profit_certification'] is False
    validate_ticket_evidence(audit, report, artifacts)


@pytest.mark.parametrize('defect', ['missing_envelope', 'tool_value', 'family_hash'])
def test_one_broken_owner_evidence_is_local_and_other_owners_remain_bound(defect):
    report, artifacts = saved_frame(scenario_available=True)
    for run in (report['ten_agent_decisions'], artifacts['independent_agent_decisions']):
        owner = run['owners'][0]
        if defect == 'missing_envelope':
            owner.pop('decision_evidence')
        elif defect == 'tool_value':
            owner['decision_evidence']['tool_results'][0]['result']['value']['recorded_value'] = .999
        else:
            owner['decision_evidence']['final']['family_uses'][0]['value_sha256'] = '0' * 64
    audit = build_ticket_evidence(report, artifacts)
    assert audit['status'] == 'PARTIAL' and len(audit['cards']) == 20
    assert all(row['owner_evidence']['status'] == 'UNKNOWN' for row in audit['cards'][:2])
    assert all(row['owner_evidence']['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for row in audit['cards'][2:])


def test_recorded_scenario_value_and_unknown_historical_receipt_are_not_reexecuted_or_promoted():
    report, artifacts = saved_frame(scenario_available=True)
    audit = build_ticket_evidence(report, artifacts)
    assert audit['new_tool_or_model_executions'] == audit['external_requests'] == 0
    receipts = report['generated_tickets'][0]['agent_decision']['tool_receipts']
    assert receipts[0]['value']['recorded_value'] == .123
    assert receipts[1]['status'] == 'UNKNOWN' and receipts[1]['value'] is None
    assert audit['cards'][0]['snapshot_sources']['selected_numeric_influence'].startswith('NOT_ESTABLISHED')
    assert audit['cards'][0]['owner_evidence']['consideration_semantics'] == 'MODEL_DECLARED_NOT_CAUSAL_FEATURE_INFLUENCE'


def test_missing_saved_snapshot_keeps_every_canonical_card_accounted_with_unknown_binding():
    report, artifacts = saved_frame()
    artifacts.pop('independent_agent_snapshot')
    audit = build_ticket_evidence(report, artifacts)
    assert audit['status'] == 'PARTIAL' and audit['accounting']['all_card_locations_accounted']
    assert len(audit['cards']) == 20 and all(row['own_selection']['status'] == 'UNKNOWN' for row in audit['cards'])


def test_changed_card_is_local_and_rebinding_rejects_a_previous_audit():
    report, artifacts = saved_frame()
    original = build_ticket_evidence(report, artifacts)
    report['generated_tickets'][0]['legs'][0]['decimal_price'] = 9
    changed = build_ticket_evidence(report, artifacts)
    assert changed['cards'][0]['own_selection']['status'] == 'UNKNOWN'
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND' for row in changed['cards'][1:])
    with pytest.raises(ValueError, match='differs from frozen source'):
        validate_ticket_evidence(original, report, artifacts)


def test_attachment_is_additive_and_legacy_auditor_output_is_unchanged():
    report, artifacts = saved_frame()
    before = deepcopy((report, artifacts))
    result = attach_ten_agent_ticket_evidence(report, artifacts)
    assert result is report['ticket_evidence']
    assert {key: value for key, value in report.items() if key != 'ticket_evidence'} == before[0]
    assert artifacts == before[1]
    old_report, old_artifacts = legacy_fixture.sample()
    assert build_ticket_evidence(old_report, old_artifacts) == _build_ticket_evidence(old_report, old_artifacts)


def test_shared_source_checks_and_existing_join_builder_run_once_for_whole_cohort(monkeypatch):
    import goal_analysis.jobs.ten_agent_ticket_evidence as agent_audit
    import goal_analysis.jobs.ticket_evidence as legacy_audit

    report, artifacts = saved_frame()
    calls = {'legacy': 0, 'shared_sources': 0}
    original_join, original_sources = legacy_audit._build_ticket_evidence, agent_audit._source_context

    def one_join(*args, **kwargs):
        calls['legacy'] += 1
        assert kwargs == {'groups': ('generated_tickets',)}
        assert len(args[0]['generated_tickets']) == 20
        return original_join(*args, **kwargs)

    def one_sources(data):
        calls['shared_sources'] += 1
        return original_sources(data)

    monkeypatch.setattr(legacy_audit, '_build_ticket_evidence', one_join)
    monkeypatch.setattr(agent_audit, '_source_context', one_sources)
    audit = build_ticket_evidence(report, artifacts)
    assert audit['status'] == 'COMPLETE' and len(audit['cards']) == 20
    assert calls == {'legacy': 1, 'shared_sources': 1}
    assert audit['legacy_source_join_mode'] == 'ONE_FULL_CANONICAL_COHORT'


def test_bad_optional_card_join_falls_back_locally_without_losing_other_cards():
    report, artifacts = saved_frame()
    report['generated_tickets'][0]['expert_ranking'] = {'the_stats_api': ['malformed optional metadata']}
    audit = build_ticket_evidence(report, artifacts)
    assert audit['legacy_source_join_mode'] == 'PER_CARD_FALLBACK_AFTER_SHARED_JOIN_ERROR'
    assert audit['status'] == 'PARTIAL' and len(audit['cards']) == 20
    assert audit['cards'][0]['source_joins']['status'] == 'UNKNOWN'
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND'
               and 'source_joins' not in row for row in audit['cards'][1:])


def test_corrupt_optional_family_source_is_local_to_that_family():
    from goal_analysis.jobs.ten_agent_ticket_evidence import _snapshot_links, _source_context

    report, artifacts = saved_frame()
    data = deepcopy(artifacts['independent_agent_snapshot'])
    data['families']['sourced_news']['source_refs'].append('TESTONLY-missing-source-reference')
    source_context = _source_context(data)
    links = _snapshot_links(report['generated_tickets'][0], data, source_context)
    assert links['families']['sourced_news']['status'] == 'UNKNOWN'
    assert links['families']['numeric_models']['source_binding'] == 'SHARED_VALUE_HASH_AND_REFERENCES_BOUND'
    assert links['admitted_selected_fixture_models']
