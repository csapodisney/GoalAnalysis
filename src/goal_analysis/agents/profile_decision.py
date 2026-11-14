"""Pure owner/state/decision contract; no inference, tools, fits or money writes."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from goal_analysis.agents.profile_roster import agents_for_group, get_agent_definition
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    SharedSnapshot,
    digest,
    identifiers,
    json_text,
    probability,
    require,
)

SIMULATOR_ROLES = ('candidate_scenarios', 'historical_strategy_evaluation')
FAMILY_USES = {'AVAILABLE_AND_CONSIDERED', 'AVAILABLE_NOT_RELEVANT', 'MISSING',
               'STALE', 'CONFLICTING', 'UNUSABLE_FOR_THIS_METHOD'}


@dataclass(frozen=True, slots=True)
class ProfileState:
    agent_id: str
    session_id: str
    method_version: str
    snapshot_id: str
    options_json: str = '[]'
    fixture_reviews_json: str = '{}'
    family_uses_json: str = '{}'
    transcript: tuple[str, ...] = ()
    requests: tuple[str, ...] = ()
    receipts: tuple[str, ...] = ()
    final_json: str | None = None

    def __post_init__(self) -> None:
        require(all(isinstance(value, str) for value in (
            self.agent_id, self.session_id, self.method_version, self.snapshot_id,
            self.options_json, self.fixture_reviews_json, self.family_uses_json))
            and (self.final_json is None or isinstance(self.final_json, str))
            and all(isinstance(rows, tuple) and all(isinstance(row, str) for row in rows)
                    for rows in (self.transcript, self.requests, self.receipts)),
            'IMMUTABLE_OWNER_STATE_REQUIRED')


@dataclass(frozen=True, slots=True)
class ToolRequest:
    payload_json: str

    def __post_init__(self) -> None:
        require(isinstance(self.payload_json, str), 'IMMUTABLE_JSON_STRING_REQUIRED')

    def data(self) -> dict:
        return json.loads(self.payload_json)


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    payload_json: str

    def __post_init__(self) -> None:
        require(isinstance(self.payload_json, str), 'IMMUTABLE_JSON_STRING_REQUIRED')

    def data(self) -> dict:
        return json.loads(self.payload_json)


def _check(state: ProfileState, snapshot: SharedSnapshot) -> dict:
    data = snapshot.decision_data()
    require(state.agent_id in data['owner_ids'] and state.snapshot_id == snapshot.snapshot_id
            and state.session_id == digest([state.agent_id, state.method_version, snapshot.snapshot_id]),
            'FOREIGN_OWNER_OR_SNAPSHOT')
    require(state.final_json is None, 'OWNER_ALREADY_FINAL')
    return data


def _event(state: ProfileState, value: dict, **updates) -> ProfileState:
    event = {**value, 'agent_id': state.agent_id, 'session_id': state.session_id,
             'snapshot_id': state.snapshot_id}
    return replace(state, transcript=(*state.transcript, json_text(event)), **updates)


def start_sessions(snapshot: SharedSnapshot, method_versions: dict[str, str]) -> tuple[ProfileState, ...]:
    data = snapshot.decision_data()
    owners = data['owner_ids']
    agents = agents_for_group(data.get('decision_group', 'CORE_TEN'))
    require(set(owners) == {row.agent_id for row in agents}, 'EXACT_GROUP_OWNERS_REQUIRED')
    require(set(method_versions) == set(owners) and len(method_versions) == len(agents)
            and all(isinstance(value, str) and value for value in method_versions.values()),
            'EXACT_TEN_METHOD_OWNERS_REQUIRED')
    return tuple(ProfileState(owner, digest([owner, method_versions[owner], snapshot.snapshot_id]),
                              method_versions[owner], snapshot.snapshot_id) for owner in owners)


def set_options(state: ProfileState, snapshot: SharedSnapshot, options: list[dict]) -> ProfileState:
    data = _check(state, snapshot)
    require(isinstance(options, list) and all(isinstance(row, dict) for row in options), 'OWN_OPTIONS_REQUIRED')
    identifiers([row.get('option_id') for row in options])
    for option in options:
        from goal_analysis.ticket_product_policy import validate_option_product

        validate_option_product(option)
        require(option.get('agent_id') == state.agent_id and isinstance(option.get('legs'), list)
                and bool(option['legs']), 'FOREIGN_OR_EMPTY_OPTION')
        for leg in option['legs']:
            require(isinstance(leg, dict) and leg.get('fixture_id') in data['fixture_ids'], 'FOREIGN_FIXTURE')
            if 'model_probability' in leg:
                key = leg.get('model_probability_id')
                record = data['model_probabilities'].get(key)
                require(record is not None and probability(leg['model_probability'])
                        and leg['model_probability'] == record['value']
                        and all(leg.get(field) == record[field]
                                for field in ('fixture_id', 'market', 'outcome')),
                        'MODEL_P_CHANGED_OR_INVENTED')
    return _event(state, {'type': 'OWN_OPTIONS', 'option_hash': digest(options)}, options_json=json_text(options))


def consider_fixture(state: ProfileState, snapshot: SharedSnapshot, fixture_id: str,
                     *, reason: str, rejected: bool = False) -> ProfileState:
    data = _check(state, snapshot)
    require(fixture_id in data['fixture_ids'] and isinstance(reason, str) and reason
            and type(rejected) is bool, 'FIXTURE_CONSIDERATION_REQUIRED')
    reviews = json.loads(state.fixture_reviews_json)
    reviews[fixture_id] = {'reason': reason, 'rejected': rejected}
    return _event(state, {'type': 'FIXTURE_CONSIDERED', 'fixture_id': fixture_id},
                  fixture_reviews_json=json_text(reviews))


def consider_family(state: ProfileState, snapshot: SharedSnapshot, family: str,
                    *, status: str, reason: str, value_sha256: str,
                    option_ids=(), risk: str | None = None) -> ProfileState:
    data = _check(state, snapshot)
    require(family in FAMILIES and status in FAMILY_USES and isinstance(reason, str) and reason,
            'FAMILY_CONSIDERATION_REQUIRED')
    value = data['families'][family]
    require(value_sha256 == value['value_sha256'], 'STALE_FAMILY_VALUE')
    own = {row['option_id']: row for row in json.loads(state.options_json)}
    links = identifiers(option_ids)
    require(set(links) <= set(own) and (risk is None or isinstance(risk, str) and bool(risk.strip())),
            'FOREIGN_FAMILY_OPTION')
    if status in {'AVAILABLE_AND_CONSIDERED', 'AVAILABLE_NOT_RELEVANT'}:
        require(value['status'] == 'AVAILABLE', 'UNAVAILABLE_FACT_CANNOT_BECOME_AVAILABLE')
    allowed = {'AVAILABLE': {'AVAILABLE_AND_CONSIDERED', 'AVAILABLE_NOT_RELEVANT'},
               'UNKNOWN': {'MISSING'}, 'STALE': {'STALE'}, 'CONFLICTING': {'CONFLICTING'}}
    require(status in allowed[value['status']] | {'UNUSABLE_FOR_THIS_METHOD'}, 'FAMILY_STATE_MISREPORTED')
    if status == 'AVAILABLE_AND_CONSIDERED':
        require(bool(links) or bool(risk), 'VALUE_TO_OPTION_OR_RISK_LINK_REQUIRED')
    uses = json.loads(state.family_uses_json)
    uses[family] = {'status': status, 'reason': reason, 'value_sha256': value_sha256,
                    'option_ids': list(links), 'risk': risk,
                    'option_hashes': {key: digest(own[key]) for key in links}}
    return _event(state, {'type': 'FAMILY_CONSIDERED', 'family': family}, family_uses_json=json_text(uses))


def request_tool(state: ProfileState, snapshot: SharedSnapshot, role: str,
                 *, parameters: dict | None = None) -> tuple[ProfileState, ToolRequest]:
    data = _check(state, snapshot)
    require(role in SIMULATOR_ROLES, 'TOOL_NOT_ALLOWLISTED')
    parameters = {} if parameters is None else parameters
    require(isinstance(parameters, dict) and not set(parameters) - {'window', 'policy_hash'},
            'TOOL_ARGUMENT_NOT_ALLOWLISTED')
    options = json.loads(state.options_json)
    payload = {'agent_id': state.agent_id, 'session_id': state.session_id,
        'snapshot_id': snapshot.snapshot_id, 'method_version': state.method_version,
        'as_of': data['as_of'], 'role': role, 'option_hash': digest(options),
        'own_option_ids': [row['option_id'] for row in options], 'parameters': parameters}
    payload['request_id'] = digest(payload)
    require(not any(json.loads(row)['request_id'] == payload['request_id'] for row in state.requests),
            'NO_DUPLICATE_TOOL_REQUEST')
    request = ToolRequest(json_text(payload))
    return _event(state, {'type': 'TOOL_REQUEST', 'request_id': payload['request_id']},
                  requests=(*state.requests, request.payload_json)), request


def validate_tool_result(state: ProfileState, snapshot: SharedSnapshot, request: ToolRequest,
                         result: dict) -> None:
    """Check an untrusted tool envelope before it can enter the owner's prompt."""
    _check(state, snapshot)
    payload = request.data()
    require(request.payload_json in state.requests and payload['agent_id'] == state.agent_id
            and payload['session_id'] == state.session_id and payload['snapshot_id'] == snapshot.snapshot_id
            and payload['option_hash'] == digest(json.loads(state.options_json)), 'FOREIGN_TOOL_REQUEST')
    require(isinstance(result, dict) and result.get('request_id') == payload['request_id']
            and result.get('agent_id') == state.agent_id and result.get('snapshot_id') == snapshot.snapshot_id
            and result.get('option_hash') == payload['option_hash'], 'FOREIGN_TOOL_RESULT')
    require(result.get('status') in {'AVAILABLE', 'CACHED', 'UNKNOWN'}, 'EXPLICIT_TOOL_RESULT_REQUIRED')
    links = identifiers(result.get('own_option_ids', []))
    risk = result.get('risk')
    require(set(links) <= set(payload['own_option_ids'])
            and (risk is None or isinstance(risk, str) and bool(risk.strip())), 'FOREIGN_RESULT_OPTION')
    if result['status'] == 'UNKNOWN':
        require(result.get('value') is None and isinstance(result.get('reason'), str)
                and bool(result['reason']), 'UNKNOWN_RESULT_MUST_BE_EXPLICIT')
    else:
        require(result.get('value') is not None and bool(identifiers(result.get('source_refs', [])))
                and isinstance(result.get('result_id'), str) and bool(result['result_id']), 'ACTUAL_RESULT_REFERENCE_REQUIRED')
        require(bool(links) or bool(risk), 'RESULT_INTERPRETATION_LINK_REQUIRED')
        if result['status'] == 'CACHED':
            require(isinstance(result.get('cached_from_result_id'), str)
                    and bool(result['cached_from_result_id']), 'CACHE_PROVENANCE_REQUIRED')
    require(not any(json.loads(row)['request_id'] == payload['request_id'] for row in state.receipts),
            'NO_DUPLICATE_TOOL_RECEIPT')


