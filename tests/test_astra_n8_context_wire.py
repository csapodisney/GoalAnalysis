"""Oversized real-shaped context can share facts without changing evidence contracts."""
import json
from copy import deepcopy
from hashlib import sha256

import pytest
from test_astra_review import KEY, SOURCE, Transport, candidate, researched, response
from test_candidate_context_review import NOW, assessed, packets

from goal_analysis.agents import astra_review as astra
from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import candidate_context_review as context_job
from goal_analysis.jobs.ticket_evidence import _preselection_review, _Sources

CONTEXT_SOURCE = SOURCE + '?facts=players-xg-schedule-travel-rotation-weather'


def _rich_packets():
    """Six sourced categories and 16 forecasts exceed the eight-forecast live attempt."""
    contexts, forecasts = {}, {}
    metadata = {'provider': 'the_stats', 'retrieved_at': NOW.isoformat(),
        'source_url': SOURCE, 'quoted_at': None, 'observed_not_provider_quote_time': True,
        'limitations': ['Observed fixture facts; not an actual itinerary or confirmed XI.'] * 7}
    for index in range(16):
        fixture_id = f'the_stats:{1000 + index}'
        weather = {'fixture_id': fixture_id, 'status': 'SOURCED', 'source_url': SOURCE,
            'observed_at': NOW.isoformat(), 'forecast_at': NOW.isoformat(),
            'hourly': [{'hour': hour, 'temperature_c': 12.5 + index / 10,
                        'precipitation_mm': 0.0, 'metadata': metadata} for hour in range(12)]}
        forecasts[fixture_id] = weather
        rows = [context_job._row(name, weather if name == 'weather' else {
                    'fixture_id': fixture_id, 'metadata': metadata,
                    'observations': [{'ordinal': i, 'value': index + i / 10,
                                      'metadata': metadata} for i in range(12)]},
                sources=[SOURCE, CONTEXT_SOURCE], fixture_id=fixture_id, cutoff=NOW.isoformat())
                for name in context_job.CATEGORIES]
        ctx = {'version': context_job.VERSION, 'fixture_id': fixture_id,
               'as_of': NOW.isoformat(), 'categories': rows}
        contexts[fixture_id] = {**ctx, 'context_id': canonical_sha256(ctx)}
    values = []
    for index in range(30):
        value = candidate(f'candidate-{index}', f'ev-{index % 16}')
        value['fixture_id'] = f'the_stats:{1000 + index % 16}'
        value['evidence'][0]['source_id'] = value['fixture_id']
        values.append(value)
    return context_job.attach(values, {'contexts': contexts, 'weather_by_fixture': forecasts})


def _answer(packet):
    answer = assessed(packet)
    answer['evidence_ids'] = [packet['evidence'][0]['evidence_id']]
    answer['source_urls'] = [SOURCE, CONTEXT_SOURCE]
    return answer


def _baseline(monkeypatch, values):
    """Capture the exact validated representation immediately before new fallback."""
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(astra, '_share_context_facts', lambda value: captured.append(deepcopy(value)) or value)
        with pytest.raises(astra._ReviewError, match='compact review budget'):
            astra.AstraReviewer(KEY, web_search=False)._prepare(values, NOW.date().isoformat(), None)
    assert len(captured) == 1
    return captured[0]


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(astra, '_utcnow', lambda: NOW)


def test_all_six_category_facts_and_sixteen_forecasts_roundtrip_exactly(monkeypatch):
    values = _rich_packets()
    original = deepcopy(values)
    baseline = _baseline(monkeypatch, values)
    packed, _ = astra.AstraReviewer(KEY, web_search=False)._prepare(values, NOW.date().isoformat(), None)
    assert len(astra._wire_json(baseline).encode()) > 360_000
    assert len(astra._wire_json(packed).encode()) < 330_000
    assert packed['shared_context_facts'] == 'lossless-context-and-evidence-v1'
    assert 'shared_fact_id' in packed['candidates'][0]['decision_context']['source_urls']
    assert 'shared_fact_id' in packed['candidates'][0]['evidence']
    restored = astra._expand_shared_facts(packed)
    assert restored == astra._expand_shared_facts(baseline)
    assert canonical_sha256(restored) == canonical_sha256(astra._expand_shared_facts(baseline))
    assert restored['decision_contexts'] == list({p['decision_context']['context_id']:
        p['decision_context'] for p in original}.values())
    assert len(restored['decision_contexts']) == 16
    for before, after, wire in zip(original, restored['candidates'], packed['candidates'], strict=True):
        for field in ('candidate_id', 'fixture_id', 'decimal_price', 'support_score',
                      'market_key', 'selection_key'):
            assert before[field] == after[field] == wire[field]
        assert after['weather_forecast'] == before['weather_forecast']
        assert after['evidence'] == before['evidence']
        assert all(row['status'] == 'SOURCED' for row in wire['decision_context']['categories'])
        assert all(isinstance(row['evidence_id'], str) for row in wire['decision_context']['categories'])
    assert values == original


