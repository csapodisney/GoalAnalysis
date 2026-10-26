"""Export exact existing reviews without changing identities or decisions."""
import json
from copy import deepcopy

import pytest
from test_candidate_context_review import NOW
from test_profile_context_review import generated, quote, witnessed

from goal_analysis.jobs import profile_context_review as binding
from goal_analysis.jobs.portfolio import write_portfolio_bundle
from goal_analysis.jobs.sensitivity_notes import ARTIFACT_NAME, build_export_notes, markdown_lines


@pytest.fixture(autouse=True)
def actual_mock_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)


def sample():
    source = quote();artifacts, prepared = witnessed([source])
    response = artifacts['candidate_review_response_batches'][0]['response']
    response['reviews'][0]['risk_notes'] = ['Synthetic exact-quote risk, not an actual match assertion.']
    leg = generated(source);leg['astra_review'] = deepcopy(response['reviews'][0])
    leg['sensitivity_note'] = 'Synthetic legacy uncertainty, no confirmed lineup.'
    ticket = {'ticket_id': 'synthetic-stable-id', 'profile_id': 'gauss', 'profile_name': 'Gauss',
              'status': 'READY', 'bookmaker': source['bookmaker'], 'combined_price': source['decimal_price'],
              'legs': [leg], 'risk_notes': []}
    report = {'run_id': 'synthetic-export', 'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
              'finished_at': NOW.isoformat(), 'construction_status': 'COMPLETE', 'astra': response,
              'tickets': [ticket], 'profile_proposals': [], 'recommendations': [], 'preview_tickets': [],
              'candidate_context_review': {'mode': 'ACTIVE'}, 'profile_context_review': {'mode': 'ACTIVE'}}
    artifacts.update(candidate_input={'candidates': [source]}, candidate_context_inputs=prepared)
    return report, artifacts


def first(report, artifacts):
    return build_export_notes(report, artifacts)['entries'][0]


def test_actual_mock_request_answer_and_exact_source_identity_bound_without_mutation():
    report, artifacts = sample();before = deepcopy((report, artifacts))
    row = first(report, artifacts)
    assert row['status'] == 'EXACT_QUOTE_ACTUAL_REVIEW_NOTE'
    assert row['note'] == report['tickets'][0]['legs'][0]['astra_review']['risk_notes'][0]
    assert row['source_candidate_id'] != row['candidate_id']
    assert len(row['actual_wire_sha256']) == len(row['response_sha256']) == 64
    assert row['unknown_context_categories']
    assert row['new_lineup_classification_or_weights'] is False
    assert (report, artifacts) == before


@pytest.mark.parametrize('field', [*binding.QUOTE_FIELDS, *binding.PROVENANCE_FIELDS, 'quoted_at', 'settlement', 'quote_timestamp_raw'])
def test_selected_leg_cannot_borrow_any_other_quote_identity_receipt_or_clock(field):
    report, artifacts = sample();leg = report['tickets'][0]['legs'][0]
    leg[field] = 9.9 if field == 'decimal_price' else 'changed'
    assert first(report, artifacts)['status'] == 'PENDING_EXACT_REVIEW_NOTE'


def test_same_fixture_and_product_another_bookmaker_quote_has_no_borrowed_note():
    report, artifacts = sample();other = quote('other');other['bookmaker'] = 'other_book'
    artifacts['candidate_input']['candidates'].append(other)
    leg = generated(other);leg['astra_review'] = deepcopy(report['astra']['reviews'][0])
    report['tickets'][0]['legs'] = [leg]
    assert first(report, artifacts)['status'] == 'PENDING_EXACT_REVIEW_NOTE'


