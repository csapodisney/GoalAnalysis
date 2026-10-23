"""Reproducible review and actual file/receipt boundaries, never LIVE approval."""
import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_calibration_shadow import NOW, archive_calibration, data, physical, row
from test_native_forecast_capture import native, write

from goal_analysis.jobs import calibration_release as job
from goal_analysis.jobs import daily_native_shadow as daily
from goal_analysis.jobs import native_forecast_capture as capture
from goal_analysis.jobs.calibration_shadow import inspect_calibration
from goal_analysis.learning import LearningAgent
from goal_analysis.learning.calibration import POLICY, shadow_probability, walk_forward
from goal_analysis.learning.release_gate import assess
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha


@pytest.fixture
def evidence():
    cutoff=NOW-timedelta(days=10)
    rows=[{**row(1000+day*4+i,day),'profile_id':'native_shadow_gauss'} for day in range(70,10,-1) for i in range(4)]
    dataset=data(rows,as_of=cutoff); calibrated=walk_forward(dataset,as_of=cutoff)
    assert calibrated['groups'][0]['status']=='CANDIDATE_SHADOW'
    candidate=calibrated['groups'][0]['candidate']; registered=cutoff+timedelta(hours=1)
    observations=[]
    for day in range(7,0,-1):
        forecast={**row(9000+day,day),'profile_id':'native_shadow_gauss'}
        at=utc(forecast['saved_at'])+timedelta(minutes=1)
        observations.append({'candidate_id':candidate['candidate_id'],'forecast':forecast,
            'registered_at':registered.isoformat(),'shadow_available_at':at.isoformat(),
            'calibrated_probability':shadow_probability(candidate,forecast,as_of=at)['probability']})
    return dataset,calibrated,observations


def test_default_policy_reproduced_then_seven_true_closed_days_only_allow_manual_review(evidence):
    dataset,calibrated,observations=evidence
    before=deepcopy((dataset,calibrated,observations))
    result=assess(dataset,calibrated,observations,as_of=NOW)
    group=result['groups'][0]
    assert result['status']=='REVIEW_AVAILABLE' and all(group['checks'].values())
    assert group['real_forecast_days']==60 and group['shadow']['consecutive_closed_days']==7
    assert group['shadow']['unique_fixtures']==7
    assert not group['apply_allowed'] and not group['live_approved']
    assert not result['apply_allowed'] and not result['automatic_parameter_writes']
    assert result['remaining_promotion_gates']
    assert (dataset,calibrated,observations)==before


@pytest.mark.parametrize('attack',['self_pass','weaker_policy','parameters','score','dataset','extra_live_flag'])
def test_hashes_or_llm_pass_flags_cannot_replace_actual_walk_forward(evidence,attack):
    dataset,calibrated,observations=deepcopy(evidence)
    if attack=='self_pass': calibrated['groups'][0]['status']='PASS'
    if attack=='weaker_policy': calibrated['policy']={**POLICY,'min_train_fixtures':1}
    if attack=='parameters': calibrated['groups'][0]['candidate']['parameters']['temperature']=.5
    if attack=='score': calibrated['groups'][0]['calibrated_scores']['brier']=0.
    if attack=='dataset': dataset['rows'].pop()
    if attack=='extra_live_flag': calibrated['automatic_parameter_writes']=True
    calibrated['report_sha256']=sha({k:v for k,v in calibrated.items() if k!='report_sha256'})
    with pytest.raises(ValueError): assess(dataset,calibrated,observations,as_of=NOW)


@pytest.mark.parametrize('case',['gap','duplicates','future','unsettled','late_commit','current_day_only','worse'])
def test_shadow_sample_not_fabricated_from_calendar_wait_or_duplicate_runs(evidence,case):
    dataset,calibrated,observations=deepcopy(evidence)
    if case=='gap': observations.pop()
    if case=='duplicates': observations=observations[:1]*20
    if case=='future': observations[-1]['shadow_available_at']=(NOW+timedelta(days=1)).isoformat()
    if case=='unsettled': observations[0]['forecast']['result_receipts']=[]
    if case=='late_commit': observations[0]['shadow_available_at']=(utc(observations[0]['forecast']['kickoff'])-timedelta(minutes=15)).isoformat()
    if case=='current_day_only': observations=[]
    if case=='worse':
        for item in observations: item['forecast']['result_receipts'][0]['outcome']=1
    result=assess(dataset,calibrated,observations,as_of=NOW)
    assert result['status']=='BLOCKED' and not result['apply_allowed']
    assert result['groups'][0]['blocked_by']


