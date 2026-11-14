"""Saved workshop reads and explicit dispatch boundary; no legacy run fallback."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.config.portfolio import load_settings
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest, json_text
from goal_analysis.jobs.portfolio import validate_target_day
from goal_analysis.jobs.workshop_launch import LaunchConflict, LaunchStore, target_day
from goal_analysis.operating_policy import current_contract
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.workshop_views import calendar_page, card_page, family_page, workshop

RUN_ID = re.compile(r'\d{4}-\d{2}-\d{2}-[a-zA-Z0-9]{8,64}\Z')


@contextmanager
def readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('SAVED_LEDGER_NOT_FOUND')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


class ReadOnlyLedger(PortfolioLedger):
    """Existing monetary views with no constructor/schema/backfill side effects."""

    def __init__(self, path):
        self.path = Path(path)

    def _connection(self):
        return readonly(self.path)

    def ticket_page(self, day, offset, limit):
        target_day(day)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('BOUNDED_LEDGER_PAGE_REQUIRED')
        with self._connection() as db:
            predicate = "date=? AND (active=1 OR EXISTS(SELECT 1 FROM portfolio_wagers w WHERE w.ticket_id=portfolio_tickets.ticket_id))"
            total = db.execute('SELECT count(*) FROM portfolio_tickets WHERE ' + predicate, (day,)).fetchone()[0]
            if offset > total:
                raise ValueError('PAGE_OFFSET_OUT_OF_RANGE')
            rows = db.execute('SELECT * FROM portfolio_tickets WHERE ' + predicate +
                ' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?', (day, limit, offset)).fetchall()
            items = [self._ticket_view(db, row, monetary_contract='CURRENT_HASH_V2') for row in rows]
        return {'date': day, 'monetary_contract': 'CURRENT_HASH_V2', 'total': total,
                'offset': offset, 'next_offset': offset + len(items) if offset + len(items) < total else None,
                'items': items}



class SavedWorkshop:
    """Latest report and one immutable snapshot cache; no arbitrary source paths."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.ledger = ReadOnlyLedger(self.root / 'data/arthur/ledger.sqlite3')
        self._report_key = None
        self._report_value = None
        self._snapshot_key = None
        self._snapshot_value = None
        self._cache_lock = threading.Lock()

    def report(self, *, day=None, run_id=None):
        if (day is None) == (run_id is None):
            raise ValueError('EXACT_DATE_OR_RUN_ID_REQUIRED')
        if day is not None:
            target_day(day)
        elif not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise ValueError('INVALID_SAVED_RUN_ID')
        with self._cache_lock, readonly(self.ledger.path) as db:
            db.execute('BEGIN')
            try:
                query = ('SELECT run_id,snapshot_hash,length(CAST(snapshot_json AS BLOB)) AS byte_count FROM portfolio_runs WHERE date=? ORDER BY observed_at DESC,rowid DESC LIMIT 1'
                         if day is not None else 'SELECT run_id,snapshot_hash,length(CAST(snapshot_json AS BLOB)) AS byte_count FROM portfolio_runs WHERE run_id=?')
                row = db.execute(query, (day if day is not None else run_id,)).fetchone()
                if row is None:
                    if run_id is not None:
                        raise KeyError('SAVED_RUN_NOT_FOUND')
                    return None
                if row['byte_count'] > 64 * 1024 * 1024:
                    raise ValueError('SAVED_REPORT_EXCEEDS_READ_BOUND')
                stamps = []
                for path in (self.ledger.path, Path(str(self.ledger.path) + '-wal')):
                    stat = path.stat() if path.exists() else None
                    stamps.append(None if stat is None else (stat.st_ino, stat.st_size, stat.st_mtime_ns))
                key = (row['run_id'], row['snapshot_hash'], row['byte_count'], tuple(stamps))
                if key != self._report_key:
                    raw = db.execute('SELECT snapshot_json FROM portfolio_runs WHERE run_id=?', (row['run_id'],)).fetchone()[0]
                    value = json.loads(raw)
                    if digest(value) != row['snapshot_hash']:
                        raise ValueError('SAVED_REPORT_HASH_MISMATCH')
                    if value.get('date') != (day or run_id[:10]) or value.get('run_id') != row['run_id']:
                        raise ValueError('SAVED_REPORT_IDENTITY_MISMATCH')
                    self._report_key, self._report_value = key, value
                # Private immutable-by-contract cached data is consumed by pure projections.
                # This is cache invalidation, not an out-of-band tamper-proof database claim.
                return self._report_value
            finally:
                db.execute('ROLLBACK')

    def snapshot(self, report):
        if not report or not isinstance(report.get('ten_agent_decisions'), dict):
            return None
        run_id = report['run_id']
        if RUN_ID.fullmatch(run_id) is None:
            raise ValueError('INVALID_SAVED_RUN_ID')
        expected = report['ten_agent_decisions']['snapshot_id']
        path = self.root / 'data/arthur/agent-decisions' / run_id / 'shared-data.json'
        if not path.is_file():
            return None
        stat = path.stat()
        if (path.is_symlink() or not path.resolve().is_relative_to(self.root / 'data/arthur/agent-decisions')
                or stat.st_size > 512 * 1024 * 1024):
            raise ValueError('SAVED_SNAPSHOT_READ_BOUND')
        key = (str(path), stat.st_size, stat.st_mtime_ns, expected)
        with self._cache_lock:
            if key != self._snapshot_key:
                with path.open('rb') as stream:
                    raw = stream.read(512 * 1024 * 1024 + 1)
                if len(raw) != stat.st_size or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError('SAVED_SNAPSHOT_PHYSICAL_HASH_MISMATCH')
                value = json.loads(raw)
                if value.get('run_id') != run_id or value.get('target_date') != report['date']:
                    raise ValueError('SAVED_SNAPSHOT_IDENTITY_MISMATCH')
                self._snapshot_value = {**value, 'snapshot_id': expected}
                self._snapshot_key = key
            # Private cache is passed only to pure projections which detach responses.
            return self._snapshot_value

    def states(self, report):
        if not report or 'generated_tickets' not in report:
            return []
        result = []
        for ticket in report['generated_tickets']:
            state = self.ledger.get_ticket(ticket['ticket_id'], monetary_contract='CURRENT_HASH_V2')
            if state is not None:
                result.append(state)
        return result


