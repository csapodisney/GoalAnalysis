"""Explicit recovery of committed source bytes; no generation or backdating."""
import socket
import time
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_native_forecast_capture import native, sports_rows, write

from goal_analysis.jobs import native_forecast_capture as module
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha


def partial(root, monkeypatch):
    run_id, report, analysis, summary, at, ledger, output = native(root)
    original = module._new
    def crash(path, value):
        if path.name == 'receipt.json':
            raise OSError('interrupted after committed capture')
        return original(path, value)
    with monkeypatch.context() as m:
        m.setattr(module, '_new', crash)
        with pytest.raises(OSError):
            module.capture(root, run_id, sha(report), clock=lambda: at)
    write(root/'config/native-calibration-capture.json', module.POLICY)
    work = root/'data/arthur/native-calibration-capture'/run_id
    return run_id, report, analysis, summary, at, ledger, output, work


def test_explicit_new_presence_clock_and_strict_reader_preserve_original_bytes(tmp_path, monkeypatch):
    run_id, report, _, _, at, ledger, output, work = partial(tmp_path, monkeypatch)
    originals = {p: p.read_bytes() for p in [ledger, work/'claim.json', work/'capture.json',
        output/'analysis.json', output/'summary.json', output/'settings.json']}
    sports = sports_rows(tmp_path)
    forbidden = Mock(side_effect=AssertionError('no network or generation'))
    monkeypatch.setattr(socket, 'socket', forbidden)
    new_at = at+timedelta(minutes=10)
    stamps = iter([new_at, new_at+timedelta(seconds=1)])
    result = module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: next(stamps))
    assert result['presence_recovered'] and result['new_presence_receipt']
    assert result['new_forecasts'] == result['additional_model_fits'] == result['external_requests'] == 0
    assert result['available_at'] == (new_at+timedelta(seconds=1)).isoformat()
    assert not (work/'receipt.json').exists()
    before = module.read_forecasts(tmp_path, as_of=new_at)
    assert before['eligible_forecasts'] == 0
    after = module.read_forecasts(tmp_path, as_of=new_at+timedelta(seconds=2))
    assert after['eligible_forecasts'] == 7 and after['labeled_forecasts'] == 0
    assert {r['saved_at'] for r in after['dataset']['rows']} == {result['available_at']}
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert sports_rows(tmp_path) == sports
    forbidden.assert_not_called()


def test_uncertain_capture_does_not_automatically_recover_or_repeat_any_extraction(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    extract = Mock(side_effect=AssertionError('no automatic retry'))
    monkeypatch.setattr(module, '_extract', extract)
    result = module.capture(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=1))
    assert result['status'] == 'UNCERTAIN_PREVIOUS_CAPTURE' and result['automatic_retry'] is False
    assert not (work/module.RECOVERY_FILE).exists()
    extract.assert_not_called()


def test_first_recovery_is_idempotent_and_normal_capture_reads_it_without_new_clock(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    result = module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=5))
    originals = {p: p.read_bytes() for p in work.glob('*.json')}
    extract = Mock(side_effect=AssertionError('no repeat verification/generation on idempotent writer'))
    monkeypatch.setattr(module, '_extract', extract)
    for call in (module.recover_presence, module.capture):
        repeat = call(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=20))
        assert repeat['receipt_id'] == result['receipt_id'] and repeat['new_forecasts'] == 0
        assert repeat['available_at'] == result['available_at']
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    extract.assert_not_called()


@pytest.mark.parametrize('minute,eligible', [(344,7),(345,0),(346,0),(800,0)])
def test_recovery_uses_only_new_clock_for_fifteen_minute_boundary(tmp_path, monkeypatch, minute, eligible):
    run_id, report, _, summary, _, _, _, _ = partial(tmp_path, monkeypatch)
    at = utc(summary['as_of'])+timedelta(minutes=minute)
    result = module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at)
    assert result['prospectively_eligible_forecasts'] == eligible
    read = module.read_forecasts(tmp_path, as_of=at)
    assert read['eligible_forecasts'] == eligible


@pytest.mark.parametrize('file', ['analysis.json','summary.json','settings.json','dataset','staging','capture.json','claim.json'])
def test_changed_actual_originals_do_not_get_a_recovery_receipt(tmp_path, monkeypatch, file):
    run_id, report, _, _, at, _, output, work = partial(tmp_path, monkeypatch)
    path = (next((output/'fit/datasets').glob('*.json')) if file == 'dataset' else
        tmp_path/'data/arthur/nightly-staging'/f'{run_id}.json' if file == 'staging' else
        work/file if file in {'capture.json','claim.json'} else output/file)
    path.write_text('{}')
    with pytest.raises((ValueError, KeyError)):
        module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=5))
    assert path.read_text() == '{}' and not (work/module.RECOVERY_FILE).exists()
    assert module.read_forecasts(tmp_path, as_of=at+timedelta(minutes=10))['eligible_forecasts'] == 0