@pytest.mark.parametrize('case',['late_registration','transformed_probability','wrong_cohort'])
def test_observation_is_bound_to_preexisting_exact_candidate(evidence,case):
    dataset,calibrated,observations=deepcopy(evidence)
    first=observations[0]
    if case=='late_registration': first['registered_at']=first['shadow_available_at']
    if case=='transformed_probability': first['calibrated_probability']=.999
    if case=='wrong_cohort': first['forecast']['strategy_version']='unrelated'
    with pytest.raises(ValueError): assess(dataset,calibrated,observations,as_of=NOW)


def test_repeated_observations_are_not_additional_independent_fixtures(evidence):
    dataset,calibrated,observations=deepcopy(evidence)
    result=assess(dataset,calibrated,observations*4,as_of=NOW)
    assert result['groups'][0]['shadow']['unique_fixtures']==7
    assert result['groups'][0]['shadow']['excluded']==21


def test_same_time_different_original_probabilities_are_not_arbitrarily_chosen(evidence):
    dataset,calibrated,observations=deepcopy(evidence)
    other=deepcopy(observations[0]);other['forecast']['probability']=.1
    candidate=calibrated['groups'][0]['candidate']
    other['calibrated_probability']=shadow_probability(candidate,other['forecast'],as_of=other['shadow_available_at'])['probability']
    with pytest.raises(ValueError,match='CONFLICTING'):
        assess(dataset,calibrated,[*observations,other],as_of=NOW)


def test_review_reads_real_sources_and_own_archive_without_learning_or_ledger_writes(tmp_path):
    ledger,_,_,_,_=physical(tmp_path)
    evidence=inspect_calibration(tmp_path,as_of=NOW,enabled=True,clock=lambda:NOW)
    artifact=archive_calibration(tmp_path,evidence)['dataset_id'];before=ledger.read_bytes()
    agent=LearningAgent(tmp_path,clock=lambda:NOW)
    result=agent.release_review(artifact,as_of=NOW,enabled=True)
    assert result['status']=='BLOCKED' and result['groups'][0]['candidate'] is None
    assert 'walk_forward' in result['groups'][0]['blocked_by']
    a=job.archive_review(tmp_path,result);b=job.archive_review(tmp_path,result)
    assert a['created'] and not b['created'] and a['dataset_id']==b['dataset_id']
    assert not a['apply_allowed'] and ledger.read_bytes()==before
    assert not (tmp_path/'data/learning/learning.sqlite3').exists()
    assert not (tmp_path/'config').exists()


def test_physically_missing_subset_of_forecasts_not_accepted_even_with_valid_dataset_hash(tmp_path):
    physical(tmp_path)
    evidence=inspect_calibration(tmp_path,as_of=NOW,enabled=True,clock=lambda:NOW)
    # A newly sealed but selectively emptied dataset is still not the source population.
    evidence['source_admission']['dataset']=data([],as_of=NOW)
    evidence['calibration']=walk_forward(evidence['source_admission']['dataset'],as_of=NOW)
    archive=archive_calibration(tmp_path,evidence)['dataset_id']
    with pytest.raises(ValueError,match='POPULATION'):
        job.review_release(tmp_path,archive,as_of=NOW,enabled=True,clock=lambda:NOW)


def test_disabled_release_review_does_no_io_and_future_cutoff_refused(tmp_path):
    result=job.review_release(tmp_path,'not-an-id',as_of=NOW,clock=lambda:NOW)
    assert result['status']=='DISABLED' and list(tmp_path.iterdir())==[]
    with pytest.raises(ValueError): job.review_release(tmp_path,'a'*64,as_of=NOW+timedelta(seconds=1),clock=lambda:NOW)
    with pytest.raises(ValueError): job.review_release(tmp_path,'a'*64,as_of=NOW,enabled=1,clock=lambda:NOW)


def registered_native(root,monkeypatch):
    run_id,report,analysis,_summary,capture_at,_,output=native(root)
    capture.capture(root,run_id,sha(report),clock=lambda:capture_at)
    native_row=capture.read_forecasts(root,as_of=NOW)['dataset']['rows'][0]
    # Controlled historical forecasts test registration and actual observation
    # IO, not a claim of physically sourced sixty-day football performance.
    at=utc(analysis['as_of'])-timedelta(days=2)
    rows=[]
    for day in range(70,0,-1):
        for i in range(4):
            r=row(10000+day*4+i,day+7)
            r.update({k:native_row[k] for k in ('profile_id','strategy_version','model_version',
                'market_key','selection_key','period','settlement','line')})
            rows.append(r)
    dataset=data(rows,as_of=at);calibrated=walk_forward(dataset,as_of=at)
    candidate=calibrated['groups'][0]['candidate'];assert candidate is not None
    monkeypatch.setattr(job,'_evidence',lambda *args:(dataset,calibrated))
    registered=job.register_candidate(root,'f'*64,candidate['candidate_id'],clock=lambda:at+timedelta(hours=1))
    assert registered['new_registration']
    return run_id,report,capture_at,registered,candidate,output


