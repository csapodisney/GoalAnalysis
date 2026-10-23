"""Original UEFA source bytes, real raw/receipt stores, synthetic league tables.

Private test pins authorize controlled review bundles only inside these tests.
Production trusts solely the independently sealed digest shipped in the reader.
"""
import json
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from test_competition_types import competition, match, setup

from goal_analysis.features.stakes import build_stakes_features
from goal_analysis.jobs import official_rules_shadow as module
from goal_analysis.jobs.stakes_shadow import inspect_stakes
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.providers.the_stats_api import TheStatsApiClient, TheStatsApiError
from goal_analysis.storage.temporal import TemporalStore

FIXTURES = Path(__file__).parent/'fixtures/official-rules-202627'
ORIGINAL = json.loads((FIXTURES/'reviewed-bundle.json').read_bytes())
NOW = utc(ORIGINAL['available_at'])+timedelta(minutes=1)
SCOPES = [(r['competition_id'], r['season_id']) for r in ORIGINAL['records']]


def full_table():
    return [{'team': {'id': f'tm_{i}'}, 'group_label': None, 'position': i,
             'matches_played': 2, 'wins': 0, 'draws': 2, 'losses': 0,
             'goals_for': 0, 'goals_against': 0, 'goal_difference': 0, 'points': 2}
            for i in range(1, 37)]


def seal(tmp_path, bundle, monkeypatch):
    raw = json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    digest = sha256(raw).hexdigest()
    folder = tmp_path/'data/arthur/official-rules';folder.mkdir(parents=True, exist_ok=True)
    (folder/(digest+'.json')).write_bytes(raw)
    monkeypatch.setattr(module, 'REVIEWED_BUNDLE', digest)
    return folder/(digest+'.json')


def prepared(tmp_path, monkeypatch):
    comp, season = SCOPES[0]
    m = match(season_id=season, stage_name='league_phase', utc_date=(NOW+timedelta(days=1)).isoformat())
    db, _raw, temporal, put, f, _, _ = setup(tmp_path, catalogue=[competition()], matches=[m])
    f.update(provider_season_id=season, kickoff=m['utc_date'])
    bundle = deepcopy(ORIGINAL)
    for record in bundle['records']:
        url = 'https://api.thestatsapi.com/api/football/competitions/'+record['competition_id']+'/seasons'
        source = put([record['native_season']], 'daily_context', url)
        record['native_season_source'] = {'provider': 'the_stats_api', 'source_url': url,
            'content_hash': source.content_hash, 'observed_at': source.fetched_at.isoformat()}
        for doc in record['documents']:
            name = 'europa' if record['competition_id'] == comp else 'conference'
            number = 17 if 'Article 17' in doc['title'] else 18
            blob = (FIXTURES/f'{name}-article-{number}-body.html').read_bytes()
            folder = tmp_path/'data/arthur/official-rules';folder.mkdir(parents=True, exist_ok=True)
            (folder/(doc['sha256']+'.html')).write_bytes(blob)
    temporal.capture('raw', limit=100)
    seal(tmp_path, bundle, monkeypatch)
    url = f'https://api.thestatsapi.com/api/football/competitions/{comp}/seasons/{season}/standings'
    put(full_table(), 'daily_context', url, when=NOW-timedelta(minutes=5))
    TemporalStore(db, tmp_path/'data/raw', clock=lambda: NOW-timedelta(minutes=2)).capture('raw', limit=100)
    return db, put, f, bundle


def target():
    return {'fixture_id': 'mt_1', 'competition_id': SCOPES[0][0], 'season_id': SCOPES[0][1],
            'home_team_id': 'tm_1', 'away_team_id': 'tm_2', 'kickoff': (NOW+timedelta(days=1)).isoformat(),
            'phase': 'LEAGUE_PHASE', 'status': 'scheduled', 'is_neutral': False}


def table_envelope():
    p = {'competition_id': SCOPES[0][0], 'season_id': SCOPES[0][1], 'raw_rows': full_table()}
    return {'record_id': 'test-table', 'payload': p, 'payload_sha256': sha(p),
            'times': module._times(NOW.isoformat(), NOW.isoformat())}