def test_changed_wire_provenance_rejected_even_with_recomputed_wire_digest():
    from hashlib import sha256
    report, artifacts = sample();response = artifacts['candidate_review_response_batches'][0]['response']
    response['decision_context_input']['candidates'][0]['quote_source_sha256'] = 'b' * 64
    response['decision_context_input_sha256'] = sha256(json.dumps(response['decision_context_input'],
        sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert first(report, artifacts)['status'] == 'PENDING_EXACT_REVIEW_NOTE'


@pytest.mark.parametrize('change', ['missing_batch', 'duplicate_batch', 'changed_context', 'changed_selected_answer', 'missing_risk', 'failed_primary'])
def test_missing_or_ambiguous_source_response_and_risk_is_explicit_pending(change):
    report, artifacts = sample()
    if change == 'missing_batch':artifacts.pop('candidate_review_response_batches')
    elif change == 'duplicate_batch':artifacts['candidate_review_response_batches'] *= 2
    elif change == 'changed_context':artifacts['candidate_context_inputs']['contexts'].clear()
    elif change == 'changed_selected_answer':report['tickets'][0]['legs'][0]['astra_review']['risk_notes'] = ['changed']
    elif change == 'missing_risk':report['astra']['reviews'][0]['risk_notes'] = []
    else:report['astra']['status'] = 'FAILED'
    row = first(report, artifacts)
    assert row['status'] == 'PENDING_EXACT_REVIEW_NOTE' and row['note'] is None
    assert 'LOW' not in str(row)


def test_unrelated_failed_secondary_does_not_hide_successful_actual_primary_note():
    report, artifacts = sample()
    artifacts['candidate_review_response_batches'].append({'packet_sha256': 'invalid', 'response': {'status': 'FAILED'}})
    result = build_export_notes(report, artifacts)
    assert result['entries'][0]['status'] == 'EXACT_QUOTE_ACTUAL_REVIEW_NOTE'
    assert result['binding_issues']


def test_no_note_content_is_invented_and_markdown_is_literal_source_text():
    report, artifacts = sample();risk = 'Synthetic ![remote](https://invalid.example/) <script>data</script>.'
    report['astra']['reviews'][0]['risk_notes'] = [risk]
    report['tickets'][0]['legs'][0]['astra_review']['risk_notes'] = [risk]
    row = first(report, artifacts);text = '\n'.join(markdown_lines(row))
    assert row['note'] == risk and '\\[remote\\]' in text and '\\<script\\>' in text
    assert 'Az előzetes adatkeretben ismeretlen:' in text
    assert '![remote]' not in text


def test_writer_preserves_all_artifact_json_cards_ids_and_existing_directory(tmp_path):
    report, artifacts = sample();bundle = {'artifacts': {**artifacts, 'report': report}}
    before = deepcopy(bundle);directory = tmp_path / 'new-export'
    write_portfolio_bundle(directory, bundle)
    assert bundle == before
    for name, value in before['artifacts'].items():
        assert json.loads((directory / (name + '.json')).read_bytes()) == value
    notes = json.loads((directory / (ARTIFACT_NAME + '.json')).read_bytes())
    assert notes['mode'] == 'EXPORT_ONLY' and notes['decision_effect'] == 'NONE'
    note = notes['entries'][0]['note']
    assert note in (directory / 'report.md').read_text(encoding='utf-8')
    snapshot = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(FileExistsError):write_portfolio_bundle(directory, bundle)
    assert snapshot == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize('group', ['tickets', 'preview_tickets', 'recommendations', 'profile_proposals'])
def test_every_group_uses_its_own_original_card_and_leg_location(group):
    report, artifacts = sample();report[group] = report.pop('tickets') if group != 'tickets' else report[group]
    row = first(report, artifacts)
    assert row['location'] == [group, 0, 'legs', 0]


def test_missing_legacy_and_actual_note_are_not_filled_with_fiction():
    report, artifacts = sample();artifacts['candidate_review_response_batches'] = []
    report['tickets'][0]['legs'][0].pop('sensitivity_note')
    row = first(report, artifacts)
    assert row['note'] is row['legacy_note'] is None
    assert 'nem áll rendelkezésre' in '\n'.join(markdown_lines(row))


def test_reserved_sidecar_collision_rejected_before_creating_output(tmp_path):
    report, artifacts = sample();artifacts.update(report=report)
    artifacts[ARTIFACT_NAME] = {'untrusted': True}
    with pytest.raises(ValueError, match='Reserved'):write_portfolio_bundle(tmp_path / 'new', {'artifacts': artifacts})
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('invalid', [None, 'not-a-list', [None]])
def test_optional_metadata_failure_preserves_valid_report_and_export(tmp_path, invalid):
    report, artifacts = sample();artifacts['candidate_input']['candidates'] = invalid
    bundle = {'artifacts': {**artifacts, 'report': report}};before = deepcopy(bundle)
    write_portfolio_bundle(tmp_path / 'export', bundle)
    sidecar = json.loads((tmp_path / 'export' / (ARTIFACT_NAME + '.json')).read_bytes())
    assert sidecar['entries'][0]['status'] == 'PENDING_EXACT_REVIEW_NOTE'
    assert sidecar['binding_issues'] == [{'reason': 'SENSITIVITY_SOURCE_INPUT_UNAVAILABLE'}]
    assert json.loads((tmp_path / 'export/report.json').read_bytes()) == report
    assert bundle == before
