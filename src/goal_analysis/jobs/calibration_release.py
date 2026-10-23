"""Physical SHADOW candidate registration, prospective observation and review.

Own immutable research files only. No config, learning DB, ledger, model fit,
network, approval or execution writer is available from this module.
"""
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic

from goal_analysis.jobs import native_forecast_capture as capture
from goal_analysis.jobs.calibration_shadow import inspect_calibration
from goal_analysis.learning.calibration import GROUP, shadow_probability
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.learning.release_gate import POLICY_RELEASE, VERSION, assess
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot, validate
from goal_analysis.storage.training_datasets import DatasetStore

POLICY = {'enabled':True,'mode':'SHADOW','max_seconds':5}
SOURCE_FILES = ('learning/release_gate.py','jobs/calibration_release.py','learning/calibration.py',
    'learning/calibration_sources.py','jobs/calibration_shadow.py','jobs/native_forecast_capture.py',
    'learning/__init__.py','jobs/daily_native_shadow.py')


def implementation():
    folder=Path(__file__).resolve().parents[1]
    return sha({name:sha256((folder/name).read_bytes()).hexdigest() for name in SOURCE_FILES})


def enabled(root):
    path=Path(root)/'config/calibration-release-shadow.json'
    return path.is_file() and path.stat().st_size<4096 and sha(json.loads(path.read_bytes()))==sha(POLICY)


def _home(root):
    root=Path(root).resolve(); folder=root/'data/arthur/calibration-release-shadow'
    if not folder.resolve().is_relative_to(root):
        raise ValueError('RELEASE_PATH_ESCAPE')
    return root,folder


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):
        raise ValueError('RELEASE_SHA256_ID_REQUIRED')
    return value


def _evidence(root, artifact_id, cutoff):
    store=DatasetStore(root/'data/arthur/calibration-shadow')
    path=store.path(_id(artifact_id))
    if not 0<path.stat().st_size<=capture.MAX_BYTES:
        raise ValueError('RELEASE_ARTIFACT_BYTE_BUDGET')
    archived=store.read(artifact_id)
    if archived['manifest']['role']!='calibration_candidate_report' or len(archived['rows'])!=1:
        raise ValueError('RELEASE_CALIBRATION_ARTIFACT_REQUIRED')
    result=archived['rows'][0]; calibration=result['calibration']; dataset=validate(result['source_admission']['dataset'])
    at=utc(calibration['as_of'])
    if (at>cutoff or utc(archived['manifest']['as_of'])!=at or result['mode']!='SHADOW'
            or result['live_decision_effect'] is not False or calibration['input_dataset_id']!=dataset['manifest']['dataset_id']):
        raise ValueError('RELEASE_FUTURE_OR_UNBOUND_CALIBRATION_ARTIFACT')
    # Re-read actual files/SQLite/raw receipts at the ORIGINAL cutoff. Future
    # files can add diagnostic gaps, but not change the complete forecast rows.
    rebuilt=inspect_calibration(root,as_of=at,enabled=True,clock=lambda:cutoff)
    physical=rebuilt['source_admission']['dataset']
    key=lambda r:(r['forecast_id'],sha(r))
    if sha(sorted(dataset['rows'],key=key))!=sha(sorted(physical['rows'],key=key)):
        raise ValueError('RELEASE_FORECAST_POPULATION_NOT_PHYSICALLY_REPRODUCED')
    return dataset,calibration


def _read(work, cutoff):
    if not work.resolve().is_relative_to(work.parent.parent.resolve()):
        raise ValueError('RELEASE_BUNDLE_PATH_ESCAPE')
    body,receipt,claim=(capture._read(work/name) for name in ('body.json','receipt.json','claim.json'))
    if (body['version']!=VERSION or receipt['version']!=VERSION or claim['version']!=VERSION
            or body['mode']!='SHADOW' or body.get('live_approved',False) is not False
            or body.get('live_decision_effect',False) is not False
            or body['implementation_sha256']!=implementation()
            or receipt['body_sha256']!=sha(body) or receipt['receipt_id']!=sha({k:v for k,v in receipt.items() if k!='receipt_id'})
            or receipt['work_id']!=work.name or claim['work_id']!=work.name
            or not utc(claim['started_at'])<=utc(receipt['available_at'])<=cutoff):
        raise ValueError('RELEASE_IMMUTABLE_RECEIPT_OR_VERSION_CONFLICT')
    return body,receipt


