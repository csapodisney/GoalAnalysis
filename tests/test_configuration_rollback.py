"""Configuration-only restore, failure and crash recovery with real shared locks."""
import importlib.util
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_daily_223_live import config as live_config

from goal_analysis.config.portfolio import DEFAULT_SETTINGS
from goal_analysis.jobs import configuration_rollback as job
from goal_analysis.jobs.portfolio import RunLock


@pytest.fixture
def environment(tmp_path):
    values = {
        'arthur-settings.json': deepcopy(DEFAULT_SETTINGS),
        'daily223-live.json': live_config(),
        'candidate-context-review.json': {'version': 'candidate-context-review-v1', 'mode': 'ACTIVE'},
        'profile-context-review.json': {'version': 'profile-context-review-v1', 'mode': 'ACTIVE'},
        'native-shadow.json': {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 180},
        'grok-native-gate.json': {'enabled': True, 'mode': 'SHADOW'},
        'grok.json': {'enabled': True, 'mode': 'SHADOW', 'model': 'grok-4.7',
                      'daily_budget_usd': 1., 'monthly_budget_usd': 20., 'max_requests_per_day': 2},
        'club-fixture-coverage.json': {'version': 'club-fixture-coverage-activation-v1', 'mode': 'ACTIVE'},
        'daily-review-cohort.json': {'version': 'daily-review-cohort-v1', 'mode': 'ACTIVE'},
    }
    raw = {name: (json.dumps(value, indent=2)+'\r\n').encode() for name, value in values.items()}
    folder = tmp_path/'config';folder.mkdir()
    for name, data in raw.items():
        (folder/name).write_bytes(data)
    database = tmp_path/'data/goal_analysis.sqlite3';database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE original_results (id INTEGER, actual_score TEXT)')
        db.execute("INSERT INTO original_results VALUES (1,'2-1')")
    report = tmp_path/'reports/arthur/old/report.json';report.parent.mkdir(parents=True)
    report.write_bytes(b'{"original_immutable_test_report":true,"quoted_at":null}')
    protected = {database: database.read_bytes(), report: report.read_bytes()}
    previous = {**raw, 'club-fixture-coverage.json': None, 'daily-review-cohort.json': None}
    release = job.seal_verified_configuration(tmp_path, previous,
        provenance={'kind': 'EXPLICIT_SYNTHETIC_PRIOR_CONFIGURATION_NOT_LIVE_ACCEPTANCE'})
    return tmp_path, raw, previous, release, protected


def assert_protected(protected):
    assert all(path.read_bytes() == raw for path, raw in protected.items())


def test_real_files_roundtrip_absence_and_current_exact_bytes_are_idempotent(environment):
    root, current, previous, old, protected = environment
    current_release = job.capture(root, provenance={'kind': 'SYNTHETIC_CURRENT'})
    assert job.capture(root, provenance={'kind': 'SYNTHETIC_CURRENT'}) == current_release
    requested = job.plan(root, old['release_id'])
    assert requested['changed_files'] == list(job.FILES[-2:]) and requested['production_writes'] == 0
    result = job.restore(root, old['release_id'], expected_configuration_id=requested['current_configuration_id'])
    assert result['status'] == 'RESTORED' and job._read_configuration(root) == previous
    assert job.restore(root, old['release_id'],
                       expected_configuration_id=requested['current_configuration_id'])['status'] == 'ALREADY_CURRENT'
    from goal_analysis.jobs.daily_review_cohort import policy as daily_policy
    from goal_analysis.tipsters.coverage_search import policy as coverage_policy
    assert daily_policy(root) == coverage_policy(root) == 'SHADOW'
    assert job.recover(root, result['operation_id'])['status'] == 'ALREADY_COMMITTED'
    restored = job.restore(root, current_release['release_id'], expected_configuration_id=old['configuration_id'])
    assert restored['status'] == 'RESTORED' and job._read_configuration(root) == current
    assert daily_policy(root) == coverage_policy(root) == 'ACTIVE'
    assert job.restore(root, current_release['release_id'],
                       expected_configuration_id=current_release['configuration_id'])['status'] == 'ALREADY_CURRENT'
    assert len(job._operations(root)) == 2
    assert_protected(protected)


def test_failed_atomic_change_restores_the_before_bundle(environment, monkeypatch):
    root, current, _previous, old, protected = environment
    original = job._replace
    failed = []

    def fail_once(*args):
        original(*args)
        if not failed:
            failed.append(True)
            raise OSError('Explicit synthetic failure after first atomic replacement')

    monkeypatch.setattr(job, '_replace', fail_once)
    with pytest.raises(OSError):
        job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    assert job._read_configuration(root) == current
    job.ensure_consistent(root)
    assert (job._operations(root)[0]/'recovered.json').is_file()
    assert_protected(protected)


