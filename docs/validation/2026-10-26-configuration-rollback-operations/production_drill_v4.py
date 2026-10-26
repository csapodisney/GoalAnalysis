"""Single config-only production drill, gated by the caller's OWN acceptance.

No import or default CLI action applies a configuration. The closure caller
must supply its own authority, output directory and an already held RootRunLock.
Network and subprocess construction are forbidden during the two restores.
"""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch

from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import load_settings
from goal_analysis.jobs import configuration_rollback as job
from goal_analysis.jobs.daily_review_cohort import policy as daily_policy
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.tipsters.coverage_search import policy as coverage_policy

import preservation_reader_v4 as preservation_reader


REFERENCE_RUN = '2026-10-26-cc3b72cd5b81'
REFERENCE_REPORT_SHA256 = '916089cc9a91372873e6cdce61ff768d3930b1658a799cf56c1a09d373c326ce'
REFERENCE_REPORT_BYTES_SHA256 = '5592bf755cdc07c56969073d711b9c253107877bf53409e2dbe2dce06b7433be'
PRIOR_RUN = '2026-10-25-3472f0b6fb86'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def verify_reference_report(root):
    report = Path(root) / 'reports/arthur' / REFERENCE_RUN / 'report.json'
    assert sha(report) == REFERENCE_REPORT_BYTES_SHA256
    value = read(report)
    assert value['report_sha256'] == REFERENCE_REPORT_SHA256
    return report, value


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def forbidden(*_args, **_kwargs):
    raise RuntimeError('CONFIGURATION_DRILL_FORBIDS_NETWORK_LLM_AND_CHILD_PROCESSES')


def require_lock(root, lock):
    if (not isinstance(lock, RunLock) or lock.stream is None or lock.stream.closed
            or lock.path.resolve() != (root / 'data/arthur/run.lock').resolve()
            or lock.configuration_recovery):
        raise RuntimeError('CONFIGURATION_DRILL_REQUIRES_HELD_NORMAL_ROOT_RUNLOCK')


def roundtrip_locked(root, current, previous, prior_provenance, output, lock):
    """Exact two-restore core; caller supplies a validated root and shared lock."""
    root, output = Path(root).resolve(), Path(output).resolve()
    require_lock(root, lock)
    if job._read_configuration(root) != current:
        raise RuntimeError('CONFIGURATION_DRILL_CURRENT_BYTE_MISMATCH')
    if list(n for n in job.FILES if previous[n] != current[n]) != list(job.FILES[-2:]):
        raise RuntimeError('CONFIGURATION_DRILL_UNEXPECTED_PRIOR_DELTA')
    if any(previous[n] is not None for n in job.FILES[-2:]):
        raise RuntimeError('CONFIGURATION_DRILL_PRIOR_ABSENCE_REQUIRED')
    if coverage_policy(root) != 'ACTIVE' or daily_policy(root) != 'ACTIVE':
        raise RuntimeError('CONFIGURATION_DRILL_CURRENT_POLICY_NOT_ACTIVE')
    current_settings = load_settings(root / 'config/arthur-settings.json')
    current_live = validate_live_config(read(root / 'config/daily223-live.json'))
    # These seals are confined to backups/, never data/, reports/ or source.
    saved = job._seal(root, current, {'kind': 'OWN_ACCEPTED_PRE_DRILL_CURRENT_CONFIGURATION',
                                     'reference_run': REFERENCE_RUN})
    old = job._seal(root, previous, prior_provenance)
    save_new(output / 'production-drill-intent.json', {
        'at': datetime.now(UTC).isoformat(), 'task': '11.7',
        'status': 'OWN_ACCEPTANCE_CHECKED_CURRENT_BUNDLE_SEALED_BEFORE_PRIOR_RESTORE',
        'current_release_id': saved['release_id'], 'current_configuration_id': saved['configuration_id'],
        'prior_release_id': old['release_id'], 'prior_configuration_id': old['configuration_id'],
        'normal_daily_must_remain_off': True, 'provider_or_llm_or_generation': 0})
    prior_result = current_result = None
    with patch.object(socket, 'socket', side_effect=forbidden), \
            patch.object(subprocess, 'Popen', side_effect=forbidden):
        try:
            prior_result = job._restore_locked(root, old['release_id'],
                                               expected_configuration_id=saved['configuration_id'])
            assert prior_result['status'] == 'RESTORED'
            assert job._read_configuration(root) == previous
            assert coverage_policy(root) == daily_policy(root) == 'SHADOW'
            assert load_settings(root / 'config/arthur-settings.json') == current_settings
            assert validate_live_config(read(root / 'config/daily223-live.json')) == current_live
            save_new(output / 'production-prior-configuration-observed.json', {
                'at': datetime.now(UTC).isoformat(), 'status': 'ACTUAL_PRIOR_ABSENCE_AND_SHADOW_MODES_READ_BACK',
                'restore_receipt': prior_result,
                'effective_modes': {'club_fixture_coverage': 'SHADOW', 'daily_review_cohort': 'SHADOW'},
                'model_settings_equal_pre_drill': True, 'live_settings_equal_pre_drill': True,
                'both_optional_configurations_physically_absent': all(
                    not (root / 'config' / n).exists() for n in job.FILES[-2:]),
                'prior_does_not_change_models_or_retirements': True})
        finally:
            # Known CAS only. An unrelated external edit is never overwritten.
            now = job._read_configuration(root)
            if now == previous:
                current_result = job._restore_locked(root, saved['release_id'],
                                                     expected_configuration_id=old['configuration_id'])
            elif now != current:
                raise RuntimeError('CONFIGURATION_DRILL_EXTERNAL_EDIT_REQUIRES_INSPECTION')
        assert job._read_configuration(root) == current
        assert coverage_policy(root) == daily_policy(root) == 'ACTIVE'
        assert current_result and current_result['status'] == 'RESTORED'
        assert job._restore_locked(root, saved['release_id'],
                    expected_configuration_id=saved['configuration_id'])['status'] == 'ALREADY_CURRENT'
        job.ensure_consistent(root)
    return {'prior_restore': prior_result, 'current_restore': current_result,
            'current_release_id': saved['release_id'], 'prior_release_id': old['release_id'],
            'prior_observed_shadow_current_exact_active_restored': True,
            'model_and_live_settings_equal': True, 'both_restores_same_root_runlock': True,
            'external_requests': 0, 'llm_requests': 0, 'generation': 0}