def _seal(work, body, clock):
    if not work.resolve().is_relative_to(work.parent.parent.resolve()):
        raise ValueError('RELEASE_BUNDLE_PATH_ESCAPE')
    work.mkdir(parents=True,exist_ok=False)
    started=utc(clock())
    capture._new(work/'claim.json',{'version':VERSION,'work_id':work.name,'started_at':started.isoformat()})
    capture._new(work/'body.json',body)
    assert sha(capture._read(work/'body.json'))==sha(body)
    available=utc(clock())
    if available<started:
        raise ValueError('RELEASE_CLOCK_ROLLBACK')
    receipt={'version':VERSION,'work_id':work.name,'body_sha256':sha(body),'available_at':available.isoformat()}
    receipt['receipt_id']=sha(receipt);capture._new(work/'receipt.json',receipt)
    return body,receipt


def _registrations(folder, cutoff):
    path=folder/'registrations'
    paths=sorted(path.iterdir()) if path.is_dir() else []
    if len(paths)>POLICY_RELEASE['maximum_registrations']:
        raise ValueError('RELEASE_REGISTRATION_BUDGET')
    result=[]
    for work in paths:
        _id(work.name)
        body,receipt=_read(work,cutoff)
        candidate=body['candidate']
        if candidate['candidate_id']!=sha({k:v for k,v in candidate.items() if k!='candidate_id'}):
            raise ValueError('RELEASE_CANDIDATE_HASH_CONFLICT')
        expected=sha([body['calibration_artifact_id'],body['candidate']['candidate_id'],implementation()])
        if expected!=work.name or body['kind']!='REGISTERED_SHADOW_CANDIDATE':
            raise ValueError('RELEASE_REGISTRATION_ID_CONFLICT')
        result.append((work.name,body,receipt))
    return result


def register_candidate(root, artifact_id, candidate_id, *, clock=lambda:datetime.now(UTC)):
    root,folder=_home(root);cutoff=utc(clock());_id(candidate_id)
    dataset,calibration=_evidence(root,artifact_id,cutoff)
    assessed=assess(dataset,calibration,[],as_of=cutoff)
    choices=[g for g in assessed['groups'] if g['candidate'] is not None and g['candidate']['candidate_id']==candidate_id]
    if len(choices)!=1:
        raise ValueError('RELEASE_EXACT_CANDIDATE_REQUIRED')
    value=choices[0]
    required=('walk_forward','supported_observer_cohort','thirty_real_forecast_days','parameter_change')
    if not all(value['checks'][k] for k in required):
        return {'status':'BLOCKED','blocked_by':[k for k in required if not value['checks'][k]],'new_registration':False}
    work_id=sha([artifact_id,candidate_id,implementation()]); work=folder/'registrations'/work_id
    if work.exists():
        _body,receipt=_read(work,cutoff)
        return {'status':'REGISTERED_SHADOW','registration_id':work_id,'available_at':receipt['available_at'],'new_registration':False}
    _registrations(folder,cutoff)
    if (folder/'registrations').exists() and len(list((folder/'registrations').iterdir()))>=8:
        raise ValueError('RELEASE_REGISTRATION_BUDGET')
    body={'version':VERSION,'kind':'REGISTERED_SHADOW_CANDIDATE','mode':'SHADOW',
        'implementation_sha256':implementation(),'calibration_artifact_id':artifact_id,
        'calibration_report_sha256':calibration['report_sha256'],'candidate':value['candidate'],
        'baseline':value['baseline'],'proposed_parameters':value['proposed_parameters'],'live_approved':False}
    _,receipt=_seal(work,body,clock)
    return {'status':'REGISTERED_SHADOW','registration_id':work_id,'available_at':receipt['available_at'],
            'new_registration':True,'live_approved':False,'apply_allowed':False}


