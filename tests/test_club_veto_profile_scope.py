"""TESTONLY profile ownership through real ranking, packing and review binding."""

import hashlib
import json
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_candidate_context_review import context as decision_context
from test_full_day_portfolio_system import NOW as SYSTEM_NOW
from test_full_day_portfolio_system import cohort
from test_tipster_personas import NOW, enriched, model_results
from test_veto_overrides import NOW as REVIEW_NOW
from test_veto_overrides import proof_input, reviewed

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.astra_review import AstraReviewer, _ReviewError, _wire_json
from goal_analysis.config.portfolio import LEGACY_PROFILE_IDS, PROFILE_IDS
from goal_analysis.engine.veto_overrides import verified_veto_override
from goal_analysis.jobs import portfolio
from goal_analysis.operating_policy import effective_persona
from goal_analysis.tipsters import recommendations as club
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG

A, B = (p['profile_id'] for p in TIPSTER_CATALOG[:2])


@pytest.fixture(autouse=True)
def no_real_boundaries(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('TESTONLY_NO_DB_NETWORK_PROCESS_OR_FIT'))
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield
    forbidden.assert_not_called()


def persona(identifier=A, version=2):
    source = next(p for p in TIPSTER_CATALOG if p['profile_id'] == identifier)
    return effective_persona(source, {'operating_contract_version': version})


def packer():
    result = object.__new__(AstraReviewer)
    result._prompt = lambda _: ''
    return result


