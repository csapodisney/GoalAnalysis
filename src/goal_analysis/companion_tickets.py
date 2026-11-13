"""Two source-bound protected constructors; no acquisition, review or wagering."""
from collections import Counter
from copy import deepcopy
from datetime import datetime

from goal_analysis.agents import canonical_sha256 as digest

VERSION = 'companion-ticket-channels-v1'
CHANNELS = ('daily_portfolio', 'legacy_daily223')
PRODUCERS = {
    CHANNELS[0]: {'module': 'goal_analysis.engine.portfolio', 'function': 'build_portfolio',
                  'version': 'arthur-portfolio-v3-independent-daily223'},
    CHANNELS[1]: {'module': 'goal_analysis.jobs.daily_223_live', 'function': '_build_for_one_bookmaker',
                  'version': 'arthur-pentagram-v2.1/daily-223-evidence-v1'},
}
CLAIM_FIELDS = ('run_id', 'target_date', 'shared_snapshot_id', 'calendar_ids', 'calendar_ids_sha256',
                'input_sha256', 'input_hashes', 'settings_sha256', 'input_counts', 'families',
                'family_use_claim', 'numeric_weather_news_simulator_use')
PRODUCT_FIELDS = ('candidate_id', 'fixture_id', 'home_team', 'away_team', 'competition',
                  'competition_type', 'market_key', 'selection_key', 'period', 'settlement',
                  'bookmaker', 'region', 'currency', 'decimal_price', 'quote_source_id')


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def publication_identity(card):
    """Historical unchannelled rows keep their original profile identity."""
    channel = card.get('channel_id')
    return ('companion:' + channel if channel in CHANNELS else card['profile_id'])


def _source_card(channel, produced, candidate_input, run_id):
    from goal_analysis.generated_tickets import QUOTE_FIELDS

    if channel == 'daily_portfolio':
        choices = [row for row in produced.get('profile_proposals', [])
                   if row.get('profile_id') == 'daily223' and row.get('legs')]
        _require(len(choices) <= 1, 'MULTIPLE_PROTECTED_DAILY_RESULTS')
        if not choices:
            return None
        card = deepcopy(choices[0])
    else:
        if produced.get('construction_status') != 'COMPLETE' or not produced.get('legs'):
            return None
        card = {'profile_id': 'daily223', 'profile_name': 'Legacy DAILY 223',
            'date': produced['date'], 'created_at': produced['observed_at'],
            'status': 'DRAFT', 'legs': deepcopy(produced['legs']),
            'combined_price': produced['combined_price'], 'odds_band': produced['odds_band'],
            'reasoning': ['A meglévő historikus DAILY 223 szabályrendszer választása.'],
            'risk_notes': deepcopy(produced.get('risk_notes', [])),
            'quality_warnings': ['LEGACY_RESEARCH_ONLY_NOT_REVIEWED']}
    raw = candidate_input['candidates']
    counts = Counter(row.get('candidate_id') for row in raw)
    by_id = {row['candidate_id']: row for row in raw if counts[row.get('candidate_id')] == 1}
    for leg in card['legs']:
        source = by_id.get(leg.get('candidate_id'))
        _require(source is not None and all(leg.get(key) == source.get(key) for key in PRODUCT_FIELDS),
                 'COMPANION_SELECTED_SOURCE_PRODUCT_CHANGED')
        # Existing constructors normalize timestamps, but cannot change their instant.
        _require(datetime.fromisoformat(leg['kickoff']) == datetime.fromisoformat(source['kickoff']),
                 'COMPANION_SELECTED_KICKOFF_CHANGED')
        for key in QUOTE_FIELDS:
            if key in {'quoted_at', 'quote_timestamp_raw', 'quote_time_basis', 'quote_observed_at'}:
                continue  # Derived quote-clock metadata is kept in the unchanged producer artifact.
            if key in leg and key in source:
                _require(leg[key] == source[key] or key == 'kickoff', 'COMPANION_SOURCE_FIELD_CHANGED')
        leg.update({key: deepcopy(source[key]) for key in QUOTE_FIELDS if key in source})
        leg['source_candidate_id'] = source['candidate_id']
    card.update(channel_id=channel, producer_profile_id='daily223', publication_scope='RESEARCH',
                playable=False, real_wager_placed=False, stake_eur=None)
    card['ticket_id'] = 'companion-' + digest([run_id, channel, digest(produced), card])[:24]
    for key in ('bookmaker', 'region', 'currency'):
        card[key] = card['legs'][0][key]
    return card