def observe(root, run_id, *, max_seconds=5, clock=lambda:datetime.now(UTC)):
    if type(max_seconds) is not int or not 1<=max_seconds<=5:
        raise ValueError('RELEASE_OBSERVER_TIME_BUDGET')
    deadline=monotonic()+max_seconds;root,folder=_home(root); now=utc(clock())
    registrations=_registrations(folder,now)
    if not registrations:
        return {'status':'NO_REGISTERED_CANDIDATES','new_observations':0,'external_requests':0,'live_decision_effect':False}
    _,output,_=capture._paths(root,run_id);work=folder/'observations'/run_id
    if work.exists():
        if not (work/'receipt.json').is_file():
            return {'status':'UNCERTAIN_PREVIOUS_OBSERVATION','new_observations':0,'automatic_retry':False}
        _read(work,now)
        return {'status':'ALREADY_OBSERVED','new_observations':0}
    original,first=capture._bundle(root,root/'data/arthur/native-calibration-capture'/run_id,now)
    analysis,summary,settings,_=capture._inputs(root,output,run_id,original['report_sha256'])
    with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        rows,_,_=capture._extract(root,output,analysis,summary,settings,
            Sources(root,db,dataset_root=output/'fit/datasets',deadline=deadline))
    if sha(rows)!=sha(original['rows']):
        raise ValueError('RELEASE_CAPTURE_ORIGINAL_FORECAST_DIFFERENCE')
    observed=[]
    for registration_id,registration,registered in registrations:
        candidate=registration['candidate']
        for raw in rows:
            if monotonic()>=deadline:
                raise ValueError('RELEASE_OBSERVER_TIME_BUDGET')
            if any(raw[k]!=candidate['group'][k] for k in GROUP) or utc(registered['available_at'])>=utc(raw['model_as_of']):
                continue
            row={k:v for k,v in raw.items() if k!='target'}
            row['saved_at']=first['available_at'];row['forecast_id']=sha([first['receipt_id'],sha(raw)])
            if utc(row['kickoff'])<=now+timedelta(minutes=15):
                continue
            transformed=shadow_probability(candidate,row,as_of=now)
            observed.append({'registration_id':registration_id,'candidate_id':candidate['candidate_id'],
                'registered_at':registered['available_at'],'forecast_id':row['forecast_id'],
                'capture_receipt_id':first['receipt_id'],'calibrated_probability':transformed['probability']})
    if len(observed)>10000 or monotonic()>=deadline:
        raise ValueError('RELEASE_OBSERVATION_ROW_OR_TIME_BUDGET')
    body={'version':VERSION,'kind':'PROSPECTIVE_CANDIDATE_OBSERVATIONS','mode':'SHADOW',
        'implementation_sha256':implementation(),'run_id':run_id,'capture_sha256':first['capture_sha256'],
        'decision_as_of':now.isoformat(),'observations':observed,'live_decision_effect':False}
    _,receipt=_seal(work,body,clock)
    # The final receipt, not the earlier transform clock, controls eligibility.
    starts={sha([first['receipt_id'],sha(r)]):utc(r['kickoff']) for r in rows}
    eligible=sum(starts[o['forecast_id']]>utc(receipt['available_at'])+timedelta(minutes=15) for o in observed)
    return {'status':'OBSERVED_SHADOW' if eligible else 'GAPS','new_observations':len(observed),
        'eligible_at_commit':eligible,'available_at':receipt['available_at'],'external_requests':0,
        'additional_model_fits':0,'live_decision_effect':False}


def _observations(root, folder, cutoff):
    deadline=monotonic()+30
    path=folder/'observations'; paths=sorted(path.iterdir()) if path.is_dir() else []
    if len(paths)>512:
        raise ValueError('RELEASE_OBSERVATION_RUN_BUDGET')
    if not paths:
        return []
    forecasts=capture.read_forecasts(root,as_of=cutoff)
    known={r['forecast_id']:r for r in forecasts['dataset']['rows']}
    registrations={i:(b,r) for i,b,r in _registrations(folder,cutoff)}
    result=[]; total=0
    for work in paths:
        if monotonic()>=deadline:
            raise ValueError('RELEASE_OBSERVATION_READER_TIME_BUDGET')
        _,_,safe=capture._paths(root,work.name)
        body,receipt=_read(work,cutoff)
        original,source=capture._bundle(root,safe,cutoff)
        total+=sum(p.stat().st_size for p in work.glob('*.json'))
        if total>128_000_000:
            raise ValueError('RELEASE_OBSERVATION_TOTAL_BYTE_BUDGET')
        if (body['kind']!='PROSPECTIVE_CANDIDATE_OBSERVATIONS' or body['run_id']!=work.name
                or body['capture_sha256']!=source['capture_sha256']):
            raise ValueError('RELEASE_OBSERVATION_CAPTURE_BINDING')
        decision=utc(body['decision_as_of'])
        if not utc(source['available_at'])<=decision<=utc(receipt['available_at']):
            raise ValueError('RELEASE_OBSERVATION_DECISION_CLOCK')
        expected=[]
        for registration_id,(registration,registered) in registrations.items():
            candidate=registration['candidate']
            if utc(registered['available_at'])>decision:
                continue
            for raw in original['rows']:
                if (any(raw[k]!=candidate['group'][k] for k in GROUP)
                        or utc(registered['available_at'])>=utc(raw['model_as_of'])
                        or utc(raw['kickoff'])<=decision+timedelta(minutes=15)):
                    continue
                row={**raw,'saved_at':source['available_at']}
                probability=shadow_probability(candidate,row,as_of=decision)['probability']
                expected.append({'registration_id':registration_id,'candidate_id':candidate['candidate_id'],
                    'registered_at':registered['available_at'],'forecast_id':sha([source['receipt_id'],sha(raw)]),
                    'capture_receipt_id':source['receipt_id'],'calibrated_probability':probability})
        if sha(expected)!=sha(body['observations']):
            raise ValueError('RELEASE_PARTIAL_OR_ALTERED_SHADOW_OBSERVATION_POPULATION')
        for observation in body['observations']:
            registration,registered=registrations[observation['registration_id']]
            candidate=registration['candidate']; forecast=known[observation['forecast_id']]
            if (observation['candidate_id']!=candidate['candidate_id']
                    or observation['registered_at']!=registered['available_at']
                    or observation['capture_receipt_id']!=source['receipt_id']
                    or source['receipt_id'] not in forecast['source_receipt_ids']):
                raise ValueError('RELEASE_OBSERVATION_REGISTRATION_OR_FORECAST_CONFLICT')
            result.append({**observation,'forecast':forecast,'shadow_available_at':receipt['available_at']})
            if len(result)>10000:
                raise ValueError('RELEASE_TOTAL_OBSERVATION_ROW_BUDGET')
    return result


