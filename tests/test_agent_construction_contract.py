"""Pure own-ticket contract fixtures; no host inference, provider, DB or fit."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest

from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.engine import portfolio as core
from goal_analysis.generated_tickets import CORE_MARKETS
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    build_agent_option_builder,
)
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.operating_policy import CAUTIOUS_PROFILE_IDS, effective_persona
from goal_analysis.tipsters.catalog import TIPSTER_BY_ID

NOW = datetime(2026, 11, 13, 10, tzinfo=UTC)
SETTINGS = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
            'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}


def _inputs():
    candidates = []
    for identifier in range(1, 81):
        candidates.append({
            'candidate_id': f'TESTONLY-complete-ticket-{identifier}', 'fixture_id': f'api_football:{identifier}',
            'api_football_fixture_id': str(identifier), 'api_football_home_team_id': str(identifier * 2),
            'api_football_away_team_id': str(identifier * 2 + 1), 'source_provider': 'api_football',
            'home_team': f'TESTONLY Home {identifier}', 'away_team': f'TESTONLY Away {identifier}',
            'competition': 'TESTONLY league', 'competition_type': 'LEAGUE', 'provider_status': 'NS',
            'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'event_status': 'SCHEDULED',
            'market_key': 'totals_2_5', 'selection_key': 'over', 'period': 'FULL_TIME',
            'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
            'decimal_price': 2.0, 'bookmaker': 'test_api_football_8', 'region': 'eu', 'currency': 'EUR',
            'quote_available': True, 'quote_provider': 'api_football', 'provider_market_id': 5,
            'quote_source_id': f'https://v3.football.api-sports.io/odds?fixture={identifier}&bookmaker=8&bet=5',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(), 'quoted_at': NOW.isoformat(),
            'evidence': [], 'sensitivity_note': 'TESTONLY missing optional history stays unknown.',
        })
    fields = ('fixture_id', 'api_football_fixture_id', 'api_football_home_team_id',
              'api_football_away_team_id', 'source_provider', 'home_team', 'away_team',
              'competition', 'competition_type', 'provider_status', 'kickoff')
    fixtures = [{key: row[key] for key in fields} for row in candidates]
    receipt = {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'date': NOW.date().isoformat(),
               'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE', 'routing_status': 'COMPLETE',
               'full_product_run': True, 'provider_fixture_ids': [row['fixture_id'] for row in fixtures],
               'events': deepcopy(fixtures)}
    artifacts = {'calendar': {'scope_receipt': receipt}, 'fixtures': {'records': fixtures},
                 'candidate_input': {'candidates': candidates}, 'numeric_models': {}}
    enriched = {'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
                'candidates': deepcopy(candidates), 'history_analysis': {'candidates': []}}
    club = {**deepcopy(enriched), 'fixtures': deepcopy(fixtures)}
    settings = deepcopy(SETTINGS)
    report = {'run_id': 'TESTONLY-complete-ticket-contract', 'date': NOW.date().isoformat(),
              'observed_at': NOW.isoformat(), 'settings': settings, 'tickets': [], 'recommendations': []}
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
                                    snapshot_as_of=NOW.isoformat(), artifacts=artifacts,
                                    enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, settings, NOW)
    return report, artifacts, enriched, club, settings, snapshot, builder


def test_all_ten_contracts_use_exact_existing_core_or_club_policy():
    report, _, _, _, settings, _, builder = _inputs()
    normalized = core._settings(settings)
    for owner in AGENT_IDS:
        actual = builder.construction_contract(owner)
        assert actual['agent_id'] == owner
        assert (actual['run_id'], actual['target_date'], actual['source_as_of']) == (
            report['run_id'], report['date'], NOW.isoformat())
        assert actual['settings_sha256'] == digest(normalized)
        expected = (core._effective_core_contract(owner, normalized) if owner in CORE_MARKETS
                    else effective_persona(TIPSTER_BY_ID[owner], normalized))
        assert actual['own_policy']['effective_policy'] == expected
        assert actual['primary_combined_price'] == {'min': 15.0, 'max': 40.0}
        assert actual['maximum_source_candidate_ids'] == 6
        assert actual['maximum_final_variants'] == 3
        if owner in CORE_MARKETS:
            assert actual['minimum_legs'] == expected['minimum_legs']
            assert actual['maximum_legs'] == expected['maximum_legs']
        else:
            assert actual['minimum_legs'] == expected['min_legs']
            assert actual['maximum_legs'] == min(6, expected['max_legs'])
    assert builder.construction_contract('kronikas')['minimum_legs'] == 3
    assert builder.construction_contract('orszem')['minimum_legs'] == 2
    assert builder.construction_contract('merlin')['own_policy']['required_candidate_metadata'] == {
        'competition_type': 'NONEMPTY_STRING_REQUIRED_BY_EXISTING_CORE_POOL'}


def test_contract_is_detached_from_caller_settings_and_each_returned_copy():
    report, _, _, _, settings, _, builder = _inputs()
    before = builder.construction_contract('tipster_fek_tade')
    returned = builder.construction_contract('tipster_fek_tade')
    returned['primary_combined_price']['min'] = 1
    returned['own_policy']['effective_policy']['model_weights'].clear()
    returned['same_quote_cohort_fields'].clear()
    settings['target_min_odds'] = 1
    report['run_id'] = 'TESTONLY-mutated-caller'
    assert builder.construction_contract('tipster_fek_tade') == before


def test_cautious_exception_is_conditional_and_never_granted_by_contract():
    *_, builder = _inputs()
    for owner in AGENT_IDS:
        actual = builder.construction_contract(owner)
        exception = actual['cautious_exception']
        assert exception['owner_eligible'] == (owner in CAUTIOUS_PROFILE_IDS)
        assert exception['combined_price'] == ({'min': 5.0, 'max': 15.0}
                                                if owner in CAUTIOUS_PROFILE_IDS else None)
        assert exception['authorization'] == 'NOT_GRANTED_BY_THIS_DESCRIPTION'
        assert len(exception['conditions']) == 3
        assert actual['candidate_filter_applied'] is False
        assert actual['fallback_or_reselection_by_application'] is False
        assert actual['ready_or_profit_guarantee'] is False


def test_real_builder_accepts_complete_four_leg_ticket_but_rejects_single_leg():
    _, artifacts, _, _, _, snapshot, builder = _inputs()
    before = deepcopy(artifacts)
    selected = [row['candidate_id'] for row in artifacts['candidate_input']['candidates'][:4]]
    proposal = {'option_id': 'TESTONLY-kronikas-complete', 'source_candidate_ids': selected,
                'reason': 'TESTONLY four independently selected fixtures form one complete accumulator.'}
    option = builder('kronikas', proposal, snapshot)
    card = option['canonical_card']
    assert len(card['legs']) == 4
    assert card['combined_price'] == 16.0
    assert card['status'] == 'DRAFT' and card['playable'] is False
    assert card['agent_product_binding']['source_candidate_ids'] == selected
    with pytest.raises(OwnOptionAdmissionRejected) as raised:
        builder('kronikas', {**proposal, 'option_id': 'TESTONLY-kronikas-single',
                            'source_candidate_ids': selected[:1]}, snapshot)
    assert raised.value.data()['reason'] == 'CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN'
    assert artifacts == before


def test_actual_runner_sends_each_own_contract_and_all_eighty_fixtures_in_plan(tmp_path):
    *_, snapshot, builder = _inputs()
    requests, lock = [], Lock()

    def host(request, *, timeout):
        assert timeout > 0
        with lock:
            requests.append(deepcopy(request))
        raise RuntimeError('TESTONLY_CAPTURE_ONLY_NO_REASONING_OR_FINAL')

    result = ProfileDecisionRunner(
        host=host, tools=object(), option_builder=builder,
        journal=DecisionJournal(tmp_path / 'TESTONLY-contract-plan', snapshot),
        budget=DecisionBudget(10, NOW + timedelta(seconds=60), clock=lambda: NOW),
    ).run(snapshot)
    assert len(requests) == 10
    assert {row['agent_id'] for row in requests} == set(AGENT_IDS)
    for request in requests:
        assert request['phase'] == 'PLAN'
        assert request['data']['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
        assert len(request['data']['snapshot']['fixture_ids']) == 80
        actual = request['data']['own_construction_contract']
        assert actual == builder.construction_contract(request['agent_id'])
        assert actual['option_means'] == 'ONE_COMPLETE_ACCUMULATOR_NOT_ONE_RESEARCH_LEG'
    assert all(row['status'] == 'INCOMPLETE_NO_RETRY' for row in result['owners'])
    assert result['distinct_decision_count'] == 0


def test_foreign_contract_binding_stops_before_any_host_request(tmp_path):
    *_, snapshot, builder = _inputs()
    original = builder.construction_contract
    builder.construction_contract = lambda owner: {**original(owner), 'agent_id': 'TESTONLY-foreign-owner'}
    requests = []

    def host(request, *, timeout):
        requests.append(request)
        raise AssertionError('Foreign contract must not reach any host')

    result = ProfileDecisionRunner(
        host=host, tools=object(), option_builder=builder,
        journal=DecisionJournal(tmp_path / 'TESTONLY-foreign-contract', snapshot),
        budget=DecisionBudget(2, NOW + timedelta(seconds=60), clock=lambda: NOW),
    ).run(snapshot, pilot_agent_id=AGENT_IDS[0])
    assert requests == []
    assert result['owners'][0]['reason'] == 'OWN_CONSTRUCTION_CONTRACT_BINDING_MISMATCH'
    assert result['host_requests_reserved'] == 0