@pytest.mark.parametrize('web_search', [False, True])
def test_review_and_cache_validate_restored_candidates_and_sources(tmp_path, web_search):
    values = _rich_packets()
    responses = ([researched()] if web_search else []) + [response({'reviews': [_answer(p) for p in values]})]
    transport = Transport(*responses)
    reviewer = astra.AstraReviewer(KEY, web_search=web_search, transport=transport, cache_dir=tmp_path)
    result = reviewer.review(values, NOW.date().isoformat())
    assert result['status'] == 'COMPLETE', result.get('message')
    assert {SOURCE, CONTEXT_SOURCE} <= {item['url'] for item in result['sources']}
    for _, _, sent in transport.calls:
        assert 'decision_contexts category' in sent['instructions']
        packed = json.loads(sent['input'])
        assert len(sent['input'].encode()) <= 360_000
        restored = astra._expand_shared_facts(packed)
        assert restored['candidates'][0]['evidence'] == values[0]['evidence']
    call_count = len(transport.calls)
    cached = reviewer.review(values, NOW.date().isoformat())
    assert cached['status'] == 'COMPLETE' and cached['usage']['cache_hit']
    assert len(transport.calls) == call_count
    assert cached['reviews'] == result['reviews']
    wire = result['decision_context_input']
    assert result['decision_context_input_sha256'] == sha256(json.dumps(
        wire, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def test_expanded_validation_rejects_fabricated_category_evidence():
    values = _rich_packets()
    answers = [_answer(p) for p in values]
    answers[0]['context_assessments'][-1]['evidence_ids'] = ['context:invented']
    transport = Transport(response({'reviews': answers}))
    result = astra.AstraReviewer(KEY, web_search=False, transport=transport).review(values, NOW.date().isoformat())
    assert result['status'] == 'FAILED'
    assert len(transport.calls) == 1


def _receipt(values, result):
    report = {'candidate_context_review': {'mode': 'ACTIVE'}, 'astra': result}
    artifacts = {'candidate_review_actual_packets': values,
        'candidate_context_inputs': {'contexts': {p['fixture_id']: p['decision_context'] for p in values}},
        'candidate_review_response_batches': [{'packet_sha256': canonical_sha256(values), 'response': result}]}
    leg = {**values[0], 'astra_review': result['reviews'][0]}
    return report, artifacts, leg


@pytest.mark.parametrize('tamper', [None, 'raw_hash', 'fact_value', 'context'])
def test_ticket_receipt_binds_original_packet_to_hashed_and_restored_wire(tamper):
    values = _rich_packets()
    transport = Transport(response({'reviews': [_answer(p) for p in values]}))
    result = astra.AstraReviewer(KEY, web_search=False, transport=transport).review(values, NOW.date().isoformat())
    assert result['status'] == 'COMPLETE'
    result = deepcopy(result)
    packed = result['decision_context_input']
    if tamper == 'raw_hash':
        packed['target_date'] = '2026-10-24'
    elif tamper == 'fact_value':
        first = next(iter(packed['shared_candidate_facts'].values()))
        first['value'] = {'tampered': True}
    elif tamper == 'context':
        packed['decision_contexts'][0]['as_of'] = '2026-10-22T10:00:00+00:00'
    if tamper in {'fact_value', 'context'}:
        result['decision_context_input_sha256'] = sha256(json.dumps(
            packed, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    report, artifacts, leg = _receipt(values, result)
    if tamper:
        with pytest.raises(ValueError, match='input hash mismatch|Invalid shared facts|differs from selected context'):
            _preselection_review(report, artifacts, leg, _Sources(report, artifacts))
    else:
        before = deepcopy((report, artifacts, leg))
        receipt = _preselection_review(report, artifacts, leg, _Sources(report, artifacts))
        assert receipt['status'] == 'EXACT_SELECTED_LEG_REVIEW_BOUND'
        assert len(receipt['categories']) == 6
        assert all(row['response_considered'] and row['context_evidence_cited'] for row in receipt['categories'])
        assert (report, artifacts, leg) == before


@pytest.mark.parametrize('bad_pool', ['cycle', 'unknown', 'hash', 'shape'])
def test_invalid_shared_pool_is_rejected(bad_pool):
    value = {'provider': 'the_stats', 'source_url': SOURCE}
    digest = sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    packed = {'candidates': [{'evidence': {'shared_fact_id': 'fact:example'}}],
        'shared_candidate_facts': {'fact:example': {'content_sha256': digest, 'value': value}}}
    if bad_pool == 'cycle':
        packed['shared_candidate_facts']['fact:example']['value'] = {'shared_fact_id': 'fact:example'}
    elif bad_pool == 'unknown':
        packed['candidates'][0]['evidence']['shared_fact_id'] = 'fact:missing'
    elif bad_pool == 'hash':
        packed['shared_candidate_facts']['fact:example']['content_sha256'] = '0' * 64
    else:
        packed['shared_candidate_facts']['fact:example'] = []
    with pytest.raises(astra._ReviewError):
        astra._expand_shared_facts(packed)


def test_small_existing_input_does_not_activate_context_sharing(monkeypatch):
    def unexpected(_):
        raise AssertionError('Under-budget inputs must retain their existing representation')
    monkeypatch.setattr(astra, '_share_context_facts', unexpected)
    packed, _ = astra.AstraReviewer(KEY, web_search=False)._prepare(packets(), NOW.date().isoformat(), None)
    assert 'shared_context_facts' not in packed
