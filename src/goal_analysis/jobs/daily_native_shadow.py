"""Bounded post-publication native model/options/player sidecar, never LIVE.

The worker reads existing evidence only, with SQLite forced read-only and no
network or child process access. A hard process timeout cannot delay a freeze
which has already completed. Original reports, staging and ledgers are inputs.
"""
import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import sysconfig
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization.canonical import utc

VERSION = 'daily-native-options-player-next-grok-calendar-xg-movement-release-weather-features-shadow-v15'
MAX_SECONDS = 180
MAX_BYTES = 64 * 1024 * 1024
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_seconds': MAX_SECONDS}


def enabled(root):
    path = Path(root)/'config/native-shadow.json'
    if not path.is_file():
        return False
    return _json(path) == POLICY


def _json(path):
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('SHADOW_FILE_BYTE_LIMIT')
    return json.loads(path.read_text('utf-8-sig'))


def _paths(root, run_id):
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,100}', run_id):
        raise ValueError('INVALID_SHADOW_RUN_ID')
    staging = root/'data/arthur/nightly-staging'/f'{run_id}.json'
    output = root/'data/arthur/native-shadow'/run_id
    if not staging.resolve().is_relative_to(root) or not output.resolve().is_relative_to(root):
        raise ValueError('SHADOW_PATH_OUTSIDE_ROOT')
    return root, staging, output


def _inputs(staging, report_hash):
    bundle = _json(staging)
    artifacts = bundle['artifacts']
    report = artifacts['report']
    if canonical_sha256(report) != report_hash:
        raise ValueError('SHADOW_REPORT_HASH_MISMATCH')
    fixtures = artifacts.get('fixtures', {}).get('records', [])
    analysis = artifacts.get('candidate_analysis') or artifacts.get('candidate_input') or {}
    candidates = analysis.get('candidates', [])
    if (not isinstance(fixtures, list) or not isinstance(candidates, list)
            or len(fixtures) > 1000 or len(candidates) > 10000):
        raise ValueError('BOUNDED_DAILY_NATIVE_INPUT_REQUIRED')
    return report, {**analysis, 'fixtures': fixtures, 'candidates': candidates, 'date': report['date']}


def _completion_checks(analysis, completed):
    checks = []
    options = analysis['options_shadow']
    for binding in options['option_source_bindings']:
        card = options['options'][binding['profile_id']][binding['option_index']]['recommendation']
        fresh = all(utc(leg['kickoff']) > completed + timedelta(minutes=15) for leg in card['legs'])
        for leg in binding['legs']:
            for kind in ('price_receipt', 'target_receipt'):
                age = (completed-utc(leg[kind]['times']['observed_at'])).total_seconds()
                fresh &= 0 <= age <= 1800
        checks.append({'profile_id': binding['profile_id'], 'option_index': binding['option_index'],
                       'eligible_at_completion': bool(fresh)})
    return checks


def _cup_completion_check(analysis, completed):
    return _specialist_completion_check(analysis, completed, 'cup_review')


def _xg_completion_check(analysis, completed):
    return _specialist_completion_check(analysis, completed, 'xg_review')


def _motivation_completion_check(analysis, completed):
    result = _specialist_completion_check(analysis, completed, 'motivation_review',
                                          expected_status='SHADOW_REVIEW_CANDIDATE')
    return {**result, 'independent_review_required': True, 'publication_eligible': False}


def _specialist_completion_check(analysis, completed, key, expected_status='SHADOW_CANDIDATE'):
    cup = analysis['options_shadow'].get(key, {})
    legs, bindings = cup.get('candidate_legs', []), cup.get('source_bindings', [])
    fresh = cup.get('status') == expected_status and 3 <= len(legs) <= 5
    fresh &= len(legs) == len(bindings)
    for leg, binding in zip(legs, bindings, strict=False):
        fresh &= (binding['leg_sha256'] == canonical_sha256(leg)
                  and utc(leg['kickoff']) > completed + timedelta(minutes=15))
        for kind in ('price_receipt', 'target_receipt'):
            age = (completed-utc(binding[kind]['times']['observed_at'])).total_seconds()
            fresh &= 0 <= age <= 1800
    return {'status': cup.get('status', 'UNAVAILABLE'),
            'reason': cup.get('reason', 'MISSING_'+key.upper()),
            'eligible_at_completion': bool(fresh), 'candidate_legs': len(legs),
            'diagnostics': cup.get('diagnostics', {})}


