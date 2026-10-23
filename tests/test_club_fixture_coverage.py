"""New-fixture coverage is a bounded SHADOW search, never odds padding."""
from copy import deepcopy
from math import prod

import pytest
from test_club_positive_support import inputs as zero_support_inputs
from test_club_search_budget import ranked_rows
from test_tipster_personas import NOW, enriched, model_results

from goal_analysis.tipsters import recommendations as module
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID
from goal_analysis.tipsters.contracts import valid_construction

PROFILE = 'tipster_archiv_nora'
PERSONA = TIPSTER_BY_ID[PROFILE]


def fixtures(options):
    return {row['quote']['fixture_id'] for group in options for row in group}


def comparable(value):
    value = deepcopy(value)
    if isinstance(value, dict):
        value.pop('option_search_shadow', None)
        return {key: comparable(item) for key, item in value.items()}
    return [comparable(item) for item in value] if isinstance(value, list) else value


def data(count=20):
    source, models = enriched(count=count, keys=['home']), model_results(count)
    for fixture in source['fixtures']:
        fixture['competition'] = 'synthetic_single_league'
    for model in models.values():
        for index, prediction in enumerate(model['predictions']):
            p = .7 - index / 1000
            prediction['probabilities'].update(home=p, draw=(1-p)/2, away=(1-p)/2)
    return source, models


def build(source, models, **kwargs):
    options = {}
    cards = module.build_tipster_recommendations(source, models, {'enabled_profiles': [PROFILE]}, NOW,
                                                 diversity_options=options, **kwargs)
    return cards[0], options[PROFILE]


@pytest.mark.parametrize('count', [20, 40])
def test_fixed_beam_coverage_preserves_new_eligible_fixture_routes(count):
    rows = ranked_rows(count)
    original = deepcopy(rows)
    old, before = module._options(rows, PERSONA)
    new, after = module._options(rows, PERSONA, fixture_coverage=True)
    assert before['retained_option_fixture_count'] == 12
    assert after['retained_option_fixture_count'] == count
    assert fixtures(old) < fixtures(new)
    assert old[0] == new[0] and rows == original
    assert after['beam_width'] == before['beam_width'] == module.CLUB_OPTION_BEAM_WIDTH
    assert len(new) <= module.CLUB_MAX_TICKET_OPTIONS
    for group in new:
        assert all(row in original for row in group)
        ticket = {'profile_id': PROFILE, 'catalog_version': CATALOG_VERSION,
                  'legs': [row['quote'] for row in group],
                  'combined_price': prod(row['quote']['decimal_price'] for row in group)}
        assert valid_construction(ticket)


def test_actual_selector_runs_shadow_without_publishing_new_choices_or_pool():
    source, models = data()
    original = deepcopy([source, models])
    shadow, shadow_options = build(source, models)
    stable, stable_options = build(source, models, option_search_mode='OFF')
    assert comparable(shadow) == stable and comparable(shadow_options) == stable_options
    probe = shadow['selection_diagnostics']['option_search_shadow']
    assert probe['mode'] == 'SHADOW' and probe['publication_choices_unchanged']
    assert probe['status'] == 'COMPARED' and probe['newly_represented_fixture_ids']
    assert probe['challenger_retained_fixtures'] > probe['baseline_retained_fixtures']
    assert probe['example_new_fixture_route_candidate_ids']
    assert not probe['challenger_is_exhaustive_or_promoted']
    assert [source, models] == original


def test_shadow_exception_preserves_all_stable_primary_and_alternative_choices(monkeypatch):
    source, models = data()
    stable, stable_options = build(source, models, option_search_mode='OFF')
    original = module._options
    def fail(*args, **kwargs):
        if kwargs.get('fixture_coverage'):
            raise ValueError('Injected optional search failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_options', fail)
    shadow, options = build(source, models)
    assert shadow['selection_diagnostics']['option_search_shadow']['status'] == 'UNAVAILABLE'
    assert comparable(shadow) == stable and comparable(options) == stable_options


@pytest.mark.parametrize('count', [0, 1, 2, 4, 12])
def test_complete_coverage_or_insufficient_shape_does_not_request_extra_search(count, monkeypatch):
    source, models = data(count)
    original = module._options
    def inspect(*args, **kwargs):
        assert not kwargs.get('fixture_coverage'), 'No unnecessary search'
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_options', inspect)
    card, _ = build(source, models)
    assert 'option_search_shadow' not in card['selection_diagnostics']


def test_zero_support_is_not_a_source_of_new_fixture_capacity():
    source, models = zero_support_inputs(count=20, weak=20)
    card, options = build(source, models)
    assert not card['legs'] and not options
    assert 'option_search_shadow' not in card['selection_diagnostics']


def test_missing_shared_models_produce_no_challenger_or_fillers():
    source, _ = data()
    card, options = build(source, {})
    assert not card['legs'] and not options
    assert 'option_search_shadow' not in card['selection_diagnostics']


def test_reordered_sources_have_exact_same_coverage_challenger_and_diagnostics():
    rows = ranked_rows(20)
    assert module._options(rows, PERSONA, fixture_coverage=True) == module._options(
        list(reversed(rows)), PERSONA, fixture_coverage=True)


@pytest.mark.parametrize('mode', ['LIVE', 'coverage', '', None, True])
def test_unimplemented_live_promotion_or_unknown_mode_never_runs(mode):
    source, models = data(1)
    with pytest.raises(ValueError, match='option_search_mode'):
        build(source, models, option_search_mode=mode)


@pytest.mark.parametrize('flag', [None, 1, 'SHADOW'])
def test_coverage_flag_must_be_explicit_boolean(flag):
    with pytest.raises(ValueError, match='Fixture coverage'):
        module._options(ranked_rows(1), PERSONA, fixture_coverage=flag)


def test_challenger_retains_source_cohorts_and_distinct_match_contracts():
    rows = ranked_rows(20)
    rows += [{**deepcopy(row), 'quote': {**row['quote'], 'candidate_id': row['quote']['candidate_id'] + '-other',
                                       'bookmaker': 'other', 'currency': 'USD'}} for row in rows[:6]]
    new, _ = module._options(rows, PERSONA, fixture_coverage=True)
    assert new
    assert all(len({r['quote']['fixture_id'] for r in group}) == len(group) for group in new)
    assert all(len({(r['quote']['bookmaker'], r['quote']['region'], r['quote']['currency']) for r in group}) == 1
               for group in new)
