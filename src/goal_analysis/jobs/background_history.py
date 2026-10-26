"""Cheap, bounded history ticks after an independently verified daily freeze.

The normal daily pipeline runs first. No AI, wager, regeneration or LLM review.
An uncertain/crashed tick keeps its cooldown; provider attempts keep their claims.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash
from goal_analysis.storage.provider_quota import SharedQuota, activation, validate_policy

from . import historical_import_queue as queue_job
from . import the_stats_history as history
from .nightly import TERMINAL, JobStore, schedule_plan
from .portfolio import RunLock

VERSION = 'historical-background-v1'
SOURCE_FILES = ('src/goal_analysis/jobs/background_history.py', 'src/goal_analysis/jobs/historical_import_queue.py',
    'src/goal_analysis/jobs/the_stats_history.py','src/goal_analysis/jobs/the_stats_context.py',
    'src/goal_analysis/jobs/nightly.py','src/goal_analysis/jobs/nightly_recovery.py',
    'src/goal_analysis/storage/provider_quota.py',
    'src/goal_analysis/providers/the_stats_api.py','src/goal_analysis/providers/the_stats_primary.py',
    'scripts/run-arthur-background-history.py','scripts/run-arthur-scheduled.ps1',
    'config/historical-background.json',
    'src/goal_analysis/jobs/historical_result_sync.py',
    'src/goal_analysis/jobs/historical_source_binding.py',
    'src/goal_analysis/jobs/historical_results.py',
    'src/goal_analysis/jobs/historical_result_sink.py',
    'src/goal_analysis/jobs/historical_page_availability.py',
    'src/goal_analysis/storage/raw_admission.py',
    'src/goal_analysis/storage/raw_evidence.py',
    'src/goal_analysis/storage/http_observations.py',
    'src/goal_analysis/storage/temporal.py',
    'src/goal_analysis/storage/historical_results.py',
    'src/goal_analysis/normalization/historical_results.py')


def implementation_hash():
    code = Path(__file__).resolve().parents[3]
    return history._hash({name:hashlib.sha256((code/name).read_bytes()).hexdigest() for name in SOURCE_FILES})


def policy_at(root):
    path = Path(root)/'config/historical-background.json'
    if not path.is_file():
        return None
    value = json.loads(path.read_bytes())
    if (set(value) != {'version','enabled','interval_seconds','max_pages','max_seconds','quota'}
            or value['version'] != VERSION or type(value['enabled']) is not bool
            or type(value['interval_seconds']) is not int or not 900 <= value['interval_seconds'] <= 86400
            or type(value['max_pages']) is not int or not 1 <= value['max_pages'] <= 20
            or type(value['max_seconds']) is not int or not 30 <= value['max_seconds'] <= 90):
        raise ValueError('INVALID_BACKGROUND_HISTORY_POLICY')
    validate_policy(value['quota'])
    return value if value['enabled'] else None


def managed(root):
    """An armed replacement owns history spending, including while paused."""
    return bool(policy_at(root) and activation(root).get('tracking_enabled'))


def publication_gate(root, now, seconds):
    plan = schedule_plan(now)
    job = JobStore(root).get(plan['target_date'])
    if not job:
        return {'status':'DAILY_WORK_HAS_PRIORITY'}
    stages = {r['name']:r for r in job['stages']}
    if stages.get('archive',{}).get('status') != 'SUCCESS':
        return {'status':'DAILY_WORK_HAS_PRIORITY'}
    stop = min(now+timedelta(seconds=seconds), utc(plan['next_run_at'])-timedelta(seconds=30))
    for name, stage in stages.items():
        if stage['status'] in TERMINAL:
            continue
        retry = utc(stage['next_retry_at']) if stage.get('next_retry_at') else None
        if stage['status'] == 'RUNNING' or retry is not None and retry <= now:
            return {'status':'DAILY_WORK_HAS_PRIORITY'}
        if retry is not None:
            stop = min(stop,retry-timedelta(seconds=30))
        elif name != 'learning' or all(stages[n]['status'] in TERMINAL for n in ('settlement','generation','archive')):
            return {'status':'DAILY_WORK_HAS_PRIORITY'}
    if (stop-now).total_seconds() < 30:
        return {'status':'NO_SPARE_TIME'}
    ledger = Path(root)/'data/arthur/ledger.sqlite3'
    if not ledger.is_file():
        return {'status':'MISSING_DAILY_FREEZE'}
    with closing(sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        chains=PortfolioLedger._freeze_chains(db,plan['target_date'])
        if len(chains) != 1:
            return {'status':'MISSING_DAILY_FREEZE'}
        frozen=chains[0][-1]
        run_id=stages['generation'].get('run_id')
        if frozen['run_id'] != run_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',str(run_id)):
            return {'status':'DAILY_FREEZE_MISMATCH'}
        source=db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_runs WHERE run_id=?',(run_id,)).fetchone()
        if source is None:
            return {'status':'MISSING_DAILY_REPORT'}
        report=json.loads(source['snapshot_json'])
        archive=Path(root)/'reports/arthur'/run_id/'report.json'
        if (_hash(report) != source['snapshot_hash'] or frozen['snapshot']['report'] != report
                or not archive.is_file() or json.loads(archive.read_bytes()) != report):
            return {'status':'DAILY_REPORT_PROOF_MISMATCH'}
        stamp=utc(frozen['frozen_at'])
        legs=[l for r in frozen['snapshot']['recommendations'] for l in r.get('legs',[])]
        if (not legs or stamp > now or any(stamp >= utc(l['kickoff'])-timedelta(minutes=15) for l in legs)):
            return {'status':'INVALID_DAILY_FREEZE_TIME'}
    legacy=job.get('the_stats_history',{})
    spent=legacy.get('requests',0)
    if legacy.get('observed_at') and utc(legacy['observed_at']).date() != now.date():
        spent=0
    return {'status':'AVAILABLE','stop_at':stop,'run_id':run_id,'target_date':plan['target_date'],
            'snapshot_hash':frozen['snapshot_hash'], 'legacy_requests':spent}


def tick(root, *, scheduled=True, max_requests=None, clock=lambda: datetime.now(UTC),
         api_key=None, transport=None, runner=None):
    root=Path(root).resolve()
    policy=policy_at(root)
    armed=activation(root)
    base={'version':VERSION,'mode':'SHADOW','requests':0,'llm_calls':0,'ledger_writes':0}
    if not policy or not armed.get('tracking_enabled') or scheduled and not armed.get('background_enabled'):
        return {**base,'status':'DISARMED'}
    if utc(armed['activated_at']) > utc(clock()) or armed.get('source_sha256') != implementation_hash():
        return {**base,'status':'VALIDATED_SOURCE_CHANGED'}
    if max_requests is not None and (type(max_requests) is not int or not 1 <= max_requests <= policy['quota']['max_requests']):
        raise ValueError('INVALID_BACKGROUND_REQUEST_OVERRIDE')
    try:
        with RunLock(root/'data/arthur/run.lock') as root_lock:
            now=utc(clock())
            gate=publication_gate(root,now,policy['max_seconds'])
            if gate['status'] != 'AVAILABLE':
                return {**base,**gate}
            work=root/'data/arthur/historical-background'
            state_path=work/'state.json'
            state=history._read(state_path) if state_path.is_file() else {}
            if state.get('attempted_at') and utc(state['attempted_at']) > now:
                return {**base,'status':'CLOCK_MOVED_BACKWARDS'}
            if state.get('next_attempt_at') and utc(state['next_attempt_at']) > now:
                return {**base,'status':'COOLDOWN','next_attempt_at':state['next_attempt_at']}
            quota=SharedQuota(root,kind='history',policy=policy['quota'],
                              legacy_requests=gate['legacy_requests'],clock=clock,
                              source_check=lambda:implementation_hash()==armed['source_sha256'])
            available=quota.allowance()
            if not available['requests']:
                return {**base,**available}
            plan_path=root/'data/arthur/the-stats-history/active-plan.json'
            if not plan_path.is_file():
                return {**base,'status':'NO_ACTIVE_HISTORY_PLAN'}
            queue=queue_job.prepare_queue(history.load_plan(plan_path),as_of=now)
            folder=root/'data/arthur/historical-import-queue/plans'
            folder.mkdir(parents=True,exist_ok=True)
            queue_path=folder/(queue['queue_sha256']+'.json')
            # Freeze one queue for the UTC day; new ticks must reuse its exact
            # as_of/plan hashes rather than repeatedly downloading a new delta.
            if state.get('queue_day') == now.date().isoformat() and state.get('queue_path'):
                queue_path=Path(state['queue_path'])
                if not queue_path.resolve().is_relative_to(folder.resolve()):
                    raise ValueError('INVALID_SAVED_QUEUE_PATH')
                queue=history._read(queue_path)
                queue_job.validate_queue(queue)
            else:
                queue_job.save_queue(queue_path,queue)
            cap=min(available['requests'],max_requests or policy['quota']['max_requests'])
            state={'version':VERSION,'status':'STARTED','attempted_at':now.isoformat(),
                'next_attempt_at':(now+timedelta(seconds=policy['interval_seconds'])).isoformat(),
                'queue_day':now.date().isoformat(),'queue_path':str(queue_path),
                'queue_sha256':queue['queue_sha256'],'max_requests':cap,'daily_gate':{k:v for k,v in gate.items() if k!='stop_at'}}
            history._atomic(state_path,state)  # persists before any uncertain network I/O
            result=(runner or queue_job.run_queue)(root,queue,execute=True,daily_work_complete=True,
                max_requests=cap,max_pages=policy['max_pages'],monthly_reserve=available['monthly_reserve'],
                clock=clock,stop_at=gate['stop_at'],api_key=api_key,transport=transport,quota_observer=quota,
                **({'root_lock':root_lock} if runner is None else {}))
            state.update(status=result['status'],result=result,completed_at=utc(clock()).isoformat(),quota=available)
            history._atomic(state_path,state)
            return {**base,**result,'next_attempt_at':state['next_attempt_at'],'daily_gate':state['daily_gate']}
    except RuntimeError as error:
        if str(error) == 'Már fut egy Arthur-feladat. Várd meg a befejezését.':
            return {**base,'status':'DAILY_PROCESS_BUSY'}
        raise
