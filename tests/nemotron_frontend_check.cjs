"use strict";

// Read-only frontend contract checks. No browser, provider, database or file writes.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
process.chdir(path.resolve(__dirname, ".."));
const nodes = [], ids = new Map();
class Node {
  constructor(tagName = "div") {
    Object.assign(this, {tagName, children:[], _text:"", className:"", style:{}, value:"", hidden:false, disabled:false, checked:false, dataset:{}, attrs:{}});
    this.classList = {add(){}, remove(){}, toggle(){}};
    nodes.push(this);
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent || "").join(" "); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ""; this.children = items; }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener() {}
  set innerHTML(value) { throw new Error("Unsafe HTML assignment: " + value); }
}
const html = fs.readFileSync("src/goal_analysis/web/index.html", "utf8");
for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
  assert(!ids.has(match[1]), "duplicate HTML id: " + match[1]);
  ids.set(match[1], new Node());
}
const document = {
  getElementById(id) { assert(ids.has(id), "Missing HTML id: " + id); return ids.get(id); },
  createElement(tagName) { return new Node(tagName); },
};
const requests = [];
let response = {ok:true, json:async () => ({configured:false})};
const context = {document, window:{}, URL, Intl, Date, console, setTimeout:() => 1, clearTimeout(){}, fetch:async (url, options) => { requests.push({url, options}); return typeof response === "function" ? response() : response; }};
vm.createContext(context);
const source = fs.readFileSync("src/goal_analysis/web/app.js", "utf8");
vm.runInContext(source.replace("  state.today = todayBerlin();", "  globalThis.T={state,renderNemotron,loadNemotron,nemotronReport}; return;\n  state.today = todayBerlin();"), context);
const T = context.T;
const text = id => ids.get(id).textContent;
let checks = 0;
function ok(value, message) { assert(value, message); checks++; }
function has(id, fragment, message) { ok(text(id).includes(fragment), message || fragment); }
const report = () => ({
  status:"COMPLETE", scope:"RUN", subject_id:"run-2026-10-12", created_at:"2026-10-12T17:40:00Z", evidence_as_of:"2026-10-12T17:00:00Z",
  model:"gpt-6-astra", policy_version:"codex-advisory-v2", automatic_changes:false,
  tasks:[{name:"Kockázatok", status:"COMPLETE", proposals:[{title:"Közös meccsek ellenőrzése", detail:"Két profil ugyanarra a meccsre támaszkodik.", priority:"HIGH", evidence_ids:["run:123/profile_a", "run:123/profile_b"]}]}],
  usage:{requests:1, known_input_tokens:100, known_output_tokens:40, usage_complete:true, unknown_usage_tasks:[]},
});
const payload = () => ({configured:true, enabled:true, model:"gpt-6-astra", policy_version:"codex-advisory-v2", report:report(), learning:null});

