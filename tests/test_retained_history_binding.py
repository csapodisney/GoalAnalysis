"""Original feature/engine output binding; adversarial changes never become used data."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223_history import FETCHED, NOW, history, input_payload
from test_ticket_evidence import sections

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.daily_223 import Daily223Policy, _score_evidence
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.jobs.ticket_evidence import build_ticket_evidence, validate_ticket_evidence


def sample():
    snapshot = history()
    analysis = enrich_daily223_input(input_payload(), snapshot)
    original = analysis['candidates'][0]
    _, normalized = _score_evidence(original['evidence'], NOW, Daily223Policy())
    assert normalized and all(e['fresh'] is True for e in normalized)
    leg = {**deepcopy(original), 'evidence': normalized}
    report = {'run_id': 'unit-original-engine', 'date': NOW.date().isoformat(),
        'observed_at': NOW.isoformat(), 'finished_at': (NOW+timedelta(hours=1)).isoformat(),
        'tickets': [{'profile_id': 'daily223', 'status': 'READY', 'legs': [leg]}]}
    return report, {'candidate_analysis': analysis, 'history': snapshot}


def form(report, artifacts):
    return sections(build_ticket_evidence(report, artifacts))['form']


def test_actual_feature_then_original_engine_normalization_binds_without_mutation():
    report, artifacts = sample()
    before = deepcopy((report, artifacts))
    bundle = build_ticket_evidence(report, artifacts)
    section = sections(bundle)['form']
    assert section['status'] == 'RETAINED_SELECTION_EVIDENCE'
    paths = [bundle['sources'][ref]['path'] for ref in section['source_refs']]
    assert ['history', 'snapshot_sha256'] in paths
    assert ['candidate_analysis', 'observed_at'] in paths
    validate_ticket_evidence(bundle, report, artifacts)
    assert (report, artifacts) == before


@pytest.mark.parametrize('field,value', [('statement', 'changed'), ('evidence_id', 'different'),
    ('source_id', 'history-sha256:forged'), ('observed_at', '2026-09-20T09:59:01+00:00'),
    ('strength', .123), ('reliability', .123), ('claim_type', 'FACT'), ('unexpected', 'extra')])
def test_only_fresh_may_differ_between_selected_and_source(field, value):
    report, artifacts = sample()
    report['tickets'][0]['legs'][0]['evidence'][0][field] = value
    assert form(report, artifacts)['status'] == 'MISSING'


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_caller_freshness_is_not_trusted(value):
    report, artifacts = sample()
    report['tickets'][0]['legs'][0]['evidence'][0]['fresh'] = value
    assert form(report, artifacts)['status'] == 'MISSING'


@pytest.mark.parametrize('fault', ['duplicate_candidate', 'missing_candidate', 'duplicate_source_evidence',
    'duplicate_selected_evidence', 'different_fixture', 'different_price', 'different_period',
    'source_extra_field', 'source_has_fresh', 'wrong_snapshot_digest', 'changed_history_bytes',
    'metadata_digest', 'metadata_sources', 'metadata_version', 'analysis_clock', 'missing_report_clock',
    'future_history', 'future_evidence', 'evidence_after_collection', 'naive_evidence', 'invalid_evidence'])
def test_ambiguous_sources_hash_and_clock_changes_remain_missing(fault):
    report, artifacts = sample()
    analysis = artifacts['candidate_analysis']
    source = analysis['candidates'][0]
    selected = report['tickets'][0]['legs'][0]['evidence']
    if fault == 'duplicate_candidate':
        analysis['candidates'].append(deepcopy(source))
    elif fault == 'missing_candidate':
        analysis['candidates'] = []
    elif fault == 'duplicate_source_evidence':
        source['evidence'].append(deepcopy(source['evidence'][0]))
    elif fault == 'duplicate_selected_evidence':
        selected.append(deepcopy(selected[0]))
    elif fault.startswith('different_'):
        key = {'different_fixture': 'fixture_id', 'different_price': 'decimal_price', 'different_period': 'period'}[fault]
        source[key] = 9 if key == 'decimal_price' else 'changed'
    elif fault == 'source_extra_field':
        source['evidence'][0]['unexpected'] = 'extra'
    elif fault == 'source_has_fresh':
        source['evidence'][0]['fresh'] = False
    elif fault == 'wrong_snapshot_digest':
        artifacts['history']['snapshot_sha256'] = 'forged'
    elif fault == 'changed_history_bytes':
        artifacts['history']['records'][0]['full_time_home'] += 1
    elif fault.startswith('metadata_'):
        key = {'metadata_digest': 'snapshot_sha256', 'metadata_sources': 'sources', 'metadata_version': 'feature_version'}[fault]
        analysis['history_analysis'][key] = [] if key == 'sources' else 'changed'
    elif fault == 'analysis_clock':
        analysis['observed_at'] = (NOW+timedelta(seconds=1)).isoformat()
    elif fault == 'missing_report_clock':
        report.pop('observed_at')
    elif fault == 'future_history':
        snapshot = artifacts['history']
        snapshot['collected_at'] = (NOW+timedelta(seconds=1)).isoformat()
        snapshot['snapshot_sha256'] = canonical_sha256({k: v for k, v in snapshot.items() if k != 'snapshot_sha256'})
        analysis['history_analysis']['snapshot_sha256'] = snapshot['snapshot_sha256']
    else:
        stamp = {'future_evidence': (NOW+timedelta(seconds=1)).isoformat(),
            'evidence_after_collection': (FETCHED+timedelta(seconds=1)).isoformat(),
            'naive_evidence': '2026-09-20T09:00:00', 'invalid_evidence': 'bad'}[fault]
        selected[0]['observed_at'] = stamp
        source['evidence'][0]['observed_at'] = stamp
    assert form(report, artifacts)['status'] == 'MISSING'


def test_history_evidence_keeps_original_no_age_expiry_semantics():
    report, artifacts = sample()
    later = NOW+timedelta(days=400)
    report['observed_at'] = artifacts['candidate_analysis']['observed_at'] = later.isoformat()
    assert form(report, artifacts)['status'] == 'RETAINED_SELECTION_EVIDENCE'


@pytest.mark.parametrize('target', ['source', 'history', 'report_clock', 'derived_flag', 'bundle'])
def test_strict_validator_rejects_changes_even_if_bundle_is_rehashed(target):
    report, artifacts = sample()
    bundle = build_ticket_evidence(report, artifacts)
    if target == 'source':
        artifacts['candidate_analysis']['candidates'][0]['evidence'][0]['statement'] = 'changed'
    elif target == 'history':
        artifacts['history']['records'][0]['full_time_home'] += 1
    elif target == 'report_clock':
        report['observed_at'] = (NOW-timedelta(days=1)).isoformat()
    elif target == 'derived_flag':
        report['tickets'][0]['legs'][0]['evidence'][0]['fresh'] = False
    else:
        sections(bundle)['form']['source_refs'] = []
    bundle.pop('bundle_sha256')
    bundle['bundle_sha256'] = canonical_sha256(bundle)
    with pytest.raises(ValueError, match='frozen source'):
        validate_ticket_evidence(bundle, report, artifacts)