class PowerLoss(BaseException):
    pass


def interrupted(environment, monkeypatch):
    root, current, _previous, old, _protected = environment
    original = job._replace

    def crash(*args):
        original(*args)
        raise PowerLoss('Synthetic process loss after real first file change')

    monkeypatch.setattr(job, '_replace', crash)
    with pytest.raises(PowerLoss):
        job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    monkeypatch.setattr(job, '_replace', original)
    return job._operations(root)[0].name


def test_interrupted_restore_blocks_normal_jobs_and_settings_then_recovers(environment, monkeypatch):
    root, current, _previous, _old, protected = environment
    operation = interrupted(environment, monkeypatch)
    with pytest.raises(RuntimeError, match='CONFIGURATION_RESTORE_PENDING'), RunLock(root/'data/arthur/run.lock'):
        pytest.fail('An incomplete bundle cannot admit a normal job')
    from goal_analysis.portfolio_dashboard import ArthurState
    state = ArthurState(root)
    with pytest.raises(RuntimeError, match='CONFIGURATION_RESTORE_PENDING'):
        state.update_settings({'strictness': 36})
    assert job.recover(root, operation)['status'] == 'RECOVERED_BEFORE_CONFIGURATION'
    assert job.recover(root, operation)['status'] == 'ALREADY_RECOVERED'
    assert job._read_configuration(root) == current
    with RunLock(root/'data/arthur/run.lock'):
        pass
    assert_protected(protected)


def test_process_loss_during_recovery_reuses_only_exact_verified_temporary_bytes(environment, monkeypatch):
    root, current, _previous, _old, protected = environment
    operation = interrupted(environment, monkeypatch)
    original = job._new

    def crash_after_temporary_write(path, raw):
        original(path, raw)
        if path.name.endswith('-recover.tmp'):
            raise PowerLoss('Synthetic process loss after recovery temporary fsync')

    monkeypatch.setattr(job, '_new', crash_after_temporary_write)
    with pytest.raises(PowerLoss):
        job.recover(root, operation)
    monkeypatch.setattr(job, '_new', original)
    assert job.recover(root, operation)['status'] == 'RECOVERED_BEFORE_CONFIGURATION'
    assert job._read_configuration(root) == current
    assert_protected(protected)


def test_recovery_preserves_unrelated_external_configuration_edit(environment, monkeypatch):
    root, _current, _previous, _old, protected = environment
    operation = interrupted(environment, monkeypatch)
    path = root/'config/arthur-settings.json';value = json.loads(path.read_bytes())
    value['strictness'] = 37;path.write_text(json.dumps(value), encoding='utf-8')
    changed = path.read_bytes()
    with pytest.raises(ValueError, match='EXTERNAL_EDIT_CONFLICT'):
        job.recover(root, operation)
    assert path.read_bytes() == changed
    with pytest.raises(RuntimeError, match='CONFIGURATION_RESTORE_PENDING'), RunLock(root/'data/arthur/run.lock'):
        pass
    assert_protected(protected)


def test_stale_configuration_and_shared_lock_refuse_before_mutating(environment):
    root, current, _previous, old, protected = environment
    with RunLock(root/'data/arthur/run.lock'), pytest.raises(RuntimeError):
        job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    with pytest.raises(ValueError, match='CHANGED_SINCE_REVIEW'):
        job.restore(root, old['release_id'], expected_configuration_id='0'*64)
    assert job._read_configuration(root) == current and not job._operations(root)
    assert_protected(protected)


@pytest.mark.parametrize('invalid', ['../outside', 'A'*64, 'a'*63, '', None])
def test_release_and_operation_identifiers_cannot_address_arbitrary_paths(environment, invalid):
    root, current, _previous, _old, protected = environment
    with pytest.raises(ValueError):job.plan(root, invalid)
    with pytest.raises(ValueError):job.recover(root, invalid)
    assert job._read_configuration(root) == current
    assert_protected(protected)


