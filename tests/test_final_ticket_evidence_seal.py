"""Real nightly staging/ledger/archive contracts, no model or provider service."""
import json
from copy import deepcopy
from datetime import date
from hashlib import sha256
from unittest.mock import Mock

import pytest
from test_nightly import setup_workflow

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import ticket_evidence as evidence_module
from goal_analysis.strategy_provenance import attach_strategy_provenance


def workflow(root):
    flow,clock,calls=setup_workflow(root)
    generate=flow.generator
    original_build=evidence_module.build_ticket_evidence
    generated=[]
    def generator(target,settings,job):
        bundle=generate(target,settings,job)
        report=bundle['artifacts']['report']
        # Same alias shape as real tickets/profile_proposals. Two provenance
        # passes and the scheduler's final run ID reproduced the actual defect.
        report['profile_proposals']=[report['recommendations'][0]]
        attach_strategy_provenance(report,settings)
        report['ticket_evidence']=original_build(report,bundle['artifacts'])
        generated.append(deepcopy(bundle))
        return bundle
    flow.generator=generator
    return flow,clock,calls,generated


def load_outputs(flow,outcome):
    run_id=flow._stage(outcome['job'],'generation')['run_id']
    root=flow.root
    staging=json.loads((root/'data/arthur/nightly-staging'/f'{run_id}.json').read_text('utf-8'))
    saved=flow._saved_report(run_id)
    exported=json.loads((root/'reports/arthur'/run_id/'report.json').read_text('utf-8'))
    return run_id,staging,saved,exported


def test_new_final_run_card_versions_and_disk_ledger_export_share_one_valid_evidence_bundle(tmp_path):
    flow,_,calls,generated=workflow(tmp_path)
    outcome=flow.run()
    run_id,staging,saved,exported=load_outputs(flow,outcome)
    assert calls.count('generation')==1
    assert staging['artifacts']['report']==saved==exported
    assert saved['run_id']==saved['ticket_evidence']['run_id']==run_id
    evidence_module.validate_ticket_evidence(saved['ticket_evidence'],saved,staging['artifacts'])
    old=generated[0]['artifacts']['report']
    assert old['ticket_evidence']['run_id']!=run_id
    assert saved['ticket_evidence']['bundle_sha256']!=old['ticket_evidence']['bundle_sha256']
    # Exactly the original probabilities/prices/legs/status/stakes remain.
    for group in ['recommendations','profile_proposals']:
        for first,last in zip(old[group],saved[group],strict=True):
            assert first['legs']==last['legs']
            assert first['status']==last['status'] and first['stake_eur']==last['stake_eur']
            assert first['combined_price']==last['combined_price']
    unsigned={k:v for k,v in saved.items() if k!='report_sha256'}
    assert canonical_sha256(unsigned)==saved['report_sha256']


@pytest.mark.parametrize('fault',['build','memory_validation','disk_validation'])
def test_optional_audit_failure_never_regenerates_or_changes_selection_and_is_not_false_valid(tmp_path,monkeypatch,fault):
    flow,_,calls,generated=workflow(tmp_path)
    real=evidence_module.validate_ticket_evidence
    validations=[]
    if fault=='build':
        monkeypatch.setattr(evidence_module,'build_ticket_evidence',Mock(side_effect=ValueError('synthetic-sensitive')))
    else:
        def validate(*args):
            validations.append(1)
            if fault=='memory_validation' or len(validations)==2:
                raise ValueError('synthetic-sensitive')
            return real(*args)
        monkeypatch.setattr(evidence_module,'validate_ticket_evidence',validate)
    outcome=flow.run()
    _,staging,saved,exported=load_outputs(flow,outcome)
    assert calls.count('generation')==1 and staging['artifacts']['report']==saved==exported
    assert saved['ticket_evidence']=={'mode':'AUDIT_ONLY','status':'UNAVAILABLE',
        'reason':'FINAL_TICKET_EVIDENCE_FAILED','decision_effect':'NONE'}
    assert 'synthetic-sensitive' not in json.dumps(saved)
    assert saved['recommendations'][0]['legs']==generated[0]['artifacts']['report']['recommendations'][0]['legs']
    assert saved['report_sha256']==canonical_sha256({k:v for k,v in saved.items() if k!='report_sha256'})


@pytest.mark.parametrize('field',['run_id','card_version','source_price','card_status'])
def test_strict_validator_still_rejects_later_changes_after_final_seal(tmp_path,field):
    flow,_,_,_=workflow(tmp_path)
    outcome=flow.run();_,staging,saved,_=load_outputs(flow,outcome)
    report=deepcopy(saved);artifacts=deepcopy(staging['artifacts']);artifacts['report']=report
    if field=='run_id':report['run_id']='changed'
    elif field=='card_version':report['recommendations'][0]['method_version']='changed'
    elif field=='source_price':report['recommendations'][0]['legs'][0]['decimal_price']=12.0
    else:report['recommendations'][0]['status']='READY'
    with pytest.raises(ValueError,match='differs'):
        evidence_module.validate_ticket_evidence(report['ticket_evidence'],report,artifacts)