(async () => {
  T.state.nemotron = null; T.renderNemotron();
  has("nemotron-status", "Még nincs lekért állapot");
  has("nemotron-connection", "még nem ellenőrizhető");
  ok(ids.get("nemotron-reports").children.length === 0, "no synthetic report");
  T.state.nemotron = {configured:true, model:payload().model, report:null, learning:null}; T.renderNemotron();
  has("nemotron-connection", "Helyi hozzáférés beállítva");
  has("nemotron-connection", "Sikeres modellválasz még nincs igazolva");
  has("nemotron-status", "Még nincs futásértékelés");
  has("nemotron-reports", "nem módosítja");
  T.state.nemotron = payload(); T.renderNemotron();
  has("nemotron-enabled", "bekapcsolva");
  T.state.nemotron.enabled = false; T.renderNemotron();
  has("nemotron-enabled", "kikapcsolva");
  has("nemotron-enabled", "Codex-bejelentkezés megléte önmagában nem kapcsolja be");
  T.state.nemotron.enabled = true;
  has("nemotron-status", "Értékelés elkészült");
  has("nemotron-model", "gpt-6-astra");
  has("nemotron-connection", "nem folyamatos elérhetőségi garancia");
  has("nemotron-reports", "Adatok időhatára:");
  has("nemotron-reports", "Javaslat · nincs alkalmazva");
  has("nemotron-reports", "run:123/profile_a");
  has("nemotron-reports", "Prioritás: magas");
  has("nemotron-reports", "Ismert bemeneti token: 100");
  has("nemotron-reports", "Pénzbeli költség ebből nem állapítható meg");
  has("club-nemotron-status", "Értékelés elkészült");
  const partial = payload(); partial.report.status = "PARTIAL"; partial.report.usage = {requests:2, known_input_tokens:100, known_output_tokens:40, usage_complete:false, unknown_usage_tasks:["failure"]};
  partial.report.tasks.push({name:"Hiányzó válasz", status:"UNAVAILABLE", proposals:[{title:"DO_NOT_DISPLAY_FAILED_PROPOSAL", evidence_ids:["x"]}]});
  T.state.nemotron = partial; T.renderNemotron();
  has("nemotron-status", "Részleges értékelés");
  has("nemotron-reports", "hiányzó rész nem nulla");
  has("nemotron-reports", "Ismeretlen használatú részfeladat: 1");
  ok(!text("nemotron-reports").includes("DO_NOT_DISPLAY_FAILED_PROPOSAL"), "failed task proposal hidden");
  has("nemotron-reports", "Közös meccsek ellenőrzése", "successful sibling retained");
  partial.report.tasks[1].status = "INVALID_RESPONSE"; partial.report.tasks[1].name = "data_quality"; T.renderNemotron();
  has("nemotron-reports", "Adatminőség és szorzólefedettség");
  has("nemotron-reports", "A válasz ellenőrzése sikertelen");
  const attack = payload(); attack.report.tasks[0].proposals[0].title = '<img src=x onerror="bad()">'; attack.report.tasks[0].proposals[0].detail = "<script>bad()</script>"; attack.report.tasks[0].name = "<svg onload=bad()>";
  attack.report.model = "<img>"; attack.report.message = "<script>message()</script>"; attack.report.tasks[0].proposals.push({title:"UNSOURCED", detail:"not evidence", evidence_ids:[]});
  T.state.nemotron = attack; T.renderNemotron();
  has("nemotron-reports", '<img src=x onerror="bad()">', "malicious title plain text");
  ok(!nodes.some(node => ["img", "script", "svg"].includes(node.tagName)), "no executable model HTML");
  ok(!text("nemotron-reports").includes("UNSOURCED"), "unsourced conclusion not presented");
  attack.report.tasks = [null, "invalid", {name:"Malformed", status:"COMPLETE", proposals:[null, "invalid"]}]; attack.report.usage = {requests:null, known_input_tokens:null, known_output_tokens:null}; attack.report.automatic_changes = undefined;
  T.state.nemotron = attack; T.renderNemotron();
  has("nemotron-reports", "Ismert bemeneti token: —");
  has("nemotron-reports", "nem igazolja a kizárólag tanácsadó működést");
  for (const [status, label] of [["UNAVAILABLE","Értékelés nem elérhető"],["SKIPPED_NO_DATA","Nincs elég adat"],["INTERRUPTED","Megszakadt"]]) {
    T.state.nemotron = {configured:false, report:{...report(), status, tasks:[], usage:{}}, learning:null}; T.renderNemotron();
    has("nemotron-status", label); has("nemotron-reports", "Nem pótoljuk kitalált értékeléssel");
  }
  const learning = payload(); learning.report = null; learning.learning = report(); T.state.nemotron = learning; T.renderNemotron();
  has("nemotron-reports", "Legfrissebb történeti tanulási értékelés");
  has("nemotron-status", "Még nincs futásértékelés", "learning not falsely a run report");
  T.state.data = {latest:{run_id:"a&b"}, application_version:"3.12"}; T.state.date = "2026-10-12";
  response = {ok:false, status:404}; await T.loadNemotron(true);
  has("nemotron-feedback", "újraindítás szükséges");
  has("nemotron-status", "Nem elérhető");
  ok(requests.at(-1).url === "/api/codex-advisory?run_id=a%26b", "exact escaped run id");
  const after404 = requests.length; await T.loadNemotron();
  ok(requests.length === after404, "404 not repeatedly polled");
  response = {ok:true, json:async () => payload()}; await T.loadNemotron(true);
  has("nemotron-status", "Értékelés elkészült", "forced refresh recovers old endpoint");
  ok(!T.state.nemotronError && !T.state.nemotronMissing, "old error cleared");
  const existingReport = ids.get("nemotron-reports").children[0]; await T.loadNemotron(true);
  ok(ids.get("nemotron-reports").children[0] === existingReport, "unchanged payload preserves expanded details");
  ok(requests.every(item => !item.options.method && !item.options.body), "only read-only GETs, never model POST");
  ok(requests.every(item => item.options.credentials === "same-origin" && item.options.cache === "no-store"), "same-origin uncached requests");
  const afterSuccess = requests.length; await T.loadNemotron();
  ok(requests.length === afterSuccess, "normal polling throttled");
  response = {ok:true, json:async () => ({raw_error:"SECRET_MUST_NOT_BE_DISPLAYED"})}; await T.loadNemotron(true);
  has("nemotron-feedback", "nem olvasható");
  ok(!text("nemotron-feedback").includes("SECRET_MUST_NOT_BE_DISPLAYED"), "raw malformed response hidden");
  response = {ok:false, status:500}; await T.loadNemotron(true);
  ok(!T.state.nemotronMissing, "transient failure not treated as missing deployment");
  ok(!ids.get("nemotron-refresh").disabled, "refresh button released after failure");
  let finish;
  response = () => new Promise(resolve => { finish = resolve; });
  const pending = T.loadNemotron(true); T.state.data.latest.run_id = "new-run";
  finish({ok:true, json:async () => payload()}); await pending;
  ok(T.state.nemotron === null, "stale previous run response ignored");
  ok(html.includes("naponta 1 tanulási értékelés") && html.includes("2 párhuzamos"), "bounded workload disclosed");
  ok(html.includes("nem indít modellhívást") && html.includes("nem köt fogadást"), "read-only and non-execution disclosed");
  ok(html.includes("utólagos ellenőrzése") && html.includes("nem új szorzólekérés"), "historical audit not fresh odds or prepublication approval");
  console.log(`${checks} Codex advisory frontend contract and safety assertions passed; no network/browser/data writes.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