def materialize_channel(report, channel):
    """Rebind a saved producer envelope; never rerun either constructor."""
    from goal_analysis.generated_tickets import _invalid, _materialize

    identifier = channel.get('channel_id')
    _require(identifier in CHANNELS and channel.get('run_id') == report['run_id']
             and channel.get('target_date') == report['date']
             and channel.get('shared_snapshot_id') == report['ten_agent_decisions']['snapshot_id'],
             'FOREIGN_COMPANION_CHANNEL')
    _require(channel.get('producer') == PRODUCERS[identifier], 'COMPANION_PRODUCER_CHANGED')
    calendar = channel.get('calendar_ids')
    _require(isinstance(calendar, list) and all(isinstance(key, str) and key for key in calendar)
             and len(set(calendar)) == len(calendar) and digest(calendar) == channel['calendar_ids_sha256'],
             'COMPANION_CALENDAR_BINDING_CHANGED')
    inputs = channel.get('input_hashes')
    _require(isinstance(inputs, dict) and set(inputs) == {'candidate_input', 'history', 'enriched', 'settings'}
             and all(isinstance(value, str) and len(value) == 64
                     and set(value) <= set('0123456789abcdef') for value in inputs.values())
             and digest(inputs) == channel['input_sha256']
             and inputs['settings'] == channel['settings_sha256'] == digest(report['settings']),
             'COMPANION_INPUT_COMMITMENT_CHANGED')
    claim = {**{key: channel[key] for key in CLAIM_FIELDS}, 'channel_id': identifier,
             'producer': PRODUCERS[identifier], 'maximum_attempts': 1}
    _require(channel.get('claim') == claim and channel.get('claim_sha256') == digest(claim),
             'COMPANION_CLAIM_CHANGED')
    produced = channel.get('producer_report')
    _require((produced is None and channel.get('producer_report_sha256') is None)
             or isinstance(produced, dict) and digest(produced) == channel.get('producer_report_sha256')
             and (channel.get('status') == 'FAILED_NO_RETRY' or produced.get('report_sha256') == digest({key: value for key, value in produced.items()
                                                         if key != 'report_sha256'})),
             'COMPANION_PRODUCER_REPORT_CHANGED')
    if channel['status'] != 'COMPLETE':
        _require(channel['status'] in {'CONSTRUCTION_INCOMPLETE', 'FAILED_NO_RETRY'}
                 and channel.get('canonical_source_card') is None and channel.get('ticket_ids') == [],
                 'INCOMPLETE_COMPANION_HAS_CARD')
        return None
    _require(produced.get('policy', {}).get('strategy_version') == PRODUCERS[identifier]['version']
             if identifier == CHANNELS[0] else produced.get('module') == 'DAILY_223'
             and produced.get('prompt_version', '') + '/' + produced.get('weight_version', '')
             == PRODUCERS[identifier]['version'], 'COMPANION_PRODUCER_VERSION_CHANGED')
    card = channel['canonical_source_card']
    _require(isinstance(card, dict) and channel.get('canonical_source_card_sha256') == digest(card)
             and card.get('channel_id') == identifier and card.get('profile_id') == 'daily223'
             and card.get('producer_profile_id') == 'daily223'
             and card.get('publication_scope') == 'RESEARCH' and card.get('playable') is False
             and 'agent_decision' not in card and 'agent_product_binding' not in card
             and channel.get('ticket_ids') == [card.get('ticket_id')], 'UNBOUND_COMPANION_PRODUCT')
    source_rows = channel.get('selected_source_rows')
    _require(isinstance(source_rows, list) and len(source_rows) == len(card['legs'])
             and {row['candidate_id'] for row in source_rows} == {leg['candidate_id'] for leg in card['legs']}
             and all(row['fixture_id'] in calendar for row in source_rows)
             and _source_card(identifier, produced, {'candidates': source_rows}, report['run_id']) == card,
             'COMPANION_PRODUCER_TO_CANONICAL_REPRODUCTION_CHANGED')
    cutoff = datetime.fromisoformat(report.get('finished_at', report['observed_at']))
    failure = _invalid(card, report, cutoff)
    _require(failure is None, 'INVALID_COMPANION_PRODUCT:' + str(failure))
    output = _materialize(card, 'companion_ticket_channels', report, {})
    output['companion_decision'] = {key: deepcopy(channel[key]) for key in (
        'channel_id', 'run_id', 'target_date', 'shared_snapshot_id', 'calendar_ids_sha256',
        'input_sha256', 'settings_sha256', 'producer', 'producer_report_sha256', 'claim_sha256',
        'canonical_source_card_sha256')}
    output['companion_decision']['authority'] = 'PROTECTED_CONSTRUCTOR_NOT_AGENT_REASONING'
    return output


