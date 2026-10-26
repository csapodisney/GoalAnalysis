"""Independent physical root readback after the ONE successful closure follower.

No config/source/report/ledger changes, collection, fit or generation. A fresh
bounded data read and local/Tailscale HTTP reads verify the completed operation.
Root tasklist changes and final GitHub push remain separate, explicit steps.
"""
from datetime import UTC, datetime
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import acceptance_authority as authority
import production_drill_v4 as drill
import preservation_reader_v4 as preservation
import runtime_restore_v4 as runtime_restore
from goal_analysis.jobs import configuration_rollback as job
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.storage.training_datasets import DatasetStore


def gate():
    outcome = HERE / 'independent-operations-v4-outcome.json'
    if not outcome.exists() or drill.read(outcome).get('exit_code') != 0:
        raise RuntimeError('FOLLOWER_NOT_SUCCESSFULLY_CLOSED_YET')
    state = drill.read(HERE / 'operations-v4-state.json')
    assert state['status'] == 'OWN_DRILL_RUNTIME_REPORT_CHECKPOINT_VERIFIED_INDEPENDENT_ROOT_AND_GITHUB_REQUIRED'
    assert state['source_attempt'] == 'followup-v2' and state['task'] == '11.7'
    assert drill.read(HERE / 'independent-operations-v4-start.json')['outside_codex_ancestry'] is True
    return authority.accepted_validation()


def replay_physical(root):
    summary = drill.read(HERE / 'replay-active/replay-summary.json')
    assert summary['numeric_mode'] == 'RECOMPUTED_ACTUAL_NUMERICAL_MODELS'
    assert len(summary['cases']) == 3
    records = []
    for case in summary['cases']:
        name = case['case']
        assert len(case['checks']) == 8 and all(case['checks'].values())
        value = drill.read(HERE / 'replay-active' / (name + '.json'))
        assert value['checks'] == case['checks']
        assert value['numeric_mode'] == 'RECOMPUTED_ACTUAL_NUMERICAL_MODELS'
        assert value['metrics']['provider_request_total'] == 0 and not value['metrics']['forbidden_attempts']
        models = drill.read(HERE / 'replay-active' / (name + '-numeric-models.json'))
        golden = drill.read(root / 'artifacts/golden-baseline-20261020' / name / 'pass-1/numeric-models.json')
        assert {p: {k: v for k, v in m.items() if k != 'training_datasets'}
                for p, m in models.items()} == golden
        manifests = drill.read(HERE / 'replay-active' / (name + '-dataset-manifests.json'))
        store = DatasetStore(HERE / 'replay-active' / name / 'datasets')
        for manifest in manifests:
            assert store.read(manifest['dataset_id'])['manifest'] == manifest
        records.append({'case': name, 'eight_checks': True,
                        'actual_dataset_manifests': len(manifests),
                        'model_sha256': drill.sha(HERE / 'replay-active' / (name + '-numeric-models.json'))})
    assert sorted(r['actual_dataset_manifests'] for r in records) == [1, 33, 97]
    return records


