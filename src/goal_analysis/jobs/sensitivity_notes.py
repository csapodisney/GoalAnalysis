"""Pure export view of existing exact-quote reviews; never alter a saved leg.

This repeats no research/review and introduces no selection, rank, probability,
lineup-sensitivity classification or publication effect. Text is a retained
model assessment, not independent fact or counterfactual robustness proof.
"""
from collections import Counter
from datetime import datetime

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.astra_review import _CANDIDATE_KEYS, _expand_shared_facts
from goal_analysis.jobs import profile_context_review as binding

VERSION = 'arthur-source-bound-sensitivity-export-v1'
ARTIFACT_NAME = 'report-sensitivity-notes'
GROUPS = ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets', 'generated_tickets')
LABELS = {'players': 'játékos- és keretadat', 'xg': 'xG', 'schedule': 'csapatnaptár',
          'travel': 'utazás', 'rotation': 'rotáció', 'weather': 'időjárás'}


def _same_clock(left, right):
    if left is None or right is None:
        return left is right
    try:
        a, b = datetime.fromisoformat(left), datetime.fromisoformat(right)
        return (a.tzinfo is not None and a.utcoffset() is not None
                and b.tzinfo is not None and b.utcoffset() is not None and a == b)
    except (TypeError, ValueError):
        return False


def _note(leg, report, artifacts, indexed, covered, wires):
    identifier, original = binding.original_quote(leg, indexed)
    if any(leg.get(f) != original.get(f) for f in ('settlement', 'quote_timestamp_raw')):
        raise ValueError('SELECTED_SETTLEMENT_OR_RAW_QUOTE_CLOCK_DIFFERS')
    if not _same_clock(original.get('quoted_at'), leg.get('quoted_at')):
        raise ValueError('SELECTED_PROVIDER_QUOTE_CLOCK_DIFFERS')
    record = covered.get(identifier)
    if record is None:
        raise ValueError('NO_EXACT_SOURCE_BOUND_REVIEW')
    reviews = [r for r in report.get('astra', {}).get('reviews', []) if r.get('candidate_id') == identifier]
    if (report.get('astra', {}).get('status') != 'COMPLETE' or len(reviews) != 1
            or leg.get('astra_review') != record['answer'] or reviews[0] != record['answer']):
        raise ValueError('SELECTED_REVIEW_DIFFERS_FROM_ACTUAL_ANSWER')
    number = record['response_batch_index']
    response = artifacts['candidate_review_response_batches'][number]['response']
    if number not in wires:
        wires[number] = _expand_shared_facts(response['decision_context_input'])
    sent = [p for p in wires[number].get('candidates', []) if p.get('candidate_id') == identifier]
    if len(sent) != 1:
        raise ValueError('ACTUAL_QUOTE_REQUEST_NOT_UNIQUE')
    request = sent[0]
    fields = (*binding.QUOTE_FIELDS, 'settlement', 'quote_timestamp_raw',
              *(f for f in binding.PROVENANCE_FIELDS if f in _CANDIDATE_KEYS))
    if any(request.get(f) != original.get(f) for f in fields):
        raise ValueError('ACTUAL_WIRE_QUOTE_OR_RECEIPT_DIFFERS')
    if not _same_clock(request.get('quoted_at'), original.get('quoted_at')):
        raise ValueError('ACTUAL_WIRE_PROVIDER_QUOTE_CLOCK_DIFFERS')
    risks = record['answer'].get('risk_notes')
    if (not isinstance(risks, list) or not risks or not isinstance(risks[0], str)
            or not risks[0].strip() or len(risks[0]) > 1200):
        raise ValueError('NO_BOUNDED_RETAINED_RISK_NOTE')
    return {'status': 'EXACT_QUOTE_ACTUAL_REVIEW_NOTE', 'note': risks[0],
            'source_candidate_id': identifier, 'original_quote_sha256': canonical_sha256(original),
            'actual_wire_sha256': record['actual_wire_sha256'],
            'packet_batch_sha256': record['packet_batch_sha256'],
            'response_batch_index': number, 'response_sha256': canonical_sha256(response),
            'answer_sha256': canonical_sha256(record['answer']), 'risk_note_index': 0,
            'context_id': record['context_id'], 'unknown_context_categories': record['unknown_categories'],
            'sourced_context_categories': record['sourced_categories'],
            'semantics': 'RETAINED_MODEL_RISK_TEXT_NOT_FACT_OR_ROBUSTNESS_CERTIFICATION'}


