// Execute only the actual four catalogue functions in a TESTONLY DOM/API double.
// No browser, fetch, sockets, server, source mutation or credential operation.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.resolve(here, '../src/goal_analysis/web/app.js'), 'utf8');
const names = ['selectedCompetitions', 'isDefaultCompetition', 'renderCompetitions', 'loadCompetitions'];
const functions = names.map(name => {
  const lines = source.split('\n').filter(line => new RegExp(`^\\s*(async )?function ${name}\\(`).test(line));
  if (lines.length !== 1) throw new Error(`Unique original function required: ${name}`);
  return lines[0];
}).join('\n');
const cases = [];
for (const fixture of [
  { name: 'configured-null', status: 'CONFIGURED_FALLBACK', competitions: [{id:39,name:'TESTONLY',country:'',configured:true,default_selected:true,type:'CLUB',fixture_count:null,fixtures:[]}] },
  { name: 'stale-mixed', status: 'STALE_SNAPSHOT', competitions: [{id:39,name:'TESTONLY',country:'',configured:true,default_selected:true,type:'CLUB',fixture_count:2,fixtures:[]},{id:32,name:'UNMAPPED',country:'',configured:true,default_selected:false,type:'NATIONAL',fixture_count:null,fixtures:[]}] },
  { name: 'known-empty', status: 'CACHED_SNAPSHOT', competitions: [{id:39,name:'TESTONLY',country:'',configured:true,default_selected:true,type:'CLUB',fixture_count:0,fixtures:[]}] },
]) {
  const text = {}, small = [], requests = [];
  const nodes = new Map();
  const element = () => ({value:'', addEventListener(){}});
  const sandbox = {
    state: {date:'2026-10-27',today:'2026-10-27',competitionSelections:new Map(),competitionRequest:0,competitions:[]},
    $: id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    setText: (id,value) => { text[id]=value; },
    el: (kind,_classes,value) => { if (kind==='small') small.push(value); return element(); },
    append: node => node, replace(){}, arr: value => Array.isArray(value)?value:[],
    api: async uri => { requests.push(uri); return structuredClone(fixture); },
  };
  vm.createContext(sandbox);
  vm.runInContext(functions, sandbox, {timeout:1000});
  await vm.runInContext('loadCompetitions(true)', sandbox, {timeout:1000});
  if (requests.length !== 1 || !requests[0].endsWith('&refresh=1')) throw new Error('Local reread call changed');
  if (!text['competition-feedback'].includes('explicit elemzési adatgyűjtés')) throw new Error('Refresh meaning omitted');
  if (fixture.name==='configured-null' && (!small[0].includes('meccsszám ismeretlen') || !text['competition-feedback'].includes('1 sorozat meccsszáma ismeretlen'))) throw new Error('NULL became known zero');
  if (fixture.name==='stale-mixed' && (!text['competition-feedback'].includes('2 mentett mérkőzés') || !text['competition-feedback'].includes('A mentett lista elavult.'))) throw new Error('STALE provenance lost');
  if (fixture.name==='known-empty' && (!small[0].includes('0 meccs (mentett állapot)') || !text['competition-feedback'].includes('0 sorozat meccsszáma ismeretlen'))) throw new Error('Known zero became unknown');
  cases.push({name:fixture.name,feedback:text['competition-feedback'],details:small,actual_catalogue_functions_executed:names,requests_are_API_doubles_not_HTTP:requests.length});
}
const result = {status:'THREE_PURE_ACTUAL_JS_CATALOGUE_FUNCTION_CONTROLS_PASS_NOT_BROWSER_OR_HTTP',
  candidate_js_sha256:crypto.createHash('sha256').update(source).digest('hex'),cases,
  actual_browser_HTTP_provider_DB_credential_operations:0};
process.stdout.write(JSON.stringify({status:result.status,cases:cases.length})+'\n');
