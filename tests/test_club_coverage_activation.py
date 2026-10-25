"""Bounded source-preserving coverage promotion, not performance evidence."""
import json
from copy import deepcopy
from math import prod

import pytest
from test_club_fixture_coverage import PERSONA, build, data, fixtures
from test_club_search_budget import ranked_rows

from goal_analysis.jobs.profile_context_review import original_quote, quote_index
from goal_analysis.tipsters import coverage_search
from goal_analysis.tipsters import recommendations as module
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_CATALOG
from goal_analysis.tipsters.contracts import valid_construction


def identity(group):
    return tuple(sorted(row['quote']['candidate_id'] for row in group))


@pytest.mark.parametrize('count', [20, 40])
def test_active_pool_contains_new_fixture_routes_and_the_old_best_with_fixed_limits(count):
    rows = ranked_rows(count)
    original = deepcopy(rows)
    baseline, search = module._options(rows, PERSONA)
    active, measured, proof = module._coverage_active(rows, PERSONA, (), baseline, search)
    assert fixtures(baseline) < fixtures(active)
    assert identity(baseline[0]) in {identity(group) for group in active}
    assert len(active) <= module.CLUB_MAX_TICKET_OPTIONS == 512
    assert measured['beam_width'] == 128 and proof['baseline_best_retained']
    assert proof['newly_represented_fixture_ids'] == sorted(fixtures(active) - fixtures(baseline))
    assert len({identity(group) for group in active}) == len(active)
    assert active == sorted(active, key=lambda group: module._ticket_rank(group, PERSONA))
    assert rows == original
    for group in active:
        assert all(row in original for row in group)
        assert valid_construction({'profile_id': PERSONA['profile_id'], 'catalog_version': CATALOG_VERSION,
                                   'legs': [row['quote'] for row in group],
                                   'combined_price': prod(row['quote']['decimal_price'] for row in group)})


def test_actual_active_primary_and_allocator_templates_retain_exact_original_quotes():
    source, models = data(20)
    preserved = deepcopy((source, models, TIPSTER_CATALOG))
    stable, stable_options = build(source, models, option_search_mode='OFF')
    active, active_options = build(source, models, option_search_mode='ACTIVE')
    assert active['selection_diagnostics']['option_search_active']['newly_represented_fixture_ids']
    before = {leg['fixture_id'] for option in stable_options for leg in option['recommendation']['legs']}
    after = {leg['fixture_id'] for option in active_options for leg in option['recommendation']['legs']}
    assert before < after
    baseline_ids = {leg['candidate_id'] for leg in stable['legs']}
    assert any({leg['candidate_id'] for leg in option['recommendation']['legs']} == baseline_ids
               for option in active_options)
    index = quote_index(source['candidates'])
    for card in [active, *(option['recommendation'] for option in active_options)]:
        assert valid_construction(card)
        for leg in card['legs']:
            identifier, quote = original_quote(leg, index)
            assert identifier == leg['source_candidate_id'] == leg['quote_candidate_id']
            assert quote['decimal_price'] == leg['decimal_price']
    assert (source, models, TIPSTER_CATALOG) == preserved


@pytest.mark.parametrize('count', [0, 1, 2, 4, 12])
def test_no_trigger_preserves_whole_card_and_alternatives_without_an_extra_search(count, monkeypatch):
    source, models = data(count)
    stable, options = build(source, models, option_search_mode='OFF')
    old = module._options

    def observe(*args, **kwargs):
        assert not kwargs.get('fixture_coverage')
        return old(*args, **kwargs)

    monkeypatch.setattr(module, '_options', observe)
    active, actual = build(source, models, option_search_mode='ACTIVE')
    assert active == stable and actual == options


def test_optional_active_search_failure_preserves_stable_choices(monkeypatch):
    source, models = data(20)
    stable, _ = build(source, models, option_search_mode='OFF')
    old = module._options

    def fail(*args, **kwargs):
        if kwargs.get('fixture_coverage'):
            raise ValueError('injected failure')
        return old(*args, **kwargs)

    monkeypatch.setattr(module, '_options', fail)
    active, _ = build(source, models, option_search_mode='ACTIVE')
    assert active['legs'] == stable['legs']
    assert active['selection_diagnostics']['option_search_active']['status'] == 'UNAVAILABLE_BASELINE_RETAINED'


