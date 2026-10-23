"""A pinned immutable rulebook, not daily caller-supplied personality tuning."""
import json
from copy import deepcopy

import pytest
from test_tipster_personas import NOW, enriched, model_results

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.cup_specialist_shadow import _persona
from goal_analysis.strategy_provenance import strategy_manifest
from goal_analysis.tipsters import profile_config as config
from goal_analysis.tipsters.catalog import TIPSTER_BY_ID, TIPSTER_CATALOG, get_catalogue
from goal_analysis.tipsters.recommendations import build_tipster_recommendations
from goal_analysis.tipsters.strategy_contracts import club_strategy_contracts


def test_twenty_exact_pinned_profiles_round_trip_without_new_parameters():
    original = json.loads(config.CONFIGURATION_PATH.read_text('utf-8'))
    assert len(TIPSTER_CATALOG) == len(TIPSTER_BY_ID) == 20
    assert get_catalogue() == original['profiles'] == json.loads(json.dumps(TIPSTER_CATALOG))
    assert all(p['profile_id'].startswith('tipster_') for p in TIPSTER_CATALOG)
    assert all(10 <= p['target_odds_band']['min'] <= p['target_odds_band']['max'] <= 50 for p in TIPSTER_CATALOG)


@pytest.mark.parametrize('mutate', [
    lambda p: p.__setitem__('min_legs', 1), lambda p: p.__delitem__('selection_policy'),
    lambda p: p.update(min_legs=1), lambda p: p.clear(), lambda p: p.pop('min_legs'),
    lambda p: p.popitem(), lambda p: p.setdefault('new', 0), lambda p: p.__ior__({'min_legs': 1}),
    lambda p: p['selection_policy'].__setitem__('min_edge', -1),
    lambda p: p['model_weights'].update(gauss=0),
    lambda p: p['selection_policy']['required_models'].append('fake'),
    lambda p: p['preferences'].__setitem__(slice(None), ['different']),
    lambda p: p['preferences'].extend(['different']), lambda p: p['preferences'].insert(0, 'other'),
    lambda p: p['preferences'].pop(), lambda p: p['preferences'].remove(p['preferences'][0]),
    lambda p: p['preferences'].reverse(), lambda p: p['preferences'].sort(),
    lambda p: p['preferences'].__iadd__(['extra']), lambda p: p['preferences'].__imul__(2),
])
def test_shared_nested_policy_rejects_normal_mutators(mutate):
    before = canonical_sha256(TIPSTER_CATALOG)
    with pytest.raises(TypeError, match='READ_ONLY'):
        mutate(TIPSTER_CATALOG[0])
    assert canonical_sha256(TIPSTER_CATALOG) == before


def test_top_level_catalogue_and_lookup_cannot_be_replaced_through_container_mutation():
    with pytest.raises(TypeError, match='READ_ONLY'):
        TIPSTER_CATALOG.clear()
    with pytest.raises(TypeError, match='READ_ONLY'):
        TIPSTER_BY_ID[TIPSTER_CATALOG[0]['profile_id']] = {}


def test_display_and_shadow_copies_are_detached_mutable_and_cannot_relax_live_policy():
    original = canonical_sha256(TIPSTER_CATALOG)
    detached = get_catalogue()
    detached[0]['selection_policy']['min_edge'] = -1
    detached[0]['preferences'].append('other')
    shadow = _persona()
    shadow['selection_policy'].update(min_edge=0)
    shadow['preferences'].clear()
    assert canonical_sha256(TIPSTER_CATALOG) == original


@pytest.mark.parametrize('damage', ['missing', 'whitespace', 'self_signed', 'oversized'])
def test_unavailable_modified_or_self_signed_rulebook_is_not_an_authority(tmp_path, monkeypatch, damage):
    path = tmp_path / 'profiles.json'
    raw = config.CONFIGURATION_PATH.read_bytes()
    if damage == 'whitespace':
        path.write_bytes(raw + b' ')
    elif damage == 'self_signed':
        doc = json.loads(raw)
        doc['profiles'][0]['selection_policy']['min_edge'] = -1
        doc['verified'] = True
        doc['sha256'] = canonical_sha256(doc)
        path.write_text(json.dumps(doc), 'utf-8')
    elif damage == 'oversized':
        path.write_bytes(b' ' * (config.MAX_BYTES + 1))
    monkeypatch.setattr(config, 'CONFIGURATION_PATH', path)
    with pytest.raises(config.ProfileConfigurationError):
        config.load_configuration()
    # This running process retains its already validated immutable release.
    assert TIPSTER_CATALOG[0]['selection_policy']['min_edge'] == .03


