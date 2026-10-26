"""Exact, bounded configuration releases and journaled restore; no data migration."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import load_settings

VERSION = 'arthur-configuration-rollback-v1'
FILES = (
    'arthur-settings.json', 'daily223-live.json', 'candidate-context-review.json',
    'profile-context-review.json', 'native-shadow.json', 'grok-native-gate.json',
    'grok.json', 'club-fixture-coverage.json', 'daily-review-cohort.json',
)
OPTIONAL = frozenset(FILES[-2:])
MAX_BYTES = 64 * 1024
MAX_OPERATIONS = 512
_ID = re.compile(r'[a-f0-9]{64}\Z')
_OP = re.compile(r'[a-f0-9]{32}\Z')


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('DUPLICATE_CONFIGURATION_KEY')
        result[key] = value
    return result


def _json(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('CONFIGURATION_BYTE_BUDGET')
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                       parse_constant=lambda _v: (_ for _ in ()).throw(ValueError('NONFINITE_CONFIGURATION')))
    if not isinstance(value, dict):
        raise TypeError('CONFIGURATION_JSON_OBJECT_REQUIRED')
    return value


def _path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.resolve() != path or not path.resolve().is_relative_to(root):
        raise ValueError('CONFIGURATION_PATH_REDIRECTION')
    return path


def _home(root):
    return _path(root, 'backups/configuration-rollback')


def _validate(files):
    if set(files) != set(FILES):
        raise ValueError('EXACT_CONFIGURATION_ALLOWLIST_REQUIRED')
    values = {}
    for name, raw in files.items():
        if raw is None:
            if name not in OPTIONAL:
                raise ValueError('REQUIRED_CONFIGURATION_MISSING')
        elif not isinstance(raw, bytes):
            raise ValueError('CONFIGURATION_BYTES_REQUIRED')
        else:
            values[name] = _json(raw)
    settings_raw = files['arthur-settings.json'].decode('utf-8-sig')
    settings = load_settings(SimpleNamespace(exists=lambda: True, read_text=lambda _encoding: settings_raw))
    live = validate_live_config(values['daily223-live.json'])
    # Established exact-quote gates and native SHADOW policy cannot be rolled out.
    for name, version in [('candidate-context-review.json', 'candidate-context-review-v1'),
                          ('profile-context-review.json', 'profile-context-review-v1')]:
        if values[name] != {'version': version, 'mode': 'ACTIVE'}:
            raise ValueError('EXACT_QUOTE_REVIEW_GATE_REQUIRED')
    if values['native-shadow.json'] != {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 180}:
        raise ValueError('NATIVE_SHADOW_POLICY_REQUIRED')
    if values['grok-native-gate.json'] != {'enabled': True, 'mode': 'SHADOW'}:
        raise ValueError('GROK_NATIVE_SHADOW_GATE_REQUIRED')
    grok = values['grok.json']
    if grok != {'enabled': True, 'mode': 'SHADOW', 'model': 'grok-4.7',
                'daily_budget_usd': 1., 'monthly_budget_usd': 20., 'max_requests_per_day': 2}:
        raise ValueError('ORIGINAL_GROK_MODEL_BUDGET_REQUIRED')
    if (type(grok['enabled']) is not bool or type(grok['max_requests_per_day']) is not int
            or any(type(grok[k]) not in (int, float) for k in ('daily_budget_usd', 'monthly_budget_usd'))):
        raise ValueError('ORIGINAL_GROK_BUDGET_TYPES_REQUIRED')
    for name, version in [('club-fixture-coverage.json', 'club-fixture-coverage-activation-v1'),
                          ('daily-review-cohort.json', 'daily-review-cohort-v1')]:
        if name in values and values[name] not in (
            {'version': version, 'mode': mode} for mode in ('OFF', 'SHADOW', 'ACTIVE')
        ):
            raise ValueError('INVALID_OPTIONAL_ROUTE_POLICY')
    return {'settings': settings, 'live_config': live,
            'sports_provider': 'the_stats_api', 'odds_provider': 'the_stats_api'}


def _read_configuration(root):
    files = {}
    for name in FILES:
        path = _path(root, 'config/' + name)
        if path.exists() and (not path.is_file() or path.stat().st_size > MAX_BYTES):
            raise ValueError('INVALID_CONFIGURATION_FILE')
        files[name] = path.read_bytes() if path.exists() else None
    _validate(files)
    return files


def configuration_id(files):
    _validate(files)
    return _sha(_encoded({name: None if raw is None else _sha(raw) for name, raw in files.items()}))


def _new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the exclusive temporary basename short on Windows installations
    # whose temporary test/work roots approach the native path-length limit.
    temporary = path.with_name('.p-' + uuid4().hex[:16])
    created = False
    try:
        with temporary.open('xb') as stream:
            created = True
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic exclusive publication; never truncates a receipt
    finally:
        if created:
            try:
                temporary.unlink()
            except OSError:
                pass  # an orphan part is not a committed receipt and remains inspectable


def _release(root, identifier):
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError('CONFIGURATION_RELEASE_ID_REQUIRED')
    folder = _path(root, 'backups/configuration-rollback/releases/' + identifier)
    manifest = _json((folder/'manifest.json').read_bytes())
    body = {k: v for k, v in manifest.items() if k != 'release_id'}
    if (set(body) != {'version', 'configuration_id', 'files', 'provenance'}
            or body['version'] != VERSION or _sha(_encoded(body)) != identifier
            or manifest.get('release_id') != identifier or set(body['files']) != set(FILES)):
        raise ValueError('CONFIGURATION_RELEASE_MANIFEST_CONFLICT')
    files = {}
    for name, expected in body['files'].items():
        path = _path(root, str((folder/'files'/name).relative_to(Path(root).resolve())))
        if expected is None:
            if path.exists():
                raise ValueError('CONFIGURATION_RELEASE_ABSENCE_CONFLICT')
            files[name] = None
        else:
            raw = path.read_bytes()
            if not isinstance(expected, str) or not _ID.fullmatch(expected) or _sha(raw) != expected:
                raise ValueError('CONFIGURATION_RELEASE_FILE_CONFLICT')
            files[name] = raw
    if configuration_id(files) != body['configuration_id']:
        raise ValueError('CONFIGURATION_RELEASE_IDENTITY_CONFLICT')
    return manifest, files


def _seal(root, files, provenance):
    _validate(files)
    if not isinstance(provenance, dict) or not provenance or len(_encoded(provenance)) > 4096:
        raise ValueError('BOUNDED_RELEASE_PROVENANCE_REQUIRED')
    body = {'version': VERSION, 'configuration_id': configuration_id(files),
            'files': {name: None if raw is None else _sha(raw) for name, raw in files.items()},
            'provenance': provenance}
    identifier = _sha(_encoded(body))
    folder = _path(root, 'backups/configuration-rollback/releases/' + identifier)
    if folder.exists():
        existing, existing_files = _release(root, identifier)
        if existing_files != files:
            raise ValueError('EXISTING_CONFIGURATION_RELEASE_CONFLICT')
        return existing
    folder.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        if raw is not None:
            _new(folder/'files'/name, raw)
    _new(folder/'manifest.json', _encoded(dict(body, release_id=identifier)) + b'\n')
    _release(root, identifier)
    return dict(body, release_id=identifier)


def capture(root, *, provenance):
    from goal_analysis.jobs.portfolio import RunLock

    root = Path(root).resolve()
    with RunLock(root/'data/arthur/run.lock'):
        return _seal(root, _read_configuration(root), provenance)


def seal_verified_configuration(root, files, *, provenance):
    """Seal an operator-supplied prior configuration; provenance is not approval."""
    from goal_analysis.jobs.portfolio import RunLock

    root = Path(root).resolve()
    with RunLock(root/'data/arthur/run.lock'):
        return _seal(root, files, provenance)


def _operations(root):
    folder = _path(root, 'backups/configuration-rollback/operations')
    if not folder.exists():
        return []
    paths = list(folder.iterdir())
    if len(paths) > MAX_OPERATIONS:
        raise ValueError('CONFIGURATION_OPERATION_BUDGET')
    if any(not _OP.fullmatch(p.name) or not p.is_dir() or p.resolve() != p for p in paths):
        raise ValueError('INVALID_CONFIGURATION_OPERATION_LAYOUT')
    return paths


def _verify_terminal(root, folder, terminal):
    intent = _json((folder/'intent.json').read_bytes())
    receipt = _json((folder/terminal).read_bytes())
    if (intent.get('version') != VERSION or intent.get('operation_id') != folder.name
            or receipt.get('operation_id') != folder.name):
        raise ValueError('CONFIGURATION_JOURNAL_CONFLICT')
    release_id = intent['target_release_id'] if terminal == 'complete.json' else intent['before_release_id']
    release, _files = _release(root, release_id)
    status = 'RESTORED' if terminal == 'complete.json' else 'RECOVERED_BEFORE_CONFIGURATION'
    if receipt.get('status') != status or receipt.get('configuration_id') != release['configuration_id']:
        raise ValueError('CONFIGURATION_JOURNAL_CONFLICT')


def ensure_consistent(root):
    """Called under the shared application RunLock before a normal job can start."""
    for folder in _operations(root):
        terminal = ('complete.json' if (folder/'complete.json').is_file() else
                    'recovered.json' if (folder/'recovered.json').is_file() else None)
        if terminal is None:
            raise RuntimeError('A beállítások helyreállítása még nem fejeződött be. '
                               '(CONFIGURATION_RESTORE_PENDING)')
        _verify_terminal(root, folder, terminal)


def plan(root, release_id):
    root = Path(root).resolve()
    ensure_consistent(root)
    target, target_files = _release(root, release_id)
    before = _read_configuration(root)
    return {'version': VERSION, 'current_configuration_id': configuration_id(before),
            'target_configuration_id': target['configuration_id'], 'release_id': release_id,
            'changed_files': [name for name in FILES if before[name] != target_files[name]],
            'production_writes': 0, 'automatic_model_promotion': False}


def _replace(root, name, raw, operation_id):
    path = _path(root, 'config/' + name)
    if raw is None:
        if path.exists():
            path.unlink()
    else:
        temporary = _path(root, 'config/.' + name + '.' + operation_id + '.tmp')
        if temporary.exists():
            if not temporary.is_file() or temporary.stat().st_size != len(raw) or temporary.read_bytes() != raw:
                raise ValueError('CONFIGURATION_TEMPORARY_CONFLICT')
        else:
            _new(temporary, raw)
        os.replace(temporary, path)
    if (path.read_bytes() if path.exists() else None) != raw:
        raise OSError('CONFIGURATION_REPLACEMENT_NOT_VERIFIED')


def _recover_locked(root, folder):
    if (folder/'complete.json').exists():
        _verify_terminal(root, folder, 'complete.json')
        return {'status': 'ALREADY_COMMITTED', 'operation_id': folder.name}
    if (folder/'recovered.json').exists():
        _verify_terminal(root, folder, 'recovered.json')
        return {'status': 'ALREADY_RECOVERED', 'operation_id': folder.name}
    intent = _json((folder/'intent.json').read_bytes())
    if (set(intent) != {'version', 'operation_id', 'before_release_id', 'target_release_id',
                       'expected_configuration_id', 'created_at'}
            or intent['version'] != VERSION or intent['operation_id'] != folder.name):
        raise ValueError('CONFIGURATION_RECOVERY_INTENT_CONFLICT')
    before_manifest, before = _release(root, intent['before_release_id'])
    _target_manifest, target = _release(root, intent['target_release_id'])
    if before_manifest['configuration_id'] != intent['expected_configuration_id']:
        raise ValueError('CONFIGURATION_RECOVERY_BEFORE_IDENTITY_CONFLICT')
    # A failed operation may leave a mixed bundle; validate exact allowed bytes,
    # not its intermediate semantics. Never overwrite an unrelated operator edit.
    current = {}
    for name in FILES:
        path = _path(root, 'config/' + name)
        if path.exists() and (not path.is_file() or path.stat().st_size > MAX_BYTES):
            raise ValueError('INVALID_RECOVERY_CONFIGURATION_FILE')
        current[name] = path.read_bytes() if path.exists() else None
        if current[name] not in (before[name], target[name]):
            raise ValueError('CONFIGURATION_RECOVERY_EXTERNAL_EDIT_CONFLICT')
    for name in reversed(FILES):
        if current[name] != before[name]:
            _replace(root, name, before[name], folder.name + '-recover')
    if _read_configuration(root) != before:
        raise OSError('CONFIGURATION_RECOVERY_NOT_VERIFIED')
    receipt = {'status': 'RECOVERED_BEFORE_CONFIGURATION', 'operation_id': folder.name,
               'configuration_id': configuration_id(before), 'at': datetime.now(UTC).isoformat()}
    _new(folder/'recovered.json', _encoded(receipt) + b'\n')
    return receipt


def restore(root, release_id, *, expected_configuration_id):
    from goal_analysis.jobs.portfolio import RunLock

    root = Path(root).resolve()
    with RunLock(root/'data/arthur/run.lock'):
        return _restore_locked(root, release_id, expected_configuration_id=expected_configuration_id)


def _restore_locked(root, release_id, *, expected_configuration_id):
    """Internal restore for an operator holding the shared RunLock session."""
    request = plan(root, release_id)
    before = _read_configuration(root)
    if not request['changed_files']:
        return {'status': 'ALREADY_CURRENT', **request}
    if configuration_id(before) != expected_configuration_id:
        raise ValueError('CONFIGURATION_CHANGED_SINCE_REVIEW')
    target, target_files = _release(root, release_id)
    if len(_operations(root)) >= MAX_OPERATIONS:
        raise ValueError('CONFIGURATION_OPERATION_BUDGET')
    previous = _seal(root, before, {'kind': 'PRE_RESTORE_EXACT_CONFIGURATION',
                                    'target_release_id': release_id})
    operation_id = uuid4().hex
    folder = _path(root, 'backups/configuration-rollback/operations/' + operation_id)
    prepared = _path(root, 'backups/configuration-rollback/prepared/' + operation_id)
    prepared.mkdir(parents=True, exist_ok=False)
    intent = {'version': VERSION, 'operation_id': operation_id,
              'before_release_id': previous['release_id'], 'target_release_id': release_id,
              'expected_configuration_id': expected_configuration_id,
              'created_at': datetime.now(UTC).isoformat()}
    _new(prepared/'intent.json', _encoded(intent) + b'\n')
    folder.parent.mkdir(parents=True, exist_ok=True)
    os.rename(prepared, folder)  # a visible operation always has its complete intent
    try:
        for name in request['changed_files']:
            path = _path(root, 'config/' + name)
            if (path.read_bytes() if path.exists() else None) != before[name]:
                raise ValueError('CONFIGURATION_CHANGED_DURING_RESTORE')
            _replace(root, name, target_files[name], operation_id)
        if _read_configuration(root) != target_files:
            raise OSError('CONFIGURATION_RESTORE_NOT_VERIFIED')
        receipt = {'status': 'RESTORED', 'operation_id': operation_id,
                   'before_release_id': previous['release_id'], 'release_id': release_id,
                   'configuration_id': target['configuration_id'],
                   'changed_files': request['changed_files'], 'at': datetime.now(UTC).isoformat()}
        _new(folder/'complete.json', _encoded(receipt) + b'\n')
        return receipt
    except Exception:
        _recover_locked(root, folder)
        raise


def recover(root, operation_id):
    from goal_analysis.jobs.portfolio import RunLock

    if not isinstance(operation_id, str) or not _OP.fullmatch(operation_id):
        raise ValueError('CONFIGURATION_OPERATION_ID_REQUIRED')
    root = Path(root).resolve()
    with RunLock(root/'data/arthur/run.lock', configuration_recovery=True):
        folder = _path(root, 'backups/configuration-rollback/operations/' + operation_id)
        return _recover_locked(root, folder)