def test_existing_immutable_run_and_staging_are_not_resealed_on_repeat(tmp_path,monkeypatch):
    flow,_,calls,_=workflow(tmp_path)
    outcome=flow.run();run_id,_,saved,_=load_outputs(flow,outcome)
    path=tmp_path/'data/arthur/nightly-staging'/f'{run_id}.json'
    before=sha256(path.read_bytes()).hexdigest()
    builder=Mock(side_effect=AssertionError('NO_RESEAL_EXISTING_RUN'))
    monkeypatch.setattr(evidence_module,'build_ticket_evidence',builder)
    assert flow.run()['status']=='ALREADY_FINISHED'
    job=flow.store.get(outcome['job']['target_date']);flow._generation_stage(job,flow._stage(job,'generation'))
    assert not builder.called and calls.count('generation')==1
    assert flow._saved_report(run_id)==saved and sha256(path.read_bytes()).hexdigest()==before


def test_preexisting_staging_from_before_ledger_commit_is_not_rewritten_or_regenerated(tmp_path,monkeypatch):
    from goal_analysis.jobs.nightly import schedule_plan
    flow,clock,calls,_generated=workflow(tmp_path)
    job=flow._new_job(schedule_plan(clock[0]));stage=flow._stage(job,'generation');stage.update(attempts=1,run_id='prior-staged-run')
    bundle=flow.generator(date.fromisoformat(job['target_date']),job['settings'],job)
    report=bundle['artifacts']['report'];report['run_id']=stage['run_id']
    attach_strategy_provenance(report,job['settings'])
    path=tmp_path/'data/arthur/nightly-staging'/f"{stage['run_id']}.json";path.parent.mkdir(parents=True)
    path.write_text(json.dumps(bundle),'utf-8');before=sha256(path.read_bytes()).hexdigest()
    builder=Mock(side_effect=AssertionError('NO_REWRITE_LEGACY_CHECKPOINT'))
    monkeypatch.setattr(evidence_module,'build_ticket_evidence',builder)
    flow._generation_stage(job,stage)
    assert not builder.called and calls.count('generation')==1
    assert sha256(path.read_bytes()).hexdigest()==before
    assert flow._saved_report(stage['run_id'])==report


@pytest.mark.parametrize('change',['run_id','price'])
def test_validation_reads_actual_new_file_and_rejects_disk_divergence_without_changing_selection(tmp_path,monkeypatch,change):
    from pathlib import Path

    flow,_,calls,generated=workflow(tmp_path)
    write=Path.write_text;attempts=[]
    def altered(path,text,*args,**kwargs):
        if path.suffix=='.tmp' and 'nightly-staging' in path.parts:
            attempts.append(1)
            if len(attempts)==1:
                value=json.loads(text);report=value['artifacts']['report']
                if change=='run_id':report['run_id']='synthetic-disk-divergence'
                else:report['recommendations'][0]['legs'][0]['decimal_price']=99.
                text=json.dumps(value)
        return write(path,text,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',altered)
    outcome=flow.run();run_id,staging,saved,exported=load_outputs(flow,outcome)
    assert len(attempts)==2 and calls.count('generation')==1
    assert saved==staging['artifacts']['report']==exported
    assert saved['run_id']==run_id and saved['ticket_evidence']['status']=='UNAVAILABLE'
    assert saved['recommendations'][0]['legs']==generated[0]['artifacts']['report']['recommendations'][0]['legs']


def test_unreadable_new_staging_file_has_no_durable_commit_or_second_generation(tmp_path,monkeypatch):
    from datetime import timedelta
    from pathlib import Path

    flow,clock,calls,_=workflow(tmp_path)
    write=Path.write_text
    def corrupt(path,text,*args,**kwargs):
        if path.suffix=='.tmp' and 'nightly-staging' in path.parts:text='{invalid'
        return write(path,text,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',corrupt)
    outcome=flow.run();stage=flow._stage(outcome['job'],'generation')
    assert stage['status']=='BLOCKED' and stage['reason']=='UNKNOWN_EXECUTION_REQUIRES_REVIEW'
    assert not (tmp_path/'data/arthur/nightly-staging'/f"{stage['run_id']}.json").exists()
    clock[0]+=timedelta(minutes=16)
    assert flow.run()['status']=='ALREADY_FINISHED' and calls.count('generation')==1