def record_tool_result(state: ProfileState, snapshot: SharedSnapshot, request: ToolRequest,
                       result: dict, *, interpretation: str) -> ProfileState:
    validate_tool_result(state, snapshot, request, result)
    require(isinstance(interpretation, str) and bool(interpretation), 'EXPLICIT_TOOL_RESULT_REQUIRED')
    payload = request.data()
    receipt = {**json.loads(json_text(result)), 'role': payload['role'],
               'interpretation': interpretation, 'result_sha256': digest(result)}
    return _event(state, {'type': 'TOOL_RESULT', 'request_id': payload['request_id']},
                  receipts=(*state.receipts, json_text(receipt)))


def finalize_decision(state: ProfileState, snapshot: SharedSnapshot, *, option_id: str | None,
                      reason: str) -> tuple[ProfileState, OwnerDecision]:
    """Compatibility entry point for one own variant or an explicit abstention."""
    return finalize_decisions(state, snapshot,
                              option_ids=() if option_id is None else (option_id,), reason=reason)


def option_exposure(option: dict) -> str:
    """Economic selections, independent of owner/name, prose, price refresh and order."""
    rows = [{
        'fixture_id': leg.get('fixture_id'),
        'market': leg.get('market_key', leg.get('market')),
        'outcome': leg.get('selection_key', leg.get('outcome')),
        'period': leg.get('period'), 'line': leg.get('line'), 'handicap': leg.get('handicap'),
    } for leg in option['legs']]
    return digest(sorted(rows, key=json_text))


