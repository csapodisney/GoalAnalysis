"""Bounded, owner-isolated decisions on the existing host; never places a wager."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from goal_analysis.agents.profile_decision import (
    SIMULATOR_ROLES,
    consider_family,
    consider_fixture,
    finalize_decisions,
    option_exposure,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
    validate_tool_result,
)
from goal_analysis.agents.profile_input import (
    decision_view,
    final_option_details,
    final_shared_reference,
    public_tool_results,
)
from goal_analysis.agents.profile_roster import ACTIVE_AGENTS, get_agent_definition
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    own_option_admission_partition,
)
from goal_analysis.jobs.agent_snapshot import digest, identifiers, json_text, require


def _now():
    return datetime.now(UTC)


@dataclass
class DecisionBudget:
    """Caller binds a finite allowance; this does not claim a monetary/token cap."""

    max_requests: int
    deadline: datetime
    clock: object = _now
    stop_requested: object = lambda: False
    used_requests: int = field(default=0, init=False)
    _lock: object = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self):
        require(type(self.max_requests) is int and 0 <= self.max_requests <= 20,
                'FINITE_TEN_AGENT_REQUEST_BUDGET_REQUIRED')
        require(self.deadline.tzinfo is not None and self.deadline.utcoffset() is not None,
                'AWARE_DEADLINE_REQUIRED')

    def reserve(self):
        with self._lock:
            seconds = (self.deadline - self.clock()).total_seconds()
            require(not self.stop_requested(), 'HUMAN_OR_ACCOUNT_STOP')
            require(seconds > 0 and self.used_requests < self.max_requests, 'DECISION_BUDGET_EXHAUSTED')
            self.used_requests += 1
            return min(300.0, seconds)


class DecisionJournal:
    """Exclusive claims survive interruption; an existing claim never resubmits."""

    def __init__(self, directory, snapshot):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        identity = {'snapshot_id': snapshot.snapshot_id, 'run_id': snapshot.data()['run_id']}
        target = self.directory / 'snapshot.json'
        try:
            self.write_once('snapshot.json', identity)
        except FileExistsError:
            require(json.loads(target.read_text('utf-8')) == identity, 'FOREIGN_JOURNAL_SNAPSHOT')

    def write_once(self, name, value):
        require(Path(name).name == name, 'JOURNAL_PATH_NOT_LOCAL')
        with (self.directory / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json_text(value))
            stream.flush()
            os.fsync(stream.fileno())

    def claim(self, request):
        purpose = {name: request.get(name) for name in
                   ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        require(all(isinstance(value, str) and value for value in purpose.values())
                and purpose['phase'] in {'PLAN', 'FINAL'}, 'EXACT_OWNER_PHASE_CLAIM_REQUIRED')
        key = digest(purpose)
        self.write_once(f'{key}.claim.json', {'claim_key': key, 'purpose': purpose,
                                            'request_sha256': digest(request), 'request': request})
        return key


def _identity(state):
    return {key: getattr(state, key) for key in ('agent_id', 'session_id', 'snapshot_id')}


def _reply_identity(reply, state, phase):
    require(isinstance(reply, dict) and reply.get('phase') == phase
            and all(reply.get(key) == value for key, value in _identity(state).items()),
            'FOREIGN_OR_INVALID_HOST_REPLY')


def source_final_reference(snapshot, seed):
    """Bind FINAL to its actual seed; full backing was available, not inline."""
    reference = final_shared_reference(snapshot, seed)
    reference['view'].update(
        scope='THIS_OWNER_FULL_CALENDAR_PLAN_PLUS_SOURCE_ACCESS_AND_OWN_SELECTED_DETAILS',
        full_inventory_was_supplied_in='AVAILABLE_THROUGH_BOUND_SOURCE_BUNDLE',
        full_candidate_values_supplied_inline=False)
    return reference


class ProfileDecisionRunner:
    """One PLAN and one FINAL per owner, tools mediated by the application.

    ``option_builder`` is mandatory: model-written source IDs must pass the
    existing strategy/product builder, never become unchecked financial cards.
    The returned collection is a decision artifact, awaiting canonical publication.
    """

    def __init__(self, *, host, tools, option_builder, journal, budget,
                 max_input_chars=360_000, max_output_chars=150_000, source_bundle=None):
        require(callable(host) and callable(option_builder), 'HOST_AND_PRODUCT_BUILDER_REQUIRED')
        require(type(max_input_chars) is int and 0 < max_input_chars <= 360_000
                and type(max_output_chars) is int and 0 < max_output_chars <= 150_000,
                'FINITE_HOST_PAYLOAD_CAPS_REQUIRED')
        self.host, self.tools, self.option_builder = host, tools, option_builder
        self.journal, self.budget = journal, budget
        self.max_input_chars, self.max_output_chars = max_input_chars, max_output_chars
        self.source_bundle = source_bundle

    def _call(self, state, phase, data, *, source_records=None):
        body = {**_identity(state), 'phase': phase,
                'persona': get_agent_definition(state.agent_id).as_dict(), 'data': data}
        require(len(json_text(body)) <= self.max_input_chars, 'OWNER_INPUT_BUDGET_EXCEEDED')
        timeout = self.budget.reserve()
        key = self.journal.claim(body)
        reply = None
        try:
            returned = self.host(json.loads(json_text(body)), timeout=timeout)
            if self.source_bundle is not None:
                from goal_analysis.agents.profile_source_host import (
                    SourceAccessReply,
                    validate_source_evidence,
                )

                require(isinstance(returned, SourceAccessReply) and isinstance(source_records, dict),
                        'VERIFIED_PRIVATE_SOURCE_ACCESS_REPLY_REQUIRED')
                reply, proof = returned.reply(), returned.evidence()
                self.journal.write_once(f'{key}.source-access.json', proof)
                validate_source_evidence(proof, request=body, reply=reply, bundle=self.source_bundle)
                source_records[phase] = proof
            else:
                reply = returned
            require(len(json_text(reply)) <= self.max_output_chars, 'OWNER_OUTPUT_BUDGET_EXCEEDED')
            _reply_identity(reply, state, phase)
        except Exception as error:
            usage = (reply.get('host_usage') if isinstance(reply, dict)
                     else getattr(error, 'host_usage', None))
            error.host_usage = usage if usage is not None else {
                'requests': None, 'usage_complete': False, 'cost_amount': None,
                'reason': 'HOST_ATTEMPT_USAGE_UNRECORDED'}
            self.journal.write_once(f'{key}.outcome.json', {
                'status': 'FAILED_NO_RETRY', 'error_type': type(error).__name__,
                'claim_key': key, 'request_sha256': digest(body), 'agent_id': state.agent_id,
                'host_usage': error.host_usage,
                'usage_completeness': 'ONLY_OBSERVED_VALUES_UNKNOWN_IS_NOT_ZERO',
            })
            raise
        self.journal.write_once(f'{key}.outcome.json', {
            'status': 'HOST_COMPLETED', 'claim_key': key, 'request_sha256': digest(body), 'reply': reply,
        })
        return reply

    def _owner(self, state, snapshot, view, source_seed=None):
        original = state
        observed_usage = []
        source_records = {}
        try:
            plan_view = view if self.source_bundle is None else source_seed
            require(isinstance(plan_view, dict), 'BOUND_PLAN_VIEW_REQUIRED')
            plan = self._call(state, 'PLAN', {'snapshot': plan_view}, source_records=source_records)
            observed_usage.append(plan.get('host_usage'))
            require(isinstance(plan.get('options'), list) and len(plan['options']) <= 6,
                    'BOUNDED_OWN_PROPOSALS_REQUIRED')
            require(all(isinstance(row, dict) for row in plan['options']), 'OWN_PROPOSALS_REQUIRED')
            identifiers([row.get('option_id') for row in plan['options']])
            if self.source_bundle is not None:
                from goal_analysis.agents.profile_source_access import require_candidate_reads

                candidate_ids = []
                for proposal in plan['options']:
                    for identifier in identifiers(proposal.get('source_candidate_ids')):
                        if identifier not in candidate_ids:
                            candidate_ids.append(identifier)
                require_candidate_reads(source_records['PLAN']['journal_records'],
                                        candidate_ids=candidate_ids, bundle=self.source_bundle)
            options, rejected_options = [], []
            for proposal_index, proposal in enumerate(plan['options']):
                require(isinstance(proposal, dict) and isinstance(proposal.get('reason'), str)
                        and bool(proposal['reason'].strip()), 'OWN_VARIANT_REASON_REQUIRED')
                try:
                    option = self.option_builder(state.agent_id, proposal, snapshot)
                except OwnOptionAdmissionRejected as error:
                    rejected_options.append({**error.data(), 'proposal_index': proposal_index,
                        'status': 'REJECTED_OWN_OPTION',
                        'reason_authority': 'PRODUCER_REPORTED_EXISTING_ADMISSION_GATE'})
                    continue
                require(option.get('option_id') == proposal.get('option_id')
                        and option.get('reason') == proposal['reason'], 'PRODUCT_BUILDER_CHANGED_OWNER_CHOICE')
                options.append(option)
            admission = own_option_admission_partition(snapshot, state.agent_id, plan['options'],
                                                       options, rejected_options)
            state = set_options(state, snapshot, options)
            fixture_rows = plan.get('fixture_reviews')
            require(isinstance(fixture_rows, list)
                    and all(isinstance(row, dict) for row in fixture_rows)
                    and len({row.get('fixture_id') for row in fixture_rows}) == len(fixture_rows),
                    'UNIQUE_FULL_DAY_REVIEW_REQUIRED')
            require({row.get('fixture_id') for row in fixture_rows} == set(snapshot.data()['fixture_ids']),
                    'FULL_CALENDAR_NOT_CONSIDERED')
            for row in fixture_rows:
                state = consider_fixture(state, snapshot, row['fixture_id'],
                                         reason=row['reason'], rejected=row['rejected'])
            requests = plan.get('tool_requests')
            require(isinstance(requests, list) and len(requests) == len(SIMULATOR_ROLES)
                    and {row.get('role') for row in requests} == set(SIMULATOR_ROLES),
                    'BOTH_OWN_SIMULATOR_REQUESTS_REQUIRED')
            tool_results = []
            for command in requests:
                state, request = request_tool(state, snapshot, command['role'],
                                              parameters=command.get('parameters'))
                result = self.tools.evaluate(request, options)
                validate_tool_result(state, snapshot, request, result)
                self.journal.write_once(f'{digest([state.session_id, request.data()["request_id"]])}.tool-full-result.json',
                    {'request': request.data(), 'result': result, 'raw_result_preserved_before_final': True})
                tool_results.append({'request': request.data(), 'result': result})
            final_data = {
                'snapshot': (source_final_reference(snapshot, plan_view) if self.source_bundle is not None
                             else final_shared_reference(snapshot, view)), 'own_plan': plan,
                'validated_own_options': options, 'tool_results': public_tool_results(tool_results),
                'own_source_details': final_option_details(snapshot, state.agent_id, options),
                'own_option_admission': admission,
            }
            final = self._call(state, 'FINAL', final_data, source_records=source_records)
            observed_usage.append(final.get('host_usage'))
            interpretations = final.get('tool_interpretations')
            require(isinstance(interpretations, list) and len(interpretations) == len(tool_results)
                    and len({row.get('request_id') for row in interpretations}) == len(interpretations),
                    'OWN_TOOL_INTERPRETATIONS_REQUIRED')
            interpretations = {row['request_id']: row['interpretation'] for row in interpretations}
            from goal_analysis.agents.profile_decision import ToolRequest
            for row in tool_results:
                request = ToolRequest(json_text(row['request']))
                state = record_tool_result(state, snapshot, request, row['result'],
                                           interpretation=interpretations[row['request']['request_id']])
            family_rows = final.get('family_uses')
            require(isinstance(family_rows, list)
                    and len({row.get('family') for row in family_rows}) == len(family_rows),
                    'UNIQUE_FAMILY_INTERPRETATIONS_REQUIRED')
            for row in family_rows:
                state = consider_family(state, snapshot, row['family'], status=row['status'],
                    reason=row['reason'], value_sha256=row['value_sha256'],
                    option_ids=row.get('option_ids', ()), risk=row.get('risk'))
            state, decision = finalize_decisions(state, snapshot,
                option_ids=final['option_ids'], reason=final['reason'])
            outcome = {'status': 'DECISION_COMPLETED', **_identity(state),
                       'decision': decision.data(), 'state_transcript': list(state.transcript),
                       'decision_evidence': json.loads(json_text({
                           'version': ('owner-decision-evidence-v3-source-access'
                               if self.source_bundle is not None else 'owner-decision-evidence-v2-compact-views'),
                           'snapshot_id': snapshot.snapshot_id,
                           'input_views': {
                               'PLAN': {'kind': ('profile_source_access.build_source_seed'
                                   if self.source_bundle is not None else 'profile_input.decision_view'),
                                   'sha256': digest(plan_view)},
                               'FINAL': {'kind': ('profile_decision_runner.source_final_reference+own_details'
                                   if self.source_bundle is not None else 'profile_input.final_shared_reference+own_details'),
                                   'shared_sha256': digest(final_data['snapshot']),
                                   'own_details_sha256': digest(final_data['own_source_details']),
                                   'public_tools_sha256': digest(final_data['tool_results']),
                                   'admission_sha256': digest(admission)},
                           },
                           'plan': plan, 'validated_own_options': options,
                           'own_option_admission': admission,
                           'tool_results': tool_results, 'final': final,
                           **({'source_access': source_records} if self.source_bundle is not None else {}),
                           'evidence_level': 'IN_PROCESS_REQUEST_REPLY_NOT_PHYSICAL_AUTHENTICATION',
                       }))}
        except Exception as error:  # noqa: BLE001 - one owner's failure cannot discard the other owners.
            if getattr(error, 'host_usage', None) is not None:
                observed_usage.append(error.host_usage)
            outcome = {'status': 'INCOMPLETE_NO_RETRY', **_identity(original),
                       'error_type': type(error).__name__, 'reason': str(error)[:300],
                       'state_transcript': list(state.transcript)}
        outcome['observed_host_usage'] = observed_usage
        self.journal.write_once(f'{original.session_id}.owner.json', outcome)
        return outcome

    def run(self, snapshot):
        # A partial or completed run is never silently resubmitted. The caller
        # can inspect existing outcomes without constructing a fresh operation.
        self.journal.write_once('run.claim.json', {'snapshot_id': snapshot.snapshot_id,
                                                  'automatic_retry': False})
        states = start_sessions(snapshot, {row.agent_id: row.method_version for row in ACTIVE_AGENTS})
        if self.source_bundle is not None:
            from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed

            require(isinstance(self.source_bundle, SourceBundle)
                    and self.source_bundle.snapshot_id == snapshot.snapshot_id
                    and self.source_bundle.raw_json == snapshot.payload_json,
                    'SOURCE_BUNDLE_SNAPSHOT_CHANGED')
            view = json.loads(self.source_bundle.view_json)
            source_seed = build_source_seed(snapshot, view)
        else:
            view, source_seed = decision_view(snapshot), None
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda state: self._owner(state, snapshot, view, source_seed), states))
        cards, duplicate_links, seen = [], [], {}
        for outcome in outcomes:
            for option in outcome.get('decision', {}).get('selected_options', []):
                exposure = option_exposure(option)
                ref = {'agent_id': outcome['agent_id'], 'option_id': option['option_id']}
                if exposure in seen:
                    duplicate_links.append({'duplicate': ref, 'same_exposure_as': seen[exposure]})
                else:
                    seen[exposure] = ref
                    cards.append({'agent_id': outcome['agent_id'], 'session_id': outcome['session_id'],
                        'option_id': option['option_id'], 'option': option,
                        'publication_status': 'REQUIRES_CANONICAL_PRODUCT_VALIDATION',
                        'playable': False})
        result = {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot.snapshot_id,
            'status': 'COMPLETE' if all(row['status'] == 'DECISION_COMPLETED' for row in outcomes) else 'PARTIAL',
            'agent_count': len(outcomes), 'owners': outcomes, 'distinct_decisions': cards,
            'duplicate_exposures': duplicate_links, 'distinct_decision_count': len(cards),
            'minimum15_decision_count_met': len(cards) >= 15, 'canonical_ticket_count': None,
            'host_requests_reserved': self.budget.used_requests,
            'financial_approval': False, 'wagers': 0}
        self.journal.write_once('decision-run.json', result)
        return result


class CodexDecisionHost:
    """Public existing-host bridge. Construction performs no login or request."""

    def __init__(self, reviewer_factory):
        require(callable(reviewer_factory), 'EXISTING_REVIEWER_FACTORY_REQUIRED')
        self.reviewer_factory = reviewer_factory

    def __call__(self, request, *, timeout, source_access=None):
        reviewer = self.reviewer_factory()
        if source_access is None:
            return reviewer.request_profile_decision(request, timeout=timeout)
        return reviewer.request_profile_decision(request, timeout=timeout, source_access=source_access)


def profile_response_schema(phase):
    """Strict, narrow action schema; model cannot supply a wager or new prices."""
    def obj(properties):
        return {'type': 'object', 'properties': properties,
                'required': list(properties), 'additionalProperties': False}

    def array(items, maximum=None):
        return {'type': 'array', 'items': items, **({'maxItems': maximum} if maximum else {})}

    text = {'type': 'string'}
    identity = {'phase': {'type': 'string', 'enum': [phase]},
                'agent_id': text, 'session_id': text, 'snapshot_id': text}
    if phase == 'PLAN':
        return obj({**identity,
            'options': array(obj({'option_id': text, 'source_candidate_ids': array(text, 6),
                                  'reason': text}), 6),
            'fixture_reviews': array(obj({'fixture_id': text, 'reason': text,
                                          'rejected': {'type': 'boolean'}})),
            'tool_requests': array(obj({
                'role': {'type': 'string', 'enum': list(SIMULATOR_ROLES)},
                'parameters': obj({'window': {'anyOf': [obj({'start': text, 'end': text}),
                                                         {'type': 'null'}]},
                                   'policy_hash': {'type': ['string', 'null']}}),
            }), 2),
        })
    require(phase == 'FINAL', 'UNKNOWN_OWNER_PHASE')
    from goal_analysis.agents.profile_decision import FAMILY_USES
    from goal_analysis.jobs.agent_snapshot import FAMILIES
    return obj({**identity, 'option_ids': array(text, 3), 'reason': text,
        'tool_interpretations': array(obj({'request_id': text, 'interpretation': text}), 2),
        'family_uses': array(obj({'family': {'type': 'string', 'enum': list(FAMILIES)},
            'status': {'type': 'string', 'enum': sorted(FAMILY_USES)}, 'reason': text,
            'value_sha256': text, 'option_ids': array(text, 6),
            'risk': {'type': ['string', 'null']}}), len(FAMILIES)),
    })
