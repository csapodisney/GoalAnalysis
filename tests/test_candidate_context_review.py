"""Source consumption, missingness and same-input SHADOW boundary regressions."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from test_astra_review import KEY, SOURCE, Transport, candidate, response, review

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.astra_review import (
    AstraReviewer,
    _ReviewError,
    _schema,
    _validate_reviews,
)
from goal_analysis.jobs import candidate_context_review as job

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


def context(fid='api_football:123', sourced=('players',)):
    args = {'fixture_id': fid, 'cutoff': NOW.isoformat()}
    rows = [job._row(name, {'explicit_test_fact': name} if name in sourced else None,
                    sources=[SOURCE] if name in sourced else [], **args) for name in job.CATEGORIES]
    value = {'version': job.VERSION, 'fixture_id': fid, 'as_of': NOW.isoformat(), 'categories': rows}
    return {**value, 'context_id': canonical_sha256(value)}


def packets():
    return job.attach([candidate()], {'contexts': {'api_football:123': context()}})


def assessed(packet):
    value = review(packet['candidate_id'])
    value['context_assessments'] = [{'category': r['category'],
        'status': 'considered' if r['status'] == 'SOURCED' else 'unavailable',
        'assessment': 'neutral' if r['status'] == 'SOURCED' else 'unknown',
        'evidence_ids': [r['evidence_id']] if r['status'] == 'SOURCED' else [],
        'reason': 'Explicit supplied facts reviewed; missing evidence remains unknown.'}
        for r in packet['decision_context']['categories']]
    return value


@pytest.fixture(autouse=True)
def actual_test_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)


def test_sourced_category_requires_explicit_exact_candidate_assessment():
    value = packets()[0]; answer = assessed(value)
    assert _validate_reviews({'reviews': [answer]}, [value], []) == [answer]
    fields = _schema(['candidate-1'], context_review=True)['properties']['reviews']['items']['properties']
    assert 'context_assessments' in fields
    assert 'context_assessments' not in _schema(['candidate-1'])['properties']['reviews']['items']['properties']


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong_category', 'wrong_evidence',
                                   'unknown_as_healthy', 'unknown_cited', 'empty_reason', 'oversized_reason'])
def test_false_or_missing_context_consumption_rejected(change):
    value = packets()[0]; answer = assessed(value)
    details = answer['context_assessments']
    if change == 'missing': details.pop()
    elif change == 'duplicate': details[-1] = deepcopy(details[0])
    elif change == 'wrong_category': details[0]['category'] = 'invented'
    elif change == 'wrong_evidence': details[0]['evidence_ids'] = ['ev-1']
    elif change == 'unknown_as_healthy': details[1].update(status='considered', assessment='support')
    elif change == 'unknown_cited': details[1]['evidence_ids'] = [value['decision_context']['categories'][1]['evidence_id']]
    elif change == 'empty_reason': details[0]['reason'] = ''
    else: details[0]['reason'] = 'x'*401
    with pytest.raises(_ReviewError):
        _validate_reviews({'reviews': [answer]}, [value], [])


@pytest.mark.parametrize('change', ['hash', 'future', 'fixture', 'missing_category', 'missing_candidate_evidence'])
def test_bad_decision_context_stops_before_transport(change):
    value = packets()[0]; ctx = value['decision_context']
    if change == 'hash': ctx['categories'][0]['data']['explicit_test_fact'] = 'tampered'
    elif change == 'future': ctx['as_of'] = (NOW+timedelta(seconds=1)).isoformat()
    elif change == 'fixture': ctx['fixture_id'] = 'api_football:999'
    elif change == 'missing_category': ctx['categories'].pop()
    else:
        value['evidence'] = []; value['evidence_ids'] = []
    if change != 'hash':
        ctx['context_id'] = canonical_sha256({k:v for k,v in ctx.items() if k != 'context_id'})
    transport = Transport()
    result = AstraReviewer(KEY, web_search=False, transport=transport).review([value], NOW.date().isoformat())
    assert result['status'] == 'FAILED' and not transport.calls


def test_shared_fixture_context_transported_once_with_exact_actual_input_hash(tmp_path):
    original = packets()[0]; other = deepcopy(original); other['candidate_id'] = 'candidate-2'
    values = [original, other]; before = deepcopy(values)
    transport = Transport(response({'reviews': [assessed(v) for v in values]}))
    result = AstraReviewer(KEY, web_search=False, transport=transport, cache_dir=tmp_path).review(values, NOW.date().isoformat())
    assert result['status'] == 'COMPLETE' and values == before
    sent = json.loads(transport.calls[0][2]['input'])
    assert len(sent['decision_contexts']) == 1
    assert sent['decision_contexts'][0] == original['decision_context']
    assert all('data' not in r for c in sent['candidates'] for r in c['decision_context']['categories'])
    archived = result['decision_context_input']
    import hashlib
    assert result['decision_context_input_sha256'] == hashlib.sha256(json.dumps(
        archived, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert sent['decision_contexts'] == archived['decision_contexts']
    assert SOURCE in {s['url'] for s in result['sources']}


def test_sent_considered_cited_and_applied_are_distinct():
    value = packets()[0]; answer = assessed(value)
    item = job.usage([value], {'reviews': [answer]})[0]
    assert item['sent'] and item['response_considered'] and item['context_evidence_cited']
    assert not item['overall_evidence_cited'] and not item['existing_caution_assessment_applies']
    answer['assessment'] = 'caution'; answer['evidence_ids'].append(value['decision_context']['categories'][0]['evidence_id'])
    item = job.usage([value], {'reviews': [answer]})[0]
    assert item['existing_caution_assessment_applies']
    assert item['counterfactual_causality_or_predictive_gain_proven'] is False


def test_existing_original_packet_and_probabilities_never_mutated():
    original = [candidate()]; before = deepcopy(original)
    result = job.attach(original, {'contexts': {'api_football:123': context()}})
    assert original == before and result != original
    assert result[0]['decimal_price'] == original[0]['decimal_price']
    assert result[0]['support_score'] == original[0]['support_score']


@pytest.mark.parametrize('mode', ['OFF', 'SHADOW', 'ACTIVE'])
def test_policy_is_explicit_bounded_and_validated(tmp_path, mode):
    assert job.policy(tmp_path) == 'OFF'
    folder = tmp_path/'config'; folder.mkdir()
    (folder/'candidate-context-review.json').write_text(json.dumps({'version': job.VERSION, 'mode': mode}))
    assert job.policy(tmp_path) == mode
    (folder/'candidate-context-review.json').write_text(json.dumps({'version': job.VERSION, 'mode': mode, 'bypass': True}))
    with pytest.raises(ValueError):job.policy(tmp_path)


def test_failed_or_unknown_preselection_budget_cannot_trigger_a_second_collection(tmp_path, monkeypatch):
    from goal_analysis.jobs import expert_advisory as expert
    def forbidden(*args, **kwargs):raise AssertionError('Duplicate110-request collection attempted')
    monkeypatch.setattr(expert, 'enrich_sports_supplement', forbidden)
    monkeypatch.setattr(expert, 'analyze_report', lambda *a, **k: {'weather': {'fixtures': []}})
    output = expert.review_experts(tmp_path, {'fixtures': [], 'candidate_context_attempt': {
        'status': 'UNAVAILABLE_NO_AUTOMATIC_SECOND_COLLECTION'}}, {}, clock=lambda: NOW)
    collection = output['report']['sports_supplement']['the_stats_api']
    assert collection['usage']['requests'] is None
    assert collection['automatic_second_collection'] is False


def test_rehashed_normalized_sports_fact_cannot_replace_archived_bytes(tmp_path):
    from test_fixture_contexts import setup
    data, _db, _registry = setup(tmp_path)
    collection = deepcopy(data['sports_supplement']['the_stats_api'])
    good, _issues = job.verified_supplement(tmp_path, collection, data['fixtures'], NOW)
    # The fixture test archive has its own actual source cutoff.
    from test_the_stats_context import NOW as SOURCE_NOW
    good, _issues = job.verified_supplement(tmp_path, collection, data['fixtures'], SOURCE_NOW+timedelta(minutes=1))
    assert good
    value = next(iter(collection['context_by_fixture'].values()))
    fact = next(f for f in value['facts'] if f['category'] == 'injuries_suspensions' and f['status'] == 'AVAILABLE')
    fact['data']['injuries'] = [{'player_id': 'pl_99999', 'active': True}]
    bad, issues = job.verified_supplement(tmp_path, collection, data['fixtures'], SOURCE_NOW+timedelta(minutes=1))
    assert not bad and issues


@pytest.mark.parametrize('mode', ['SHADOW', 'ACTIVE'])
def test_actual_coordinator_context_hook_is_before_review_and_shadow_does_not_apply(tmp_path, monkeypatch, mode):
    from test_daily_223_live import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger
    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3', now=lambda: FLOW_NOW)
    folder = tmp_path/'config';folder.mkdir(exist_ok=True)
    (folder/'candidate-context-review.json').write_text(json.dumps({'version': job.VERSION, 'mode': mode}))
    def prepare(root, fixtures, requested, **kwargs):
        contexts = {}
        for packet in requested:
            value = context(packet['fixture_id'], sourced=())
            value['as_of'] = FLOW_NOW.isoformat()
            value['context_id'] = canonical_sha256({k:v for k,v in value.items() if k != 'context_id'})
            contexts[packet['fixture_id']] = value
        return {'contexts': contexts, 'as_of': FLOW_NOW.isoformat(), 'collection': {'usage': {'requests': 0}}}
    monkeypatch.setattr(job, 'prepare', prepare)
    reviewer = RecordedReviewer()
    bundle = run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(), reviewer, ledger, clock=lambda: FLOW_NOW)
    sent = reviewer.calls[0][0]
    assert all(('decision_context' in packet) == (mode == 'ACTIVE') for packet in sent)
    report = bundle['artifacts']['report']
    assert report['candidate_context_review']['mode'] == mode
    assert report['candidate_context_review']['new_probability_weights'] is False
    batches = bundle['artifacts']['candidate_review_response_batches']
    assert batches[0]['packet_sha256'] == canonical_sha256(sent)
    assert batches[0]['response']['reviews']
    assert all('decision_context' not in p for p in bundle['artifacts']['candidate_input']['candidates'])
    daily = next(t for t in report['tickets'] if t['profile_id'] == 'daily223')
    assert len(daily['legs']) == 3 and daily['combined_price'] >= 11.76


def test_actual_existing_raw_player_reader_reaches_first_review_context_without_network(tmp_path, monkeypatch):
    from test_player_collection_shadow import setup
    from test_player_specialist_shadow import NOW as SOURCE_NOW

    from goal_analysis.jobs import expert_advisory, schedule_shadow
    data, _models, _calls, _factory = setup(tmp_path, monkeypatch)
    count = []
    def cached(*args, **kwargs):
        count.append(1)
        return {'context_collection_shadow': {'collection': {'version': 'thestatsapi-context-v1',
            'provider': 'the_stats_api', 'observed_at': SOURCE_NOW.isoformat(),
            'context_by_fixture': {}, 'usage': {'requests': 0}}}}
    monkeypatch.setattr(expert_advisory, 'enrich_sports_supplement', cached)
    monkeypatch.setattr(schedule_shadow, 'inspect_schedule', lambda *a, **k: (_ for _ in ()).throw(ValueError('isolated-calendar-failure')))
    requested = [{'candidate_id': str(i), 'fixture_id': f['fixture_id']} for i, f in enumerate(data['fixtures'])]
    before = deepcopy((data, requested))
    prepared = job.prepare(tmp_path, data['fixtures'], requested, run_id='test-source-pipeline', clock=lambda: SOURCE_NOW)
    assert len(count) == 1 and (data, requested) == before
    assert prepared['source_views']['schedule']['status'] == 'UNAVAILABLE'
    assert prepared['contexts']
    for ctx in prepared['contexts'].values():
        rows = {r['category']: r for r in ctx['categories']}
        assert rows['players']['status'] == 'SOURCED'
        assert rows['schedule']['status'] == rows['rotation']['status'] == 'UNKNOWN'
        assert all(r['source_sha256'] and r['available_at'] for r in rows['players']['data'])


def test_uncovered_follow_up_fixture_is_explicitly_unknown_without_new_collection():
    value = candidate(); value['fixture_id'] = 'api_football:999'
    result = job.attach([value], {'contexts': {}, 'as_of': NOW.isoformat()})
    assert [r['category'] for r in result[0]['decision_context']['categories']] == list(job.CATEGORIES)
    assert all(r['status'] == 'UNKNOWN' and r['data'] is None for r in result[0]['decision_context']['categories'])
    assert result[0]['evidence'] == value['evidence']


def selected_evidence():
    from goal_analysis.jobs.ticket_evidence import _Sources
    packet = packets()[0]; answer = assessed(packet)
    leg = {'fixture_id': packet['fixture_id'], 'candidate_id': packet['candidate_id'], 'astra_review': answer}
    report = {'candidate_context_review': {'mode': 'ACTIVE'}, 'astra': {'status': 'COMPLETE', 'reviews': [answer]}}
    artifacts = {'candidate_review_actual_packets': [packet], 'candidate_context_inputs': {
        'contexts': {packet['fixture_id']: deepcopy(packet['decision_context'])}}}
    packed, _ = AstraReviewer(api_key=KEY)._prepare([packet], '2026-10-24', None)
    response = {'status': 'COMPLETE', 'reviews': [deepcopy(answer)], 'decision_context_input': packed,
        'decision_context_input_sha256': sha256(json.dumps(packed, sort_keys=True,
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()}
    artifacts['candidate_review_response_batches'] = [{
        'packet_sha256': canonical_sha256([packet]), 'response': response}]
    return report, artifacts, leg, _Sources(report, artifacts)


def test_selected_leg_context_evidence_rebinds_to_actual_packet_response_and_source():
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    value = _preselection_review(report, artifacts, leg, sources)
    assert value['status'] == 'EXACT_SELECTED_LEG_REVIEW_BOUND'
    assert value['categories'][0]['context_evidence_cited']
    assert value['categories'][0]['response_considered']
    assert len(sources.records) == 4 and all(r['payload'] is not None for r in sources.records.values())
    assert len(packets()[0]['decision_context']['categories'][0]['evidence_id']) <= 24


def test_other_profiles_without_retained_review_cannot_claim_selected_leg_usage():
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    leg.pop('astra_review')
    value = _preselection_review(report, artifacts, leg, sources)
    assert value['status'] == 'NO_EXACT_RETAINED_SELECTED_LEG_REVIEW'
    assert value['categories'] == []


def test_selected_context_cannot_be_replaced_by_a_different_self_hashed_input():
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    stored = artifacts['candidate_context_inputs']['contexts'][leg['fixture_id']]
    stored['categories'][0]['data'] = {'different': True}
    stored['context_id'] = canonical_sha256({k:v for k,v in stored.items() if k != 'context_id'})
    with pytest.raises(ValueError):_preselection_review(report, artifacts, leg, sources)


def test_selected_leg_without_actual_review_request_cannot_claim_source_use():
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    artifacts.pop('candidate_review_response_batches')
    value = _preselection_review(report, artifacts, leg, sources)
    assert value['status'] == 'NO_ACTUAL_SOURCE_BOUND_REVIEW_REQUEST'
    assert value['categories'] == []


@pytest.mark.parametrize('change', ['hash', 'market'])
def test_selected_leg_binds_the_original_request_hash_and_exact_market(change):
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    result = artifacts['candidate_review_response_batches'][0]['response']
    result['decision_context_input']['candidates'][0]['market_key'] = 'different-market'
    if change == 'market':
        result['decision_context_input_sha256'] = sha256(json.dumps(result['decision_context_input'],
            sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with pytest.raises(ValueError):
        _preselection_review(report, artifacts, leg, sources)


def test_follow_up_request_is_retained_and_bound_separately():
    from goal_analysis.jobs.ticket_evidence import _preselection_review
    report, artifacts, leg, sources = selected_evidence()
    packet = artifacts['candidate_review_actual_packets'].pop()
    artifacts['candidate_review_follow_up_packets'] = [[packet]]
    value = _preselection_review(report, artifacts, leg, sources)
    assert value['status'] == 'EXACT_SELECTED_LEG_REVIEW_BOUND'
    assert len(value['source_refs']) == 4


def test_repeated_history_and_weather_are_lossless_and_source_allowlisting_survives():
    first = candidate()
    first['historical_context'] = {'sample': [{'fixture': str(i), 'goals': i % 3} for i in range(30)]}
    first['weather_forecast'] = {'status': 'SOURCED', 'source_url': SOURCE,
        'retained_weather_values': [{'hour': i, 'temperature': i / 2} for i in range(30)]}
    second = {**deepcopy(first), 'candidate_id': first['candidate_id'] + '-second'}
    values = [first, second]; before = deepcopy(values)
    reviewer = AstraReviewer(api_key=KEY)
    packed, _ = reviewer._prepare(values, '2026-10-24', None)
    pool = packed['shared_candidate_facts']
    def expand(value):
        if isinstance(value, dict) and 'shared_fact_id' in value:
            row = pool[value['shared_fact_id']]; restored = expand(row['value'])
            assert row['content_sha256'] == sha256(json.dumps(restored, sort_keys=True,
                ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            return restored
        if isinstance(value, dict): return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list): return [expand(v) for v in value]
        return value
    assert expand(packed['candidates']) == values
    assert values == before
    assert SOURCE in {r['url'] for r in reviewer._supplied_sources(packed['candidates'])}
    assert len(json.dumps(packed)) < len(json.dumps({'candidates': values}))


def test_extended_context_input_budget_still_rejects_oversized_evidence():
    value = packets()[0]
    ctx = value['decision_context']; ctx['categories'][0]['data'] = {'bounded_source': 'a' * 200_000}
    ctx['context_id'] = canonical_sha256({k: v for k, v in ctx.items() if k != 'context_id'})
    reviewer = AstraReviewer(api_key=KEY)
    packed, _ = reviewer._prepare([value], '2026-10-24', None)
    assert packed['decision_contexts'][0] == ctx
    ctx['categories'][0]['data']['bounded_source'] = 'a' * 370_000
    ctx['context_id'] = canonical_sha256({k: v for k, v in ctx.items() if k != 'context_id'})
    with pytest.raises(_ReviewError, match='compact review budget'):
        reviewer._prepare([value], '2026-10-24', None)