class WorkshopService:
    """Capabilities remain false until reviewed production adapters are supplied."""

    def __init__(self, root, settings_path, *, repository=None, store=None,
                 dispatcher=None, schedule_adapter=None, route_capabilities=None, clock=lambda: datetime.now(UTC)):
        self.root = Path(root).resolve()
        self.settings_path = Path(settings_path).resolve()
        self.repository = repository or SavedWorkshop(self.root)
        self.store = store or LaunchStore(self.root / 'data/arthur/workshop-launch.sqlite3')
        self.dispatcher = dispatcher
        self.schedule_adapter = schedule_adapter
        self.clock = clock
        self.route_capabilities = dict(route_capabilities or {})

    def capabilities(self):
        return {'manual_generation': self.dispatcher is not None,
                'schedule_mutation': self.dispatcher is not None and self.schedule_adapter is not None,
                **{name: self.route_capabilities.get(name) is True for name in (
                    'team_status', 'settings_mutation', 'financial_recording', 'result_refresh')},
                'explicit_probes': False}

    def view(self, day=None):
        now = self.clock()
        day = day or now.astimezone(ZoneInfo('Europe/Berlin')).date().isoformat()
        target_day(day)
        report = self.repository.report(day=day)
        value = workshop(day=day, report=report, states=self.repository.states(report),
                        snapshot=self.repository.snapshot(report), operation=self.store.latest(day),
                        schedule=self.store.schedule(now), capabilities=self.capabilities(), now=now)
        value['settings'] = load_settings(self.settings_path)
        return value

    def cards(self, run_id, offset=0, limit=10):
        report = self.repository.report(run_id=run_id)
        return card_page(report, self.repository.states(report), offset=offset, limit=limit)

    def calendar(self, run_id, offset=0, limit=100):
        snapshot = self.repository.snapshot(self.repository.report(run_id=run_id))
        if snapshot is None:
            raise KeyError('SAVED_SNAPSHOT_NOT_FOUND')
        return calendar_page(snapshot, offset=offset, limit=limit)

    def evidence(self, run_id, family, fixture_id=None, offset=0, limit=25):
        snapshot = self.repository.snapshot(self.repository.report(run_id=run_id))
        if snapshot is None:
            raise KeyError('SAVED_SNAPSHOT_NOT_FOUND')
        return family_page(snapshot, family, fixture_id=fixture_id, offset=offset, limit=limit)

    def ledger_tickets(self, day, offset=0, limit=25):
        return self.repository.ledger.ticket_page(day, offset, limit)

    def export_family(self, run_id, family):
        if family not in FAMILIES:
            raise ValueError('EXACT_SAVED_FAMILY_REQUIRED')
        snapshot = self.repository.snapshot(self.repository.report(run_id=run_id))
        if snapshot is None:
            raise KeyError('SAVED_SNAPSHOT_NOT_FOUND')
        value = snapshot['families'][family]
        result = {'run_id': run_id, 'snapshot_id': snapshot['snapshot_id'], 'family': family,
                  'as_of': snapshot['as_of'], 'envelope': value,
                  'source_manifest': {key: snapshot['source_manifest'][key] for key in value['source_refs']},
                  'scope': 'EXACT_SAVED_FAMILY_NOT_NEW_COLLECTION'}
        raw = json_text(result).encode('utf-8')
        if len(raw) > 536870912:
            raise ValueError('SAVED_FAMILY_EXPORT_EXCEEDS_BOUND')
        return raw

    def start(self, payload, *, trigger='MANUAL'):
        if self.dispatcher is None:
            raise LaunchConflict('REVIEWED_DURABLE_LAUNCHER_NOT_INSTALLED')
        if not isinstance(payload, dict) or set(payload) - {'date', 'request_id', 'mode'}:
            raise ValueError('FULL_DAY_REQUEST_ONLY_NO_SUBSET_OR_SETTINGS_OVERRIDE')
        if payload.get('mode', 'TEN_AGENT_FULL_DAY') != 'TEN_AGENT_FULL_DAY':
            raise ValueError('FULL_DAY_MODE_REQUIRED')
        day = target_day(payload['date'])
        now = self.clock()
        validate_target_day(date.fromisoformat(day), now)
        settings = load_settings(self.settings_path)
        if not current_contract(settings) or not set(AGENT_IDS) <= set(settings['enabled_profiles']):
            raise ValueError('CURRENT_TEN_OWNER_FULL_DAY_CONTRACT_REQUIRED')
        # The compatibility route remains the same durable adapter. New UI always supplies UUID.
        request_id = payload.get('request_id') or str(uuid4())
        job, reused = self.store.reserve(request_id=request_id, day=day, trigger=trigger,
                                         settings=settings, now=now)
        if not reused and job.get('state') == 'RESERVED':
            try:
                self.dispatcher(job['job_id'])
            except Exception as error:
                # No automatic resubmission: request identity remains durably consumed.
                # Dispatcher must preserve whether process creation might have occurred.
                if getattr(error, 'definitely_not_started', False):
                    job = self.store.transition(job['job_id'], expected='RESERVED', values={
                        'state': 'FAILED', 'stage': 'DISPATCH', 'reason_code': 'DISPATCH_BEFORE_PROCESS_FAILURE',
                        'finished_at': self.clock().isoformat(), 'tree_stopped': True})
                raise LaunchConflict('DISPATCH_UNCERTAIN_READ_EXISTING_JOB_NO_RETRY') from error
        return {'schema_version': 1, 'started': job.get('state') in ('RESERVED', 'RUNNING', 'VERIFYING'),
                'operation': 'run', 'date': job.get('target_date', day), 'job_id': job.get('job_id'),
                'request_id': request_id, 'state': job['state'], 'reused': reused}

    def morning(self):
        # This explicit scheduler entry is not invoked by GET, dashboard start or polling.
        from goal_analysis.jobs.workshop_launch import morning_slot
        slot = morning_slot(self.clock())
        schedule = self.store.schedule(self.clock())
        if schedule['enabled'] is not True or schedule['observation_status'] != 'VERIFIED_FIXED_DEFINITION':
            raise LaunchConflict('MORNING_TASK_NOT_VERIFIED_ENABLED')
        return self.start({'date': slot['target_date'], 'request_id': str(uuid4()),
                           'mode': 'TEN_AGENT_FULL_DAY'}, trigger='MORNING')

    def update_schedule(self, payload):
        if self.schedule_adapter is None or self.dispatcher is None:
            raise LaunchConflict('REVIEWED_FIXED_TASK_ADAPTER_NOT_INSTALLED')
        if set(payload) != {'enabled', 'expected_revision'} or type(payload['enabled']) is not bool:
            raise ValueError('EXACT_FIXED_SCHEDULE_REQUEST_REQUIRED')
        # Adapter owns one exclusive cross-process revision lock around OS change + readback.
        # It must not call the production launcher as a registration test.
        return self.store.change_schedule(payload['expected_revision'], payload['enabled'],
            self.schedule_adapter, self.clock())


def production_workshop(root, settings_path, *, route_capabilities=None):
    """Explicit installed composition; construction and GET never create jobs/tasks."""
    from goal_analysis.jobs.workshop_dispatch import ProductionDispatcher
    from goal_analysis.jobs.workshop_schedule import FixedMorningTask

    return WorkshopService(root, settings_path, dispatcher=ProductionDispatcher(root),
        schedule_adapter=FixedMorningTask(root), route_capabilities=route_capabilities)
