"""Explicit existing-run recovery: pure admission before writes; no generation."""
import json
import re
import sqlite3
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.nightly import MAX_ATTEMPTS, STAGES, NightlyWorkflow, schedule_plan
from goal_analysis.normalization.canonical import utc
from goal_analysis.storage.portfolio_ledger import _hash
from goal_analysis.strategy_provenance import strategy_manifest
from goal_analysis.team.routing import validate_plan

VERSION = 'existing-normal-run-recovery-v1'
SHA = re.compile('[0-9a-f]{64}')


def plan_existing_report(report, *, run_id, expected_snapshot_hash,
                         actual_file_hash, expected_file_hash, settings, config,
                         now, max_seconds=1800):
    """Keep the OLD producer identity and a separate CURRENT recovery identity.

    A valid persisted report can outlive export/operational source changes. Its
    old manifest is verified, never rewritten as the current strategy version.
    No decision is recomputed here; the future executor must prohibit generation.
    """
    now = utc(now)
    if not isinstance(run_id, str) or re.fullmatch('[A-Za-z0-9_-]{1,150}', run_id) is None:
        raise ValueError('INVALID_RECOVERY_RUN_ID')
    if (any(not isinstance(v, str) or SHA.fullmatch(v) is None for v in
            (expected_snapshot_hash, actual_file_hash, expected_file_hash))
            or _hash(report) != expected_snapshot_hash or actual_file_hash != expected_file_hash):
        raise ValueError('EXACT_IMMUTABLE_RECOVERY_HASH_REQUIRED')
    if report.get('run_id') != run_id:
        raise ValueError('RECOVERY_RUN_ID_MISMATCH')
    schedule = schedule_plan(now)
    if report.get('date') != now.astimezone(ZoneInfo('Europe/Berlin')).date().isoformat() or report['date'] != schedule['target_date']:
        raise ValueError('RECOVERY_REQUIRES_CURRENT_SLOT_TARGET')
    if type(max_seconds) is not int or not 60 <= max_seconds <= 3600:
        raise ValueError('INVALID_RECOVERY_TIME_BUDGET')
    if report.get('settings') != settings:
        raise ValueError('RECOVERY_SETTINGS_CHANGED')
    manifest = deepcopy(report['strategy_manifest'])
    identity = manifest.pop('manifest_sha256')
    profiles = manifest.pop('profiles')
    if (canonical_sha256(manifest) != identity
            or manifest['code_sha256'] != canonical_sha256(manifest['source_files'])
            or manifest['settings_sha256'] != canonical_sha256(settings)
            or set(profiles) != set(settings['enabled_profiles'])):
        raise ValueError('INVALID_ORIGINAL_GENERATION_MANIFEST')
    if any(profiles[p] != {'version_id': f"{p}-v{manifest['application_version']}-{identity[:16]}"}
           for p in settings['enabled_profiles']):
        raise ValueError('INVALID_ORIGINAL_PROFILE_VERSION_IDENTITIES')
    if (report.get('construction_status') not in {'COMPLETE', 'PARTIAL'}
            or report.get('astra', {}).get('status') != 'COMPLETE'):
        raise ValueError('RECOVERY_REQUIRES_ACTUAL_COMPLETED_PRIMARY_REVIEW')
    observed, finished = utc(report['observed_at']), utc(report['finished_at'])
    if not observed <= finished <= now:
        raise ValueError('INVALID_ORIGINAL_GENERATION_CLOCKS')
    if report.get('report_sha256') != canonical_sha256({k: v for k, v in report.items() if k != 'report_sha256'}):
        raise ValueError('INVALID_EMBEDDED_REPORT_HASH')
    recommendations = report.get('recommendations', [])
    profile_ids = [row['profile_id'] for row in recommendations]
    if len(profile_ids) != len(set(profile_ids)) or set(profile_ids) != set(settings['enabled_profiles']):
        raise ValueError('INCOMPLETE_OR_DUPLICATE_RECOVERY_PROFILE_INVENTORY')
    team = validate_plan(report['team'], settings['enabled_profiles'])
    legs = [leg for row in recommendations for leg in row.get('legs', [])]
    if not legs or not any(t.get('status') == 'READY' and t.get('legs') for t in report.get('tickets', [])):
        raise ValueError('NO_PERSISTED_READY_RECOVERY_TICKET')
    first_kickoff = min(utc(leg['kickoff']) for leg in legs)
    deadline = min(now + timedelta(seconds=max_seconds), first_kickoff - timedelta(minutes=15))
    if (deadline - now).total_seconds() < 60:
        raise ValueError('NO_SAFE_EXISTING_RUN_RECOVERY_WINDOW')
    recovery_id = canonical_sha256([VERSION, run_id, expected_snapshot_hash, expected_file_hash])
    stamp = now.isoformat()
    recovery = {'version': VERSION, 'recovery_id': recovery_id, 'run_id': run_id,
        'source_snapshot_sha256': expected_snapshot_hash, 'source_report_file_sha256': expected_file_hash,
        'created_at': stamp, 'deadline': deadline.isoformat(), 'max_seconds': max_seconds,
        'original_scheduled_deadline': schedule['deadline'], 'first_kickoff': first_kickoff.isoformat(),
        'original_generation_observed_at': report['observed_at'],
        'original_generation_finished_at': report['finished_at'],
        'original_strategy_manifest_sha256': identity,
        'recovery_strategy_manifest_sha256': strategy_manifest(settings)['manifest_sha256'],
        'generation_calls_allowed': 0, 'fresh_generation_or_quote_claim': False}
    stages = [{'name': name, 'status': 'PENDING', 'attempts': 0, 'max_attempts': MAX_ATTEMPTS,
        'last_started_at': None, 'last_finished_at': None, 'next_retry_at': None,
        'reason': None, 'result': {}} for name in STAGES]
    next(stage for stage in stages if stage['name'] == 'generation')['run_id'] = run_id
    return {'job_id': 'nightly-' + report['date'], 'slot_date': schedule['slot_date'],
        'target_date': report['date'], 'scheduled_at': schedule['scheduled_at'], 'deadline': schedule['deadline'],
        'status': 'PENDING', 'created_at': stamp, 'updated_at': stamp, 'next_retry_at': None,
        'completed_at': None, 'settings': deepcopy(settings), 'config': deepcopy(config),
        'team_plan': team, 'manifest_sha256': identity,
        'usage': {'generation_football_calls': 0, 'settlement_football_calls': 0},
        'existing_run_recovery': recovery, 'stages': stages}


