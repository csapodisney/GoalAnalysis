"""Artifact-only actual PowerShell exit controls; every child is a TESTONLY stub."""
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'scripts/run-arthur-scheduled.ps1'
RUNNER=ROOT/'scripts/run-arthur-odds-monitor.py'
pytestmark=pytest.mark.skipif(sys.platform != 'win32',reason='Windows scheduled-wrapper contract')
MARKER='SYNTHETIC_SECRET_DO_NOT_LOG'

def guard(path):
    assert not path.resolve().is_relative_to(ROOT.resolve()), 'TESTONLY fixtures outside production checkout'
    path.mkdir(parents=True,exist_ok=True)
    return path

def fixture_root(path,source=CANDIDATE,background=True,activation=True,odds_present=True,odds_activation=True):
    root=guard(path);scripts=guard(root/'scripts');py=guard(root/'.venv/Scripts')
    (root/'TESTONLY.json').write_text(json.dumps({'production_jobs_forbidden':True,'scope':'scheduled-odds-outcome-contract'}))
    shutil.copy2(ROOT/'.venv/Scripts/python.exe',py/'python.exe')
    shutil.copy2(ROOT/'.venv/pyvenv.cfg',root/'.venv/pyvenv.cfg')
    shutil.copy2(source,scripts/'run-arthur-scheduled.ps1')
    (scripts/'arthur-secrets.ps1').write_text('''
function Import-ArthurSecret {
 param([string]$ProjectRoot,[string]$EnvironmentName,[switch]$SavedOnly)
 if (-not $SavedOnly) { throw 'SavedOnly omitted' }
 Add-Content -LiteralPath (Join-Path $ProjectRoot 'credential-read.txt') -Value $EnvironmentName
 throw 'SYNTHETIC_SECRET_DO_NOT_LOG'
}
''',encoding='utf-8')
    template='''
import json,os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
assert json.loads((root/'TESTONLY.json').read_text())['production_jobs_forbidden']
stage=STAGE
if '--check-schedule' in sys.argv:
 assert stage=='nightly' and not (root/'credential-read.txt').exists()
 print(json.dumps({'status':'CHECK_ONLY'}))
 sys.exit(int(os.environ['TESTONLY_DIAG_EXIT']))
with (root/'events.jsonl').open('a') as f:f.write(json.dumps({'stage':stage,'args':sys.argv[1:]})+'\\n')
print('SYNTHETIC_SECRET_DO_NOT_LOG')
print('SYNTHETIC_SECRET_DO_NOT_LOG',file=sys.stderr)
sys.exit(int(os.environ['TESTONLY_'+stage.upper()+'_EXIT']))
'''
    for stage,name in ([('odds','run-arthur-odds-monitor.py')] if odds_present else [])+[('nightly','run-arthur-nightly.py')]+([('background','run-arthur-background-history.py')] if background else []):
        (scripts/name).write_text(template.replace('STAGE',repr(stage)),encoding='utf-8')
    if activation:
        target=root/'data/arthur/historical-background/activation.json';target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'TESTONLY':True,'enabled':True}),encoding='utf-8')
    if odds_activation:
        target=root/'data/arthur/prospective-odds/activation.json';target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'TESTONLY':True,'enabled':True}),encoding='utf-8')
    return root

def run(root,nightly=0,background=0,check=False,diag=0,odds=0):
    env={**os.environ,'TESTONLY_NIGHTLY_EXIT':str(nightly),'TESTONLY_BACKGROUND_EXIT':str(background),
         'TESTONLY_ODDS_EXIT':str(odds),'TESTONLY_DIAG_EXIT':str(diag)}
    args=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(root/'scripts/run-arthur-scheduled.ps1')]
    if check:args.append('-CheckOnly')
    p=subprocess.run(args,cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,check=False)
    log=root/'logs/arthur-scheduler.log';text=log.read_text(encoding='utf-8-sig') if log.exists() else ''
    assert MARKER not in p.stdout+p.stderr+text
    events=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
    return p,text,events


