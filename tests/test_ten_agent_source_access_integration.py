"""TESTONLY kernel reads and simulated CLI events; no CLI, provider or inference."""
from copy import deepcopy

import pytest
import test_agent_portfolio as product_fixture
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_source_host import SourceAccessReply
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor, make_normal_agent_executor
from goal_analysis.jobs.ticket_evidence import build_ticket_evidence


def _normal(tmp_path, *, fault=None):
    _, artifacts, enriched, club, _, _, proposals = product_fixture.inputs(
        owners=('kronikas', 'ritmusor'), variants=1)
    calls = []

    def host(request, *, timeout, source_access):
        assert timeout > 0
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        data = request['data']
        if request['phase'] == 'PLAN':
            own = deepcopy(proposals['kronikas']) if request['agent_id'] == 'kronikas' else []
            answer = {**identity, 'options': own,
                'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY entire supplied calendar',
                    'rejected': not bool(own)} for key in data['snapshot']['fixture_ids']],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
            candidate_ids = [key for option in own for key in option['source_candidate_ids']]
            if fault == 'unread' and own:
                candidate_ids = []
        else:
            selected = data['own_option_admission']['accepted_option_ids']
            answer = {**identity, 'option_ids': selected, 'reason': 'TESTONLY own choice or honest abstention',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY supplied result; UNKNOWN remains uncertainty'}
                    for row in data['tool_results']],
                'family_uses': [{'family': key, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                    'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                    'value_sha256': family['value_sha256'], 'option_ids': selected,
                    'reason': 'TESTONLY declared interpretation, not causal feature proof',
                    'risk': 'TESTONLY missing data never becomes neutral'}
                    for key, family in data['snapshot']['families'].items()]}
            candidate_ids = []
        answer['host_usage'] = {'requests': 1, 'usage_complete': False, 'cost_amount': None,
                                'reason': 'TESTONLY fake host, no real usage claimed'}
        reads = list(candidate_ids)
        if fault == 'partial' and identity['agent_id'] == 'kronikas' and reads:
            reads[0] = {**identity, 'namespace': 'candidates', 'path': [reads[0]],
                'mode': 'text', 'offset': 0, 'limit': 1}
        returned = finish_fake_source_reply(source_access, request, reads, answer)
        if identity['agent_id'] == 'kronikas' and identity['phase'] == 'PLAN':
            if fault == 'foreign_phase':
                proof = returned.evidence()
                proof['binding']['phase'] = 'FINAL'
                returned = SourceAccessReply.create(returned.reply(), proof)
            elif fault == 'raw_reply':
                return returned.reply()
        return returned

    executor = NormalAgentExecutor(host=host, journal_root=tmp_path / 'journals',
        clock=lambda: product_fixture.NOW, enable_source_access=True)
    report = executor(run_id='TESTONLY-source-access-run', target_date=product_fixture.NOW.date(),
        as_of=product_fixture.NOW, artifacts=artifacts,
        fixture_records=artifacts['fixtures']['records'], enriched=enriched, club_enriched=club,
        candidate_context={}, weather_by_fixture={}, historical_runs=[], feedback={},
        settings=product_fixture.SETTINGS, team_plan={})
    return report, artifacts, calls


