"""Real local receipt/canonical pipeline; synthetic football HTTP fixtures only."""
import json
import socket
import subprocess
from copy import deepcopy
from datetime import timedelta

import pytest
from test_player_collection_shadow import NOW, collect, setup

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.grok_packet_tables import expand_tables
from goal_analysis.jobs import daily_native_shadow
from goal_analysis.jobs import grok_player_handoff_shadow as module
from goal_analysis.normalization.canonical import utc


def environment(root, monkeypatch):
    data, _, calls, _ = setup(root, monkeypatch)
    for f in data['fixtures']:
        f['config_league_id'] = '3'
    collect(root, data)
    calls.clear()
    legs = []
    for fixture, candidate in zip(data['fixtures'], data['candidates'], strict=True):
        legs.append({**candidate, 'home_team': fixture['home_team'], 'away_team': fixture['away_team'],
                     'kickoff': fixture['kickoff'], 'model_probability': .6})
    report = {'run_id': 'player-handoff', 'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
              'fixtures': deepcopy(data['fixtures']), 'recommendations': [
                  {'profile_id': 'tipster_szines_reka', 'status': 'READY', 'legs': legs}]}
    return data, report, calls


def prepare(root, data, report, *, now=NOW):
    return module.prepare(root, report, data['fixtures'], as_of=now)