def test_original_bundle_and_article_hashes_are_the_reviewed_production_pin():
    assert sha256((FIXTURES/'reviewed-bundle.json').read_bytes()).hexdigest() == module.REVIEWED_BUNDLE
    for record in ORIGINAL['records']:
        name = 'europa' if record['competition_id'] == 'comp_7739' else 'conference'
        for doc, number in zip(record['documents'], (17, 18), strict=True):
            assert sha256((FIXTURES/f'{name}-article-{number}-body.html').read_bytes()).hexdigest() == doc['sha256']


def test_actual_reader_integrates_edition_and_rules_but_not_ties_or_motivation(tmp_path, monkeypatch):
    _, _, f, _ = prepared(tmp_path, monkeypatch)
    before = deepcopy(f)
    report = inspect_stakes(tmp_path, [f], as_of=NOW)
    feature = report['features'][0]
    assert feature['rules_status'] == 'VERIFIED_FIXED_TOP_K'
    assert feature['official_rules_context']['status'] == 'REVIEWED_SOURCE_BOUND'
    assert report['counts'] == {'CONSERVATIVE_POINTS_BOUNDS_SHADOW': 2}
    assert all(t['games_remaining'] == 6 and t['maximum_on_field_points'] == 20 for t in feature['teams'])
    assert all(o['ties'] == 'NOT_RESOLVED' and o['status'] == 'UNRESOLVED_BOUNDS'
               for t in feature['teams'] for o in t['objectives'])
    assert [o['kind'] for o in feature['teams'][0]['objectives']] == ['ROUND_OF_16', 'KNOCKOUT_PHASE']
    assert feature['teams'][0]['mental_motivation'] is None and not report['live_decision_effect']
    assert report['external_requests'] == report['production_writes'] == 0 and f == before


def test_europa_and_conference_schedules_are_separate_exact_scope_bindings(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch)
    contexts = module.inspect_official_rules(tmp_path, SCOPES, as_of=NOW)
    assert {k: v['rules']['scheduled_games'] for k, v in contexts.items()} == {SCOPES[0]: 8, SCOPES[1]: 6}
    for scope in SCOPES:
        t = {**target(), 'competition_id': scope[0], 'season_id': scope[1]}
        table = table_envelope();table['payload'].update(competition_id=scope[0], season_id=scope[1])
        table['payload_sha256'] = sha(table['payload'])
        view, rules = module.bind_rules(contexts[scope], t, table, as_of=NOW)
        result = build_stakes_features({**t, 'table_group': None}, table, as_of=NOW, rules=rules)
        assert view['applicability'] == 'CONSERVATIVE_POINTS_BOUNDS_SHADOW'
        assert result['teams'][0]['games_remaining'] == (6 if scope == SCOPES[0] else 4)


@pytest.mark.parametrize('when', [utc(ORIGINAL['available_at'])-timedelta(microseconds=1), NOW+timedelta(days=8)])
def test_review_not_known_yet_or_expired_is_never_retroactively_available(tmp_path, monkeypatch, when):
    prepared(tmp_path, monkeypatch)
    ctx = module.inspect_official_rules(tmp_path, SCOPES, as_of=when)
    assert all(v['status'] == 'UNKNOWN' for v in ctx.values())


@pytest.mark.parametrize('fault', ['article_bytes', 'bundle_bytes', 'no_season', 'season_bytes',
                                  'wrong_edition', 'wrong_games', 'wrong_tie_order', 'wrong_article',
                                  'wrong_map_edition', 'future_document', 'duplicate_season'])
