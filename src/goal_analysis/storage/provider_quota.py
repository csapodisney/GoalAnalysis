"""Shared credential-free TheStats receipts and conservative request claims.

Claims precede network I/O and survive uncertain transport/process failures.
Only actual authenticated responses supply quota facts. Daily reads record
usage; the optional background policy alone applies reservation gates.
"""
import json
import math
import sqlite3
import stat
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from goal_analysis.normalization.canonical import utc

POLICY_LIMITS = {'max_requests':(1,20), 'max_quota_age_seconds':(60,3600),
    'monthly_reserve':(0,100000), 'daily_reserve':(1,10000), 'minute_reserve':(1,119),
    'daily_cap':(1,2000), 'per_minute_cap':(1,20)}


def validate_policy(policy):
    if (not isinstance(policy,dict) or set(policy) != set(POLICY_LIMITS)
            or any(type(policy[k]) is not int or not low <= policy[k] <= high
                   for k,(low,high) in POLICY_LIMITS.items())):
        raise ValueError('INVALID_BACKGROUND_QUOTA_POLICY')
    return dict(policy)


def activation(root):
    path = Path(root) / 'data/arthur/historical-background/activation.json'
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError('INVALID_BACKGROUND_ACTIVATION') from None
    try:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError
        value = json.loads(path.read_bytes())
        if (not isinstance(value, dict)
                or value.get('version') != 'historical-background-v1'
                or type(value.get('tracking_enabled')) is not bool
                or type(value.get('background_enabled')) is not bool):
            raise ValueError
        utc(value['activated_at'])
    except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError):
        raise ValueError('INVALID_BACKGROUND_ACTIVATION') from None
    return value


class _BlockedQuotaObserver:
    """Keep cache-only clients usable without allowing an untracked request."""

    def begin(self):
        raise ValueError('INVALID_INSTALLED_QUOTA_ACTIVATION')

    def finish(self, identifier, receipt, usage):
        raise ValueError('INVALID_INSTALLED_QUOTA_ACTIVATION')

    def legacy_state(self):
        # No valid activation means no quota evidence. Cached historical pages
        # can still be checked; begin() never permits a fresh untracked request.
        return None


def observer_for(root, *, kind='daily', clock=None):
    # The deployed collector stays on its previous path until validation arms
    # tracking. Synthetic base-client tests never write the real project store.
    try:
        armed = activation(root)
        enabled = armed.get('tracking_enabled') is True
    except (ValueError, OSError, KeyError):
        return _BlockedQuotaObserver()
    if not enabled:
        return None
    if kind == 'history':
        from goal_analysis.jobs.background_history import implementation_hash, policy_at

        policy = policy_at(root)
        if policy is None:
            raise ValueError('BACKGROUND_HISTORY_DISABLED')
        now=utc((clock or (lambda:datetime.now(UTC)))())
        legacy=armed.get('legacy_history_requests',{})
        old=legacy.get('requests',0) if legacy.get('date') == now.date().isoformat() else 0
        return SharedQuota(root,kind=kind,clock=clock,policy=policy['quota'],legacy_requests=old,
                           source_check=lambda:implementation_hash()==armed.get('source_sha256'))
    return SharedQuota(root, kind=kind, clock=clock)