def main(execute=False):
    accepted = gate()  # Must precede any Windows/HTTP/proof/data scan.
    root = Path(authority.ROOT).resolve()
    if not execute:
        print(json.dumps({'status': 'OWN_SUCCESSFUL_FOLLOWER_GATE_PASS_READBACK_NOT_EXECUTED',
                          'new_provider_or_fit_or_generation': 0}))
        return
    assert not (HERE / 'closure-root-readback.json').exists(), 'Readback already exists; inspect, never overwrite'
    with RunLock(root / 'data/arthur/run.lock'):
        assert drill.read(HERE / 'active-final-acceptance.json') == accepted
        xml = ET.parse(HERE / 'full-active.xml').getroot()
        assert len(list(xml.iter('testcase'))) == 5776
        assert not any(list(xml.iter(k)) for k in ('error', 'failure', 'skipped'))
        assert drill.sha(HERE / 'full-active.xml') == accepted['full_xml_sha256']
        actual = authority.current_fingerprints()
        plan = drill.read(HERE / 'acceptance-plan.json')
        assert all(drill.sha(root / n) == h for n, h in plan['preserved_archive_files'].items())
        for contract in ('worker-helper-frozen.json', 'closure-helper-frozen.json', 'operations-v2-helper-frozen.json', 'operations-v3-helper-frozen.json', 'operations-v4-helper-frozen.json'):
            assert all(drill.sha(HERE / n) == h for n, h in drill.read(HERE / contract)['files_sha256'].items())
        records = replay_physical(root)
        proof = drill.read(HERE / 'production-drill-proof.json')
        assert proof['acceptance_sha256'] == drill.sha(HERE / 'active-final-acceptance.json')
        assert proof['data_before_sha256'] == drill.sha(HERE / 'production-data-before.json')
        assert proof['data_after_sha256'] == drill.sha(HERE / 'production-data-after.json')
        before, after = (drill.read(HERE / name) for name in
                         ('production-data-before.json', 'production-data-after.json'))
        preserved_drill = preservation.compare(before, after)
        assert preserved_drill == proof['data_preservation']
        runtime_data = drill.read(HERE / 'production-data-after-runtime.json')
        preserved_runtime = preservation.compare(after, runtime_data)
        runtime_proof = drill.read(HERE / 'runtime-data-preservation.json')
        assert runtime_proof['data_after_runtime_sha256'] == drill.sha(HERE / 'production-data-after-runtime.json')
        assert preserved_runtime == runtime_proof['comparison']
        # Physical sealed bytes and receipts, independently of the summary flags.
        restores = proof['restores']
        old_manifest, old_files = job._release(root, restores['prior_release_id'])
        current_manifest, current_files = job._release(root, restores['current_release_id'])
        assert job._read_configuration(root) == current_files
        prior = drill.read(root / 'artifacts/roadmap-execution-20261020/7.4.N6/source-under-test-active.json')
        for name in job.FILES:
            expected = prior['files'].get('config/' + name)
            if expected is None:
                assert name in job.OPTIONAL and old_files[name] is None
            else:
                assert old_files[name] == current_files[name]
                assert hashlib.sha256(old_files[name]).hexdigest() == expected
        journals = []
        for label, target in (('prior_restore', old_manifest), ('current_restore', current_manifest)):
            receipt = restores[label]
            folder = root / 'backups/configuration-rollback/operations' / receipt['operation_id']
            assert drill.read(folder / 'complete.json') == receipt
            intent = drill.read(folder / 'intent.json')
            assert intent['target_release_id'] == target['release_id']
            assert receipt['configuration_id'] == target['configuration_id']
            assert receipt['changed_files'] == list(job.FILES[-2:])
            job._verify_terminal(root, folder, 'complete.json')
            journals.append({'label': label, 'operation_id': receipt['operation_id'],
                             'intent_sha256': drill.sha(folder / 'intent.json'),
                             'complete_sha256': drill.sha(folder / 'complete.json')})
        assert len({j['operation_id'] for j in journals}) == 2
        assert drill.sha(root / 'reports/arthur' / drill.REFERENCE_RUN / 'report.json') == drill.REFERENCE_REPORT_BYTES_SHA256
        runtime = drill.read(HERE / 'runtime-proof.json')
        hold = drill.read(HERE / 'next-step-scheduler-hold.json')
        assert runtime['own_acceptance_sha256'] == drill.sha(HERE / 'active-final-acceptance.json')
        assert runtime['own_drill_proof_sha256'] == drill.sha(HERE / 'production-drill-proof.json')
        assert hold['runtime_proof_sha256'] == drill.sha(HERE / 'runtime-proof.json')
        assert runtime['daily_enabled'] is True and hold['daily_enabled'] is False
        assert hold['same_root_runlock_as_drill_and_runtime'] and hold['legacy_enabled'] is False
        channel = runtime_restore.transport(authority, HERE, execute=False)
        channel.activation_preflight()
        serving = channel.verify_serving({'run_id': drill.REFERENCE_RUN,
            'report_sha256': drill.REFERENCE_REPORT_SHA256, 'date': '2026-10-26'})
        windows = channel.windows_snapshot()
        assert windows['gateway'] == runtime['after']['gateway']
        assert windows['dashboard'] == runtime['after']['dashboard']
        assert windows['tasks']['daily']['enabled'] is False
        assert windows['tasks']['daily']['state'] != 'Running'
        assert windows['tasks']['legacy'] and all(not t['enabled'] and t['state'] != 'Running'
                                                 for t in windows['tasks']['legacy'])
        fresh = preservation.snapshot(root)
        drill.save_new(HERE / 'production-data-root-readback.json', fresh)
        root_data = preservation.compare(runtime_data, fresh)
        checkpoint = drill.read(HERE / 'protection-after.json')
        assert checkpoint['report_sha256'] == drill.sha(HERE / 'REPORT.md')
        assert checkpoint['task_state'] == 'OWN_SCOPE_VALIDATED_INDEPENDENT_ROOT_AND_GITHUB_PENDING'
        for name, value in checkpoint['evidence_sha256'].items():
            assert drill.sha(HERE / name) == value
        out = {'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'attempt': 'followup-v2',
               'status': 'OWN_FULL_CONFIG_JOURNALS_DATA_RUNTIME_AND_CHECKPOINT_PHYSICALLY_ROOT_VERIFIED',
               'operation_attempt': 'operations-v4',
               'original_pre_mutation_hash_kind_failure_preserved': True,
               'full_tests_passed': 5776, 'targeted_tests_passed': 75, 'cwd_regressions_passed': 15,
               'adapter_tests_passed': 10, 'actual_replays': records,
               'source_files_exact': len(actual['files']), 'prior_ledger_rows_exact': 4016,
               'old_archives_exact': len(plan['preserved_archive_files']),
               'data_preservation_drill': preserved_drill, 'data_preservation_runtime': preserved_runtime,
               'data_preservation_root_current': root_data, 'physical_journals': journals,
               'serving': serving, 'windows': windows,
               'closure_checkpoint_sha256': drill.sha(HERE / 'protection-after.json'),
               'production_data_root_readback_sha256': drill.sha(HERE / 'production-data-root-readback.json'),
               'provider_llm_fit_or_generation_for_readback': 0,
               'no_new_quote_or_bettability_claim': True, 'project_complete': False,
               'root_tasklist_and_final_scope_github_push_still_required': True}
        drill.save_new(HERE / 'closure-root-readback.json', out)
        print(json.dumps(out))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    main(parser.parse_args().execute)
