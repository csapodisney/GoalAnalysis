"""TESTONLY full-calendar news integration; fake CLI, no network/DB/model calls."""
import json
import socket
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.web_research import TOPICS
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)
END = NOW + timedelta(seconds=2)
URL = 'https://example.org/official/team-news'
UNOBSERVED = 'https://example.org/model-invented-news'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY no network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def fixtures():
    return [{'fixture_id': f'TESTONLY:{index}', 'home_team': f'Home {index}',
             'away_team': f'Away {index}', 'kickoff': (NOW + timedelta(hours=7)).isoformat()}
            for index in range(73)]


class NewsCli:
    def __init__(self, searches=1):
        self.searches, self.prompts = searches, []

    def __call__(self, command, *, prompt, cwd, timeout):
        if command[-1] == '--version':
            return subprocess.CompletedProcess(command, 0, 'codex-cli 0.155.1', '')
        if command[-2:] == ['login', 'status']:
            return subprocess.CompletedProcess(command, 0, '', 'Logged in using ChatGPT')
        assert 'exec' in command and 0 < timeout <= 300
        self.prompts.append(prompt)
        answer = {'notes': 'TESTONLY quoted team news, not verified facts.',
            'sources': [{'url': URL, 'title': 'Observed native fixture source'},
                        {'url': UNOBSERVED, 'title': 'Model-only source'}], 'odds_quotes': [],
            'fixture_context': [
                {'fixture_id': 'TESTONLY:72', 'topic': 'lineup', 'status': 'SOURCED',
                 'summary': 'TESTONLY late fixture remains in the common news scope.',
                 'source_urls': [URL], 'published_at': '2020-01-01T00:00:00Z'},
                {'fixture_id': 'TESTONLY:0', 'topic': 'absences', 'status': 'SOURCED',
                 'summary': 'This model-only URL must not establish evidence.', 'source_urls': [UNOBSERVED]},
                {'fixture_id': 'FOREIGN', 'topic': 'coach', 'status': 'SOURCED',
                 'summary': 'Foreign fixture must not establish evidence.', 'source_urls': [URL]}]}
        Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(answer), encoding='utf-8')
        events = [{'type': 'item.completed', 'item': {'type': 'web_search', 'id': f'search-{number}',
                   'results': [{'url': URL, 'title': 'Actual fixture search output'}]}}
                  for number in range(self.searches)]
        events.append({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 20,
                       'cached_input_tokens': 0, 'reasoning_output_tokens': 3}})
        return subprocess.CompletedProcess(command, 0, '\n'.join(map(json.dumps, events)), '')


def reviewer(tmp_path, cli, *, enabled=True):
    return CodexReviewer(command=['TESTONLY-codex'], runner=cli, cache_dir=tmp_path / 'cache',
                         web_search=enabled)


def test_all_73_fixture_news_uses_actual_native_urls_and_preserves_unknown_publication(tmp_path):
    rows, cli = fixtures(), NewsCli()
    original = deepcopy(rows)
    clocks = iter([NOW, END])
    result = reviewer(tmp_path, cli).research_fixtures(rows, NOW.date().isoformat(), clock=lambda: next(clocks))
    assert rows == original and len(cli.prompts) == 1
    supplied = json.loads(cli.prompts[0].split('INPUT DATA:\n', 1)[1])
    assert supplied['fixtures'] == original and 'candidates' not in supplied
    assert result['fixture_ids'] == [row['fixture_id'] for row in original]
    assert len(result['coverage']) == 73 * len(TOPICS)
    assert len(result['envelopes']) == 1 and result['envelopes'][0]['fixture_id'] == 'TESTONLY:72'
    envelope = result['envelopes'][0]
    assert envelope['source_urls'] == [URL] and envelope['published_at'] is None
    assert envelope['content_status'] == 'UNVERIFIED' and envelope['PIT_certified'] is False
    assert envelope['observed_at'] == END.isoformat()
    assert result['source_result_sha256'] == digest(result['source_result'])
    assert result['source_result']['transport_evidence']['native_exit_code'] == 0
    assert result['source_result']['transport_evidence']['completed_search_ids'] == ['search-0']
    assert result['usage']['requests'] == 1 and result['usage']['input_tokens'] == 100
    assert result['all_fixtures_independently_researched'] is False
    assert sum(row['status'] == 'UNKNOWN' for row in result['coverage']) == 73 * len(TOPICS) - 1


@pytest.mark.parametrize('enabled,searches', [(False, 1), (True, 0), (True, 5)])
def test_disabled_or_unobserved_news_is_unknown_and_search_overrun_is_visible(tmp_path, enabled, searches):
    cli = NewsCli(searches)
    result = reviewer(tmp_path, cli, enabled=enabled).research_fixtures(
        fixtures(), NOW.date().isoformat(), clock=lambda: NOW)
    assert len(cli.prompts) == int(enabled)
    assert result['limits']['search_cap_enforced'] is False
    if not enabled or not searches:
        assert result['status'] == 'UNKNOWN' and result['envelopes'] == []
        assert all(row['status'] == 'UNKNOWN' for row in result['coverage'])
    else:
        assert result['usage']['web_search_calls'] == 5
        assert result['budget_gap'] == 'ACTUAL_NATIVE_SEARCH_COUNT_EXCEEDED_ADVISORY_FOUR'


