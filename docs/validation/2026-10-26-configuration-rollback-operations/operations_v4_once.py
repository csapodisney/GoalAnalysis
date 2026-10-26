"""Once-only helper-fixed config operation on the SAME accepted v2 source.

No full test/refit/generation is repeated: no app/config/test byte changed.
The original failed operation and its16-pinned helpers remain immutable.
"""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import acceptance_authority as authority
import production_drill_v4 as drill
import runtime_restore_v4 as runtime
import close_checkpoint_v4 as close_checkpoint
from goal_analysis.jobs.portfolio import RunLock


def state(status, **extra):
    (HERE / 'operations-v4-state.json').write_text(json.dumps({
        'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'source_attempt': 'followup-v2',
        'operation_attempt': 'operations-v4', 'pid': os.getpid(), 'status': status,
        'automatic_retry': False, 'new_provider_llm_fit_or_generation': 0, **extra}, indent=2) + '\n', encoding='utf-8')


def check_contracts():
    for filename in ('worker-helper-frozen.json', 'closure-helper-frozen.json', 'operations-v2-helper-frozen.json', 'operations-v3-helper-frozen.json', 'operations-v4-helper-frozen.json'):
        assert all(drill.sha(HERE / n) == h for n, h in drill.read(HERE / filename)['files_sha256'].items())


def main():
    assert os.environ.get('ARTHUR_CONFIGURATION_ROLLBACK_OPERATIONS_V4_SCHEDULED_WORKER') == '1'
    assert drill.read(HERE / 'independent-operations-v4-start.json')['outside_codex_ancestry'] is True
    assert drill.read(HERE / 'closure-worker-state.json')['status'] == 'STOPPED_INSPECT_OWN_VERSION_EVIDENCE_NO_RETRY'
    assert drill.read(HERE / 'independent-closure-outcome.json')['exit_code'] == 1
    assert drill.read(HERE / 'operations-v2-state.json')['status']=='STOPPED_INSPECT_OWN_OPERATION_VERSION_EVIDENCE_NO_RETRY'
    assert drill.read(HERE / 'independent-operations-v2-outcome.json')['exit_code']==1
    assert drill.read(HERE / 'operations-v3-state.json')['detail']=='PRESERVATION_READER_ROW_BOUND'
    assert drill.read(HERE / 'independent-operations-v3-outcome.json')['exit_code']==1
    assert not (HERE / 'production-drill-intent.json').exists(), 'A production restore was already attempted; inspect only'
    assert drill.read(HERE / 'operations-initial-hash-kind-diagnosis.json')['production_config_or_bundle_writes_before_failure'] == 0
    check_contracts()
    accepted = authority.accepted_validation()
    assert accepted['attempt'] == 'followup-v2' and accepted['full_tests_passed'] == 5776
    assert drill.read(HERE / 'acceptance-independent-readback.json')['acceptance_sha256'] == drill.sha(HERE / 'active-final-acceptance.json')
    state('OWN_ACCEPTED5776_SOURCE_UNCHANGED_ONE_HASH_FIXED_CONFIG_DRILL_RUNTIME_RUNNING')
    root = Path(authority.ROOT)
    with RunLock(root / 'data/arthur/run.lock') as lock:
        authority.current_fingerprints()
        channel = runtime.transport(authority, HERE, execute=True)
        channel.activation_preflight()
        drill.drill_locked(authority, lock, HERE, channel.windows_snapshot)
        runtime.restore_locked(authority, lock, HERE, channel)
        close_checkpoint.write_locked(authority, lock, HERE)
        check_contracts()
        drill.save_new(HERE / 'operations-v4-complete.json', {
            'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'source_attempt': 'followup-v2',
            'operation_attempt': 'operations-v4',
            'status': 'HASH_KIND_FIXED_PRODUCTION_DRILL_RUNTIME_AND_OWN_CHECKPOINT_VERIFIED',
            'acceptance_sha256': drill.sha(HERE / 'active-final-acceptance.json'),
            'checkpoint_sha256': drill.sha(HERE / 'protection-after.json'),
            'original_failed_operation_preserved': True,
            'operations_v2_namespace_collision_preserved': True,
            'application_config_test_source_unchanged_since_full5776': True,
            'independent_root_and_github_pending': True})
    state('OWN_DRILL_RUNTIME_REPORT_CHECKPOINT_VERIFIED_INDEPENDENT_ROOT_AND_GITHUB_REQUIRED',
          production_drill_verified=True, runtime_restored=True,
          daily_enabled=False, legacy_enabled=False, project_complete=False)


if __name__ == '__main__':
    assert not (HERE / 'operations-v4-state.json').exists(), 'Already attempted; no retry'
    try:
        main()
    except BaseException as error:
        state('STOPPED_INSPECT_OWN_OPERATION_VERSION_EVIDENCE_NO_RETRY',
              failure_class=type(error).__name__, detail=str(error))
        raise
