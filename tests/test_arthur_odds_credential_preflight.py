"""Actual candidate CLI, actual pure planner, explicit TESTONLY data and denied IO."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
CLI_BOOTSTRAP='"""Actual copied CLI + actual pure planner, explicit synthetic fixtures, no production IO."""\nimport contextlib,io,json,os,runpy,socket,sqlite3,sys\nfrom datetime import UTC,datetime,timedelta\nfrom pathlib import Path\nfrom unittest.mock import patch\nroot=Path(sys.argv[1]).resolve();mode=sys.argv[2];source=Path(sys.argv[3]).resolve()\nassert (root/\'TESTONLY.json\').is_file() and not root.is_relative_to(source)\nsys.path.insert(0,str(source/\'src\'))\nfrom goal_analysis.jobs import odds_monitor as om,odds_monitor_providers as plans,prospective_odds as shadow\nfrom goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient\nfrom goal_analysis.storage import Database\nfrom goal_analysis.storage.temporal import TemporalStore\nclock=datetime.now(UTC);blocked=[];planned=[];dispatched=[];constructors=[];empty_tick_runs=[]\nfixture={\'fixture_id\':\'the_stats_api:mt_990001\',\'source_provider\':\'the_stats_api\',\'provider_fixture_id\':\'mt_990001\',\'provider_league_id\':\'comp_990001\',\'provider_home_team_id\':\'tm_990001\',\'provider_away_team_id\':\'tm_990002\',\'provider_season_id\':\'sn_990001\',\'kickoff\':(clock+timedelta(hours=1)).isoformat(),\'season\':2026,\'provider_status\':\'NS\',\'sport_key\':\'soccer_arthur_testonly\',\'home_team\':\'TESTONLY Home\',\'away_team\':\'TESTONLY Away\'}\nif mode in {\'unqueryable\',\'key-no-query\'}:fixture[\'provider_status\']=\'FT\'\nif mode==\'locked\':fixture[\'kickoff\']=(clock+timedelta(minutes=14)).isoformat()\nif mode==\'invalid\':fixture[\'provider_home_team_id\']=\'bad\'\nif mode==\'retired\':fixture[\'source_provider\']=\'api_football\'\nsaved={\'fixtures\':[] if mode==\'no-fixtures\' else [fixture],\'recommendations\':[]}\nif mode==\'duplicate\':saved[\'fixtures\'].append(dict(fixture))\noriginal_plan=plans.build_monitor_queries\noriginal_tick=om.OddsMonitor.tick\n\ndef deny(*a,**k):blocked.append(\'FORBIDDEN_IO\');raise AssertionError(\'NO_PRODUCTION_IO\')\ndef observe(*a,**k):\n result=original_plan(*a,**k);planned.append(result);return result\ndef fake_ctor(*a,**k):\n constructors.append(\'TESTONLY_PROVIDER_DOUBLE\')\n if mode==\'constructor-failure\':raise RuntimeError(\'TESTONLY_SECRET_NEVER_LOG\')\ndef tick(self,queries,fetch):\n dispatched.append(queries)\n if mode in {\'key-query\',\'key-no-query\',\'constructor-failure\'}:\n  return {\'status\':\'NO_CHANGE\',\'provider_calls\':0,\'queries\':[],\'astra_calls\':0,\'TESTONLY_monitor_double\':True}\n empty_tick_runs.append(len(queries))\n return original_tick(self,queries,fetch)\nstream=io.StringIO();code=0\nkey=\'TESTONLY_NOT_A_REAL_KEY\' if mode in {\'key-query\',\'key-no-query\',\'constructor-failure\'} else (\'  \' if mode==\'blank\' else (\'\\t\\r\\n\' if mode==\'whitespace\' else \'\'))\nwith patch.dict(os.environ,{\'THE_STATS_API_KEY\':key,\'XAI_API_KEY\':\'\'}),patch.object(sqlite3,\'connect\',deny),patch.object(Database,\'connect\',deny),patch.object(TemporalStore,\'capture\',deny),patch.object(socket,\'create_connection\',deny),patch.object(socket.socket,\'connect\',deny),patch.object(TheStatsPrimaryClient,\'__init__\',fake_ctor if mode in {\'key-query\',\'key-no-query\',\'constructor-failure\'} else deny),patch.object(plans,\'fetch_monitor_query\',deny),patch.object(plans,\'build_monitor_queries\',observe),patch.object(om,\'saved_monitor_input\',return_value=saved),patch.object(om.OddsMonitor,\'tick\',tick),patch.object(shadow,\'tick\',return_value={\'status\':\'TESTONLY_OPTIONAL_SHADOW_STUB\',\'llm_calls\':0}):\n if mode==\'absent\':os.environ.pop(\'THE_STATS_API_KEY\',None)\n try:\n  with contextlib.redirect_stdout(stream):runpy.run_path(str(root/\'scripts/run-arthur-odds-monitor.py\'),run_name=\'__main__\')\n except SystemExit as error:code=error.code if type(error.code) is int else 0 if error.code is None else 1\nassert not blocked\nresult=json.loads(stream.getvalue());assert \'TESTONLY_SECRET_NEVER_LOG\' not in stream.getvalue() and \'TESTONLY_NOT_A_REAL_KEY\' not in stream.getvalue()\nprint(json.dumps({\'TESTONLY\':True,\'mode\':mode,\'actual_native_exit\':code,\'result\':result,\'actual_pure_planned_queries\':sum(len(x) for x in planned),\'queries_dispatched_to_monitor\':sum(len(x) for x in dispatched),\'provider_constructor_doubles\':len(constructors),\'production_io_attempts\':blocked,\'fixture_clock_basis\':\'synthetic kickoff derived from actual UTC clock; no production fixture or clock modified\',\'actual_empty_query_tick_used\':bool(empty_tick_runs) and all(n==0 for n in empty_tick_runs)}))\nsys.exit(code)\n'
@pytest.mark.parametrize('mode,plan,dispatch,ctors,status,code,reason',[
 ('absent',1,0,0,'MONITOR_UNAVAILABLE',1,'THE_STATS_CREDENTIAL_UNAVAILABLE'),
 ('empty',1,0,0,'MONITOR_UNAVAILABLE',1,'THE_STATS_CREDENTIAL_UNAVAILABLE'),
 ('blank',1,0,0,'MONITOR_UNAVAILABLE',1,'THE_STATS_CREDENTIAL_UNAVAILABLE'),
 ('whitespace',1,0,0,'MONITOR_UNAVAILABLE',1,'THE_STATS_CREDENTIAL_UNAVAILABLE'),
 ('no-fixtures',0,0,0,'NO_UNLOCKED_FIXTURES',0,None),
 ('unqueryable',0,0,0,'NO_CHANGE',0,None),('invalid',0,0,0,'NO_CHANGE',0,None),
 ('locked',0,0,0,'NO_CHANGE',0,None),('duplicate',0,0,0,'NO_CHANGE',0,None),
 ('retired',0,0,0,'NO_CHANGE',0,None),('key-query',1,1,1,'NO_CHANGE',0,None),
 ('key-no-query',0,0,0,'NO_CHANGE',0,None),
 ('constructor-failure',1,0,1,'MONITOR_UNAVAILABLE',1,None)])
def test_candidate_cli(tmp_path,mode,plan,dispatch,ctors,status,code,reason):
 assert not tmp_path.resolve().is_relative_to(ROOT.resolve()), 'TESTONLY fixtures outside production checkout'
 root=tmp_path/'r';(root/'scripts').mkdir(parents=True);(root/'config').mkdir()
 (root/'TESTONLY.json').write_text(json.dumps({'production_io_forbidden':True}))
 shutil.copy2(ROOT/'scripts/run-arthur-odds-monitor.py',root/'scripts/run-arthur-odds-monitor.py')
 shutil.copy2(ROOT/'config/daily223-live.json',root/'config/daily223-live.json')
 bootstrap=root/'guarded_bootstrap.py';bootstrap.write_text(CLI_BOOTSTRAP,encoding='utf-8')
 p=subprocess.run([sys.executable,'-B',str(bootstrap),str(root),mode,str(ROOT)],cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30,check=False)
 assert p.returncode==code,p.stderr
 v=json.loads(p.stdout);assert v['actual_native_exit']==code and v['result']['status']==status
 assert v['actual_pure_planned_queries']==plan and v['queries_dispatched_to_monitor']==dispatch
 assert v['provider_constructor_doubles']==ctors and not v['production_io_attempts']
 assert v['actual_empty_query_tick_used']==(mode in {'unqueryable','invalid','locked','duplicate','retired'})
 assert v['result'].get('reason')==reason
 assert v['result']['provider_calls']==(None if mode=='constructor-failure' else 0)
 assert v['result']['prospective_shadow']['status']=='TESTONLY_OPTIONAL_SHADOW_STUB'