def normal_frame():
    rows = fixtures()
    artifacts = {'calendar': {'scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
        'date': NOW.date().isoformat(), 'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE',
        'routing_status': 'COMPLETE', 'provider_fixture_ids': [row['fixture_id'] for row in rows],
        'events': deepcopy(rows)}}, 'fixtures': {'records': deepcopy(rows)},
        'candidate_input': {'candidates': []}, 'numeric_models': {}}
    return {'run_id': 'TESTONLY-news-before-owners', 'target_date': NOW.date(), 'as_of': NOW,
        'artifacts': artifacts, 'fixture_records': rows, 'enriched': {'candidates': []},
        'club_enriched': {'candidates': []}, 'candidate_context': {}, 'weather_by_fixture': {},
        'historical_runs': [], 'feedback': {}, 'settings': {'operating_contract_version': 2,
            'enabled_profiles': list(AGENT_IDS), 'target_ticket_count': 15, 'max_ticket_count': 30,
            'stake_eur': 1.0}, 'team_plan': {}}


def abstaining_host(calls):
    def host(request, *, timeout):
        assert timeout > 0
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        data = request['data']
        if request['phase'] == 'PLAN':
            reply = {**identity, 'options': [], 'fixture_reviews': [
                {'fixture_id': key, 'reason': 'TESTONLY no supplied price, honest abstention.', 'rejected': True}
                for key in data['snapshot']['fixture_ids']],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
        else:
            reply = {**identity, 'option_ids': [], 'reason': 'TESTONLY no eligible own product; no filler.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY missing simulation remains unknown.'} for row in data['tool_results']],
                'family_uses': [{'family': key, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                    'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                    'value_sha256': family['value_sha256'], 'option_ids': [],
                    'reason': 'TESTONLY evidence read declaration, not causal proof.', 'risk': 'Unknown remains a gap.'}
                    for key, family in data['snapshot']['families'].items()]}
        reply['host_usage'] = {'requests': 1, 'usage_complete': False, 'cost_amount': None}
        return reply
    return host


def test_shared_news_precedes_all_ten_owners_and_is_not_repeated_for_same_run(tmp_path):
    frame, calls, cli = normal_frame(), [], NewsCli()
    clock_values = iter([NOW, NOW, END, END])
    def clock():
        return next(clock_values, END)
    def producer(*, fixtures, target_date):
        assert calls == []
        return reviewer(tmp_path, cli).research_fixtures(fixtures, target_date, clock=clock)
    executor = NormalAgentExecutor(host=abstaining_host(calls), journal_root=tmp_path / 'journal',
        clock=clock, news_producer=producer)
    report = executor(**frame)
    assert len(cli.prompts) == 1 and len(calls) == 20
    assert {row['agent_id'] for row in calls} == set(AGENT_IDS)
    assert report['ten_agent_decisions']['status'] == 'COMPLETE'
    assert report['generated_tickets'] == []
    snapshot = frame['artifacts']['independent_agent_snapshot']
    assert snapshot['as_of'] == END.isoformat() and len(snapshot['fixture_ids']) == 73
    news = snapshot['families']['sourced_news']
    assert news['status'] == 'AVAILABLE' and len(news['value']['envelopes']) == 1
    assert len(news['coverage']['by_fixture_topic']) == 73 * len(TOPICS)
    assert all(len(row['data']['snapshot']['fixture_ids']) == 73 for row in calls if row['phase'] == 'PLAN')
    assert all(row['data']['snapshot']['families']['sourced_news']['value_sha256'] == news['value_sha256']
               for row in calls)
    assert report['usage']['openai']['requests'] == 21 and report['usage']['llm_calls'] == 21
    assert report['usage']['openai']['owner_request_budget'] == 20
    assert report['usage']['openai']['shared_news_request_budget'] == 1
    with pytest.raises(FileExistsError):
        executor(**frame)
    assert len(cli.prompts) == 1 and len(calls) == 20


def test_failed_optional_news_keeps_all_owners_and_explicit_unknown_coverage(tmp_path):
    frame, calls, attempted = normal_frame(), [], []
    def producer(**kwargs):
        attempted.append(kwargs)
        raise RuntimeError('TESTONLY optional research failed')
    report = NormalAgentExecutor(host=abstaining_host(calls), journal_root=tmp_path / 'journal',
        clock=lambda: NOW, news_producer=producer)(**frame)
    assert len(attempted) == 1 and len(attempted[0]['fixtures']) == 73
    assert len(calls) == 20 and report['ten_agent_decisions']['status'] == 'COMPLETE'
    news = frame['artifacts']['independent_agent_snapshot']['families']['sourced_news']
    assert news['status'] == 'UNKNOWN' and news['value'] is None
    assert len(news['coverage']['by_fixture_topic']) == 73 * len(TOPICS)
    assert all(row['status'] == 'UNKNOWN' for row in news['coverage']['by_fixture_topic'])
    assert report['fresh_news_research']['reason'] == 'NEWS_RESEARCH_FAILED_NO_RETRY'
    assert report['usage']['openai']['requests'] is None and report['usage']['openai']['known_requests'] == 20
