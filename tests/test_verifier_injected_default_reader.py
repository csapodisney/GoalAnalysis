"""Actual saved-cohort entry route respects implicit versus explicit byte policy."""
import importlib.util
import json
import sys
from pathlib import Path

from goal_analysis.agents.profile_source_access import SourceBundle, stage_source_bundle_files
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest, json_text
from goal_analysis.jobs.workshop_verify import SavedReader, verify_new_cohort

OLD_LIMIT = 805306368
V2_LIMIT = 3 * 1024**3
RUN = '2026-11-14-TESTONLY-default-reader'


def _saved_v2(root):
    journal = root / 'data/arthur/agent-decisions' / RUN
    folder = journal / 'source-access'
    folder.mkdir(parents=True)
    families = {name: {'status': 'UNKNOWN', 'reason': 'TESTONLY_MISSING_OPTIONAL',
                      'value': None, 'value_sha256': digest(None)} for name in FAMILIES}
    data = {'run_id': RUN, 'target_date': '2026-11-14', 'fixture_ids': ['FIRST', 'LAST'],
            'owner_ids': ['TESTONLY-owner-' + str(index) for index in range(10)],
            'families': families, 'source_manifest': {}, 'padding': 'p' * 16384}
    snapshot_id = digest(data)
    view = {'view': {'source_snapshot_id': snapshot_id}, 'fixture_ids': data['fixture_ids'],
            'owner_ids': data['owner_ids'], 'families': families}
    bundle = SourceBundle(json_text(data), json_text(view), snapshot_id, digest(view))
    manifest_path = folder / 'bundle.json'
    manifest = stage_source_bundle_files(bundle, manifest_path)
    stored = manifest_path.stat().st_size + sum(part['stored_bytes'] for part in manifest['parts'].values())
    receipt = {'storage_version': manifest['version'], 'snapshot_id': snapshot_id,
        'view_sha256': manifest['view_sha256'], 'bundle_sha256': digest(manifest),
        'bundle_bytes': stored, 'bundle_byte_limit': manifest['stored_limit'],
        'bundle_manifest_bytes': manifest_path.stat().st_size,
        'decoded_bytes': sum(part['decoded_bytes'] for part in manifest['parts'].values()),
        'decoded_byte_limit': manifest['decoded_limit']}
    (journal / 'source-access-bundle.json').write_text(json_text(receipt), encoding='utf-8')
    (journal / 'shared-data.json').write_text(json_text(data), encoding='utf-8')
    report = {'run_id': RUN, 'date': '2026-11-14', 'generated_tickets': [],
              'ten_agent_decisions': {'snapshot_id': snapshot_id}}
    report['report_sha256'] = digest(report)
    directory = root / 'reports/arthur' / RUN
    directory.mkdir(parents=True)
    (directory / 'report.json').write_text(json_text(report), encoding='utf-8')
    (directory / 'independent_agent_snapshot.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    return report, journal, directory, snapshot_id


def test_actual_verifier_cli_main_injected_default_selects_v2_policy(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    report, journal, _directory, snapshot_id = _saved_v2(root)
    direct = verify_new_cohort(root, report)
    raw_key = (journal / 'shared-data.json').relative_to(root).as_posix()
    assert direct['physical_files'][raw_key]['sha256'] == snapshot_id
    script = Path(__file__).resolve().parents[1] / 'scripts/run-arthur-workshop-verify.py'
    spec = importlib.util.spec_from_file_location('TESTONLY_verifier_cli', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    reader = SavedReader(root)
    assert reader.uses_default_byte_limit is True and reader.maximum_bytes == OLD_LIMIT
    reader.read_bytes = OLD_LIMIT - 8192  # aggregate edge, not a giant allocation
    owned = tmp_path / 'arthur-ten-TESTONLY'
    owned.mkdir()
    output = owned / 'physical-verification.json'
    monkeypatch.setattr(cli, 'ROOT', root)
    monkeypatch.setattr(cli, 'SavedReader', lambda root: reader)
    monkeypatch.setattr(sys, 'argv', [str(script), '--run-id', RUN, '--output', str(output)])
    hooks = []
    # Capture only this additional process-global registration. The outside-Root
    # FIRST harness audit guard is already active and is not removed or disabled.
    monkeypatch.setattr(sys, 'addaudithook', hooks.append)
    assert cli.main() == 0
    assert len(hooks) == 1 and callable(hooks[0])
    result = json.loads(output.read_text('utf-8'))
    assert reader.maximum_bytes == V2_LIMIT and reader.read_bytes > OLD_LIMIT
    assert result['physical_files'][raw_key]['sha256'] == snapshot_id
    assert result['optional_family_states'] == {
        name: {'status': 'UNKNOWN', 'reason': 'TESTONLY_MISSING_OPTIONAL'} for name in FAMILIES}
    assert result['status'] == direct['status'] == 'PARTIAL'
    assert not any(row.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND' for row in result['gaps'])
    assert result['new_model_or_tool_calls'] == 0 and result['owners'] == {}


def test_explicit_equal_or_smaller_caps_and_markerless_default_never_auto_expand(tmp_path):
    report, journal, _directory, _snapshot_id = _saved_v2(tmp_path)
    for maximum in (OLD_LIMIT, 4096):
        reader = SavedReader(tmp_path, maximum_bytes=maximum)
        assert reader.uses_default_byte_limit is False
        if maximum == OLD_LIMIT:
            reader.read_bytes = OLD_LIMIT - 8192
        result = verify_new_cohort(tmp_path, report, reader=reader)
        assert reader.maximum_bytes == maximum and result['status'] == 'PARTIAL'
        assert any(row.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND'
                   and row.get('verification_aborted') is True for row in result['gaps'])
        assert result['new_model_or_tool_calls'] == 0
    (journal / 'source-access-bundle.json').unlink()
    legacy = SavedReader(tmp_path)
    legacy.read_bytes = OLD_LIMIT - 8192
    result = verify_new_cohort(tmp_path, report, reader=legacy)
    assert legacy.uses_default_byte_limit is True and legacy.maximum_bytes == OLD_LIMIT
    assert result['status'] == 'PARTIAL'
    assert any(row.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND' for row in result['gaps'])