def build_export_notes(report, artifacts):
    """Create a separate view; the report, cards, IDs and raw inputs stay exact."""
    try:
        quotes = artifacts.get('candidate_input', {}).get('candidates', [])
        prepared = artifacts.get('candidate_context_inputs', {})
        indexed = binding.quote_index(quotes)
        covered, issues = binding.bindings(artifacts, prepared, quotes)
    except (ValueError, KeyError, TypeError, OverflowError):
        # An optional export explanation never becomes a new publication gate.
        indexed, covered = {}, {}
        issues = [{'reason': 'SENSITIVITY_SOURCE_INPUT_UNAVAILABLE'}]
    entries, wires = [], {}
    for group in GROUPS:
        for card_index, card in enumerate(report.get(group, [])):
            for leg_index, leg in enumerate(card.get('legs', [])):
                entry = {'location': [group, card_index, 'legs', leg_index],
                         'profile_id': card.get('profile_id'), 'ticket_id': card.get('ticket_id'),
                         'card_sha256': canonical_sha256(card), 'selected_leg_sha256': canonical_sha256(leg),
                         'candidate_id': leg.get('candidate_id'), 'fixture_id': leg.get('fixture_id'),
                         'legacy_note': leg.get('sensitivity_note'),
                         'new_lineup_classification_or_weights': False}
                try:
                    entry.update(_note(leg, report, artifacts, indexed, covered, wires))
                except (ValueError, KeyError, TypeError, OverflowError):
                    entry.update(status='PENDING_EXACT_REVIEW_NOTE', note=None,
                                 reason='EXACT_QUOTE_REQUEST_CONTEXT_AND_RESPONSE_NOTE_NOT_PROVEN')
                entries.append(entry)
    result = {'version': VERSION, 'mode': 'EXPORT_ONLY', 'decision_effect': 'NONE',
              'run_id': report.get('run_id'), 'input_report_sha256': report.get('report_sha256'),
              'as_of': report.get('finished_at', report.get('observed_at')),
              'source_report_and_all_cards_unchanged': True, 'new_provider_or_llm_requests': 0,
              'new_research_or_counterfactual_robustness_claim': False,
              'binding_issues': issues, 'entries': entries,
              'leg_occurrence_counts_not_unique_tickets': dict(Counter(e['status'] for e in entries))}
    result['bundle_sha256'] = canonical_sha256(result)
    return result


def markdown_lines(entry):
    """Literal model text, separately labelled; never execute source Markdown."""
    def literal(value):
        text = value.replace('\r', ' ').replace('\n', ' ')
        for char in ('\\', '`', '*', '_', '{', '}', '[', ']', '<', '>', '#', '|'):
            text = text.replace(char, '\\' + char)
        return text
    if entry.get('status') == 'EXACT_QUOTE_ACTUAL_REVIEW_NOTE':
        lines = ['', 'Érzékenység – a mentett Astra-értékelés megjegyzése: ' + literal(entry['note'])]
        missing = entry.get('unknown_context_categories', [])
        if missing:
            lines.append('Az előzetes adatkeretben ismeretlen: ' + ', '.join(LABELS[c] for c in missing) + '.')
        return [*lines, '']
    note = entry.get('legacy_note')
    return (['', 'Előzetes megjegyzés: ' + literal(note),
             'Ehhez a lábhoz nem igazolt a konkrét értékelési megjegyzés.', '']
            if isinstance(note, str) and note else
            ['', 'Ehhez a lábhoz nem áll rendelkezésre igazolt érzékenységi megjegyzés.', ''])