def recovery_deadline(job):
    """A separately bounded operator window, never a changed scheduled deadline."""
    value = job['existing_run_recovery']
    created, end = utc(value['created_at']), utc(value['deadline'])
    if (value['version'] != VERSION or value['original_scheduled_deadline'] != job['deadline']
            or value['generation_calls_allowed'] != 0
            or type(value['max_seconds']) is not int or not 60 <= value['max_seconds'] <= 3600
            or not created < end <= created + timedelta(seconds=value['max_seconds'])
            or end > utc(value['first_kickoff']) - timedelta(minutes=15)
            or value['recovery_id'] != canonical_sha256([VERSION, value['run_id'],
                value['source_snapshot_sha256'], value['source_report_file_sha256']])
            or job['manifest_sha256'] != value['original_strategy_manifest_sha256']
            or next(s for s in job['stages'] if s['name'] == 'generation').get('run_id') != value['run_id']):
        raise ValueError('INVALID_EXISTING_RUN_RECOVERY_WINDOW_OR_IDENTITY')
    return end


def inspect_existing(root, *, run_id, expected_snapshot_hash, expected_file_hash, now,
                     max_seconds=1800):
    """RO/CAS admission before any store initialization or stage action."""
    from hashlib import sha256

    from goal_analysis.config.daily_223_live import validate_live_config
    from goal_analysis.config.portfolio import load_settings
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    root = Path(root).resolve()
    if not isinstance(run_id, str) or re.fullmatch('[A-Za-z0-9_-]{1,150}', run_id) is None:
        raise ValueError('INVALID_RECOVERY_RUN_ID')
    path = root / 'data/arthur/ledger.sqlite3'
    archive = root / 'reports/arthur' / run_id / 'report.json'
    if not 0 < archive.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError('RECOVERY_REPORT_FILE_BUDGET')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        row = db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None or row['snapshot_hash'] != expected_snapshot_hash:
            raise ValueError('MISSING_OR_DIFFERENT_IMMUTABLE_RECOVERY_CHECKPOINT')
        report = json.loads(row['snapshot_json'])
        raw = archive.read_bytes()
        if json.loads(raw) != report:
            raise ValueError('RECOVERY_ARCHIVE_AND_LEDGER_DIFFER')
        plan = plan_existing_report(report, run_id=run_id, expected_snapshot_hash=expected_snapshot_hash,
            actual_file_hash=sha256(raw).hexdigest(), expected_file_hash=expected_file_hash,
            settings=load_settings(root / 'config/arthur-settings.json'),
            config=validate_live_config(json.loads((root / 'config/daily223-live.json').read_bytes())),
            now=now, max_seconds=max_seconds)
        chains = PortfolioLedger._freeze_chains(db, report['date'])
        if chains and (len(chains) != 1 or chains[0][-1]['run_id'] != run_id):
            raise ValueError('CONFLICTING_DAILY_FREEZE_PRESERVED')
    return plan


