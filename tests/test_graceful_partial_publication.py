"""New TESTONLY real product/publisher/file-write regression; no model or DB."""
import hashlib
import json
import math
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from test_agent_portfolio import NOW, SETTINGS, inputs
from test_final_multioption_projection import _restore
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents import (
    profile_decision_runner,
    profile_source_access,
    profile_source_stdio,
)
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_input import decision_view
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_source_access import SourceBundle
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder, publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.portfolio import write_portfolio_bundle
from goal_analysis.ticket_product_policy import product_contract, product_failure


def test_completed_own_products_publish_and_save_when_remaining_nine_owners_expire(tmp_path, monkeypatch):
    report, artifacts, enriched, club, _, _, _ = inputs(owners=('kronikas',), variants=2)
    report['ticket_product_contract'] = product_contract()
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    original_sources = digest((artifacts, enriched, club, snapshot.data()))
    view = decision_view(snapshot)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    source_root = tmp_path / 'source'
    source_root.mkdir()
    bundle_path = source_root / 'bundle.json'
    bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
    clock, calls, proposed = [NOW], [], []

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        assert request['agent_id'] == 'kronikas' and 0 < timeout <= 600
        data = request['data'] if request['phase'] == 'PLAN' else _restore(request['data'])
        if request['phase'] == 'PLAN':
            assert data['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
            assert len(snapshot.fixture_ids) == 8
            assert data['own_construction_contract']['ticket_product_contract'] == product_contract()
            seeds = data['own_strategy_search']['suggestions']
            assert len(seeds) >= 2
            proposed.extend({'option_id': row['search_option_id'],
                'source_candidate_ids': list(row['source_candidate_ids']),
                'reason': f'TESTONLY own ranked legal variant {index}.'}
                for index, row in enumerate(seeds[:2]))
            reads = list(dict.fromkeys(key for row in proposed for key in row['source_candidate_ids']))
            answer = {**identity, 'options': deepcopy(proposed),
                'fixture_reviews': [{'fixture_id': key, 'rejected': False,
                    'reason': 'TESTONLY full unchanged calendar considered.'} for key in snapshot.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {'window': None, 'policy_hash': None}}
                                  for role in SIMULATOR_ROLES]}
        else:
            options = data['validated_own_options']
            assert data['own_option_admission']['rejected_options'] == []
            assert len(options) == 2 and all(row['agent_id'] == 'kronikas' for row in options)
            assert [row['option_id'] for row in options] == [row['option_id'] for row in proposed]
            assert all(product_failure(row['canonical_card']) is None for row in options)
            tools = {row['request']['role']: row for row in data['tool_results']}
            assert set(tools) == set(SIMULATOR_ROLES)
            assert tools['candidate_scenarios']['result']['status'] == 'AVAILABLE'
            assert tools['historical_strategy_evaluation']['result']['status'] == 'UNKNOWN'
            selected = [row['option_id'] for row in options]
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY choose actual admitted own products; historical gap remains explicit.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY actual result considered; UNKNOWN is not fabricated support.'}
                    for row in data['tool_results']],
                'family_uses': [{'family': name,
                    'status': ('AVAILABLE_AND_CONSIDERED' if family['status'] == 'AVAILABLE' else
                               {'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']]),
                    'value_sha256': family['value_sha256'],
                    'option_ids': selected if family['status'] == 'AVAILABLE' else [],
                    'reason': 'TESTONLY exact family values or local gap considered.',
                    'risk': 'Historical and optional source gaps remain explicit uncertainty.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
        scope = SourceAccessScope(source_root, bundle_path, file_sha(bundle_path), identity,
            Path(sys.executable), server, file_sha(server), kernel, file_sha(kernel))
        return finish_fake_source_reply(scope, request, reads, answer)

    class SerialExecutor:
        def __init__(self, *, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def map(self, function, states):
            for index, state in enumerate(states):
                outcome = function(state)
                if index == 0:
                    clock[0] = NOW + timedelta(seconds=121)
                yield outcome

    monkeypatch.setattr(profile_decision_runner, 'ThreadPoolExecutor', SerialExecutor)
    journal = DecisionJournal(tmp_path / 'journal', snapshot)
    budget = DecisionBudget(20, NOW + timedelta(seconds=120), clock=lambda: clock[0],
                            skip_expired_owners=True)
    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
        journal=journal, source_bundle=bundle, budget=budget)
    result = runner.run(snapshot)
    assert result['status'] == 'PARTIAL' and result['agent_count'] == 10
    assert result['owners'][0]['agent_id'] == 'kronikas'
    assert result['owners'][0]['status'] == 'DECISION_COMPLETED'
    assert all(row['status'] == 'NOT_STARTED_DEADLINE' for row in result['owners'][1:])
    assert {row['agent_id'] for row in result['owners']} == set(AGENT_IDS)
    assert len(calls) == budget.used_requests == 2 and 'unattempted_agent_ids' not in result
    assert digest((artifacts, enriched, club, snapshot.data())) == original_sources
    assert len(list(journal.directory.glob('*.owner.json'))) == 10
    assert len(list(journal.directory.glob('*.tool-full-result.json'))) == 2

    publish_agent_decisions(report, result, SETTINGS, NOW)
    cards = report['generated_tickets']
    assert report['construction_status'] == 'PARTIAL' and len(cards) == 2
    assert report['ten_agent_decisions']['canonical_ticket_count'] == 2
    assert {row['agent_decision']['option_id'] for row in cards} == {row['option_id'] for row in proposed}
    assert all(row['profile_id'] == 'kronikas' and product_failure(row) is None for row in cards)
    assert all(row['ticket_product_contract'] == product_contract() and row['combined_price'] >= 10
               and len({leg['fixture_id'] for leg in row['legs']}) == len(row['legs']) <= 6
               and math.isclose(row['combined_price'], math.prod(leg['decimal_price'] for leg in row['legs']))
               for row in cards)
    assert all(row['status'] == 'DRAFT' and row['publication_scope'] == 'RESEARCH'
               and row['playable'] is False and row['stake_eur'] is None for row in cards)
    assert len(report['profiles']) == 10 and sum(row['published'] for row in report['profiles']) == 1
    artifacts['independent_agent_snapshot'] = snapshot.data()
    artifacts['independent_agent_decisions'] = deepcopy(result)
    artifacts['report'] = report
    output = tmp_path / 'saved-report'
    write_portfolio_bundle(output, {'artifacts': artifacts})
    saved = json.loads((output / 'report.json').read_text('utf-8'))
    assert saved == report and saved['generated_tickets'] == cards
    assert len(saved['ten_agent_decisions']['owners']) == 10
    assert sum(row['status'] == 'NOT_STARTED_DEADLINE' for row in saved['ten_agent_decisions']['owners']) == 9
    assert (output / 'report.md').is_file()
    assert json.loads((output / 'independent_agent_decisions.json').read_text('utf-8')) == result
