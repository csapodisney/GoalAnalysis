"""Review request expansion must not become a DAILY selection whitelist."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223 import NOW, candidate
from test_portfolio import source

from goal_analysis.jobs.daily_review_cohort import VERSION, plan, policy


def inputs():
    rows = [candidate('missing', 2), candidate('covered', 2), candidate('third', 3),
            candidate('alternate', 2.1), candidate('near', 1.96), candidate('low', 1.95),
            candidate('other-book', 2.3, bookmaker='other'),
            candidate('other-region', 2.3, region='GB'), candidate('other-currency', 2.3, currency='GBP')]
    report = {'policy': {'strictness': 35}, 'tickets': [{'profile_id': 'daily223', 'legs': rows[:3]}]}
    return source(rows), report


def test_current_missing_quotes_precede_exact_cohort_alternatives_without_mutating_solver_input():
    enriched, report = inputs()
    before = deepcopy((enriched, report))
    result = plan(enriched, report, {'covered', 'third'}, ['missing'], maximum=30, now=NOW)
    assert result['candidate_ids'] == ['missing', 'alternate', 'near']
    assert result['additional_cohort_quotes'] == 2 and not result['solver_or_probability_changed']
    assert (enriched, report) == before


def test_same_economic_product_from_another_quote_is_never_borrowed_as_reviewed():
    enriched, report = inputs()
    alternate = next(row for row in enriched['candidates'] if row['candidate_id'] == 'alternate')
    alternate.update(fixture_id='missing', decimal_price=2)
    # It must be an actual selected quote, not merely share the product.
    with pytest.raises(ValueError, match='NOT_IN_SELECTED_DAILY'):
        plan(enriched, report, {'covered', 'third', 'missing'}, ['alternate'], maximum=30, now=NOW)


def test_selected_alternate_quote_needs_its_own_review_even_if_product_was_reviewed():
    enriched, report = inputs()
    alternate = next(row for row in enriched['candidates'] if row['candidate_id'] == 'alternate')
    alternate.update(fixture_id='missing', decimal_price=2)
    report['tickets'][0]['legs'][0] = deepcopy(alternate)
    result = plan(enriched, report, {'covered', 'third', 'missing'}, ['alternate'], maximum=30, now=NOW)
    assert result['candidate_ids'][0] == 'alternate'


def test_maximum_is_a_hard_request_limit_and_missing_selected_order_is_preserved():
    enriched, report = inputs()
    result = plan(enriched, report, set(), ['third', 'missing'], maximum=2, now=NOW)
    assert result['candidate_ids'] == ['third', 'missing']
    assert result['unrequested_eligible_quotes'] > 0 and not result['convergence_guaranteed']


def test_alternatives_use_existing_source_evidence_score_without_new_weights():
    enriched, report = inputs()
    stronger = candidate('z-strong', 2.2, strength=.99)
    enriched['candidates'].append(stronger)
    result = plan(enriched, report, {'covered', 'third'}, ['missing'], maximum=2, now=NOW)
    assert result['candidate_ids'] == ['missing', 'z-strong']


@pytest.mark.parametrize('maximum', [0, 31, True, 1.5])
def test_invalid_batch_budget_is_rejected(maximum):
    enriched, report = inputs()
    with pytest.raises(ValueError, match='BATCH_LIMIT'):
        plan(enriched, report, set(), ['missing'], maximum=maximum, now=NOW)


@pytest.mark.parametrize('missing', [['missing', 'missing'], ['absent'], ['alternate']])
def test_ambiguous_or_unselected_missing_ids_cannot_expand_request(missing):
    enriched, report = inputs()
    with pytest.raises(ValueError):
        plan(enriched, report, set(), missing, maximum=30, now=NOW)


def test_duplicate_source_quote_ids_fail_instead_of_silently_selecting_one():
    enriched, report = inputs()
    enriched['candidates'].append(deepcopy(enriched['candidates'][0]))
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        plan(enriched, report, set(), ['missing'], maximum=30, now=NOW)


def test_publication_lockout_never_triggers_new_review_request():
    enriched, report = inputs()
    enriched['candidates'][0]['kickoff'] = (NOW + timedelta(minutes=14)).isoformat()
    with pytest.raises(ValueError, match='NO_LONGER_REVIEW_ELIGIBLE'):
        plan(enriched, report, set(), ['missing'], maximum=30, now=NOW)


def test_existing_history_eligibility_cannot_be_replaced_by_a_high_price():
    enriched, report = inputs()
    next(row for row in enriched['candidates'] if row['candidate_id'] == 'alternate')['evidence'] = []
    result = plan(enriched, report, {'covered', 'third'}, ['missing'], maximum=30, now=NOW)
    assert 'alternate' not in result['candidate_ids']


@pytest.mark.parametrize('mode', ['OFF', 'SHADOW', 'ACTIVE'])
def test_explicit_policy_modes(tmp_path, mode):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/daily-review-cohort.json').write_text(json.dumps({'version': VERSION, 'mode': mode}))
    assert policy(tmp_path) == mode


def test_absent_policy_is_shadow_and_malformed_policy_is_not_active(tmp_path):
    assert policy(tmp_path) == 'SHADOW'
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/daily-review-cohort.json').write_text('{"mode":"ACTIVE"}')
    with pytest.raises(ValueError):
        policy(tmp_path)
