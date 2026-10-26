"""Actual Windows scheduled-wrapper exits; every child job is a TESTONLY stub."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'scripts/run-arthur-scheduled.ps1'
pytestmark=pytest.mark.skipif(sys.platform != 'win32', reason='Windows scheduled-wrapper contract')
MARKER='SYNTHETIC_SECRET_DO_NOT_LOG'

def guard(path):
    assert not path.resolve().is_relative_to(ROOT.resolve()), "TESTONLY fixtures must stay outside the production checkout"
    path.mkdir(parents=True,exist_ok=True)
    return path

def fixture_root(path,source=CANDIDATE,background=True,activation=True):
    root=guard(path);scripts=guard(root/'scripts');py=guard(root/'.venv/Scripts')
    (root/'TESTONLY.json').write_text(json.dumps({'production_jobs_forbidden':True,'scope':'scheduled-background-outcome-contract'}))
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
    for stage,name in [('odds','run-arthur-odds-monitor.py'),('nightly','run-arthur-nightly.py')]+([('background','run-arthur-background-history.py')] if background else []):
        (scripts/name).write_text(template.replace('STAGE',repr(stage)),encoding='utf-8')
    if activation:
        target=root/'data/arthur/historical-background/activation.json';target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'TESTONLY':True,'enabled':True}),encoding='utf-8')
    return root

def run(root,nightly=0,background=0,check=False,diag=0):
    env={**os.environ,'TESTONLY_NIGHTLY_EXIT':str(nightly),'TESTONLY_BACKGROUND_EXIT':str(background),
         'TESTONLY_ODDS_EXIT':'0','TESTONLY_DIAG_EXIT':str(diag)}
    args=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(root/'scripts/run-arthur-scheduled.ps1')]
    if check:args.append('-CheckOnly')
    p=subprocess.run(args,cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,check=False)
    log=root/'logs/arthur-scheduler.log';text=log.read_text(encoding='utf-8-sig') if log.exists() else ''
    assert MARKER not in p.stdout+p.stderr+text
    events=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
    return p,text,events

@pytest.mark.parametrize('nightly,bg,present,active,expected,stages',[
 (0,0,True,True,0,[]),(0,7,True,True,7,['background']),
 (2,0,True,True,2,['nightly']),(2,7,True,True,2,['nightly','background']),
 (0,0,False,True,1,['background']),(0,0,False,False,0,[]),
 (0,7,True,False,7,['background']),(2,0,False,True,2,['nightly','background'])],
 ids=['success-warning','background-error','nightly-error','both-errors','configured-missing','uninstalled','present-error-no-activation','nightly-and-missing'])
def test_exit_contract(tmp_path,nightly,bg,present,active,expected,stages):
    root=fixture_root(tmp_path/'w',background=present,activation=active)
    p,log,events=run(root,nightly,bg)
    assert p.returncode==expected
    assert [x['stage'] for x in events]==['odds','nightly']+(['background'] if present else [])
    assert [x['args'] for x in events]==[[],['--scheduled']]+([[]] if present else [])
    assert ('Arthur nightly check failed' in log)==('nightly' in stages)
    assert ('Arthur background history check failed' in log)==('background' in stages)
    assert len(log.splitlines())==len(stages)
    if not stages:assert not (root/'logs').exists()
    assert set((root/'credential-read.txt').read_text().splitlines())=={'XAI_API_KEY','THE_STATS_API_KEY'}

@pytest.mark.parametrize('code',[0,5])
def test_check_only(tmp_path,code):
    root=fixture_root(tmp_path/'w')
    p,log,events=run(root,nightly=7,background=9,check=True,diag=code)
    assert p.returncode==code and 'CHECK_ONLY' in p.stdout
    assert not log and not events and not (root/'credential-read.txt').exists() and not(root/'logs').exists()
