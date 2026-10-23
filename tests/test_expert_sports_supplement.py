"""Sourced sports additions reach review without changing protected decisions."""

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis.jobs import expert_advisory
from goal_analysis.portfolio_dashboard import compact_status_snapshot

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)


def report():
    card = {'profile_id': 'daily223', 'combined_price': 12.3, 'probability': .2,
            'legs': [{'fixture_id': 'api_football:1', 'decimal_price': 2.1,
                      'model_probability': .6, 'model_details': {'probability': .6}}]}
    return {'date': '2026-10-18', 'run_id': 'review-test', 'fixtures': [{'fixture_id': 'api_football:1'}],
            'sports_supplement': {'other_provider': {'preserve': True}},
            **{group: [deepcopy(card), dict(deepcopy(card), profile_id='club')]
               for group in expert_advisory.GROUPS}}


def collection():
    return {'version': 'test', 'status': 'PARTIAL', 'observed_at': NOW.isoformat(),
            'context_by_fixture': {'api_football:1': {'players': [{'id': 'pl_1', 'name': 'Player'}]}},
            'coverage': {'fixtures': 2, 'matched': 1, 'teams': 2, 'players': 1,
                         'with_xg': 1, 'with_lineups': 0, 'missing_facts': 3,
                         'gaps': {'LINEUPS_NOT_AVAILABLE': 1, 'IDENTITY_UNMAPPED': 1}},
            'usage': {'requests': 4, 'cache_hits': 3, 'max_requests': 40}}


def install_collector(monkeypatch, collector):
    # Never fall through to a real key inherited from a developer's environment.
    monkeypatch.setitem(sys.modules, 'goal_analysis.jobs.the_stats_context',
                        SimpleNamespace(collect_context=collector))


def test_context_is_attached_before_review_and_cannot_mutate_any_ticket(tmp_path, monkeypatch):
    original = report()
    before = deepcopy(original)
    expected = collection()
    calls = []

    def collect(root, supplied, **kwargs):
        calls.append(kwargs)
        supplied['tickets'][0]['combined_price'] = 999  # Defend against collector mutation.
        return expected

    def analyze(root, supplied, **kwargs):
        assert supplied['sports_supplement']['the_stats_api'] == expected
        return {'weather': {'fixtures': []}, 'daily223_protected': True}

    install_collector(monkeypatch, collect)
    monkeypatch.setattr(expert_advisory, 'analyze_report', analyze)
    options = {'daily223': [{'recommendation': before['recommendations'][0]}]}
    result = expert_advisory.review_experts(tmp_path, original, options, clock=lambda: NOW)
    assert original == before
    assert result['options'] == options
    for group in expert_advisory.GROUPS:
        assert result['report'][group] == before[group]
    assert result['report']['sports_supplement']['other_provider'] == {'preserve': True}
    assert calls[0]['max_requests'] == 110 and calls[0]['client'] is None
    assert result['report']['sports_supplement']['the_stats_api'] == expected
    assert result['report']['sports_supplement']['the_stats_api'] is not expected
    routing = result['report']['provider_routing']
    assert routing['effective_priority_by_domain']['team_player_xg_availability'] == [
        'the_stats_api', 'api_football']
    assert routing['target_priority_after_shadow_validation']['odds'] == [
        'the_stats_api', 'the_odds_api', 'api_football']
    assert routing['overwrite_primary_with_fallback'] is False


def test_collector_failure_is_explicit_safe_and_does_not_skip_other_experts(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Authorization: Bearer synthetic-private-secret')

    install_collector(monkeypatch, fail)
    monkeypatch.setattr(expert_advisory, 'analyze_report', lambda *a, **kw: {'weather': {'fixtures': []}})
    original = report()
    result = expert_advisory.review_experts(tmp_path, original, {}, clock=lambda: NOW)
    saved = result['report']['sports_supplement']['the_stats_api']
    assert saved['status'] == 'UNAVAILABLE'
    assert saved['usage']['requests'] is None  # Unknown attempts are not reported as zero.
    assert 'synthetic-private-secret' not in json.dumps(result)
    assert result['report']['expert_advisory'] == {'weather': {'fixtures': []}}
    assert result['report']['tickets'] == original['tickets']


def test_later_weather_failure_retains_sports_data_and_original_options(tmp_path, monkeypatch):
    install_collector(monkeypatch, lambda *a, **kw: collection())
    monkeypatch.setattr(expert_advisory, 'analyze_report',
                        lambda *a, **kw: (_ for _ in ()).throw(ValueError('local failure')))
    original = report()
    options = {'daily223': [{'quality': .8}]}
    result = expert_advisory.review_experts(tmp_path, original, options, clock=lambda: NOW)
    assert result['report']['expert_advisory']['status'] == 'UNAVAILABLE'
    assert result['report']['sports_supplement']['the_stats_api'] == collection()
    assert result['report']['tickets'] == original['tickets'] and result['options'] == options


def test_missing_collector_module_has_safe_fallback(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'goal_analysis.jobs.the_stats_context', None)
    result = expert_advisory.enrich_sports_supplement(tmp_path, report(), clock=lambda: NOW)
    assert result['sports_supplement']['the_stats_api']['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('budget', [-1, 111, True])
def test_context_budget_cannot_expand(tmp_path, budget):
    with pytest.raises(ValueError, match='budget'):
        expert_advisory.enrich_sports_supplement(tmp_path, report(), max_requests=budget)


def test_status_has_coverage_without_player_dump_and_saved_export_body_unchanged():
    full = {'latest': {**report(), 'sports_supplement': {'the_stats_api': collection()}}}
    before = deepcopy(full)
    compact = compact_status_snapshot(full)
    small = compact['latest']['sports_supplement']['the_stats_api']
    assert small['coverage'] == full['latest']['sports_supplement']['the_stats_api']['coverage']
    assert small['usage']['requests'] == 4 and 'context_by_fixture' not in small
    assert full == before and full['latest']['sports_supplement']['the_stats_api']['context_by_fixture']
    assert compact['latest']['tickets'] == full['latest']['tickets']


def test_ui_coverage_displays_counts_and_missing_data_without_player_details():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    source = (Path(__file__).parents[1] / 'src/goal_analysis/web/app.js').read_text('utf-8')
    function = source.split('  function sportsCoverageText(collection) {', 1)[1].split(
        '  function renderExpertAdvisory()', 1)[0]
    script = ('function sportsCoverageText(collection) {' + function
              + '\nconsole.log(JSON.stringify([sportsCoverageText(' + json.dumps(collection())
              + '),sportsCoverageText(null)]));')
    result = subprocess.run([node, '-e', script], check=True, capture_output=True,
                            encoding='utf-8', timeout=10)
    rendered, empty = json.loads(result.stdout)
    assert 'Mérkőzés: 1 / 2' in rendered and 'Csapat: 2' in rendered
    assert 'Keretjátékos: 1' in rendered and 'xG: 1 adatblokk' in rendered
    assert 'Játékosstatisztika: —' in rendered  # Unknown is not zero.
    assert 'Kezdőcsapat: 0 adatblokk' in rendered and 'Hiányzó adat: 3' in rendered
    assert 'Player' not in rendered and 'nincs kiegészítő adatlekérés' in empty