@pytest.mark.parametrize('case', range(10))
def test_invalid_secret_provider_gate_model_and_budget_configuration_is_not_sealed(environment, case):
    root, current, _previous, _old, protected = environment
    value = deepcopy(current)
    if case == 0:value['../../data/ledger.sqlite3'] = b'forbidden'
    elif case == 1:value['arthur-settings.json'] = b'{"api_key":"explicit-synthetic-secret"}'
    elif case == 2:
        settings = json.loads(value['arthur-settings.json']);settings['openai']['model'] = 'unapproved-model'
        value['arthur-settings.json'] = json.dumps(settings).encode()
    elif case == 3:value['daily223-live.json'] = b'{"provider":"api_football"}'
    elif case == 4:value['candidate-context-review.json'] = b'{"version":"candidate-context-review-v1","mode":"OFF"}'
    elif case == 5:value['profile-context-review.json'] = b'{"version":"profile-context-review-v1","mode":"OFF"}'
    elif case == 6:
        grok = json.loads(value['grok.json']);grok['daily_budget_usd'] = 2
        value['grok.json'] = json.dumps(grok).encode()
    elif case == 7:
        grok = json.loads(value['grok.json']);grok['daily_budget_usd'] = True
        value['grok.json'] = json.dumps(grok).encode()
    elif case == 8:value['grok.json'] = b'{"enabled":true,"enabled":false}'
    else:value['native-shadow.json'] = b'{"enabled":true,"mode":"LIVE","max_seconds":180}'
    with pytest.raises((ValueError, TypeError)):
        job.seal_verified_configuration(root, value, provenance={'kind': 'INVALID_SYNTHETIC_INPUT'})
    assert job._read_configuration(root) == current and not job._operations(root)
    assert_protected(protected)


@pytest.mark.parametrize('case', ['payload', 'manifest', 'absence'])
def test_tampered_release_is_rejected_before_config_changes(environment, case):
    root, current, _previous, old, protected = environment
    folder=root/'backups/configuration-rollback/releases'/old['release_id']
    if case == 'payload':(folder/'files/grok.json').write_bytes(b'{}')
    elif case == 'manifest':
        p=folder/'manifest.json';v=json.loads(p.read_bytes());v['configuration_id']='0'*64;p.write_text(json.dumps(v))
    else:(folder/'files/daily-review-cohort.json').write_bytes(current['daily-review-cohort.json'])
    with pytest.raises(ValueError):
        job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    assert job._read_configuration(root) == current and not job._operations(root)
    assert_protected(protected)


def test_redirected_config_path_is_refused_without_following_it(environment, monkeypatch):
    root, _current, _previous, _old, protected = environment
    resolve = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == root/'config/grok.json':return root.parent/'outside.json'
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    with pytest.raises(ValueError, match='PATH_REDIRECTION'):
        job.capture(root, provenance={'kind': 'SYMLINK_RESOLUTION_CONTROL'})
    assert_protected(protected)


def test_corrupt_completed_journal_is_not_an_admission_token(environment):
    root, current, _previous, old, protected = environment
    receipt=job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    p=job._operations(root)[0]/'complete.json';value=json.loads(p.read_bytes());value['configuration_id']='0'*64
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='JOURNAL_CONFLICT'), RunLock(root/'data/arthur/run.lock'):
        pass
    with pytest.raises(ValueError, match='JOURNAL_CONFLICT'):job.recover(root, receipt['operation_id'])
    assert_protected(protected)


def test_operation_budget_does_not_leave_successful_restore_that_blocks_next_start(environment, monkeypatch):
    root, current, _previous, old, protected = environment
    now=job.capture(root, provenance={'kind': 'SYNTHETIC_CURRENT'})
    monkeypatch.setattr(job, 'MAX_OPERATIONS', 1)
    job.restore(root, old['release_id'], expected_configuration_id=job.configuration_id(current))
    with pytest.raises(ValueError, match='OPERATION_BUDGET'):
        job.restore(root, now['release_id'], expected_configuration_id=old['configuration_id'])
    with RunLock(root/'data/arthur/run.lock'):
        pass
    assert_protected(protected)


def test_dashboard_settings_writer_obeys_cross_process_application_lock(environment):
    root, _current, _previous, _old, protected = environment
    from goal_analysis.portfolio_dashboard import ArthurState
    state=ArthurState(root);before=(root/'config/arthur-settings.json').read_bytes()
    with RunLock(root/'data/arthur/run.lock'), pytest.raises(RuntimeError):
        state.update_settings({'strictness': 36})
    assert (root/'config/arthur-settings.json').read_bytes() == before
    assert state.update_settings({'strictness': 36})['strictness'] == 36
    assert_protected(protected)