def test_registered_candidate_gets_actual_atomic_observation_before_lockout_and_repeat_zero(tmp_path,monkeypatch):
    run_id,_report,at,registration,candidate,_output=registered_native(tmp_path,monkeypatch)
    again=job.register_candidate(tmp_path,'f'*64,candidate['candidate_id'],clock=lambda:at)
    assert not again['new_registration'] and again['available_at']==registration['available_at']
    observed=job.observe(tmp_path,run_id,clock=lambda:at+timedelta(minutes=1))
    assert observed['new_observations']==observed['eligible_at_commit']==1
    assert observed['external_requests']==observed['additional_model_fits']==0
    assert job.observe(tmp_path,run_id,clock=lambda:NOW)['new_observations']==0
    rows=job._observations(tmp_path,tmp_path/'data/arthur/calibration-release-shadow',NOW)
    assert len(rows)==1 and rows[0]['candidate_id']==candidate['candidate_id']
    assert rows[0]['registered_at']==registration['available_at']
    assert rows[0]['forecast']['result_receipts']


@pytest.mark.parametrize('case',['receipt_tamper','source_changed','code_version','late_registration','interrupted'])
def test_observer_boundary_never_turns_missing_or_late_evidence_into_success(tmp_path,monkeypatch,case):
    run_id,_report,at,registered,_candidate,output=registered_native(tmp_path,monkeypatch)
    folder=tmp_path/'data/arthur/calibration-release-shadow'
    if case=='receipt_tamper': (folder/'registrations'/registered['registration_id']/'receipt.json').write_text('{}')
    if case=='source_changed': (output/'analysis.json').write_text('{}')
    if case=='code_version': monkeypatch.setattr(job,'implementation',lambda:'x'*64)
    if case=='late_registration':
        receipt_path=folder/'registrations'/registered['registration_id']/'receipt.json'
        receipt=json.loads(receipt_path.read_bytes());receipt['available_at']=at.isoformat()
        receipt['receipt_id']=sha({k:v for k,v in receipt.items() if k!='receipt_id'});write(receipt_path,receipt)
        result=job.observe(tmp_path,run_id,clock=lambda:at+timedelta(minutes=1))
        assert result['new_observations']==0
        return
    if case=='interrupted':
        path=folder/'observations'/run_id;path.mkdir(parents=True);write(path/'claim.json',{})
        assert job.observe(tmp_path,run_id,clock=lambda:at)['status']=='UNCERTAIN_PREVIOUS_OBSERVATION'
        return
    with pytest.raises((KeyError,ValueError)):
        job.observe(tmp_path,run_id,clock=lambda:at+timedelta(minutes=1))


def test_no_registration_observer_does_not_even_open_sqlite(tmp_path,monkeypatch):
    forbidden=Mock(side_effect=AssertionError('forbidden DB/fit'))
    monkeypatch.setattr(job.sqlite3,'connect',forbidden)
    result=job.observe(tmp_path,'not-used-without-candidates')
    assert result['status']=='NO_REGISTERED_CANDIDATES' and list(tmp_path.iterdir())==[]
    forbidden.assert_not_called()


def test_partial_observation_cannot_hide_losing_prediction_even_with_new_self_hash(tmp_path,monkeypatch):
    run_id,_report,at,_registered,_candidate,_output=registered_native(tmp_path,monkeypatch)
    job.observe(tmp_path,run_id,clock=lambda:at+timedelta(minutes=1))
    folder=tmp_path/'data/arthur/calibration-release-shadow';work=folder/'observations'/run_id
    body=json.loads((work/'body.json').read_bytes());body['observations']=[];write(work/'body.json',body)
    receipt=json.loads((work/'receipt.json').read_bytes());receipt['body_sha256']=sha(body)
    receipt['receipt_id']=sha({k:v for k,v in receipt.items() if k!='receipt_id'});write(work/'receipt.json',receipt)
    with pytest.raises(ValueError,match='POPULATION'):job._observations(tmp_path,folder,NOW)


def test_missing_whole_observer_run_is_an_explicit_coverage_gap(tmp_path,monkeypatch):
    _rid,_report,_at,_registered,_candidate,_out=registered_native(tmp_path,monkeypatch)
    result=job.review_release(tmp_path,'f'*64,as_of=NOW,enabled=True,clock=lambda:NOW)
    assert result['groups'][0]['missing_shadow_forecasts']==1
    assert not result['groups'][0]['checks']['complete_shadow_population']
    assert result['status']=='BLOCKED'