class RecoveryWorkflow(NightlyWorkflow):
    """Reuse is mandatory. A missing persisted checkpoint can never call AI."""
    def _generation_stage(self, job, stage):
        recovery = job['existing_run_recovery']
        report = self._saved_report(recovery['run_id'])
        if (stage['run_id'] != recovery['run_id'] or report is None
                or _hash(report) != recovery['source_snapshot_sha256']):
            raise ValueError('IMMUTABLE_RECOVERY_GENERATION_UNAVAILABLE')
        result, status = super()._generation_stage(job, stage)
        result.update(execution_kind='REUSED_EXISTING_NORMAL_RUN', generation_calls_in_recovery=0,
            original_generation_observed_at=recovery['original_generation_observed_at'],
            original_generation_finished_at=recovery['original_generation_finished_at'],
            original_strategy_manifest_sha256=recovery['original_strategy_manifest_sha256'])
        return result, status

    def _weather_history(self, job):
        return {'status': 'SKIPPED_EXISTING_RUN_RECOVERY_NO_WEATHER_CLAIM', 'requests': 0}

    def _the_stats_history(self, job):
        return {'status': 'DEFERRED_TO_VALIDATED_BACKGROUND_TICK', 'requests': 0}


def recover_existing(root, *, run_id, expected_snapshot_hash, expected_file_hash,
                     now=None, max_seconds=1800, workflow_factory=RecoveryWorkflow):
    """One explicit recovery pass. Normal scheduling and --run-now stay unchanged."""
    from datetime import UTC, datetime

    from goal_analysis.jobs.portfolio import RunLock
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    clock = now or (lambda: datetime.now(UTC))
    root = Path(root).resolve()
    arguments = {'run_id': run_id, 'expected_snapshot_hash': expected_snapshot_hash,
                 'expected_file_hash': expected_file_hash, 'max_seconds': max_seconds}
    inspect_existing(root, now=clock(), **arguments)
    with RunLock(root / 'data/arthur/run.lock'):
        planned = inspect_existing(root, now=clock(), **arguments)
        workflow = workflow_factory(root, clock=clock)
        job = workflow.store.get(planned['target_date'])
        if job is not None:
            if (job.get('existing_run_recovery', {}).get('recovery_id')
                    != planned['existing_run_recovery']['recovery_id']):
                raise ValueError('EXISTING_WORKFLOW_JOB_PRESERVED')
            if job['settings'] != planned['settings'] or job['config'] != planned['config']:
                raise ValueError('RECOVERY_CONFIGURATION_CHANGED')
            if job['status'] in {'COMPLETED', 'COMPLETED_WITH_WARNINGS'}:
                return {'status': 'ALREADY_RECOVERED', 'job': job}
            if (job['existing_run_recovery']['max_seconds'] != max_seconds
                    or job['existing_run_recovery']['recovery_strategy_manifest_sha256']
                    != planned['existing_run_recovery']['recovery_strategy_manifest_sha256']):
                raise ValueError('RECOVERY_BUDGET_OR_EXECUTION_SOURCE_CHANGED')
            if any(s['status'] == 'RUNNING' and s['name'] != 'generation' for s in job['stages']):
                raise ValueError('UNKNOWN_RECOVERY_STAGE_EXECUTION_REQUIRES_REVIEW')
        else:
            job = planned
        if utc(clock()) >= recovery_deadline(job):
            raise ValueError('RECOVERY_WINDOW_EXPIRED')
        # All immutable-source/conflict checks precede the first write-capable boundary.
        workflow.ledger = workflow.ledger or PortfolioLedger(root / 'data/arthur/ledger.sqlite3', now=clock,
                                                             correction_window_hours=168)
        workflow._save(job)
        for name in STAGES:
            if utc(clock()) >= recovery_deadline(job):
                break
            workflow._execute_stage(job, name)
        workflow._summarize(job)
        workflow._save(job)
        return {'status': job['status'], 'job': job}
