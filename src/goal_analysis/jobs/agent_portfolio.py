"""Pure bridge from exact owner source selections to existing product gates.

The reasoning host supplies IDs and prose only. Quotes, model arithmetic and
construction rules come from the detached NORMAL snapshot and existing helpers.
No provider, DB, fit, replay, inference or money operation occurs here.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from datetime import date, datetime

from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.engine import portfolio as core
from goal_analysis.engine.daily_223 import Daily223Policy, _product_key
from goal_analysis.engine.publication_window import allows_new_leg
from goal_analysis.generated_tickets import CORE_MARKETS, QUOTE_FIELDS, _invalid, finalize_generated
from goal_analysis.jobs.agent_snapshot import SharedSnapshot, digest, identifiers, require
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.operating_policy import (
    cautious_fallback_allowed,
    current_contract,
    effective_persona,
)
from goal_analysis.team.metrics import _quote_valid
from goal_analysis.tipsters import recommendations as club
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID

VERSION = 'ten-agent-canonical-product-bridge-v1'
ADMISSION_VERSION = 'own-option-admission-v1'
LOCAL_ADMISSION_REASONS = frozenset({
    'OWN_CORE_MARKET_OR_PRICE_RULE', 'OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE',
    'CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN', 'OUTSIDE_OWN_APPROVED_ODDS_RANGE',
    'OWN_STRATEGY_CONSTRUCTION_INVALID', 'MIXED_QUOTE_COHORT', 'DUPLICATE_SELECTED_FIXTURE',
})


class OwnOptionAdmissionRejected(ValueError):
    """A source-bound own proposal failed one explicitly named product gate."""

    def __init__(self, payload):
        require(isinstance(payload, dict) and payload.get('reason') in LOCAL_ADMISSION_REASONS,
                'UNRECOGNIZED_LOCAL_ADMISSION_REASON')
        self.payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        super().__init__(payload['reason'])

    def data(self):
        return json.loads(self.payload_json)


class _ProductAdmissionRejected(ValueError):
    pass


def _time(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'AWARE_PRODUCT_CUTOFF_REQUIRED')
    return stamp


def option_legs(card):
    """Exact products; persona means stay labelled in the canonical card.

    A persona's weighted mean is not one direct model_probability_id. Its full
    unchanged components/math remain on canonical_card, while the tool computes
    scenarios from the snapshot's original admitted per-model probabilities.
    """
    legs = deepcopy(card['legs'])
    for leg in legs:
        leg.pop('model_probability', None)
        leg.pop('model_probability_id', None)
    return legs


def _source_quotes(source, candidates, selected, now):
    raw = source.get('candidates', [])
    require(isinstance(raw, list) and isinstance(candidates, list), 'SOURCE_CANDIDATES_REQUIRED')
    raw_index, current = {}, {}
    for row in raw:
        if isinstance(row, dict):
            raw_index.setdefault(row.get('candidate_id'), []).append(row)
    for row in candidates:
        if isinstance(row, dict):
            current.setdefault(row.get('candidate_id'), []).append(row)
    for identifier in selected:
        originals, enriched = raw_index.get(identifier, []), current.get(identifier, [])
        require(len(originals) == len(enriched) == 1, 'MISSING_OR_AMBIGUOUS_SOURCE_CANDIDATE')
        original, enriched = originals[0], enriched[0]
        require(all(enriched.get(key) == original.get(key) for key in QUOTE_FIELDS),
                'SOURCE_PRODUCT_OR_QUOTE_CHANGED')
        for key in ('quote_observed_at', 'quoted_at', 'quote_timestamp_raw'):
            if original.get(key) is not None:
                require(_time(original[key]) <= now, 'FUTURE_OR_INVALID_SELECTED_QUOTE_CLOCK')


def _admission_binding(owner, proposal, snapshot, data):
    """Strict identity/provenance before any failure may be classified as local."""
    get_agent_definition(owner)
    require(owner in data['owner_ids'] and isinstance(proposal, dict)
            and set(proposal) == {'option_id', 'source_candidate_ids', 'reason'}
            and isinstance(proposal['option_id'], str) and bool(proposal['option_id'])
            and isinstance(proposal['reason'], str) and bool(proposal['reason'].strip()),
            'SOURCE_IDS_AND_REASON_ONLY')
    selected = identifiers(proposal['source_candidate_ids'])
    require(0 < len(selected) <= 6, 'ONE_TO_SIX_OWN_SOURCE_SELECTIONS_REQUIRED')
    odds = data['families']['odds_and_price_history']['value']
    source = odds['enriched'] if owner in CORE_MARKETS else odds['club_enriched']
    now = _time(data['as_of'])
    _source_quotes(odds['candidate_input'], source['candidates'], selected, now)
    rows = {row['candidate_id']: row for row in odds['candidate_input']['candidates']
            if isinstance(row, dict) and row.get('candidate_id') in selected}
    events = {row['fixture_id']: row for row in data['families']['calendar']['value']}
    club_events = (club._fixtures(list(events.values()), data['target_date'], now)
                   if owner not in CORE_MARKETS else None)
    for identifier in selected:
        row = rows[identifier]
        require(row.get('fixture_id') in data['fixture_ids'], 'SOURCE_FIXTURE_OUTSIDE_FULL_DAY')
        fixture_identity(row)
        event = events[row['fixture_id']]
        fixture_identity(event)
        require(_time(row['kickoff']) == _time(event['kickoff']), 'CALENDAR_KICKOFF_MISMATCH')
        require(allows_new_leg(row, now) and allows_new_leg(event, now), 'PREMATCH_LOCKOUT')
        require(all(club._name(row.get(key)) and club._name(row.get(key)) == club._name(event.get(key))
                    for key in ('home_team', 'away_team'))
                and all(str(row[key]) == str(event[key]) for key in (
                    'api_football_fixture_id', 'api_football_home_team_id', 'api_football_away_team_id',
                    'source_provider', 'provider_fixture_id', 'provider_home_team_id',
                    'provider_away_team_id', 'provider_league_id', 'provider_season_id')
                    if key in row and key in event), 'CALENDAR_PARTICIPANT_IDENTITY_MISMATCH')
        require(all(isinstance(row.get(key), str) and bool(row[key].strip())
                    for key in ('market_key', 'selection_key', 'period', 'bookmaker', 'region', 'currency'))
                and type(row.get('decimal_price')) in (int, float)
                and math.isfinite(row['decimal_price']) and 1 < row['decimal_price'] <= 1000,
                'INVALID_SELECTED_PRODUCT_FIELDS')
        require(_quote_valid({'fixture_id': row['fixture_id'],
            'market': row['period'] + ':' + row['market_key'], 'selection': row['selection_key'],
            'quote_provenance': row}), 'INVALID_SELECTED_NATIVE_QUOTE_SOURCE')
        if club_events is not None:
            require(row['fixture_id'] in club_events, 'INVALID_SELECTED_CLUB_FIXTURE')
            if row['period'] == 'FULL_TIME' and (row['market_key'], row['selection_key']) in club.KEYS:
                require(club._verified_quote(row, event), 'INVALID_SELECTED_CLUB_QUOTE_SOURCE')
    return {'version': ADMISSION_VERSION, 'agent_id': owner, 'snapshot_id': snapshot.snapshot_id,
        'option_id': proposal['option_id'], 'proposal_sha256': digest(proposal),
        'source_candidate_ids': list(selected), 'source_rows_sha256': digest([rows[key] for key in selected]),
        'source_as_of': data['as_of']}


def own_option_admission_partition(snapshot, owner, proposals, options, rejected):
    """Exact ordered source-bound partition; gate reasons remain producer-reported.

    No gate is rerun and no claimed reason becomes independent causal proof.
    """
    require(isinstance(proposals, list) and len(proposals) <= 6 and all(isinstance(row, dict)
            for row in proposals), 'BOUNDED_OWN_PROPOSALS_REQUIRED')
    proposal_ids = identifiers([row.get('option_id') for row in proposals])
    accepted_ids = identifiers([row.get('option_id') for row in options])
    require(isinstance(rejected, list) and all(isinstance(row, dict) for row in rejected),
            'EXPLICIT_OWN_REJECTION_ENVELOPES_REQUIRED')
    rejected_ids = identifiers([row.get('option_id') for row in rejected])
    require(not set(accepted_ids) & set(rejected_ids)
            and set(accepted_ids) | set(rejected_ids) == set(proposal_ids), 'OWN_PROPOSAL_PARTITION_CHANGED')
    require(list(accepted_ids) == [key for key in proposal_ids if key in accepted_ids]
            and list(rejected_ids) == [key for key in proposal_ids if key in rejected_ids],
            'OWN_PROPOSAL_ORDER_CHANGED')
    by_id = {row['option_id']: row for row in proposals}
    for option in options:
        require(option.get('agent_id') == owner and option.get('reason') == by_id[option['option_id']]['reason'],
                'PRODUCT_BUILDER_CHANGED_OWNER_CHOICE')
    data = snapshot.data() if rejected else None
    for row in rejected:
        identifier = row['option_id']
        binding = _admission_binding(owner, by_id[identifier], snapshot, data)
        require(row.get('reason') in LOCAL_ADMISSION_REASONS
                and row == {**binding, 'proposal_index': proposal_ids.index(identifier),
                    'status': 'REJECTED_OWN_OPTION', 'reason': row['reason'],
                    'reason_authority': 'PRODUCER_REPORTED_EXISTING_ADMISSION_GATE'},
                'OWN_REJECTION_SOURCE_OR_REASON_CHANGED')
    return {'version': ADMISSION_VERSION, 'proposal_count': len(proposals),
        'accepted_option_ids': list(accepted_ids), 'rejected_options': deepcopy(rejected),
        'ordered_partition': [{'proposal_index': index, 'option_id': key,
            'status': 'ACCEPTED' if key in accepted_ids else 'REJECTED_OWN_OPTION'}
            for index, key in enumerate(proposal_ids)],
        'reason_validation': 'SOURCE_AND_PARTITION_BOUND_NOT_INDEPENDENT_GATE_REEXECUTION'}


def _core_card(owner, selected, enriched, settings, now, receipt, universe):
    raw = [row for row in enriched['candidates'] if isinstance(row, dict)]
    policy = Daily223Policy(**enriched.get('policy', {}))
    history = enriched.get('history_analysis')
    history_rows = history.get('candidates', []) if isinstance(history, dict) else []
    history_rows = history_rows if isinstance(history_rows, list) else []
    analyses = {row.get('candidate_id'): row for row in history_rows if isinstance(row, dict)}
    prepared, rejected = core._core_pool(raw, date.fromisoformat(enriched['date']), now, policy,
        settings, analyses, Counter(_product_key(row) for row in raw),
        Counter(row.get('candidate_id') for row in raw), universe, None)
    matching = [row for row in prepared if core._matches_profile(row, owner)
                and row['decimal_price'] <= 40]
    index = {row['candidate_id']: row for row in matching}
    missing = [key for key in selected if key not in index]
    if missing:
        if any(key in rejected for key in missing):
            raise ValueError('OWN_CORE_CANDIDATE_NOT_ADMITTED:' + ','.join(
                rejected.get(key, 'OWN_MARKET_OR_PRODUCT_RULE') for key in missing))
        raise _ProductAdmissionRejected('OWN_CORE_MARKET_OR_PRICE_RULE')
    legs = [index[key] for key in selected]
    card = core._ticket(owner, legs, enriched['date'], now.isoformat(), settings, None)
    if math.prod(row['decimal_price'] for row in legs) < 15:
        full = core._core_universe({**enriched, 'calendar_scope_receipt': receipt,
                                   'calendar_universe': universe}, raw)[3]
        if not (cautious_fallback_allowed(owner, settings, [], all_daily_fixtures_considered=full)
                and core._primary_absence_proven(matching)):
            raise _ProductAdmissionRejected('CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN')
        card.update(core._effective_core_contract(owner, settings, fallback=True))
        card.setdefault('selection_diagnostics', {})['odds_exception'] = {
            'status': 'APPLIED', 'applied': True, 'full_day_coverage': full,
            'primary_options_count': 0, 'primary_absence_proven': True,
            'reason': 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH'}
    return card


def _club_card(owner, selected, enriched, settings, now, models):
    persona = effective_persona(TIPSTER_BY_ID[owner], settings)
    fixtures = club._fixtures(enriched.get('fixtures', []), enriched['date'], now)
    base, _ = club._pool(enriched, fixtures, club._models(models, fixtures, now), now)
    ranked = club._ranked(base, persona, now)
    supported = [row for row in ranked if club._positive_support(row)]
    eligible = [row for row in supported if row['components'] or club._history_option_support(
        row, enriched, enriched['date'], settings, now, persona=persona) is not None]
    fallback = None
    raw_selected = [row for row in enriched['candidates'] if row.get('candidate_id') in selected]
    if math.prod(row['decimal_price'] for row in raw_selected) < 15:
        primary, _ = club._options(eligible, persona, enriched.get('preferred_bookmakers', []))
        full = club._full_day_input(enriched)[2]
        if not cautious_fallback_allowed(owner, settings, primary, all_daily_fixtures_considered=full):
            raise _ProductAdmissionRejected('CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN')
        persona = effective_persona(TIPSTER_BY_ID[owner], settings, cautious_fallback=True)
        ranked = club._ranked(base, persona, now)
        eligible = [row for row in ranked if club._positive_support(row) and (
            row['components'] or club._history_option_support(row, enriched, enriched['date'],
                settings, now, persona=persona) is not None)]
        fallback = {'status': 'APPLIED', 'applied': True, 'full_day_coverage': full,
            'primary_options_count': 0, 'reason': 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH'}
    index = {row['quote']['candidate_id']: row for row in eligible}
    if not set(selected) <= set(index):
        raise _ProductAdmissionRejected('OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE')
    rows = [index[key] for key in selected]
    legs = [club._leg(row, persona, now, enriched) for row in rows]
    return {'profile_id': owner, 'profile_name': persona['name'], 'date': enriched['date'],
        'created_at': now.isoformat(), 'catalog_version': CATALOG_VERSION,
        'strategy_version': club.VERSION, 'persona': persona,
        'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
        'stake_eur': None, 'real_wager_placed': False, 'construction_verified': True,
        'combined_price': math.prod(row['decimal_price'] for row in legs),
        'combined_price_kind': 'THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE',
        'legs': legs, 'reasoning': [row['selection_reason'] for row in legs],
        'risk_notes': ['Calibration and historical profitability are not established.'],
        'quality_warnings': ['EXACT_READY_SOURCE_REVIEW_NOT_ESTABLISHED'],
        'selection_diagnostics': {'odds_exception': fallback,
                                 'selected_source_candidate_ids': list(selected)}}


def build_agent_option_builder(report, artifacts, enriched, club_enriched, settings, as_of):
    """Return an owner/proposal/snapshot callable over detached NORMAL inputs.

    All full-calendar choices remain available to the reasoning host. This
    function only admits the requested exact products; it never chooses a
    replacement or a supposedly stronger shared shortlist.
    """
    now = _time(as_of)
    require(current_contract(settings), 'CURRENT_OPERATING_CONTRACT_REQUIRED')
    report, artifacts, enriched, club_enriched = deepcopy((report, artifacts, enriched, club_enriched))
    settings = core._settings(deepcopy(settings))
    bound_snapshot = None
    bound_data = None
    admitted_models = None

    def build(owner, proposal, snapshot):
        nonlocal bound_snapshot, bound_data, admitted_models
        get_agent_definition(owner)
        require(isinstance(snapshot, SharedSnapshot), 'SHARED_SNAPSHOT_REQUIRED')
        if bound_snapshot is None:
            data = snapshot.data()
            odds = data['families']['odds_and_price_history'].get('value')
            require(isinstance(odds, dict) and odds.get('candidate_input') == artifacts.get('candidate_input')
                    and odds.get('enriched') == enriched and odds.get('club_enriched') == club_enriched,
                    'NORMAL_PRODUCT_SNAPSHOT_MISMATCH')
            require(data['run_id'] == report['run_id'] and data['target_date'] == report['date']
                    and _time(data['as_of']) == now, 'NORMAL_PRODUCT_RUN_OR_CUTOFF_MISMATCH')
            raw_models = (data['families']['numeric_models'].get('value') or {}).get('results', {})
            admitted_models = ProfileToolService(snapshot)._admitted_models(raw_models)
            bound_snapshot, bound_data = snapshot.snapshot_id, data
        require(snapshot.snapshot_id == bound_snapshot, 'PRODUCT_BUILDER_SNAPSHOT_CHANGED')
        require(isinstance(proposal, dict) and set(proposal) == {'option_id', 'source_candidate_ids', 'reason'}
                and isinstance(proposal['option_id'], str) and bool(proposal['option_id'])
                and isinstance(proposal['reason'], str) and bool(proposal['reason'].strip()),
                'SOURCE_IDS_AND_REASON_ONLY')
        selected = identifiers(proposal['source_candidate_ids'])
        require(0 < len(selected) <= 6, 'ONE_TO_SIX_OWN_SOURCE_SELECTIONS_REQUIRED')
        data = bound_data
        universe = data['families']['calendar']['value']
        receipt = data['calendar_receipt']['producer_receipt']
        source = enriched if owner in CORE_MARKETS else club_enriched
        candidates = source.get('candidates', [])
        _source_quotes(artifacts['candidate_input'], candidates, selected, now)
        require(all(row.get('fixture_id') in data['fixture_ids'] for row in candidates
                    if row.get('candidate_id') in selected), 'SOURCE_FIXTURE_OUTSIDE_FULL_DAY')
        admission_binding = _admission_binding(owner, proposal, snapshot, data)
        source = {**source, 'date': report['date'], 'fixtures': universe,
                  'calendar_universe': universe, 'calendar_scope_receipt': receipt}
        try:
            card = (_core_card(owner, selected, source, settings, now, receipt, universe)
                    if owner in CORE_MARKETS else
                    _club_card(owner, selected, source, settings, now, admitted_models))
        except _ProductAdmissionRejected as error:
            raise OwnOptionAdmissionRejected({**admission_binding, 'reason': str(error)}) from error
        card.update(status='DRAFT', publication_scope='RESEARCH', playable=False, stake_eur=None,
                    real_wager_placed=False, construction_verified=True)
        card.setdefault('reasoning', []).append(proposal['reason'])
        card['agent_product_binding'] = {'version': VERSION, 'snapshot_id': snapshot.snapshot_id,
            'agent_id': owner, 'option_id': proposal['option_id'],
            'run_id': report['run_id'], 'target_date': report['date'], 'source_as_of': now.isoformat(),
            'source_candidate_ids': list(selected), 'source_candidate_input_sha256': digest(artifacts['candidate_input']),
            'physical_authentication': 'NOT_PERFORMED_BY_PURE_BRIDGE'}
        failure = _invalid(card, report, now)
        if failure in {'OUTSIDE_OWN_APPROVED_ODDS_RANGE', 'OWN_STRATEGY_CONSTRUCTION_INVALID',
                       'MIXED_QUOTE_COHORT', 'DUPLICATE_OR_INVALID_FIXTURE'}:
            reason = 'DUPLICATE_SELECTED_FIXTURE' if failure == 'DUPLICATE_OR_INVALID_FIXTURE' else failure
            raise OwnOptionAdmissionRejected({**admission_binding, 'reason': reason})
        require(failure is None, 'CANONICAL_PRODUCT_REJECTED:' + str(failure))
        return {'option_id': proposal['option_id'], 'agent_id': owner, 'reason': proposal['reason'],
            'snapshot_id': snapshot.snapshot_id, 'legs': option_legs(card),
            'canonical_card': card, 'canonical_card_sha256': digest(card)}

    return build


def publish_agent_decisions(report, runner_result, settings, as_of, *, team_plan=None):
    """Atomically update and return report with only these owners' selected products.

    All incomplete/abstaining owners remain visible. Canonical validation can
    reject a selection; it cannot choose a different alternative on their behalf.
    """
    require(current_contract(settings), 'CURRENT_OPERATING_CONTRACT_REQUIRED')
    require(isinstance(runner_result, dict) and runner_result.get('version') == 'ten-agent-decision-run-v1'
            and runner_result.get('agent_count') == len(AGENT_IDS), 'TEN_AGENT_RUN_REQUIRED')
    owners = runner_result.get('owners')
    require(isinstance(owners, list) and len(owners) == len(AGENT_IDS)
            and {row.get('agent_id') for row in owners} == set(AGENT_IDS), 'EXACT_TEN_OWNER_OUTCOMES_REQUIRED')
    result = deepcopy(report)
    result.update(settings=deepcopy(settings), finished_at=_time(as_of).isoformat(),
                  ten_agent_decisions=deepcopy(runner_result), profile_decision_sources=[],
                  tickets=[], recommendations=[], profile_proposals=[], preview_tickets=[], profiles=[])
    result['team'] = deepcopy(team_plan or result.get('team', {}))
    finalize_generated(result, settings, team_plan=result['team'])
    result['profiles'] = [{'profile_id': row['agent_id'],
        'profile_name': get_agent_definition(row['agent_id']).name,
        'status': ('ABSTAINED' if row.get('decision', {}).get('status') == 'ABSTAIN' else
                   'COMPLETE' if row['status'] == 'DECISION_COMPLETED' else 'PARTIAL'),
        'agent_status': row['status'], 'reason': row.get('reason', row.get('decision', {}).get('reason')),
        'ticket_count': sum(card['profile_id'] == row['agent_id'] for card in result['generated_tickets']),
        'published': any(card['profile_id'] == row['agent_id'] for card in result['generated_tickets'])}
        for row in owners]
    complete = (result['generation_capacity']['shortfall'] == 0
                and runner_result.get('status') == 'COMPLETE'
                and all(row['status'] == 'DECISION_COMPLETED' for row in owners))
    result['construction_status'] = 'COMPLETE' if complete else 'PARTIAL'
    result['ten_agent_decisions']['canonical_ticket_count'] = len(result['generated_tickets'])
    report.clear()
    report.update(result)
    return report