@pytest.mark.parametrize('damage', ['root', 'version_bool', 'duplicate', 'daily', 'unknown_field',
    'bad_market', 'empty_market', 'weight_bool', 'nonfinite', 'bad_sum', 'disabled_required',
    'leg_bool', 'low_odds', 'empty_preferences', 'policy_field', 'incomplete_contrarian'])
def test_closed_schema_rejects_invalid_fixed_rules_before_any_selector(damage):
    doc = deepcopy(config.load_configuration())
    p = doc['profiles'][0]
    if damage == 'root':
        doc['daily_overrides'] = {}
    elif damage == 'version_bool':
        doc['schema_version'] = True
    elif damage == 'duplicate':
        doc['profiles'][1]['profile_id'] = p['profile_id']
    elif damage == 'daily':
        p['profile_id'] = 'daily223'
    elif damage == 'unknown_field':
        p['dynamic_odds_floor'] = True
    elif damage == 'bad_market':
        p['selection_policy']['allowed_market_keys'] = ['some_other_market']
    elif damage == 'empty_market':
        p['selection_policy']['allowed_market_keys'] = []
    elif damage == 'weight_bool':
        p['model_weights']['gauss'] = True
    elif damage == 'nonfinite':
        p['selection_policy']['min_edge'] = float('nan')
    elif damage == 'bad_sum':
        p['model_weights']['gauss'] = .1
    elif damage == 'disabled_required':
        p['model_weights'] = {'gauss': 0, 'kepler': .5, 'laplace': .5}
        p['selection_policy']['required_models'] = ['gauss']
    elif damage == 'leg_bool':
        p['min_legs'] = True
    elif damage == 'low_odds':
        p['target_odds_band']['min'] = p['reference_odds_band']['min'] = 1
    elif damage == 'empty_preferences':
        p['preferences'] = []
    elif damage == 'policy_field':
        p['selection_policy']['daily_adjustment'] = .5
    else:
        p['selection_policy']['contrarian_value'] = True
    with pytest.raises(config.ProfileConfigurationError):
        config._validate(doc)


def test_duplicate_json_keys_and_nonfinite_constants_are_rejected():
    with pytest.raises(config.ProfileConfigurationError, match='DUPLICATE'):
        config._pairs([('min_edge', .1), ('min_edge', -1)])
    with pytest.raises(config.ProfileConfigurationError, match='NONFINITE'):
        config._constant('NaN')


def test_rulebook_is_bound_to_shared_run_and_each_club_declaration():
    manifest = strategy_manifest({'enabled_profiles': [TIPSTER_CATALOG[0]['profile_id']]})
    assert manifest['source_files']['tipsters/profiles.json'] == config.CONFIGURATION_SHA256
    for contract in club_strategy_contracts()['contracts']:
        assert contract['implementation_files_sha256']['tipsters/profiles.json'] == config.CONFIGURATION_SHA256
        assert 'tipsters/profile_config.py' in contract['implementation_files_sha256']


def test_working_directory_and_environment_do_not_supply_profile_parameters(tmp_path, monkeypatch):
    (tmp_path / 'profiles.json').write_text('{"verified":true}', 'utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ARTHUR_PROFILES_PATH', str(tmp_path / 'profiles.json'))
    assert deepcopy(config.load_configuration()['profiles']) == get_catalogue()


def test_reinitialization_cannot_rewrite_an_existing_shared_container():
    before = canonical_sha256(TIPSTER_CATALOG)
    TIPSTER_CATALOG[0].__init__({'min_legs': 1})
    TIPSTER_CATALOG[0]['selection_policy']['required_models'].__init__(['fake'])
    assert canonical_sha256(TIPSTER_CATALOG) == before


def test_daily_data_and_settings_cannot_override_the_personality():
    data, models = enriched(), model_results()
    settings = {'enabled_profiles': list(TIPSTER_BY_ID)}
    before = build_tipster_recommendations(data, models, settings, NOW)
    assert any(c['legs'] for c in before)
    overrides = {'min_legs': 1, 'target_odds_band': {'min': 1, 'max': 2},
                 'model_weights': {'gauss': 1, 'kepler': 0, 'laplace': 0},
                 'selection_policy': {'min_edge': -1}}
    data['profiles'] = data['profile_overrides'] = overrides
    settings.update(profiles=overrides, profile_overrides=overrides, dynamic_profile_tuning=True)
    after = build_tipster_recommendations(data, models, settings, NOW)
    assert after == before


def test_thin_daily_supply_abstains_without_changing_fixed_thresholds():
    data = enriched()
    data['candidates'] = []
    before = canonical_sha256(TIPSTER_CATALOG)
    cards = build_tipster_recommendations(data, model_results(), {'enabled_profiles': list(TIPSTER_BY_ID)}, NOW)
    assert len(cards) == 20 and all(not c['legs'] and c['status'] == 'ABSTAINED' for c in cards)
    assert canonical_sha256(TIPSTER_CATALOG) == before
