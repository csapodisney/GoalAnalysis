"""Source-bound player availability challenger; explicit SHADOW, never LIVE.

Player facts screen directions and adjust a bounded heuristic rank/required
edge. They never manufacture a player-adjusted probability or a healthy squad.
"""
import hashlib
import math
import sqlite3
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.cup_specialist_shadow import _persona, _prices
from goal_analysis.jobs.fixture_contexts import raw_source
from goal_analysis.jobs.the_stats_context import _canonical, normalize_fact_data
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.storage.fixture_contexts import FixtureContexts
from goal_analysis.tipsters.recommendations import _fixtures, _models, _options, _pool, _ranked

VERSION = 'player-specialist-shadow-v2'
TTL = {'squad': 86400, 'player_season_stats': 86400, 'injuries_suspensions': 21600, 'lineup': 5400}
SOURCE_FILES = ('jobs/player_specialist_shadow.py', 'jobs/cup_specialist_shadow.py',
                'jobs/xg_specialist_shadow.py', 'jobs/the_stats_context.py', 'storage/fixture_contexts.py',
                'jobs/fixture_contexts.py', 'agents/grok_stats_data.py',
                'storage/canonical_registry.py', 'normalization/canonical.py', 'tipsters/recommendations.py',
                'tipsters/catalog.py', 'quote_metadata.py', 'normalization/live_identity.py',
                'normalization/identity.py', 'normalization/leakage.py', 'storage/temporal.py',
                'engine/ticket_options.py', 'engine/ticket_scoring.py', 'engine/model_recommendations.py',
                'team/metrics.py', 'providers/the_stats_odds.py', 'storage/raw_evidence.py',
                'storage/source_observations.py', 'storage/http_observations.py')
ROLES = {**dict.fromkeys(('F', 'FW', 'FORWARD', 'ATTACKER'), 'ATTACKER'),
         **dict.fromkeys(('M', 'MF', 'MIDFIELDER'), 'MIDFIELDER'),
         **dict.fromkeys(('D', 'DF', 'DEFENDER'), 'DEFENDER'),
         **dict.fromkeys(('G', 'GK', 'GOALKEEPER'), 'GOALKEEPER')}