@pytest.mark.parametrize('field', ['probability','gaps','files','population','report','analysis'])
def test_self_consistent_caller_hashes_cannot_replace_original_source_reconstruction(tmp_path, monkeypatch, field):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    body = module._read(work/'capture.json')
    if field == 'probability': body['rows'][0]['probability'] = .99
    if field == 'gaps': body['gaps'] = {'invented': 1}
    if field == 'files': body['files'].pop(next(iter(body['files'])))
    if field == 'population': body['population'] = 'SELECTED_WINNERS_ONLY'
    if field == 'report': body['report_sha256'] = 'a'*64
    if field == 'analysis': body['analysis_sha256'] = 'b'*64
    write(work/'capture.json', body)
    with pytest.raises(ValueError):
        module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=5))
    assert not (work/module.RECOVERY_FILE).exists()


@pytest.mark.parametrize('tamper', ['available_at','verification_started_at','claim_hash','basis','original_files','new_claim'])
def test_rehashed_recovery_tamper_does_not_pass_independent_claim_and_source_bindings(tmp_path, monkeypatch, tamper):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=10))
    receipt = module._read(work/module.RECOVERY_FILE)
    if tamper in {'available_at','verification_started_at'}: receipt[tamper] = at.isoformat()
    if tamper == 'claim_hash': receipt['recovery_claim_sha256'] = 'a'*64
    if tamper == 'basis': receipt['basis'] = 'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'
    if tamper == 'original_files': receipt['original_files'] = {}
    if tamper == 'new_claim':
        claim = module._read(work/module.RECOVERY_CLAIM);claim['original_files'] = {}
        write(work/module.RECOVERY_CLAIM, claim)
    receipt['receipt_id'] = sha({k:v for k,v in receipt.items() if k != 'receipt_id'})
    write(work/module.RECOVERY_FILE, receipt)
    assert module.read_forecasts(tmp_path, as_of=at+timedelta(hours=1))['eligible_forecasts'] == 0


def test_failed_receipt_write_can_explicitly_verify_again_without_rewriting_the_first_claim(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    original = module._new
    def crash(path, value):
        if path.name == module.RECOVERY_FILE: raise OSError('before recovery commit')
        return original(path, value)
    with monkeypatch.context() as m:
        m.setattr(module, '_new', crash)
        with pytest.raises(OSError):
            module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=1))
    old_claim = (work/module.RECOVERY_CLAIM).read_bytes()
    result = module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=2))
    assert result['available_at'] == (at+timedelta(minutes=2)).isoformat()
    assert (work/module.RECOVERY_CLAIM).read_bytes() == old_claim
    assert not (work/'receipt.json').exists()


def test_original_ten_second_budget_and_clock_rollback_never_get_a_receipt(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    base = time.monotonic();ticks = iter([base,base+11])
    with monkeypatch.context() as m:
        m.setattr(module, 'monotonic', lambda: next(ticks))
        with pytest.raises(ValueError, match='BUDGET'):
            module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=1))
    assert not (work/module.RECOVERY_FILE).exists()
    stamps = iter([at+timedelta(minutes=2),at+timedelta(minutes=1)])
    with pytest.raises(ValueError, match='CLOCK'):
        module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: next(stamps))
    assert not (work/module.RECOVERY_FILE).exists()


@pytest.mark.parametrize('seconds', [0,11,True,1.5])
def test_recovery_budget_remains_exact_typed_and_bounded(tmp_path, seconds):
    with pytest.raises(ValueError, match='BUDGET'):
        module.recover_presence(tmp_path, 'safe', 'a'*64, max_seconds=seconds)


def test_disabled_or_uncommitted_capture_never_runs_generation(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    (tmp_path/'config/native-calibration-capture.json').unlink()
    assert module.recover_presence(tmp_path, run_id, sha(report))['status'] == 'DISABLED'
    write(tmp_path/'config/native-calibration-capture.json', module.POLICY)
    (work/'capture.json').unlink()
    with pytest.raises(FileNotFoundError):
        module.recover_presence(tmp_path, run_id, sha(report), clock=lambda: at+timedelta(minutes=1))
    assert not (work/module.RECOVERY_FILE).exists()


def test_primary_receipt_is_never_recovered_or_replaced(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _, work = partial(tmp_path, monkeypatch)
    # A valid normal receipt from the same actual synthetic committed source.
    body = module._read(work/'capture.json')
    receipt = {'version':module.VERSION,'run_id':run_id,'capture_sha256':sha(body),
        'available_at':at.isoformat(),'basis':'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'}
    receipt['receipt_id'] = sha(receipt);write(work/'receipt.json',receipt)
    original = (work/'receipt.json').read_bytes()
    result = module.recover_presence(tmp_path,run_id,sha(report),clock=lambda:at+timedelta(minutes=1))
    assert not result['presence_recovered'] and (work/'receipt.json').read_bytes() == original
    assert not (work/module.RECOVERY_FILE).exists()
