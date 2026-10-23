"""Bounded post-freeze player-source acquisition; no decisions or refits."""
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.calendar_collection_shadow import _bootstrap_allowed as calendar_bootstrap
from goal_analysis.jobs.fixture_contexts import link_report
from goal_analysis.jobs.player_specialist_shadow import _contexts, _signals
from goal_analysis.jobs.the_stats_context import VERSION as CONTEXT_VERSION
from goal_analysis.jobs.the_stats_context import Resources, _collect_fact
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage.provider_quota import SharedQuota

VERSION = 'active-absence-player-collection-shadow-v1'
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_requests': 20, 'max_seconds': 45}
QUOTA = {'max_requests': 20, 'max_quota_age_seconds': 3600, 'monthly_reserve': 65000,
         'daily_reserve': 2500, 'minute_reserve': 30, 'daily_cap': 400, 'per_minute_cap': 20}


def enabled(root):
    path = Path(root)/'config/player-collection-shadow.json'
    return path.is_file() and path.stat().st_size < 1000 and json.loads(path.read_bytes()) == POLICY


def _bootstrap_allowed(quota, now):
    # Reuse the existing one-necessary-GET rule and retain the larger research
    # reserve as well. Unknown/denied/stale quota never authorizes a bootstrap.
    if not calendar_bootstrap(quota, now):
        return False
    with closing(sqlite3.connect(quota.path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        month = quota._dimension(db, 'monthly_remaining')
        usage = json.loads(month['usage_json'])
        reserve = max(QUOTA['monthly_reserve'],
                      math.ceil((usage['monthly_reset']-now.timestamp())/86400)*QUOTA['daily_reserve'])
        return usage['monthly_remaining'] == -1 or (
            usage['monthly_remaining']-quota._debit(db, month) >= reserve+POLICY['max_requests'])


def _record(fixture):
    source = fixture['fixture_source']
    mapping_source = {'provider': 'the_stats_api', 'content_hash': source['content_hash'],
        'source_url': source['source_url'], 'observed_at': source['observed_at'],
        'resource_type': 'daily_context', 'published_at': None}
    for key in ('http_observation_id', 'http_receipt_id', 'http_observation_status'):
        if key in source:
            mapping_source[key] = source[key]
    return {'fixture_id': fixture['fixture_id'], 'kickoff': fixture['kickoff'],
        'status': 'MATCHED', 'facts': [], 'missing': [],
        'mapping': {'method': 'EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE', 'source': mapping_source,
            **{name: fixture['provider_'+field] for name, field in (
                ('match_id', 'fixture_id'), ('competition_id', 'league_id'), ('season_id', 'season_id'),
                ('home_team_id', 'home_team_id'), ('away_team_id', 'away_team_id'))}}}


def _plan(fixtures, contexts):
    # Deduplicate exact team/player identities; do not infer a role, importance,
    # OUT status or XI. The existing consumer alone decides whether facts qualify.
    queues = []
    for index, fixture in enumerate(sorted(fixtures, key=lambda f: (utc(f['kickoff']), f['fixture_id']))):
        rows = contexts.get(fixture['fixture_id'], [])
        teams = (fixture['provider_home_team_id'], fixture['provider_away_team_id'])
        for team in teams if index % 2 == 0 else reversed(teams):
            absent = sorted({absence['player_id'] for row in rows
                if row['category'] == 'injuries_suspensions' and row['payload']['team_id'] == team
                for kind in ('injuries', 'suspensions') for absence in row['payload'].get(kind, [])
                if absence.get('active') is True and isinstance(absence.get('player_id'), str)})
            if absent:
                queues.append([(fixture, 'squad', team, team)] + [
                    (fixture, 'player_season_stats', player, team) for player in absent[:2]])
    return [queue[index] for index in range(3) for queue in queues if index < len(queue)]


def collect(root, fixtures, *, clock=None, max_requests=20, max_seconds=45):
    """Only source-bound active absences; complete cache prepass before GETs.

    Targets must already have fresh genuine receipts. Never refresh old odds,
    acquire a new XI or create a shared numerical fit. New context is appended
    with its actual new availability, never attached to an old frozen report.
    """
    if (not isinstance(fixtures, list) or len(fixtures) > 1000
            or type(max_requests) is not int or not 0 <= max_requests <= 20
            or type(max_seconds) is not int or not 1 <= max_seconds <= 45):
        raise ValueError('INVALID_PLAYER_COLLECTION_BUDGET')
    root = Path(root).resolve(); clock = clock or (lambda: datetime.now(UTC))
    before = canonical_sha256(fixtures); started = utc(clock()); timer = monotonic()
    by_id = {}
    for fixture in fixtures:
        if fixture['fixture_id'] in by_id:
            raise ValueError('DUPLICATE_PLAYER_COLLECTION_TARGET')
        by_id[fixture['fixture_id']] = fixture
    targets, _, issues = _targets(root, by_id, started)
    accepted = [by_id[fid] for fid in targets if utc(by_id[fid]['kickoff']) > started+timedelta(minutes=15)]
    contexts, context_issues, _ = _contexts(root, {f['fixture_id']: f for f in accepted}, started) if accepted else ({}, {}, 0)
    plan = _plan(accepted, contexts)
    records = {f['fixture_id']: _record(f) for f in accepted}
    for fid, rows in contexts.items():
        for row in rows:
            if row['category'] in {'squad', 'player_season_stats'}:
                records[fid]['facts'].append({'category': row['category'],
                    'subject_id': row['payload'].get('player_id') if row['category'] == 'player_season_stats'
                                  else row['payload']['team_id'], 'status': 'AVAILABLE',
                    'data': row['payload'], 'reason': None,
                    'source': {'provider': 'the_stats_api', 'content_hash': row['source_sha256'],
                        'source_url': row['source_url'], 'observed_at': row['observed_at'],
                        'resource_type': row['category'], 'published_at': None}})
    quota = SharedQuota(root, kind='daily', policy=QUOTA, clock=clock)
    client = TheStatsPrimaryClient(root, max_requests=max(1, max_requests), timeout=3,
                                   clock=clock, quota_observer=quota) if plan else None
    resources = Resources(root, client, clock, max_requests) if plan else None
    done = set(); outcomes = []; bootstrapped = False; bootstrap_requests = 0
    for cache_only in (True, False):
        for index, (fixture, category, subject, team) in enumerate(plan):
            if index in done:
                continue
            now = utc(clock()); status = None
            if now < started or monotonic()-timer >= max_seconds-6:
                status = 'COLLECTION_TIME_LIMIT'
            elif utc(fixture['kickoff']) <= now+timedelta(minutes=15):
                status = 'PREMATCH_LOCKOUT'
            record = records[fixture['fixture_id']]
            if category == 'player_season_stats':
                roster = next((fact for fact in record['facts'] if fact['category'] == 'squad'
                    and fact['subject_id'] == team and fact['status'] == 'AVAILABLE'), None)
                if roster is None or roster['data']['truncated'] or subject not in {
                        player['player_id'] for player in roster['data']['players']}:
                    if cache_only:
                        continue
                    status = status or 'MISSING_EXACT_PLAYER_IN_ROSTER'
            bootstrap = False
            if status is None and any(fact['category'] == category and fact['subject_id'] == subject
                    and fact['status'] == 'AVAILABLE' for fact in record['facts']):
                status = 'REUSED_AVAILABLE_CONTEXT'
            if status is None and not cache_only:
                if resources.usage['requests'] >= max_requests or resources.usage['stopped_reason']:
                    status = resources.usage['stopped_reason'] or 'REQUEST_BUDGET'
                else:
                    allowance = quota.allowance()
                    if (allowance['status'] == 'QUOTA_REFRESH_REQUIRED' and not bootstrapped
                            and _bootstrap_allowed(quota, now)):
                        client.quota_observer = SharedQuota(root, kind='daily', clock=clock)
                        bootstrap = bootstrapped = True
                    elif allowance['requests'] > 0:
                        client.quota_observer = quota
                    else:
                        status = allowance['status']
            if status is None:
                previous = client.authenticated_request_count
                complete = _collect_fact(resources, record, category, subject, cache_only=cache_only)
                if bootstrap:
                    bootstrap_requests += client.authenticated_request_count-previous
                if not complete:
                    continue
                fact = record['facts'][-1]
                status = 'AVAILABLE' if fact['status'] == 'AVAILABLE' else fact['reason']
            done.add(index)
            outcomes.append({'fixture_id': fixture['fixture_id'], 'category': category,
                             'subject_id': subject, 'status': status})
    completed = utc(clock())
    publishable_fixtures = [fixture for fixture in accepted
        if utc(fixture['kickoff']) > completed+timedelta(minutes=15)
        and timedelta(0) <= completed-utc(fixture['fixture_source']['observed_at']) <= timedelta(minutes=30)]
    retained = {fixture['fixture_id']: records[fixture['fixture_id']] for fixture in publishable_fixtures}
    collection = {'version': CONTEXT_VERSION, 'provider': 'the_stats_api',
        'observed_at': completed.isoformat(), 'context_by_fixture': retained}
    linking = link_report(root, {'run_id': 'shadow-player-sources-'+canonical_sha256(
        [started.isoformat(), before])[:24], 'fixtures': publishable_fixtures,
        'sports_supplement': {'the_stats_api': collection}}, clock=clock,
        include_saved_context=False) if plan and retained else {}
    after_contexts, after_issues, _ = _contexts(root,
        {f['fixture_id']: f for f in publishable_fixtures}, utc(clock())) if plan and retained else ({}, {}, 0)
    views = {f['fixture_id']: _signals(f, after_contexts.get(f['fixture_id'], [])) for f in publishable_fixtures}
    if before != canonical_sha256(fixtures):
        raise ValueError('PLAYER_COLLECTION_INPUT_MUTATED')
    return {'version': VERSION, 'mode': 'SHADOW', 'status': 'OBSERVED' if plan else 'NO_ELIGIBLE_ABSENCE_TARGETS',
        'started_at': started.isoformat(), 'completed_at': utc(clock()).isoformat(),
        'seconds': monotonic()-timer, 'input_fixtures': len(fixtures), 'verified_targets': len(accepted),
        'target_issues': issues, 'context_issues_before': context_issues, 'context_issues_after': after_issues,
        'planned_jobs': len(plan), 'records': list(retained.values()), 'outcomes': outcomes,
        'canonical_linking': linking, 'accepted_context_categories': dict(Counter(
            row['category'] for rows in after_contexts.values() for row in rows)),
        'supported_fixture_count': sum(bool(view['signals']) for view in views.values()),
        'fixture_evidence': views, 'max_individual_stats_per_absence_team': 2,
        'usage': resources.usage if resources else {'requests': 0, 'cache_hits': 0, 'max_requests': max_requests},
        'external_requests': client.request_count if client else 0,
        'authenticated_requests': client.authenticated_request_count if client else 0,
        'quota_bootstrap_requests': bootstrap_requests, 'live_decision_effect': False,
        'llm_requests': 0, 'model_fits': 0, 'ledger_writes': 0, 'probability_adjustment': 0}