def test_cli_dry_run_does_not_apply_a_release(environment, capsys):
    root, current, _previous, old, protected = environment
    path=Path(__file__).parents[1]/'scripts/arthur-config-rollback.py'
    spec=importlib.util.spec_from_file_location('configuration_rollback_cli', path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    assert cli.main(['--root', str(root), 'restore', old['release_id'], '--expect', job.configuration_id(current)]) == 0
    result=json.loads(capsys.readouterr().out)
    assert result['production_writes'] == 0 and result['changed_files'] == list(job.FILES[-2:])
    assert job._read_configuration(root) == current and not job._operations(root)
    assert_protected(protected)


def runner(root):
    path=Path(__file__).parents[1]/'scripts/run-arthur.py'
    spec=importlib.util.spec_from_file_location('rollback_normal_cli_boundary', path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    cli.ROOT=root
    return cli


def test_normal_cli_reloads_model_and_data_settings_after_configuration_lock(environment, monkeypatch):
    root, _current, _previous, _old, protected = environment
    cli=runner(root);seen=[]
    settings_loader=cli.load_settings

    def load(path):
        value=settings_loader(path);seen.append(('settings',value['strictness']));return value

    class InterleavedConfiguration(RunLock):
        def __enter__(self):
            value=super().__enter__()
            # Emulate a transaction committed after the initial CLI read,
            # immediately before this caller was admitted to the shared lock.
            path=root/'config/arthur-settings.json';settings=json.loads(path.read_bytes())
            settings['strictness']=36;path.write_text(json.dumps(settings))
            path=root/'config/daily223-live.json';config=json.loads(path.read_bytes())
            config['max_football_calls']=7;path.write_text(json.dumps(config))
            return value

    monkeypatch.setattr(cli,'RunLock',InterleavedConfiguration)
    monkeypatch.setattr(cli,'load_settings',load)
    monkeypatch.setattr(cli,'PortfolioLedger',lambda *_a:SimpleNamespace(refresh_results=lambda _c:{}))
    monkeypatch.setattr(cli,'Database',lambda *_a:SimpleNamespace(initialize=lambda:None))
    monkeypatch.setattr(cli,'TheStatsPrimaryClient',lambda *_a,**k:seen.append(('budget',k['max_requests'])))
    assert cli.main(['--settle'])==0
    assert seen==[('settings',35),('settings',36),('budget',27)]
    assert_protected(protected)


def test_normal_cli_pending_restore_stops_before_provider_or_ledger_initialization(environment, monkeypatch):
    root, _current, _previous, _old, protected = environment
    interrupted(environment,monkeypatch);cli=runner(root)

    def forbidden(*_a,**_k):
        pytest.fail('Pending recovery may not open a provider or mutate the portfolio database')

    monkeypatch.setattr(cli,'PortfolioLedger',forbidden)
    monkeypatch.setattr(cli,'TheStatsPrimaryClient',forbidden)
    assert cli.main(['--settle'])==1
    assert_protected(protected)


def test_receipt_is_atomically_published_and_crash_before_commit_is_recoverable(environment, monkeypatch):
    root, current, _previous, old, protected = environment
    original=job.os.link

    def crash_before_complete(source, target):
        if target.name=='complete.json':raise PowerLoss('Loss before atomic receipt publication')
        return original(source,target)

    monkeypatch.setattr(job.os,'link',crash_before_complete)
    with pytest.raises(PowerLoss):
        job.restore(root,old['release_id'],expected_configuration_id=job.configuration_id(current))
    operation=job._operations(root)[0]
    assert not (operation/'complete.json').exists()
    with pytest.raises(RuntimeError,match='CONFIGURATION_RESTORE_PENDING'), RunLock(root/'data/arthur/run.lock'):
        pass
    monkeypatch.setattr(job.os,'link',original)
    assert job.recover(root,operation.name)['status']=='RECOVERED_BEFORE_CONFIGURATION'
    assert job._read_configuration(root)==current
    assert_protected(protected)


def test_loss_before_intent_publication_does_not_admit_an_empty_pending_operation(environment, monkeypatch):
    root, current, _previous, old, protected = environment
    original=job.os.link

    def crash_before_intent(source,target):
        if target.name=='intent.json':raise PowerLoss('Loss before the prepared intent was published')
        return original(source,target)

    monkeypatch.setattr(job.os,'link',crash_before_intent)
    with pytest.raises(PowerLoss):
        job.restore(root,old['release_id'],expected_configuration_id=job.configuration_id(current))
    assert not job._operations(root)
    with RunLock(root/'data/arthur/run.lock'):
        pass
    assert job._read_configuration(root)==current
    assert_protected(protected)


def test_exclusive_metadata_publication_never_truncates_an_existing_receipt(tmp_path):
    path=tmp_path/'receipt.json';job._new(path,b'{"original":true}')
    with pytest.raises(FileExistsError):job._new(path,b'{"rewritten":true}')
    assert path.read_bytes()==b'{"original":true}'