def worker(root, run_id, report_hash, cutoff, settings_path):
    from goal_analysis.jobs.native_options_shadow import replay_native_options
    from goal_analysis.jobs.rotation_option_shadow import completion_checks as rotation_checks
    from goal_analysis.jobs.weather_option_shadow import completion_checks as weather_checks

    root, staging, output = _paths(root, run_id)
    if Path(settings_path).resolve() != (output/'settings.json').resolve():
        raise ValueError('UNEXPECTED_SHADOW_SETTINGS_PATH')
    cutoff = utc(cutoff)
    if cutoff > datetime.now(UTC):
        raise ValueError('FUTURE_SHADOW_CUTOFF')
    report, enriched = _inputs(staging, report_hash)
    settings = _json(Path(settings_path))
    profiles = tuple(p for p in settings['enabled_profiles'] if p in ('gauss', 'kepler', 'laplace'))
    before = sha256(staging.read_bytes()).hexdigest()
    connect = sqlite3.connect
    database = (root/'data/goal_analysis.sqlite3').resolve()
    prospective = (root/'data/arthur/prospective-odds/shadow-monitor.sqlite3').resolve()

    def readonly(path, *args, **kwargs):
        value = str(path)
        if value.startswith('file:'):
            value = unquote(urlsplit(value).path)
            if len(value) > 2 and value[0] == '/' and value[2] == ':':
                value = value[1:]
        target = Path(value).resolve()
        if target not in {database, prospective}:
            raise ValueError('UNEXPECTED_SHADOW_DATABASE')
        db = connect(target.as_uri()+'?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db

    def forbidden(*args, **kwargs):
        raise RuntimeError('SHADOW_EXTERNAL_IO_FORBIDDEN')

    started = monotonic()
    with patch.object(sqlite3, 'connect', readonly), patch.object(socket, 'socket', forbidden), \
            patch.object(subprocess, 'Popen', forbidden):
        analysis = replay_native_options(root, report, enriched, settings, as_of=cutoff,
                                         profiles=profiles, artifact_dir=output/'fit')
    completed = datetime.now(UTC)
    checks = _completion_checks(analysis, completed)
    cup_check = _cup_completion_check(analysis, completed)
    xg_check = _xg_completion_check(analysis, completed)
    motivation_check = _motivation_completion_check(analysis, completed)
    weather_check = weather_checks(analysis['options_shadow'].get('weather_review', {}), completed)
    options = analysis['options_shadow']
    rotation_check = rotation_checks(options.get('rotation_review', {}), completed,
        options=options.get('options', {}), specialists={'player': options.get('player_review', {}).get('specialist', {}),
            'cup': options.get('cup_review', {}), 'xg': options.get('xg_review', {}),
            'motivation': options.get('motivation_review', {})})
    if before != sha256(staging.read_bytes()).hexdigest():
        raise ValueError('SHADOW_STAGING_CHANGED')
    raw = json.dumps(analysis, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('SHADOW_RESULT_BYTE_LIMIT')
    with (output/'analysis.json').open('x', encoding='utf-8') as stream:
        stream.write(raw)
    counts = analysis['options_shadow']['summary']
    summary = {'version': VERSION, 'mode': 'SHADOW', 'run_id': run_id,
        'status': 'OBSERVED' if checks and all(c['eligible_at_completion'] for c in checks) else 'GAPS',
        'as_of': cutoff.isoformat(), 'completed_at': completed.isoformat(),
        'seconds': monotonic()-started, 'report_sha256': report_hash,
        'worker_pid': os.getpid(), 'native_pipeline_invocations': 1,
        'analysis_sha256': canonical_sha256(analysis),
        'implementation_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'native_summary': counts, 'completion_checks': checks,
        'cup_completion_check': cup_check,
        'xg_completion_check': xg_check,
        'motivation_completion_check': motivation_check,
        'weather_completion_checks': weather_check,
        'rotation_completion_check': rotation_check,
        'eligible_options_at_completion': sum(c['eligible_at_completion'] for c in checks),
        'source_status': analysis['source_view']['status'],
        'source_gaps': analysis['source_view'].get('gaps', {}),
        'option_gaps': analysis['options_shadow']['gaps'],
        'external_requests': 0, 'llm_requests': 0, 'ledger_writes': 0,
        'live_decision_effect': False, 'full_integration_or_calibration_certified': False}
    with (output/'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, ensure_ascii=False, allow_nan=False)
    return summary


def run(root, run_id, report_hash, settings, *, as_of, max_seconds=MAX_SECONDS, prepare_sources=False):
    """After archive: bounded parent source preparation, then isolated RO worker."""
    if type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS:
        raise ValueError('INVALID_SHADOW_TIME_BUDGET')
    if type(prepare_sources) is not bool:
        raise ValueError('BOOLEAN_NATIVE_PREPARATION_REQUIRED')
    root, staging, output = _paths(root, run_id)
    report, enriched = _inputs(staging, report_hash)
    output.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    admission = {'status': 'NOT_REQUESTED', 'created': 0}
    players = {'status': 'DISABLED', 'external_requests': 0}
    next_grok = {'status': 'NOT_PREPARED', 'external_requests': 0, 'llm_requests': 0}
    xg_sources = {'status': 'DISABLED', 'external_requests': 0}
    xg_capture = {'status': 'DISABLED', 'external_requests': 0}
    calendars = {'status': 'DISABLED', 'external_requests': 0}
    if prepare_sources:
        from goal_analysis.jobs.native_source_admission import admit_history

        try:
            from goal_analysis.jobs import player_collection_shadow as player_sources

            if player_sources.enabled(root):
                spare = int(max_seconds-(monotonic()-started)-30)
                if spare >= 7:
                    collected = player_sources.collect(root, enriched['fixtures'],
                        max_seconds=min(player_sources.POLICY['max_seconds'], spare))
                    with (output/'player-collection.json').open('x', encoding='utf-8') as stream:
                        json.dump(collected, stream, ensure_ascii=False, allow_nan=False)
                    players = {k:v for k,v in collected.items() if k not in ('records', 'fixture_evidence')}
                    players['artifact'] = str((output/'player-collection.json').relative_to(root))
                else:
                    players = {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0}
        except Exception:  # noqa: BLE001 - optional acquisition cannot veto a frozen package.
            players = {'status': 'UNAVAILABLE', 'reason': 'PLAYER_COLLECTION_FAILED',
                       'external_requests': None, 'uncertain_work_must_not_repeat': True}
        try:
            from goal_analysis.jobs import calendar_collection_shadow as calendar

            if calendar.enabled(root):
                spare = int(max_seconds-(monotonic()-started)-30)
                if spare >= 4:
                    collected = calendar.collect(root, enriched['fixtures'],
                        max_seconds=min(calendar.POLICY['max_seconds'], spare))
                    with (output/'calendar-collection.json').open('x', encoding='utf-8') as stream:
                        json.dump(collected, stream, ensure_ascii=False, allow_nan=False)
                    calendars = {k:v for k,v in collected.items() if k not in ('records', 'dataset')}
                    calendars['artifact'] = str((output/'calendar-collection.json').relative_to(root))
                else:
                    calendars = {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0}
        except Exception:  # noqa: BLE001 - optional acquisition never vetoes frozen cards or the existing worker.
            calendars = {'status': 'UNAVAILABLE', 'reason': 'CALENDAR_COLLECTION_FAILED',
                         'external_requests': None, 'uncertain_work_must_not_repeat': True}
        try:
            admission = admit_history(root, enriched['fixtures'])
        except Exception:  # noqa: BLE001 - missing admission cannot veto an already frozen package.
            admission = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_HISTORY_ADMISSION_FAILED'}
        try:
            from goal_analysis.jobs import xg_collection_shadow

            if xg_collection_shadow.enabled(root):
                spare = int(max_seconds-(monotonic()-started)-35)
                if spare >= 5:
                    collected = xg_collection_shadow.collect(root, enriched['fixtures'], max_seconds=min(30, spare))
                    with (output/'xg-collection.json').open('x', encoding='utf-8') as stream:
                        json.dump(collected, stream, ensure_ascii=False, allow_nan=False)
                    xg_sources = {key:value for key,value in collected.items() if key not in ('outcomes', 'envelopes')}
                    xg_sources['artifact'] = str((output/'xg-collection.json').relative_to(root))
                else:
                    xg_sources['status'] = 'SKIPPED_NO_SPARE_WINDOW'
        except Exception:  # noqa: BLE001 - optional sources never veto a frozen package.
            xg_sources = {'status': 'UNAVAILABLE', 'reason': 'XG_COLLECTION_FAILED',
                          'external_requests': None, 'uncertain_work_must_not_repeat': True}
        # Newly witnessed physical presence is NOT known at the caller's older
        # cutoff. Start this prospective run AFTER admission, at actual NOW.
        as_of = datetime.now(UTC)
        try:
            from goal_analysis.jobs.grok_player_handoff_shadow import prepare

            if max_seconds-(monotonic()-started) >= 35:
                handoff = prepare(root, report, enriched['fixtures'], as_of=as_of)
                with (output/'next-grok-input.json').open('x', encoding='utf-8') as stream:
                    json.dump(handoff, stream, ensure_ascii=False, allow_nan=False)
                next_grok = {key: handoff[key] for key in ('status', 'schema_valid', 'wire_characters',
                    'accepted_player_facts', 'fresh_price_receipts', 'dispatch_allowed',
                    'external_requests', 'llm_requests')}
                next_grok['artifact'] = str((output/'next-grok-input.json').relative_to(root))
            else:
                next_grok['status'] = 'SKIPPED_NO_SPARE_WINDOW'
        except Exception:  # noqa: BLE001 - detached preparation cannot veto frozen cards.
            next_grok.update(status='UNAVAILABLE', reason='NEXT_GROK_PLAYER_INPUT_FAILED')
        try:
            from goal_analysis.jobs import xg_forecast_capture

            if xg_forecast_capture.enabled(root):
                spare = int(max_seconds-(monotonic()-started)-30)
                xg_capture = (xg_forecast_capture.capture(root, run_id, report_hash, enriched['fixtures'],
                    max_seconds=min(10, spare)) if spare >= 1 else
                    {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0})
        except Exception:  # noqa: BLE001 - prospective research cannot veto frozen cards.
            xg_capture = {'status': 'UNAVAILABLE', 'reason': 'XG_FORECAST_CAPTURE_FAILED',
                          'external_requests': 0, 'automatic_retry': False}
    settings_path = output/'settings.json'
    with settings_path.open('x', encoding='utf-8') as stream:
        json.dump(settings, stream, ensure_ascii=False, allow_nan=False)
    env = {k:v for k,v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA',
        'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'}}
    # Windows venv/python.exe may launch a second interpreter. Start the base
    # interpreter directly with these SAME local dependencies, so timeout kills
    # the actual worker, not just a launcher which could leave it running.
    paths = [str(Path(__file__).resolve().parents[2]), sysconfig.get_paths()['purelib'],
             sysconfig.get_paths()['platlib']]
    env['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(paths))
    env['OMP_NUM_THREADS'] = env['OPENBLAS_NUM_THREADS'] = env['MKL_NUM_THREADS'] = '1'
    command = [getattr(sys, '_base_executable', sys.executable), '-m', 'goal_analysis.jobs.daily_native_shadow',
        '--root', str(root), '--run-id', run_id, '--report-hash', report_hash,
        '--as-of', utc(as_of).isoformat(), '--settings', str(settings_path)]
    try:
        remaining = max_seconds-(monotonic()-started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, max_seconds)
        result = subprocess.run(command, cwd=root, env=env, timeout=remaining,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'TIMEOUT', 'mode': 'SHADOW', 'max_seconds': max_seconds,
                'external_requests': 0, 'llm_requests': 0, 'live_decision_effect': False,
                'source_admission': admission, 'calendar_collection': calendars,
                'player_collection': players, 'next_grok_input': next_grok,
                'xg_forecast_capture': xg_capture,
                'xg_collection': xg_sources}
    if result.returncode:
        raise ValueError('DAILY_NATIVE_SHADOW_WORKER_FAILED')
    summary, analysis = _json(output/'summary.json'), _json(output/'analysis.json')
    if (summary['analysis_sha256'] != canonical_sha256(analysis)
            or summary['report_sha256'] != report_hash or summary['run_id'] != run_id
            or summary['mode'] != 'SHADOW' or summary['live_decision_effect'] is not False):
        raise ValueError('DAILY_NATIVE_SHADOW_RESULT_MISMATCH')
    captured = {'status': 'DISABLED', 'external_requests': 0, 'additional_model_fits': 0}
    try:
        from goal_analysis.jobs import native_forecast_capture

        if native_forecast_capture.enabled(root):
            spare = int(max_seconds-(monotonic()-started))
            captured = (native_forecast_capture.capture(root, run_id, report_hash, max_seconds=min(10, spare))
                if spare >= 1 else {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0,
                                   'additional_model_fits': 0})
    except Exception:  # noqa: BLE001 - optional research capture cannot veto frozen cards.
        captured = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_FORECAST_CAPTURE_FAILED',
                    'external_requests': 0, 'additional_model_fits': 0, 'automatic_retry': False}
    release = {'status': 'DISABLED', 'external_requests': 0}
    try:
        from goal_analysis.jobs import calibration_release

        if calibration_release.enabled(root):
            spare = int(max_seconds-(monotonic()-started))
            if not captured.get('receipt_id'):
                release = {'status': 'SKIPPED_NO_VERIFIED_CAPTURE', 'external_requests': 0}
            elif spare < 1:
                release = {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0}
            else:
                release = calibration_release.observe(root, run_id, max_seconds=min(5, spare))
    except Exception:  # noqa: BLE001 - candidate research cannot veto a frozen package.
        release = {'status': 'UNAVAILABLE', 'reason': 'RELEASE_SHADOW_OBSERVATION_FAILED',
                   'external_requests': 0, 'live_decision_effect': False, 'automatic_retry': False}
    weather_features = {'status': 'DISABLED', 'external_requests': 0}
    try:
        from goal_analysis.jobs import weather_feature_capture

        if weather_feature_capture.enabled(root):
            spare = int(max_seconds-(monotonic()-started))
            if not captured.get('receipt_id'):
                weather_features = {'status': 'SKIPPED_NO_VERIFIED_CAPTURE', 'external_requests': 0}
            elif spare < 1:
                weather_features = {'status': 'SKIPPED_NO_SPARE_WINDOW', 'external_requests': 0}
            else:
                weather_features = weather_feature_capture.capture(root, run_id, report_hash,
                    fixtures=enriched['fixtures'], max_seconds=min(30, spare))
    except Exception:  # noqa: BLE001 - feature collection cannot veto frozen cards.
        weather_features = {'status': 'UNAVAILABLE', 'reason': 'WEATHER_FEATURE_CAPTURE_FAILED',
            'external_requests': 0, 'additional_model_fits': 0, 'automatic_retry': False,
            'live_decision_effect': False}
    return {**summary, 'max_seconds': max_seconds, 'source_admission': admission,
            'xg_forecast_capture': xg_capture,
            'xg_collection': xg_sources,
            'next_grok_input': next_grok,
            'player_collection': players,
            'calendar_collection': calendars,
            'calibration_capture': captured,
            'weather_feature_capture': weather_features,
            'calibration_release_shadow': release,
            'artifact': str((output/'analysis.json').relative_to(root))}


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'run-id', 'report-hash', 'as-of', 'settings'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    worker(args.root, args.run_id, args.report_hash, args.as_of, args.settings)


if __name__ == '__main__':
    main()