def test_all_selected_player_facts_survive_real_validator_and_lossless_transport(tmp_path, monkeypatch):
    data, report, calls = environment(tmp_path, monkeypatch)
    before = deepcopy((data, report))
    monkeypatch.setattr(socket.socket, 'connect', lambda *_: pytest.fail('NO_NETWORK'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_k: pytest.fail('NO_LLM_OR_WORKER'))
    result = prepare(tmp_path, data, report)
    assert result['accepted_player_facts'] == {'injuries_suspensions': 6, 'squad': 3, 'player_season_stats': 3}
    assert result['schema_valid'] and result['fresh_price_receipts']
    assert expand_tables(result['wire_packet']) == result['packet']
    assert result['wire_characters'] <= 100000
    for fixture in result['packet']['fixtures']:
        facts = fixture['the_stats_api']['facts']
        assert any(f['category'] == 'lineup' and f['status'] == 'UNAVAILABLE' for f in facts)
        assert 'PLAYER_SEASON_STATS_PARTIAL_OR_UNPROVEN' in fixture['the_stats_api']['missing']
    assert all(utc(row['available_at']) <= NOW for row in result['source_events'])
    assert not calls and (data, report) == before
    assert not result['dispatch_allowed'] and not result['live_decision_effect']
    assert result['production_writes'] == result['llm_requests'] == result['external_requests'] == 0


def test_packet_has_only_current_recommendation_fixtures(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    report['recommendations'][0]['legs'] = report['recommendations'][0]['legs'][:1]
    result = prepare(tmp_path, data, report)
    assert len(result['packet']['fixtures']) == 1
    assert result['accepted_player_facts']['squad'] == result['accepted_player_facts']['player_season_stats'] == 1
    assert {r['fixture_id'] for r in result['source_events']} == {data['fixtures'][0]['fixture_id']}


@pytest.mark.parametrize('key', ['home_team', 'away_team', 'provider_season_id', 'provider_home_team_id', 'kickoff'])
def test_supplied_fixture_mismatch_cannot_cross_link_player_evidence(tmp_path, monkeypatch, key):
    data, report, _ = environment(tmp_path, monkeypatch)
    data['fixtures'][0][key] = (NOW+timedelta(days=2)).isoformat() if key == 'kickoff' else 'wrong'
    with pytest.raises(ValueError, match='TARGET_IDENTITY_MISMATCH'):
        prepare(tmp_path, data, report)


def test_duplicate_fixture_rejected_before_canonical_read(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    data['fixtures'].append(deepcopy(data['fixtures'][0]))
    with pytest.raises(ValueError, match='DUPLICATE_GROK_PLAYER_TARGET'):
        prepare(tmp_path, data, report)


def test_old_quote_receipt_is_never_refreshed_or_converted_into_quote_time(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    for leg in report['recommendations'][0]['legs']:
        leg['quote_observed_at'] = (NOW-timedelta(hours=2)).isoformat()
    before = deepcopy(report)
    result = prepare(tmp_path, data, report)
    assert not result['fresh_price_receipts'] and not result['dispatch_allowed']
    for quote in result['packet']['quote_sources']:
        assert quote['quoted_at'] is None and utc(quote['quote_observed_at']) == NOW-timedelta(hours=2)
    assert report == before


def test_expired_target_blocks_player_transport_even_if_roster_ttl_remains_valid(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    result = prepare(tmp_path, data, report, now=NOW+timedelta(minutes=31))
    assert not result['accepted_player_facts'] and not result['source_events']
    assert result['target_issues']['STALE_TARGET_METADATA'] == 3
    assert len(result['omissions']) == 3


def test_source_fact_tampering_is_rejected_by_original_byte_reader(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    original = module._contexts
    # Tamper physical bytes, not the downstream reader's result.
    contexts, _, _ = original(tmp_path, {f['fixture_id']: f for f in data['fixtures']}, NOW)
    digest = next(row['source_sha256'] for rows in contexts.values() for row in rows if row['category'] == 'squad')
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(tmp_path/'data/goal_analysis.sqlite3')) as db:
        stored = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (digest,))
        columns = [c[0] for c in stored.description]; row = dict(zip(columns, stored.fetchone(), strict=True))
    path = tmp_path/'data/raw'/row['relative_path']
    path.write_bytes(b'{"data":[]}')
    result = prepare(tmp_path, data, report)
    assert result['accepted_player_facts'].get('squad', 0) < 3
    assert result['context_issues']


def test_absent_target_input_is_explicit_and_does_not_retain_old_player_claims(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    data['fixtures'] = []
    result = prepare(tmp_path, data, report)
    assert len(result['omissions']) == 3 and not result['accepted_player_facts']


def test_size_limit_withholds_packet_instead_of_dropping_player_facts(tmp_path, monkeypatch):
    data, report, _ = environment(tmp_path, monkeypatch)
    before = deepcopy(report)
    monkeypatch.setattr(module, 'wire_packet', lambda packet: {'oversize': 'x'*100001})
    with pytest.raises(OverflowError, match='PACKET_TOO_LARGE'):
        prepare(tmp_path, data, report)
    assert report == before


@pytest.mark.parametrize('fixtures', [None, [None]*1001])
def test_unbounded_or_nonlist_input_is_rejected_before_read(tmp_path, fixtures):
    with pytest.raises(ValueError, match='BOUNDED_GROK_PLAYER_TARGETS_REQUIRED'):
        module.prepare(tmp_path, {}, fixtures, as_of=NOW)


@pytest.mark.parametrize('failure', [False, True])
def test_post_freeze_parent_creates_sidecar_before_worker_and_never_sends(tmp_path, monkeypatch, failure):
    from test_daily_native_shadow import staged

    from goal_analysis.jobs import native_source_admission

    run_id, report, path, _ = staged(tmp_path); before = path.read_bytes(); order = []
    monkeypatch.setattr(native_source_admission, 'admit_history', lambda *_a: {'status': 'NO_DATA'})
    def handoff(*args, **kwargs):
        order.append('handoff')
        assert args[1] == report
        if failure:
            raise ValueError('sensitive-secret')
        return {'status': 'NEXT_REVIEW_INPUT_PREPARED', 'schema_valid': True, 'wire_characters': 1,
            'accepted_player_facts': {'squad': 1}, 'fresh_price_receipts': False,
            'dispatch_allowed': False, 'external_requests': 0, 'llm_requests': 0}
    monkeypatch.setattr(module, 'prepare', handoff)
    def worker(command, **kwargs):
        order.append('worker')
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(daily_native_shadow.subprocess, 'run', worker)
    result = daily_native_shadow.run(tmp_path, run_id, canonical_sha256(report), {},
                                    as_of=NOW, prepare_sources=True)
    assert order == ['handoff', 'worker'] and path.read_bytes() == before
    assert result['next_grok_input']['status'] == ('UNAVAILABLE' if failure else 'NEXT_REVIEW_INPUT_PREPARED')
    assert 'sensitive-secret' not in json.dumps(result)
    output = tmp_path/'data/arthur/native-shadow'/run_id/'next-grok-input.json'
    assert output.exists() is not failure
