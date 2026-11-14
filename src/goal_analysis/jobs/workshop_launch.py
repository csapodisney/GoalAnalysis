"""Durable manual/morning once claims; no process, credentials or APP DB access."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from goal_analysis.workshop_views import success_identity_coverage

BERLIN = ZoneInfo('Europe/Berlin')
TASK_NAME = 'Arthur Ten Agents Morning'
CONTRACT = 'TEN_AGENT_WITH_COMPANIONS_FULL_DAY_V2'
ACTIVE = ('RESERVED', 'RUNNING', 'VERIFYING')
TERMINAL = ('SUCCEEDED', 'PARTIAL', 'FAILED', 'STOPPED')
PUBLIC_JOB_FIELDS = (
    'job_id', 'request_id', 'target_date', 'trigger', 'slot_id', 'state', 'stage',
    'created_at', 'started_at', 'finished_at', 'run_id', 'native_exit', 'tree_stopped',
    'verification_state', 'generated_ticket_count', 'target_met', 'capacity', 'reason_code', 'no_retry',
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def aware(now):
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    return now.astimezone(UTC)


def request_identity(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('CANONICAL_REQUEST_UUID_REQUIRED')
    return value


def target_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('EXACT_TARGET_DATE_REQUIRED')
    return value


def morning_slot(now):
    local = aware(now).astimezone(BERLIN)
    start = datetime.combine(local.date(), time(8), BERLIN)
    end = datetime.combine(local.date(), time(8, 15), BERLIN)
    return {'slot_id': CONTRACT + ':' + local.date().isoformat(),
            'target_date': local.date().isoformat(),
            'state': 'WAITING' if local < start else 'OPEN' if local <= end else 'MISSED',
            'start': start.isoformat(), 'end': end.isoformat()}


def schedule_view(saved, now, last_slot=None):
    local = aware(now).astimezone(BERLIN)
    following = datetime.combine(local.date(), time(8), BERLIN)
    if local >= following:
        following = datetime.combine(local.date() + timedelta(days=1), time(8), BERLIN)
    value = dict(saved or {})
    enabled = value.get('enabled') if type(value.get('enabled')) is bool else None
    slot = morning_slot(now)
    if slot['state'] == 'MISSED' and (not last_slot or last_slot.get('slot_id') != slot['slot_id']):
        slot['state'] = 'MISSED_NO_AUTOMATIC_CATCHUP'
    return {'task_name': TASK_NAME, 'enabled': enabled, 'current_slot': slot, 'timezone': 'Europe/Berlin',
            'local_time': '08:00', 'latest_start_local_time': '08:15',
            'required_session': 'INTERACTIVE_USER_LOGGED_IN', 'wake_to_run': False, 'no_catchup': True,
            'target_date_policy': 'SAME_BERLIN_DAY',
            'next_scheduled_at_utc': following.astimezone(UTC).isoformat() if enabled else None,
            'next_scheduled_at_local': following.isoformat() if enabled else None,
            'revision': value.get('revision'), 'last_slot': last_slot,
            'legacy_tasks': value.get('legacy_tasks', []),
            'observation_status': value.get('observation_status', 'UNKNOWN_NOT_OBSERVED')}


class LaunchConflict(RuntimeError):
    """A conflict is not a retry instruction."""


class LaunchStore:
    """A separate launch database; constructors and GET methods have no writes."""

    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _read(self):
        if not self.path.exists():
            yield None
            return
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, job_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS morning_slots(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
            yield db
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    @staticmethod
    def _job(db, job_id):
        row = db.execute('SELECT document FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('JOB_NOT_FOUND')
        return json.loads(row[0])

    @staticmethod
    def public(value):
        return {key: value.get(key) for key in PUBLIC_JOB_FIELDS}

    def get(self, job_id, *, private=False):
        request_identity(job_id)
        with self._read() as db:
            if db is None:
                raise KeyError('JOB_NOT_FOUND')
            result = self._job(db, job_id)
        return result if private else self.public(result)

    def latest(self, day=None):
        if day is not None:
            target_day(day)
        with self._read() as db:
            if db is None:
                return None
            # Active state is global, so a manual/schedule conflict stays visible across dates.
            rows = db.execute("SELECT document FROM jobs ORDER BY CASE WHEN state IN ('RESERVED','RUNNING','VERIFYING') THEN 0 ELSE 1 END,rowid DESC")
            for row in rows:
                value = json.loads(row[0])
                if value['state'] in ACTIVE or day is None or value['target_date'] == day:
                    return self.public(value)
        return None

    def by_request(self, request_id):
        request_identity(request_id)
        with self._read() as db:
            if db is None:
                raise KeyError('REQUEST_NOT_FOUND')
            row = db.execute('SELECT job_id FROM requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise KeyError('REQUEST_NOT_FOUND')
            return {'request_id': request_id, 'job': self.public(self._job(db, row[0]))}

    def schedule(self, now):
        with self._read() as db:
            saved = last = None
            if db is not None:
                row = db.execute('SELECT document FROM schedule WHERE id=1').fetchone()
                saved = json.loads(row[0]) if row else None
                row = db.execute('SELECT document FROM morning_slots ORDER BY rowid DESC LIMIT 1').fetchone()
                last = json.loads(row[0]) if row else None
        return schedule_view(saved, now, last)

    def reserve(self, *, request_id, day, trigger, settings, now):
        request_identity(request_id)
        target_day(day)
        now = aware(now)
        if trigger not in {'MANUAL', 'MORNING'}:
            raise ValueError('EXACT_TRIGGER_REQUIRED')
        slot = morning_slot(now) if trigger == 'MORNING' else None
        if slot and day != slot['target_date']:
            raise ValueError('MORNING_TARGET_MUST_BE_BERLIN_TODAY')
        payload = {'target_date': day, 'trigger': trigger, 'contract': CONTRACT,
                   'settings_sha256': digest(settings)}
        with self._write() as db:
            existing = db.execute('SELECT payload_sha256,job_id FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing[0] != digest(payload):
                    raise LaunchConflict('REQUEST_ID_PAYLOAD_CONFLICT')
                return self.public(self._job(db, existing[1])), True
            if slot:
                previous = db.execute('SELECT document FROM morning_slots WHERE id=?', (slot['slot_id'],)).fetchone()
                if previous:
                    value = json.loads(previous[0])
                    if value.get('job_id'):
                        job = self._job(db, value['job_id'])
                        db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, digest(payload), job['job_id']))
                        return self.public(job), True
                    return value, True
                if slot['state'] != 'OPEN':
                    if slot['state'] == 'MISSED':
                        db.execute('INSERT INTO morning_slots VALUES(?,?)', (slot['slot_id'], encoded(slot)))
                    return slot, False
            if slot:
                completed = db.execute("SELECT document FROM jobs WHERE state='SUCCEEDED' ORDER BY rowid DESC").fetchall()
                for (document,) in completed:
                    accepted = json.loads(document)
                    if (accepted['target_date'] == day and accepted.get('verification_state') == 'VERIFIED'
                            and success_identity_coverage(accepted)):
                        db.execute('INSERT INTO morning_slots VALUES(?,?)', (slot['slot_id'], encoded({
                            **slot, 'state': 'CONSUMED_BY_VERIFIED_SHARED_JOB', 'job_id': accepted['job_id']})))
                        db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, digest(payload), accepted['job_id']))
                        return self.public(accepted), True
            active = db.execute("SELECT document FROM jobs WHERE state IN ('RESERVED','RUNNING','VERIFYING') LIMIT 1").fetchone()
            if active:
                job = json.loads(active[0])
                reused = True
            else:
                job = {'job_id': str(uuid4()), 'request_id': request_id, 'target_date': day,
                       'trigger': trigger, 'slot_id': slot['slot_id'] if slot else None,
                       'state': 'RESERVED', 'stage': 'RESERVED', 'created_at': now.isoformat(),
                       'settings': json.loads(encoded(settings)), 'settings_sha256': digest(settings),
                       'contract': CONTRACT, 'no_retry': True, 'verification_state': 'NOT_STARTED',
                       'native_exit': None, 'tree_stopped': None, 'generated_ticket_count': None,
                       'target_met': None, 'capacity': None, 'reason_code': None}
                db.execute('INSERT INTO jobs VALUES(?,?,?)', (job['job_id'], job['state'], encoded(job)))
                reused = False
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, digest(payload), job['job_id']))
            if slot:
                db.execute('INSERT INTO morning_slots VALUES(?,?)', (slot['slot_id'], encoded({**slot, 'state': 'CLAIMED', 'job_id': job['job_id']})))
        return self.public(job), reused

    def transition(self, job_id, *, expected, values):
        """Worker CAS; an old/missing outcome cannot be rewritten into native0."""
        request_identity(job_id)
        if expected not in ACTIVE or values.get('state') not in (*ACTIVE, *TERMINAL):
            raise ValueError('INVALID_JOB_TRANSITION')
        allowed = set(PUBLIC_JOB_FIELDS) - {'job_id', 'request_id', 'target_date', 'trigger', 'slot_id', 'created_at', 'no_retry'}
        if set(values) - allowed:
            raise ValueError('UNKNOWN_JOB_RESULT_FIELD')
        with self._write() as db:
            job = self._job(db, job_id)
            if job['state'] != expected:
                raise LaunchConflict('JOB_ALREADY_CLAIMED_OR_COMPLETED')
            result = {**job, **values}
            if result['state'] in TERMINAL and result.get('tree_stopped') is not True:
                raise ValueError('UNKNOWN_OR_ACTIVE_TREE_CANNOT_RELEASE_GLOBAL_CLAIM')
            if result['state'] == 'SUCCEEDED' and not (
                result.get('native_exit') == 0 and type(result.get('native_exit')) is int
                and result.get('tree_stopped') is True and result.get('verification_state') == 'VERIFIED'
                and success_identity_coverage(result)
                and result.get('target_met') is True
            ):
                raise ValueError('SUCCESS_REQUIRES_NATIVE_TREE_VERIFICATION_AND_ALL_TWELVE_IDENTITIES')
            db.execute('UPDATE jobs SET state=?,document=? WHERE id=?', (result['state'], encoded(result), job_id))
        return self.public(result)

    @staticmethod
    def _validate_schedule(verified):
        if verified.get('task_name') != TASK_NAME or type(verified.get('enabled')) is not bool:
            raise ValueError('EXACT_VERIFIED_MORNING_TASK_REQUIRED')
        if verified.get('observation_status') != 'VERIFIED_FIXED_DEFINITION':
            raise ValueError('ACTUAL_TASK_READBACK_REQUIRED')
        if not verified.get('legacy_tasks') or any(row.get('enabled') is not False for row in verified['legacy_tasks']):
            raise ValueError('OBSERVED_LEGACY_OFF_REQUIRED')

    def change_schedule(self, expected_revision, enabled, adapter, now):
        """One cross-process lock spans durable intent, OS readback and final commit."""
        from goal_analysis.jobs.portfolio import RunLock

        if type(enabled) is not bool:
            raise ValueError('BOOLEAN_SCHEDULE_REQUIRED')
        with RunLock(self.path.with_suffix('.schedule.lock')):
            with self._write() as db:
                row = db.execute('SELECT document FROM schedule WHERE id=1').fetchone()
                old = json.loads(row[0]) if row else {}
                if old.get('revision') != expected_revision:
                    raise LaunchConflict('SCHEDULE_REVISION_CONFLICT')
                pending = {'task_name': TASK_NAME, 'enabled': None, 'desired_enabled': enabled,
                    'observation_status': 'PENDING_OS_CHANGE', 'changed_at': aware(now).isoformat(),
                    'previous_revision': expected_revision, 'operation_id': str(uuid4())}
                pending['revision'] = digest(pending)
                db.execute('INSERT OR REPLACE INTO schedule VALUES(1,?)', (encoded(pending),))
            try:
                verified = adapter(enabled)
                self._validate_schedule(verified)
                if verified['enabled'] is not enabled:
                    raise ValueError('OS_READBACK_DOES_NOT_MATCH_REQUEST')
                final = {**verified, 'operation_id': pending['operation_id'],
                         'previous_revision': expected_revision}
            except Exception as error:  # noqa: BLE001 - Persist unknown OS outcome; never retry the durable claim.
                final = {**pending, 'observation_status': 'UNKNOWN_OS_CHANGE_OUTCOME',
                    'enabled': None, 'error_type': type(error).__name__}
            final.pop('revision', None)
            final['revision'] = digest(final)
            with self._write() as db:
                row = json.loads(db.execute('SELECT document FROM schedule WHERE id=1').fetchone()[0])
                if row['revision'] != pending['revision']:
                    raise LaunchConflict('SCHEDULE_PENDING_REVISION_CHANGED')
                db.execute('UPDATE schedule SET document=? WHERE id=1', (encoded(final),))
            return final
