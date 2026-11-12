"""Pure descriptive historical market scenarios, never historical profit or PIT.

All supplied rows are accounted for. Source hashes bind in-memory producer
values; they do not authenticate physical files. No DB, network, fitting or
replay entry point is called. Existing strategy/sample/settlement rules stay put.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.daily_223_history import _market_predicate, _sample
from goal_analysis.normalization.live_identity import field_id, provider_id
from goal_analysis.normalization.training_datasets import validate as validate_dataset

VERSION = 'descriptive-recorded-results-v1'
KIND = 'DESCRIPTIVE_RECORDED_RESULTS_V1'
MAX_INPUT_ROWS = 40000
_SCOPES = ('same_competition', 'team_any_venue', 'team_same_known_venue', 'head_to_head')
_REGULATION = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'
_PERIOD_SETTLEMENT = {'FULL_TIME': _REGULATION,
                      'FIRST_HALF': 'FIRST_HALF_PLUS_STOPPAGE'}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _clock(value):
    _require(isinstance(value, str), 'AWARE_HISTORY_CLOCK_REQUIRED')
    stamp = datetime.fromisoformat(value)
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
             'AWARE_HISTORY_CLOCK_REQUIRED')
    return stamp.astimezone(UTC)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def policy_catalog():
    result = []
    for scope in _SCOPES:
        policy = {'kind': KIND, 'version': VERSION, 'scope': scope,
                  'selection': 'ALL_MATCHING_SUPPLIED_ROWS_IN_REQUESTED_WINDOW',
                  'competition_scope': ('EXACT_CURRENT_COMPETITION' if scope == 'same_competition'
                                        else 'ALL_SUPPLIED_COMPETITIONS_FOR_SAME_PROVIDER'),
                  'orientation': 'ORIGINAL_HOME_AWAY' if scope == 'same_competition'
                                 else 'CURRENT_TARGET_TEAM_ROLE',
                  'probability_or_profit': False}
        result.append({**policy, 'policy_hash': canonical_sha256(policy)})
    return result


def is_descriptive_policy(policy_hash):
    return any(row['policy_hash'] == policy_hash for row in policy_catalog())


def _score(value):
    _require(value is None or type(value) is int and value >= 0, 'INVALID_PERIOD_SCORE')
    return value


def _record(raw, provider, cutoff):
    _require(isinstance(raw, dict), 'HISTORY_ROW_REQUIRED')
    provider = raw.get('provider', provider)
    _require(provider in {'api_football', 'the_stats_api'}, 'UNKNOWN_HISTORY_PROVIDER')
    ids = {field + '_id': provider_id(raw.get(field + '_id'), field, provider)
           for field in ('fixture', 'league', 'home_team', 'away_team')}
    _require(ids['home_team_id'] != ids['away_team_id'], 'IDENTICAL_HISTORY_TEAMS')
    kickoff, observed = _clock(raw.get('kickoff')), _clock(raw.get('observed_at'))
    _require(kickoff < observed <= cutoff, 'RESULT_NOT_KNOWN_AT_CURRENT_CUTOFF')
    for key in ('available_at', 'ingested_at', 'published_at'):
        if raw.get(key) is not None:
            _require(_clock(raw[key]) <= cutoff, 'FUTURE_HISTORY_KNOWLEDGE')
    _require(raw.get('status') in {'FT', 'AET', 'PEN'}, 'RESULT_NOT_FINISHED')
    _require(isinstance(raw.get('source_id'), str) and bool(raw['source_id']),
             'HISTORY_SOURCE_REQUIRED')
    # These fields are the existing producer's regulation/half-time contract.
    # Never use generic goals/AET/penalty totals as a substitute.
    values = {key: _score(raw.get(key)) for key in
              ('full_time_home', 'full_time_away', 'first_half_home', 'first_half_away')}
    for side in ('home', 'away'):
        full, half = values['full_time_' + side], values['first_half_' + side]
        _require(full is None or half is None or half <= full, 'HALF_TIME_EXCEEDS_REGULATION')
    neutral = raw.get('neutral_venue')
    _require(neutral is None or type(neutral) is bool, 'INVALID_HISTORY_VENUE')
    return {**ids, **values, 'provider': provider, 'kickoff': raw['kickoff'],
            'observed_at': raw['observed_at'], 'status': raw['status'],
            'neutral_venue': neutral, 'source_id': raw['source_id'],
            'knowledge_clocks': {key: raw[key] for key in
                ('observed_at', 'available_at', 'ingested_at', 'published_at') if key in raw}}


def _inputs(artifacts, cutoff):
    batches, issues = [], []
    history = artifacts.get('history')
    if history is not None:
        try:
            _require(isinstance(history, dict) and history.get('kind') == 'DAILY223_HISTORY'
                     and type(history.get('schema_version')) is int
                     and history['schema_version'] == 1, 'INVALID_HISTORY_ENVELOPE')
            _require(history.get('snapshot_sha256') == canonical_sha256(
                {key: value for key, value in history.items() if key != 'snapshot_sha256'}),
                'HISTORY_ENVELOPE_HASH_MISMATCH')
            _require(_clock(history.get('collected_at')) <= cutoff, 'FUTURE_HISTORY_COLLECTION')
            _require(isinstance(history.get('records'), list), 'HISTORY_RECORDS_REQUIRED')
            batches.append(('artifacts.history', history['records'], history.get('provider'),
                            history['snapshot_sha256'], _clock(history['collected_at'])))
        except (TypeError, ValueError, KeyError, OverflowError) as error:
            issues.append({'source': 'artifacts.history', 'reason': str(error)})
    normal = artifacts.get('normal_model_input')
    if isinstance(normal, dict) and normal.get('status') == 'COMPLETE':
        try:
            view = normal.get('input_view')
            _require(isinstance(view, dict), 'NORMAL_HISTORY_VIEW_REQUIRED')
            dataset = validate_dataset(view.get('dataset'))
            manifest = dataset['manifest']
            _require(manifest['role'] == 'normal_standard_venue_history'
                     and _clock(manifest['as_of']) == _clock(view.get('as_of')) <= cutoff
                     and dataset['rows'] == view.get('history'), 'NORMAL_HISTORY_DATASET_MISMATCH')
            # The existing NORMAL schema treats an absent provider as legacy
            # API-Football. Native rows must carry their explicit provider.
            batches.append(('artifacts.normal_model_input.input_view.dataset', dataset['rows'],
                            'api_football', manifest['dataset_id'], _clock(manifest['as_of'])))
        except (TypeError, ValueError, KeyError, OverflowError) as error:
            issues.append({'source': 'artifacts.normal_model_input', 'reason': str(error)})
    return batches, issues


def build_history_view(artifacts, *, as_of):
    """Build one detached whole supplied-cohort view; never read another source."""
    _require(isinstance(artifacts, dict), 'HISTORY_ARTIFACTS_REQUIRED')
    cutoff = _clock(as_of)
    batches, issues = _inputs(artifacts, cutoff)
    count = sum(len(batch[1]) for batch in batches)
    groups, exclusions = defaultdict(list), []
    if count > MAX_INPUT_ROWS:
        issues.append({'source': 'ALL_INPUTS', 'reason': 'HISTORY_ROW_BOUND_NO_FIRST_N'})
    else:
        for path, rows, provider, source_hash, source_cutoff in batches:
            for position, raw in enumerate(rows):
                ref = {'source_path': path, 'source_sha256': source_hash,
                       'row_position': position, 'row_sha256': canonical_sha256(raw)}
                try:
                    row = _record(raw, provider, min(source_cutoff, cutoff))
                    key = (row['provider'], row['fixture_id'])
                    groups[key].append((row, ref))
                except (TypeError, ValueError, KeyError, OverflowError) as error:
                    exclusions.append({**ref, 'reason': str(error)})
    records = []
    for key, aliases in sorted(groups.items()):
        truth_fields = ('provider', 'fixture_id', 'league_id', 'home_team_id', 'away_team_id',
                        'kickoff', 'status', 'full_time_home', 'full_time_away',
                        'first_half_home', 'first_half_away')
        truths = {canonical_sha256({field: _clock(row[field]).isoformat()
                                    if field == 'kickoff' else row[field]
                                    for field in truth_fields}) for row, _ in aliases}
        venues = {row['neutral_venue'] for row, _ in aliases if row['neutral_venue'] is not None}
        if len(truths) != 1 or len(venues) > 1:
            exclusions.extend({**ref, 'reason': 'CONFLICTING_HISTORY_FIXTURE'} for _, ref in aliases)
            continue
        # Earliest original observation is retained, not grafted onto other rows.
        row = _copy(min(aliases, key=lambda item: (_clock(item[0]['observed_at']),
                                                 item[1]['row_sha256']))[0])
        row['neutral_venue'] = next(iter(venues)) if venues else None
        row['source_aliases'] = [{**ref, 'kickoff': source['kickoff'],
                                  'knowledge_clocks': source['knowledge_clocks'],
                                  'neutral_venue': source['neutral_venue']}
                                 for source, ref in aliases]
        row['record_id'] = canonical_sha256([list(key), next(iter(truths))])
        records.append(row)
    records.sort(key=lambda row: (_clock(row['kickoff']), row['provider'], row['fixture_id']))
    value = {'version': VERSION, 'as_of': as_of, 'records': records,
             'input_row_count': count, 'source_issues': issues, 'excluded_rows': exclusions,
             'source_boundary': 'SUPPLIED_PRODUCER_HASH_CONSISTENCY_NOT_PHYSICAL_AUTHENTICATION',
             'coverage_scope': 'COLLECTED_ONLINE_AND_ADMITTED_NORMAL_INPUT_NOT_WHOLE_ARCHIVE',
             'historical_PIT_certified': False, 'profit_or_PIT_claim': False}
    return {**value, 'source_view_sha256': canonical_sha256(value)}


def describe_history_view(view):
    records = view['records']
    teams = Counter(row['provider'] + ':' + row[field] for row in records
                    for field in ('home_team_id', 'away_team_id'))
    window = None if not records else {'start': records[0]['kickoff'],
        'end': min(_clock(view['as_of']), _clock(records[-1]['kickoff']) + timedelta(microseconds=1)).isoformat()}
    return {'evidence_kind': KIND, 'source_view_sha256': view['source_view_sha256'],
            'tool_available': True, 'own_result_status': 'NOT_REQUESTED',
            'data_status': 'AVAILABLE' if records else 'UNKNOWN', 'available_window': window,
            'admitted_record_count': len(records), 'tool_catalog': policy_catalog(),
            'coverage': {'scope': view['coverage_scope'], 'input_row_count': view['input_row_count'],
                'providers': dict(Counter(row['provider'] for row in records)),
                'competitions': dict(Counter(row['provider'] + ':' + row['league_id'] for row in records)),
                'team_counts': dict(sorted(teams.items())),
                'excluded_counts': dict(Counter(row['reason'] for row in view['excluded_rows'])),
                'source_issues': _copy(view['source_issues'])},
            'original_strategy_decision_history': False, 'profit_or_PIT_claim': False}


def _product(leg):
    period, market, selection = leg.get('period'), leg.get('market_key'), leg.get('selection_key')
    _require(period in _PERIOD_SETTLEMENT and leg.get('settlement') == _PERIOD_SETTLEMENT[period],
             'UNSUPPORTED_EXACT_PERIOD_OR_SETTLEMENT')
    _require(leg.get('handicap') is None, 'UNSUPPORTED_EXACT_PRODUCT')
    predicate = _market_predicate(market, selection)
    line = leg.get('line')
    if line is not None:
        match = re.fullmatch(r'totals_(\d+)_5', market)
        _require(match is not None and type(line) in (str, int, float)
                 and math.isfinite(float(line)) and float(line) == int(match.group(1)) + .5,
                 'EXPLICIT_MARKET_LINE_MISMATCH')
    return period, predicate


def _bound_target(leg, candidates, fixtures, owner, cutoff):
    identifier = leg.get('source_candidate_id') or leg.get('quote_candidate_id') or leg.get('candidate_id')
    matches = [row for row in candidates if row.get('candidate_id') == identifier]
    targets = [row for row in fixtures if row.get('fixture_id') == leg.get('fixture_id')]
    _require(len(matches) == len(targets) == 1, 'EXACT_UNAMBIGUOUS_SOURCE_REQUIRED')
    source, target = matches[0], targets[0]
    for key in ('fixture_id', 'market_key', 'selection_key', 'period', 'settlement'):
        _require(key in leg and leg[key] == source.get(key), 'SOURCE_PRODUCT_IDENTITY_MISMATCH')
    for key in ('line', 'handicap', 'decimal_price', 'bookmaker', 'quote_provider',
                'quote_source_id', 'quote_source_sha256'):
        _require(leg.get(key) == source.get(key), 'SOURCE_PRODUCT_VALUE_MISMATCH')
    for key in ('quote_observed_at', 'quoted_at'):
        if leg.get(key) is None and source.get(key) is None:
            continue
        _require(_clock(leg.get(key)) == _clock(source.get(key)) <= cutoff,
                 'SOURCE_QUOTE_CLOCK_MISMATCH')
    for key in ('source_candidate_id', 'quote_candidate_id'):
        _require(leg.get(key) in (None, identifier), 'CONFLICTING_CANDIDATE_ALIAS')
    if leg.get('candidate_id') not in (None, identifier):
        virtual = owner + '-' + canonical_sha256([owner, leg['fixture_id'], leg['market_key'],
                    leg['selection_key'], source.get('bookmaker')])[:24]
        _require(leg['candidate_id'] == virtual and leg.get('source_candidate_id') == identifier
                 and leg.get('quote_candidate_id') == identifier, 'CONFLICTING_CANDIDATE_ALIAS')
    _require(_clock(leg.get('kickoff')) == _clock(source.get('kickoff'))
             == _clock(target.get('kickoff')), 'SOURCE_FIXTURE_CLOCK_MISMATCH')
    provider = target.get('source_provider', 'api_football')
    ids = {field + '_id': field_id(target, field) for field in
           ('fixture', 'league', 'home_team', 'away_team')}
    _require(target['fixture_id'] == provider + ':' + ids['fixture_id'], 'SOURCE_FIXTURE_IDENTITY_MISMATCH')
    for key in ('provider_fixture_id', 'provider_league_id', 'provider_season_id',
                'provider_home_team_id', 'provider_away_team_id', 'api_football_fixture_id',
                'api_football_league_id', 'api_football_home_team_id', 'api_football_away_team_id'):
        _require(key not in leg or leg[key] == target.get(key), 'SOURCE_FIXTURE_IDENTITY_MISMATCH')
    return {**ids, 'provider': provider, 'neutral_venue': target.get('neutral_venue'),
            'source_candidate_sha256': canonical_sha256(source)}


def _cohorts(records, target, scope):
    rows = [row for row in records if row['provider'] == target['provider']
            and row['fixture_id'] != target['fixture_id']]
    if scope == 'same_competition':
        return [('competition', None, 'home', [row for row in rows if row['league_id'] == target['league_id']])]
    result = []
    for role in ('home', 'away'):
        team = target[role + '_team_id']
        own = [row for row in rows if team in (row['home_team_id'], row['away_team_id'])]
        if scope == 'team_same_known_venue':
            own = [row for row in own if type(target['neutral_venue']) is bool
                   and row['neutral_venue'] is target['neutral_venue'] and row[role + '_team_id'] == team]
        elif scope == 'head_to_head':
            own = [row for row in own if {row['home_team_id'], row['away_team_id']}
                   == {target['home_team_id'], target['away_team_id']}]
        result.append((role, team, role, own))
    return result


class HistoricalScenarioEngine:
    """One immutable run view with request-time own-option market calculations."""

    def __init__(self, view):
        _require(isinstance(view, dict) and _hash(view.get('source_view_sha256'))
                 and view['source_view_sha256'] == canonical_sha256(
                     {key: value for key, value in view.items() if key != 'source_view_sha256'}),
                 'HISTORICAL_VIEW_HASH_MISMATCH')
        self._view = _copy(view)
        self.source_view_sha256 = view['source_view_sha256']
        self._summary = describe_history_view(self._view)
        self._windows = {}

    def evaluate(self, payload, options, *, candidates, fixtures):
        parameters = payload['parameters']
        policies = [row for row in policy_catalog() if row['policy_hash'] == parameters.get('policy_hash')]
        _require(len(policies) == 1, 'UNKNOWN_DESCRIPTIVE_POLICY')
        window = parameters.get('window')
        _require(isinstance(window, dict) and set(window) == {'start', 'end'}, 'HISTORICAL_WINDOW_REQUIRED')
        start, end, cutoff = _clock(window['start']), _clock(window['end']), _clock(payload['as_of'])
        _require(start < end <= cutoff and cutoff == _clock(self._view['as_of']), 'HISTORICAL_WINDOW_INVALID')
        window_key = (start, end)
        if window_key not in self._windows:
            self._windows[window_key] = [row for row in self._view['records']
                                        if start <= _clock(row['kickoff']) < end]
        rows = self._windows[window_key]
        manifests, by_option, known = {}, {}, 0
        for option in options:
            legs = []
            for position, leg in enumerate(option['legs']):
                measured = {'leg_index': position, 'source_leg_sha256': canonical_sha256(leg),
                            'source_candidate_id': leg.get('source_candidate_id') or leg.get('candidate_id'),
                            'status': 'UNKNOWN', 'reason': None, 'scope_samples': {}}
                try:
                    period, predicate = _product(leg)
                    target = _bound_target(leg, candidates, fixtures, payload['agent_id'], cutoff)
                    measured['source_candidate_sha256'] = target['source_candidate_sha256']
                    measured['product'] = {key: leg.get(key) for key in
                        ('fixture_id', 'market_key', 'selection_key', 'period', 'settlement', 'line', 'handicap')}
                    for name, team, role, cohort in _cohorts(rows, target, policies[0]['scope']):
                        # Competition orientation is each original fixture's home/away.
                        samples = [_sample([row], row['home_team_id'], 'home', period, predicate)
                                   for row in cohort] if team is None else [_sample(cohort, team, role, period, predicate)]
                        size = sum(value['sample_size'] for value in samples)
                        hits = sum(value['market_hits'] for value in samples)
                        manifest = [{'record_id': row['record_id'], 'source_aliases': row['source_aliases']}
                                    for row in cohort]
                        manifest_hash = canonical_sha256(manifest)
                        if manifest_hash not in manifests:
                            manifests[manifest_hash] = _copy(manifest)
                        prefix = 'full_time' if period == 'FULL_TIME' else 'first_half'
                        scores = Counter(str(row[prefix + '_home']) + '-' + str(row[prefix + '_away'])
                            for row in cohort if row[prefix + '_home'] is not None and row[prefix + '_away'] is not None)
                        months = Counter(_clock(row['kickoff']).strftime('%Y-%m') for row in cohort)
                        measured['scope_samples'][name] = {'matching_row_count': len(cohort),
                            'sample_size': size, 'market_hits': hits, 'market_misses': size - hits,
                            'unknown_period_scores': len(cohort) - size,
                            'coverage_status': ('PARTIAL' if size < len(cohort) else 'AVAILABLE')
                            if size else 'UNKNOWN',
                            'reason': ('SOME_MATCHING_PERIOD_RESULTS_UNKNOWN' if size else
                                'NO_KNOWN_MATCHING_PERIOD_RESULTS') if size < len(cohort) else (
                                'NO_MATCHING_HISTORICAL_ROWS' if not size else None),
                            'descriptive_hit_rate': hits / size if size else None,
                            'score_histogram_original_home_away': dict(sorted(scores.items())),
                            'month_counts': dict(sorted(months.items())),
                            'competition_counts': dict(sorted(Counter(row['provider'] + ':'
                                + row['league_id'] for row in cohort).items())),
                            'sample_manifest_sha256': manifest_hash,
                            'scope': policies[0]['scope'], 'team_id': team, 'current_team_role': role}
                        known += int(size > 0)
                    measured['status'] = 'AVAILABLE' if any(
                        sample['sample_size'] for sample in measured['scope_samples'].values()) else 'UNKNOWN'
                    if measured['status'] == 'UNKNOWN':
                        measured['reason'] = ('KNOWN_TARGET_VENUE_REQUIRED' if policies[0]['scope']
                            == 'team_same_known_venue' and type(target['neutral_venue']) is not bool
                            else 'NO_KNOWN_MATCHING_PERIOD_RESULTS')
                except (TypeError, ValueError, KeyError, OverflowError) as error:
                    measured['reason'] = str(error)
                legs.append(measured)
            by_option[option['option_id']] = {'option_id': option['option_id'], 'legs': legs,
                'status': 'AVAILABLE' if any(row['status'] == 'AVAILABLE' for row in legs) else 'UNKNOWN',
                'whole_ticket_historical_probability': None, 'historical_roi': None}
        partial = any(leg['status'] == 'UNKNOWN' or any(not sample['sample_size'] or sample['unknown_period_scores']
            for sample in leg['scope_samples'].values())
            for option in by_option.values() for leg in option['legs'])
        return {'evidence_kind': KIND, 'status': 'AVAILABLE' if known else 'UNKNOWN',
            'coverage_status': ('PARTIAL' if partial else 'AVAILABLE') if known else 'UNKNOWN',
            'reason': None if known else 'NO_OWN_MARKET_HISTORICAL_SAMPLE', 'by_option': by_option,
            'own_option_ids': list(payload['own_option_ids']), 'source_view_sha256': self.source_view_sha256,
            'window': _copy(window), 'policy': policies[0], 'sample_manifests': manifests,
            'coverage': _copy(self._summary['coverage']),
            'original_strategy_decision_history': False, 'profit_or_PIT_claim': False,
            'limitations': ['DESCRIPTIVE_RECORDED_OUTCOMES_NOT_CALIBRATED_FUTURE_PROBABILITIES',
                'TEAM_AND_COMPETITION_SAMPLES_CAN_OVERLAP_NOT_INDEPENDENT',
                'NO_HISTORICAL_PERSONALITY_DECISION_OR_HISTORICAL_PRICE_RECONSTRUCTION',
                'NO_JOINT_HISTORICAL_TICKET_OR_RETURN_ESTIMATE',
                'OPPONENT_STRENGTH_XI_COACH_WEATHER_NOT_AUTOMATICALLY_CONTROLLED']}
