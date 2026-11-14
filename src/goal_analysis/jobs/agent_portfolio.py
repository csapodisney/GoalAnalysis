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

from goal_analysis.agents.profile_input import encode_table
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.engine import portfolio as core
from goal_analysis.engine.daily_223 import Daily223Policy, _product_key
from goal_analysis.engine.publication_window import allows_new_leg
from goal_analysis.generated_tickets import CORE_MARKETS, QUOTE_FIELDS, _invalid, finalize_generated
from goal_analysis.jobs.agent_snapshot import SharedSnapshot, digest, identifiers, require
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.operating_policy import (
    CAUTIOUS_PROFILE_IDS,
    cautious_fallback_allowed,
    current_contract,
    effective_persona,
)
from goal_analysis.team.metrics import _quote_valid
from goal_analysis.ticket_product_policy import (
    core_limits,
    feasible_search_ceiling,
    preference_key,
    report_contract,
)
from goal_analysis.tipsters import recommendations as club
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID

VERSION = 'ten-agent-canonical-product-bridge-v1'
ADMISSION_VERSION = 'own-option-admission-v1'
LOCAL_ADMISSION_REASONS = frozenset({
    'OWN_CORE_MARKET_OR_PRICE_RULE', 'OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE',
    'CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN', 'OUTSIDE_OWN_APPROVED_ODDS_RANGE',
    'OWN_STRATEGY_CONSTRUCTION_INVALID', 'MIXED_QUOTE_COHORT', 'DUPLICATE_SELECTED_FIXTURE',
    'TICKET_PRODUCT_LEG_COUNT',
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


def _core_card(owner, selected, enriched, settings, now, receipt, universe, product_policy=None):
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
                and (row['decimal_price'] <= (core_limits(owner)['max'] or math.inf)
                     if product_policy is not None else row['decimal_price'] <= 40)]
    index = {row['candidate_id']: row for row in matching}
    missing = [key for key in selected if key not in index]
    if missing:
        if any(key in rejected for key in missing):
            raise ValueError('OWN_CORE_CANDIDATE_NOT_ADMITTED:' + ','.join(
                rejected.get(key, 'OWN_MARKET_OR_PRODUCT_RULE') for key in missing))
        raise _ProductAdmissionRejected('OWN_CORE_MARKET_OR_PRICE_RULE')
    legs = [index[key] for key in selected]
    card = core._ticket(owner, legs, enriched['date'], now.isoformat(), settings, None,
                        ticket_product_contract=product_policy)
    if product_policy is None and math.prod(row['decimal_price'] for row in legs) < 15:
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


def _club_card(owner, selected, enriched, settings, now, models, product_policy=None):
    persona = effective_persona(TIPSTER_BY_ID[owner], settings, ticket_product_contract=product_policy)
    fixtures = club._fixtures(enriched.get('fixtures', []), enriched['date'], now)
    base, _ = club._pool(enriched, fixtures, club._models(models, fixtures, now), now)
    ranked = club._ranked(base, persona, now)
    supported = [row for row in ranked if club._positive_support(row)]
    eligible = [row for row in supported if row['components'] or club._history_option_support(
        row, enriched, enriched['date'], settings, now, persona=persona) is not None]
    fallback = None
    raw_selected = [row for row in enriched['candidates'] if row.get('candidate_id') in selected]
    if product_policy is None and math.prod(row['decimal_price'] for row in raw_selected) < 15:
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


SOURCE_CATALOG_POLICY = 'ALL_SOURCE_CANDIDATE_QUERY_REFERENCE_V1'


def source_candidate_catalog_reference(odds, snapshot_id):
    """Locate every original producer row; neither filter nor claim it was read."""
    require(isinstance(odds, dict) and isinstance(snapshot_id, str) and bool(snapshot_id),
            'SOURCE_CATALOG_SNAPSHOT_REQUIRED')
    primary = odds.get('candidate_input')
    require(isinstance(primary, dict) and isinstance(primary.get('candidates'), list),
            'SOURCE_CANDIDATES_REQUIRED')
    sources = {}
    for producer in ('candidate_input', 'enriched', 'club_enriched'):
        container = odds.get(producer)
        present = isinstance(container, dict) and 'candidates' in container
        rows = container.get('candidates') if present else None
        is_list = isinstance(rows, list)
        identifiers_in_order = [row['candidate_id'] for row in rows
            if isinstance(row, dict) and isinstance(row.get('candidate_id'), str) and row['candidate_id']] if is_list else []
        counts = Counter(identifiers_in_order)
        sources[producer] = {
            'namespace': 'raw',
            'path': ['families', 'odds_and_price_history', 'value', producer, 'candidates'],
            'source_field_present': present, 'queryable': is_list,
            'status': 'AVAILABLE' if is_list else 'UNKNOWN',
            'source_value_sha256': digest(rows),
            'source_row_count': len(rows) if is_list else None,
            'valid_candidate_ids_sha256': digest(identifiers_in_order) if is_list else None,
            'unique_candidate_id_count': len(counts) if is_list else None,
            'duplicate_candidate_id_count': sum(value > 1 for value in counts.values()) if is_list else None,
            'rows_without_valid_candidate_id': len(rows) - len(identifiers_in_order) if is_list else None,
        }
    return {
        'version': SOURCE_CATALOG_POLICY, 'snapshot_id': snapshot_id,
        'query_sources': sources, 'candidate_filter_applied': False,
        'full_calendar_replaced_by_priced_candidates': False,
        'full_record_read_proven': False, 'all_rows_supplied_inline': False,
        'selected_candidate_read': {'tool': 'read_data', 'namespace': 'candidates',
            'path_template': ['<exact original candidate_id>'], 'mode': 'text',
            'all_text_pages_required': True, 'includes_all_ordered_producer_rows': True},
        'instruction': 'The complete original candidate containers remain in the immutable source bundle. '
            'Use query_data on the exact raw query_sources path, with your own typed market/selection/fixture '
            'filters and projected fields; counts cover that entire container, without ranking or a shared shortlist. '
            'Use next_cursor for further matching pages within the unchanged tool budget. Do not enumerate every '
            'market row as a prerequisite. UNKNOWN optional metadata is not fabricated or silently filtered. '
            'Every proposed candidate ID still requires read_data candidates/[candidate_id] through all text pages; '
            'a query projection or this descriptor is not a full-record-read receipt. Duplicate or conflicting '
            'producer rows remain separate. All full-calendar fixtures remain in the initial snapshot. '
            'Numeric probabilities use raw/model_probabilities; compressed view tables are not row dictionaries.',
    }


def validate_source_catalog_contract(contract, snapshot_id, odds, *, source_access):
    """Rebind the new explicit protocol; leave historical inline inputs unchanged."""
    catalog = contract.get('full_candidate_catalog')
    if 'source_catalog_policy' not in contract:
        require(not isinstance(catalog, dict) or catalog.get('version') != SOURCE_CATALOG_POLICY,
                'SOURCE_CATALOG_POLICY_REQUIRED')
        return
    require(source_access and contract['source_catalog_policy'] == SOURCE_CATALOG_POLICY,
            'UNKNOWN_OR_NON_SOURCE_CATALOG_POLICY')
    expected = source_candidate_catalog_reference(odds, snapshot_id)
    require(isinstance(catalog, dict) and digest(catalog) == digest(expected),
            'SOURCE_CATALOG_REFERENCE_CHANGED')


def _full_candidate_catalog(candidate_input):
    """All original source rows, compactly addressable without admitting any row."""
    rows = candidate_input.get('candidates', [])
    require(isinstance(rows, list), 'SOURCE_CANDIDATES_REQUIRED')
    fields = ('candidate_id', 'fixture_id', 'market_key', 'selection_key', 'period',
              'decimal_price', 'bookmaker', 'region', 'currency', 'competition_type',
              'kickoff', 'quote_available', 'quoted_at', 'quote_timestamp_raw',
              'quote_timestamp_status', 'quote_observed_at', 'quote_time_basis')
    projected = [({'source_row_index': index, **{key: row[key] for key in fields if key in row}}
                  if isinstance(row, dict) else
                  {'source_row_index': index, 'malformed_source_row': deepcopy(row)})
                 for index, row in enumerate(rows)]
    counts = Counter(row['candidate_id'] for row in rows
                     if isinstance(row, dict) and isinstance(row.get('candidate_id'), str))
    return {'version': 'all-source-candidate-navigation-v1',
            'source_rows_sha256': digest(rows), 'source_row_count': len(rows),
            'unique_candidate_id_count': len(counts),
            'ambiguous_candidate_ids': {key: value for key, value in counts.items() if value > 1},
            'table': encode_table(projected), 'candidate_filter_applied': False,
            'full_calendar_replaced_by_priced_candidates': False,
            'full_record_read_proven': False,
            'instruction': 'All source rows are shown in original order, without ranking or eligibility filtering. Read each proposed ID completely through candidates/[candidate_id], following every text page. The catalogue does not replace this mandatory read, resolve duplicate IDs, or remove unpriced full-calendar fixtures.'}


def _own_construction_contract(owner, settings, report, now):
    """Describe existing gates before reasoning; never grant admission or select rows."""
    definition = get_agent_definition(owner)
    product_policy = report_contract(report)
    cautious = product_policy is None and owner in CAUTIOUS_PROFILE_IDS
    if owner in CORE_MARKETS:
        primary = core._effective_core_contract(owner, settings, ticket_product_contract=product_policy)
        exceptional = core._effective_core_contract(owner, settings, fallback=True) if cautious else None
        market, selection, period = CORE_MARKETS[owner]
        products = [{'market_key': market, 'selection_key': choice, 'period': period}
                    for choice in ([selection] if selection is not None else ['home', 'away'])]
        policy = {'source': 'core._effective_core_contract + CORE_MARKETS',
                  'effective_policy': primary, 'allowed_products': products,
                  'required_candidate_metadata': {'competition_type': 'NONEMPTY_STRING_REQUIRED_BY_EXISTING_CORE_POOL'},
                  'eligibility': 'Existing own core pool, source/quote, prematch and product validation remain required.'}
        minimum, maximum = primary['minimum_legs'], primary['maximum_legs']
        odds = {'min': primary['minimum_combined_price'], 'max': primary['maximum_combined_price']}
        fallback_odds = ({'min': exceptional['minimum_combined_price'],
                          'max': exceptional['maximum_combined_price']} if exceptional else None)
    else:
        primary = effective_persona(TIPSTER_BY_ID[owner], settings, ticket_product_contract=product_policy)
        exceptional = effective_persona(TIPSTER_BY_ID[owner], settings, cautious_fallback=True) if cautious else None
        policy = {'source': 'effective_persona(TIPSTER_BY_ID[owner], settings)',
                  'effective_policy': primary,
                  'eligibility': 'Existing own model/history support, positive support, source/quote, prematch and product validation remain required.'}
        minimum, maximum = primary['min_legs'], min(6, primary['max_legs'])
        odds = primary['target_odds_band']
        fallback_odds = exceptional['target_odds_band'] if exceptional else None
    return {
        'version': 'own-complete-ticket-construction-v1', 'agent_id': owner,
        'method_version': definition.method_version, 'run_id': report['run_id'],
        'target_date': report['date'], 'source_as_of': now.isoformat(), 'settings_sha256': digest(settings),
        **({'ticket_product_contract': deepcopy(product_policy),
            'price_policy_instruction': 'Hard combined odds minimum 10; no global upper limit. At most six distinct fixtures. Prefer 15–20 without rejecting valid values outside it. Respect any original own-personality risk maximum. A lower-odds valid ticket needs no old cautious-absence proof. Never target arbitrary huge odds or add filler; compare evidence and risk.'}
           if product_policy is not None else {}),
        'option_means': 'ONE_COMPLETE_ACCUMULATOR_NOT_ONE_RESEARCH_LEG',
        'instruction': 'For each option, select every leg of one complete ticket. Compute the product of its exact source decimal prices and satisfy this own construction policy before proposing it. Seek two or three genuinely distinct legal own tickets; never pad output or assume a rejected single leg will later be combined automatically.',
        'source_candidate_ids_mean': 'All legs of this ONE ticket, using exact original source candidate IDs.',
        'minimum_legs': minimum, 'maximum_legs': maximum, 'maximum_source_candidate_ids': 6,
        'primary_combined_price': odds, 'combined_price_rule': 'PRODUCT_OF_EXACT_SOURCE_DECIMAL_PRICES',
        'combined_price_kind': 'THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE',
        'distinct_fixtures_required': True, 'same_quote_cohort_fields': ['bookmaker', 'region', 'currency'],
        'preferred_distinct_variants': [2, 3], 'maximum_proposals': 6,
        'maximum_final_variants': definition.as_dict()['max_variants'],
        'cautious_exception': {
            'owner_eligible': cautious, 'combined_price': fallback_odds,
            'authorization': 'NOT_GRANTED_BY_THIS_DESCRIPTION',
            'conditions': ['All daily fixtures considered.',
                           'Existing builder must independently establish the required absence of a legal primary option.',
                           'All own leg, market, model/history, quote and publication conditions still apply.'],
        },
        'evidence_interpretation': {
            'core_weighted_support': ('RANKING_AND_EXPLANATORY_METADATA_NOT_A_MINIMUM_ADMISSION_GATE'
                                      if owner in CORE_MARKETS else 'OWN_CLUB_POLICY_REMAINS_REQUIRED'),
            'legacy_veto_override_minimum': 'Do not apply an inherited veto_override_context.minimum_weighted_support such as 7.2 as a new current-contract gate. Use this own_policy and the unchanged builder; club min_models, required_models, probability, edge and spread requirements remain mandatory.',
            'quote_clock': 'quoted_at=null or MISSING remains unknown and is not by itself a universal rejection of an otherwise source-verified product. Never invent a timestamp. Existing identity, quote availability, price, prematch and non-null clock validation still apply.',
            'profit_and_pit': 'Unproved profit or point-in-time history is uncertainty, not a newly invented universal admission requirement. Preserve any actual own-policy historical/model condition and explain missing evidence locally.',
        },
        'source_navigation': {
            'probability_by_id': {'namespace': 'raw', 'path': ['model_probabilities', '<model_probability_id>']},
            'probability_view': {'namespace': 'view', 'path': ['model_probabilities'],
                                 'format': 'NAMED_COLUMNS_V1', 'identifier_column': 'probability_id'},
            'instruction': 'The view table is not an ID-keyed object. Use raw/model_probabilities/<model_probability_id> for a direct original admitted probability. Keep missing probabilities UNKNOWN. Budget reads for complete candidate records before proposing full own tickets; optional evidence gaps need not stop valid alternatives.',
        },
        'own_policy': policy, 'candidate_filter_applied': False,
        'source_record_instruction': 'Read every complete original candidate record before proposing it. Check mandatory raw identity, quote, kickoff and own-policy metadata. An invalid or missing mandatory field makes that candidate ineligible: do not propose it or invent/backfill the field; continue considering other fixtures. A proposed invalid candidate can still stop this owner under the existing builder.',
        'missing_optional_data': 'Keep local UNKNOWN; do not fabricate neutral values or waive mandatory product rules.',
        'admission_authority': ('EXPLICIT_VERSIONED_PRODUCT_POLICY_WITH_EXISTING_SOURCE_MODEL_AND_PUBLICATION_GATES'
                                if product_policy is not None else
                                'UNCHANGED_EXISTING_PRODUCT_BUILDER_AND_CANONICAL_VALIDATION'),
        'fallback_or_reselection_by_application': False, 'ready_or_profit_guarantee': False,
    }


def _core_strategy_search(owner, source, settings, now, universe, product_policy=None):
    """Existing own specialist search; bounded alternatives, never publication."""
    raw = source['candidates']
    require(isinstance(raw, list) and all(isinstance(row, dict) for row in raw),
            'STRATEGY_SOURCE_CANDIDATES_REQUIRED')
    history = source.get('history_analysis')
    history_rows = history.get('candidates', []) if isinstance(history, dict) else []
    history_rows = history_rows if isinstance(history_rows, list) else []
    analyses = {row.get('candidate_id'): row for row in history_rows if isinstance(row, dict)}
    prepared, rejected = core._core_pool(raw, date.fromisoformat(source['date']), now,
        Daily223Policy(**source.get('policy', {})), settings, analyses,
        Counter(_product_key(row) for row in raw), Counter(row.get('candidate_id') for row in raw),
        universe, None)
    matching = [row for row in prepared if core._matches_profile(row, owner)
                and (row['decimal_price'] <= (core_limits(owner)['max'] or math.inf)
                     if product_policy is not None else row['decimal_price'] <= core.TARGET_MAX_ODDS)]
    rejected.update({row['candidate_id']: 'PROFILE_MARKET_MISMATCH' for row in prepared
                     if not core._matches_profile(row, owner)})
    rejected.update({row['candidate_id']: 'LEG_PRICE_ABOVE_PROFILE_MAXIMUM' for row in prepared
                     if product_policy is None and core._matches_profile(row, owner)
                     and row['decimal_price'] > core.TARGET_MAX_ODDS})
    groups = sorted({(row['bookmaker'], row['region'], row['currency']) for row in prepared})
    contract = core._effective_core_contract(owner, settings, ticket_product_contract=product_policy)
    options = []
    for group in groups:
        scoped = [row for row in matching
                  if (row['bookmaker'], row['region'], row['currency']) == group]
        upper = (feasible_search_ceiling(scoped, contract['maximum_legs'])
                 if product_policy is not None else core.TARGET_MAX_ODDS)
        if product_policy is not None and contract['maximum_combined_price'] is not None:
            upper = min(upper, contract['maximum_combined_price'])
        options.extend(core._specialist_options(scoped, upper,
            minimum=contract['minimum_combined_price'], min_legs=contract['minimum_legs'],
            max_legs=contract['maximum_legs']))
    full = core._core_universe(source, raw)
    fallback = {'status': 'PRIMARY_RANGE' if options else 'NOT_AUTHORIZED',
                'primary_options_count': len(options), 'full_day_coverage': full[3], 'applied': False}
    if product_policy is None and cautious_fallback_allowed(owner, settings, options, all_daily_fixtures_considered=full[3]):
        proven = core._primary_absence_proven(matching)
        fallback['primary_absence_proven'] = proven
        if proven:
            options = [legs for group in groups for legs in core._specialist_options(
                [row for row in matching if (row['bookmaker'], row['region'], row['currency']) == group],
                core.TARGET_MIN_ODDS, minimum=core.EXCEPTIONAL_MIN_ODDS, min_legs=2)
                if core._product(legs) < core.TARGET_MIN_ODDS]
            fallback.update(status='APPLIED' if options else 'NO_FALLBACK_OPTION',
                applied=bool(options), reason='NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH')
        else:
            fallback['reason'] = 'PRIMARY_SEARCH_COMPLETENESS_NOT_PROVEN'
    preferred = source.get('preferred_bookmakers', ['betano'])
    def rank(legs):
        preference = preference_key(float(core._product(legs))) if product_policy is not None else ()
        return (*preference, *core._specialist_rank(legs)[:3], core._bookmaker_rank(legs[0]['bookmaker'], preferred),
                len(legs), -core._product(legs), core._specialist_rank(legs)[-1])
    options.sort(key=rank)
    options = [legs for legs in options if len(legs) == 1] + core._bounded_specialist_combinations(options)
    options.sort(key=rank)
    trace = core._core_trace(raw, prepared, matching, [], rejected, full, now)
    return {'alternatives': [{'source_candidate_ids': [row['candidate_id'] for row in legs],
        'fixture_ids': [row['fixture_id'] for row in legs], 'combined_price': float(core._product(legs)),
        'quote_cohort': {key: legs[0][key] for key in ('bookmaker', 'region', 'currency')},
        'scoring': core._specialist_scoring(legs),
        'quality_warnings': [warning for row in legs for warning in row.get('quality_warnings', [])]}
        for legs in options], 'diagnostics': trace, 'odds_exception': fallback,
        'search_method': 'EXISTING_OWN_SPECIALIST_BOUNDED_DIVERSIFIED_BEAM',
        'source_producer': 'engine.portfolio._core_pool/_specialist_options/_specialist_rank',
        **({'ticket_product_contract': deepcopy(product_policy),
            'search_upper_bound': 'INPUT_DERIVED_MAX_SIX_DISTINCT_FIXTURE_PRODUCT_NOT_ADMISSION_LIMIT'}
           if product_policy is not None else {})}


def _club_strategy_search(owner, source, settings, now, models, product_policy=None):
    """Existing own persona alternatives, with no report or automatic choice published."""
    own_settings = {**deepcopy(settings), 'enabled_profiles': [owner]}
    alternatives = {}
    results = club.build_tipster_recommendations(source, models, own_settings, now,
        diversity_options=alternatives, support_guard_mode='LIVE', option_search_mode='OFF',
        ticket_product_contract=product_policy)
    own = [row for row in results if row.get('profile_id') == owner]
    require(len(own) == 1 and set(alternatives) <= {owner}, 'EXACT_OWN_PERSONA_SEARCH_REQUIRED')
    rows = []
    for item in alternatives.get(owner, []):
        card = item['recommendation']
        require(card['profile_id'] == owner, 'FOREIGN_PERSONA_SEARCH_RESULT')
        chosen = card['selection_diagnostics']['selected_source_candidate_ids']
        require(len(chosen) == len(card['legs']), 'OWN_SEARCH_SOURCE_ID_COUNT')
        rows.append({'source_candidate_ids': list(chosen),
            'fixture_ids': [leg['fixture_id'] for leg in card['legs']],
            'combined_price': card['combined_price'],
            'quote_cohort': {key: card['legs'][0][key] for key in ('bookmaker', 'region', 'currency')},
            'scoring': deepcopy(item['scoring']), 'quality_warnings': deepcopy(card['quality_warnings'])})
    return {'alternatives': rows, 'diagnostics': deepcopy(own[0]['selection_diagnostics']),
        'odds_exception': deepcopy(own[0]['selection_diagnostics'].get('odds_exception')),
        'search_method': 'EXISTING_OWN_PERSONA_BOUNDED_TICKET_GROUPS',
        'source_producer': 'tipsters.recommendations.build_tipster_recommendations/diversity_options',
        'shadow_challenger_executed': False, 'legacy_chosen_card_published': False}


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
    product_policy = report_contract(report)
    catalogue_json = json.dumps(_full_candidate_catalog(artifacts.get('candidate_input', {})),
                                sort_keys=True, ensure_ascii=False, allow_nan=False)
    construction_contracts = {owner: json.dumps(
        _own_construction_contract(owner, settings, report, now),
        sort_keys=True, ensure_ascii=False, allow_nan=False) for owner in AGENT_IDS}
    bound_snapshot = None
    bound_data = None
    admitted_models = None
    source_catalog_json = None

    search_cache = {}

    def bind_snapshot(owner, snapshot):
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

    def build(owner, proposal, snapshot):
        bind_snapshot(owner, snapshot)
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
            card = (_core_card(owner, selected, source, settings, now, receipt, universe, product_policy)
                    if owner in CORE_MARKETS else
                    _club_card(owner, selected, source, settings, now, admitted_models, product_policy))
        except _ProductAdmissionRejected as error:
            raise OwnOptionAdmissionRejected({**admission_binding, 'reason': str(error)}) from error
        card.update(status='DRAFT', publication_scope='RESEARCH', playable=False, stake_eur=None,
                    real_wager_placed=False, construction_verified=True)
        if product_policy is not None:
            card['ticket_product_contract'] = deepcopy(product_policy)
        card.setdefault('reasoning', []).append(proposal['reason'])
        card['agent_product_binding'] = {'version': VERSION, 'snapshot_id': snapshot.snapshot_id,
            'agent_id': owner, 'option_id': proposal['option_id'],
            'run_id': report['run_id'], 'target_date': report['date'], 'source_as_of': now.isoformat(),
            'source_candidate_ids': list(selected), 'source_candidate_input_sha256': digest(artifacts['candidate_input']),
            'physical_authentication': 'NOT_PERFORMED_BY_PURE_BRIDGE'}
        failure = _invalid(card, report, now)
        if failure in {'OUTSIDE_OWN_APPROVED_ODDS_RANGE', 'OWN_STRATEGY_CONSTRUCTION_INVALID',
                       'MIXED_QUOTE_COHORT', 'DUPLICATE_OR_INVALID_FIXTURE', 'TICKET_PRODUCT_LEG_COUNT'}:
            reason = 'DUPLICATE_SELECTED_FIXTURE' if failure == 'DUPLICATE_OR_INVALID_FIXTURE' else failure
            raise OwnOptionAdmissionRejected({**admission_binding, 'reason': reason})
        require(failure is None, 'CANONICAL_PRODUCT_REJECTED:' + str(failure))
        return {'option_id': proposal['option_id'], 'agent_id': owner, 'reason': proposal['reason'],
            'snapshot_id': snapshot.snapshot_id, 'legs': option_legs(card),
            'canonical_card': card, 'canonical_card_sha256': digest(card)}

    def construction_contract(owner):
        get_agent_definition(owner)
        result = json.loads(construction_contracts[owner])
        result['full_candidate_catalog'] = json.loads(catalogue_json)
        return result

    def source_construction_contract(owner, snapshot):
        nonlocal source_catalog_json
        bind_snapshot(owner, snapshot)
        if source_catalog_json is None:
            source_catalog_json = json.dumps(source_candidate_catalog_reference(
                bound_data['families']['odds_and_price_history']['value'], snapshot.snapshot_id),
                sort_keys=True, ensure_ascii=False, allow_nan=False)
        result = json.loads(construction_contracts[owner])
        result['source_catalog_policy'] = SOURCE_CATALOG_POLICY
        result['full_candidate_catalog'] = json.loads(source_catalog_json)
        result['optional_data_instruction'] = (
            'Missing optional weather, xG, lineup, venue, cost or historical PIT/profit evidence alone is not '
            'a whole-owner abstention requirement. Continue with other valid fixtures and available evidence; '
            'retain explicit UNKNOWN risks. Never fabricate a probability or override existing source, price, '
            'kickoff, product, own model/history or numerical construction gates.')
        return result

    def strategy_search(owner, snapshot):
        """Callable construction aid; no host invocation or selected-card authority."""
        bind_snapshot(owner, snapshot)
        if owner in search_cache:
            return json.loads(search_cache[owner])
        data = bound_data
        universe = data['families']['calendar']['value']
        source = enriched if owner in CORE_MARKETS else club_enriched
        source = {**deepcopy(source), 'date': report['date'], 'fixtures': deepcopy(universe),
                  'calendar_universe': deepcopy(universe),
                  'calendar_scope_receipt': deepcopy(data['calendar_receipt']['producer_receipt'])}
        result = {'version': 'own-existing-strategy-search-v1', 'agent_id': owner,
            'method_version': get_agent_definition(owner).method_version,
            'run_id': data['run_id'], 'target_date': data['target_date'],
            'snapshot_id': snapshot.snapshot_id, 'source_as_of': data['as_of'],
            'evaluation_as_of': now.isoformat(), 'settings_sha256': digest(settings),
            'original_producer_observed_at': source.get('observed_at'),
            'full_fixture_ids': list(data['fixture_ids']),
            'source_candidate_rows': len(artifacts['candidate_input']['candidates']),
            'source_candidate_input_sha256': digest(artifacts['candidate_input']),
            'own_enriched_input_sha256': digest(source), 'source_manifest_sha256': digest(data['source_manifest']),
            'all_family_states': {name: {key: envelope.get(key) for key in ('status', 'value_sha256')}
                                  for name, envelope in data['families'].items()},
            'common_shortlist_applied': False, 'agent_may_reject_modify_or_choose_outside_results': True,
            'canonical_admission_still_required': True, 'selected_raw_record_reads_still_required': True,
            'both_simulations_still_required': True, 'automatic_selection_or_publication': False,
            'calibrated_profit_or_exhaustive_optimum_claim': False,
            'search_data_usage': 'EXISTING_DECLARED_SCORE_TERMS_AND_GATES_NOT_ALL_FAMILY_CAUSAL_USE',
            'actual_host_tool_invocation': False, 'transport_binding': 'RUNNER_PRECOMPUTED_OWN_PLAN_CONTEXT_NOT_MCP_TOOL'}
        try:
            produced = (_core_strategy_search(owner, source, settings, now, universe, product_policy)
                        if owner in CORE_MARKETS else
                        _club_strategy_search(owner, source, settings, now, admitted_models, product_policy))
            result.update(produced)
            for row in result['alternatives']:
                chosen = identifiers(row['source_candidate_ids'])
                row['search_option_id'] = digest([owner, snapshot.snapshot_id, list(chosen)])
            result['status'] = 'AVAILABLE' if result['alternatives'] else 'NO_COMPLIANT_OPTION'
            result['reason'] = None if result['alternatives'] else 'OWN_EXISTING_SEARCH_RETURNED_NO_OPTION'
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            result.update(status='UNKNOWN', reason='OWN_SEARCH_SOURCE_OR_POLICY_UNAVAILABLE',
                          error_type=type(error).__name__, source_error=str(error)[:240], alternatives=[])
        result['result_id'] = digest(result)
        search_cache[owner] = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return json.loads(search_cache[owner])

    build.construction_contract = construction_contract
    build.source_construction_contract = source_construction_contract
    build.strategy_search = strategy_search
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