def test_final_committed_clock_not_early_transform_clock_controls_shadow_lockout(tmp_path,monkeypatch):
    run_id,_report,at,_registered,_candidate,output=registered_native(tmp_path,monkeypatch)
    analysis=json.loads((output/'analysis.json').read_bytes())
    kickoff=utc(analysis['source_view']['fixtures'][0]['kickoff'])
    stamps=iter([at+timedelta(minutes=1),at+timedelta(minutes=1),kickoff-timedelta(minutes=15)])
    result=job.observe(tmp_path,run_id,clock=lambda:next(stamps))
    assert result['new_observations']==1 and result['eligible_at_commit']==0
    report=job.review_release(tmp_path,'f'*64,as_of=NOW,enabled=True,clock=lambda:NOW)
    assert report['groups'][0]['shadow']['invalid_observations']==1
    assert not report['groups'][0]['checks']['shadow_integrity']


def test_unsupported_club_cohort_cannot_register_as_a_native_model(evidence,tmp_path,monkeypatch):
    dataset,_old,_observations=deepcopy(evidence)
    rows=[{**r,'profile_id':'club_personality'} for r in dataset['rows']]
    changed=data(rows,as_of=dataset['manifest']['as_of'])
    calibrated=walk_forward(changed,as_of=dataset['manifest']['as_of'])
    candidate=calibrated['groups'][0]['candidate'];assert candidate is not None
    monkeypatch.setattr(job,'_evidence',lambda *a:(changed,calibrated))
    result=job.register_candidate(tmp_path,'f'*64,candidate['candidate_id'],clock=lambda:NOW)
    assert result['status']=='BLOCKED' and 'supported_observer_cohort' in result['blocked_by']
    assert list(tmp_path.iterdir())==[]


def test_identity_mapping_is_not_a_new_parameter_version(tmp_path,monkeypatch,evidence):
    dataset,_old,_observations=deepcopy(evidence)
    changed=data([{**r,'probability':.5} for r in dataset['rows']],as_of=dataset['manifest']['as_of'])
    calibrated=walk_forward(changed,as_of=dataset['manifest']['as_of'])
    candidate=calibrated['groups'][0]['candidate'];assert candidate is not None
    monkeypatch.setattr(job,'_evidence',lambda *a:(changed,calibrated))
    result=job.register_candidate(tmp_path,'f'*64,candidate['candidate_id'],clock=lambda:NOW)
    assert result['status']=='BLOCKED' and 'parameter_change' in result['blocked_by']


@pytest.mark.parametrize('policy',[{'enabled':1},{'mode':'LIVE'},{'max_seconds':6},{'extra':True}])
def test_release_policy_must_be_exact_typed_shadow(tmp_path,policy):
    write(tmp_path/'config/calibration-release-shadow.json',{**job.POLICY,**policy})
    assert not job.enabled(tmp_path)


def test_registration_budget_and_ids_do_not_create_unbounded_work(tmp_path):
    folder=tmp_path/'data/arthur/calibration-release-shadow'
    for i in range(9):(folder/'registrations'/sha(i)).mkdir(parents=True)
    with pytest.raises(ValueError,match='BUDGET'):job.observe(tmp_path,'run')
    with pytest.raises(ValueError,match='SHA256'):job._id('../escape')


@pytest.mark.parametrize('case',['enabled','no_capture','failure','disabled'])
def test_daily_parent_release_hook_has_no_extra_worker_or_publication_veto(tmp_path,monkeypatch,case):
    import shutil
    run_id,report,_a,_s,_at,_ledger,output=native(tmp_path)
    prepared=tmp_path/'prepared';output.rename(prepared)
    write(tmp_path/'config/native-calibration-capture.json',capture.POLICY)
    if case!='disabled': write(tmp_path/'config/calibration-release-shadow.json',job.POLICY)
    def child(*args,**kwargs):
        for name in ('analysis.json','summary.json'):shutil.copy2(prepared/name,output/name)
        return Mock(returncode=0)
    runner=Mock(side_effect=child);monkeypatch.setattr(daily.subprocess,'run',runner)
    monkeypatch.setattr(capture,'capture',lambda *a,**k: {} if case=='no_capture' else {'receipt_id':'a'*64})
    observer=Mock(return_value={'status':'OBSERVED_SHADOW'})
    if case=='failure':observer.side_effect=OSError('failure')
    monkeypatch.setattr(job,'observe',observer)
    result=daily.run(tmp_path,run_id,sha(report),{'enabled_profiles':['gauss']},as_of=NOW)
    assert runner.call_count==1 and result['status']=='OBSERVED'
    assert observer.call_count==int(case in {'enabled','failure'})
    assert result['calibration_release_shadow']['status']=={'enabled':'OBSERVED_SHADOW',
        'no_capture':'SKIPPED_NO_VERIFIED_CAPTURE','failure':'UNAVAILABLE','disabled':'DISABLED'}[case]
