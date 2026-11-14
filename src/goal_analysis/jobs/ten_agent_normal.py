"""Explicit NORMAL integration of the ten independent supplied-data owners."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from goal_analysis.agents.profile_decision_runner import (
    CodexDecisionHost,
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest, json_text, require
from goal_analysis.jobs.profile_historical_scenarios import build_history_view


def make_normal_agent_executor(root, settings, *, clock, stop_requested=lambda: False):
    """Use the existing saved-login host; this factory itself performs no I/O."""
    from goal_analysis.agents.codex_review import CodexReviewer
    from goal_analysis.jobs.agent_evidence import read_current_news
    from goal_analysis.operating_policy import current_contract

    require(current_contract(settings) and set(AGENT_IDS) <= set(settings['enabled_profiles']),
            'TEN_AGENT_SETTINGS_REQUIRED_BEFORE_ACQUISITION')
    root = Path(root)
    ai = settings['openai']

    def reviewer_factory():
        return CodexReviewer(model=ai['model'], reasoning_effort=ai['reasoning_effort'],
            web_search=False, max_output_tokens=ai['max_output_tokens'],
            cache_dir=root / 'data/arthur/codex-cache')

    def news_producer(*, fixtures, target_date):
        return CodexReviewer(model=ai['model'], reasoning_effort=ai['reasoning_effort'],
            web_search=True, max_output_tokens=ai['max_output_tokens'],
            cache_dir=root / 'data/arthur/codex-cache').research_fixtures(
                fixtures, target_date, clock=clock)

    return NormalAgentExecutor(host=CodexDecisionHost(reviewer_factory),
        journal_root=root / 'data/arthur/agent-decisions', clock=clock,
        stop_requested=stop_requested, enable_source_access=True, enable_companion_channels=True,
        enable_ticket_product_contract=True, enable_grouped_fixture_reviews=True,
        news_reader=lambda **kwargs: read_current_news(root / 'data/goal_analysis.sqlite3', **kwargs),
        news_producer=news_producer if ai.get('web_search') is True else None)


class NormalAgentExecutor:
    """Construction is inert; invocation belongs to a new authorized NORMAL run.

    Optional source readers receive the current full fixture list and cutoff.
    They must return already validated evidence, never perform a fit or replay.
    Missing historical evaluation remains explicit UNKNOWN; it is not a fabricated
    backtest. The ordinary collectors and model run precede this callback.
    """

    def __init__(self, *, host, journal_root, clock, stop_requested=lambda: False,
                 news_reader=None, historical_reader=None, news_producer=None, max_requests=20,
                 timeout_seconds=3600, enable_source_access=False, pilot_agent_id=None,
                 enable_companion_channels=False, enable_ticket_product_contract=False,
                 enable_grouped_fixture_reviews=False):
        require(callable(host) and callable(clock), 'EXPLICIT_HOST_AND_CLOCK_REQUIRED')
        require(type(timeout_seconds) is int and 0 < timeout_seconds <= 3600,
                'FINITE_NORMAL_AGENT_DEADLINE_REQUIRED')
        require(type(max_requests) is int and 0 <= max_requests <= 20,
                'FINITE_NORMAL_AGENT_REQUEST_BUDGET_REQUIRED')
        require(type(enable_source_access) is bool, 'EXPLICIT_SOURCE_ACCESS_MODE_REQUIRED')
        require(pilot_agent_id is None or isinstance(pilot_agent_id, str) and pilot_agent_id in AGENT_IDS,
                'VALID_PILOT_AGENT_REQUIRED')
        self.host, self.clock, self.stop_requested = host, clock, stop_requested
        self.journal_root = Path(journal_root)
        self.news_reader, self.historical_reader = news_reader, historical_reader
        require(news_producer is None or callable(news_producer), 'OPTIONAL_NEWS_PRODUCER_REQUIRED')
        self.news_producer = news_producer
        self.max_requests, self.timeout_seconds = max_requests, timeout_seconds
        self.enable_source_access = enable_source_access
        self.pilot_agent_id = pilot_agent_id
        require(type(enable_companion_channels) is bool, 'EXPLICIT_COMPANION_MODE_REQUIRED')
        self.enable_companion_channels = enable_companion_channels
        require(type(enable_ticket_product_contract) is bool, 'EXPLICIT_TICKET_PRODUCT_MODE_REQUIRED')
        self.enable_ticket_product_contract = enable_ticket_product_contract
        require(type(enable_grouped_fixture_reviews) is bool, 'EXPLICIT_GROUPED_FIXTURE_REVIEW_MODE_REQUIRED')
        self.enable_grouped_fixture_reviews = enable_grouped_fixture_reviews

    @staticmethod
    def _stage_source_access(journal, snapshot):
        """One exact owned bundle, shared by all phase capabilities; no host call."""
        from goal_analysis.agents import profile_source_access, profile_source_stdio
        from goal_analysis.agents.profile_input import decision_view

        bundle = profile_source_access.SourceBundle.from_snapshot(snapshot, decision_view(snapshot))
        owned_root = (journal.directory / 'source-access').resolve()
        require(owned_root.is_relative_to(journal.directory.resolve()), 'SOURCE_ACCESS_ROOT_OUTSIDE_RUN')
        owned_root.mkdir(exist_ok=False)
        bundle_path = owned_root / 'bundle.json'
        checksum, written = hashlib.sha256(), 0
        encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        with bundle_path.open('xb') as stream:
            for part in encoder.iterencode(bundle.as_dict()):
                encoded = part.encode('utf-8')
                written += len(encoded)
                require(written <= 536_870_912, 'SOURCE_BUNDLE_STORAGE_BOUND_EXCEEDED_BEFORE_HOST')
                stream.write(encoded)
                checksum.update(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        from goal_analysis.agents import profile_source_query

        query = Path(profile_source_query.__file__).resolve()
        server, kernel = Path(profile_source_stdio.__file__).resolve(), Path(profile_source_access.__file__).resolve()
        scope_args = {'owned_root': owned_root, 'bundle_path': bundle_path,
            'bundle_sha256': checksum.hexdigest(),
            'python_executable': Path(sys.executable).resolve(), 'server_script': server,
            'server_sha256': hashlib.sha256(server.read_bytes()).hexdigest(),
            'kernel_source': kernel, 'kernel_sha256': hashlib.sha256(kernel.read_bytes()).hexdigest(),
            'query_source': query, 'query_sha256': hashlib.sha256(query.read_bytes()).hexdigest()}
        journal.write_once('source-access-bundle.json', {
            'snapshot_id': snapshot.snapshot_id, 'view_sha256': bundle.view_sha256,
            'bundle_sha256': scope_args['bundle_sha256'],
            'bundle_bytes': written, 'bundle_byte_limit': 536_870_912,
            'access_semantics': 'AVAILABLE_TO_ALL_OWNERS_NOT_AN_ACTUAL_READ_CLAIM'})
        return bundle, scope_args

    @staticmethod
    def _read_optional(reader, *, fixture_ids, as_of):
        if reader is None:
            return [], {'status': 'UNKNOWN', 'reason': 'NO_BOUND_SOURCE_READER'}
        try:
            rows = reader(fixture_ids=tuple(fixture_ids), as_of=as_of)
            require(isinstance(rows, (tuple, list)), 'SOURCE_READER_SEQUENCE_REQUIRED')
            return deepcopy(list(rows)), {'status': 'RETURNED', 'count': len(rows)}
        except Exception as error:  # noqa: BLE001 - optional source errors remain local UNKNOWN.
            return [], {'status': 'UNKNOWN', 'reason': 'SOURCE_READER_FAILED',
                        'error_type': type(error).__name__}

    def _fresh_news(self, *, run_id, target_date, as_of, full_ids, fixtures):
        """One durable business research attempt; failures are local, retries are not."""
        from goal_analysis.agents.web_research import TOPICS

        if self.news_producer is None:
            return None, as_of
        require(len(full_ids) == len(set(full_ids)) == len(fixtures)
                and {row.get('fixture_id') for row in fixtures} == set(full_ids),
                'FULL_CALENDAR_NEWS_INPUT_REQUIRED_BEFORE_HOST')
        directory = self.journal_root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / 'run.claim.json').exists() or (directory / 'snapshot.json').exists():
            raise FileExistsError('EXISTING_OWNER_RUN_CANNOT_RESEARCH_AGAIN')

        def write_once(name, value):
            with (directory / name).open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json_text(value))
                stream.flush()
                os.fsync(stream.fileno())

        started = self.clock()
        require(isinstance(started, datetime) and started.tzinfo is not None and started >= as_of,
                'CURRENT_NEWS_CLOCK_REQUIRED_BEFORE_HOST')
        write_once('fresh-news.claim.json', {'run_id': run_id, 'target_date': target_date.isoformat(),
            'fixture_ids': full_ids, 'fixture_input_sha256': digest(fixtures),
            'started_at': started.isoformat(), 'maximum_attempts': 1, 'owner_request_budget': self.max_requests,
            'separate_shared_research_request_budget': 1})
        unknown = [{'fixture_id': key, 'topic': topic, 'status': 'UNKNOWN'} for key in full_ids for topic in TOPICS]
        try:
            result = deepcopy(self.news_producer(fixtures=deepcopy(fixtures), target_date=target_date.isoformat()))
            require(isinstance(result, dict) and result.get('version') == 'full-day-news-research-v1'
                    and result.get('fixture_ids') == full_ids, 'FULL_DAY_NEWS_PRODUCER_IDENTITY_REQUIRED')
            envelopes, coverage = result.get('envelopes'), result.get('coverage')
            require(isinstance(envelopes, list) and isinstance(coverage, list)
                    and len(coverage) == len(unknown)
                    and {(row['fixture_id'], row['topic']) for row in coverage}
                    == {(row['fixture_id'], row['topic']) for row in unknown}, 'FULL_DAY_NEWS_COVERAGE_REQUIRED')
            if envelopes:
                details = result.get('source_result', {})
                proof = details.get('transport_evidence', {})
                require(result.get('source_result_sha256') == digest(details)
                        and proof.get('native_exit_code') == 0 and proof.get('turn_completed') is True
                        and proof.get('completed_search_ids'), 'COMPLETED_NEWS_TRANSPORT_REQUIRED')
                allowed = set(details.get('native_source_urls', []))
                require(proof.get('native_source_urls_sha256') == digest(sorted(allowed))
                        and coverage == details.get('coverage'), 'NATIVE_NEWS_RESULT_BINDING_REQUIRED')
                observed = datetime.fromisoformat(result['observed_at'])
                require(observed.tzinfo is not None and observed >= started,
                        'ACTUAL_NEWS_OBSERVATION_CLOCK_REQUIRED')
                expected = [{**row, 'version': 'WEB_RESEARCH_SUMMARY',
                    'observed_at': result['observed_at'], 'published_at': None,
                    'representation': 'MODEL_GENERATED_SUMMARY', 'content_status': 'UNVERIFIED',
                    'provenance_status': 'NATIVE_SEARCH_CITATION_MATCHED',
                    'source_result_sha256': result['source_result_sha256'], 'PIT_certified': False}
                    for row in details.get('fixture_context', [])]
                require(envelopes == expected, 'EXACT_VALIDATED_NEWS_ENVELOPES_REQUIRED')
                for row in envelopes:
                    require(row.get('fixture_id') in full_ids and row.get('topic') in TOPICS
                            and row.get('version') == 'WEB_RESEARCH_SUMMARY' and row.get('published_at') is None
                            and row.get('content_status') == 'UNVERIFIED'
                            and row.get('provenance_status') == 'NATIVE_SEARCH_CITATION_MATCHED'
                            and row.get('source_result_sha256') == result['source_result_sha256']
                            and row.get('source_urls') and set(row['source_urls']) <= allowed,
                            'SOURCE_BOUND_NEWS_ENVELOPE_REQUIRED')
        except Exception as error:  # noqa: BLE001 - one failed optional producer is not a global veto.
            result = {'version': 'full-day-news-research-v1', 'status': 'UNKNOWN',
                'fixture_ids': full_ids, 'coverage': unknown, 'envelopes': [],
                'reason': 'NEWS_RESEARCH_FAILED_NO_RETRY', 'error_type': type(error).__name__,
                'usage': {'requests': None, 'usage_complete': False, 'cost_amount': None}}
        finished = self.clock()
        require(isinstance(finished, datetime) and finished.tzinfo is not None and finished >= started,
                'CURRENT_NEWS_COMPLETION_CLOCK_REQUIRED')
        write_once('fresh-news-result.json', {'result': result, 'result_sha256': digest(result),
                                            'finished_at': finished.isoformat()})
        return result, finished

    def __call__(self, *, run_id, target_date, as_of, artifacts, fixture_records,
                 enriched, club_enriched, candidate_context, weather_by_fixture,
                 historical_runs, feedback, settings, team_plan):
        from goal_analysis.jobs.agent_portfolio import (
            build_agent_option_builder,
            publish_agent_decisions,
        )

        require(Path(run_id).name == run_id and run_id not in {'', '.', '..'}, 'LOCAL_RUN_ID_REQUIRED')
        require(set(AGENT_IDS) <= set(settings['enabled_profiles']), 'TEN_AGENT_SETTINGS_REQUIRED')
        require(not self.stop_requested(), 'HUMAN_OR_ACCOUNT_STOP')
        full_ids = artifacts.get('calendar', {}).get('scope_receipt', {}).get('provider_fixture_ids', [])
        fresh_news = None
        if self.news_producer is not None:
            events = artifacts['calendar']['scope_receipt']['events']
            by_id = {row['fixture_id']: row for row in fixture_records}
            by_event = {row['fixture_id']: row for row in events}
            require(len(by_event) == len(events) and set(by_event) == set(full_ids),
                    'FULL_CALENDAR_NEWS_EVENTS_REQUIRED_BEFORE_HOST')
            fixtures = [{**by_event[key], **by_id.get(key, {})} for key in full_ids]
            fresh_news, as_of = self._fresh_news(run_id=run_id, target_date=target_date,
                as_of=as_of, full_ids=full_ids, fixtures=fixtures)
        news, news_status = self._read_optional(self.news_reader, fixture_ids=full_ids, as_of=as_of)
        coverage = None
        if fresh_news is not None:
            news.extend(deepcopy(fresh_news['envelopes']))
            coverage = {'status': fresh_news['status'], 'by_fixture_topic': fresh_news['coverage'],
                'result_sha256': digest(fresh_news), 'research_scope': 'FULL_PROVIDER_DAY',
                'all_fixtures_independently_researched': False,
                'publication_time_inferred': False, 'content_verified': False,
                'budget_gap': fresh_news.get('budget_gap')}
            artifacts['fresh_news_research'] = deepcopy(fresh_news)
        history, history_status = self._read_optional(self.historical_reader, fixture_ids=full_ids, as_of=as_of)
        historical_view = build_history_view(artifacts, as_of=as_of.isoformat())
        snapshot = build_normal_snapshot(run_id=run_id, target_date=target_date.isoformat(),
            snapshot_as_of=as_of.isoformat(), artifacts=artifacts, fixture_records=fixture_records,
            enriched=enriched, club_enriched=club_enriched, candidate_context=candidate_context,
            weather_by_fixture=weather_by_fixture, historical_runs=historical_runs,
            feedback=feedback, news_envelopes=news, historical_view=historical_view,
            **({'news_coverage': coverage} if coverage is not None else {}))
        report = {'schema_version': 3, 'module': 'ARTHUR_PORTFOLIO', 'run_id': run_id,
            'date': target_date.isoformat(), 'observed_at': as_of.isoformat(),
            'finished_at': as_of.isoformat(), 'settings': deepcopy(settings),
            'tickets': [], 'recommendations': [], 'profile_proposals': [], 'preview_tickets': [],
            'profiles': [], 'fixtures': deepcopy(fixture_records), 'diagnostics': [],
            'real_wager_placed': False, 'construction_status': 'AGENT_DECISIONS_PENDING',
            'agent_source_readers': {'news': news_status, 'historical_strategy': history_status,
                'descriptive_recorded_results': {'source': 'ALREADY_COLLECTED_NORMAL_INPUT',
                    'source_view_sha256': historical_view['source_view_sha256'],
                    'admitted_record_count': len(historical_view['records']),
                    'physical_source_validation': 'NOT_PERFORMED', 'profit_or_PIT_claim': False}}}
        if self.enable_ticket_product_contract:
            from goal_analysis.ticket_product_policy import product_contract

            report['ticket_product_contract'] = product_contract()
        journal = DecisionJournal(self.journal_root / run_id, snapshot)
        journal.write_once('shared-data.json', snapshot.data())
        journal.write_once('descriptive-history-input.json', historical_view)
        source_bundle, source_scope_args = (self._stage_source_access(journal, snapshot)
                                           if self.enable_source_access else (None, None))
        tools = ProfileToolService(snapshot, artifacts=artifacts, historical_evaluations=history,
                                   historical_view=historical_view)
        builder = build_agent_option_builder(report, artifacts, enriched, club_enriched, settings, as_of)
        blocked = set(team_plan.get('blocked_profiles', []))

        def owner_host(request, *, timeout):
            require(request['agent_id'] not in blocked, 'OWNER_PAUSED_BY_TEAM_POLICY')
            if self.enable_source_access:
                from goal_analysis.agents.profile_source_host import SourceAccessScope

                binding = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
                scope = SourceAccessScope(**source_scope_args, binding=binding)
                return self.host(request, timeout=timeout, source_access=scope)
            return self.host(request, timeout=timeout)

        result = ProfileDecisionRunner(host=owner_host, tools=tools, option_builder=builder,
            journal=journal, source_bundle=source_bundle,
            grouped_fixture_reviews=self.enable_grouped_fixture_reviews, budget=DecisionBudget(self.max_requests,
                self.clock() + timedelta(seconds=self.timeout_seconds),
                clock=self.clock, stop_requested=self.stop_requested)).run(snapshot,
                    **({'pilot_agent_id': self.pilot_agent_id} if self.pilot_agent_id is not None else {}))
        artifacts['independent_agent_snapshot'] = snapshot.data()
        artifacts['independent_agent_decisions'] = deepcopy(result)
        if result.get('unattempted_agent_ids'):
            report.update(construction_status='PARTIAL', generated_tickets=[],
                          ten_agent_decisions=deepcopy(result), team=deepcopy(team_plan),
                          unattempted_agent_ids=list(result['unattempted_agent_ids']))
            report['diagnostics'].append('A pilot nem adott saját szelvényválasztást; a többi agent nem indult el.')
        else:
            publish_agent_decisions(report, result, settings, self.clock(), team_plan=team_plan)
        usages = [value for owner in result['owners'] for value in owner.get('observed_host_usage', [])]
        owner_usages = deepcopy(usages)
        if fresh_news is not None:
            usages.append(fresh_news.get('usage', {}))
            report['fresh_news_research'] = deepcopy(fresh_news)
        complete_requests = bool(usages) and all(isinstance(value, dict)
            and type(value.get('requests')) is int for value in usages)
        complete_usage = complete_requests and all(value.get('usage_complete') is True for value in usages)
        known_requests = sum(value['requests'] for value in usages if isinstance(value, dict)
                             and type(value.get('requests')) is int)
        usage = {'requests': known_requests if complete_requests else None,
            'known_requests': known_requests, 'host_requests_reserved': result['host_requests_reserved'],
            'cost_amount': None, 'cost_currency': 'USD', 'usage_complete': complete_usage}
        for field in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens', 'reasoning_tokens'):
            known = [value[field] for value in usages if isinstance(value, dict)
                     and type(value.get(field)) is int]
            usage[field] = sum(known) if usages and len(known) == len(usages) else None
        if fresh_news is not None:
            usage.update(owner_phase_usage=owner_usages, shared_news_usage=fresh_news.get('usage', {}),
                         owner_request_budget=self.max_requests, shared_news_request_budget=1,
                         maximum_combined_requests=self.max_requests + 1)
        report['astra'] = {'status': 'COMPLETE' if result['status'] == 'COMPLETE' else 'PARTIAL',
            'mode': 'TEN_INDEPENDENT_OWNER_DECISIONS', 'reviews': [], 'usage': usage,
            'message': 'Tíz külön döntési út; a hiányzó vagy félbeszakadt eredmények agentenként láthatók.',
            'legacy_candidate_review_performed': False}
        report['usage'] = {**deepcopy(artifacts.get('report', {}).get('usage', {})),
                           'openai': usage, 'llm_calls': usage['requests']}
        if result.get('unattempted_agent_ids'):
            report['astra']['message'] = 'Csak a kijelölt pilot futott; a többi kilenc döntési út nem indult el.'
        report['policy'] = deepcopy(settings)
        report['finished_at'] = self.clock().isoformat()
        if self.enable_companion_channels and not result.get('unattempted_agent_ids'):
            from goal_analysis.companion_tickets import run_companion_channels

            require(not self.stop_requested(), 'HUMAN_OR_ACCOUNT_STOP_BEFORE_COMPANIONS')
            run_companion_channels(report, snapshot=snapshot, artifacts=artifacts, enriched=enriched,
                                   settings=settings, journal=journal)
        return report
