"""Declared current policy, without another selection engine or ticket mutation."""
import socket
from copy import deepcopy

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.portfolio_dashboard import ArthurState
from goal_analysis.tipsters import strategy_contracts as contracts
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG


def test_all_twenty_actual_policies_have_stable_detached_versioned_contracts():
    original = deepcopy(TIPSTER_CATALOG)
    first = contracts.club_strategy_contracts()
    assert first == contracts.club_strategy_contracts() and TIPSTER_CATALOG == original
    assert len(first['contracts']) == len({c['version_id'] for c in first['contracts']}) == 20
    by_id = {row['profile_id']: row for row in first['contracts']}
    for persona in TIPSTER_CATALOG:
        c = by_id[persona['profile_id']]
        assert c['selection_policy_snapshot'] == persona['selection_policy']
        assert c['odds_band'] == persona['target_odds_band']
        assert c['construction']['min_legs'] == persona['min_legs']
        assert c['construction']['max_legs'] == persona['max_legs']
        assert c['numeric_model_weights'] == persona['model_weights']
        assert c['competition_type_policy']['preference'] == 'UNSPECIFIED'
        assert c['competition_type_policy']['persona_type_filter_enforced'] is False
        assert c['decision_effect'] is False
        assert 'daily223' not in by_id
    first['contracts'][0]['selection_policy_snapshot']['min_probability'] = 0
    assert TIPSTER_CATALOG == original and first != contracts.club_strategy_contracts()


def test_actual_over_under_and_btts_preferences_are_separate():
    rows = {c['profile_id']: c for c in contracts.club_strategy_contracts()['contracts']}
    assert [r['model_market_key'] for r in rows['tipster_golvadasz_lili']['preferred_markets']] == ['over25']
    assert {r['model_market_key'] for r in rows['tipster_csendes_mira']['preferred_markets']} == {'under25', 'btts_no'}
    assert [r['model_market_key'] for r in rows['tipster_dupla_aron']['preferred_markets']] == ['btts_yes']
    assert rows['tipster_bastya_bence']['evidence_thresholds']['min_models'] == 2
    assert len(rows['tipster_bastya_bence']['numeric_model_weights']) == 3  # Prose cannot invent a fourth model.


def test_policy_change_versions_only_the_changed_profile_under_same_implementation(monkeypatch):
    before = contracts.club_strategy_contracts()['contracts']
    catalogue = deepcopy(TIPSTER_CATALOG)
    catalogue[0]['selection_policy']['min_probability'] += .01
    monkeypatch.setattr(contracts, 'TIPSTER_CATALOG', catalogue)
    after = contracts.club_strategy_contracts()['contracts']
    assert before[0]['version_id'] != after[0]['version_id']
    assert before[1:] == after[1:]


def test_selector_implementation_change_versions_declarations(monkeypatch):
    before = contracts.club_strategy_contracts()['contracts']
    monkeypatch.setattr(contracts, '_sources', lambda: {'tipsters/recommendations.py': 'a' * 64})
    after = contracts.club_strategy_contracts()['contracts']
    assert all(a['version_id'] != b['version_id'] for a, b in zip(before, after, strict=True))


def test_self_signed_relaxed_snapshot_has_no_authority():
    c = contracts.club_strategy_contracts()['contracts'][0]
    assert contracts.matches_current_contract(c)
    c['odds_band']['min'] = 1.01
    body = {k: v for k, v in c.items() if k not in {'version_id', 'contract_sha256'}}
    c['contract_sha256'] = canonical_sha256(body)
    c['version_id'] = c['profile_id'] + '-v1-' + c['contract_sha256'][:16]
    assert not contracts.matches_current_contract(c)
    assert not contracts.matches_current_contract({'profile_id': 'daily223'})


@pytest.mark.parametrize('change', ['unknown_market', 'duplicate_identity', 'bad_odds', 'bad_shape',
                                   'bad_weights', 'required_model_disabled'])
def test_bad_source_catalogue_is_visible_error_not_an_invented_default(monkeypatch, change):
    catalogue = deepcopy(TIPSTER_CATALOG)
    if change == 'unknown_market':
        catalogue[0]['selection_policy']['allowed_market_keys'] = ['UNKNOWN']
    elif change == 'duplicate_identity':
        catalogue[1]['profile_id'] = catalogue[0]['profile_id']
    elif change == 'bad_odds':
        catalogue[0]['target_odds_band']['min'] = 1.1
    elif change == 'bad_shape':
        catalogue[0]['min_legs'] = True
    elif change == 'bad_weights':
        catalogue[0]['model_weights']['gauss'] = 10
    else:
        catalogue[1]['model_weights'] = {'gauss': 0.0, 'kepler': .5, 'laplace': .5}
    monkeypatch.setattr(contracts, 'TIPSTER_CATALOG', catalogue)
    with pytest.raises(ValueError):
        contracts.club_strategy_contracts()


def test_dashboard_exposes_current_contracts_without_relabelling_old_saved_cards(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('No provider/LLM network allowed'))
    saved = {'profile_id': TIPSTER_CATALOG[0]['profile_id'], 'strategy_version': 'historical-original',
             'legs': [], 'combined_price': None}
    original = deepcopy(saved)
    value = ArthurState.tipster_snapshot({'enabled_profiles': [saved['profile_id']]}, [], [saved], {})
    assert len(value['strategy_contracts']['contracts']) == 20
    assert value['recommendations'] == [original] and saved == original
    assert value['recommendations'][0]['strategy_version'] == 'historical-original'


def test_optional_contract_read_failure_cannot_hide_tickets(monkeypatch):
    def failed():
        raise OSError('Synthetic source unavailable')
    monkeypatch.setattr(contracts, 'club_strategy_contracts', failed)
    saved = {'profile_id': TIPSTER_CATALOG[0]['profile_id'], 'legs': []}
    view = ArthurState.tipster_snapshot({'enabled_profiles': [saved['profile_id']]}, [], [saved], {})
    assert view['recommendations'] == [saved]
    assert view['strategy_contracts']['status'] == 'UNAVAILABLE'