def with_wire(packet, envelope=None):
    result = deepcopy(envelope or reviewed(packet))
    wire, _ = packer()._prepare([packet], REVIEW_NOW.date().isoformat(), None)
    result['decision_context_input'] = wire
    result['decision_context_input_sha256'] = hashlib.sha256(json.dumps(
        wire, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return result


def owned_packet():
    row, envelope = proof_input()
    row['veto_override_context'][club.CLUB_GATE_SCOPE] = {'LEAGUE_BASE_RATE': [A]}
    return row, envelope


def valid_pool():
    data, results = enriched(1), model_results(1)
    for result in results.values():
        result['predictions'][0]['probabilities'].update(home=.7, draw=.15, away=.15)
    fixtures = club._fixtures(data['fixtures'], data['date'], NOW)
    rows, _ = club._pool(data, fixtures, club._models(results, fixtures, NOW), NOW)
    return [row for row in rows if row['key'] == 'home']


def test_foreign_and_unscoped_proposals_preserve_own_math_but_own_veto_and_models_hold():
    base = valid_pool()
    original = deepcopy(base)
    expected = club._ranked(base, persona(), NOW)
    assert len(expected) == 1
    for context in (
        {'gates': ['LEAGUE_BASE_RATE'], club.CLUB_GATE_SCOPE: {'LEAGUE_BASE_RATE': [B]}},
        {'gates': ['LEAGUE_BASE_RATE'], 'profile_ids': [B]},
    ):
        rows = deepcopy(base)
        rows[0]['quote']['veto_override_context'] = context
        actual = club._ranked(rows, persona(), NOW)
        assert len(actual) == 1
        for key in ('probability', 'components', 'weights', 'edge', 'score', 'score_terms'):
            assert actual[0][key] == expected[0][key]
    own = deepcopy(base)
    own[0]['quote']['veto_override_context'] = {
        'gates': ['LEAGUE_BASE_RATE'], club.CLUB_GATE_SCOPE: {'LEAGUE_BASE_RATE': [A]}}
    reasons = {}
    assert club._ranked(own, persona(), NOW, rejections=reasons) == []
    assert set(reasons.values()) == {'UNVERIFIED_LEAGUE_RATE_OVERRIDE'}
    missing = deepcopy(base)
    missing[0]['models'] = {}
    reasons = {}
    assert club._ranked(missing, persona(), NOW, rejections=reasons) == []
    assert set(reasons.values()) == {'REQUIRED_MODEL_UNKNOWN'}
    assert base == original


def test_real_review_binding_keeps_gate_owners_and_history_exceptions_separate(monkeypatch):
    row, _ = owned_packet()
    row['veto_override_context'] = club.merge_club_veto_contexts(
        row['veto_override_context'], {'gates': ['HIGH_ODDS'], 'profile_ids': [B],
                                      club.CLUB_GATE_SCOPE: {'HIGH_ODDS': [B]}})
    envelope = with_wire(row, reviewed(row, ['LEAGUE_BASE_RATE', 'HIGH_ODDS']))
    data = {'candidates': [deepcopy(row)]}
    portfolio._attach_veto_reviews(data, [row], envelope, REVIEW_NOW, profile_scope=True)
    attached = data['candidates'][0]
    assert club._profile_override(attached, persona(A), REVIEW_NOW)['gates'] == ['LEAGUE_BASE_RATE']
    assert club._profile_override(attached, persona(B), REVIEW_NOW)['gates'] == ['HIGH_ODDS']
    monkeypatch.setattr(club, '_history_support_details', lambda *args: (6, 7, {'gates': ['LEAGUE_BASE_RATE']}))
    args = ({'quote': attached}, {}, REVIEW_NOW.date().isoformat(), {'operating_contract_version': 2}, REVIEW_NOW)
    assert club._history_option_support(*args, persona=persona(A)) == 6
    assert club._history_option_support(*args, persona=persona(B)) is None
    assert club._history_option_support(*args) is None
    changed = deepcopy(attached)
    changed['veto_override_context'][club.CLUB_GATE_SCOPE]['HIGH_ODDS'] = [A]
    assert club._profile_override(changed, persona(A), REVIEW_NOW) is None
    legacy, legacy_review = proof_input()
    legacy_data = {'candidates': [legacy]}
    portfolio._attach_veto_reviews(legacy_data, [deepcopy(legacy)], legacy_review, REVIEW_NOW)
    assert club._profile_override(legacy, persona(version=1), REVIEW_NOW)['gates'] == ['LEAGUE_BASE_RATE']
    assert club._profile_override(legacy, persona(), REVIEW_NOW) is None


@pytest.mark.parametrize('bad_wire', [None, [], {'candidates': [None]}, {'candidates': [], 'bad': float('nan')}])
def test_corrupt_optional_actual_wire_is_local_unknown_preserving_baseline_review(bad_wire):
    row, envelope = owned_packet()
    envelope = with_wire(row, envelope)
    envelope['decision_context_input'] = bad_wire
    try:
        encoded = json.dumps(bad_wire, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    except ValueError:
        pass  # NaN is deliberately unhashable and must stay local UNKNOWN.
    else:
        envelope['decision_context_input_sha256'] = hashlib.sha256(encoded).hexdigest()
    data = {'candidates': [deepcopy(row)]}
    portfolio._attach_veto_reviews(data, [row], envelope, REVIEW_NOW, profile_scope=True)
    actual = data['candidates'][0]
    assert actual['veto_override_context'] == row['veto_override_context']
    assert actual['astra_veto_review']['status'] == 'COMPLETE'
    assert verified_veto_override(actual, now=REVIEW_NOW)['gates'] == ['LEAGUE_BASE_RATE']
    assert club._profile_override(actual, persona(), REVIEW_NOW) is None
    assert club._profile_override(actual, persona(version=1), REVIEW_NOW) is not None
    good = {'candidates': [deepcopy(row)]}
    portfolio._attach_veto_reviews(good, [row], with_wire(row), REVIEW_NOW, profile_scope=True)
    assert club._profile_override(good['candidates'][0], persona(), REVIEW_NOW) is not None


def test_addition_only_actual_pack_overflow_omits_only_owner_metadata_with_local_gap():
    row, envelope = owned_packet()
    baseline = deepcopy(row)
    baseline['veto_override_context'].pop(club.CLUB_GATE_SCOPE)
    feedback = {'TESTONLY': 'x' * 11887}  # 11903 JSON chars, unchanged feedback limit.
    reviewer = packer()
    baseline['veto_override_context']['TESTONLY_capacity_fact'] = ''
    wire, _ = reviewer._prepare([baseline], REVIEW_NOW.date().isoformat(), feedback)
    padding = 'x' * (180_000 - len(_wire_json(wire).encode()))
    baseline['veto_override_context']['TESTONLY_capacity_fact'] = padding
    full = deepcopy(row)
    full['veto_override_context']['TESTONLY_capacity_fact'] = padding
    before = deepcopy(full)
    reviewer._prepare([baseline], REVIEW_NOW.date().isoformat(), feedback)
    with pytest.raises(_ReviewError, match='exceeded the compact review budget'):
        reviewer._prepare([full], REVIEW_NOW.date().isoformat(), feedback)
    packets, gaps = portfolio._profile_scope_review_packets(
        reviewer, [full], REVIEW_NOW.date().isoformat(), feedback)
    assert packets == [baseline] and gaps == [row['candidate_id']] and full == before
    artifacts, data = {}, {'candidates': [full]}
    portfolio._record_profile_scope_gaps(artifacts, data, gaps)
    assert artifacts['profile_veto_scope_review_gaps'][0]['status'] == 'UNKNOWN_SCOPE_CAPACITY'
    portfolio._attach_veto_reviews(data, packets, with_wire(baseline, envelope), REVIEW_NOW, profile_scope=True)
    assert data['candidates'][0]['veto_override_context'] == before['veto_override_context']
    assert club._profile_override(data['candidates'][0], persona(), REVIEW_NOW) is None


def test_valid_compact_near_cap_wire_keeps_binding_despite_larger_digest_serialization():
    row, envelope = owned_packet()
    current_context = decision_context(row['fixture_id'], sourced=())
    current_context['as_of'] = REVIEW_NOW.isoformat()
    current_context['context_id'] = canonical_sha256({
        key: value for key, value in current_context.items() if key != 'context_id'})
    row['decision_context'] = current_context
    context = row['veto_override_context']
    context['TESTONLY_facts'] = [{'k': 'v', 'n': 1} for _ in range(5000)]
    context['TESTONLY_padding'] = ''
    wire, _ = packer()._prepare([row], REVIEW_NOW.date().isoformat(), None)
    context['TESTONLY_padding'] = 'x' * (360_000 - len(_wire_json(wire).encode()))
    result = with_wire(row, envelope)
    actual = result['decision_context_input']
    assert len(_wire_json(actual).encode()) == 360_000
    assert len(json.dumps(actual, sort_keys=True, ensure_ascii=False).encode()) > 360_000
    bindings = portfolio._actual_club_scope_bindings([row], result)
    assert bindings[row['candidate_id']]['owners'] == {'LEAGUE_BASE_RATE': [A]}


def test_actual_job_all_thirty_keep_full_calendar_when_optional_review_and_models_unknown(monkeypatch):
    config, source, _ = cohort()
    source['candidate_input']['candidates'] = []
    original = deepcopy(source)
    calls = []

    def predictor(fixtures, history, now, **kwargs):
        calls.append(sorted(row['fixture_id'] for row in fixtures))
        return {}

    def unavailable(*args, **kwargs):
        raise RuntimeError('TESTONLY optional reviewer unavailable')

    monkeypatch.setattr(portfolio, 'run_daily223_live', lambda *a, **k: {'artifacts': deepcopy(source)})
    result = portfolio.run_portfolio(config, {'operating_contract_version': 2},
        SimpleNamespace(provider='the_stats_api'), SimpleNamespace(provider='the_stats_api'), None,
        SYSTEM_NOW.date(), reviewer=None, clock=lambda: SYSTEM_NOW, model_predictor=predictor,
        expert_reviewer=unavailable, package_reviewer=unavailable)
    report = result['artifacts']['report']
    expected = sorted(row['fixture_id'] for row in source['fixtures']['records'])
    assert calls == [expected] and len(expected) == 45
    cards = {card['profile_id']: card for card in report['recommendations']}
    core = {card['profile_id']: card for card in report['profiles'] if card['profile_id'] in LEGACY_PROFILE_IDS}
    assert set(cards) == set(PROFILE_IDS) and len(cards) == 30
    for name in PROFILE_IDS:
        trace = (core[name] if name in LEGACY_PROFILE_IDS else cards[name])['selection_diagnostics']
        assert trace['input_fixture_ids'] == expected, name
    assert report['generation_capacity']['generated_count'] == 0
    assert report['portfolio_objectives']['capacity_shortfall'] == 15
    assert source == original
