"""Reviewed original rules plus exact native-season receipts, read only.

An arbitrary config/LLM flag cannot certify a competition rule. New editions
require a new engineering review and immutable bundle digest in this module.
The seven-day window is local review policy, never an invented season end.
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from goal_analysis.features.stakes import normalize_table
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import model_inputs, require_known_before
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.source_observations import read_source_raw

VERSION = 'official-competition-rules-shadow-v1'
REVIEWED_BUNDLE = '92064b47701b8c18f344d16aec750ad81212bd89006d39b8c16e628c9f3a3a00'
SCOPES = {'comp_7739': ('Europa', 'sn_9624824', 8),
          'comp_408698': ('Conference', 'sn_7250309', 6)}
TIE_BREAKS = ['GOAL_DIFFERENCE', 'GOALS_FOR', 'AWAY_GOALS', 'WINS', 'AWAY_WINS',
              'OPPONENT_POINTS', 'OPPONENT_GOAL_DIFFERENCE', 'OPPONENT_GOALS',
              'LOWER_DISCIPLINARY_POINTS', 'CLUB_COEFFICIENT']
OBJECTIVES = [{'kind': 'ROUND_OF_16', 'top_k': 8, 'unconditional': True},
              {'kind': 'KNOCKOUT_PHASE', 'top_k': 24, 'unconditional': True}]
DFL_BUNDLE = 'fb2aafccf86394dfc4db33e7185537c255c6406eb7f67bce1c0aef7f35c0c3b4'
DFL_SEMANTICS = {
    'competition_id': 'comp_0406', 'season_id': 'sn_6190936', 'edition': '2026/27',
    'phase': 'LEAGUE_PHASE', 'group': None, 'participants': 18, 'scheduled_games': 34,
    'points': {'win': 3, 'draw': 1, 'loss': 0},
    'objectives': [{'kind': 'TITLE', 'top_k': 1, 'unconditional': True},
        {'kind': 'DIRECT_PROMOTION', 'top_k': 2, 'unconditional': True},
        {'kind': 'DIRECT_SURVIVAL', 'top_k': 15, 'unconditional': True}],
    'tie_break_order': ['GOAL_DIFFERENCE', 'GOALS_FOR', 'HEAD_TO_HEAD_AGGREGATE',
        'HEAD_TO_HEAD_AWAY_GOALS', 'AWAY_GOALS', 'NEUTRAL_DECIDING_MATCH'],
    'tie_break_scope': 'DURING_SEASON_FIRST_TWO_UNLESS_BOTH_HEAD_TO_HEAD_LEGS_PLAYED_NO_DECIDING_MATCH',
    'rank_policy': 'SHARED_COMPETITION_POSITIONS', 'tie_resolution_implemented': False,
    'conditional_routes': [{'rank': 3, 'route': 'BUNDESLIGA_PROMOTION_PLAYOFF', 'automatic_promotion': False},
        {'rank': 16, 'route': 'THIRD_TIER_RELEGATION_PLAYOFF', 'automatic_relegation': False}],
    'direct_relegation_ranks': [17, 18],
    'scope_limitations': 'ON_FIELD_POINTS_ONLY_NOT_LICENSING_ADMINISTRATIVE_EXCEPTIONS_OR_PLAYOFF_OUTCOMES',
    'publisher_revision': '2026-08-04'}
DFL_DOCUMENTS = (
    ('index', 'https://www.bundesliga-gruppe.de/ueber-uns/statuten/', 'html'),
    ('spol', 'https://media.bundesliga-gruppe.de/sites/2/2026/08/Spielordnung-SpOL-2026-08-04-Stand.pdf', 'pdf'),
    ('participants', 'https://www.bundesliga.com/en/2bundesliga/table/2026-2027', 'html'))


def _bytes(path, digest, limit):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('MISSING_OR_OVERSIZED_OFFICIAL_RULE_SOURCE')
    raw = path.read_bytes()
    if len(raw) > limit or sha256(raw).hexdigest() != digest:
        raise ValueError('OFFICIAL_RULE_SOURCE_HASH_MISMATCH')
    return raw


def _times(observed, available):
    return {'observed_at': observed, 'available_at': available,
            'published_at': None, 'ingested_at': None, 'event_time': None,
            'event_time_kind': 'UNKNOWN', 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}


def _bundle(root, cutoff):
    folder = root/'data/arthur/official-rules'
    bundle = json.loads(_bytes(folder/(REVIEWED_BUNDLE+'.json'), REVIEWED_BUNDLE, 200_000))
    if (bundle['version'] != VERSION or bundle['local_review_max_age_days'] != 7
            or bundle['available_at'] != bundle['reviewed_at'] or len(bundle['records']) != 2):
        raise ValueError('UNREVIEWED_RULE_BUNDLE')
    require_known_before(_times(bundle['reviewed_at'], bundle['available_at']), as_of=cutoff)
    if cutoff-utc(bundle['reviewed_at']) > timedelta(days=7):
        raise ValueError('OFFICIAL_RULE_REVIEW_EXPIRED')
    return bundle


def _documents(root, bundle, record, cutoff):
    name, season, games = SCOPES[record['competition_id']]
    if (record['season_id'] != season or record['edition'] != '2026/27'
            or record['phase'] != 'LEAGUE_PHASE' or record['group'] is not None
            or record['participants'] != 36 or record['scheduled_games'] != games
            or record['points'] != {'win': 3, 'draw': 1, 'loss': 0}
            or record['objectives'] != OBJECTIVES or record['tie_break_order'] != TIE_BREAKS
            or record['tie_resolution_implemented'] is not False or len(record['documents']) != 2):
        raise ValueError('UNREVIEWED_RULE_SEMANTICS')
    reader = f'https://documents.uefa.com/r/Regulations-of-the-UEFA-{name}-League-2026/27-Online'
    proofs = []
    for doc, number in zip(record['documents'], (17, 18), strict=True):
        expected = ('https://documents.uefa.com/api/khub/maps/'+doc['map_id']+
                    '/topics/'+doc['content_id']+'/content')
        meta = {m['key']: m['values'] for m in doc['map_metadata']['metadata']}
        if (doc['url'] != expected or doc['reader_url'] != reader
                or doc['map_metadata']['readerUrl'] != reader
                or doc['map_metadata']['mapId'] != doc['map_id']
                or doc['node']['contentId'] != doc['content_id']
                or not doc['title'].startswith(f'Article {number} ')
                or meta['FullSeasonYears'] != ['2026/27'] or meta['ft:locale'] != ['en-GB']):
            raise ValueError('OFFICIAL_RULE_EDITION_OR_ARTICLE_MISMATCH')
        raw = _bytes(root/'data/arthur/official-rules'/(doc['sha256']+'.html'), doc['sha256'], 32_000)
        if len(raw) != doc['size_bytes']:
            raise ValueError('OFFICIAL_RULE_DOCUMENT_SIZE_MISMATCH')
        times = _times(doc['observed_at'], bundle['available_at'])
        require_known_before(times, as_of=cutoff)
        proofs.append({'url': doc['url'], 'reader_url': reader, 'article': number,
                       'content_sha256': doc['sha256'], 'times': times})
    return proofs


def _season(db, root, record, cutoff):
    source = record['native_season_source']
    expected = 'https://api.thestatsapi.com/api/football/competitions/'+record['competition_id']+'/seasons'
    if source['source_url'] != expected:
        raise ValueError('NATIVE_SEASON_SCOPE_MISMATCH')
    row = db.execute('''SELECT rowid AS _sequence,* FROM raw_snapshots
        WHERE provider='the_stats_api' AND content_hash=?''', (source['content_hash'],)).fetchone()
    if row is None or row['size_bytes'] > 2_000_000:
        raise ValueError('MISSING_NATIVE_SEASON_SOURCE')
    body, proof = read_source_raw(db, dict(row), root, cutoff, source)
    if not isinstance(body['data'], list) or len(body['data']) > 200:
        raise ValueError('BOUNDED_NATIVE_SEASON_LIST_REQUIRED')
    selected = [r for r in body['data'] if r.get('id') == record['season_id']]
    if (len(selected) != 1 or selected[0] != record['native_season']
            or type(selected[0].get('start_year')) is not int or selected[0]['start_year'] != 2026
            or type(selected[0].get('end_year')) is not int or selected[0]['end_year'] != 2027
            or selected[0].get('year') != '26/27'):
        raise ValueError('UNVERIFIED_NATIVE_SEASON_EDITION')
    return proof


def _dfl_bundle(root, cutoff):
    folder = root/'data/arthur/official-rules'
    bundle = json.loads(_bytes(folder/(DFL_BUNDLE+'.json'), DFL_BUNDLE, 200_000))
    if (bundle['version'] != VERSION or bundle['local_review_max_age_days'] != 7
            or bundle['available_at'] != bundle['reviewed_at'] or len(bundle['records']) != 1):
        raise ValueError('UNREVIEWED_DFL_BUNDLE')
    require_known_before(_times(bundle['reviewed_at'],bundle['available_at']),as_of=cutoff)
    if cutoff-utc(bundle['reviewed_at']) > timedelta(days=7):
        raise ValueError('OFFICIAL_RULE_REVIEW_EXPIRED')
    return bundle


def _dfl_documents(root, bundle, record, cutoff):
    semantic = {k:v for k,v in record.items() if k not in {'documents','native_season','native_season_source'}}
    if sha(semantic) != sha(DFL_SEMANTICS) or len(record['documents']) != len(DFL_DOCUMENTS):
        raise ValueError('UNREVIEWED_DFL_SEMANTICS')
    proofs = []
    for doc,(kind,url,extension) in zip(record['documents'],DFL_DOCUMENTS,strict=True):
        if doc['kind'] != kind or doc['url'] != url or doc['extension'] != extension:
            raise ValueError('OFFICIAL_DFL_SOURCE_IDENTITY_MISMATCH')
        blob = _bytes(root/'data/arthur/official-rules'/(doc['sha256']+'.'+extension),doc['sha256'],2_000_000)
        if len(blob) != doc['size_bytes'] or (extension == 'pdf' and not blob.startswith(b'%PDF')):
            raise ValueError('OFFICIAL_DFL_SOURCE_SIZE_OR_FORMAT_MISMATCH')
        times = _times(doc['observed_at'],bundle['available_at'])
        require_known_before(times,as_of=cutoff)
        proofs.append({'url':url,'kind':kind,'content_sha256':doc['sha256'],'times':times})
    return proofs


def inspect_official_rules(root, scopes, *, as_of):
    """Zero HTTP/writes. Failure in one scope cannot discard another or tickets."""
    if not isinstance(scopes, list) or len(scopes) > 1000:
        raise ValueError('BOUNDED_RULE_SCOPES_REQUIRED')
    root, cutoff = Path(root).resolve(), utc(as_of)
    contexts = {tuple(s): {'status': 'UNKNOWN', 'reason': 'NO_REVIEWED_OFFICIAL_RULE_SCOPE',
                          'registry_version': VERSION, 'live_decision_effect': False} for s in scopes}
    bundles = []
    for reader,digest,documents,competitions in (
            (_bundle,REVIEWED_BUNDLE,_documents,set(SCOPES)),
            (_dfl_bundle,DFL_BUNDLE,_dfl_documents,{'comp_0406'})):
        try:
            bundles.append((reader(root,cutoff),digest,documents))
        except (ValueError,TypeError,KeyError,OSError):
            for scope,ctx in contexts.items():
                if scope[0] in competitions:ctx['reason'] = 'RULE_BUNDLE_UNAVAILABLE_AT_CUTOFF_OR_EXPIRED'
    path = root/'data/goal_analysis.sqlite3'
    if not path.is_file():
        return contexts
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        for bundle,digest,document_reader in bundles:
            for record in bundle['records']:
                scope = (record['competition_id'], record['season_id'])
                if scope not in contexts:continue
                try:
                    documents = document_reader(root, bundle, record, cutoff)
                    season = _season(db, root, record, cutoff)
                    if utc(season['times']['available_at']) > utc(bundle['available_at']):
                        raise ValueError('SEASON_NOT_AVAILABLE_AT_REVIEW')
                    contexts[scope] = {'status': 'REVIEWED_SOURCE_BOUND', 'reason': None,
                        'registry_version': VERSION, 'bundle_sha256': digest,
                        'rules': deepcopy({k: v for k, v in record.items() if k not in {
                            'documents', 'native_season_source', 'native_season'}}),
                        'source_receipts': documents+[season], 'reviewed_at': bundle['reviewed_at'],
                        'available_at': bundle['available_at'], 'live_decision_effect': False}
                except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
                    contexts[scope]['reason'] = 'INVALID_OR_UNAVAILABLE_RULE_SOURCE_CHAIN'
    return contexts


def bind_rules(context, target, table, *, as_of, source_group=None):
    """Return conservative math inputs only for complete, unambiguous tables.

    Official tie-break order is metadata; it is NOT applied to a partial table.
    No model probability, mental-motivation claim or specialist rank is changed.
    """
    view = deepcopy(context);view['applicability'] = 'UNKNOWN';envelope = None
    if view['status'] != 'REVIEWED_SOURCE_BOUND':return view, envelope
    rules = view['rules'];cutoff = utc(as_of)
    try:
        if target.get('status') != 'scheduled' or cutoff >= utc(target['kickoff'])-timedelta(minutes=15):
            raise ValueError('TARGET_INACTIVE_OR_PREMATCH_LOCKOUT')
        if (rules['competition_id'], rules['season_id']) != (target['competition_id'], target['season_id']):
            raise ValueError('RULE_SCOPE_MISMATCH')
        if target.get('phase') != rules['phase']:
            raise ValueError('UNKNOWN_OR_DIFFERENT_PHASE')
        if source_group is not None or ('table_group' in target and target['table_group'] is not None):
            raise ValueError('GROUP_CONFLICT_WITH_OFFICIAL_SINGLE_LEAGUE')
        start = utc(view['reviewed_at']);end = start+timedelta(days=7)
        if not start <= cutoff <= end or not start <= utc(target['kickoff']) < end:
            raise ValueError('OUTSIDE_LOCAL_REVIEW_WINDOW')
        if table is None:
            raise ValueError('MISSING_COMPLETE_WITNESSED_TABLE')
        if cutoff-utc(table['times']['observed_at']) > timedelta(hours=6):
            raise ValueError('STALE_TABLE')
        payload = model_inputs([table], as_of=cutoff)[0]
        if (payload['competition_id'], payload['season_id']) != (target['competition_id'], target['season_id']):
            raise ValueError('TABLE_SCOPE_MISMATCH')
        rows = normalize_table(payload['raw_rows'])
        ranks = Counter(r['rank'] for r in rows)
        if rules.get('rank_policy') == 'SHARED_COMPETITION_POSITIONS':
            # 1,2,2,4 is valid competition ranking; 1,2,2,3 is not.
            expected, valid_ranks = 1, True
            for rank,count in sorted(ranks.items()):
                valid_ranks &= rank == expected
                expected += count
            valid_ranks &= expected == rules['participants']+1
            triples = [(r['points'],r['goal_difference'],r['goals_for'])
                       for r in sorted(rows,key=lambda r:r['rank'])]
            valid_ranks &= triples == sorted(triples,reverse=True)
            for rank in ranks:
                values = {(r['points'],r['goal_difference'],r['goals_for']) for r in rows if r['rank']==rank}
                valid_ranks &= len(values) == 1
        else:
            valid_ranks = set(ranks) == set(range(1,rules['participants']+1))
        if (len(rows) != rules['participants'] or any(r['group'] is not None for r in rows)
                or not valid_ranks):
            raise ValueError('INCOMPLETE_OR_AMBIGUOUS_PHASE_PARTICIPANTS')
        games = rules['scheduled_games']
        if any(r['played'] > games or r['points'] != 3*r['wins']+r['draws'] for r in rows):
            raise ValueError('UNSUPPORTED_TABLE_POINTS_OR_GAMES')
        if (sum(r['wins'] for r in rows) != sum(r['losses'] for r in rows)
                or sum(r['draws'] for r in rows) % 2
                or sum(r['goals_for'] for r in rows) != sum(r['goals_against'] for r in rows)):
            raise ValueError('INCONSISTENT_COMPLETE_PHASE_TOTALS')
        by_team = {r['team_id']: r for r in rows}
        if any(t not in by_team or by_team[t]['played'] >= games
               for t in (target['home_team_id'], target['away_team_id'])):
            raise ValueError('TARGET_NOT_IN_REMAINING_PHASE_SCHEDULE')
        # This math has deliberately unresolved ties and overestimates rival
        # ceilings. Complete observed participants are not a schedule-feasibility solver.
        p = {'competition_id': rules['competition_id'], 'season_id': rules['season_id'],
             'verification_basis': 'REVIEWED_OFFICIAL_RULES_AND_COMPLETE_PARTICIPANTS',
             'format': 'FIXED_POINTS_TOP_K', 'scope': 'ON_FIELD_POINTS_ONLY',
             'phase': rules['phase'], 'group': None, 'valid_from': start.isoformat(),
             'valid_to': end.isoformat(), 'validity_basis': 'LOCAL_REVIEW_WINDOW_NOT_OFFICIAL_SEASON_END',
             'points_per_win': rules['points']['win'], 'scheduled_games_by_team': {t: games for t in by_team},
             'objectives': deepcopy(rules['objectives']), 'official_bundle_sha256': view['bundle_sha256'],
             'tie_break_order': rules['tie_break_order'], 'tie_resolution_implemented': False}
        envelope = {'record_id': sha([p, view['source_receipts'], table['record_id']]),
                    'payload': p, 'payload_sha256': sha(p),
                    'times': _times(view['reviewed_at'], view['available_at']),
                    'source_receipts': deepcopy(view['source_receipts'])}
        view['applicability'] = 'CONSERVATIVE_POINTS_BOUNDS_SHADOW';view['reason'] = None
    except (ValueError, KeyError, TypeError) as error:
        view['reason'] = str(error) if isinstance(error, ValueError) else 'INVALID_RULE_OR_TABLE_SCHEMA'
    return view, envelope