def validate_companion_cards(report):
    envelope = report.get('companion_ticket_channels')
    _require(isinstance(envelope, dict) and envelope.get('version') == VERSION
             and isinstance(envelope.get('channels'), list)
             and [row.get('channel_id') for row in envelope['channels']] == list(CHANNELS),
             'EXACT_COMPANION_CHANNELS_REQUIRED')
    expected = [card for row in envelope['channels'] if (card := materialize_channel(report, row)) is not None]
    actual = [card for card in report['generated_tickets'] if 'channel_id' in card]
    _require(actual == expected and len({row['ticket_id'] for row in actual}) == len(actual),
             'COMPANION_CANONICAL_COLLECTION_CHANGED')
    return {card['ticket_id']: 'companion:' + card['channel_id'] + ':'
            + card['companion_decision']['canonical_source_card_sha256'] for card in actual}


def refresh_companion_capacity(report):
    from goal_analysis.agents.profile_roster import AGENT_IDS

    generated = report['generated_tickets']
    agents = [card for card in generated if 'channel_id' not in card]
    companions = [card for card in generated if 'channel_id' in card]
    present = {card['profile_id'] for card in agents} & set(AGENT_IDS)
    channels = {card['channel_id'] for card in companions} & set(CHANNELS)
    _require(len(agents) <= 30 and len(companions) <= 2, 'SEPARATE_COMPANION_CAPACITY_EXCEEDED')
    filled = len(present) + len(channels)
    capacity = report['generation_capacity']
    capacity.update(version='TWELVE_MAIN_SLOTS_V1', contract='TWELVE_MAIN_SLOTS_V1', minimum=12, maximum=32, main_slot_count=12,
        filled_agent_owner_slots=len(present), filled_companion_slots=len(channels), target_met=filled == 12,
        filled_main_slots=filled, shortfall=12 - filled, status='IN_TARGET' if filled == 12 else 'BELOW_TARGET',
        agent_variant_count=len(agents), companion_ticket_count=len(companions),
        generated_ticket_count=len(generated), maximum_agent_variants=30, maximum_companion_tickets=2,
        missing_agent_ids=[key for key in AGENT_IDS if key not in present],
        missing_channel_ids=[key for key in CHANNELS if key not in channels])
    outcomes = report['ten_agent_decisions']
    report['construction_status'] = 'COMPLETE' if filled == 12 and outcomes.get('status') == 'COMPLETE' and all(
        row.get('status') == 'DECISION_COMPLETED' for row in outcomes.get('owners', [])) else 'PARTIAL'
    return report


def append_companion_cards(report):
    """Add the two saved products after the unchanged ten-owner finalizer."""
    from goal_analysis.engine.diversification import ticket_signature
    from goal_analysis.generated_tickets import refresh_generation_capacity

    envelope = report['companion_ticket_channels']
    _require(envelope.get('version') == VERSION and [row.get('channel_id') for row in envelope['channels']]
             == list(CHANNELS), 'EXACT_COMPANION_CHANNELS_REQUIRED')
    report['generated_tickets'] = [card for card in report['generated_tickets'] if 'channel_id' not in card]
    for channel in envelope['channels']:
        card = materialize_channel(report, channel)
        if card is not None:
            report['generated_tickets'].append(card)
    validate_companion_cards(report)
    cards = report['generated_tickets']
    envelope['shared_exposure'] = [{'ticket_ids': [left['ticket_id'], right['ticket_id']],
        'status': 'IDENTICAL_EXPOSURE_NOT_INDEPENDENT'} for index, left in enumerate(cards)
        for right in cards[index + 1:] if ('channel_id' in left or 'channel_id' in right)
        and ticket_signature(left) == ticket_signature(right)]
    report['tickets'] = [card for card in cards if card['status'] == 'READY' and card['publication_scope'] == 'LIVE']
    refresh_generation_capacity(report)
    return report


