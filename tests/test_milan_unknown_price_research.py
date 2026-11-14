"""New research-branch boundaries; no DB, provider, model fit or host execution."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.engine.odds_movement import annotate_odds_movement
from goal_analysis.generated_tickets import _invalid, _materialize, _own_shape
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    build_agent_option_builder,
)
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot
from goal_analysis.operating_policy import effective_persona
from goal_analysis.ticket_product_policy import product_contract
from goal_analysis.tipsters import recommendations as club
from goal_analysis.tipsters.catalog import TIPSTER_BY_ID

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
OWNER = 'tipster_ertek_milan'
SETTINGS = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
            'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}


def _inputs(*, unknown, enabled):
    quotes = []
    for number in range(1, 5):
        row = {'candidate_id': f'TESTONLY-milan-price-{number}',
            'fixture_id': f'api_football:{number}', 'api_football_fixture_id': str(number),
            'api_football_home_team_id': str(number * 2), 'api_football_away_team_id': str(number * 2 + 1),
            'source_provider': 'api_football', 'home_team': f'TESTONLY Home {number}',
            'away_team': f'TESTONLY Away {number}', 'competition': 'TESTONLY league',
            'competition_type': 'LEAGUE', 'provider_status': 'NS', 'event_status': 'SCHEDULED',
            'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'market_key': 'h2h',
            'selection_key': 'home', 'period': 'FULL_TIME',
            'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
            'decimal_price': 2.0, 'bookmaker': 'test_api_football_8', 'region': 'eu', 'currency': 'EUR',
            'quote_available': True, 'quote_provider': 'api_football', 'provider_market_id': 1,
            'quote_source_id': f'https://v3.football.api-sports.io/odds?fixture={number}&bookmaker=8&bet=1',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(),
            'quoted_at': NOW.isoformat(), 'evidence': []}
        if not unknown:
            keys = ('fixture_id', 'market_key', 'selection_key', 'period', 'settlement',
                    'bookmaker', 'region', 'currency', 'quote_provider')
            row['odds_movement'] = {'status': 'VERIFIED_RISE', 'baseline_price': 1.75, 'current_price': 2.0,
                'baseline_quoted_at': (NOW - timedelta(hours=30)).isoformat(),
                'current_quoted_at': NOW.isoformat(), 'observed_as_of': NOW.isoformat(),
                'identity': {key: row[key] for key in keys}, 'source_run_id': 'TESTONLY-prior-price',
                'evidence_id': f'TESTONLY-price-rise-{number}', 'source_snapshot_sha256': 'b' * 64,
                'relative_change': 2.0 / 1.75 - 1}
            row['contrarian_review'] = {'status': 'COMPLETE', 'candidate_id': row['candidate_id'],
                'reviewed_at': NOW.isoformat(), 'price_case': 'TESTONLY observed comparable price rise.',
                'math_case': 'TESTONLY original model value calculation.',
                'cause_status': 'UNKNOWN', 'cause_source_urls': []}
        quotes.append(row)
    fields = ('fixture_id', 'api_football_fixture_id', 'api_football_home_team_id',
              'api_football_away_team_id', 'source_provider', 'home_team', 'away_team',
              'competition', 'competition_type', 'provider_status', 'kickoff')
    fixtures = [{key: row[key] for key in fields} for row in quotes]
    fixtures.append({**fixtures[-1], 'fixture_id': 'api_football:999', 'api_football_fixture_id': '999'})
    models = {name: {'profile_id': name, 'status': 'READY', 'predictions': [
        {'fixture_id': row['fixture_id'], 'probabilities': {'home': .65, 'draw': .2, 'away': .15},
         'stability': {'home': .03, 'draw': .03, 'away': .03}, 'model_details': {
             'cutoff': NOW.isoformat(), 'version': 'TESTONLY-existing-model',
             'history_hash': 'c' * 64, 'training_count': 100}}
        for row in fixtures]} for name in ('gauss', 'laplace')}
    receipt = {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'date': NOW.date().isoformat(),
        'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE', 'routing_status': 'COMPLETE',
        'full_product_run': True, 'provider_fixture_ids': [row['fixture_id'] for row in fixtures],
        'events': deepcopy(fixtures)}
    enriched = {'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
                'candidates': deepcopy(quotes), 'history_analysis': {'candidates': []}}
    persona_input = {**deepcopy(enriched), 'fixtures': deepcopy(fixtures),
                     'calendar_universe': deepcopy(fixtures), 'calendar_scope_receipt': deepcopy(receipt)}
    if unknown:
        persona_input = annotate_odds_movement(persona_input, [], NOW)
    artifacts = {'candidate_input': {'candidates': deepcopy(quotes)}, 'numeric_models': models,
                 'fixtures': {'records': fixtures}, 'calendar': {'scope_receipt': receipt}}
    report = {'run_id': 'TESTONLY-milan-new-research', 'date': NOW.date().isoformat(),
              'observed_at': NOW.isoformat(), 'finished_at': NOW.isoformat(),
              'settings': deepcopy(SETTINGS), 'ticket_product_contract': product_contract()}
    if enabled:
        report['milan_price_history_research_contract'] = club.milan_research_contract()
    return report, artifacts, enriched, persona_input


def _ranked_inputs(frame):
    _, artifacts, _, source = frame
    fixtures = club._fixtures(source['fixtures'], source['date'], NOW)
    models = club._models(artifacts['numeric_models'], fixtures, NOW)
    base, count = club._pool(source, fixtures, models, NOW)
    assert count == len(base) == 4
    persona = effective_persona(TIPSTER_BY_ID[OWNER], SETTINGS, ticket_product_contract=product_contract())
    return base, persona


def _builder(frame):
    report, artifacts, enriched, source = frame
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=source)
    builder = build_agent_option_builder(report, artifacts, enriched, source, SETTINGS, NOW)
    proposal = {'option_id': 'TESTONLY-Milan-own-complete-choice',
                'source_candidate_ids': [row['candidate_id'] for row in source['candidates']],
                'reason': 'TESTONLY selected original products; no automatic alternative.'}
    return snapshot, builder, proposal


def test_original_complete_contrarian_rows_math_rank_and_canonical_card_are_unchanged():
    historical = _inputs(unknown=False, enabled=False)
    current = _inputs(unknown=False, enabled=True)
    before = deepcopy(historical)
    base, persona = _ranked_inputs(historical)
    old = club._ranked(base, persona, NOW)
    new = club._ranked(base, persona, NOW, milan_research_policy=club.milan_research_contract())
    assert len(old) == 4 and new == old
    assert all(row['contrarian_analysis']['kind'] == 'CONTRARIAN_VALUE_UNCALIBRATED'
               and 'model_value_research' not in row for row in new)
    first_snapshot, first, proposal = _builder(historical)
    second_snapshot, second, _ = _builder(current)
    assert first_snapshot.snapshot_id == second_snapshot.snapshot_id
    old_card = first(OWNER, proposal, first_snapshot)['canonical_card']
    new_card = second(OWNER, proposal, second_snapshot)['canonical_card']
    assert new_card == old_card and _own_shape(old_card, NOW)
    assert _invalid(new_card, current[0], NOW) is None
    assert 'milan_price_history_research_contract' not in old_card
    assert historical == before


def test_producer_unknown_branch_reaches_real_builder_and_same_canonical_research_guard():
    frame = _inputs(unknown=True, enabled=True)
    base, persona = _ranked_inputs(frame)
    assert club._ranked(base, persona, NOW) == []
    ranked = club._ranked(base, persona, NOW, milan_research_policy=club.milan_research_contract())
    assert len(ranked) == 4
    assert all(row['contrarian_analysis'] is None for row in ranked)
    snapshot, builder, proposal = _builder(frame)
    contract = builder.construction_contract(OWNER)
    assert contract['milan_price_history_research_contract'] == club.milan_research_contract()
    assert 'MODEL_VALUE_RESEARCH_FALLBACK' in contract['research_branch_instruction']
    assert len(snapshot.fixture_ids) == 5
    search = builder.strategy_search(OWNER, snapshot)
    assert search['status'] == 'AVAILABLE' and search['source_candidate_rows'] == 4
    assert len(search['full_fixture_ids']) == 5 and not search['automatic_selection_or_publication']
    option = builder(OWNER, proposal, snapshot)
    card = option['canonical_card']
    assert card['combined_price'] == 16 and len(card['legs']) == 4
    assert _own_shape(card, NOW) and _invalid(card, frame[0], NOW) is None
    assert card['status'] == 'DRAFT' and card['publication_scope'] == 'RESEARCH' and card['playable'] is False
    for leg in card['legs']:
        proof = leg['model_value_research']
        assert proof['kind'] == 'MODEL_VALUE_RESEARCH_FALLBACK'
        assert proof['price_history_status'] == proof['profit_status'] == 'UNKNOWN'
        assert proof['baseline_price'] is proof['relative_change'] is proof['price_change_cause'] is None
        assert proof['model_probability'] == .65 and proof['expected_return'] == 1.3
        assert leg['model_details']['shared_model_probabilities'] == {'gauss': .65, 'laplace': .65}
    saved = _materialize(card, 'ten_agent_decisions', frame[0], {})
    assert saved['status'] == 'DRAFT' and saved['playable'] is False
    absent = deepcopy(frame[0])
    absent.pop('milan_price_history_research_contract')
    with pytest.raises(ValueError, match='INVALID_MILAN_RESEARCH_BINDING'):
        _materialize(card, 'ten_agent_decisions', absent, {})
    promoted = {**deepcopy(card), 'status': 'READY', 'playable': True}
    assert _invalid(promoted, frame[0], NOW) == 'INVALID_MILAN_RESEARCH_BINDING'
    altered = deepcopy(card)
    altered['legs'][0]['model_value_research']['source_movement_sha256'] = '0' * 64
    assert not _own_shape(altered, NOW)
    with pytest.raises(ValueError, match='OWN_STRATEGY_CONSTRUCTION_INVALID'):
        _materialize(altered, 'ten_agent_decisions', frame[0], {})
    historical = _inputs(unknown=True, enabled=False)
    old_snapshot, old_builder, old_proposal = _builder(historical)
    with pytest.raises(OwnOptionAdmissionRejected, match='OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE'):
        old_builder(OWNER, old_proposal, old_snapshot)


def test_known_failed_malformed_source_and_missing_numeric_evidence_never_use_fallback():
    frame = _inputs(unknown=True, enabled=True)
    base, persona = _ranked_inputs(frame)
    snapshot, builder, proposal = _builder(frame)
    card = builder(OWNER, proposal, snapshot)['canonical_card']
    policy = club.milan_research_contract()
    for state in ('NO_SIGNIFICANT_RISE', 'INVALID_QUOTE', 'STALE', 'CONFLICTING', 'VERIFIED_RISE'):
        changed = deepcopy(base)
        changed[0]['quote']['odds_movement']['status'] = state
        assert changed[0]['quote']['candidate_id'] not in {
            row['quote']['candidate_id'] for row in club._ranked(changed, persona, NOW, milan_research_policy=policy)}
        bad_card = deepcopy(card)
        bad_card['legs'][0]['odds_movement']['status'] = state
        assert not _own_shape(bad_card, NOW)
    for defect in ('future', 'naive', 'foreign_identity', 'contradictory_baseline', 'missing_model', 'weak_models'):
        changed = deepcopy(base)
        bad_card = deepcopy(card)
        movement = changed[0]['quote']['odds_movement']
        if defect == 'future':
            movement['observed_as_of'] = (NOW + timedelta(seconds=1)).isoformat()
        elif defect == 'naive':
            movement['observed_as_of'] = NOW.replace(tzinfo=None).isoformat()
        elif defect == 'foreign_identity':
            movement['identity']['bookmaker'] = 'FOREIGN'
        elif defect == 'contradictory_baseline':
            movement['baseline_price'] = 2.1
        elif defect == 'missing_model':
            changed[0]['models'].pop('laplace')
            bad_card['legs'][0]['model_details']['shared_model_probabilities'].pop('laplace')
        else:
            for model in changed[0]['models'].values():
                model['values']['home'] = .5
            bad_card['legs'][0]['model_details']['shared_model_probabilities'] = {'gauss': .5, 'laplace': .5}
            bad_card['legs'][0].update(model_probability=.5, estimated_edge=0.0)
        bad_card['legs'][0]['odds_movement'] = deepcopy(movement)
        assert changed[0]['quote']['candidate_id'] not in {
            row['quote']['candidate_id'] for row in club._ranked(changed, persona, NOW, milan_research_policy=policy)}
        assert not _own_shape(bad_card, NOW)
    complete, complete_persona = _ranked_inputs(_inputs(unknown=False, enabled=True))
    complete[0]['quote'].pop('contrarian_review')
    assert complete[0]['quote']['candidate_id'] not in {
        row['quote']['candidate_id'] for row in club._ranked(complete, complete_persona, NOW,
                                                           milan_research_policy=policy)}
