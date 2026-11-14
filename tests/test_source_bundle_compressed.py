"""TESTONLY codec/storage and actual pure source proof; no native CLI or provider."""
import gzip
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents import profile_source_access as source
from goal_analysis.agents import profile_source_stdio
from goal_analysis.agents.profile_source_host import SourceAccessScope, validate_source_evidence


def _bundle(extra=''):
    raw = {'owner_ids': [f'TESTONLY:owner:{i}' for i in range(10)],
        'fixture_ids': ['TESTONLY:f'], 'families': {name: {'status': 'UNKNOWN',
        'value': None, 'value_sha256': source.value_hash(None)} for name in source.FAMILY_NAMES}}
    odds = {'candidate_input': {'candidates': [{'candidate_id': 'TESTONLY:c',
        'fixture_id': 'TESTONLY:f', 'clock': None, 'unknown': 'UNKNOWN', 'false': False,
        'zero': 0, 'unicode': '\u0151\U0001f642', 'extra': extra}]}}
    raw['families']['odds_and_price_history'] = {'status': 'AVAILABLE', 'value': odds,
        'value_sha256': source.value_hash(odds)}
    view = {**deepcopy(raw), 'view': {'source_snapshot_id': source.value_hash(raw)}}
    return source.SourceBundle(source.canonical_json(raw), source.canonical_json(view),
                               source.value_hash(raw), source.value_hash(view))


def _stage(tmp_path, bundle):
    tmp_path.mkdir(parents=True)
    path = tmp_path / 'bundle.json'
    document = source.stage_source_bundle_files(bundle, path)
    return path, document


def _scope(path, bundle):
    kernel, server = Path(source.__file__), Path(profile_source_stdio.__file__)
    binding = {'agent_id': 'TESTONLY:owner:0', 'session_id': 'TESTONLY:session',
               'snapshot_id': bundle.snapshot_id, 'phase': 'PLAN'}
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    scope = SourceAccessScope(path.parent, path, sha(path), binding, Path(sys.executable),
        server, sha(server), kernel, sha(kernel), cached_bundle=bundle)
    return scope, {**binding, 'data': {'fixture_ids': ['TESTONLY:f']}}


def test_v2_roundtrip_cached_roots_real_selected_read_proof_and_inline_compatibility(tmp_path):
    original = _bundle('repeated TESTONLY ' * 200)
    path, document = _stage(tmp_path / 'v2', original)
    loaded = source.load_source_bundle_document(document, path)
    assert loaded.as_dict() == original.as_dict()
    assert source.load_source_bundle_document(original.as_dict(), path).as_dict() == original.as_dict()
    assert source.load_source_bundle_document(document, path, cached_bundle=loaded) is loaded
    detached = loaded.data('raw')
    detached['families']['weather']['status'] = 'WRONG'
    assert loaded.data('raw')['families']['weather']['status'] == 'UNKNOWN'
    scope, request = _scope(path, loaded)
    answer = {**scope.identity(), 'options': []}
    returned = finish_fake_source_reply(scope, request, ['TESTONLY:c'], answer)
    proof = returned.evidence()
    assert returned.reply() == answer
    assert validate_source_evidence(proof, request=request, reply=answer, bundle=original)['read_count'] == 1
    source.require_candidate_reads(proof['journal_records'], candidate_ids=['TESTONLY:c'], bundle=loaded)
    assert len(loaded.data('raw')['families']) == 12
    assert loaded.data('raw')['fixture_ids'] == ['TESTONLY:f']
    assert proof['validation']['causal_feature_use_proven'] is False
    kernel = source.SourceAccessKernel(loaded, scope.identity(), lambda row: source.value_hash(row))
    assert kernel._roots is loaded._roots


def test_v2_foreign_parts_cached_identity_secret_and_tamper_fail_before_host(tmp_path, monkeypatch):
    bundle = _bundle('TESTONLY-credential')
    path, document = _stage(tmp_path / 'source', bundle)
    escaped = deepcopy(document)
    escaped['parts']['raw']['file'] = '../raw.json.gz'
    with pytest.raises(ValueError, match='EXACT_SOURCE_PART_REQUIRED'):
        source.load_source_bundle_document(escaped, path)
    with pytest.raises(ValueError, match='FOREIGN_CACHED_SOURCE_BUNDLE'):
        source.load_source_bundle_document(document, path, cached_bundle=_bundle('foreign'))
    scope, request = _scope(path, bundle)
    monkeypatch.setenv('TESTONLY_SECRET', 'TESTONLY-credential')
    with pytest.raises(ValueError, match='SOURCE_CONTAINS_CURRENT_CREDENTIAL'):
        scope.prepare(request)
    assert not (path.parent / 'p').exists()
    monkeypatch.delenv('TESTONLY_SECRET')
    raw = path.parent / 'raw.json.gz'
    content = raw.read_bytes()
    raw.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    with pytest.raises(ValueError, match='SOURCE_PART_HASH_CHANGED'):
        source.load_source_bundle_document(document, path, cached_bundle=bundle)


def test_v2_inflation_and_total_decoded_limits_stop_before_json_parse(tmp_path, monkeypatch):
    bundle = _bundle()
    path, document = _stage(tmp_path / 'source', bundle)
    raw = path.parent / 'raw.json.gz'
    packed = gzip.compress((bundle.raw_json + 'x').encode('utf-8'), mtime=0)
    raw.write_bytes(packed)
    changed = deepcopy(document)
    changed['parts']['raw'].update(stored_bytes=len(packed), stored_sha256=hashlib.sha256(packed).hexdigest())
    parsed = []

    def forbidden_parse(text):
        parsed.append(text)
        raise AssertionError('TESTONLY unexpected JSON parse')
    monkeypatch.setattr(source, '_load', forbidden_parse)
    with pytest.raises(ValueError, match='SOURCE_PART_DECODED_BOUND_EXCEEDED'):
        source.load_source_bundle_document(changed, path)
    oversize = deepcopy(document)
    oversize['parts']['raw']['decoded_bytes'] = source.BUNDLE_DECODED_LIMIT
    with pytest.raises(ValueError, match='SOURCE_BUNDLE_DECODED_BOUND_EXCEEDED'):
        source.load_source_bundle_document(oversize, path)
    assert parsed == []


def test_v2_writer_limits_and_once_fail_without_replacing_original_files(tmp_path, monkeypatch):
    bundle = _bundle('TESTONLY ' * 300)
    path, document = _stage(tmp_path / 'good', bundle)
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    with pytest.raises(ValueError, match='SOURCE_BUNDLE_MANIFEST_ALREADY_EXISTS'):
        source.stage_source_bundle_files(bundle, path)
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before
    assert json.loads(path.read_text('utf-8')) == document
    monkeypatch.setattr(source, 'BUNDLE_DECODED_LIMIT', 100)
    small = tmp_path / 'decoded'
    small.mkdir()
    with pytest.raises(ValueError, match='SOURCE_BUNDLE_DECODED_BOUND_EXCEEDED'):
        source.stage_source_bundle_files(bundle, small / 'bundle.json')
    assert not (small / 'bundle.json').exists()
    monkeypatch.setattr(source, 'BUNDLE_DECODED_LIMIT', 1_073_741_824)
    monkeypatch.setattr(source, 'BUNDLE_STORED_LIMIT', 20)
    stored = tmp_path / 'stored'
    stored.mkdir()
    with pytest.raises(ValueError, match='SOURCE_BUNDLE_STORED_BOUND_EXCEEDED'):
        source.stage_source_bundle_files(bundle, stored / 'bundle.json')
    assert not (stored / 'bundle.json').exists()