def test_broken_or_invented_source_chain_cannot_certify_rules(tmp_path, monkeypatch, fault):
    db, put, _, bundle = prepared(tmp_path, monkeypatch);r = bundle['records'][0]
    folder = tmp_path/'data/arthur/official-rules'
    if fault == 'article_bytes':
        p = folder/(r['documents'][0]['sha256']+'.html');p.write_bytes(p.read_bytes()+b' ')
    elif fault == 'bundle_bytes':
        p = folder/(module.REVIEWED_BUNDLE+'.json');p.write_bytes(p.read_bytes()+b' ')
    elif fault == 'no_season':
        r['native_season_source']['content_hash'] = '0'*64
    elif fault == 'season_bytes':
        p = tmp_path/'data/raw'/f"the_stats_api/daily_context/{r['native_season_source']['content_hash']}.bin"
        p.write_bytes(p.read_bytes()+b' ')
    elif fault == 'wrong_edition':r['edition'] = '2025/26'
    elif fault == 'wrong_games':r['scheduled_games'] = 38
    elif fault == 'wrong_tie_order':r['tie_break_order'].reverse()
    elif fault == 'wrong_article':r['documents'][0]['content_id'] = 'not-the-official-article'
    elif fault == 'wrong_map_edition':
        next(m for m in r['documents'][0]['map_metadata']['metadata'] if m['key'] == 'FullSeasonYears')['values'] = ['2025/26']
    elif fault == 'future_document':r['documents'][0]['observed_at'] = (NOW+timedelta(days=1)).isoformat()
    elif fault == 'duplicate_season':
        s = put([r['native_season'], r['native_season']], 'daily_context', r['native_season_source']['source_url'])
        r['native_season_source']['content_hash'] = s.content_hash
        TemporalStore(db, tmp_path/'data/raw', clock=lambda: NOW-timedelta(minutes=1)).capture('raw', limit=100)
    if fault not in {'article_bytes', 'bundle_bytes', 'season_bytes'}:seal(tmp_path, bundle, monkeypatch)
    ctx = module.inspect_official_rules(tmp_path, SCOPES, as_of=NOW)
    assert ctx[SCOPES[0]]['status'] == 'UNKNOWN'
    if fault != 'bundle_bytes':assert ctx[SCOPES[1]]['status'] == 'REVIEWED_SOURCE_BOUND'


@pytest.mark.parametrize('fault', ['wrong_season', 'unknown_phase', 'qualifier', 'missing_table',
    'small_roster', 'extra_roster', 'duplicate_team', 'grouped_table', 'duplicate_rank',
    'played_exceeds', 'points_adjusted', 'imbalanced_scores', 'imbalanced_wins', 'odd_draws',
    'missing_target', 'stale_table', 'future_fixture', 'future_table', 'table_hash', 'lockout', 'inactive'])
def test_inapplicable_rules_keep_table_description_without_stakes_claim(tmp_path, monkeypatch, fault):
    prepared(tmp_path, monkeypatch)
    context = module.inspect_official_rules(tmp_path, SCOPES, as_of=NOW)[SCOPES[0]]
    t, table = target(), table_envelope();rows = table['payload']['raw_rows']
    if fault == 'wrong_season':t['season_id'] = 'sn_999'
    elif fault == 'unknown_phase':t['phase'] = 'UNKNOWN'
    elif fault == 'qualifier':t['phase'] = 'QUALIFIER'
    elif fault == 'missing_table':table = None
    elif fault == 'small_roster':rows.pop()
    elif fault == 'extra_roster':rows.append({**deepcopy(rows[-1]), 'team': {'id': 'tm_37'}, 'position': 37})
    elif fault == 'duplicate_team':rows[-1]['team'] = deepcopy(rows[0]['team'])
    elif fault == 'grouped_table':rows[0]['group_label'] = 'A'
    elif fault == 'duplicate_rank':rows[-1]['position'] = 1
    elif fault == 'played_exceeds':rows[0].update(matches_played=10, draws=10, points=10)
    elif fault == 'points_adjusted':rows[0]['points'] = 1
    elif fault == 'imbalanced_scores':rows[0].update(goals_for=1, goal_difference=1)
    elif fault == 'imbalanced_wins':rows[0].update(wins=1, draws=1, points=4)
    elif fault == 'odd_draws':rows[0].update(matches_played=1, draws=1, points=1)
    elif fault == 'missing_target':t['home_team_id'] = 'tm_999'
    elif fault == 'stale_table':table['times'] = module._times((NOW-timedelta(hours=7)).isoformat(), NOW.isoformat())
    elif fault == 'future_fixture':t['kickoff'] = (NOW+timedelta(days=8)).isoformat()
    elif fault == 'future_table':table['times'] = module._times(NOW.isoformat(), (NOW+timedelta(seconds=1)).isoformat())
    elif fault == 'lockout':t['kickoff'] = (NOW+timedelta(minutes=15)).isoformat()
    elif fault == 'inactive':t['status'] = 'postponed'
    if table is not None:
        table['payload_sha256'] = 'invalid' if fault == 'table_hash' else sha(table['payload'])
    view, rules = module.bind_rules(context, t, table, as_of=NOW)
    assert rules is None and view['applicability'] == 'UNKNOWN' and view['reason']


