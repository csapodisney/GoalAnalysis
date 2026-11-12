"""TESTONLY path regression; no CLI, process, network, model or database."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path, PureWindowsPath

import pytest
from test_profile_source_host import make_source_phase

from goal_analysis.agents import profile_source_host as host

KNOWN_LONG_OWNED_ROOT = PureWindowsPath(
    'C:/Users/HP/.codex/tmp/arthur-ten-agent-tests-first-20261112-09/basetemp/'
    'test_full_day_source_access_te0/journals/TESTONLY-source-access-twenty-card-system/source-access'
)


def test_known_long_owned_root_prepares_binding_below_windows_259_characters(tmp_path):
    scope, request, _ = make_source_phase(tmp_path)
    known_length = len(str(KNOWN_LONG_OWNED_ROOT))
    padding = known_length - len(str(tmp_path.resolve())) - 1
    assert 1 <= padding <= 255
    root = tmp_path / ('r' * padding)
    root.mkdir()
    bundle = root / 'bundle.json'
    bundle.write_bytes(Path(scope.bundle_path).read_bytes())
    scope = replace(scope, owned_root=root, bundle_path=bundle, binding=scope.identity())

    prepared = scope.prepare(request)
    binding_path = prepared['binding_path']
    assert len(str(root.resolve())) == known_length
    assert len(str(root / 'source-phases' / host.sha(scope.identity()) / 'binding.json')) > 259
    assert len(str(binding_path)) <= 259
    assert binding_path == root.resolve() / 'p' / host.sha(scope.identity())[:32] / 'binding.json'
    document = json.loads(binding_path.read_text('utf-8'))
    assert document['binding'] == scope.identity()
    assert document['request_sha256'] == host.sha(request)
    assert prepared['binding_sha256'] == hashlib.sha256(binding_path.read_bytes()).hexdigest()
    assert len(document['request_sha256']) == len(prepared['binding_sha256']) == 64


def test_phase_locator_collision_fails_without_replacing_original_binding(tmp_path, monkeypatch):
    scope, request, _ = make_source_phase(tmp_path)
    prepared = scope.prepare(request)
    binding_path = prepared['binding_path']
    original_bytes = binding_path.read_bytes()
    original_digest = host.sha
    first_identity_digest = original_digest(scope.identity())
    second_binding = {**scope.identity(), 'session_id': 'TESTONLY-distinct-colliding-session'}
    second_request = {**request, **second_binding}
    second_scope = replace(scope, binding=second_binding)
    actual_second_digest = original_digest(second_binding)
    assert actual_second_digest != first_identity_digest
    collision_suffix = 'f' * 32 if first_identity_digest[32:] != 'f' * 32 else 'e' * 32
    artificial_second_digest = first_identity_digest[:32] + collision_suffix
    assert artificial_second_digest != first_identity_digest

    def collide_locator_only(value):
        return artificial_second_digest if value == second_binding else original_digest(value)

    monkeypatch.setattr(host, 'sha', collide_locator_only)
    with pytest.raises(FileExistsError):
        second_scope.prepare(second_request)
    assert binding_path.read_bytes() == original_bytes
    assert list((Path(scope.owned_root) / 'p').iterdir()) == [binding_path.parent]
    assert not prepared['journal_path'].exists() and not prepared['receipt_path'].exists()
    assert json.loads(original_bytes)['request_sha256'] == original_digest(request)
    assert prepared['binding_sha256'] == hashlib.sha256(original_bytes).hexdigest()
