"""Shared local experts: sourced context and guarded ranking, never probability edits."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

VERSION = 'arthur-shared-experts-v1'
GROUPS = ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets', 'profiles')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _stamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Aware timestamp required')
    return stamp.astimezone(UTC)


def weight_options(options, weather):
    """Direction-specific utility adjustment, only after prospective validation.

    Existing profile templates and all their eligibility checks stay intact.
    An average of leg multipliers cannot amplify long tickets exponentially.
    """
    output = deepcopy(options)
    by_id = {row['fixture_id']: row for row in weather.get('fixtures', [])}
    changed = 0
    for profile, rows in output.items():
        if profile == 'daily223':
            continue
        for option in rows:
            card = option.get('recommendation') or option.get('ticket') or {}
            if card.get('profile_id') == 'daily223' or not option.get('scoring'):
                continue
            if option['scoring'].get('weather_expert'):
                continue  # Replaying a reviewed option must not compound its adjustment.
            quality = option.get('quality')
            if type(quality) not in (float, int) or not math.isfinite(quality) or quality < 0:
                continue
            factors = []
            for leg in card.get('legs', []):
                factor = 1.0
                finding = by_id.get(leg.get('fixture_id'), {})
                key = {'over': 'over25', 'under': 'under25'}.get(leg.get('selection_key'))
                if (finding.get('live_weight_eligible') is True and key
                        and leg.get('period') == 'FULL_TIME' and leg.get('market_key') == 'totals_2_5'):
                    candidate = finding.get('market_effects', {}).get(key, 1.0)
                    if isinstance(candidate, dict):
                        candidate = candidate.get('score_multiplier', 1.0)
                    if type(candidate) in (float, int) and math.isfinite(candidate) and 0.9 <= candidate <= 1.1:
                        factor = candidate
                factors.append(factor)
            factor = math.exp(math.fsum(math.log(x) for x in factors) / len(factors)) if factors else 1.0
            if abs(factor - 1.0) > 1e-10:
                option['quality'] = quality * factor
                option['scoring']['weather_expert'] = {'version': weather.get('version'),
                    'base_quality': quality, 'multiplier': factor,
                    'kind': 'VALIDATED_RANKING_UTILITY_NOT_WIN_PROBABILITY'}
                changed += 1
    return output, changed


def _poisson_market_probabilities(home_rate, away_rate):
    """Small, deterministic xG cross-check; it is not a calibrated forecast."""
    total = home_rate + away_rate
    under25 = math.exp(-total) * (1 + total + total * total / 2)
    home, draw, away = 0.0, 0.0, 0.0
    for home_goals in range(11):
        hp = math.exp(-home_rate) * home_rate ** home_goals / math.factorial(home_goals)
        for away_goals in range(11):
            ap = math.exp(-away_rate) * away_rate ** away_goals / math.factorial(away_goals)
            probability = hp * ap
            if home_goals > away_goals:
                home += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away += probability
    return {
        ('totals_2_5', 'over'): 1 - under25,
        ('totals_2_5', 'under'): under25,
        ('btts', 'yes'): (1 - math.exp(-home_rate)) * (1 - math.exp(-away_rate)),
        ('btts', 'no'): 1 - (1 - math.exp(-home_rate)) * (1 - math.exp(-away_rate)),
        ('h2h', 'home'): home,
        ('h2h', 'draw'): draw,
        ('h2h', 'away'): away,
    }


def _stats_fixture_signal(record):
    """Return a sourced season-xG cross-check only for an exact two-team match."""
    if not isinstance(record, dict) or record.get('status') != 'MATCHED':
        return None
    mapping = record.get('mapping') or {}
    by_team = {fact.get('subject_id'): fact for fact in record.get('facts', [])
               if isinstance(fact, dict) and fact.get('category') == 'team_season_stats'
               and fact.get('status') == 'AVAILABLE'}
    home = by_team.get(mapping.get('home_team_id'))
    away = by_team.get(mapping.get('away_team_id'))
    if not home or not away:
        return None
    try:
        kickoff = _stamp(record['kickoff'])
        hd, ad = home['data'], away['data']
        if (hd.get('team_id') != mapping.get('home_team_id')
                or ad.get('team_id') != mapping.get('away_team_id')
                or hd.get('season_id') != mapping.get('season_id')
                or ad.get('season_id') != mapping.get('season_id')
                or hd.get('competition_id') != mapping.get('competition_id')
                or ad.get('competition_id') != mapping.get('competition_id')):
            return None
        hm, am = int(hd['xg_matches']), int(ad['xg_matches'])
        if hm < 5 or am < 5:
            return None
        home_rate = (float(hd['expected_goals_for']) / hm
                     + float(ad['expected_goals_against']) / am) / 2
        away_rate = (float(ad['expected_goals_for']) / am
                     + float(hd['expected_goals_against']) / hm) / 2
        if not all(math.isfinite(value) and 0 <= value <= 8
                   for value in (home_rate, away_rate)):
            return None
        sources = []
        for fact in (home, away):
            source = fact.get('source') or {}
            digest = source.get('content_hash')
            observed = _stamp(source.get('observed_at'))
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(character not in '0123456789abcdef' for character in digest.lower())
                    or observed >= kickoff - timedelta(minutes=15)):
                return None
            sources.append({'subject_id': fact['subject_id'],
                            'content_hash': digest,
                            'observed_at': observed.isoformat(),
                            'source_url': source.get('source_url')})
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

    baseline_home_rate, baseline_away_rate = home_rate, away_rate

    # Player effects are a sourced, bounded cross-check. Missing information is
    # explicit and neutral: it must never be interpreted as a healthy squad.
    def sourced_before_lockout(fact):
        source = fact.get('source') or {}
        digest = source.get('content_hash')
        try:
            observed = _stamp(source.get('observed_at'))
        except (TypeError, ValueError):
            return False
        return (isinstance(digest, str) and len(digest) == 64
                and all(character in '0123456789abcdef' for character in digest.lower())
                and observed < kickoff - timedelta(minutes=15))

    facts = [fact for fact in record.get('facts', []) if isinstance(fact, dict)
             and fact.get('status') == 'AVAILABLE' and sourced_before_lockout(fact)]
    active_absences, absence_team = set(), {}
    for fact in facts:
        if fact.get('category') != 'injuries_suspensions':
            continue
        team_id = fact.get('subject_id')
        for row in fact['data'].get('injuries', []):
            if row.get('active') is True:
                active_absences.add(row.get('player_id'))
                absence_team[row.get('player_id')] = team_id
        for row in fact['data'].get('suspensions', []):
            if (row.get('active') is True and row.get('competition_id') in
                    {None, mapping.get('competition_id')}):
                active_absences.add(row.get('player_id'))
                absence_team[row.get('player_id')] = team_id
    lineup = next((fact for fact in facts if fact.get('category') == 'lineup'), None)
    confirmed = bool(lineup and lineup['data'].get('confirmed') is True)
    starters = {}
    if lineup:
        starters = {
            mapping['home_team_id']: {p.get('player_id') for p in lineup['data'].get('home_players', [])},
            mapping['away_team_id']: {p.get('player_id') for p in lineup['data'].get('away_players', [])},
        }
    def player_role(value):
        token = ''.join(character for character in str(value or '').casefold()
                        if character.isalnum())
        if token in {'gk', 'goalkeeper', 'keeper', 'kapus'}:
            return 'GOALKEEPER'
        if token in {'d', 'df', 'defender', 'defence', 'defense', 'back'}:
            return 'DEFENDER'
        if token in {'m', 'mf', 'midfielder', 'midfield'}:
            return 'MIDFIELDER'
        if token in {'f', 'fw', 'forward', 'striker', 'attacker'}:
            return 'ATTACKER'
        return 'UNKNOWN'

    positions = {}
    for fact in facts:
        if fact.get('category') == 'squad':
            positions.update({row.get('player_id'): player_role(row.get('position'))
                              for row in fact['data'].get('players', [])})
        elif fact.get('category') == 'lineup':
            for side in ('home_players', 'away_players'):
                positions.update({row.get('player_id'): player_role(row.get('position'))
                                  for row in fact['data'].get(side, [])})

    player_context, attack_penalties = [], {mapping['home_team_id']: [],
                                            mapping['away_team_id']: []}
    team_data = {mapping['home_team_id']: hd, mapping['away_team_id']: ad}
    for fact in facts:
        if fact.get('category') != 'player_season_stats':
            continue
        data, player_id = fact['data'], fact.get('subject_id')
        team_id = data.get('team_id')
        if team_id not in team_data:
            continue
        team = team_data[team_id]
        goal_share = ((data.get('goals') or 0) / team['goals_for']
                      if type(team.get('goals_for')) in (int, float) and team['goals_for'] > 0 else 0)
        xg_share = ((data.get('expected_goals') or 0) / team['expected_goals_for']
                    if type(team.get('expected_goals_for')) in (int, float)
                    and team['expected_goals_for'] > 0 else 0)
        share = min(1.0, max(goal_share, xg_share))
        reason = ('ACTIVE_ABSENCE' if player_id in active_absences else
                  'CONFIRMED_NOT_STARTING' if confirmed and player_id not in starters.get(team_id, set())
                  else 'AVAILABLE_OR_UNKNOWN')
        role = positions.get(player_id, 'ATTACKER')
        # Each attacking absence is capped at 12%. Multiple absences combine
        # multiplicatively and the team-level reduction is capped at 15%.
        if reason != 'AVAILABLE_OR_UNKNOWN' and share >= .15:
            attack_penalties[team_id].append(min(.12, .20 * share))
        source = fact.get('source') or {}
        player_context.append({'player_id': player_id, 'team_id': team_id,
            'goals': data.get('goals'), 'expected_goals': data.get('expected_goals'),
            'assists': data.get('assists'), 'expected_assists': data.get('expected_assists'),
            'appearances': data.get('appearances'), 'starts': data.get('starts'),
            'minutes_played': data.get('minutes_played'), 'xg_matches': data.get('xg_matches'),
            'attacking_share': share, 'role': role,
            'availability': reason,
            'source_content_hash': source.get('content_hash'),
            'observed_at': source.get('observed_at')})

    attack_factors, replacement_coverage = {}, {}
    for team_id, penalties in attack_penalties.items():
        combined = 1 - math.prod(1 - value for value in penalties)
        combined = min(.15, combined)
        # A confirmed starting alternative among the provider-shortlisted
        # attackers can cover at most half of the missing production signal.
        replacement_share = max((row['attacking_share'] for row in player_context
                                 if row['team_id'] == team_id
                                 and row['availability'] == 'AVAILABLE_OR_UNKNOWN'
                                 and confirmed
                                 and row['player_id'] in starters.get(team_id, set())),
                                default=0.0)
        coverage = min(.50, .50 * replacement_share)
        replacement_coverage[team_id] = coverage
        attack_factors[team_id] = 1 - combined * (1 - coverage)
    for row in player_context:
        row['attack_multiplier'] = attack_factors[row['team_id']]
        row['replacement_coverage'] = replacement_coverage[row['team_id']]

    # Defender and goalkeeper absences increase only the opponent's independent
    # xG cross-check. Unknown positions remain visible but neutral.
    defence_penalties = {mapping['home_team_id']: [], mapping['away_team_id']: []}
    absence_context = []
    for player_id in sorted(active_absences):
        team_id = absence_team.get(player_id)
        if team_id not in defence_penalties:
            continue
        role = positions.get(player_id, 'UNKNOWN')
        penalty = .06 if role == 'GOALKEEPER' else .03 if role == 'DEFENDER' else 0.0
        if penalty:
            defence_penalties[team_id].append(penalty)
        absence_context.append({'player_id': player_id, 'team_id': team_id,
                                'role': role, 'opponent_xg_uplift': penalty})
    defence_factors = {}
    for team_id, penalties in defence_penalties.items():
        defence_factors[team_id] = 1 + min(.10, math.prod(1 + value for value in penalties) - 1)

    home_rate *= (attack_factors[mapping['home_team_id']]
                  * defence_factors[mapping['away_team_id']])
    away_rate *= (attack_factors[mapping['away_team_id']]
                  * defence_factors[mapping['home_team_id']])
    baseline_probabilities = _poisson_market_probabilities(
        baseline_home_rate, baseline_away_rate)
    probabilities = _poisson_market_probabilities(home_rate, away_rate)
    market_effects = {
        f'{market}:{selection}': (probabilities[(market, selection)] / baseline
                                  if baseline > 0 else 1.0)
        for (market, selection), baseline in baseline_probabilities.items()
    }
    shadow_odds = []
    for fact in facts:
        if fact.get('category') == 'match_odds':
            source = fact.get('source') or {}
            for quote in fact['data'].get('quotes', []):
                shadow_odds.append({**quote, 'source_content_hash': source.get('content_hash'),
                                    'observed_at': source.get('observed_at'),
                                    'timestamp_basis': fact['data'].get('timestamp_basis')})
    injury_facts = [fact for fact in facts if fact.get('category') == 'injuries_suspensions']
    lineup_status = ('CONFIRMED' if confirmed else 'PREDICTED' if lineup else 'MISSING')
    return {'home_xg_rate': home_rate, 'away_xg_rate': away_rate,
            'baseline_home_xg_rate': baseline_home_rate,
            'baseline_away_xg_rate': baseline_away_rate,
            'home_xg_matches': hm, 'away_xg_matches': am,
            'probabilities': probabilities,
            'baseline_probabilities': baseline_probabilities,
            'player_market_effects': market_effects,
            'sources': sources, 'player_context': player_context,
            'absence_context': absence_context,
            'replacement_coverage': replacement_coverage,
            'data_coverage': {
                'injuries': 'AVAILABLE' if injury_facts else 'MISSING',
                'lineup': lineup_status,
                'squad_positions': 'AVAILABLE' if positions else 'MISSING',
                'player_stats_by_team': {
                    team_id: sum(row['team_id'] == team_id for row in player_context)
                    for team_id in team_data},
            },
            'shadow_odds_quotes': shadow_odds,
            'player_availability_applied': (
                any(value < 1 for value in attack_factors.values())
                or any(value > 1 for value in defence_factors.values()))}


def _shadow_quote(leg, signal):
    market = leg.get('market_key')
    point = None
    if market in {'totals_2_5', 'totals_3_5'}:
        point, market = float(market[-3:].replace('_', '.')), 'totals'
    bookmaker = ''.join(c for c in str(leg.get('bookmaker', '')).casefold() if c.isalnum())
    matches = []
    for quote in signal.get('shadow_odds_quotes', []):
        quote_book = ''.join(c for c in str(quote.get('bookmaker', '')).casefold() if c.isalnum())
        if (quote.get('market_key') == market and quote.get('selection_key') == leg.get('selection_key')
                and quote_book == bookmaker and (point is None or quote.get('point') == point)):
            matches.append(quote)
    if len(matches) != 1:
        return None
    quote = matches[0]
    live = leg.get('decimal_price')
    last = quote.get('last_price')
    if type(live) not in (int, float) or type(last) not in (int, float) or live <= 1 or last <= 1:
        return None
    return {**quote, 'candidate_price': live,
            'relative_price_difference': last / live - 1,
            'opening_to_last_movement': (last / quote['opening_price'] - 1
                                         if type(quote.get('opening_price')) in (int, float)
                                         and quote['opening_price'] > 1 else None),
            'shadow_only': True}


def weight_sports_context(options, collection):
    """Use exact TheStats season xG as a bounded ranking tie-breaker.

    The calculation never changes odds or model probabilities. Unsupported
    markets and sparse/unmatched provider rows remain neutral. DAILY_223 is
    byte-for-byte protected.
    """
    output = deepcopy(options)
    signals = {fixture_id: signal for fixture_id, record in
               (collection.get('context_by_fixture') or {}).items()
               if (signal := _stats_fixture_signal(record)) is not None}
    weighted, attached, player_context_legs, player_adjusted_legs = 0, 0, 0, 0
    for profile, rows in output.items():
        if profile == 'daily223':
            continue
        for option in rows:
            card = option.get('recommendation') or option.get('ticket') or {}
            if card.get('profile_id') == 'daily223' or not option.get('scoring'):
                continue
            if option['scoring'].get('the_stats_api'):
                continue
            quality = option.get('quality')
            if type(quality) not in (float, int) or not math.isfinite(quality) or quality < 0:
                continue
            factors, leg_evidence = [], []
            for index, leg in enumerate(card.get('legs', [])):
                signal = signals.get(leg.get('fixture_id'))
                probability = (signal or {}).get('probabilities', {}).get(
                    (leg.get('market_key'), leg.get('selection_key')))
                price = leg.get('decimal_price')
                if (leg.get('period') != 'FULL_TIME' or type(probability) not in (int, float)
                        or type(price) not in (int, float) or price <= 1):
                    factors.append(1.0)
                    continue
                value_margin = probability * price - 1
                factor = min(1.04, max(.96, 1 + .08 * value_margin))
                base_probability = leg.get('model_probability')
                shadow_probability = None
                if (type(base_probability) in (int, float) and math.isfinite(base_probability)
                        and 0 < base_probability < 1):
                    # Research-only ensemble candidate. It is persisted for
                    # walk-forward calibration and never replaces the live
                    # probability until a later promotion gate succeeds.
                    base = min(.99, max(.01, base_probability))
                    cross_check = min(.99, max(.01, probability))
                    logit = .75 * math.log(base / (1 - base)) + .25 * math.log(
                        cross_check / (1 - cross_check))
                    shadow_probability = 1 / (1 + math.exp(-logit))
                factors.append(factor)
                leg_evidence.append({'leg_index': index, 'fixture_id': leg['fixture_id'],
                    'market_key': leg.get('market_key'), 'selection_key': leg.get('selection_key'),
                    'season_xg_probability': probability, 'quoted_price': price,
                    'value_margin': value_margin, 'score_multiplier': factor,
                    'home_xg_rate': signal['home_xg_rate'], 'away_xg_rate': signal['away_xg_rate'],
                    'baseline_home_xg_rate': signal['baseline_home_xg_rate'],
                    'baseline_away_xg_rate': signal['baseline_away_xg_rate'],
                    'home_xg_matches': signal['home_xg_matches'],
                    'away_xg_matches': signal['away_xg_matches'], 'sources': signal['sources'],
                    'player_context': signal['player_context'],
                    'absence_context': signal['absence_context'],
                    'replacement_coverage': signal['replacement_coverage'],
                    'data_coverage': signal['data_coverage'],
                    'player_market_effect': signal['player_market_effects'].get(
                        f"{leg.get('market_key')}:{leg.get('selection_key')}"),
                    'base_model_probability': base_probability,
                    'shadow_ensemble_probability': shadow_probability,
                    'shadow_ensemble_weight': .25 if shadow_probability is not None else None,
                    'shadow_only': True,
                    'shadow_quote_comparison': _shadow_quote(leg, signal),
                    'player_availability_applied': signal['player_availability_applied']})
                if signal['player_context']:
                    player_context_legs += 1
                if signal['player_availability_applied']:
                    player_adjusted_legs += 1
            if not leg_evidence:
                continue
            attached += 1
            factor = math.exp(math.fsum(math.log(x) for x in factors) / len(factors))
            option['quality'] = quality * factor
            option['scoring']['the_stats_api'] = {
                'version': collection.get('version'), 'base_quality': quality,
                'multiplier': factor, 'kind': 'SEASON_XG_RANKING_TIEBREAKER_NOT_PROBABILITY',
                'legs': leg_evidence,
            }
            if abs(factor - 1.0) > 1e-10:
                weighted += 1
    return output, {'weighted_options': weighted, 'evidence_attached_options': attached,
                    'eligible_fixtures': len(signals),
                    'player_context_legs': player_context_legs,
                    'player_availability_adjusted_legs': player_adjusted_legs,
                    'daily223_protected': True}


def _provider_routing_audit(collection, *, native_primary=False):
    """Expose which primary facts reached ranking and where fallbacks may fill gaps."""
    counts = {}
    for record in (collection.get('context_by_fixture') or {}).values():
        for fact in record.get('facts', []) if isinstance(record, dict) else []:
            if isinstance(fact, dict) and fact.get('status') == 'AVAILABLE':
                category = fact.get('category')
                counts[category] = counts.get(category, 0) + 1
    result = {
        'policy': 'PRIMARY_CONTEXT_WITH_AUDITED_LEGACY_CANDIDATE_FEEDS',
        'effective_priority_by_domain': {
            'fixtures_results_history': ['api_football'],
            'team_player_xg_availability': ['the_stats_api', 'api_football'],
            'odds': ['the_odds_api', 'api_football'],
            'weather': ['open_meteo'],
            'news_motivation': ['grok_x', 'verified_public_sources'],
        },
        'target_priority_after_shadow_validation': {
            'fixtures_results_history': ['the_stats_api', 'api_football'],
            'team_player_xg_availability': ['the_stats_api', 'api_football'],
            'odds': ['the_stats_api', 'the_odds_api', 'api_football'],
        },
        'fallback_reasons': ['MISSING', 'UNAVAILABLE', 'UNMATCHED', 'STALE', 'MARKET_GAP'],
        'primary_available_facts': dict(sorted(counts.items())),
        'ranking_inputs': {
            'team_season_stats': 'SEASON_XG_TIEBREAKER',
            'player_goal_market': 'UP_TO_TWO_ATTACKERS_PER_TEAM_SHORTLIST',
            'player_season_stats': 'GOAL_XG_ASSIST_XA_ATTACK_SHARE_AND_REPLACEMENT',
            'injuries_suspensions': 'ACTIVE_POSITION_SPECIFIC_ABSENCE_CHECK',
            'squad': 'ABSENT_PLAYER_POSITION_MAP',
            'lineup': 'PREDICTED_UNCERTAINTY_OR_CONFIRMED_REPLACEMENT_CHECK',
        },
        'overwrite_primary_with_fallback': False,
        'daily223_protected': True,
    }
    if native_primary:
        priority = {**result['effective_priority_by_domain'],
            'fixtures_results_history': ['the_stats_api'],
            'team_player_xg_availability': ['the_stats_api'], 'odds': ['the_stats_api']}
        result.update(policy='THE_STATS_NATIVE_PRIMARY_LEGACY_LIVE_PROVIDERS_RETIRED',
                      effective_priority_by_domain=priority,
                      target_priority_after_shadow_validation={k: priority[k] for k in (
                          'fixtures_results_history', 'team_player_xg_availability', 'odds')})
    return result


def _news(root, now):
    from goal_analysis.storage import Database
    from goal_analysis.storage.sports_news import NewsStore

    path = Path(root) / 'data/goal_analysis.sqlite3'
    if not path.exists():
        return []
    try:
        return NewsStore(Database(path)).claims(as_of=now)
    except sqlite3.OperationalError:
        return []


def _weather_fixtures(fixtures):
    """Join only explicit provider identifiers; never guess a team by its name."""
    output = deepcopy(fixtures)
    for fixture in output:
        if fixture.get('source_provider') == 'the_stats_api':
            from goal_analysis.normalization.live_identity import field_id

            for field in ('league', 'home_team', 'away_team'):
                fixture[field + '_id'] = 'the_stats_api:' + field_id(fixture, field)
            continue
        for target, source in (('league_id', 'api_football_league_id'),
                               ('home_team_id', 'api_football_home_team_id'),
                               ('away_team_id', 'api_football_away_team_id')):
            value = fixture.get(source)
            if str(value).isdecimal() and int(value) > 0:
                fixture[target] = f'api_football:{int(value)}'
    return output


def _save(root, result):
    result.pop('analysis_hash', None)
    result['analysis_hash'] = hashlib.sha256(_json(result).encode()).hexdigest()
    folder = Path(root) / 'data/arthur/experts'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{result['analysis_hash']}.json"
    if not destination.exists():
        destination.write_text(_json(result), encoding='utf-8')
    pointer = folder / f"{date.fromisoformat(result['date']).isoformat()}.json"
    temporary = pointer.with_suffix('.tmp')
    temporary.write_text(_json(result), encoding='utf-8')
    temporary.replace(pointer)


def analyze_report(root, report, *, weather_provider=None, clock=lambda: datetime.now(UTC),
                   max_missing_forecasts=12, dataset=None, persist=True,
                   standings_client=None, standings_cache=None, max_standings_requests=0):
    from goal_analysis.agents.motivation_expert import analyze_motivation
    from goal_analysis.agents.motivation_standings import collect_standings
    from goal_analysis.agents.weather_dataset import load_weather_dataset
    from goal_analysis.agents.weather_expert import analyze_weather

    now = _stamp(clock())
    fixtures = deepcopy(report.get('fixtures', []))
    forecasts = {f['fixture_id']: deepcopy(f['weather']) for f in fixtures if isinstance(f.get('weather'), dict)}
    used = {leg['fixture_id'] for group in GROUPS for card in report.get(group, [])
            for leg in card.get('legs', [])}
    attempts = 0
    for fixture in fixtures:
        old = forecasts.get(fixture['fixture_id'], {})
        try:
            usable = (old.get('status') == 'SOURCED' and old.get('weather_kind') == 'FORECAST'
                      and timedelta(0) <= now - _stamp(old.get('observed_at')) <= timedelta(hours=6))
        except (ValueError, TypeError):
            usable = False
        # Existing failed lookups from this run must not trigger a second attempt.
        try:
            attempted_this_run = (old.get('time_basis') == 'LOOKUP_ATTEMPT_AT'
                and timedelta(0) <= now - _stamp(old.get('observed_at')) <= timedelta(minutes=5))
        except (TypeError, ValueError):
            attempted_this_run = False
        if (not usable and not attempted_this_run and fixture['fixture_id'] in used
                and weather_provider is not None and attempts < max_missing_forecasts):
            attempts += 1
            try:
                forecasts[fixture['fixture_id']] = weather_provider.forecast(fixture)
            except Exception:  # noqa: BLE001 - independent experts survive optional provider failure
                forecasts[fixture['fixture_id']] = {'fixture_id': fixture['fixture_id'],
                    'status': 'UNKNOWN', 'weather_kind': 'FORECAST',
                    'observed_at': _stamp(clock()).isoformat(), 'time_basis': 'LOOKUP_ATTEMPT_AT',
                    'reason': 'WEATHER_LOOKUP_FAILED'}
    standings = collect_standings([f for f in fixtures if f['fixture_id'] in used],
        standings_client, standings_cache, as_of=_stamp(clock()),
        max_requests=max_standings_requests, clock=clock)
    now = _stamp(clock())
    dataset = dataset if dataset is not None else load_weather_dataset(root, as_of=now)
    weather = analyze_weather(_weather_fixtures(fixtures), forecasts, dataset, as_of=now)
    # Forecast context stays visible even when there is no validated historical effect.
    for finding in weather['fixtures']:
        forecast = forecasts.get(finding['fixture_id'], {})
        finding['forecast'] = forecast
        finding['source_url'] = forecast.get('source_url')
    motivation = analyze_motivation(fixtures, as_of=now, claims=_news(root, now),
        standings_context=standings['context_by_fixture'])
    knowledge_path = Path(root) / 'config/weather-expert-knowledge.json'
    knowledge_hash = hashlib.sha256(knowledge_path.read_bytes()).hexdigest() if knowledge_path.exists() else None
    if knowledge_path.exists():
        knowledge = json.loads(knowledge_path.read_text('utf-8'))
        weather['sources'] = [{'title': s.get('title', s['id']), 'url': s['url']}
                              for s in knowledge.get('sources', [])]
    result = {'version': VERSION, 'date': report['date'], 'run_id': report.get('run_id'),
              'observed_at': now.isoformat(), 'source_report_hash': hashlib.sha256(_json(report).encode()).hexdigest(),
              'knowledge_hash': knowledge_hash, 'weather': weather, 'motivation': motivation,
              'additional_weather_lookups': attempts, 'additional_llm_requests': 0,
              'standings_collection': {key: standings[key] for key in
                  ('requests', 'errors', 'deferred', 'observed_at')},
              'daily223_protected': True, 'ticket_changes': False}
    result['analysis_hash'] = hashlib.sha256(_json(result).encode()).hexdigest()
    if persist:
        _save(root, result)
    return result


def enrich_sports_supplement(root, report, *, max_requests=110, client=None,
                             clock=lambda: datetime.now(UTC), context_shadow=False):
    """Attach cached, sourced sports context without editing any ticket or estimate.

    The collector owns provider calls, TTL and raw archives. Keeping this step
    separate also lets a staged package acquire context before its final review
    without repeating weather analysis or model inference.
    """
    if type(max_requests) is not int or not 0 <= max_requests <= 110:
        raise ValueError('TheStats context request budget must be between zero and 110')
    if type(context_shadow) is not bool:
        raise ValueError('context_shadow must be boolean')
    output = deepcopy(report)
    try:
        from goal_analysis.jobs.the_stats_context import collect_context

        kwargs = {}
        if context_shadow:
            from goal_analysis.jobs.context_request_policy import FAIR

            kwargs['request_policy'] = FAIR
        collection = collect_context(root, deepcopy(report), max_requests=max_requests,
                                     client=client, clock=clock, **kwargs)
        if not isinstance(collection, dict) or not isinstance(collection.get('coverage'), dict):
            raise TypeError('Invalid TheStats context collection')
        collection = deepcopy(collection)
    except Exception:  # noqa: BLE001 - optional provider cannot discard the daily package or expose keys
        collection = {
            'version': 'thestatsapi-context-unavailable-v1', 'status': 'UNAVAILABLE',
            'observed_at': _stamp(clock()).isoformat(), 'context_by_fixture': {},
            'coverage': {'gaps': ['THE_STATS_CONTEXT_UNAVAILABLE']},
            'usage': {'requests': None, 'max_requests': max_requests},
        }
    if context_shadow:
        # One bounded collection, detached from live sports evidence/ranking.
        # No second legacy fetch and no mutation of existing cards/supplement.
        output['context_collection_shadow'] = {'mode': 'SHADOW', 'decision_effect': 'NONE',
            'collection': collection, 'live_promotion': False}
        return output
    supplement = output.get('sports_supplement')
    output['sports_supplement'] = {
        **(supplement if isinstance(supplement, dict) else {}), 'the_stats_api': collection,
    }
    output['provider_routing'] = _provider_routing_audit(collection, native_primary=any(
        f.get('source_provider') == 'the_stats_api' for f in output.get('fixtures', [])))
    return output


def review_experts(root, report, options, *, weather_provider=None, clock=lambda: datetime.now(UTC),
                   standings_client=None, standings_cache=None, max_standings_requests=0,
                   stats_client=None, max_stats_requests=110):
    """Before allocation: add shared evidence without changing any DAILY card."""
    attempt = report.get('candidate_context_attempt')
    if isinstance(attempt, dict):
        # The first review has already spent the shared sports-context budget.
        # A partial/failed preselection may never silently start another110 calls.
        output = deepcopy(report)
        collection = output.get('sports_supplement', {}).get('the_stats_api', {})
        try:
            from goal_analysis.agents import canonical_sha256
            from goal_analysis.jobs.candidate_context_review import verified_supplement

            if (attempt.get('status') != 'SOURCES_PREPARED'
                    or canonical_sha256(collection) != attempt.get('collection_sha256')
                    or type(collection.get('usage', {}).get('requests')) is not int
                    or not 0 <= collection['usage']['requests'] <= max_stats_requests):
                raise ValueError('UNPROVEN_PRESELECTION_COLLECTION_BUDGET')
            verified, issues = verified_supplement(root, collection, output.get('fixtures', []), _stamp(clock()))
            retained = {}
            for fid, facts in verified.items():
                retained[fid] = {**collection['context_by_fixture'][fid], 'facts': facts}
            collection = {**collection, 'context_by_fixture': retained,
                          'preselection_reused': True, 'reuse_validation_issues': issues}
        except Exception:  # noqa: BLE001 - unknown spend cannot authorize another collection
            collection = {'version': 'thestatsapi-context-unavailable-v1', 'status': 'UNAVAILABLE',
                'context_by_fixture': {}, 'coverage': {'gaps': ['PRESELECTION_REUSE_UNAVAILABLE']},
                'usage': {'requests': None, 'max_requests': max_stats_requests},
                'preselection_reused': False, 'automatic_second_collection': False}
        output['sports_supplement'] = {**output.get('sports_supplement', {}), 'the_stats_api': collection}
        output['provider_routing'] = _provider_routing_audit(collection, native_primary=any(
            f.get('source_provider') == 'the_stats_api' for f in output.get('fixtures', [])))
    else:
        output = enrich_sports_supplement(root, report, max_requests=max_stats_requests,
                                         client=stats_client, clock=clock)
    calls = 0

    class CountedStandings:
        def __getattr__(self, name):
            return getattr(standings_client, name)

        @property
        def last_usage(self):
            return getattr(standings_client, 'last_usage', None)

        def request(self, endpoint, params):
            nonlocal calls
            calls += 1
            return standings_client.request(endpoint, params)

    try:
        result = analyze_report(root, output, weather_provider=weather_provider, clock=clock,
            standings_client=CountedStandings() if standings_client is not None else None,
            standings_cache=standings_cache, max_standings_requests=max_standings_requests)
        adjusted, weather_count = weight_options(options, result['weather'])
        adjusted, stats_ranking = weight_sports_context(
            adjusted, output['sports_supplement']['the_stats_api'])
    except Exception:  # noqa: BLE001 - preserve attempts even if later local analysis fails
        output['expert_advisory'] = {'status': 'UNAVAILABLE', 'daily223_protected': True,
            'standings_collection': {'requests': calls}, 'additional_llm_requests': 0}
        return {'report': output, 'options': deepcopy(options)}
    output['expert_advisory'] = result
    output['expert_ranking'] = {
        'weighted_options': weather_count + stats_ranking['weighted_options'],
        'weather_weighted_options': weather_count,
        'the_stats_api': stats_ranking,
        'daily223_protected': True,
    }
    return {'report': output, 'options': adjusted}


def refresh_news(root, report, *, clock=lambda: datetime.now(UTC)):
    """An archive failure must never discard an already reviewed/repaired package."""
    try:
        return _refresh_news(root, report, clock=clock)
    except Exception:  # noqa: BLE001 - credential-safe failure for optional local persistence
        output = deepcopy(report)
        output['expert_news_refresh'] = {'status': 'UNAVAILABLE', 'ticket_changes': False}
        return output


def _refresh_news(root, report, *, clock):
    """Use newly archived Grok claims locally; do not make a second model request."""
    from goal_analysis.agents.motivation_expert import analyze_motivation

    if not isinstance(report.get('expert_advisory'), dict):
        return report
    output = deepcopy(report)
    result = output['expert_advisory']
    if result.get('version') != VERSION:
        return report
    now = _stamp(clock())
    contexts = {row['fixture_id']: row['standings_context']
                for row in result['motivation']['fixtures'] if row.get('standings_context')}
    result['motivation'] = analyze_motivation(output.get('fixtures', []), as_of=now,
        claims=_news(root, now), standings_context=contexts)
    result['observed_at'] = now.isoformat()
    _save(root, result)
    return output


def snapshot(root, day, report=None):
    """Cheap read-only dashboard view; never compute, fetch or rewrite a ticket."""
    try:
        day = date.fromisoformat(day).isoformat()
        path = Path(root) / 'data/arthur/experts' / f'{day}.json'
        value = json.loads(path.read_text('utf-8'))
        digest = value.pop('analysis_hash')
        if hashlib.sha256(_json(value).encode()).hexdigest() != digest:
            raise ValueError('Analysis integrity mismatch')
        value['analysis_hash'] = digest
        if value.get('date') != day:
            raise ValueError('Wrong date')
        if report and value.get('run_id') != report.get('run_id'):
            return {'status': 'DIFFERENT_RUN', 'date': day, 'run_id': value.get('run_id')}
        return {'status': 'AVAILABLE', **value}
    except (OSError, ValueError, TypeError, KeyError):
        return {'status': 'NOT_RUN', 'date': day}
