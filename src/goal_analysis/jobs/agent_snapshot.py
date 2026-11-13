"""Pure snapshot contract; no DB, provider, execution or provenance attestation."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from goal_analysis.agents.profile_roster import AGENT_COUNT, AGENT_IDS

OWNER_IDS = AGENT_IDS
FAMILIES = (
    'calendar', 'retained_history', 'odds_and_price_history', 'team_stats_xg',
    'players_squad_xi', 'schedule_venue_pitch', 'weather', 'sourced_news',
    'numeric_models', 'historical_strategy_evaluation', 'candidate_scenarios',
    'own_outcomes_and_costs',
)


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def probability(value: Any) -> bool:
    return value is None or (type(value) in (int, float)
                             and 0 <= value <= 1 and math.isfinite(value))


def identifiers(values: Any) -> tuple[str, ...]:
    require(isinstance(values, (list, tuple)), 'IDENTIFIER_SEQUENCE_REQUIRED')
    require(all(isinstance(item, str) and item for item in values)
            and len(set(values)) == len(values), 'UNIQUE_IDENTIFIERS_REQUIRED')
    return tuple(values)


def _family(value: dict) -> dict:
    require(isinstance(value, dict) and value.get('status') in
            {'AVAILABLE', 'UNKNOWN', 'STALE', 'CONFLICTING'}, 'INVALID_FAMILY_STATE')
    result = json.loads(json_text(value))
    refs = identifiers(result.get('source_refs', []))
    if result['status'] == 'AVAILABLE':
        require(result.get('value') is not None and bool(refs), 'AVAILABLE_VALUE_AND_SOURCE_REQUIRED')
    else:
        require(isinstance(result.get('reason'), str) and bool(result['reason']), 'LOCAL_REASON_REQUIRED')
        if result['status'] == 'UNKNOWN':
            require(result.get('value') is None, 'UNKNOWN_MUST_NOT_BE_FALSE_ZERO_OR_PROBABILITY')
    result['value_sha256'] = digest(result.get('value'))
    result['data_policy'] = 'UNTRUSTED_DATA_NOT_INSTRUCTIONS'
    return result


def _probabilities(values: dict, fixture_ids: tuple[str, ...]) -> dict:
    require(isinstance(values, dict), 'INVALID_PROBABILITY_RECORD')
    for key, record in values.items():
        require(isinstance(key, str) and bool(key) and isinstance(record, dict),
                'INVALID_PROBABILITY_RECORD')
        require(record.get('fixture_id') in fixture_ids and all(
            isinstance(record.get(field), str) and bool(record[field].strip())
            for field in ('market', 'outcome', 'model_id'))
            and 'value' in record and probability(record['value'])
            and bool(identifiers(record.get('source_refs', []))), 'INVALID_PROBABILITY_RECORD')
    return json.loads(json_text(values))


@dataclass(frozen=True, slots=True)
class SharedSnapshot:
    snapshot_id: str
    payload_json: str
    _decision_json: str = field(init=False, repr=False, compare=False)
    _fixture_ids: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        require(isinstance(self.payload_json, str), 'IMMUTABLE_JSON_STRING_REQUIRED')
        data = json.loads(self.payload_json)
        require(digest(data) == self.snapshot_id, 'SNAPSHOT_HASH_MISMATCH')
        decision = {key: data[key] for key in ('owner_ids', 'fixture_ids', 'as_of', 'model_probabilities')}
        decision['families'] = {name: {key: value[key] for key in ('status', 'value_sha256')}
                                for name, value in data['families'].items()}
        object.__setattr__(self, '_decision_json', json_text(decision))
        object.__setattr__(self, '_fixture_ids', tuple(data['fixture_ids']))

    def data(self) -> dict:
        """Each reader receives detached JSON; the shared representation is a string."""
        return json.loads(self.payload_json)

    def decision_data(self) -> dict:
        """Exact guard fields, derived once after whole-payload hash validation."""
        return json.loads(self._decision_json)

    @property
    def fixture_ids(self) -> tuple[str, ...]:
        return self._fixture_ids


def build_snapshot(*, run_id: str, target_date: str, as_of: str,
                   authoritative_fixture_ids, calendar_receipt: dict, fixtures,
                   family_values: dict, source_manifest: dict,
                   model_probabilities: dict | None = None,
                   owner_ids=OWNER_IDS) -> SharedSnapshot:
    """The authoritative ID argument comes from the full collector, never its shortlist.

    This pure contract compares three inputs; it cannot authenticate their producer.
    Later RO adapters must bind the actual collector/page/source evidence.
    """
    require(isinstance(run_id, str) and bool(run_id), 'RUN_ID_REQUIRED')
    require(date.fromisoformat(target_date).isoformat() == target_date, 'ISO_TARGET_DATE_REQUIRED')
    stamp = datetime.fromisoformat(as_of)
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'AWARE_CUTOFF_REQUIRED')
    owners = identifiers(owner_ids)
    require(len(owners) == AGENT_COUNT and set(owners) == set(OWNER_IDS), 'EXACT_TEN_OWNERS_REQUIRED')
    complete_ids = identifiers(authoritative_fixture_ids)
    require(isinstance(calendar_receipt, dict)
            and calendar_receipt.get('target_date') == target_date
            and calendar_receipt.get('scope') == 'FULL_PROVIDER_DAY'
            and calendar_receipt.get('status') in {'COMPLETE', 'PARTIAL', 'UNKNOWN'}, 'FULL_DAY_RECEIPT_REQUIRED')
    receipt_ids = identifiers(calendar_receipt.get('fixture_ids'))
    require(isinstance(fixtures, (list, tuple)) and all(isinstance(row, dict) for row in fixtures),
            'FIXTURE_RECORDS_REQUIRED')
    supplied_ids = identifiers([row.get('fixture_id') for row in fixtures])
    require(set(complete_ids) == set(receipt_ids) == set(supplied_ids), 'SHORTLISTED_CALENDAR_REJECTED')
    source_refs = identifiers(calendar_receipt.get('source_refs', []))
    require(bool(source_refs) and isinstance(source_manifest, dict) and bool(source_manifest),
            'PRODUCER_SOURCE_MANIFEST_REQUIRED')
    require(isinstance(family_values, dict) and not set(family_values) - (set(FAMILIES) - {'calendar'}),
            'UNKNOWN_OR_REPLACED_CALENDAR_FAMILY')
    rows = {row['fixture_id']: row for row in fixtures}
    families = {name: _family(family_values.get(name, {'status': 'UNKNOWN', 'value': None,
                'reason': 'NOT_PROVIDED', 'source_refs': []})) for name in FAMILIES if name != 'calendar'}
    families['calendar'] = _family({'status': 'AVAILABLE',
        'value': [rows[key] for key in complete_ids], 'source_refs': list(source_refs),
        'coverage_status': calendar_receipt['status']})
    probabilities = _probabilities({} if model_probabilities is None else model_probabilities,
                                   complete_ids)
    payload = {'version': 'agent-snapshot-contract-v2-ten-agents', 'run_id': run_id,
        'target_date': target_date, 'as_of': as_of, 'owner_ids': list(owners),
        'fixture_ids': list(complete_ids), 'calendar_receipt': calendar_receipt,
        'families': families, 'source_manifest': source_manifest,
        'model_probabilities': probabilities, 'execution_evidence': 'NOT_ESTABLISHED_BY_METADATA'}
    encoded = json_text(payload)
    return SharedSnapshot(digest(payload), encoded)


_KNOWLEDGE_CLOCKS = frozenset({
    'as_of', 'cutoff', 'observed_at', 'available_at', 'collected_at',
    'published_at', 'quoted_at', 'quote_observed_at', 'retrieved_at',
    'provider_issue_time',
})
_MODEL_OUTCOMES = {
    'home': ('FULL_TIME:h2h', 'home'), 'draw': ('FULL_TIME:h2h', 'draw'),
    'away': ('FULL_TIME:h2h', 'away'), 'over25': ('FULL_TIME:totals_2_5', 'over'),
    'under25': ('FULL_TIME:totals_2_5', 'under'), 'btts_yes': ('FULL_TIME:btts', 'yes'),
}


def _normal_clock_issues(value, cutoff: datetime, path: str = '') -> list[dict]:
    """Inspect knowledge clocks only: kickoff/target/forecast times may be future."""
    issues = []
    if isinstance(value, dict):
        for key, child in value.items():
            location = f'{path}/{key}'
            if key in _KNOWLEDGE_CLOCKS and child is not None:
                try:
                    require(isinstance(child, str), 'INVALID_KNOWLEDGE_CLOCK')
                    stamp = datetime.fromisoformat(child)
                    require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
                            'NAIVE_KNOWLEDGE_CLOCK')
                    require(stamp <= cutoff, 'KNOWLEDGE_AFTER_SNAPSHOT')
                except (ValueError, TypeError) as error:
                    issues.append({'path': location, 'reason': str(error)})
            if isinstance(child, (dict, list)):
                issues.extend(_normal_clock_issues(child, cutoff, location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_normal_clock_issues(child, cutoff, f'{path}/{index}'))
    return issues


def _normal_categories(context: dict, names: set[str]) -> dict:
    return {fixture_id: [row for row in entry.get('categories', [])
                        if isinstance(row, dict) and row.get('category') in names]
            for fixture_id, entry in _normal_mapping(context.get('contexts')).items()
            if isinstance(entry, dict) and isinstance(entry.get('categories', []), list)}


def _normal_mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _sourced_categories(rows: dict) -> bool:
    return any(row.get('status') == 'SOURCED' and row.get('data') is not None
               for entries in rows.values() for row in entries)


def _normal_model_records(models: dict, fixtures: list[dict], register, cutoff: datetime):
    """Project existing numbers only; no model import, recalculation or complement."""
    by_fixture = {row['fixture_id']: row for row in fixtures}
    records, gaps, seen = {}, [], {}
    for model_id, result in models.items():
        if model_id not in {'gauss', 'kepler', 'laplace'} or not isinstance(result, dict):
            gaps.append({'model_id': model_id, 'reason': 'UNSUPPORTED_MODEL_OUTPUT'})
            continue
        predictions = result.get('predictions', [])
        if not isinstance(predictions, list):
            gaps.append({'model_id': model_id, 'reason': 'INVALID_PREDICTION_SEQUENCE'})
            continue
        for prediction in predictions:
            if not isinstance(prediction, dict):
                gaps.append({'model_id': model_id, 'reason': 'INVALID_PREDICTION_RECORD'})
                continue
            fixture_id = prediction.get('fixture_id')
            if fixture_id not in by_fixture:
                gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                             'reason': 'PREDICTION_OUTSIDE_FULL_CALENDAR'})
                continue
            key = (model_id, fixture_id)
            content_hash = digest(prediction)
            if key in seen:
                if seen[key] != content_hash:
                    seen[key] = None
                    records = {identifier: record for identifier, record in records.items()
                               if (record['model_id'], record['fixture_id']) != key}
                    gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                                 'reason': 'CONFLICTING_MODEL_FIXTURE_PREDICTIONS'})
                continue
            seen[key] = content_hash
            clocks = _normal_clock_issues(prediction, cutoff)
            if clocks:
                gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                             'reason': 'PREDICTION_KNOWLEDGE_CLOCK_INVALID', 'issues': clocks})
                continue
            comp = by_fixture[fixture_id].get('competition')
            competitions = _normal_mapping(result.get('competition_results'))
            competition = competitions.get(comp) if comp is not None else None
            training = _normal_mapping(competition).get('training_datasets')
            if not competitions:
                training = result.get('training_datasets')
            training_clocks = _normal_clock_issues(training, cutoff)
            if training_clocks:
                gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                             'reason': 'DATASET_KNOWLEDGE_CLOCK_INVALID', 'issues': training_clocks})
                continue
            training_ref = register(f'numeric_models/{model_id}/{comp}/training_datasets', training) if training else None
            provenance = {'prediction': prediction, 'model_id': model_id,
                'competition': comp, 'training_source_ref': training_ref,
                'dataset_binding_status': 'PRODUCER_METADATA_ONLY' if training else 'UNKNOWN',
                'dataset_physical_validation': 'NOT_PERFORMED_BY_IN_MEMORY_ADAPTER',
                'dataset_path_rule': 'data/arthur/models/<competition>/<model>/datasets/<dataset_id>.json',
                'model_details': prediction.get('model_details')}
            ref = register(f'numeric_models/{model_id}/{fixture_id}', provenance)
            values = prediction.get('probabilities', {})
            if not isinstance(values, dict):
                gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                             'reason': 'INVALID_PROBABILITY_MAP'})
                continue
            supported = {'home', 'draw', 'away'} if model_id == 'kepler' else set(_MODEL_OUTCOMES)
            for name, (market, outcome) in _MODEL_OUTCOMES.items():
                if name not in values or name not in supported:
                    continue
                value = values[name]
                if not probability(value):
                    gaps.append({'model_id': model_id, 'fixture_id': fixture_id,
                                 'outcome': name, 'reason': 'INVALID_MODEL_PROBABILITY'})
                    continue
                identifier = digest([model_id, fixture_id, market, outcome])
                records[identifier] = {'fixture_id': fixture_id, 'model_id': model_id,
                    'market': market, 'outcome': outcome, 'value': value,
                    'status': 'UNKNOWN' if value is None else 'PRODUCER_ESTIMATE',
                    'source_refs': [ref, *([training_ref] if training_ref else [])]}
    return records, gaps


def build_normal_snapshot(*, run_id: str, target_date: str, snapshot_as_of: str,
                          artifacts: dict, fixture_records=None, enriched=None,
                          club_enriched=None, candidate_context=None, weather_by_fixture=None,
                          historical_runs=None, feedback=None, news_envelopes=None,
                          owner_ids=OWNER_IDS, historical_view=None, news_coverage=None) -> SharedSnapshot:
    """Bind an already collected NORMAL frame before independent owner decisions.

    No DB, provider, predictor, simulation or filesystem operation is performed.
    Canonical in-memory hashes are explicitly not physical/raw-source validation.
    The coordinator supplies snapshot_as_of after shared acquisition/models and
    before starting owner sessions; old model/source clocks are never replaced.
    """
    require(isinstance(artifacts, dict), 'NORMAL_ARTIFACTS_REQUIRED')
    cutoff = datetime.fromisoformat(snapshot_as_of)
    require(cutoff.tzinfo is not None and cutoff.utcoffset() is not None, 'AWARE_CUTOFF_REQUIRED')
    source_manifest = {}

    def register(path, value, *, retain_value=True):
        identifier = 'normal-value:' + digest([run_id, path, value])
        source_manifest[identifier] = {'kind': 'IN_MEMORY_PRODUCER_VALUE',
            'producer_path': path, 'canonical_value_sha256': digest(value),
            'hash_scope': 'CANONICAL_JSON_NOT_PROVIDER_RAW_BYTES',
            'physical_validation': 'NOT_PERFORMED',
            **({'producer_value': value} if retain_value else {'snapshot_value_path': f'families/{path}/value'})}
        return identifier

    calendar = _normal_mapping(artifacts.get('calendar'))
    receipt = calendar.get('scope_receipt', {})
    require(isinstance(receipt, dict) and receipt.get('scope') == 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY'
            and receipt.get('date') == target_date, 'NORMAL_FULL_DAY_RECEIPT_REQUIRED')
    require(receipt.get('status') in {'COMPLETE', 'PARTIAL', 'UNKNOWN'}, 'INVALID_CALENDAR_STATUS')
    full_ids = identifiers(receipt.get('provider_fixture_ids'))
    events = receipt.get('events')
    require(isinstance(events, list) and all(isinstance(row, dict) for row in events),
            'CALENDAR_EVENTS_REQUIRED')
    event_ids = identifiers([row.get('fixture_id') for row in events])
    require(set(event_ids) == set(full_ids), 'CALENDAR_RECEIPT_EVENT_MISMATCH')
    require(not _normal_clock_issues(calendar, cutoff), 'CALENDAR_KNOWLEDGE_CLOCK_INVALID')
    if fixture_records is None:
        fixture_records = _normal_mapping(artifacts.get('fixtures')).get('records', [])
    require(isinstance(fixture_records, (list, tuple))
            and all(isinstance(row, dict) for row in fixture_records), 'FIXTURE_RECORDS_REQUIRED')
    normalized_ids = identifiers([row.get('fixture_id') for row in fixture_records])
    require(set(normalized_ids) <= set(full_ids), 'NORMALIZED_FIXTURE_OUTSIDE_FULL_CALENDAR')
    normalized = {row['fixture_id']: row for row in fixture_records}
    fixtures = []
    for event in events:
        detail = normalized.get(event['fixture_id'], {})
        detail_issues = _normal_clock_issues(detail, cutoff)
        row = {**event, **({} if detail_issues else detail),
               'calendar_event_receipt': event,
               'normalized_detail_status': 'CONFLICTING' if detail_issues else 'AVAILABLE' if detail else 'UNKNOWN'}
        if detail_issues:
            row['normalized_detail_clock_issues'] = detail_issues
            row['excluded_normalized_source_ref'] = register(f'excluded_normalized/{event["fixture_id"]}', detail)
        fixtures.append(row)
    calendar_ref = register('artifacts.calendar', calendar)
    calendar_status = receipt['status']
    if calendar_status == 'COMPLETE' and (receipt.get('rejected_rows') or receipt.get('page_issues')
            or receipt.get('provider_calendar_status') != 'COMPLETE'
            or receipt.get('routing_status') != 'COMPLETE'):
        calendar_status = 'PARTIAL'
    adapted_receipt = {'target_date': target_date, 'scope': 'FULL_PROVIDER_DAY',
        'status': calendar_status, 'fixture_ids': list(full_ids), 'source_refs': [calendar_ref],
        'producer_scope': receipt['scope'], 'producer_receipt': receipt,
        'evidence_level': 'PRODUCER_RECEIPT_NOT_INDEPENDENT_PHYSICAL_VALIDATION'}

    context = candidate_context if candidate_context is not None else artifacts.get('candidate_context_inputs', {})
    context = context if isinstance(context, dict) else {}
    weather = weather_by_fixture if weather_by_fixture is not None else context.get('weather_by_fixture', {})
    history = _normal_mapping(artifacts.get('history'))
    normal_input = _normal_mapping(artifacts.get('normal_model_input'))
    input_view = _normal_mapping(normal_input.get('input_view'))
    candidate_input = _normal_mapping(artifacts.get('candidate_input'))
    club = _normal_mapping(club_enriched)
    source_views = _normal_mapping(context.get('source_views'))
    prior_runs = [] if historical_runs is None else historical_runs
    families = {}

    def family(name, value, *, available, reason, coverage='COLLECTED_SUBSET_NOT_FULL_DAY_COVERAGE'):
        issues = _normal_clock_issues(value, cutoff)
        status = 'CONFLICTING' if issues else 'AVAILABLE' if available else 'UNKNOWN'
        ref = register(name, value, retain_value=status == 'UNKNOWN')
        families[name] = {'status': status, 'value': value if status != 'UNKNOWN' else None,
            'reason': 'INVALID_OR_FUTURE_KNOWLEDGE_CLOCK' if issues else reason,
            'source_refs': [ref], 'coverage': coverage, 'clock_issues': issues,
            'evidence_level': 'PRODUCER_VALUES_NOT_INDEPENDENT_PHYSICAL_VALIDATION'}

    family('retained_history', {'collected_online': history,
        'consumed_model_input': input_view, 'input_status': normal_input.get('status'),
        'historical_feature_availability_certified': False},
        available=bool(history.get('records') or input_view.get('history')),
        reason='SELECTED_MODEL_HISTORY_IS_NOT_THE_WHOLE_RETAINED_ARCHIVE')
    family('odds_and_price_history', {'candidate_input': candidate_input,
        'odds_bulk': artifacts.get('odds_bulk'), 'odds_recovery': artifacts.get('odds_recovery'),
        'enriched': enriched or {}, 'club_enriched': club, 'historical_runs': prior_runs},
        available=bool(candidate_input.get('candidates') or club.get('candidates')),
        reason='PROVIDER_QUOTE_CLOCK_AND_HISTORICAL_PRICE_COVERAGE_MAY_BE_UNKNOWN')
    xg = _normal_categories(context, {'xg'})
    family('team_stats_xg', {'categories': xg, 'collection': context.get('collection'),
        'sports_issues': context.get('sports_issues'), 'target_proofs': context.get('target_proofs')},
        available=_sourced_categories(xg), reason='SEASON_XG_IS_NOT_A_FORECAST_PROBABILITY')
    players = _normal_categories(context, {'players', 'rotation'})
    family('players_squad_xi', {'categories': players, 'collection': context.get('collection'),
        'player_collection': context.get('player_collection'), 'player_issues': context.get('player_issues'),
        'rotation': source_views.get('rotation')},
        available=_sourced_categories(players), reason='PACKET_PLAYER_COMPACTION_IS_NOT_FULL_ROSTER_COVERAGE')
    schedule = _normal_categories(context, {'schedule', 'travel'})
    family('schedule_venue_pitch', {'fixtures': [{key: row.get(key) for key in
        ('fixture_id', 'kickoff', 'venue_name', 'venue_city', 'venue_country', 'neutral_venue', 'fixture_source')}
        for row in fixtures], 'categories': schedule,
        'schedule': source_views.get('schedule'),
        'previous_venue_collection': context.get('previous_venue_collection')},
        available=bool(fixtures), reason='SCHEDULE_AND_VENUE_FACTS_DO_NOT_PROVE_PITCH_OR_ACTUAL_TRAVEL',
        coverage='FULL_CALENDAR_WITH_EXPLICIT_MISSING_VENUE_AND_PITCH_FIELDS')
    family('weather', {'by_fixture': weather, 'weather_scope': context.get('weather_scope'),
        'categories': _normal_categories(context, {'weather'})},
        available=isinstance(weather, dict) and any(isinstance(row, dict) and row.get('status') == 'SOURCED'
                                                   for row in weather.values()),
        reason='COLLECTED_WEATHER_IS_NOT_FULL_CALENDAR_STRICT_FORECAST_VALIDATION')
    news = [] if news_envelopes is None else news_envelopes
    require(isinstance(news, (list, tuple)), 'NEWS_ENVELOPE_SEQUENCE_REQUIRED')
    news_value = {'envelopes': list(news)}
    if news_coverage is not None:
        require(isinstance(news_coverage, dict), 'NEWS_COVERAGE_MAPPING_REQUIRED')
        news_value['research_coverage'] = news_coverage
    family('sourced_news', news_value, available=bool(news),
           reason='NO_PHYSICAL_NEWS_ENVELOPES_SUPPLIED' if not news else 'PRODUCER_NEWS_ENVELOPES_NOT_REVALIDATED',
           **({'coverage': news_coverage} if news_coverage is not None else {}))
    models = _normal_mapping(artifacts.get('numeric_models'))
    probabilities, model_gaps = _normal_model_records(models, fixtures, register, cutoff)
    family('numeric_models', {'results': models, 'model_input_uses': normal_input.get('model_input_uses'),
        'adapter_gaps': model_gaps, 'dataset_physical_validation': 'NOT_PERFORMED'},
        available=bool(probabilities), reason='PRODUCER_PREDICTIONS_WITH_LOCAL_MODEL_AND_DATASET_GAPS')
    if historical_view is None:
        family('historical_strategy_evaluation', {'prior_observations_count': len(prior_runs)},
            available=False, reason='OWN_METHOD_PIT_PRICE_COST_BOUND_HISTORICAL_TOOL_RESULT_REQUIRED')
    else:
        from goal_analysis.jobs.profile_historical_scenarios import describe_history_view

        require(isinstance(historical_view, dict) and historical_view.get('source_view_sha256')
                == digest({key: value for key, value in historical_view.items()
                           if key != 'source_view_sha256'}), 'HISTORICAL_VIEW_HASH_MISMATCH')
        require(historical_view.get('as_of') == snapshot_as_of, 'HISTORICAL_VIEW_CUTOFF_MISMATCH')
        descriptive_summary = describe_history_view(historical_view)
        family('historical_strategy_evaluation', descriptive_summary,
            available=True, reason='TOOL_CATALOG_ACCESS_NOT_YET_OWN_OPTION_SIMULATION_OR_PIT_BACKTEST',
            coverage=descriptive_summary['coverage']['scope'])
    family('candidate_scenarios', {'postselection_scenario_present': bool(artifacts.get('profile_predecision_scenarios'))},
        available=False, reason='OWN_SOURCE_BOUND_OPTIONS_AND_TOOL_RESULT_REQUIRED_BEFORE_FINAL_DECISION')
    family('own_outcomes_and_costs', {'feedback': feedback, 'historical_runs': prior_runs,
        'after_cost_completeness': 'UNKNOWN'}, available=bool(feedback or prior_runs),
        reason='BOUNDED_PRIOR_OBSERVATIONS_NOT_FULL_LEDGER_OR_ZERO_COST_CERTIFICATION')
    snapshot = build_snapshot(run_id=run_id, target_date=target_date, as_of=snapshot_as_of,
        authoritative_fixture_ids=full_ids, calendar_receipt=adapted_receipt, fixtures=fixtures,
        family_values=families, source_manifest=source_manifest, model_probabilities=probabilities,
        owner_ids=owner_ids)
    data = snapshot.data()
    require(all(ref in data['source_manifest'] for family_value in data['families'].values()
                for ref in family_value['source_refs']) and all(
                    ref in data['source_manifest'] for record in data['model_probabilities'].values()
                    for ref in record['source_refs']), 'UNRESOLVED_NORMAL_SOURCE_REFERENCE')
    return snapshot