def test_explicit_conflicting_fixture_group_is_not_discarded(tmp_path, monkeypatch):
    _, _, f, _ = prepared(tmp_path, monkeypatch)
    context = module.inspect_official_rules(tmp_path, SCOPES, as_of=NOW)[SCOPES[0]]
    v, e = module.bind_rules(context, target(), table_envelope(), as_of=NOW, source_group='A')
    assert e is None and v['reason'] == 'GROUP_CONFLICT_WITH_OFFICIAL_SINGLE_LEAGUE'
    assert f['provider_season_id'] == SCOPES[0][1]


def test_documented_season_route_keeps_bounded_client_contract():
    calls = []
    def transport(url, headers, **kw):
        calls.append(url);return json.dumps({'data': []}).encode(), {}, 200
    client = TheStatsApiClient('dummy', transport=transport, max_requests=1)
    assert client.request('/football/competitions/comp_7739/seasons') == {'data': []}
    assert calls == ['https://api.thestatsapi.com/api/football/competitions/comp_7739/seasons']
    with pytest.raises(TheStatsApiError):client.request('/football/competitions/comp_7739/seasons', {'year': 2026})
    assert len(calls) == 1


def test_verified_fixture_group_conflict_is_visible_in_the_real_pipeline(tmp_path, monkeypatch):
    db, put, f, _ = prepared(tmp_path, monkeypatch)
    m = match(season_id=SCOPES[0][1], stage_name='league_phase', group_label='A', utc_date=f['kickoff'])
    source = put([m], 'matches', 'https://api.thestatsapi.com/api/football/matches?competition_id=comp_7739')
    f['fixture_source']['content_hash'] = source.content_hash
    TemporalStore(db, tmp_path/'data/raw', clock=lambda: NOW-timedelta(minutes=1)).capture('raw', limit=100)
    v = inspect_stakes(tmp_path, [f], as_of=NOW)['features'][0]
    assert v['official_rules_context']['reason'] == 'GROUP_CONFLICT_WITH_OFFICIAL_SINGLE_LEAGUE'
    assert v['rules_status'] == 'MISSING_VERIFIED_RULES'
    assert all(t['status'] == 'DESCRIPTIVE_ONLY' and not t['objectives'] for t in v['teams'])


def test_native_season_available_after_review_does_not_backdate_the_crosswalk(tmp_path, monkeypatch):
    db, put, _, bundle = prepared(tmp_path, monkeypatch)
    r = bundle['records'][0]
    data = [dict(r['native_season'], another_field='changes-content')]
    source = put(data, 'daily_context', r['native_season_source']['source_url'], when=NOW-timedelta(seconds=40))
    r['native_season'] = data[0]
    r['native_season_source'] = {'provider': 'the_stats_api', 'content_hash': source.content_hash,
        'source_url': r['native_season_source']['source_url'], 'observed_at': source.fetched_at.isoformat()}
    TemporalStore(db, tmp_path/'data/raw', clock=lambda: NOW-timedelta(seconds=30)).capture('raw', limit=100)
    seal(tmp_path, bundle, monkeypatch)
    assert module.inspect_official_rules(tmp_path, SCOPES, as_of=NOW)[SCOPES[0]]['status'] == 'UNKNOWN'


def test_unknown_edition_and_forged_config_are_never_registry_entries(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch)
    (tmp_path/'config').mkdir();(tmp_path/'config/competition-rules.json').write_text('{"verified":true}')
    scope = ('comp_7739', 'sn_999')
    v = module.inspect_official_rules(tmp_path, [scope], as_of=NOW)[scope]
    assert v['status'] == 'UNKNOWN' and v['reason'] == 'NO_REVIEWED_OFFICIAL_RULE_SCOPE'