def review_release(root, artifact_id, *, as_of, enabled=False, clock=lambda:datetime.now(UTC)):
    cutoff=utc(as_of)
    if type(enabled) is not bool or cutoff>utc(clock()):
        raise ValueError('RELEASE_EXPLICIT_FLAG_OR_FUTURE_CUTOFF')
    if not enabled:
        return {'version':VERSION,'status':'DISABLED','mode':'SHADOW','apply_allowed':False}
    root,folder=_home(root)
    dataset,calibration=_evidence(root,artifact_id,cutoff)
    observations=_observations(root,folder,cutoff)
    result=assess(dataset,calibration,observations,as_of=cutoff)
    if result['groups'] and (folder/'registrations').is_dir():
        native=capture.read_forecasts(root,as_of=cutoff)['dataset']['rows']
        registrations=_registrations(folder,cutoff)
        end=cutoff.replace(hour=0,minute=0,second=0,microsecond=0);start=end-timedelta(days=7)
        for value in result['groups']:
            candidate=value['candidate'];missing=set()
            if candidate is None:
                continue
            seen={o['forecast_id'] for o in observations if o['candidate_id']==candidate['candidate_id']}
            for _,registration,receipt in registrations:
                if registration['candidate']['candidate_id']!=candidate['candidate_id']:
                    continue
                for row in native:
                    if (all(row[k]==candidate['group'][k] for k in GROUP)
                            and utc(receipt['available_at'])<utc(row['model_as_of'])
                            and start<=utc(row['saved_at'])<end and row['forecast_id'] not in seen):
                        missing.add(row['forecast_id'])
            value['missing_shadow_forecasts']=len(missing)
            value['checks']['complete_shadow_population']=not missing
            if missing:
                value['blocked_by']=sorted(set(value['blocked_by']+['complete_shadow_population']))
                value['status']='BLOCKED'
        result['status']='REVIEW_AVAILABLE' if any(g['status']=='ELIGIBLE_FOR_MANUAL_REVIEW' for g in result['groups']) else 'BLOCKED'
    result.update(calibration_artifact_id=artifact_id,implementation_sha256=implementation())
    result['report_sha256']=sha({k:v for k,v in result.items() if k!='report_sha256'})
    return result


def archive_review(root, result):
    if (result.get('version')!=VERSION or result.get('mode')!='SHADOW' or result.get('apply_allowed') is not False
            or result.get('automatic_parameter_writes') is not False or result.get('live_decision_effect') is not False
            or result['report_sha256']!=sha({k:v for k,v in result.items() if k!='report_sha256'})):
        raise ValueError('RELEASE_REPORT_HASH_OR_MODE_CONFLICT')
    root,folder=_home(root)
    value=snapshot([result],role='calibration_release_review',as_of=result['as_of'],
        recipe={'version':VERSION,'policy':POLICY_RELEASE},lineage={'calibration_artifact_id':result['calibration_artifact_id']})
    store=DatasetStore(folder/'reviews');created=store.put(value)
    return {'created':created,'dataset_id':value['manifest']['dataset_id'],'path':str(store.path(value['manifest']['dataset_id'])),
            'apply_allowed':False,'live_approved':False}