class _ReadConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class _ReadDatabase:
    def __init__(self, path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, factory=_ReadConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return db


def _contexts(root, fixtures, cutoff):
    """Read already source-validated append-only contexts and actual witnesses.

    This is not a second API/cache ingestion path. Schema/identity/availability
    are rechecked; stale newest versions never fall back to convenient old ones.
    """
    store = FixtureContexts(_ReadDatabase(root / 'data/goal_analysis.sqlite3'))
    views, issues, total = {}, Counter(), 0
    raw_cache, bytes_read = {}, 0
    for fid, fixture in fixtures.items():
        try:
            canonical = store.registry.resolve('the_stats_api', 'fixture', fixture['provider_fixture_id'], as_of=cutoff)
            entity = store.registry.entity(canonical, as_of=cutoff) if canonical else None
            if entity is None:
                raise ValueError('MISSING_CANONICAL_PLAYER_CONTEXT')
            for field, kind, native in (('home_team_id', 'team', 'provider_home_team_id'),
                                       ('away_team_id', 'team', 'provider_away_team_id'),
                                       ('competition_id', 'competition', 'provider_league_id'),
                                       ('season_id', 'season', 'provider_season_id')):
                if getattr(entity, field) != store.registry.resolve('the_stats_api', kind, fixture[native], as_of=cutoff):
                    raise ValueError('PLAYER_CONTEXT_FIXTURE_IDENTITY_MISMATCH')
            if entity.kickoff != utc(fixture['kickoff']):
                raise ValueError('PLAYER_CONTEXT_KICKOFF_MISMATCH')
            rows = store.records(canonical, as_of=cutoff, categories=set(TTL), limit=1001)
            total += len(rows)
            if len(rows) > 1000 or total > 10000:
                raise OverflowError('INCOMPLETE_PLAYER_CONTEXT_BUDGET')
            groups = {}
            for row in rows:
                groups.setdefault((row['category'], row['subject_id']), []).append(row)
            accepted = []
            for values in groups.values():
                newest = max(utc(v['observed_at']) for v in values)
                latest = [v for v in values if utc(v['observed_at']) == newest]
                if len({canonical_sha256(v['payload']) for v in latest}) != 1:
                    issues['CONFLICTING_LATEST_PLAYER_CONTEXT'] += 1
                    continue
                row = max(latest, key=lambda r: utc(r['available_at']))
                if (cutoff - newest).total_seconds() > TTL[row['category']]:
                    issues['STALE_' + row['category'].upper()] += 1
                    continue
                payload = normalize_fact_data(row['category'], row['payload'])
                category = row['category']
                if category == 'lineup':
                    valid = (payload.get('match_id') == fixture['provider_fixture_id']
                             and all(payload.get(side + '_team_id') == fixture['provider_' + side + '_team_id']
                                     for side in ('home', 'away')))
                else:
                    valid = payload.get('team_id') in {fixture['provider_home_team_id'], fixture['provider_away_team_id']}
                    kind = 'player' if category == 'player_season_stats' else 'team'
                    native = payload.get('player_id' if kind == 'player' else 'team_id')
                    valid = valid and store.registry.resolve('the_stats_api', kind, native, as_of=cutoff) == row['subject_id']
                    if category == 'player_season_stats':
                        valid = valid and (payload.get('competition_id') == fixture['provider_league_id']
                                           and payload.get('season_id') == fixture['provider_season_id'])
                if not valid:
                    issues['PLAYER_FACT_SCOPE_MISMATCH'] += 1
                    continue
                source = {'provider': 'the_stats_api', 'source_url': row['source_url'],
                          'content_hash': row['source_sha256'], 'observed_at': row['observed_at'],
                          'resource_type': category, 'published_at': None}
                cache_key = canonical_sha256(source)
                if cache_key not in raw_cache:
                    with store.database.connect() as db:
                        raw_row = db.execute('SELECT size_bytes FROM raw_snapshots WHERE content_hash=?',
                                             (row['source_sha256'],)).fetchone()
                    if raw_row is None or raw_row['size_bytes'] > 4 * 1024 * 1024:
                        raise ValueError('MISSING_OR_OVERSIZED_PLAYER_RAW_SOURCE')
                    if len(raw_cache) >= 1000 or bytes_read + raw_row['size_bytes'] > 64 * 1024 * 1024:
                        raise OverflowError('INCOMPLETE_PLAYER_SOURCE_BUDGET')
                    raw_cache[cache_key] = raw_source(root, store.database, source, cutoff)
                    bytes_read += raw_row['size_bytes']
                subject = payload.get('player_id' if category == 'player_season_stats' else
                                      'match_id' if category == 'lineup' else 'team_id')
                mapping = {'match_id': fixture['provider_fixture_id'], 'competition_id': fixture['provider_league_id'],
                           'season_id': fixture['provider_season_id'], 'home_team_id': fixture['provider_home_team_id'],
                           'away_team_id': fixture['provider_away_team_id']}
                if _canonical(category, raw_cache[cache_key], subject, mapping) != payload:
                    raise ValueError('PLAYER_FACT_DIFFERS_FROM_ORIGINAL_BYTES')
                accepted.append(row)
            views[fid] = accepted
        except (ValueError, KeyError, TypeError) as error:
            issues[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return views, dict(issues), total


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _regular(stats):
    fields = ('appearances', 'starts', 'minutes_played')
    if not all(_number(stats.get(k)) for k in fields):
        return False
    return (stats['appearances'] >= stats['starts'] >= 5 and stats['minutes_played'] >= 450
            and stats['minutes_played'] <= 130 * stats['appearances'])


def _production(stats):
    """Do not divide partial xG by full-season minutes; units stay separate."""
    games = stats.get('xg_matches')
    if not _number(games) or not 5 <= games <= stats['appearances']:
        return None
    for actual, expected, threshold in (('goals', 'expected_goals', .35), ('assists', 'expected_assists', .25)):
        if (_number(stats.get(actual)) and _number(stats.get(expected))
                and stats[actual] * 90 / stats['minutes_played'] >= threshold
                and stats[expected] / games >= .25):
            return {'kind': 'SCORER' if actual == 'goals' else 'CREATOR',
                    'observed_per_90': stats[actual] * 90 / stats['minutes_played'],
                    'expected_per_covered_match': stats[expected] / games,
                    'covered_matches': games, 'fields_used': [actual, expected, 'xg_matches', 'minutes_played']}
    return None


def _signals(fixture, rows):
    teams = [fixture['provider_home_team_id'], fixture['provider_away_team_id']]
    stats, roles, absences, proofs = {}, {}, {}, {}
    starters, confirmed, predicted = set(), False, False
    gaps, signals = Counter(), []
    for row in rows:
        data, category = row['payload'], row['category']
        if category == 'player_season_stats':
            stats[(data['team_id'], data['player_id'])] = data, row['event_id']
        elif category == 'squad' and data.get('truncated') is False:
            for p in data.get('players', []):
                roles.setdefault((data['team_id'], p.get('player_id')), set()).add(ROLES.get(str(p.get('position')).upper(), 'UNKNOWN'))
        elif category == 'injuries_suspensions' and all(isinstance(data.get(k), list) for k in ('injuries', 'suspensions')):
            absences[data['team_id']] = data
        elif category == 'lineup' and data.get('confirmed') is True and data.get('type') == 'team_sheet':
            players = [data.get(side + '_players', []) for side in ('home', 'away')]
            ids = [p.get('player_id') for ps in players for p in ps]
            if all(len(ps) == 11 for ps in players) and None not in ids and len(set(ids)) == 22:
                confirmed = True
                for side, ps in zip(('home', 'away'), players):
                    for p in ps:
                        key = data[side + '_team_id'], p['player_id']
                        starters.add(key)
                        roles.setdefault(key, set()).add(ROLES.get(str(p.get('position')).upper(), 'UNKNOWN'))
        elif category == 'lineup' and data.get('confirmed') is False and data.get('type') == 'predicted':
            # Information/uncertainty only; never infer a starter, role or OUT
            # from a predicted XI. Existing entropy and scoring rules stay fixed.
            players = [data.get(side + '_players', []) for side in ('home', 'away')]
            ids = [p.get('player_id') for ps in players for p in ps]
            predicted = all(len(ps) == 11 for ps in players) and None not in ids and len(set(ids)) == 22
        proofs[row['event_id']] = {k: row[k] for k in ('event_id', 'category', 'source_sha256', 'source_url',
                                                       'observed_at', 'available_at', 'availability_basis')}
    entropy = (.5 if not confirmed else 0) + .25 * sum(team not in absences for team in teams)
    if not confirmed:
        gaps['CONFIRMED_XI_MISSING'] += 1
    conflict = False
    seen = set()
    for team, data in absences.items():
        for category in ('injuries', 'suspensions'):
            for absence in data[category]:
                key = team, absence.get('player_id')
                if absence.get('active') is not True or key in seen:
                    continue
                # A doubtful status or a suspension in another competition is not OUT.
                if category == 'injuries' and str(absence.get('status', '')).casefold() not in {'out', 'unavailable', 'ruled_out'}:
                    gaps['UNCONFIRMED_ABSENCE'] += 1
                    continue
                if category == 'suspensions' and absence.get('competition_id') != fixture['provider_league_id']:
                    gaps['SUSPENSION_COMPETITION_UNKNOWN_OR_DIFFERENT'] += 1
                    continue
                day = utc(fixture['kickoff']).date().isoformat()
                try:
                    dates = {k: date.fromisoformat(absence[k][:10]).isoformat()
                             for k in ('start_date', 'end_date') if absence.get(k)}
                except ValueError:
                    gaps['INVALID_ABSENCE_DATE'] += 1
                    continue
                if any(value > day if k == 'start_date' else value < day for k, value in dates.items()):
                    gaps['ABSENCE_DATE_OUTSIDE_MATCH'] += 1
                    continue
                seen.add(key)
                if key in starters:
                    conflict = True
                    gaps['OUT_BUT_CONFIRMED_STARTING'] += 1
                    continue
                position = roles.get(key, {'UNKNOWN'})
                if len(position) != 1 or 'UNKNOWN' in position:
                    gaps['POSITION_UNKNOWN_OR_CONFLICTING'] += 1
                    continue
                player_stats, event_id = stats.get(key, ({}, None))
                if not _regular(player_stats):
                    gaps['REGULAR_PLAYER_STATS_MISSING_OR_THIN'] += 1
                    continue
                role = next(iter(position))
                production = _production(player_stats) if role in {'ATTACKER', 'MIDFIELDER'} else None
                if role in {'ATTACKER', 'MIDFIELDER'} and production is None:
                    gaps['SCORING_OR_CREATION_SUPPORT_MISSING'] += 1
                    continue
                direction = 'LESS_SCORING' if production else 'WEAKER_DEFENCE'
                signals.append({'team_id': team, 'player_id': key[1], 'role': role, 'direction': direction,
                                'production': production, 'stats_event_id': event_id,
                                'strength': 1. if production else .5})
    gaps['ABSENCE_LIST_MISSING'] = sum(team not in absences for team in teams)
    return {'signals': [] if conflict else signals, 'entropy_score': entropy, 'gaps': dict(gaps),
            'conflicting_availability': conflict, 'source_events': list(proofs.values()),
            'confirmed_xi': confirmed, 'predicted_xi_available': predicted,
            'lineup_status': 'CONFIRMED' if confirmed else 'PREDICTED' if predicted else 'MISSING',
            'absence_teams': sorted(absences),
            'scope': 'OBSERVED_PLAYER_SUBSET_NOT_EXHAUSTIVE_SQUAD',
            'unused_fields': ['player_goal_market', 'unconfirmed_lineup', 'non_qualifying_player_totals']}


def _supports(key, signal, fixture):
    if key in {'under25', 'btts_no'}:
        return signal['direction'] == 'LESS_SCORING'
    if key in {'over25', 'btts_yes'}:
        return signal['direction'] == 'WEAKER_DEFENCE'
    if key in {'home', 'away'}:
        return signal['team_id'] == fixture['provider_' + ('away' if key == 'home' else 'home') + '_team_id']
    return False


def inspect_player_specialist(root, enriched, model_results, *, as_of, enabled=False):
    if type(enabled) is not bool:
        raise ValueError('BOOLEAN_SHADOW_FLAG_REQUIRED')
    cutoff, persona = utc(as_of), _persona()
    persona.update(profile_id='shadow_player_specialist', name='Játékosspecialista · SHADOW',
                   tagline='Igazolt kulcshiányzó és külön numerikus kontroll.',
                   personality='Bizonytalan keretnél nagyobb értéktöbbletet kér.')
    recipe = {'version': VERSION, 'persona': persona, 'context_ttl_seconds': TTL,
              'minimum_starts': 5, 'minimum_minutes': 450, 'minimum_xg_matches': 5,
              'scorer_min_goals_per_90': .35, 'creator_min_assists_per_90': .25,
              'minimum_xg_or_xa_per_covered_match': .25,
              'required_edge': '.05 + .03 * entropy', 'rank_multiplier': '1 + .04 * strength - .02 * entropy',
              'probability_adjustment': 'NONE', 'causal_or_calibrated_effect': False,
              'implementation_files_sha256': {name: hashlib.sha256(
                  (Path(__file__).resolve().parent.parent / name).read_bytes()).hexdigest() for name in SOURCE_FILES}}
    result = {'version': VERSION, 'mode': 'SHADOW', 'status': 'DISABLED', 'enabled': enabled,
              'profile_id': persona['profile_id'],
              'as_of': cutoff.isoformat(), 'recipe': recipe, 'recipe_sha256': canonical_sha256(recipe),
              'candidate_legs': [], 'supported_selections': [],
              'combined_price': None, 'probability': None, 'playable': False,
              'selected_for_portfolio': False, 'live_decision_effect': False, 'probability_adjustment': 0,
              'new_external_requests': 0, 'production_writes': 0, 'actual_new_shared_model_fits': 0,
              'calibrated': False, 'full_shared_model_pit_certified': False}
    if not enabled:
        return result
    if (not isinstance(enriched, dict) or not isinstance(enriched.get('fixtures'), list)
            or len(enriched['fixtures']) > 1000 or not isinstance(enriched.get('candidates'), list)
            or len(enriched['candidates']) > 10000 or any(not isinstance(c, dict) for c in enriched['candidates'])
            or not isinstance(model_results, dict)):
        raise ValueError('BOUNDED_PLAYER_SPECIALIST_INPUT_REQUIRED')
    try:
        root = Path(root).resolve()
        fixtures = {k: f for k, f in _fixtures(enriched['fixtures'], enriched['date'], cutoff).items()
                    if (utc(f['kickoff']) - cutoff).total_seconds() > 900}
        contexts, context_issues, read_count = _contexts(root, fixtures, cutoff)
        views = {fid: _signals(fixtures[fid], rows) for fid, rows in contexts.items()}
        targets, target_proofs, target_issues = _targets(root, fixtures, cutoff)
        eligible = {fid: fixtures[fid] for fid in targets if views.get(fid, {}).get('signals')}
        prices, receipts, price_issues = _prices(root, [c for c in enriched['candidates']
                                                      if c.get('fixture_id') in eligible], eligible, cutoff)
        models = _models(model_results, eligible, cutoff)
        pool, valid_quotes = _pool({**enriched, 'candidates': prices}, eligible, models, cutoff)
        controlled, ranked, effects = _ranked(pool, persona, cutoff), [], []
        for row in controlled:
            fid = row['quote']['fixture_id']
            view = views[fid]
            support = [s for s in view['signals'] if _supports(row['key'], s, fixtures[fid])]
            # Do not cherry-pick a favourable absence from contradictory directions.
            if not support or len(support) != len(view['signals']):
                continue
            required = .05 + .03 * view['entropy_score']
            if row['probability'] * row['quote']['decimal_price'] - 1 < required:
                continue
            multiplier = 1 + .04 * max(s['strength'] for s in support) - .02 * view['entropy_score']
            score = min(1., max(0., row['score'] * multiplier))
            ranked.append({**row, 'score': score,
                           'player_rank_multiplier': multiplier, 'required_estimated_edge': required})
            effects.append({'quote': deepcopy(row['quote']),
                'base_selection_score': row['score'], 'shadow_selection_score': score,
                'player_rank_multiplier': multiplier,
                'applied_score_multiplier': score / row['score'] if row['score'] > 0 else 1.,
                'required_estimated_edge': required, 'shared_control_probability': row['probability'],
                'shared_control_components': deepcopy(row['components']),
                'player_evidence_sha256': canonical_sha256(view),
                'target_receipt': deepcopy(target_proofs[fid]),
                'price_receipt': deepcopy(receipts[row['quote']['candidate_id']])})
        options, search = _options(ranked, persona)
        options = [rows for rows in options if 3 <= len(rows) <= 5
                   and 10 <= math.prod(r['quote']['decimal_price'] for r in rows) <= 50]
        legs = []
        for row in options[0] if options else []:
            quote = deepcopy(row['quote'])
            fid = quote['fixture_id']
            quote.update(source_candidate_id=quote['candidate_id'], selection_score=row['score'],
                         score_kind='BOUNDED_PLAYER_HEURISTIC_NOT_PROBABILITY',
                         shared_control_probability=row['probability'], shared_control_components=deepcopy(row['components']),
                         player_rank_multiplier=row['player_rank_multiplier'],
                         required_estimated_edge=row['required_estimated_edge'], player_evidence=deepcopy(views[fid]),
                         target_receipt=deepcopy(target_proofs[fid]), price_receipt=deepcopy(receipts[quote['candidate_id']]))
            legs.append(quote)
        result.update(status='SHADOW_CANDIDATE' if legs else 'ABSTAINED', candidate_legs=legs,
                      supported_selections=effects,
                      combined_price=math.prod(l['decimal_price'] for l in legs) if legs else None,
                      reason='CONSTRUCTED_SHADOW_ONLY' if legs else 'NO_PLAYER_SUPPORTED_CONTROLLED_COMBINATION',
                      fixture_evidence=views,
                      diagnostics={'read_context_events': read_count, 'context_issues': context_issues,
                                   'context_categories': dict(Counter(r['category'] for rs in contexts.values() for r in rs)),
                                   'player_supported_fixtures': sum(bool(v['signals']) for v in views.values()),
                                   'witnessed_fresh_target_fixtures': len(targets), 'target_issues': target_issues,
                                   'verified_raw_price_candidates': len(prices), 'price_issues': price_issues,
                                   'valid_quote_candidates': valid_quotes, 'control_modeled_fixtures': len(models),
                                   'control_eligible_selections': len(controlled), 'player_ranked_selections': len(ranked),
                                   'compliant_combinations': len(options), 'search': search})
    except (ValueError, TypeError, KeyError, OSError, OverflowError, sqlite3.Error) as error:
        result.update(status='UNAVAILABLE', reason=str(error) if isinstance(error, (ValueError, OverflowError)) else type(error).__name__)
    return result
