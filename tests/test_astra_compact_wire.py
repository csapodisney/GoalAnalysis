"""The bounded request must measure and send the same lossless JSON encoding."""
import json
from copy import deepcopy

import pytest
from test_astra_review import KEY, Transport, candidate, researched, response, review
from test_candidate_context_review import NOW, assessed, packets

from goal_analysis.agents.astra_review import AstraReviewer, _ReviewError


@pytest.mark.parametrize('web_search', [False, True])
def test_actual_research_and_review_wire_preserve_every_supplied_json_value(monkeypatch, web_search):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)
    supplied = packets()
    supplied[0]['evidence'].append({'evidence_id': 'format-check',
        'statement': 'Árvíztűrő  tükörfúrógép: null,  [ 1, 2 ]\n"idézet"',
        'value': None, 'active': False, 'numbers': [0, 2.125, -3]})
    before = deepcopy(supplied)
    responses = ([researched()] if web_search else []) + [response({'reviews': [assessed(supplied[0])]})]
    transport = Transport(*responses)
    reviewer = AstraReviewer(KEY, transport=transport, web_search=web_search)
    prepared, _ = reviewer._prepare(supplied, NOW.date().isoformat(), {'empty': None})

    result = reviewer.review(supplied, NOW.date().isoformat(), {'empty': None})

    assert result['status'] == 'COMPLETE' and supplied == before
    assert len(transport.calls) == (2 if web_search else 1)
    for _, _, payload in transport.calls:
        wire = payload['input']
        parsed = json.loads(wire)
        assert {k: parsed[k] for k in prepared} == prepared
        assert wire == json.dumps(parsed, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        assert parsed['candidates'][0]['evidence'][-1] == before[0]['evidence'][-1]
        assert parsed['decision_contexts'][0] == before[0]['decision_context']


@pytest.mark.parametrize('with_context,limit', [(False, 180_000), (True, 360_000)])
def test_existing_input_byte_limits_accept_exact_boundary_and_reject_one_extra(monkeypatch, with_context, limit):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)
    values = packets() if with_context else [candidate()]
    values[0]['evidence'].append({'evidence_id': 'boundary', 'statement': ''})
    reviewer = AstraReviewer(KEY, web_search=False)
    initial, _ = reviewer._prepare(values, NOW.date().isoformat(), None)
    size = len(json.dumps(initial, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    values[0]['evidence'][-1]['statement'] = 'x' * (limit - size)
    accepted, _ = reviewer._prepare(values, NOW.date().isoformat(), None)
    assert len(json.dumps(accepted, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) == limit
    values[0]['evidence'][-1]['statement'] += 'x'
    with pytest.raises(_ReviewError, match='compact review budget'):
        reviewer._prepare(values, NOW.date().isoformat(), None)


def test_whitespace_heavy_valid_facts_fit_without_dropping_numeric_data(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)
    values = [candidate()]
    numbers = list(range(28_000))
    values[0]['evidence'].append({'evidence_id': 'full-history', 'values': numbers})
    transport = Transport(response({'reviews': [review()]}))
    reviewer = AstraReviewer(KEY, web_search=False, transport=transport)
    packed, _ = reviewer._prepare(values, NOW.date().isoformat(), None)
    assert len(json.dumps(packed, ensure_ascii=False).encode('utf-8')) > 180_000
    assert len(json.dumps(packed, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) < 180_000
    result = reviewer.review(values, NOW.date().isoformat())
    assert result['status'] == 'COMPLETE'
    actual = json.loads(transport.calls[0][2]['input'])
    assert actual['candidates'][0]['evidence'][-1]['values'] == numbers


def test_multibyte_text_still_uses_utf8_bytes_instead_of_character_count():
    values = [candidate()]
    values[0]['evidence'].append({'evidence_id': 'unicode-boundary', 'statement': 'ő' * 95_000})
    transport = Transport()
    result = AstraReviewer(KEY, web_search=False, transport=transport).review(values, '2026-10-24')
    assert result['status'] == 'FAILED' and 'compact review budget' in result['message']
    assert not transport.calls