def run_companion_channels(report, *, snapshot, artifacts, enriched, settings, journal):
    """Two exclusive claims followed by one existing protected call per channel."""
    from goal_analysis.engine.portfolio import build_portfolio
    from goal_analysis.jobs.daily_223_live import _build_for_one_bookmaker

    _require('companion_ticket_channels' not in report, 'COMPANIONS_ALREADY_ATTEMPTED')
    data = artifacts['independent_agent_snapshot']
    _require(data['run_id'] == report['run_id'] and data['target_date'] == report['date']
             and digest(data) == snapshot.snapshot_id, 'COMPANION_SHARED_SNAPSHOT_CHANGED')
    candidate_input, history = artifacts['candidate_input'], artifacts['history']
    odds = data['families']['odds_and_price_history']['value']
    _require(isinstance(odds, dict) and odds['candidate_input'] == candidate_input
             and odds['enriched'] == enriched, 'COMPANION_INPUT_NOT_SHARED')
    history_family = data['families']['retained_history']
    history_value = history_family.get('value')
    if history_value is None:
        history_value = data['source_manifest'][history_family['source_refs'][0]]['producer_value']
    _require(history_value['collected_online'] == history, 'COMPANION_HISTORY_NOT_SHARED')
    calendar_ids = list(snapshot.fixture_ids)
    calendar_set = set(calendar_ids)
    _require(calendar_ids == artifacts['calendar']['scope_receipt']['provider_fixture_ids']
             and candidate_input['date'] == enriched['date'] == report['date']
             and all(row['fixture_id'] in calendar_set for row in candidate_input['candidates']),
             'COMPANION_FULL_DAY_INPUT_REQUIRED')
    input_hashes = {'candidate_input': digest(candidate_input), 'history': digest(history),
                    'enriched': digest(enriched), 'settings': digest(settings)}
    bindings = {'run_id': report['run_id'], 'target_date': report['date'],
        'shared_snapshot_id': snapshot.snapshot_id, 'calendar_ids': calendar_ids,
        'calendar_ids_sha256': digest(calendar_ids), 'input_hashes': input_hashes,
        'input_sha256': digest(input_hashes),
        'settings_sha256': digest(settings),
        'input_counts': {'calendar': len(calendar_ids), 'candidates': len(candidate_input['candidates'])},
        'families': deepcopy(snapshot.decision_data()['families']),
        'family_use_claim': 'AVAILABLE_SHARED_INPUT_NOT_ALL_FAMILIES_CAUSALLY_USED',
        'numeric_weather_news_simulator_use': 'NOT_EVALUATED_BY_PROTECTED_CONSTRUCTOR'}
    artifacts['companion_producer_results'] = {}
    channels = []
    for channel in CHANNELS:
        claim = {**bindings, 'channel_id': channel, 'producer': PRODUCERS[channel], 'maximum_attempts': 1}
        journal.write_once(channel + '.companion-claim.json', claim)
        row = {**deepcopy(bindings), 'channel_id': channel, 'claim_sha256': digest(claim),
            'claim': deepcopy(claim), 'producer': deepcopy(PRODUCERS[channel]),
            'producer_report': None, 'selected_source_rows': [],
            'producer_report_sha256': None, 'canonical_source_card': None,
            'canonical_source_card_sha256': None, 'ticket_ids': [],
            'status': 'CONSTRUCTION_INCOMPLETE', 'reason': 'NO_COMPLETE_PROTECTED_PRODUCT'}
        try:
            if channel == CHANNELS[0]:
                produced = build_portfolio(deepcopy(enriched), {**deepcopy(settings),
                    'enabled_profiles': ['daily223']}, allowed_profiles=['daily223'])
                selection = None
            else:
                produced, selection = _build_for_one_bookmaker(deepcopy(candidate_input), deepcopy(history))
            artifacts['companion_producer_results'][channel] = {
                'report': deepcopy(produced), 'bookmaker_selection': deepcopy(selection)}
            row['producer_report'] = deepcopy(produced)
            row['producer_report_sha256'] = digest(produced)
            _require(produced.get('report_sha256') == digest({key: value for key, value in produced.items()
                        if key != 'report_sha256'}), 'INVALID_PROTECTED_PRODUCER_HASH')
            card = _source_card(channel, produced, candidate_input, report['run_id'])
            if card is not None:
                chosen = {leg['candidate_id'] for leg in card['legs']}
                row['selected_source_rows'] = [deepcopy(raw) for raw in candidate_input['candidates']
                                               if raw['candidate_id'] in chosen]
                row.update(status='COMPLETE', reason=None, canonical_source_card=card,
                           canonical_source_card_sha256=digest(card), ticket_ids=[card['ticket_id']])
                materialize_channel(report, row)
        except Exception as error:  # noqa: BLE001 - a failed optional channel never discards other owners.
            row.update(status='FAILED_NO_RETRY', reason='PROTECTED_CONSTRUCTOR_OR_BINDING_FAILED',
                       error_type=type(error).__name__, canonical_source_card=None,
                       canonical_source_card_sha256=None, ticket_ids=[])
        journal.write_once(channel + '.companion-result.json', row)
        channels.append(row)
    report['companion_ticket_channels'] = {'version': VERSION, 'channels': channels,
        'constructor_attempts': 2, 'new_agent_sessions': 0, 'new_model_or_provider_calls': 0,
        'independent_math_strategies_claimed': False, 'real_wager_placed': False}
    return append_companion_cards(report)