class SharedQuota:
    def __init__(self, root, *, kind='daily', policy=None, legacy_requests=0, clock=None, source_check=None):
        if kind not in {'daily', 'history'} or type(legacy_requests) is not int or legacy_requests < 0:
            raise ValueError('INVALID_QUOTA_SCOPE')
        self.path = Path(root) / 'data/arthur/the-stats-quota.sqlite3'
        self.kind, self.policy, self.legacy_requests = kind, validate_policy(policy) if policy is not None else None, legacy_requests
        self.clock = clock or (lambda: datetime.now(UTC))
        self.source_check = source_check

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS quota_attempts (
            attempt_id TEXT PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS quota_receipts (
            attempt_id TEXT PRIMARY KEY REFERENCES quota_attempts(attempt_id),
            observed_at TEXT NOT NULL, http_status INTEGER NOT NULL, usage_json TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS quota_attempt_time ON quota_attempts(kind,started_at)')
        db.execute('CREATE INDEX IF NOT EXISTS quota_receipt_time ON quota_receipts(observed_at)')
        for table in ('quota_attempts', 'quota_receipts'):
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only quota'); END''')
        return db

    @staticmethod
    def _dimension(db, name):
        # Keep the exact original receipt for each dimension, even when newer
        # authenticated responses omit that header. No limit is synthesized.
        if name not in {'monthly_remaining', 'minute_remaining', 'retry_after', 'http_status'}:
            raise ValueError('UNKNOWN_QUOTA_DIMENSION')
        value = 'r.http_status' if name == 'http_status' else "json_extract(r.usage_json,'$." + name + "')"
        order = 'ASC' if name.endswith('remaining') else 'DESC'
        row = db.execute(f'''SELECT a.*,r.* FROM quota_receipts r JOIN quota_attempts a USING(attempt_id)
            WHERE {value} IS NOT NULL ORDER BY r.observed_at DESC,{value} {order},a.started_at DESC LIMIT 1''').fetchone()
        return dict(row) if row else None

    @staticmethod
    def _debit(db, receipt):
        # Receipt arrival order need not equal server processing order. Count
        # all overlapping/later attempts, excluding the request owning the header.
        return db.execute('''SELECT count(*) FROM quota_attempts a LEFT JOIN quota_receipts r USING(attempt_id)
            WHERE a.attempt_id<>? AND (a.started_at>=? OR r.observed_at>=? OR r.attempt_id IS NULL)''',
            (receipt['attempt_id'], receipt['started_at'], receipt['started_at'])).fetchone()[0]

    def _allowance(self, db, now):
        p = self.policy
        latest = db.execute('SELECT max(started_at) FROM quota_attempts').fetchone()[0]
        if latest and utc(latest) > now:
            return {'status':'CLOCK_MOVED_BACKWARDS', 'requests':0}
        denial = self._dimension(db, 'http_status')
        if denial and denial['http_status'] in {401, 403}:
            return {'status':'PROVIDER_ACCESS_REQUIRES_RECHECK', 'requests':0}
        if denial and denial['http_status'] == 429 and now < utc(denial['observed_at'])+timedelta(seconds=60):
            return {'status':'PROVIDER_RETRY_AFTER','requests':0}
        retry = self._dimension(db, 'retry_after')
        if retry:
            seconds = json.loads(retry['usage_json'])['retry_after']
            if now < utc(retry['observed_at']) + timedelta(seconds=seconds):
                return {'status':'PROVIDER_RETRY_AFTER', 'requests':0}
        month, minute = (self._dimension(db, key) for key in ('monthly_remaining', 'minute_remaining'))
        if month is None or minute is None:
            return {'status':'UNKNOWN_QUOTA', 'requests':0}
        for row in (month, minute):
            if utc(row['observed_at']) > now:
                return {'status':'FUTURE_QUOTA_RECEIPT', 'requests':0}
        if now - utc(month['observed_at']) > timedelta(seconds=p['max_quota_age_seconds']):
            return {'status':'STALE_MONTHLY_QUOTA', 'requests':0}
        monthly, per_minute = json.loads(month['usage_json']), json.loads(minute['usage_json'])
        month_reset, minute_reset = monthly.get('monthly_reset'), per_minute.get('minute_reset')
        if (month_reset is None or minute_reset is None or now.timestamp() >= month_reset
                or now.timestamp() >= minute_reset):
            return {'status':'QUOTA_REFRESH_REQUIRED', 'requests':0}
        reserve = max(p['monthly_reserve'], math.ceil((month_reset-now.timestamp())/86400)*p['daily_reserve'])
        remaining = monthly['monthly_remaining']
        month_free = 100000 if remaining == -1 else remaining - reserve - self._debit(db, month)
        minute_free = per_minute['minute_remaining'] - p['minute_reserve'] - self._debit(db, minute)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec='microseconds')
        day_used = db.execute("SELECT count(*) FROM quota_attempts WHERE kind='history' AND started_at>=?", (day,)).fetchone()[0]
        recent = (now-timedelta(seconds=60)).isoformat(timespec='microseconds')
        minute_used = db.execute("SELECT count(*) FROM quota_attempts WHERE kind='history' AND started_at>?", (recent,)).fetchone()[0]
        capacity = min(p['max_requests'], month_free, minute_free,
                       p['daily_cap']-day_used-self.legacy_requests, p['per_minute_cap']-minute_used)
        return {'status':'AVAILABLE' if capacity > 0 else 'QUOTA_RESERVED_FOR_DAILY_WORK',
                'requests':max(0,capacity), 'monthly_reserve':reserve,
                'monthly_header_remaining':remaining, 'minute_header_remaining':per_minute['minute_remaining'],
                'history_claimed_today':day_used, 'legacy_reserved_today':self.legacy_requests}

    def allowance(self):
        now = utc(self.clock())
        if self.policy is None:
            raise ValueError('BACKGROUND_POLICY_REQUIRED')
        if not self.path.exists():
            return {'status':'UNKNOWN_QUOTA', 'requests':0}
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            return self._allowance(db, now)

    def legacy_state(self):
        """Pass original authenticated receipts to the existing history fences."""
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            found = {r['attempt_id']:r for key in ('monthly_remaining','minute_remaining','retry_after','http_status')
                     if (r := self._dimension(db,key)) is not None}
        if not found:
            return None
        rows=[{'provider':'the_stats_api','observed_at':r['observed_at'],
               'http_status':r['http_status'],'usage':json.loads(r['usage_json'])} for r in found.values()]
        latest=max(rows,key=lambda r:utc(r['observed_at']))
        return {**latest,'_receipts':rows} if len(rows)>1 else latest

    def begin(self):
        now = utc(self.clock())
        if self.source_check is not None and not self.source_check():
            from goal_analysis.providers.the_stats_api import TheStatsApiError

            raise TheStatsApiError('VALIDATED_SOURCE_CHANGED')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if self.policy is not None:
                allowed = self._allowance(db, now)
                if not allowed['requests']:
                    from goal_analysis.providers.the_stats_api import TheStatsApiError

                    raise TheStatsApiError(allowed['status'])
            identifier = uuid4().hex
            db.execute('INSERT INTO quota_attempts VALUES (?,?,?)',
                       (identifier,self.kind,now.isoformat(timespec='microseconds')))
            db.commit()
        return identifier

    def finish(self, identifier, receipt, usage):
        if not receipt.get('authenticated'):
            raise ValueError('AUTHENTICATED_QUOTA_RECEIPT_REQUIRED')
        observed = utc(receipt['observed_at'])
        status = receipt['http_status']
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError('INVALID_QUOTA_RECEIPT_STATUS')
        values = asdict(usage)
        if any(v is not None and (type(v) is not int or v < (-1 if k.startswith('monthly_') and k != 'monthly_reset' else 0))
               for k,v in values.items()):
            raise ValueError('INVALID_QUOTA_RECEIPT_USAGE')
        with closing(self._connect()) as db:
            attempt = db.execute('SELECT * FROM quota_attempts WHERE attempt_id=?',(identifier,)).fetchone()
            if attempt is None or utc(attempt['started_at']) > observed:
                raise ValueError('UNBOUND_QUOTA_RECEIPT')
            db.execute('INSERT INTO quota_receipts VALUES (?,?,?,?)',
                       (identifier,observed.isoformat(timespec='microseconds'),status,json.dumps(values,sort_keys=True)))
            db.commit()
