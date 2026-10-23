"""Immutable canonical-context reader, witnessed prices and real local selector."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_xg_specialist_shadow import NOW, OBS
from test_xg_specialist_shadow import environment as target_environment

from goal_analysis.jobs.player_specialist_shadow import (
    _signals,
    _supports,
    inspect_player_specialist,
)
from goal_analysis.normalization.canonical import (
    CanonicalCompetition,
    CanonicalFixture,
    CanonicalPlayer,
    CanonicalSeason,
    CanonicalTeam,
    canonical_id,
)
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.canonical_registry import (
    CanonicalRegistry,
    MappingEvidence,
    ProviderBinding,
)
from goal_analysis.storage.fixture_contexts import FixtureContexts
from goal_analysis.storage.temporal import TemporalStore


def cid(kind, native):
    return canonical_id(kind, 'the_stats_api:' + native)


def environment(root, *, role='F', kind='injuries', status='out', active=True, stat_change=None,
                context_when=OBS, receipt_when=NOW - timedelta(seconds=5), squad=True,
                confirmed=False, injury_lists=True):
    data, models, db, meta = target_environment(root, history_teams=[])
    registry = CanonicalRegistry(db, clock=lambda: NOW - timedelta(minutes=1))
    registry.initialize()
    comp = data['fixtures'][0]['provider_league_id']
    entities = [CanonicalCompetition(entity_id=cid('competition', comp), name='Test cup'),
                CanonicalSeason(entity_id=cid('season', 'sn_1'), competition_id=cid('competition', comp), label='2026')]
    entities += [CanonicalTeam(entity_id=cid('team', 'tm_' + str(i)), name='Team ' + str(i)) for i in range(2, 8)]
    entities += [CanonicalPlayer(entity_id=cid('player', 'pl_' + str(i)), name='Player ' + str(i)) for i in range(2, 8)]
    entities += [CanonicalFixture(entity_id=cid('fixture', f['provider_fixture_id']),
                  competition_id=cid('competition', comp), season_id=cid('season', 'sn_1'),
                  home_team_id=cid('team', f['provider_home_team_id']), away_team_id=cid('team', f['provider_away_team_id']),
                  kickoff=NOW + timedelta(hours=6), status='SCHEDULED') for f in data['fixtures']]
    bindings = [ProviderBinding('the_stats_api', e.KIND, 'sn_1' if e.KIND == 'season' else
                comp if e.KIND == 'competition' else next(n for n in
                [*(f'mt_{i}' for i in range(1, 4)), *(f'tm_{i}' for i in range(2, 8)),
                 *(f'pl_{i}' for i in range(2, 8))] if cid(e.KIND, n) == e.entity_id), e.entity_id) for e in entities]
    registry.register(entities, bindings, MappingEvidence('NATIVE_PROVIDER_ID', meta.source_url, meta.content_hash, OBS))
    store = FixtureContexts(db, clock=lambda: receipt_when)
    store.initialize()
    events = []
    snapshots = SnapshotStore(db, root / 'data/raw')

    def event(f, category, native, payload):
        if category == 'player_season_stats':
            raw = {**payload, 'scoring': {k: payload.get(k) for k in (
                'goals', 'assists', 'expected_goals', 'expected_assists', 'xg_matches')}}
            url = f'https://api.thestatsapi.com/api/football/players/{native}/stats?season_id=sn_1&competition_id={comp}'
        elif category == 'squad':
            raw = [{'id': p['player_id'], 'name': p['name'], 'position': p['position'],
                    'current_team': {'id': native}} for p in payload['players']]
            url = f'https://api.thestatsapi.com/api/football/teams/{native}/players'
        elif category == 'injuries_suspensions':
            raw = deepcopy(payload)
            for suspension in raw['suspensions']:
                suspension['competition'] = {'id': suspension['competition_id']}
            url = f'https://api.thestatsapi.com/api/football/teams/{native}/injuries-suspensions?current=true'
        else:
            raw = {k: payload[k] for k in ('match_id', 'type', 'confirmed')}
            for side in ('home', 'away'):
                raw[side] = {'id': payload[side + '_team_id'], 'starting_xi': [
                    {'id': p['player_id'], 'name': p.get('name'), 'position': p['position']}
                    for p in payload[side + '_players']]}
            url = 'https://api.thestatsapi.com/api/football/matches/' + f['provider_fixture_id'] + '/lineups'
        proof = snapshots.save('the_stats_api', category, json.dumps({'data': raw}).encode(),
                               'application/json', fetched_at=context_when, source_url=url)
        return {'fixture_id': cid('fixture', f['provider_fixture_id']), 'category': category,
                'subject_id': cid('player' if category == 'player_season_stats' else 'team', native) if native else None,
                'payload': payload, 'source_provider': 'the_stats_api', 'source_url': url,
                'source_sha256': proof.content_hash, 'origin_reference': 'test-validated-context-link',
                'event_time': None, 'observed_at': context_when.isoformat(), 'published_at': None}

    for f in data['fixtures']:
        # An absent away regular supports the existing home-control quote.
        away = f['provider_away_team_id']
        player = 'pl_' + away[3:]
        stats = {'player_id': player, 'team_id': away, 'season_id': 'sn_1', 'competition_id': comp,
                 'appearances': 10, 'starts': 10, 'minutes_played': 900, 'goals': 8, 'assists': 3,
                 'expected_goals': 5., 'expected_assists': 3., 'xg_matches': 10, **(stat_change or {})}
        if stat_change and 'team_id' in stat_change:
            stats['team_id'] = f['provider_home_team_id']
        events.append(event(f, 'player_season_stats', player, stats))
        if squad:
            events.append(event(f, 'squad', away, {'team_id': away, 'players': [
                {'player_id': player, 'name': 'Player', 'position': role}], 'total': 1, 'truncated': False}))
        if injury_lists:
            for team in (f['provider_home_team_id'], away):
                absence = {'player_id': player, 'active': active}
                absence.update({'status': status} if kind == 'injuries' else {'competition_id': comp})
                events.append(event(f, 'injuries_suspensions', team, {'team_id': team, 'injuries': [],
                    'suspensions': [], **({kind: [absence]} if team == away else {})}))
        if confirmed:
            payload = {'match_id': f['provider_fixture_id'], 'type': 'team_sheet', 'confirmed': True}
            for side in ('home', 'away'):
                team = f['provider_' + side + '_team_id']
                payload[side + '_team_id'] = team
                payload[side + '_players'] = [{'player_id': 'pl_' + str(1000 * int(team[3:]) + i),
                                             'name': None, 'position': 'M'} for i in range(11)]
            events.append(event(f, 'lineup', None, payload))
    store.append(events)
    rows = [row for f in data['fixtures'] for row in store.records(cid('fixture', f['provider_fixture_id']), as_of=receipt_when)]
    return data, models, db, rows


def run(root, data, models):
    with patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')):
        return inspect_player_specialist(root, data, models, as_of=NOW, enabled=True)


def test_disabled_needs_no_input_or_database(tmp_path):
    result = inspect_player_specialist(tmp_path / 'missing', None, None, as_of=NOW)
    assert result['status'] == 'DISABLED' and not (tmp_path / 'missing').exists()


def test_confirmed_key_absence_changes_selection_rank_and_edge_not_probability(tmp_path):
    data, models, _, _ = environment(tmp_path)
    original = deepcopy((data, models))
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE', result
    assert len(result['candidate_legs']) == 3 and result['combined_price'] == pytest.approx(2.3 ** 3)
    assert (data, models) == original and not result['live_decision_effect'] and not result['playable']
    for leg in result['candidate_legs']:
        assert leg['shared_control_probability'] == .6 and leg['quoted_at'] is None
        assert leg['player_rank_multiplier'] == pytest.approx(1.03)
        assert leg['required_estimated_edge'] == pytest.approx(.065)
        assert leg['player_evidence']['entropy_score'] == .5
        assert leg['player_evidence']['signals'][0]['production']['kind'] == 'SCORER'
        assert all(e['available_at'] for e in leg['player_evidence']['source_events'])
    assert result['probability'] is None and result['probability_adjustment'] == 0


@pytest.mark.parametrize('role', ['GK', 'D', 'M'])
def test_other_verified_regular_positions_are_not_default_attackers(tmp_path, role):
    data, models, _, _ = environment(tmp_path, role=role)
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE'
    signal = result['candidate_legs'][0]['player_evidence']['signals'][0]
    assert signal['role'] in {'GOALKEEPER', 'DEFENDER', 'MIDFIELDER'}
    assert (signal['production'] is None) == (role != 'M')


@pytest.mark.parametrize('options', [{'squad': False}, {'role': None}, {'status': 'doubtful'},
    {'active': False}, {'injury_lists': False}, {'stat_change': {'starts': 4}},
    {'stat_change': {'expected_goals': None, 'expected_assists': None}},
    {'stat_change': {'minutes_played': 1}}, {'stat_change': {'xg_matches': 0}}])
def test_missing_doubtful_or_thin_player_evidence_cannot_create_a_tip(tmp_path, options):
    data, models, _, _ = environment(tmp_path, **options)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_creator_uses_assists_and_xa_without_inventing_zero_goals(tmp_path):
    data, models, _, _ = environment(tmp_path, role='M', stat_change={'goals': None, 'expected_goals': None})
    result = run(tmp_path, data, models)
    signal = result['candidate_legs'][0]['player_evidence']['signals'][0]
    assert signal['production']['kind'] == 'CREATOR'
    assert 'expected_assists' in signal['production']['fields_used']


def test_known_suspension_uses_exact_competition(tmp_path):
    data, models, _, events = environment(tmp_path, kind='suspensions')
    assert run(tmp_path, data, models)['status'] == 'SHADOW_CANDIDATE'
    rows = [deepcopy(e) for e in events if e['fixture_id'] == events[0]['fixture_id']]
    for row in rows:
        row['event_id'] = row['category'] + str(row['subject_id'])
        if row['category'] == 'injuries_suspensions' and row['payload']['suspensions']:
            row['payload']['suspensions'][0]['competition_id'] = 'comp_999'
    assert not _signals(data['fixtures'][0], rows)['signals']


@pytest.mark.parametrize('change', ['stale', 'future_receipt', 'wrong_season', 'wrong_competition', 'wrong_team'])
def test_actual_context_availability_ttl_and_scope_are_required(tmp_path, change):
    kwargs = ({'context_when': NOW - timedelta(days=2)} if change == 'stale' else
              {'receipt_when': NOW + timedelta(seconds=1)} if change == 'future_receipt' else
              {'stat_change': {{'wrong_season': 'season_id', 'wrong_competition': 'competition_id',
                                   'wrong_team': 'team_id'}[change]: {'wrong_season': 'sn_999',
                                   'wrong_competition': 'comp_999', 'wrong_team': 'tm_2'}[change]}})
    data, models, _, _ = environment(tmp_path, **kwargs)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_confirmed_xi_reduces_entropy_but_missing_player_is_not_inferred_absent(tmp_path):
    data, models, _, events = environment(tmp_path, confirmed=True)
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE'
    assert result['candidate_legs'][0]['required_estimated_edge'] == .05
    for row in events:
        row['event_id'] = row['category'] + str(row['subject_id'])
    rows = [e for e in events if e['fixture_id'] == events[0]['fixture_id']]
    assert not _signals(data['fixtures'][0], [r for r in rows if r['category'] != 'injuries_suspensions'])['signals']
    lineup = next(r for r in rows if r['category'] == 'lineup')
    lineup['payload']['away_players'][0] = {'player_id': 'pl_3', 'position': 'F'}
    conflict = _signals(data['fixtures'][0], rows)
    assert conflict['conflicting_availability'] and not conflict['signals']


@pytest.mark.parametrize('broken', ['price', 'models', 'lockout', 'metadata'])
def test_player_evidence_does_not_override_essential_price_model_and_time_gates(tmp_path, broken):
    data, models, _, _ = environment(tmp_path)
    if broken == 'price':
        data['candidates'][0]['decimal_price'] = 3.
    elif broken == 'models':
        models.pop('laplace')
    elif broken == 'lockout':
        data['fixtures'][0]['kickoff'] = (NOW + timedelta(minutes=15)).isoformat()
    else:
        data['fixtures'][0]['fixture_source']['content_hash'] = 'a' * 64
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_attack_and_defence_directions_actually_filter_markets():
    fixture = {'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2'}
    attack = {'direction': 'LESS_SCORING', 'team_id': 'tm_2'}
    defence = {'direction': 'WEAKER_DEFENCE', 'team_id': 'tm_1'}
    assert _supports('under25', attack, fixture) and not _supports('over25', attack, fixture)
    assert _supports('over25', defence, fixture) and not _supports('under25', defence, fixture)
    assert _supports('home', attack, fixture) and not _supports('home', defence, fixture)
    assert not _supports('draw', attack, fixture)


def test_recipe_stable_and_flags_bounded(tmp_path):
    a = inspect_player_specialist(tmp_path, None, None, as_of=NOW)
    b = inspect_player_specialist(tmp_path, None, None, as_of=NOW + timedelta(days=1))
    assert a['recipe_sha256'] == b['recipe_sha256']
    with pytest.raises(ValueError, match='BOOLEAN'):
        inspect_player_specialist(tmp_path, {}, {}, as_of=NOW, enabled='live')
    with pytest.raises(ValueError, match='BOUNDED'):
        inspect_player_specialist(tmp_path, {'fixtures': [{}] * 1001}, {}, as_of=NOW, enabled=True)


def test_entropy_threshold_changes_acceptance_for_same_positive_numeric_edge(tmp_path):
    for confirmed in (False, True):
        root = tmp_path / str(confirmed)
        data, models, _, _ = environment(root, confirmed=confirmed)
        for model in models.values():
            for p in model['predictions']:
                p['probabilities'].update(home=.46, draw=.27, away=.27)
        result = run(root, data, models)
        assert (result['status'] == 'SHADOW_CANDIDATE') is confirmed


def test_scorer_absence_selects_under_from_actual_source_prices(tmp_path):
    data, models, db, _ = environment(tmp_path)
    store = SnapshotStore(db, tmp_path / 'data/raw')
    for quote in data['candidates']:
        body = {'match_id': quote['provider_fixture_id'], 'bookmakers': [{'bookmaker': 'Synthetic', 'markets': {
            'total_goals': {'2.5': {'over': {'last_seen': 1.7}, 'under': {'last_seen': 2.3}}}}}]}
        raw = store.save('the_stats_api', 'totals', json.dumps({'data': body}).encode(), 'application/json',
                         fetched_at=OBS, source_url=quote['quote_source_id'])
        quote.update(market_key='totals_2_5', selection_key='under', provider_market_id='total_goals',
                     point=2.5, line=2.5, quote_source_sha256=raw.content_hash)
    TemporalStore(db, tmp_path / 'data/raw', clock=lambda: NOW - timedelta(seconds=1)).capture('raw', limit=100)
    for model in models.values():
        for p in model['predictions']:
            p['probabilities'].update(over25=.35, under25=.65)
            p['stability'].update(over25=.02, under25=.02)
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE', result
    assert all(l['selection_key'] == 'under' for l in result['candidate_legs'])


def test_newest_conflicting_context_is_unknown_without_convenient_fallback(tmp_path):
    data, models, db, events = environment(tmp_path)
    base = next(e for e in events if e['category'] == 'squad')
    changed = {k: deepcopy(v) for k, v in base.items() if k not in {
        'version', 'event_id', 'ingested_at', 'available_at', 'availability_basis'}}
    changed['payload']['players'][0]['position'] = 'D'
    FixtureContexts(db, clock=lambda: NOW - timedelta(seconds=1)).append([changed])
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED'
    assert result['diagnostics']['context_issues']['CONFLICTING_LATEST_PLAYER_CONTEXT'] == 1


def test_missing_database_is_optional_unavailable_without_creation(tmp_path):
    result = run(tmp_path / 'missing', {'date': NOW.date().isoformat(), 'fixtures': [], 'candidates': []}, {})
    assert result['status'] in {'UNAVAILABLE', 'ABSTAINED'}
    assert not (tmp_path / 'missing').exists()


def test_invalid_absence_dates_are_not_interpreted_as_available_evidence(tmp_path):
    data, _, _, events = environment(tmp_path)
    rows = [r for r in events if r['fixture_id'] == events[0]['fixture_id']]
    for row in rows:
        if row['category'] == 'injuries_suspensions':
            for injury in row['payload']['injuries']:
                injury['start_date'] = 'nonsense'
    view = _signals(data['fixtures'][0], rows)
    assert not view['signals'] and view['gaps']['INVALID_ABSENCE_DATE'] == 1


def test_stored_context_cannot_disagree_with_its_actual_source_bytes(tmp_path):
    data, models, db, events = environment(tmp_path)
    base = next(e for e in events if e['category'] == 'player_season_stats')
    changed = {k: deepcopy(v) for k, v in base.items() if k not in {
        'version', 'event_id', 'ingested_at', 'available_at', 'availability_basis'}}
    changed['observed_at'] = (NOW - timedelta(seconds=10)).isoformat()
    changed['payload']['goals'] = 99
    FixtureContexts(db, clock=lambda: NOW - timedelta(seconds=1)).append([changed])
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED'
    assert result['diagnostics']['context_issues']['PLAYER_FACT_DIFFERS_FROM_ORIGINAL_BYTES'] == 1


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_context_hash_and_receipt_cannot_replace_missing_or_corrupt_source_file(tmp_path, damage):
    data, models, db, events = environment(tmp_path)
    source = next(e['source_sha256'] for e in events if e['category'] == 'player_season_stats')
    with db.connect() as connection:
        path = connection.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?', (source,)).fetchone()[0]
    path = tmp_path / 'data/raw' / path
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b' ')
    result = run(tmp_path, data, models)
    assert result['status'] in {'ABSTAINED', 'UNAVAILABLE'} and not result['candidate_legs']
