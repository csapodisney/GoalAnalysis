"""Physical source equivalence and distinct finite storage policies."""
import hashlib
import json

import pytest

from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.workshop_verify import (
    SavedReader,
    SavedVerificationLimit,
    _saved_snapshot_pair,
    _saved_source_phase_binding,
    _v2_saved_source_limits,
)


def _files(tmp_path):
    journal = tmp_path / 'journal'
    folder = journal / 'source-access'
    folder.mkdir(parents=True)
    data = {'full_fixture_ids': ['a', 'z'], 'unknown': None, 'false': False, 'zero': 0}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    (journal / 'shared-data.json').write_bytes(raw)
    parts = {}
    for name, content in [('raw', b'physical-raw'), ('view', b'physical-view')]:
        (folder / (name + '.json.gz')).write_bytes(content)
        parts[name] = {'file': name + '.json.gz', 'stored_bytes': len(content),
            'stored_sha256': hashlib.sha256(content).hexdigest(),
            'decoded_bytes': len(raw) if name == 'raw' else 2,
            'decoded_sha256': digest(data) if name == 'raw' else hashlib.sha256(b'{}').hexdigest()}
    manifest = {'version': 'SOURCE_BUNDLE_GZIP_FILES_V2', 'snapshot_id': digest(data),
        'view_sha256': parts['view']['decoded_sha256'], 'stored_limit': 536870912,
        'decoded_limit': 1073741824, 'parts': parts}
    encoded = json.dumps(manifest).encode()
    (folder / 'bundle.json').write_bytes(encoded)
    receipt = {'storage_version': manifest['version'], 'snapshot_id': manifest['snapshot_id'],
        'view_sha256': manifest['view_sha256'], 'bundle_sha256': hashlib.sha256(encoded).hexdigest(),
        'bundle_bytes': len(encoded) + sum(p['stored_bytes'] for p in parts.values()),
        'bundle_byte_limit': 536870912, 'bundle_manifest_bytes': len(encoded),
        'decoded_bytes': len(raw) + 2, 'decoded_byte_limit': 1073741824}
    (journal / 'source-access-bundle.json').write_text(json.dumps(receipt))
    return journal, data, receipt


def test_v2_physical_pair_hashes_all_bytes_and_keeps_unknown_and_old_reader_limits(tmp_path):
    journal, data, _receipt = _files(tmp_path)
    reader = SavedReader(tmp_path)
    policy = _v2_saved_source_limits(reader, journal, digest(data), default_reader=True)
    assert reader.maximum_bytes == 3 * 1024**3
    _saved_snapshot_pair(reader, journal, data, digest(data), policy)
    raw_pin = reader.files['journal/shared-data.json']
    assert raw_pin['sha256'] == digest(data) and raw_pin['bytes'] == policy['raw_bytes']
    assert data['unknown'] is None and data['false'] is False and data['zero'] == 0
    smaller = SavedReader(tmp_path, maximum_bytes=4096)
    _v2_saved_source_limits(smaller, journal, digest(data), default_reader=False)
    assert smaller.maximum_bytes == 4096
    with pytest.raises(SavedVerificationLimit):
        _v2_saved_source_limits(SavedReader(tmp_path, maximum_bytes=1), journal,
                                digest(data), default_reader=False)
    (journal / 'source-access-bundle.json').unlink()
    legacy = SavedReader(tmp_path)
    assert _v2_saved_source_limits(legacy, journal, digest(data), default_reader=True) is None
    assert legacy.maximum_bytes == 805306368
    _saved_snapshot_pair(legacy, journal, data, digest(data), None)


def test_v2_forged_receipt_corrupt_part_and_same_size_raw_tamper_are_rejected(tmp_path):
    journal, data, receipt = _files(tmp_path)
    marker = journal / 'source-access-bundle.json'
    for change in [{'snapshot_id': 'f' * 64}, {'decoded_bytes': 1073741825},
                   {'decoded_byte_limit': True}, {'bundle_sha256': 'f' * 64}]:
        marker.write_text(json.dumps({**receipt, **change}))
        with pytest.raises(ValueError):
            _v2_saved_source_limits(SavedReader(tmp_path), journal, digest(data), default_reader=True)
    marker.write_text(json.dumps(receipt))
    path = journal / 'source-access/raw.json.gz'
    original = path.read_bytes()
    path.write_bytes(original[:-1] + b'X')
    with pytest.raises(ValueError):
        _v2_saved_source_limits(SavedReader(tmp_path), journal, digest(data), default_reader=True)
    path.write_bytes(original)
    reader = SavedReader(tmp_path)
    policy = _v2_saved_source_limits(reader, journal, digest(data), default_reader=True)
    raw = journal / 'shared-data.json'
    raw.write_bytes(raw.read_bytes().replace(b'"a"', b'"b"'))
    with pytest.raises(ValueError):
        _saved_snapshot_pair(reader, journal, data, digest(data), policy)

    proof = {'bundle_sha256': policy['manifest_sha256'], 'view_sha256': policy['view_sha256']}
    _saved_source_phase_binding(proof, policy)
    for field in ('bundle_sha256', 'view_sha256'):
        with pytest.raises(ValueError, match='PHYSICAL_PHASE_SOURCE_STORAGE_CHANGED'):
            _saved_source_phase_binding({**proof, field: 'f' * 64}, policy)
    _saved_source_phase_binding({}, None)