def test_ten_owners_share_one_full_day_bundle_and_bind_real_kernel_reads_without_fillers(tmp_path):
    report, artifacts, calls = _normal(tmp_path)
    assert len(calls) == 20 and {row['agent_id'] for row in calls} == set(AGENT_IDS)
    assert report['ten_agent_decisions']['status'] == 'COMPLETE'
    assert len(report['generated_tickets']) == 1 and report['generation_capacity']['shortfall'] == 14
    assert report['construction_status'] == 'PARTIAL'
    assert len(list((tmp_path / 'journals').rglob('bundle.json'))) == 1
    assert len(list((tmp_path / 'journals').rglob('*.source-access.json'))) == 20
    full_ids = artifacts['calendar']['scope_receipt']['provider_fixture_ids']
    for request in calls:
        assert request['data']['snapshot']['fixture_ids'] == full_ids
        assert len(request['data']['snapshot']['families']) == 12
    for owner in report['ten_agent_decisions']['owners']:
        evidence = owner['decision_evidence']
        assert evidence['version'] == 'owner-decision-evidence-v3-source-access'
        assert evidence['source_access']['FINAL']['receipt']['successful_reads'] == 0
        if owner['agent_id'] != 'kronikas':
            assert owner['decision']['status'] == 'ABSTAIN'
            assert evidence['source_access']['PLAN']['receipt']['successful_reads'] == 0
    audit = build_ticket_evidence(report, artifacts)
    checked = audit['owner_checks']['kronikas']
    assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
    assert checked['source_access_validation']['PLAN']['read_count'] >= 4
    assert checked['source_access_validation']['FINAL']['read_count'] == 0
    assert checked['source_access_validation']['PLAN']['causal_feature_use_proven'] is False


@pytest.mark.parametrize('fault', ['unread', 'partial', 'foreign_phase', 'raw_reply'])
def test_unread_or_untrusted_source_phase_stops_only_its_owner_before_final(tmp_path, fault):
    report, _, calls = _normal(tmp_path, fault=fault)
    own = report['ten_agent_decisions']['owners']
    assert own[0]['agent_id'] == 'kronikas' and own[0]['status'] == 'INCOMPLETE_NO_RETRY'
    assert all(row['status'] == 'DECISION_COMPLETED' for row in own[1:])
    assert [row['phase'] for row in calls if row['agent_id'] == 'kronikas'] == ['PLAN']
    assert report['generated_tickets'] == []


def test_saved_source_proof_tampering_is_local_unknown_without_new_reads(tmp_path):
    report, artifacts, calls = _normal(tmp_path)
    count = len(calls)
    for run in (report['ten_agent_decisions'], artifacts['independent_agent_decisions']):
        run['owners'][0]['decision_evidence']['source_access']['PLAN']['request_sha256'] = '0' * 64
    audit = build_ticket_evidence(report, artifacts)
    assert audit['owner_checks']['kronikas']['status'] == 'UNKNOWN' and len(calls) == count
    assert all(row['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for owner, row in audit['owner_checks'].items() if owner != 'kronikas')


def test_missing_one_owner_evidence_keeps_the_other_nine_source_checks(tmp_path):
    report, artifacts, calls = _normal(tmp_path)
    count = len(calls)
    damaged_owner = report['ten_agent_decisions']['owners'][1]['agent_id']
    for run in (report['ten_agent_decisions'], artifacts['independent_agent_decisions']):
        run['owners'][1]['decision_evidence'] = None
    audit = build_ticket_evidence(report, artifacts)
    assert audit['snapshot_binding']['status'] == 'SAVED_SNAPSHOT_BOUND'
    assert audit['owner_checks'][damaged_owner]['status'] == 'UNKNOWN'
    assert len(audit['owner_checks']) == 10 and len(calls) == count
    assert all(row['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for owner, row in audit['owner_checks'].items() if owner != damaged_owner)
    assert audit['cards'][0]['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND'


def test_only_real_factory_opts_in_while_injected_executor_stays_inline(tmp_path):
    direct = NormalAgentExecutor(host=lambda *args, **kwargs: pytest.fail('no host'),
        journal_root=tmp_path, clock=lambda: product_fixture.NOW)
    assert direct.enable_source_access is False
    settings = {**deepcopy(product_fixture.SETTINGS), 'openai': {
        'model': 'TESTONLY-not-used', 'reasoning_effort': 'high', 'max_output_tokens': 1}}
    factory = make_normal_agent_executor(tmp_path, settings, clock=lambda: product_fixture.NOW)
    assert factory.enable_source_access is True and list(tmp_path.iterdir()) == []
