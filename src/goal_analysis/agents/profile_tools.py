"""Owner-bound tools over detached evidence; no provider, DB, replay or model fit.

Source envelopes are supplied by the trusted coordinator. Their content hashes,
identities and clocks are checked here, but this module does not authenticate
physical files or provider signatures. Historical evaluation requires original
price/feature/settlement envelopes, not a self-declared ``verified`` boolean.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, ToolRequest
from goal_analysis.agents.profile_roster import get_agent_definition
from goal_analysis.jobs.agent_snapshot import (
    SharedSnapshot,
    _normal_clock_issues,
    digest,
    identifiers,
    json_text,
    probability,
    require,
)

VERSION = 'owner-bound-profile-tools-v2-descriptive-history'
_OUTCOMES = {('FULL_TIME:h2h', 'home'): 'home', ('FULL_TIME:h2h', 'draw'): 'draw',
             ('FULL_TIME:h2h', 'away'): 'away', ('FULL_TIME:totals_2_5', 'over'): 'over25',
             ('FULL_TIME:totals_2_5', 'under'): 'under25', ('FULL_TIME:btts', 'yes'): 'btts_yes'}


def _copy(value):
    return json.loads(json_text(value))


def _time(value):
    require(isinstance(value, str), 'AWARE_EVIDENCE_CLOCK_REQUIRED')
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
            'AWARE_EVIDENCE_CLOCK_REQUIRED')
    return stamp


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in '0123456789abcdef' for character in value)


def _number(value, *, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (
        value > 0 if positive else value >= 0)


class ProfileToolService:
    """A run-local evidence service. The caller supplies no mutable shared state."""

    def __init__(self, snapshot: SharedSnapshot, *, artifacts=None,
                 historical_evaluations=(), scenario_builder=None, historical_view=None):
        require(isinstance(snapshot, SharedSnapshot), 'SHARED_SNAPSHOT_REQUIRED')
        require(artifacts is None or isinstance(artifacts, dict), 'SCENARIO_ARTIFACTS_REQUIRED')
        require(isinstance(historical_evaluations, (list, tuple)), 'HISTORY_SEQUENCE_REQUIRED')
        require(scenario_builder is None or callable(scenario_builder), 'SCENARIO_BUILDER_REQUIRED')
        self._snapshot = snapshot
        self._data = snapshot.data()
        # Kernels read exactly these three original keys. Keep the complete
        # producer hash separately; raw history/report copies are not kernel input.
        self._artifacts_hash = digest(artifacts)
        self._artifacts = None if artifacts is None else _copy({key: artifacts[key]
            for key in ('candidate_input', 'numeric_models', 'fixtures') if key in artifacts})
        self._history = _copy(historical_evaluations)
        self._history_hash = digest(self._history)
        self._manifest_hash = digest(self._data['source_manifest'])
        self._historical_engine = None
        if historical_view is not None:
            from goal_analysis.jobs.profile_historical_scenarios import HistoricalScenarioEngine

            summary = self._data['families']['historical_strategy_evaluation'].get('value')
            require(isinstance(summary, dict) and summary.get('source_view_sha256')
                    == historical_view.get('source_view_sha256'), 'HISTORICAL_SNAPSHOT_VIEW_MISMATCH')
            self._historical_engine = HistoricalScenarioEngine(historical_view)
        self._scenario_builder = scenario_builder
        self._cache = {}

    def _request(self, request, options):
        require(isinstance(request, ToolRequest), 'OWN_TOOL_REQUEST_REQUIRED')
        payload = request.data()
        required = {'agent_id', 'session_id', 'snapshot_id', 'method_version', 'as_of',
                    'role', 'option_hash', 'own_option_ids', 'parameters', 'request_id'}
        require(isinstance(payload, dict) and set(payload) == required,
                'INVALID_TOOL_REQUEST_SHAPE')
        require(payload['agent_id'] in self._data['owner_ids']
                and payload['snapshot_id'] == self._snapshot.snapshot_id
                and isinstance(payload['method_version'], str) and bool(payload['method_version'])
                and payload['session_id'] == digest([payload['agent_id'], payload['method_version'],
                                                     self._snapshot.snapshot_id]),
                'FOREIGN_OWNER_OR_SNAPSHOT')
        require(payload['request_id'] == digest({key: value for key, value in payload.items()
                                                if key != 'request_id'}), 'TOOL_REQUEST_HASH_MISMATCH')
        require(payload['method_version'] == get_agent_definition(
            payload['agent_id'], decision_group=self._data.get('decision_group', 'CORE_TEN')).method_version,
                'UNBOUND_TOOL_METHOD_VERSION')
        require(payload['role'] in SIMULATOR_ROLES and isinstance(payload['parameters'], dict)
                and not set(payload['parameters']) - {'window', 'policy_hash'},
                'TOOL_NOT_ALLOWLISTED')
        require(payload['as_of'] == self._data['as_of'], 'FOREIGN_TOOL_CUTOFF')
        _time(payload['as_of'])
        require(isinstance(options, list) and all(isinstance(row, dict) for row in options),
                'OWN_OPTIONS_REQUIRED')
        option_ids = identifiers([row.get('option_id') for row in options])
        require(tuple(identifiers(payload['own_option_ids'])) == option_ids
                and payload['option_hash'] == digest(options), 'STALE_TOOL_OPTIONS')
        for option in options:
            from goal_analysis.ticket_product_policy import validate_option_product

            validate_option_product(option)
            require(option.get('agent_id') == payload['agent_id']
                    and isinstance(option.get('legs'), list) and bool(option['legs']),
                    'FOREIGN_OR_EMPTY_OPTION')
            for leg in option['legs']:
                require(isinstance(leg, dict) and leg.get('fixture_id') in self._data['fixture_ids'],
                        'FOREIGN_TOOL_FIXTURE')
                if 'model_probability' in leg:
                    source = self._data['model_probabilities'].get(leg.get('model_probability_id'))
                    require(source is not None and probability(leg['model_probability'])
                            and leg['model_probability'] == source['value']
                            and all(leg.get(key) == source[key] for key in
                                    ('fixture_id', 'market', 'outcome')), 'MODEL_P_CHANGED_OR_INVENTED')
        return payload, _copy(options)

    @staticmethod
    def _unknown(payload, reason):
        return {key: payload[key] for key in ('request_id', 'agent_id', 'session_id',
                'snapshot_id', 'option_hash')} | {'role': payload['role'], 'status': 'UNKNOWN',
                'value': None, 'reason': reason, 'own_option_ids': list(payload['own_option_ids']),
                'tool_version': VERSION, 'physical_source_validation': 'NOT_PERFORMED'}

    def evaluate(self, request: ToolRequest, options: list[dict]) -> dict:
        payload, own = self._request(request, options)
        key = digest({'request': payload, 'artifacts_sha256': self._artifacts_hash,
                      'history_sha256': self._history_hash, 'version': VERSION,
                      'descriptive_view_sha256': self._historical_engine.source_view_sha256
                      if self._historical_engine is not None else None})
        if key in self._cache:
            result = _copy(self._cache[key])
            result.update(status='CACHED', cached_from_result_id=result['result_id'])
            return result
        result = (self._scenarios(payload, own) if payload['role'] == 'candidate_scenarios'
                  else self._historical(payload, own))
        if result['status'] != 'UNKNOWN':
            self._cache[key] = _copy(result)
        return _copy(result)

    def _available(self, payload, value, bindings):
        result_id = digest({'request': payload, 'value': value, 'bindings': bindings, 'version': VERSION})
        return self._unknown(payload, '') | {'status': 'AVAILABLE', 'value': value,
            'reason': None, 'result_id': result_id,
            'source_refs': ['snapshot:' + self._snapshot.snapshot_id, 'tool-result:' + result_id],
            'source_bindings': bindings,
            'risk': 'Tool evidence retains its coverage and assumptions; no profit or current-price certification.'}

    def _scenario_inputs(self):
        families = self._data['families']
        require(families['odds_and_price_history']['status'] in {'AVAILABLE', 'CONFLICTING'}
                and families['numeric_models']['status'] in {'AVAILABLE', 'CONFLICTING'},
                'SCENARIO_SOURCE_FAMILY_NOT_AVAILABLE')
        odds = families['odds_and_price_history'].get('value')
        model = families['numeric_models'].get('value')
        require(isinstance(odds, dict) and isinstance(model, dict), 'SCENARIO_SOURCE_FAMILIES_UNAVAILABLE')
        candidates, models = odds.get('candidate_input'), model.get('results')
        require(isinstance(candidates, dict) and isinstance(models, dict), 'SCENARIO_SOURCE_VALUES_MISSING')
        calendar_rows = families['calendar']['value']
        if self._artifacts is None:
            return {'candidate_input': self._admitted_candidates(candidates),
                    'numeric_models': self._admitted_models(models),
                    'fixtures': _copy(calendar_rows)}
        actual = self._artifacts
        require(actual.get('candidate_input') == candidates and actual.get('numeric_models') == models,
                'SCENARIO_ARTIFACT_SNAPSHOT_MISMATCH')
        fixtures = actual.get('fixtures', {})
        rows = fixtures.get('records', []) if isinstance(fixtures, dict) else fixtures
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
                'SCENARIO_FIXTURES_MISSING')
        indices = {row['fixture_id']: row for row in calendar_rows}
        identifiers([row.get('fixture_id') for row in rows])
        require(all(row['fixture_id'] in indices and all(indices[row['fixture_id']].get(key) == value
                    for key, value in row.items()) for row in rows), 'SCENARIO_FIXTURE_SNAPSHOT_MISMATCH')
        result = _copy(actual)
        result['candidate_input'] = self._admitted_candidates(candidates)
        result['numeric_models'] = self._admitted_models(models)
        return result

    def _admitted_candidates(self, candidates):
        """Local exact source lookup; an unrelated invalid quote cannot veto all options."""
        rows = candidates.get('candidates')
        require(isinstance(rows, list), 'SCENARIO_CANDIDATE_SEQUENCE_REQUIRED')
        groups, admitted = {}, []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('candidate_id'), str) and row['candidate_id']:
                groups.setdefault(row['candidate_id'], []).append(row)
        cutoff = _time(self._data['as_of'])
        for group in groups.values():
            # Never resolve ambiguity by silently retaining the valid half of a duplicate pair.
            if len(group) == 1 and not _normal_clock_issues(group[0], cutoff):
                admitted.append(_copy(group[0]))
        return {'candidates': admitted, 'source_projection': {
            'original_candidate_input_sha256': digest(candidates), 'original_row_count': len(rows),
            'admitted_row_count': len(admitted), 'policy': 'UNIQUE_IDENTITIES_AND_NONFUTURE_KNOWLEDGE_CLOCKS',
            'provider_quote_clock_required_for_price_authentication': True,
            'current_price_or_PIT_authentication': False}}

    def _admitted_models(self, models):
        """Use only exact prediction fields admitted by the snapshot clock/ambiguity gate."""
        admitted = self._data['model_probabilities'].values()
        manifest, result = self._data['source_manifest'], {}
        for model_id, source in models.items():
            if model_id not in {'gauss', 'kepler', 'laplace'} or not isinstance(source, dict):
                continue
            model = result[model_id] = _copy(source)
            kept = []
            predictions = model.get('predictions', [])
            if not isinstance(predictions, list):
                predictions = []
            for prediction in predictions:
                if not isinstance(prediction, dict) or not isinstance(prediction.get('probabilities'), dict):
                    continue
                values = {}
                for record in admitted:
                    if record['model_id'] != model_id or record['fixture_id'] != prediction.get('fixture_id'):
                        continue
                    name = _OUTCOMES.get((record['market'], record['outcome']))
                    if name is None or prediction['probabilities'].get(name) != record['value']:
                        continue
                    for ref in record['source_refs']:
                        envelope = manifest.get(ref)
                        value = envelope.get('producer_value') if isinstance(envelope, dict) else None
                        if (isinstance(value, dict) and value.get('prediction') == prediction
                                and envelope.get('canonical_value_sha256') == digest(value)):
                            values[name] = record['value']
                            break
                if values:
                    kept.append({**prediction, 'probabilities': values})
            model['predictions'] = kept
            model['snapshot_admission'] = 'ONLY_EXACT_ADMITTED_PROBABILITY_FIELDS_NO_ORIGINAL_MUTATION'
        return result

    def _scenarios(self, payload, options):
        if not options:
            return self._unknown(payload, 'NO_OWN_OPTIONS_TO_SIMULATE')
        try:
            inputs = self._scenario_inputs()
        except (ValueError, KeyError, TypeError) as error:
            return self._unknown(payload, str(error) if isinstance(error, ValueError)
                                 else 'SCENARIO_SOURCE_SHAPE_INVALID')
        cards = [{**row, 'profile_id': payload['agent_id'], 'method_version': payload['method_version'],
                  'status': 'TARGET_PROPOSAL'} for row in options]
        builder = self._scenario_builder
        if builder is None:
            from goal_analysis.jobs.profile_scenarios import build_profile_scenario_context

            builder = build_profile_scenario_context
        try:
            produced = builder({'run_id': self._data['run_id'], 'date': self._data['target_date']},
                               inputs, _copy(cards), as_of=payload['as_of'], enabled=True)
            require(isinstance(produced, dict) and produced.get('status') in
                    {'AVAILABLE', 'PARTIAL', 'UNKNOWN'}, 'INVALID_SCENARIO_RESULT')
            rows = produced.get('profiles')
            require(isinstance(rows, list) and len(rows) == len(cards), 'SCENARIO_OPTION_COVERAGE_MISMATCH')
            for row, card in zip(rows, cards, strict=True):
                require(isinstance(row, dict) and row.get('profile_id') == payload['agent_id']
                        and row.get('source_card_sha256') == digest(card), 'FOREIGN_SCENARIO_RESULT')
            if produced['status'] == 'UNKNOWN':
                return self._unknown(payload, 'NO_SOURCE_BOUND_MODEL_SCENARIO')
            value = {'coverage_status': produced['status'], 'by_option': {
                option['option_id']: row for option, row in zip(options, rows, strict=True)},
                'assumptions': produced.get('assumptions'), 'producer_result': produced,
                'numeric_family_diagnostic': {key: self._data['families']['numeric_models'].get(key)
                    for key in ('status', 'reason', 'clock_issues')},
                'odds_family_diagnostic': {key: self._data['families']['odds_and_price_history'].get(key)
                    for key in ('status', 'reason', 'clock_issues')},
                'historical_profit_or_calibration_claim': False}
            return self._available(payload, value, {'input_sha256': digest(inputs),
                'numeric_models_sha256': digest(inputs['numeric_models']),
                'source_manifest_sha256': self._manifest_hash,
                'full_producer_artifacts_sha256': self._artifacts_hash,
                'evidence_kind': 'EXISTING_PURE_SCENARIO_OVER_SUPPLIED_SOURCE_VALUES'})
        except Exception:  # noqa: BLE001 - an optional own tool must not trigger a retry or global veto.
            return self._unknown(payload, 'SCENARIO_EVALUATOR_FAILED_OR_UNBOUND_RESULT')

    @staticmethod
    def _envelope(sources, key, kind, cutoff):
        source = sources.get(key)
        require(isinstance(source, dict) and source.get('kind') == kind
                and isinstance(source.get('payload'), dict)
                and source.get('sha256') == digest(source['payload'])
                and _time(source.get('available_at')) <= cutoff, 'UNBOUND_HISTORICAL_SOURCE')
        return source['payload']

    def _historical_record(self, record, payload):
        parameters, cutoff = payload['parameters'], _time(payload['as_of'])
        window = parameters['window']
        start, end = _time(window['start']), _time(window['end'])
        require(start < end <= cutoff, 'HISTORICAL_WINDOW_INVALID')
        require(all(record.get(key) == payload[key] for key in
                    ('agent_id', 'method_version', 'option_hash', 'own_option_ids'))
                and record.get('window') == window and record.get('policy_hash') == parameters['policy_hash'],
                'HISTORICAL_METHOD_OR_OPTIONS_MISMATCH')
        evaluated = _time(record.get('evaluation_as_of'))
        require(end <= evaluated <= cutoff
                and isinstance(record.get('evaluation_id'), str) and bool(record['evaluation_id']),
                'HISTORICAL_EVALUATION_CLOCK_INVALID')
        sources, observations = record.get('sources'), record.get('observations')
        require(isinstance(sources, dict) and bool(sources) and isinstance(observations, list)
                and bool(observations) and all(isinstance(row, dict) for row in observations),
                'NO_CHRONOLOGICAL_HISTORICAL_OBSERVATIONS')
        currencies, total_stake, total_return, total_cost, cost_known = set(), 0.0, 0.0, 0.0, True
        previous = None
        seen, settled_sources = set(), set()
        identifiers([row.get('observation_id') for row in observations])
        for row in observations:
            decision, kickoff, settled = (_time(row[key]) for key in ('decision_at', 'kickoff', 'settled_at'))
            require(start <= decision < kickoff <= settled <= end and (previous is None or previous <= decision)
                    and _hash(row.get('model_sha256')), 'NON_CHRONOLOGICAL_OR_FUTURE_HISTORY')
            previous = decision
            identity = (row.get('selection_id'), decision)
            require(identity not in seen and row.get('settlement_source') not in settled_sources,
                    'DUPLICATE_HISTORICAL_OBSERVATION')
            seen.add(identity)
            settled_sources.add(row.get('settlement_source'))
            features = self._envelope(sources, row.get('feature_source'), 'PIT_FEATURES', decision)
            quote = self._envelope(sources, row.get('quote_source'), 'HISTORICAL_PROVIDER_QUOTE', decision)
            selection = self._envelope(sources, row.get('decision_source'), 'HISTORICAL_STRATEGY_DECISION', decision)
            settlement = self._envelope(sources, row.get('settlement_source'), 'SETTLEMENT', evaluated)
            require(features.get('model_sha256') == row['model_sha256']
                    and all(selection.get(key) == payload[key] for key in ('agent_id', 'method_version'))
                    and selection.get('policy_hash') == parameters['policy_hash']
                    and selection.get('model_sha256') == row['model_sha256']
                    and _time(selection.get('decision_at')) == decision
                    and _time(quote.get('quoted_at')) <= _time(sources[row['quote_source']]['available_at']) <= decision
                    and _time(settlement.get('settled_at')) == settled
                    and settled <= _time(sources[row['settlement_source']]['available_at'])
                    and _number(quote.get('decimal_odds'), positive=True) and quote['decimal_odds'] > 1
                    and _number(settlement.get('stake'), positive=True)
                    and _number(settlement.get('gross_return')), 'HISTORICAL_PRICE_OR_SETTLEMENT_MISSING')
            require(isinstance(quote.get('currency'), str) and bool(quote['currency'])
                    and quote['currency'] == settlement.get('currency')
                    and isinstance(row.get('selection_id'), str) and bool(row['selection_id'])
                    and all(item.get('selection_id') == row['selection_id'] for item in
                            (features, quote, settlement, selection)), 'HISTORICAL_SELECTION_OR_CURRENCY_MISMATCH')
            currencies.add(quote['currency'])
            total_stake += settlement['stake']
            total_return += settlement['gross_return']
            if row.get('cost_source') is None:
                cost_known = False
                continue
            try:
                cost = self._envelope(sources, row['cost_source'], 'ALLOCATED_COST', evaluated)
                require(_number(cost.get('amount')) and isinstance(cost.get('currency'), str)
                        and cost.get('observation_id') == row['observation_id']
                        and cost.get('selection_id') == row['selection_id'], 'COST_ALLOCATION_UNBOUND')
                if cost['currency'] == quote['currency']:
                    converted_cost = cost['amount']
                else:
                    fx = self._envelope(sources, row.get('fx_source'), 'HISTORICAL_FX', evaluated)
                    require(fx.get('base_currency') == cost['currency']
                            and fx.get('quote_currency') == quote['currency']
                            and _number(fx.get('rate'), positive=True)
                            and _time(fx.get('quoted_at')) <= _time(sources[row['fx_source']]['available_at'])
                            and _time(cost.get('fx_conversion_at')) == _time(fx.get('quoted_at')),
                            'COST_FX_UNBOUND')
                    converted_cost = cost['amount'] * fx['rate']
                require(math.isfinite(total_cost + converted_cost), 'COST_AGGREGATE_NONFINITE')
                total_cost += converted_cost
            except (ValueError, TypeError, KeyError, OverflowError):
                cost_known = False
        require(len(currencies) == 1, 'MIXED_SETTLEMENT_CURRENCIES_UNSUPPORTED')
        require(all(math.isfinite(value) for value in (total_stake, total_return, total_cost)),
                'HISTORICAL_AGGREGATE_NONFINITE')
        net = total_return - total_stake
        roi = net / total_stake
        require(math.isfinite(roi), 'HISTORICAL_ROI_NONFINITE')
        if cost_known and not math.isfinite((net - total_cost) / total_stake):
            cost_known = False
        return {'evaluation_id': record['evaluation_id'], 'sample_count': len(observations),
            'currency': next(iter(currencies)), 'stake': total_stake, 'gross_return': total_return,
            'net_before_cost': net, 'roi_before_cost': roi,
            'allocated_cost': total_cost if cost_known else None,
            'net_after_cost': net - total_cost if cost_known else None,
            'roi_after_cost': (net - total_cost) / total_stake if cost_known else None,
            'cost_status': 'AVAILABLE' if cost_known else 'UNKNOWN_COST_OR_FX',
            'source_evidence_boundary': 'CALLER_SUPPLIED_ENVELOPES_CONSISTENCY_CHECKED_NOT_PHYSICALLY_AUTHENTICATED',
            'prospective_profit_claim': False}

    def _historical(self, payload, options):
        from goal_analysis.jobs.profile_historical_scenarios import is_descriptive_policy

        if is_descriptive_policy(payload['parameters'].get('policy_hash')):
            if self._historical_engine is None:
                return self._unknown(payload, 'DESCRIPTIVE_HISTORY_VIEW_NOT_SUPPLIED')
            try:
                odds = self._data['families']['odds_and_price_history'].get('value')
                require(isinstance(odds, dict), 'OWN_HISTORY_CANDIDATE_SOURCE_REQUIRED')
                candidates = self._admitted_candidates(odds['candidate_input'])['candidates']
                value = self._historical_engine.evaluate(payload, options, candidates=candidates,
                    fixtures=self._data['families']['calendar']['value'])
                if value['status'] == 'UNKNOWN':
                    return self._unknown(payload, value['reason']) | {'diagnostics': value}
                return self._available(payload, value, {
                    'evidence_kind': 'DESCRIPTIVE_RECORDED_RESULTS_V1',
                    'source_view_sha256': self._historical_engine.source_view_sha256,
                    'source_manifest_sha256': self._manifest_hash,
                    'full_producer_artifacts_sha256': self._artifacts_hash,
                    'historical_profit_or_PIT_claim': False})
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                return self._unknown(payload, 'DESCRIPTIVE_HISTORY_INVALID:' + str(error))
        parameters = payload['parameters']
        if (not _hash(parameters.get('policy_hash')) or not isinstance(parameters.get('window'), dict)
                or set(parameters['window']) != {'start', 'end'}):
            return self._unknown(payload, 'HISTORICAL_WINDOW_AND_POLICY_REQUIRED')
        matches = [row for row in self._history if isinstance(row, dict)
                   and row.get('agent_id') == payload['agent_id']
                   and row.get('method_version') == payload['method_version']
                   and row.get('option_hash') == payload['option_hash']
                   and row.get('policy_hash') == parameters['policy_hash']
                   and row.get('window') == parameters['window']]
        if len(matches) != 1:
            return self._unknown(payload, 'NO_UNIQUE_BOUND_HISTORICAL_STRATEGY_EVALUATION')
        try:
            value = self._historical_record(matches[0], payload)
        except (ValueError, TypeError, KeyError, OverflowError):
            return self._unknown(payload, 'HISTORICAL_EVIDENCE_INVALID_OR_FUTURE')
        return self._available(payload, value, {'evaluation_sha256': digest(matches[0]),
            'source_manifest_sha256': digest(self._data['source_manifest']),
            'evidence_kind': 'CALLER_SUPPLIED_CHRONOLOGICAL_ORIGINAL_PRICE_OBSERVATIONS'})