CLI_BOOTSTRAP='"""Run copied actual CLI with only TESTONLY failure/no-fixture input and denied IO."""\nimport contextlib\nimport io\nimport json\nimport os\nfrom pathlib import Path\nimport runpy\nimport socket\nimport sqlite3\nimport sys\nfrom unittest.mock import patch\nsys.path.insert(0,sys.argv[3])\nfrom goal_analysis.jobs import odds_monitor,prospective_odds\nfrom goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient\nfrom goal_analysis.storage import Database\nfrom goal_analysis.storage.temporal import TemporalStore\nroot=Path(sys.argv[1]).resolve();mode=sys.argv[2]\nassert (root/\'TESTONLY.json\').is_file() and not root.is_relative_to(Path(sys.argv[3]).resolve().parent)\nblocked=[]\ndef deny(*a,**k):blocked.append(\'FORBIDDEN_IO\');raise AssertionError(\'NO_PRODUCTION_IO\')\ndef optional(*a,**k):\n if mode==\'optional-error\':raise RuntimeError(\'SYNTHETIC_SECRET_DO_NOT_LOG\')\n return {\'status\':\'DISARMED_OR_SOURCE_CHANGED\',\'requests\':0,\'llm_calls\':0}\nstream=io.StringIO();code=0\nwith patch.dict(os.environ,{\'THE_STATS_API_KEY\':\'\',\'XAI_API_KEY\':\'\'}),patch.object(sqlite3,\'connect\',deny),patch.object(Database,\'connect\',deny),patch.object(TemporalStore,\'capture\',deny),patch.object(TheStatsPrimaryClient,\'__init__\',deny),patch.object(socket,\'create_connection\',deny),patch.object(socket.socket,\'connect\',deny),patch.object(prospective_odds,\'tick\',optional),patch.object(odds_monitor,\'saved_monitor_input\',return_value={\'fixtures\':[],\'recommendations\':[]}):\n try:\n  with contextlib.redirect_stdout(stream):runpy.run_path(str(root/\'scripts/run-arthur-odds-monitor.py\'),run_name=\'__main__\')\n except SystemExit as e:\n  code=0 if e.code is None else (e.code if type(e.code) is int else 1)\nassert not blocked\nresult=json.loads(stream.getvalue());assert \'SYNTHETIC_SECRET\' not in stream.getvalue()\nprint(json.dumps({\'actual_cli_result\':result,\'blocked_io_attempts\':blocked,\'api_fit_gen_receipt_import\':0,\'actual_cli_exit_code\':code}))\n\nsys.exit(code)\n'

@pytest.mark.parametrize('odds,nightly,bg,present,active,expected,stages',[
 (7,0,0,True,True,7,['odds']),(7,2,0,True,True,2,['nightly','odds']),
 (7,0,3,True,True,7,['odds','background']),(7,2,3,True,True,2,['nightly','odds','background']),
 (0,0,0,True,True,0,[]),(0,0,0,False,True,1,['odds']),
 (0,0,0,False,False,0,[]),(7,0,0,True,False,7,['odds']),
 (0,2,0,False,True,2,['nightly','odds'])],ids=['odds-error','primary-priority','odds-before-bg','all-errors','warning-zero','configured-missing','uninstalled','present-no-activation','nightly-missing'])
def test_native_matrix(tmp_path,odds,nightly,bg,present,active,expected,stages):
 root=fixture_root(tmp_path/'w',odds_present=present,odds_activation=active)
 p,log,events=run(root,nightly=nightly,background=bg,odds=odds)
 assert p.returncode==expected
 assert [e['stage'] for e in events]==(['odds'] if present else [])+['nightly','background']
 assert [e['args'] for e in events]==([[]] if present else [])+[['--scheduled'],[]]
 assert len(log.splitlines())==len(stages)
 for key,text in [('nightly','Arthur nightly check failed'),('odds','Arthur odds monitor check failed'),('background','Arthur background history check failed')]:assert (text in log)==(key in stages)
 if not stages:assert not(root/'logs').exists()

@pytest.mark.parametrize('code',[0,5])
def test_diagnostic(tmp_path,code):
 root=fixture_root(tmp_path/'w');p,log,events=run(root,odds=7,nightly=2,background=3,check=True,diag=code)
 assert p.returncode==code and 'CHECK_ONLY' in p.stdout
 assert not log and not events and not(root/'credential-read.txt').exists() and not(root/'logs').exists()

@pytest.mark.parametrize('result,expected',[
 ({'status':'UPDATED'},0),({'status':'NO_CHANGE','queries':[{'status':'FETCH_FAILED'}]},0),
 ({'status':'NO_UNLOCKED_FIXTURES'},0),({'status':'MONITOR_UNAVAILABLE','provider_calls':None},1),
 ({'status':'UNKNOWN'},1),({},1),(None,1),([],1),({'status':[]},1),({'status':{}},1)])
def test_primary_status(result,expected):
 m=runpy.run_path(str(RUNNER),run_name='exit_contract_import_only')
 assert m['process_exit_code'](result)==expected

@pytest.mark.parametrize('mode,expected',[('missing-config',1),('no-fixtures',0),('optional-error',0)])
def test_actual_cli(tmp_path,mode,expected):
 root=fixture_root(tmp_path/'w');(root/'scripts/run-arthur-odds-monitor.py').write_bytes(RUNNER.read_bytes())
 if mode!='missing-config':
  (root/'config').mkdir();(root/'config/daily223-live.json').write_bytes((ROOT/'config/daily223-live.json').read_bytes())
 bootstrap=root/'guarded_cli_bootstrap.py'
 bootstrap.write_text(CLI_BOOTSTRAP,encoding='utf-8')
 p=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-B',str(bootstrap),str(root),mode,str(ROOT/'src')],cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30,check=False)
 assert p.returncode==expected,p.stderr
 value=json.loads(p.stdout);assert not value['blocked_io_attempts'] and value['actual_cli_exit_code']==expected
 result=value['actual_cli_result']
 assert result['status']==('MONITOR_UNAVAILABLE' if mode=='missing-config' else 'NO_UNLOCKED_FIXTURES')
 assert result['provider_calls']==(None if mode=='missing-config' else 0)
 assert result['prospective_shadow']['status']==('PROSPECTIVE_UNAVAILABLE' if mode=='optional-error' else 'DISARMED_OR_SOURCE_CHANGED')
