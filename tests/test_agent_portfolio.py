"""TESTONLY pure construction and canonical count; no DB/provider/fit invocation."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.profile_decision import (
    consider_family,
    consider_fixture,
    finalize_decisions,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.generated_tickets import CORE_MARKETS, finalize_generated
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder, publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import FAMILIES, build_normal_snapshot, digest

NOW = datetime(2026, 11, 12, 10, tzinfo=UTC)
SETTLEMENT = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'
SETTINGS = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
            'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}


def quote(identifier, owner):
    market, selection, period = CORE_MARKETS.get(owner, ('h2h', 'home', 'FULL_TIME'))
    selection = selection or 'home'
    bet = {'h2h': 1, 'totals_2_5': 5, 'btts': 8}[market]
    row = {'candidate_id': f'TESTONLY-source-{identifier}-{market}-{selection}', 'fixture_id': f'api_football:{identifier}',
        'api_football_fixture_id': str(identifier), 'api_football_home_team_id': str(identifier * 2),
        'api_football_away_team_id': str(identifier * 2 + 1), 'source_provider': 'api_football',
        'home_team': f'TESTONLY Home {identifier}', 'away_team': f'TESTONLY Away {identifier}',
        'competition': 'TESTONLY league', 'competition_type': 'LEAGUE', 'provider_status': 'NS',
        'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'event_status': 'SCHEDULED',
        'market_key': market, 'selection_key': selection, 'period': period, 'settlement': SETTLEMENT,
        'decimal_price': 2.0, 'bookmaker': 'test_api_football_8', 'region': 'eu', 'currency': 'EUR',
        'quote_available': True, 'quote_provider': 'api_football', 'provider_market_id': bet,
        'quote_source_id': f'https://v3.football.api-sports.io/odds?fixture={identifier}&bookmaker=8&bet={bet}',
        'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(), 'quoted_at': NOW.isoformat(),
        'evidence': [], 'sensitivity_note': 'TESTONLY historical and lineup support unknown.'}
    if owner == 'tipster_ertek_milan':
        identity_keys = ('fixture_id', 'market_key', 'selection_key', 'period', 'settlement',
                         'bookmaker', 'region', 'currency', 'quote_provider')
        row['odds_movement'] = {'status': 'VERIFIED_RISE', 'baseline_price': 1.75, 'current_price': 2.,
            'baseline_quoted_at': (NOW - timedelta(hours=30)).isoformat(),
            'current_quoted_at': NOW.isoformat(), 'observed_as_of': NOW.isoformat(),
            'identity': {key: row[key] for key in identity_keys}, 'source_run_id': 'TESTONLY-old-price-run',
            'evidence_id': f'TESTONLY-movement-{identifier}', 'source_snapshot_sha256': 'b' * 64,
            'relative_change': 2. / 1.75 - 1}
        row['contrarian_review'] = {'status': 'COMPLETE', 'candidate_id': row['candidate_id'],
            'reviewed_at': NOW.isoformat(), 'price_case': 'TESTONLY original price increased.',
            'math_case': 'TESTONLY unchanged model components exceed existing value floor.',
            'cause_status': 'UNKNOWN', 'cause_source_urls': []}
    return row


def inputs(*, owners=AGENT_IDS, variants=2):
    candidates, proposals, fixtures = [], {}, []
    identifier = 1
    for owner in owners:
        proposals[owner] = []
        for variant in range(variants):
            chosen = [quote(number, owner) for number in range(identifier, identifier + 4)]
            identifier += 4
            candidates.extend(chosen)
            proposals[owner].append({'option_id': f'{owner}-own-{variant}',
                'source_candidate_ids': [row['candidate_id'] for row in chosen],
                'reason': f'TESTONLY own {owner} variant {variant}, distinct observed fixtures.'})
    fields = ('fixture_id', 'api_football_fixture_id', 'api_football_home_team_id',
              'api_football_away_team_id', 'source_provider', 'home_team', 'away_team',
              'competition', 'competition_type', 'provider_status', 'kickoff')
    fixtures = [{key: row[key] for key in fields} for row in candidates]
    models = {}
    for model in ('gauss', 'kepler', 'laplace'):
        values = {'home': .65, 'draw': .2, 'away': .15}
        if model != 'kepler':
            values.update(over25=.65, under25=.35, btts_yes=.65)
        models[model] = {'profile_id': model, 'status': 'READY', 'predictions': [
            {'fixture_id': row['fixture_id'], 'probabilities': deepcopy(values),
             'stability': dict.fromkeys(values, .03), 'model_details': {
                 'cutoff': NOW.isoformat(), 'version': 'TESTONLY-original-model',
                 'history_hash': 'c' * 64, 'training_count': 100}}
            for row in fixtures]}
    ids = [row['fixture_id'] for row in fixtures]
    receipt = {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'date': NOW.date().isoformat(),
        'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE', 'routing_status': 'COMPLETE',
        'full_product_run': True, 'provider_fixture_ids': ids, 'events': deepcopy(fixtures)}
    artifacts = {'calendar': {'scope_receipt': receipt}, 'fixtures': {'records': fixtures},
                 'candidate_input': {'candidates': deepcopy(candidates)}, 'numeric_models': models}
    enriched = {'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
        'candidates': deepcopy(candidates), 'history_analysis': {'candidates': []}}
    club = {**deepcopy(enriched), 'fixtures': deepcopy(fixtures)}
    report = {'run_id': 'TESTONLY-ten-owner-product-run', 'date': NOW.date().isoformat(),
        'observed_at': NOW.isoformat(), 'settings': deepcopy(SETTINGS), 'tickets': [],
        'recommendations': [], 'astra': {'status': 'DISABLED'},
        'financial_policy': {'max_daily_tickets': 5, 'real_wagers_today': 0}}
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    return report, artifacts, enriched, club, snapshot, builder, proposals


def decisions(snapshot, options):
    sessions = start_sessions(snapshot, {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})
    outcomes = []
    for state in sessions:
        own = options.get(state.agent_id, [])
        state = set_options(state, snapshot, own)
        for fixture in snapshot.fixture_ids:
            state = consider_fixture(state, snapshot, fixture, reason='TESTONLY full fixture accounting.')
        for role in ('candidate_scenarios', 'historical_strategy_evaluation'):
            state, request = request_tool(state, snapshot, role)
            payload = request.data()
            result = {key: payload[key] for key in ('request_id', 'agent_id', 'session_id', 'snapshot_id', 'option_hash')}
            result.update(status='UNKNOWN', value=None, own_option_ids=payload['own_option_ids'],
                          reason='TESTONLY explicit missing simulator producer; no simulated result claimed.')
            state = record_tool_result(state, snapshot, request, result,
                                        interpretation='TESTONLY unknown tool risk retained.')
        for family in FAMILIES:
            value = snapshot.data()['families'][family]
            status = {'AVAILABLE': 'AVAILABLE_NOT_RELEVANT', 'UNKNOWN': 'MISSING',
                      'CONFLICTING': 'CONFLICTING', 'STALE': 'STALE'}[value['status']]
            state = consider_family(state, snapshot, family, status=status,
                reason='TESTONLY contract fixture, not actual reasoning consumption.', value_sha256=value['value_sha256'])
        state, final = finalize_decisions(state, snapshot,
            option_ids=[row['option_id'] for row in own], reason='TESTONLY explicit own selections.')
        outcomes.append({'agent_id': state.agent_id, 'session_id': state.session_id,
            'snapshot_id': snapshot.snapshot_id, 'status': 'DECISION_COMPLETED', 'decision': final.data()})
    return {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot.snapshot_id,
            'agent_count': len(AGENT_IDS), 'owners': outcomes, 'status': 'COMPLETE'}


def test_ten_owner_twenty_exact_cards_pass_real_canonical_gates_without_live_promotion():
    report, artifacts, enriched, club, snapshot, builder, proposals = inputs()
    before = deepcopy((artifacts, enriched, club))
    options = {owner: [builder(owner, row, snapshot) for row in proposals[owner]] for owner in AGENT_IDS}
    result = decisions(snapshot, options)
    financial = deepcopy(report['financial_policy'])
    returned = publish_agent_decisions(report, result, SETTINGS, NOW)
    assert returned is report
    assert len(report['generated_tickets']) == 20
    assert {owner: sum(row['profile_id'] == owner for row in report['generated_tickets'])
            for owner in AGENT_IDS} == dict.fromkeys(AGENT_IDS, 2)
    assert report['generation_capacity']['shortfall'] == 0
    assert report['tickets'] == [] and report['financial_policy'] == financial
    assert all(row['status'] == 'DRAFT' and row['stake_eur'] is None and not row['playable']
               for row in report['generated_tickets'])
    assert all(row['agent_decision']['snapshot_id'] == snapshot.snapshot_id for row in report['generated_tickets'])
    assert (artifacts, enriched, club) == before


def test_two_same_owner_variants_remain_two_and_legacy_mode_remains_one():
    report, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    options = [builder('kronikas', row, snapshot) for row in proposals['kronikas']]
    ordinary = deepcopy(report)
    ordinary['recommendations'] = [row['canonical_card'] for row in options]
    finalize_generated(ordinary, SETTINGS)
    assert len(ordinary['generated_tickets']) == 1
    publish_agent_decisions(report, decisions(snapshot, {'kronikas': options}), SETTINGS, NOW)
    assert len(report['generated_tickets']) == 2
    assert report['generation_capacity']['shortfall'] == 13


@pytest.mark.parametrize('defect', ['price', 'probability', 'ready', 'foreign_source', 'wrong_market', 'too_few'])
def test_reasoning_backend_cannot_invent_products_or_relax_own_shape(defect):
    _, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    proposal = deepcopy(proposals['kronikas'][0])
    owner = 'kronikas'
    if defect in {'price', 'probability', 'ready'}:
        proposal[defect] = 99
    elif defect == 'foreign_source':
        proposal['source_candidate_ids'][0] = 'does-not-exist'
    elif defect == 'wrong_market':
        owner = 'merlin'
    else:
        proposal['source_candidate_ids'] = proposal['source_candidate_ids'][:2]
    with pytest.raises(ValueError):
        builder(owner, proposal, snapshot)


def test_changed_normal_enrichment_cannot_bind_to_old_snapshot():
    report, artifacts, enriched, club, snapshot, _, proposals = inputs(owners=('kronikas',))
    enriched['candidates'][0]['decimal_price'] = 99
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    with pytest.raises(ValueError, match='SNAPSHOT_MISMATCH'):
        builder('kronikas', proposals['kronikas'][0], snapshot)


def test_invalid_selected_card_is_not_replaced_by_available_generic_alternative():
    report, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    first, alternative = [builder('kronikas', row, snapshot) for row in proposals['kronikas']]
    run = decisions(snapshot, {'kronikas': [first]})
    report['ten_agent_decisions'] = run
    report['finished_at'] = (NOW + timedelta(hours=7, minutes=50)).isoformat()
    finalize_generated(report, SETTINGS, options={'kronikas': [{'ticket': alternative['canonical_card']}]})
    assert report['generated_tickets'] == []
    assert any(row['reason'] == 'PREMATCH_LOCKOUT' for row in report['generation_capacity']['excluded_profiles'])


def test_tampered_canonical_card_cannot_be_published_as_original_owner_choice():
    report, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    option = builder('kronikas', proposals['kronikas'][0], snapshot)
    run = decisions(snapshot, {'kronikas': [option]})
    run['owners'][0]['decision']['selected_options'][0]['canonical_card']['legs'][0]['decimal_price'] = 4
    with pytest.raises(ValueError, match='UNBOUND_AGENT_CANONICAL_PRODUCT'):
        publish_agent_decisions(report, run, SETTINGS, NOW)


def test_missing_contrarian_evidence_is_local_to_its_owner_without_invented_review():
    report, artifacts, enriched, club, _, _, proposals = inputs(owners=('kronikas', 'tipster_ertek_milan'))
    for rows in (artifacts['candidate_input']['candidates'], enriched['candidates'], club['candidates']):
        for row in rows:
            row.pop('contrarian_review', None)
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    assert builder('kronikas', proposals['kronikas'][0], snapshot)['canonical_card']['combined_price'] == 16
    with pytest.raises(ValueError, match='OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE'):
        builder('tipster_ertek_milan', proposals['tipster_ertek_milan'][0], snapshot)


def test_under15_requires_actual_cautious_full_day_absence_not_backend_claim():
    report, artifacts, enriched, club, _, _, proposals = inputs(owners=('orszem',))
    # First own choice is only 8, but the full provider day still contains 16-price options.
    selected = proposals['orszem'][0]['source_candidate_ids'][:3]
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    with pytest.raises(ValueError, match='CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN'):
        builder('orszem', {'option_id': 'own-cautious', 'source_candidate_ids': selected,
                          'reason': 'TESTONLY backend cannot self-authorize fallback.'}, snapshot)


def test_canonical_hash_is_not_sufficient_without_matching_option_product():
    report, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    option = builder('kronikas', proposals['kronikas'][0], snapshot)
    run = decisions(snapshot, {'kronikas': [option]})
    changed = run['owners'][0]['decision']['selected_options'][0]
    changed['canonical_card']['legs'][0]['decimal_price'] = 4
    changed['canonical_card_sha256'] = digest(changed['canonical_card'])
    with pytest.raises(ValueError, match='UNBOUND_AGENT_CANONICAL_PRODUCT'):
        publish_agent_decisions(report, run, SETTINGS, NOW)


def test_absent_original_provider_quote_clock_cannot_be_invented_by_enrichment():
    report, artifacts, enriched, club, _, _, proposals = inputs(owners=('kronikas',))
    for row in artifacts['candidate_input']['candidates']:
        row.pop('quoted_at')
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    with pytest.raises(ValueError, match='SOURCE_PRODUCT_OR_QUOTE_CHANGED'):
        builder('kronikas', proposals['kronikas'][0], snapshot)


def test_completed_decision_cannot_be_relabelled_as_another_same_day_run():
    report, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',))
    option = builder('kronikas', proposals['kronikas'][0], snapshot)
    run = decisions(snapshot, {'kronikas': [option]})
    report['run_id'] = 'TESTONLY-another-same-day-run'
    with pytest.raises(ValueError, match='UNBOUND_AGENT_CANONICAL_PRODUCT'):
        publish_agent_decisions(report, run, SETTINGS, NOW)


def test_normal_executor_actual_runner_tools_and_bridge_publish_twenty_fixture_cards(tmp_path):
    from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor

    report, artifacts, enriched, club, _, _, _ = inputs()
    combinations = {
        'kronikas': [(1, 2, 3, 4), (5, 6, 7, 8)],
        'ritmusor': [(1, 2, 3, 4), (5, 6, 7, 8)],
        'parharcmester': [(1, 2, 3, 4), (5, 6, 7, 8)],
        'orszem': [(1, 2, 3, 4), (5, 6, 7, 8)],
        'merlin': [(1, 2, 3, 4), (5, 6, 7, 8)],
        'tipster_archiv_nora': [(1, 2, 3, 5), (1, 2, 3, 6)],
        'tipster_forma_soma': [(1, 2, 4, 5), (1, 2, 4, 6)],
        'tipster_ertek_milan': [(1, 3, 4, 5), (1, 3, 4, 6)],
        'tipster_szines_reka': [(2, 3, 4, 5), (2, 3, 4, 6)],
        'tipster_fek_tade': [(1, 2, 5, 6), (1, 3, 5, 6)],
    }
    candidates, proposals = {}, {}
    for owner, variants in combinations.items():
        proposals[owner] = []
        for index, numbers in enumerate(variants):
            chosen = []
            for number in numbers:
                row = quote(number, owner)
                if (row['market_key'], row['selection_key']) == ('h2h', 'home'):
                    row = quote(number, 'tipster_ertek_milan')
                candidates[row['candidate_id']] = row
                chosen.append(row['candidate_id'])
            proposals[owner].append({'option_id': f'{owner}-fixture-{index}',
                'source_candidate_ids': chosen, 'reason': f'TESTONLY own {owner} method variant {index}.'})
    fixtures = artifacts['fixtures']['records'][:8]
    ids = [row['fixture_id'] for row in fixtures]
    artifacts['fixtures']['records'] = fixtures
    receipt = artifacts['calendar']['scope_receipt']
    receipt.update(provider_fixture_ids=ids, events=deepcopy(fixtures))
    artifacts['candidate_input']['candidates'] = list(candidates.values())
    for model in artifacts['numeric_models'].values():
        model['predictions'] = model['predictions'][:8]
    enriched['candidates'] = deepcopy(list(candidates.values()))
    club.update(candidates=deepcopy(list(candidates.values())), fixtures=deepcopy(fixtures))
    observed = []

    def host(request, *, timeout):
        assert 0 < timeout <= 300
        owner, phase = request['agent_id'], request['phase']
        data = request['data']
        view = data['snapshot']
        assert set(view['fixture_ids']) == set(ids)
        assert set(view['families']) == set(FAMILIES)
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        observed.append((owner, phase))
        if phase == 'PLAN':
            return {**identity, 'options': proposals[owner],
                'fixture_reviews': [{'fixture_id': identifier, 'reason': 'TESTONLY own full-day assessment.',
                                     'rejected': False} for identifier in ids],
                'tool_requests': [{'role': role, 'parameters': {'window': None, 'policy_hash': None}}
                    for role in ('candidate_scenarios', 'historical_strategy_evaluation')]}
        selected = [row['option_id'] for row in data['validated_own_options']]
        rows = []
        for family, value in view['families'].items():
            status = {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED', 'UNKNOWN': 'MISSING',
                      'CONFLICTING': 'CONFLICTING', 'STALE': 'STALE'}[value['status']]
            rows.append({'family': family, 'status': status, 'reason': 'TESTONLY own supplied-value interpretation.',
                'value_sha256': value['value_sha256'], 'option_ids': selected,
                'risk': 'TESTONLY evidence is not calibrated or physically authenticated.'})
        history = next(row['result'] for row in data['tool_results']
                       if row['request']['role'] == 'historical_strategy_evaluation')
        assert history['status'] == 'UNKNOWN' and history['value'] is None
        return {**identity, 'option_ids': selected, 'reason': 'TESTONLY own two final choices.',
            'tool_interpretations': [{'request_id': row['request']['request_id'],
                'interpretation': f"TESTONLY actual tool result {row['result']['status']}; limitations retained."}
                for row in data['tool_results']], 'family_uses': rows}

    def news_reader(*, fixture_ids, as_of):
        assert set(fixture_ids) == set(ids) and as_of == NOW
        return [{'source_id': 'TESTONLY:news', 'observed_at': NOW.isoformat(),
                 'statement': 'TESTONLY sourced team availability remains uncertain.'}]

    executor = NormalAgentExecutor(host=host, journal_root=tmp_path / 'owned-journal',
        clock=lambda: NOW, news_reader=news_reader)
    result = executor(run_id=report['run_id'], target_date=NOW.date(), as_of=NOW,
        artifacts=artifacts, fixture_records=fixtures, enriched=enriched, club_enriched=club,
        candidate_context={}, weather_by_fixture={}, historical_runs=[], feedback=None,
        settings=SETTINGS, team_plan={})
    assert set(observed) == {(owner, phase) for owner in AGENT_IDS for phase in ('PLAN', 'FINAL')}
    assert len(observed) == 20
    assert result['ten_agent_decisions']['host_requests_reserved'] == 20
    assert result['ten_agent_decisions']['wagers'] == 0
    assert len(result['generated_tickets']) == 20
    assert result['tickets'] == []
    assert all(row['status'] == 'DRAFT' and row['publication_scope'] == 'RESEARCH'
               and not row['playable'] and row['stake_eur'] is None for row in result['generated_tickets'])
    assert len(list((tmp_path / 'owned-journal' / report['run_id']).glob('*.owner.json'))) == 10
    assert artifacts['independent_agent_snapshot']['fixture_ids'] == ids