def drill_locked(authority, lock, output, maintenance_reader):
    """Production entry; own completed acceptance and current source are mandatory."""
    accepted = authority.accepted_validation()
    assert accepted['task'] == '11.7' and accepted['full_tests_passed'] == 5776
    assert accepted['targeted_tests_passed'] == 75 and accepted['actual_replays_passed'] == 3
    assert accepted['adapter_tests_passed'] == 10 and accepted['checks_per_case'] == 8
    assert accepted['old_ledger_rows_unchanged'] == 4016 and accepted['published'] is False
    root = Path(authority.ROOT).resolve()
    execution = root / 'artifacts/roadmap-execution-20261020'
    output = Path(output).resolve()
    assert output.parent == execution / '11.7' or output == execution / '11.7'
    require_lock(root, lock)
    maintenance = maintenance_reader()
    assert maintenance['tasks']['daily']['enabled'] is False
    assert maintenance['tasks']['daily']['state'] != 'Running'
    legacy = maintenance['tasks']['legacy']
    assert isinstance(legacy, list) and legacy
    assert all(t['enabled'] is False and t['state'] != 'Running' for t in legacy)
    save_new(output / 'operations-v4-maintenance-before.json', maintenance)
    assert not (output / 'production-drill-intent.json').exists(), 'Already attempted; do not retry'
    authority.current_fingerprints()
    full = read(output / 'acceptance-independent-readback.json')
    assert full['task'] == '11.7' and full['full_tests_passed'] == 5776
    assert full['acceptance_sha256'] == sha(output / 'active-final-acceptance.json')
    report, reference_value = verify_reference_report(root)
    assert reference_value['settings'] == load_settings(root / 'config/arthur-settings.json')
    frozen = read(output / 'source-under-test-active.json')
    prior_path = execution / '7.4.N6/source-under-test-active.json'
    prior = read(prior_path)
    current = job._read_configuration(root)
    previous = {}
    for name, raw in current.items():
        assert raw is not None and hashlib.sha256(raw).hexdigest() == frozen['files']['config/' + name]
        expected = prior['files'].get('config/' + name)
        if expected is None:
            assert name in job.OPTIONAL
            previous[name] = None
        else:
            assert hashlib.sha256(raw).hexdigest() == expected
            previous[name] = raw
    # Both complete data inventories are persisted outside data/ and reports/.
    before = preservation_reader.snapshot(root)
    save_new(output / 'production-data-before.json', before)
    result = roundtrip_locked(root, current, previous, {
        'kind': 'ACTUAL_ACCEPTED_N6_CONFIGURATION_MANIFEST_EXACT_BYTES_AND_ABSENCE',
        'prior_run': PRIOR_RUN, 'manifest_sha256': sha(prior_path)}, output, lock)
    after = preservation_reader.snapshot(root)
    save_new(output / 'production-data-after.json', after)
    preserved = preservation_reader.compare(before, after)
    authority.current_fingerprints()
    plan = read(output / 'acceptance-plan.json')
    assert all(sha(root / n) == h for n, h in plan['preserved_archive_files'].items())
    assert sha(report) == REFERENCE_REPORT_BYTES_SHA256
    proof = {'at': datetime.now(UTC).isoformat(), 'task': '11.7',
             'status': 'OWN_ACCEPTED_PRODUCTION_PRIOR_CURRENT_CONFIG_RESTORE_AND_COMMITTED_DATA_VERIFIED',
             'acceptance_sha256': sha(output / 'active-final-acceptance.json'),
             'data_before_sha256': sha(output / 'production-data-before.json'),
             'data_after_sha256': sha(output / 'production-data-after.json'),
             'restores': result, 'data_preservation': preserved,
             'old_report_sha256': REFERENCE_REPORT_SHA256,
             'old_report_bytes_sha256': REFERENCE_REPORT_BYTES_SHA256,
             'source_acceptance_attempt': 'followup-v2', 'operation_attempt': 'operations-v4',
             'no_new_quote_or_current_bettability_claim': True,
             'runtime_restored': False, 'task_complete': False, 'project_complete': False}
    save_new(output / 'production-drill-proof.json', proof)
    return proof