def finalize_decisions(state: ProfileState, snapshot: SharedSnapshot, *, option_ids,
                       reason: str) -> tuple[ProfileState, OwnerDecision]:
    """Freeze up to three distinct own variants after both tools and input accounting.

    This authenticates the decision contract, not quotes, payout eligibility or
    the strategy's economic edge. Those remain the canonical product gates.
    """
    data = _check(state, snapshot)
    options = json.loads(state.options_json)
    option_hash = digest(options)
    selected_ids = identifiers(option_ids)
    require(len(selected_ids) <= get_agent_definition(
        state.agent_id, decision_group=data.get('decision_group', 'CORE_TEN')).max_variants,
            'OWNER_VARIANT_LIMIT_EXCEEDED')
    require(set(selected_ids) <= {row['option_id'] for row in options}, 'FOREIGN_FINAL_OPTION')
    selected = [next(row for row in options if row['option_id'] == key) for key in selected_ids]
    require(len({option_exposure(row) for row in selected}) == len(selected),
            'DUPLICATE_SELECTED_EXPOSURE')
    require(len(selected) <= 1 or all(isinstance(row.get('reason'), str)
                                     and row['reason'].strip() for row in selected),
            'EACH_SELECTED_VARIANT_REASON_REQUIRED')
    require(isinstance(reason, str) and bool(reason), 'FINAL_REASON_REQUIRED')
    roles = {json.loads(row)['role'] for row in state.receipts
             if json.loads(row)['option_hash'] == option_hash}
    require(set(SIMULATOR_ROLES) <= roles, 'MISSING_SIMULATION_RECEIPT')
    current_receipts = [json.loads(row) for row in state.receipts
                        if json.loads(row)['option_hash'] == option_hash]
    for role in SIMULATOR_ROLES:
        covered = set()
        for receipt in current_receipts:
            if receipt['role'] == role:
                covered.update(selected_ids if receipt['status'] == 'UNKNOWN'
                               else receipt.get('own_option_ids', []))
        require(set(selected_ids) <= covered, 'SELECTED_VARIANT_SIMULATION_NOT_COVERED')
    reviews = json.loads(state.fixture_reviews_json)
    require(set(reviews) == set(data['fixture_ids']), 'FULL_CALENDAR_NOT_CONSIDERED')
    require(all(not reviews[leg['fixture_id']]['rejected']
                for option in selected for leg in option['legs']), 'SELECTED_FIXTURE_REJECTED')
    uses = json.loads(state.family_uses_json)
    require(set(uses) == set(FAMILIES), 'ALL_FAMILY_STATES_REQUIRED')
    require(all(set(value['option_ids']) <= {row['option_id'] for row in options}
                for value in uses.values()), 'STALE_FAMILY_OPTION_LINK')
    current_hashes = {row['option_id']: digest(row) for row in options}
    require(all(value['option_hashes'] == {key: current_hashes[key] for key in value['option_ids']}
                for value in uses.values()), 'STALE_FAMILY_OPTION_CONTENT')
    final = {'version': ('profile-owner-decision-v1-companion-pair'
                         if data.get('decision_group') == 'COMPANION_PAIR'
                         else 'profile-owner-decision-v2-ten-agents'),
        'agent_id': state.agent_id, 'session_id': state.session_id,
        'snapshot_id': snapshot.snapshot_id, 'method_version': state.method_version,
        'status': 'ABSTAIN' if not selected_ids else 'FINAL_DECISION',
        'option_id': selected_ids[0] if len(selected_ids) == 1 else None,
        'option_ids': list(selected_ids), 'selected_options': selected,
        'option_hash': option_hash, 'reason': reason, 'family_uses': uses,
        'tool_receipts': [json.loads(row) for row in state.receipts
                          if json.loads(row)['option_hash'] == option_hash],
        'execution_evidence': 'CONTRACT_ONLY_NOT_INFERENCE_OR_RUNTIME_PROOF'}
    decision = OwnerDecision(json_text(final))
    return _event(state, {'type': 'FINAL', 'option_ids': list(selected_ids)},
                  final_json=decision.payload_json), decision
