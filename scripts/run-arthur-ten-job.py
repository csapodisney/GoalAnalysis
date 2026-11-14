"""One durable full-day job. No scheduler/daemon retry or implicit settlement."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_source_host import source_environment
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.portfolio import RunLock, validate_target_day
from goal_analysis.jobs.workshop_dispatch import exclusive_json
from goal_analysis.jobs.workshop_launch import LaunchStore, request_identity
from goal_analysis.jobs.workshop_windows_job import (
    WindowsJob,
    drain,
    human_deadline_policy,
    human_phase_deadline,
    resource_cancel_decision,
)
from goal_analysis.workshop_service import SavedWorkshop, readonly
from goal_analysis.workshop_views import main_slot_capacity

OUTPUT = None
DEADLINE = None
HUMAN_DEADLINE = None


def require(value, reason):
    if not value:
        raise ValueError(reason)


def save_new(path, value):
    exclusive_json(path, value)


def resource_hardstop():
    path = ROOT / 'data/arthur/workshop-resource-stop.json'
    if not path.exists():
        return None
    require(not path.is_symlink(), 'EXACT_RESOURCE_STOP_FILE')
    with path.open('rb') as stream:
        raw = stream.read(4097)
    return resource_cancel_decision(raw, datetime.now(UTC))


def load_human_deadline(job):
    """Read one optional request authority before any business DB read or spawn."""
    path = ROOT / 'data/arthur/workshop-deadlines' / (job['request_id'] + '.json')
    if not path.exists():
        return None
    require(not path.is_symlink() and path.resolve().is_relative_to(ROOT.resolve()), 'EXACT_HUMAN_DEADLINE_PATH')
    with path.open('rb') as stream:
        raw = stream.read(4097)
    require(0 < len(raw) <= 4096, 'BOUNDED_HUMAN_DEADLINE_REQUIRED')
    source = json.loads(raw.decode('utf-8-sig'))['source_map']
    relative = source['relative_path']
    require(isinstance(relative, str) and relative == PurePosixPath(relative).as_posix()
            and not PurePosixPath(relative).is_absolute() and '..' not in PurePosixPath(relative).parts
            and ':' not in relative and '\\' not in relative
            and relative.startswith('artifacts/') and relative.endswith('/SOURCE-MAP.json'),
            'EXACT_HUMAN_DEADLINE_SOURCE_MAP_PATH')
    source_path = ROOT / relative
    require(not source_path.is_symlink() and source_path.resolve().is_relative_to((ROOT / 'artifacts').resolve())
            and source_path.stat().st_size <= 1024**2, 'BOUNDED_DEADLINE_SOURCE_MAP')
    with source_path.open('rb') as stream:
        source_raw = stream.read(1024**2 + 1)
    require(len(source_raw) <= 1024**2, 'DEADLINE_SOURCE_MAP_GREW')
    manifest = json.loads(source_raw)
    rows = manifest['files']
    require(isinstance(rows, list) and 0 < len(rows) <= 1000, 'BOUNDED_DEADLINE_SOURCE_MEMBERSHIP')
    actual = {}
    for row in rows:
        name = row['path']
        require(isinstance(name, str) and name == PurePosixPath(name).as_posix()
                and not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
                and ':' not in name and '\\' not in name
                and (name.startswith(('src/', 'scripts/', 'config/')) or name == 'pyproject.toml') and name not in actual,
                'UNIQUE_DECLARED_DEADLINE_SOURCE_PATH')
        item = ROOT / name
        require(not item.is_symlink() and item.resolve().is_relative_to(ROOT.resolve())
                and item.is_file() and item.stat().st_size <= 8 * 1024**2, 'BOUNDED_ACTUAL_DEADLINE_SOURCE')
        with item.open('rb') as stream:
            item_raw = stream.read(8 * 1024**2 + 1)
        require(len(item_raw) <= 8 * 1024**2, 'DEADLINE_SOURCE_FILE_GREW')
        actual[name] = hashlib.sha256(item_raw).hexdigest()
        require(actual[name] == row['worktree_sha256'], 'HUMAN_DEADLINE_SOURCE_BYTES_CHANGED')
    binding = {'relative_path': relative, 'sha256': hashlib.sha256(source_raw).hexdigest(),
               'source_commit': manifest['source_commit'], 'source_files_sha256': digest(actual)}
    policy = human_deadline_policy(raw, datetime.now(UTC), request_id=job['request_id'],
        target_date=job['target_date'], source_binding=binding)
    with (OUTPUT / 'human-deadline-authority.json').open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    save_new(OUTPUT / 'human-deadline-binding.json', {'job_id': job['job_id'], 'request_id': job['request_id'],
        'target_date': job['target_date'], 'source_map': binding, 'authority_sha256': policy['authority_sha256'],
        'business_stop_at_utc': policy['business_stop_at_utc'],
        'verification_stop_at_utc': policy['verification_stop_at_utc'],
        'human_stop_at_utc': policy['human_stop_at_utc'], 'read_once_no_periodic_sentinel': True})
    return policy


def supervise(command, folder, *, seconds, label='business', env=None, spawn=None, job_factory=WindowsJob):
    """Only actual command supplied by checked fixed CLI; tests inject harmless child."""
    require(label in {'business', 'verification'}, 'FIXED_OWNED_PHASE_REQUIRED')
    folder = Path(folder)
    require(folder.resolve() == OUTPUT and not OUTPUT.is_relative_to(ROOT), 'EXACT_NEW_OWNED_OUTPUT_REQUIRED')
    spawn = spawn or subprocess.Popen
    child, job, pumps = None, None, []
    assigned = False
    streams = {'stdout': {}, 'stderr': {}}
    result = {'status': 'PRESPAWN_FAILURE', 'native_exit_code': None, 'child_tree_stopped': False, 'automatic_retry': False}
    started = time.monotonic()
    try:
        require(0 < seconds <= (7200 if label == 'business' else 180), 'FINITE_OUTER_BOUND')
        stop = resource_hardstop()
        if stop is not None:
            result.update(status='RESOURCE_HARDSTOP', native_exit_code=None, child_tree_stopped=True,
                          child_started=False, resource_hardstop=stop)
            return result
        if HUMAN_DEADLINE is not None:
            result['human_deadline'] = {'authority_sha256': HUMAN_DEADLINE['authority_sha256'],
                'phase_stop_at_utc': HUMAN_DEADLINE[label + '_stop_at_utc'],
                'effective_deadline_at_utc': DEADLINE.isoformat()}
            if datetime.now(UTC) >= DEADLINE:
                result.update(status='HUMAN_PHASE_DEADLINE_BEFORE_SPAWN', child_tree_stopped=True, child_started=False)
                return result
        job = job_factory()
        flags = 0x00000004 | 0x00000200  # CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
        child = spawn(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, creationflags=flags, close_fds=True)
        job.assign(child)
        assigned = True
        result['child_started_at'] = datetime.now(UTC).isoformat()
        save_new(folder / (label + '-child-start.json'), {'pid': child.pid, 'at': result['child_started_at'],
                 'image': command[0], 'arguments': command[1:], 'owned_job_assigned_before_resume': True,
                 'ownership': 'EXACT_POPEN_PROCESS_HANDLE_AND_EXCLUSIVE_WINDOWS_JOB_NO_PID_KILL'})
        for name, stream_result in streams.items():
            pump = threading.Thread(target=drain, args=(getattr(child, name), folder / (label + '-' + name + '.log'), stream_result), daemon=True)
            pump.start()
            pumps.append(pump)
        job.resume(child)
        exited_at = None
        while True:
            stop = resource_hardstop()
            if stop is not None:
                job.terminate()
                child.wait(timeout=10)
                result.update(status='RESOURCE_HARDSTOP', native_exit_code=child.returncode,
                              child_tree_stopped=job.active() == 0, child_started=True,
                              resource_hardstop=stop)
                break
            code = child.poll()
            active = job.active()
            if code is not None and active == 0:
                result.update(status='CHILD_STOPPED_NATIVE', native_exit_code=code, child_tree_stopped=True)
                break
            if code is not None and exited_at is None:
                exited_at = time.monotonic()
            if datetime.now(UTC) >= DEADLINE or time.monotonic() - started >= seconds or (exited_at is not None and time.monotonic() - exited_at > 30):
                job.terminate()
                child.wait(timeout=10)
                human_expired = HUMAN_DEADLINE is not None and datetime.now(UTC) >= datetime.fromisoformat(HUMAN_DEADLINE[label + '_stop_at_utc'])
                result.update(status='HUMAN_PHASE_DEADLINE_STOP' if human_expired else 'TIMEOUT_DURABLE_PARTIAL' if exited_at is None else 'ORPHAN_DESCENDANTS_DURABLE_PARTIAL',
                              native_exit_code=child.returncode, child_tree_stopped=job.active() == 0)
                break
            time.sleep(0.1)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(status='STOPPED_PRESERVE_PARTIAL', error_type=type(error).__name__)
        if child is not None and not assigned:
            # The only child outside our job is still suspended, so it has no descendants.
            # Popen.kill uses the exact open process handle on Windows, never a reused PID.
            child.kill()
            child.wait(timeout=10)
            result['child_tree_stopped'] = True
        if job is not None:
            try:
                job.terminate()
                if child is not None:
                    child.wait(timeout=10)
                result['child_tree_stopped'] = job.active() == 0 and (child is None or child.poll() is not None)
            except (OSError, ValueError, subprocess.SubprocessError):
                result['child_tree_stopped'] = False
        elif child is None:
            result['child_tree_stopped'] = True
    finally:
        if job is not None:
            job.close()  # kill-on-close is a second owned-tree boundary, never taskkill/PID lookup.
        for pump in pumps:
            pump.join(timeout=10)
        result.update(at=datetime.now(UTC).isoformat(), elapsed_seconds=time.monotonic() - started,
                      log_streams=streams, log_pumps_stopped=all(not pump.is_alive() for pump in pumps))
        save_new(folder / (label + '-outcome.json'), result)
    return result


def target_runs(day):
    with readonly(ROOT / 'data/arthur/ledger.sqlite3') as db:
        rows = db.execute('SELECT run_id FROM portfolio_runs WHERE date=? LIMIT 1001', (day,)).fetchall()
    require(len(rows) <= 1000, 'TARGET_RUN_INVENTORY_BOUND')
    return {row['run_id'] for row in rows}


def verify_saved(day, before):
    new_ids = target_runs(day) - before
    require(len(new_ids) == 1, 'EXACT_ONE_NEW_SAVED_RUN_REQUIRED')
    run_id = next(iter(new_ids))
    repository = SavedWorkshop(ROOT)
    report = repository.report(run_id=run_id)
    path = ROOT / 'reports/arthur' / run_id / 'report.json'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024 * 1024,
            'BOUNDED_NEW_PHYSICAL_REPORT_REQUIRED')
    raw = path.read_bytes()
    require(json.loads(raw) == report and report['date'] == day,
            'PHYSICAL_REPORT_AND_LEDGER_SNAPSHOT_MUST_MATCH')
    require(digest({key: value for key, value in report.items() if key != 'report_sha256'}) == report['report_sha256'],
            'EMBEDDED_REPORT_HASH_REQUIRED')
    cards = report['generated_tickets']
    require(isinstance(cards, list) and len(cards) <= 32
            and len({card['ticket_id'] for card in cards}) == len(cards), 'CANONICAL_CARD_CAPACITY_AND_IDENTITIES')
    require(isinstance(report.get('companion_ticket_channels'), dict), 'TWELVE_SLOT_REPORT_MARKER_REQUIRED')
    from goal_analysis.companion_tickets import validate_companion_cards

    validate_companion_cards(report)  # Saved product validation only, no producer execution.
    capacity = main_slot_capacity(report)
    require(not capacity['invalid_card_ids'] and not capacity['identity_issues'],
            'EXACT_CURRENT_AGENT_AND_COMPANION_IDENTITIES_REQUIRED')
    with readonly(ROOT / 'data/arthur/ledger.sqlite3') as db:
        rows = db.execute('SELECT ticket_id,snapshot_json,snapshot_hash FROM portfolio_tickets WHERE run_id=?', (run_id,)).fetchall()
    stored = {row['ticket_id']: row for row in rows}
    require(set(stored) == {card['ticket_id'] for card in cards}, 'ALL_CANONICAL_TICKET_ROWS_REQUIRED')
    require(all(json.loads(stored[card['ticket_id']]['snapshot_json']) == card
                and stored[card['ticket_id']]['snapshot_hash'] == digest(card) for card in cards),
            'EXACT_CANONICAL_TICKET_SNAPSHOTS_REQUIRED')
    evidence = report.get('ticket_evidence', {})
    require(evidence.get('accounting', {}).get('canonical_cohort_sha256') == digest(cards),
            'SAVED_COHORT_EVIDENCE_BINDING_REQUIRED')
    owners = report['ten_agent_decisions']['owners']
    require(len(owners) == 10 and {row['agent_id'] for row in owners} == set(AGENT_IDS), 'ALL_TEN_OUTCOMES_REQUIRED')
    complete = evidence.get('status') == 'COMPLETE' and all(
        row.get('status') == 'DECISION_COMPLETED' for row in owners)
    return {'run_id': run_id, 'generated_ticket_count': len(cards), 'target_met': capacity['target_met'],
            'capacity': capacity,
            'saved_evidence_complete': complete, 'canonical_cohort_sha256': digest(cards),
            'physical_report_sha256': hashlib.sha256(raw).hexdigest(),
            'verification_scope': 'SAVED_CANONICAL_LEDGER_REPORT_AND_PRODUCER_EVIDENCE',
            'independent_native_source_or_all_dataset_audit': False, 'profit_proven': False}


def source_pins():
    paths = sorted((ROOT / 'src/goal_analysis').rglob('*.py'))
    paths += [ROOT / 'scripts' / name for name in (
        'run-arthur.py', 'run-arthur-ten-job.py', 'run-arthur-workshop-verify.py',
        'arthur-ten-job-bootstrap.ps1', 'arthur-secrets.ps1')]
    require(len(paths) <= 1000 and all(not path.is_symlink() for path in paths), 'STATIC_SOURCE_BOUND')
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    global OUTPUT, DEADLINE, HUMAN_DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    request_identity(args.job_id)
    store = LaunchStore(ROOT / 'data/arthur/workshop-launch.sqlite3')
    job = store.get(args.job_id, private=True)
    require(job['state'] == 'RESERVED', 'ONCE_RESERVED_JOB_REQUIRED')
    now = datetime.now(UTC)
    validate_target_day(date.fromisoformat(job['target_date']), now)
    require(digest(job['settings']) == job['settings_sha256'], 'EXACT_RESERVED_SETTINGS_REQUIRED')
    OUTPUT = Path(tempfile.gettempdir()).resolve() / ('arthur-ten-' + args.job_id)
    require(not OUTPUT.exists() and not OUTPUT.is_relative_to(ROOT), 'NEW_SHORT_OWNED_JOB_FOLDER_REQUIRED')
    DEADLINE = now + timedelta(seconds=7200)
    store.transition(args.job_id, expected='RESERVED', values={'state': 'RUNNING',
        'stage': 'PREFLIGHT', 'started_at': now.isoformat()})
    native = None
    try:
        OUTPUT.mkdir()
        pins = source_pins()
        save_new(OUTPUT / 'source-before.json', pins)
        save_new(OUTPUT / 'settings.json', job['settings'])
        save_new(OUTPUT / 'job-intent.json', {key: value for key, value in job.items() if key != 'settings'})
        HUMAN_DEADLINE = load_human_deadline(job)
        if HUMAN_DEADLINE is not None:
            DEADLINE = human_phase_deadline(now, seconds=7200, label='business', policy=HUMAN_DEADLINE)
        with RunLock(ROOT / 'data/arthur/run.lock'):
            before = target_runs(job['target_date'])
            save_new(OUTPUT / 'before-runs.json', sorted(before))
        # Lock intentionally released: the unchanged CLI acquires its own RootRunLock.
        owner_deadline = DEADLINE - timedelta(seconds=300)
        require(datetime.now(UTC) < owner_deadline, 'OWNER_SAVE_RESERVE_EXHAUSTED_BEFORE_SPAWN')
        save_new(OUTPUT / 'owner-deadline-binding.json', {
            'version': 'GRACEFUL_OWNER_COMPLETION_V1', 'job_id': job['job_id'],
            'request_id': job['request_id'], 'business_stop_at_utc': DEADLINE.isoformat(),
            'owner_stop_at_utc': owner_deadline.isoformat(), 'save_reserve_seconds': 300,
            'reserve_is_not_a_wall_time_guarantee': True})
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(ROOT / 'scripts/arthur-ten-job-bootstrap.ps1'), '-Root', str(ROOT),
            '-TargetDate', job['target_date'], '-Settings', str(OUTPUT / 'settings.json'), '-Output', str(OUTPUT),
            '-OwnerDeadlineAt', owner_deadline.isoformat()]
        native = supervise(command, OUTPUT, seconds=7200, env=source_environment())
        require(native['child_tree_stopped'] and native['log_pumps_stopped'], 'NATIVE_TREE_AND_LOGS_STOP_REQUIRED')
        store.transition(args.job_id, expected='RUNNING', values={'state': 'VERIFYING', 'stage': 'SAVED_READBACK',
            'native_exit': native['native_exit_code'], 'tree_stopped': True})
        verified = verify_saved(job['target_date'], before)
        save_new(OUTPUT / 'saved-verification.json', verified)
        # The 7200s business guardian is separate from the stopped 180s verifier.
        DEADLINE = human_phase_deadline(datetime.now(UTC), seconds=180, label='verification', policy=HUMAN_DEADLINE)
        verification_native = supervise([str(ROOT / '.venv/Scripts/python.exe'), '-B',
            str(ROOT / 'scripts/run-arthur-workshop-verify.py'), '--run-id', verified['run_id'],
            '--output', str(OUTPUT / 'physical-verification.json')], OUTPUT, seconds=180,
            label='verification', env=source_environment())
        require(verification_native['native_exit_code'] == 0
            and verification_native['child_tree_stopped'] and verification_native['log_pumps_stopped'],
            'PHYSICAL_VERIFIER_NATIVE_AND_TREE_REQUIRED')
        output_path = OUTPUT / 'physical-verification.json'
        require(output_path.is_file() and not output_path.is_symlink()
            and output_path.stat().st_size <= 8388608, 'BOUNDED_PHYSICAL_VERIFICATION_RESULT_REQUIRED')
        physical = json.loads(output_path.read_bytes())
        require(physical['run_id'] == verified['run_id'], 'PHYSICAL_VERIFIER_RUN_CHANGED')
        require(source_pins() == pins, 'SOURCE_CHANGED_DURING_JOB')
        passed = physical['status'] == 'VERIFIED'
        success = passed and native['native_exit_code'] == 0 and verified['target_met'] is True
        store.transition(args.job_id, expected='VERIFYING', values={
            'state': 'SUCCEEDED' if success else 'PARTIAL', 'stage': 'FINISHED',
            'run_id': verified['run_id'], 'generated_ticket_count': verified['generated_ticket_count'],
            'target_met': verified['target_met'], 'capacity': verified['capacity'],
            'verification_state': 'VERIFIED' if passed else 'PARTIAL',
            'reason_code': None if success else 'NATIVE_NONZERO' if native['native_exit_code'] != 0
                else 'NEW_COHORT_PHYSICAL_EVIDENCE_INCOMPLETE' if not passed else 'TWELVE_MAIN_SLOTS_NOT_FILLED',
            'finished_at': datetime.now(UTC).isoformat()})
        return native['native_exit_code'] if type(native['native_exit_code']) is int else 1
    except Exception as error:  # noqa: BLE001 - Persist unknown native outcome; never retry the durable job.
        current = store.get(args.job_id)
        if current['state'] in ('RUNNING', 'VERIFYING'):
            stopped = native.get('child_tree_stopped') if native is not None else not (OUTPUT / 'business-child-start.json').exists()
            store.transition(args.job_id, expected=current['state'], values={'state': 'STOPPED' if stopped is True else current['state'], 'stage': 'STOPPED' if stopped is True else 'TREE_STATE_UNKNOWN',
                'native_exit': native.get('native_exit_code') if native is not None else None,
                'tree_stopped': stopped, 'verification_state': 'UNKNOWN',
                'reason_code': type(error).__name__, 'finished_at': datetime.now(UTC).isoformat() if stopped is True else None})
        if OUTPUT.is_dir():
            save_new(OUTPUT / 'worker-stop.json', {'status': 'STOPPED_NO_RETRY', 'error_type': type(error).__name__,
                'at_utc': datetime.now(UTC).isoformat(), 'original_native_outcome_preserved': True})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