def test_active_cannot_bypass_the_enforced_positive_support_guard():
    source, models = data(20)
    with pytest.raises(ValueError, match='enforced support guard'):
        build(source, models, option_search_mode='ACTIVE', support_guard_mode='SHADOW')


def test_altered_challenger_quote_is_rejected_without_publishing_it():
    rows = ranked_rows(20)
    baseline, search = module._options(rows, PERSONA)
    altered = deepcopy(baseline[0])
    altered[0]['quote']['decimal_price'] += 0.01
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        coverage_search.promote(rows, PERSONA, baseline, search,
                                challenger=lambda: ([altered], search),
                                rank=lambda group: module._ticket_rank(group, PERSONA), maximum=512)


def test_reversed_input_preserves_exact_active_output_and_diagnostics():
    rows = ranked_rows(20)
    a, s = module._options(rows, PERSONA)
    b, t = module._options(list(reversed(rows)), PERSONA)
    assert module._coverage_active(rows, PERSONA, (), a, s) == module._coverage_active(
        list(reversed(rows)), PERSONA, (), b, t)


def test_absent_policy_preserves_shadow_default(tmp_path):
    assert coverage_search.policy(tmp_path) == 'SHADOW'


@pytest.mark.parametrize('mode', ['OFF', 'SHADOW', 'ACTIVE'])
def test_valid_explicit_policy(tmp_path, mode):
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'club-fixture-coverage.json').write_text(json.dumps({'version': coverage_search.VERSION, 'mode': mode}))
    assert coverage_search.policy(tmp_path) == mode


@pytest.mark.parametrize('value', [None, [], {}, {'version': coverage_search.VERSION, 'mode': []},
                                 {'version': coverage_search.VERSION, 'mode': 'LIVE'},
                                 {'version': coverage_search.VERSION, 'mode': 'ACTIVE', 'max_options': 999},
                                 {'version': 'old', 'mode': 'ACTIVE'}])
def test_bad_policy_never_silently_enables_decisions(tmp_path, value):
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'club-fixture-coverage.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='INVALID_CLUB_COVERAGE_POLICY'):
        coverage_search.policy(tmp_path)


@pytest.mark.parametrize('mode', ['OFF', 'SHADOW', 'ACTIVE'])
def test_real_producer_reads_explicit_policy_and_preserves_daily(tmp_path, monkeypatch, mode):
    from test_portfolio_flow import NOW, RecordedReviewer, environment

    from goal_analysis import tipsters
    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg, football, odds, cache, *_ = environment(tmp_path)
    folder = tmp_path / 'config'
    folder.mkdir(exist_ok=True)
    (folder / 'club-fixture-coverage.json').write_text(json.dumps({'version': coverage_search.VERSION, 'mode': mode}))
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: NOW)
    observed = []
    original = tipsters.build_tipster_recommendations

    def inspect(*args, **kwargs):
        observed.append(kwargs['option_search_mode'])
        return original(*args, **kwargs)

    monkeypatch.setattr(tipsters, 'build_tipster_recommendations', inspect)
    report = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(), ledger,
                           clock=lambda: NOW)['artifacts']['report']
    assert observed == [mode]
    assert report['tickets'][0]['profile_id'] == 'daily223' and len(report['tickets'][0]['legs']) == 3
    if mode == 'ACTIVE':
        assert report['tipster_club']['fixture_coverage_search']['mode'] == 'ACTIVE'
    else:
        assert 'fixture_coverage_search' not in report['tipster_club']


def test_invalid_policy_stops_before_provider_collection_or_model_review(tmp_path):
    from test_portfolio_flow import NOW, RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg, football, odds, cache, calls, *_ = environment(tmp_path)
    folder = tmp_path / 'config'
    folder.mkdir(exist_ok=True)
    (folder / 'club-fixture-coverage.json').write_text('{"mode":"ACTIVE"}')
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: NOW)
    reviewer = RecordedReviewer()
    with pytest.raises(ValueError, match='INVALID_CLUB_COVERAGE_POLICY'):
        run_portfolio(cfg, {}, football, odds, cache, NOW.date(), reviewer, ledger, clock=lambda: NOW)
    assert not any(calls.values()) and reviewer.calls == []
