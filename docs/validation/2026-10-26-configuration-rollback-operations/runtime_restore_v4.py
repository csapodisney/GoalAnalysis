"""Own accepted config drill and existing immutable report runtime restoration.

Transport functions are reused by SHA only. Its older main/acceptance/publication
authority is never called. Both config restores, runtime restoration and timer
hold are performed by one caller holding the SAME RootRunLock.
"""
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import time

import production_drill_v4 as drill
import preservation_reader_v4 as preservation

TRANSPORT_SHA = '7dc233b937929fd61b6aa8bbcbe7263ee844b9e549d6ebf22e0b1eec94418e72'


def transport(authority, output, execute=False, log_namespace=None):
    authority.accepted_validation()  # Before Windows/HTTP/runtime writes.
    path = Path(authority.ROOT) / 'artifacts/roadmap-execution-20261020/8.6.N2/followup-v4-scope/runtime_restore.py'
    assert drill.sha(path) == TRANSPORT_SHA
    spec = importlib.util.spec_from_file_location('rollback_transport_functions_only', path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if execute:
        namespace = log_namespace or 'operations-v4-transport'
        assert namespace in {'operations-v4-transport', 'operations-v4-preflight-transport'}
        logroot = Path(output) / namespace
        logroot.mkdir(exist_ok=False)
        frozen = Path(authority.HERE) / 'source-under-test-active.json'
        with (logroot / frozen.name).open('xb') as stream:
            stream.write(frozen.read_bytes())
        assert drill.sha(logroot / frozen.name) == drill.sha(frozen)
        module.HERE = logroot
    else:
        module.HERE = Path(output)
    module.LOG_OPERATIONS = execute
    return module


def restore_locked(authority, lock, output, channel):
    authority.accepted_validation()
    root, output = Path(authority.ROOT).resolve(), Path(output).resolve()
    drill.require_lock(root, lock)
    proof = drill.read(output / 'production-drill-proof.json')
    assert proof['status'] == 'OWN_ACCEPTED_PRODUCTION_PRIOR_CURRENT_CONFIG_RESTORE_AND_COMMITTED_DATA_VERIFIED'
    assert proof['acceptance_sha256'] == drill.sha(output / 'active-final-acceptance.json')
    assert proof['restores']['prior_observed_shadow_current_exact_active_restored']
    assert proof['old_report_sha256'] == drill.REFERENCE_REPORT_SHA256
    assert proof['old_report_bytes_sha256'] == drill.REFERENCE_REPORT_BYTES_SHA256
    assert not (output / 'runtime-start.json').exists(), 'Already attempted; no retry'
    frozen = authority.current_fingerprints()
    channel.activation_preflight()
    before = channel.windows_snapshot()
    assert before['tasks']['daily']['enabled'] is False and before['tasks']['daily']['state'] != 'Running'
    assert before['tasks']['legacy'] and all(not r['enabled'] and r['state'] != 'Running' for r in before['tasks']['legacy'])
    report = root / 'reports/arthur' / drill.REFERENCE_RUN / 'report.json'
    assert drill.sha(report) == drill.REFERENCE_REPORT_BYTES_SHA256
    view = {'run_id': drill.REFERENCE_RUN, 'report_sha256': drill.REFERENCE_REPORT_SHA256,
            'date': '2026-10-26'}
    protected = ('data/learning/learning.sqlite3', 'data/team/team.sqlite3')
    stores = {n: drill.sha(root / n) for n in protected}
    drill.save_new(output / 'runtime-start.json', {
        'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'attempt': 'followup-v2',
        'own_acceptance_sha256': drill.sha(output / 'active-final-acceptance.json'),
        'own_production_drill_proof_sha256': drill.sha(output / 'production-drill-proof.json'),
        'before': before, 'transport_sha256': TRANSPORT_SHA,
        'existing_immutable_report': view, 'new_generation': False, 'automatic_retry': False})
    try:
        operation = channel.restart_dashboard(before['dashboard'])
        for _ in range(25):
            try:
                channel.http(channel.LOCAL + '/')
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError('Dashboard restart finite readiness bound reached')
        serving = channel.verify_serving(view)
        after = channel.windows_snapshot()
        assert after['gateway'] == before['gateway']
        assert after['dashboard']['pid'] != before['dashboard']['pid']
        from goal_analysis.normalization.canonical import utc
        latest = max(p.stat().st_mtime for p in (root / 'src').rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
        assert utc(after['dashboard']['created_at']) >= datetime.fromtimestamp(latest, UTC)
        current = authority.current_fingerprints()
        assert current['files'] == frozen['files'] and current['ledger_tables'] == frozen['ledger_tables']
        assert {n: drill.sha(root / n) for n in protected} == stores
        assert drill.sha(report) == drill.REFERENCE_REPORT_BYTES_SHA256
        channel.activation_preflight()
        tasks = channel.enable_daily()
        restored = channel.windows_snapshot()
        assert restored['tasks']['daily']['enabled'] is True
        assert restored['gateway'] == before['gateway'] and restored['dashboard'] == after['dashboard']
        assert all(not r['enabled'] for r in tasks['legacy'])
        drill.save_new(output / 'runtime-proof.json', {
            'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'attempt': 'followup-v2',
            'status': 'OWN_VALIDATED_CONFIGURATION_DRILL_AND_EXISTING_REPORT_RUNTIME_RESTORED',
            'own_acceptance_sha256': drill.sha(output / 'active-final-acceptance.json'),
            'own_drill_proof_sha256': drill.sha(output / 'production-drill-proof.json'),
            'serving': serving, 'serving_run_id': drill.REFERENCE_RUN,
            'serving_report_sha256': drill.REFERENCE_REPORT_SHA256,
            'before': before, 'after': restored, 'operation': operation,
            'daily_enabled': True, 'legacy_enabled': False,
            'source_and_4016_prior_ledger_rows_unchanged': True,
            'learning_team_databases_unchanged': True, 'gateway_identity_preserved': True,
            'both_drill_restores_and_runtime_same_root_runlock': True,
            'new_generation_or_model_fit_or_sports_request': 0,
            'no_new_quote_or_current_bettability_claim': True})
        held = channel.disable_daily_after_failure()
        final = channel.windows_snapshot()
        assert held['daily_enabled'] is False and final['tasks']['daily']['enabled'] is False
        assert final['gateway'] == before['gateway'] and final['dashboard'] == after['dashboard']
        assert all(not r['enabled'] and r['state'] != 'Running' for r in final['tasks']['legacy'])
        drill.save_new(output / 'next-step-scheduler-hold.json', {
            'at': datetime.now(UTC).isoformat(), 'task': 'Arthur Goal Analysis Daily',
            'daily_enabled': False, 'legacy_enabled': False, 'restore_required': True,
            'previous_enabled': True, 'same_root_runlock_as_drill_and_runtime': True,
            'next_task': 'sensitivity_note', 'runtime_proof_sha256': drill.sha(output / 'runtime-proof.json')})
        preserved_after = preservation.snapshot(root)
        drill.save_new(output / 'production-data-after-runtime.json', preserved_after)
        unchanged = preservation.compare(drill.read(output / 'production-data-after.json'), preserved_after)
        authority.current_fingerprints()
        drill.save_new(output / 'runtime-data-preservation.json', {
            'at': datetime.now(UTC).isoformat(), 'status': 'ALL_RAW_FILES_AND_COMMITTED_SQLITE_ROWS_UNCHANGED_AFTER_RUNTIME',
            'data_after_runtime_sha256': drill.sha(output / 'production-data-after-runtime.json'),
            'comparison': unchanged})
        return drill.read(output / 'runtime-proof.json')
    except BaseException as error:
        failure = {'at': datetime.now(UTC).isoformat(), 'failure_class': type(error).__name__,
                   'automatic_retry': False}
        try:
            failure['safe_daily_hold'] = channel.disable_daily_after_failure()
        except BaseException as hold_error:
            failure['hold_failure_class'] = type(hold_error).__name__
        drill.save_new(output / 'runtime-failure.json', failure)
        raise
