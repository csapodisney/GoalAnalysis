"use strict";

// Run: node tests/tipster_frontend_check.cjs
// No dependencies, browser, network, API/model calls, or persistent data writes.
// A minimal injected DOM tests contracts and guards, not actual browser layout.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const all = [], ids = new Map();
class Node {
  constructor(tag = "div") {
    Object.assign(this, {tagName:tag, children:[], _text:"", className:"", style:{}, value:"", hidden:false, disabled:false, checked:false, dataset:{}, attrs:{}, listeners:{}});
    this.classList = {add(){}, remove(){}, toggle(){}};
    all.push(this);
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent || "").join(" "); }
  append(...nodes) { for (const node of nodes) { if (node.tagName === "#document-fragment") { this.children.push(...node.children); node.children = []; } else this.children.push(node); } }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  setAttribute(key,value) { this.attrs[key] = String(value); if (key === "class") this.className = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener(name,fn) { this.listeners[name] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  select() {}
  closest() { return this.parent || (this.parent = new Node()); }
  querySelector() { return new Node(); }
  set innerHTML(value) { throw new Error("Unsafe HTML attempted"); }
}
for (const match of read("src/goal_analysis/web/index.html").matchAll(/\bid="([^"]+)"/g)) {
  assert(!ids.has(match[1]), `Duplicate HTML ID: ${match[1]}`);
  const node = new Node(); node.id = match[1]; ids.set(match[1],node);
}
const byId = id => { assert(ids.has(id), `Missing HTML ID: ${id}`); return ids.get(id); };
const descendants = node => [node, ...node.children.flatMap(descendants)];
const nodesWith = (node, cls) => descendants(node).filter(item => item.className.split(/\s+/).includes(cls));
const text = id => byId(id).textContent;
const requests = [];
let freshStatus, paperResponse, deferredPaperResponse, failRequest = false;
const context = {
  document:{getElementById:byId, createElement:tag=>new Node(tag), createElementNS:(_,tag)=>new Node(tag), createDocumentFragment:()=>new Node("#document-fragment"), createTextNode:value=>{const node=new Node("#text");node.textContent=value;return node;}, querySelectorAll:selector=>all.filter(node=>node.className.split(/\s+/).includes(selector.slice(1)))},
  window:{location:{hash:""}, scrollTo(){}, ARTHUR_TOKEN:"injected-test-token"}, history:{replaceState(){}},
  URL, Intl, Date, console, setTimeout:()=>1, clearTimeout(){},
  fetch:async (url,options) => {
    requests.push({url,options});
    if (failRequest) throw new Error("DO_NOT_DISPLAY_THIS_SECRET");
    if (url.startsWith("/api/paper-performance")) return {ok:true,json:async()=>deferredPaperResponse ? deferredPaperResponse : structuredClone(paperResponse)};
    return {ok:true,json:async()=>url.startsWith("/api/status") ? structuredClone(freshStatus) : {status:"SAVED"}};
  }
};
vm.createContext(context);
const source = read("src/goal_analysis/web/app.js");
const sentinel = "  state.today = todayBerlin();";
assert(source.includes(sentinel), "App initializer test seam changed");
vm.runInContext(source.replace(sentinel, "  globalThis.T={state,isDefaultCompetition,renderTipsters,clubAvatar,clubRecommendations,clubCatalogue,saveClubSelection,syncClubControls,renderTeam,renderModelWorkbench,reportTickets,recordWager,profileName,selectTab,saveSettings,paperPrice,renderPaperPerformance,renderPaperLedger,renderPaperSummary,loadPaperPerformance,modelStatus,modelNote,renderOddsMonitor}; return;\n" + sentinel).replace("  async function refreshStatus() {", "  async function refreshStatus() { return; "),context);
const T = context.T;
let checks = 0;
const ok = (condition,message) => { assert(condition,message); checks++; };
ok(T.isDefaultCompetition({id:89,configured:true,default_selected:true}), "New configured league is selected by default");
ok(T.isDefaultCompetition({id:138,configured:false,default_selected:true,fallback:true}), "Sparse-day Serie C fallback is selected");
ok(!T.isDefaultCompetition({id:138,configured:false,default_selected:false}), "Busy-day Serie C remains a manual option");
ok(!T.isDefaultCompetition({configured:true,default_selected:false}), "Explicit server default takes precedence");
ok(T.isDefaultCompetition({configured:true}), "Older server configured fallback survives");
ok(T.isDefaultCompetition({type:"NATIONAL"}), "Older server national fallback survives");

const existing = ["daily223","kronikas","ritmusor","parharcmester","orszem","merlin","h1_over05","gauss","kepler","laplace"];
const checkedInCatalogue = JSON.parse(read("src/goal_analysis/tipsters/profiles.json")).profiles;
assert(Array.isArray(checkedInCatalogue) && checkedInCatalogue.length === 20, "Checked-in JSON catalogue must contain twenty profiles");
assert(new Set(checkedInCatalogue.map(person=>person.profile_id)).size === 20, "Catalogue IDs must be unique");
assert(checkedInCatalogue.every(person=>person.profile_id.startsWith("tipster_") && typeof person.name === "string" && person.name.length), "Catalogue identities must be explicit");
// This compatibility fixture intentionally retains the historical 7–18 band.
// Exercise current policy separately below; never infer live policy from this fixture.
const catalogue = checkedInCatalogue.map((person,index)=>({profile_id:person.profile_id,name:person.name,tagline:`Nézőpont ${index}`,personality:"Fiktív személyiség, nem valódi szakértő.",preferences:[index % 2 ? "Gólpiac" : "Forma", "Közös bizonyíték"],target_odds_band:{min:7,max:18},min_legs:2,max_legs:4,risk:"LOWER",fictional:true,method_kind:"SHARED_EVIDENCE_PERSONA"}));
const [first,second,third] = catalogue;
const metrics = {tickets:0,total_tickets:0,days:0,unique_fixtures:0,roi_pct:null,actual:{financial:{available:false,roi_pct:null,net_eur:null},tickets:{decided:0}},research:{tickets:{total:0,decided:0}},frozen:{tickets:{total:0}}};
const member = (id,status="PROBATION")=>({profile_id:id,status,metrics:structuredClone(metrics),qualification:{eligible:false,blockers:["NO_PROSPECTIVE_DATA"]},awards:[],podium:null});
const day = "2026-10-11";
const ticket = {profile_id:first.profile_id,date:day,publication_scope:"PAPER_AUDITION",method_kind:"SHARED_EVIDENCE_PERSONA",status:"READY",combined_price:7.8,probability:null,outcome:"WON",recommendation_id:"original-rec",run_id:"original-run",legs:[{fixture_id:17,home_team:"Hazai klub",away_team:"Vendég klub",market_key:"1x2",selection_key:"home",decimal_price:2.2,kickoff:"2026-10-11T21:00:00+02:00",model_probability:null,model_details:{selection_score_terms:{heuristic:99}}}],settlement:{legs:[{fixture_id:17,outcome:"WON",home_goals:2,away_goals:1}]}};
const base = ()=>({selected_date:day,settings:{enabled_profiles:[...existing,...catalogue.map(person=>person.profile_id)]},runtime:{running:false},tipster_club:{catalogue:structuredClone(catalogue),recommendations:[structuredClone(ticket),{profile_id:second.profile_id,date:day,status:"ABSTAINED",publication_scope:"PAPER_AUDITION",legs:[],reason_code:"NO_VALID_QUOTES",abstention_reason:"Hiányzó ellenőrizhető szorzók."}],members:catalogue.map(person=>member(person.profile_id)),max_daily:20,existing_profile_count:10,overlap:[{profile_a:first.profile_id,profile_b:third.profile_id,shared_fixtures:1}]},team:{mode:"LIVE_PROFILE_SUPERVISION",members:[...existing.map(id=>member(id,"ACTIVE")),...catalogue.map(person=>member(person.profile_id))],events:[],policy:{min_tickets:260,min_days:90,min_unique_fixtures:300}},learning:{metrics:{research:{tickets:{total:87,decided:70}}}}});
const reset = ()=>{T.state.data=base();T.state.date=day;T.state.today=day;T.state.tipsterChanges.clear();T.state.tipsterBusy=false;T.state.busy=false;byId("club-search").value="";byId("club-filter").value="all";freshStatus=structuredClone(T.state.data);requests.length=0;failRequest=false;};

(async()=>{
  T.state.data={}; T.renderTipsters();
  ok(byId("club-content").hidden,"Old backend has honest missing-feature state");
  ok(text("club-availability").includes("újraindítása"),"Old backend restart guidance");
  ok(byId("club-save").disabled,"Missing backend cannot write settings");
  reset(); T.renderTipsters();
  ok(catalogue.length===20,"Actual checked-in catalogue contains twenty fictional people");
  ok(byId("club-members").children.length===20,"All twenty characters have cards without needing tips");
  ok(byId("club-engines").children.length===10,"Ten original profiles remain a separate group");
  T.state.data.tipster_club.catalogue = structuredClone(checkedInCatalogue);
  T.renderTipsters();
  ok(byId("club-members").children.length===20,"Current immutable JSON profiles all render");
  ok(checkedInCatalogue.every(person=>person.target_odds_band.min>=10 && person.target_odds_band.max<=50),"Current live catalogue retains its fixed 10–50 envelope");
  const currentFirst = checkedInCatalogue[0];
  ok(byId("club-members").children[0].textContent.includes(`${currentFirst.target_odds_band.min}–${currentFirst.target_odds_band.max}`),"Current JSON odds band reaches the actual card");
  reset(); T.renderTipsters();
  T.state.data.tipster_club.catalogue[0] = {...T.state.data.tipster_club.catalogue[0], target_odds_minimum_enforced:false, target_odds_band:{min:null,max:18}, min_legs:1};
  T.renderTipsters();
  ok(byId("club-members").children[0].textContent.includes("Nincs minimum · legfeljebb 18"),"Evidence-first catalogue has no hidden minimum odds");
  ok(byId("club-members").children[0].textContent.includes("1–4 meccs"),"Evidence-first catalogue permits a single qualified selection");
  ok(byId("club-members").children[1].textContent.includes("7–18"),"Historical catalogue odds remain readable");
  ok(nodesWith(byId("club-members"),"club-avatar").length===20,"Every character has a code-native portrait");
  const avatarSignature = node=>JSON.stringify([node.tagName,node.attrs,node.children.map(avatarSignature)]);
  ok(new Set(catalogue.map(person=>avatarSignature(T.clubAvatar(person.profile_id)))).size===20,"Twenty distinct local portrait designs");
  ok(!all.some(node=>["img","script","iframe"].includes(node.tagName)),"No external image, script or frame elements");
  ok(nodesWith(byId("club-members"),"club-enable").every(node=>node.type==="checkbox"&&node.attrs["aria-label"]),"Every participation switch is a labelled native checkbox");
  ok(text("club-members").includes("Papírpróba · nincs valódi tét"),"Original paper publication scope visible");
  ok(text("club-members").includes("Hiányzó ellenőrizhető szorzók."),"Explicit abstention, not an invented ticket");
  ok(text("club-members").includes("2–1")&&text("club-members").includes("Nyert"),"Saved settlement and actual final score remain visible");
  ok(!text("club-members").includes("99%"),"Selection-score heuristics are never probabilities");
  ok(text("club-members").includes("nincs számszerű valószínűségi"),"Missing model probability is not zero percent");
  ok(!descendants(byId("club-members")).some(node=>node.tagName==="button"),"Persona cards never contain betting or promotion buttons");
  const actualStats=nodesWith(byId("club-members").children[0],"club-stat-grid")[0];
  ok(actualStats.children[2].children[1].textContent==="—"&&actualStats.children[3].children[1].textContent==="—","Unknown actual ROI and net are not fabricated zeroes");
  ok(text("club-members").includes("Még nincs teljesített minősítési minta"),"Small/unqualified sample caveat visible");
  ok(!byId("club-overlap-panel").hidden&&text("club-overlap").includes("1 közös meccs"),"Shared fixture exposure displayed");
  ok(requests.length===0,"Rendering does not start any network request");
  const toggle=nodesWith(byId("club-members").children[0],"club-enable")[0];toggle.checked=false;toggle.listeners.change();
  ok(T.state.tipsterChanges.get(first.profile_id)===false&&!byId("club-save").disabled,"Explicit switch edit awaits batch save");
  ok(requests.length===0,"Toggling alone neither writes nor runs models");
  T.renderTipsters();ok(!nodesWith(byId("club-members").children[0],"club-enable")[0].checked,"Draft survives polling render");
  T.state.data.tipster_club.catalogue[0].name="<img src=x onerror=alert(1)>";
  T.state.data.tipster_club.recommendations[1].abstention_reason="<script>unsafe</script>";T.renderTipsters();
  ok(text("club-members").includes("<script>unsafe</script>")&&!all.some(node=>node.tagName==="script"),"Untrusted catalogue and abstention text remain inert text");
  reset(); byId("club-search").value=second.name;T.renderTipsters();ok(byId("club-members").children.length===1&&text("club-members").includes(second.name),"Search by real catalogue name");
  byId("club-search").value="";byId("club-filter").value="ticket";T.renderTipsters();ok(text("club-result-count").startsWith("1 /"),"Day-ticket filter");
  byId("club-filter").value="abstained";T.renderTipsters();ok(text("club-result-count").startsWith("1 /"),"Explicit abstention filter excludes not-yet-run members");
  byId("club-filter").value="all";T.state.date="2026-10-10";T.renderTipsters();
  ok(T.clubRecommendations().length===0&&!text("club-members").includes("Hazai klub"),"Day change immediately hides stale tickets");
  ok(byId("club-overlap-panel").hidden,"Previous-day overlaps are not shown on a new date");
  T.state.data.selected_date=T.state.date;T.state.data.tipster_club.recommendations=[];T.renderTipsters();
  ok(text("club-members").includes("Nem gyártunk visszamenőleges tippet"),"No backfilled historical predictions");
  reset();T.state.data.recommendations=[{profile_id:"gauss",publication_scope:"LIVE",legs:[]},{...ticket,publication_scope:"LIVE"},{...ticket,profile_id:second.profile_id,publication_scope:"PAPER_AUDITION"}];
  ok(T.reportTickets().length===1&&T.reportTickets()[0].profile_id==="gauss","Club LIVE and probation recommendations do not enter ordinary daily money desk");
  T.recordWager({...ticket,ticket_id:"malformed-persona-ticket",publication_scope:"LIVE"});ok(!byId("wager-dialog").open,"Defensive actual-wager guard also rejects LIVE personas");
  ok(T.profileName({profile_id:first.profile_id})===first.name,"Dynamic names available in team/history");
  ok(T.profileName({profile_id:first.profile_id,profile_name:"Original saved name"})==="Original saved name","Original archived display name is never overwritten");
  byId("team-cohort").value="research";T.renderTeam();
  ok(byId("team-members").children.length===30,"Szent Grál supports all thirty existing/new members");
  ok(text("team-members").includes("Fiktív tipster · közös adat, saját stílus"),"Team does not call personas independent models");
  ok(text("team-history-context").includes("87")&&text("team-history-context").includes("70"),"Original historical research counts preserved");
  T.state.data.settings.enabled_profiles=T.state.data.settings.enabled_profiles.filter(id=>id!==third.profile_id);T.renderModelWorkbench();
  ok(byId("configured-profiles").children.length===30,"Disabled new personas remain in full profile registry");
  T.selectTab("tipsters");ok(T.state.tab==="tipsters"&&!byId("selected-date").closest().hidden,"Club navigation keeps archive date picker visible");
  reset();T.state.tipsterChanges.set(first.profile_id,false);T.state.tipsterChanges.set(second.profile_id,true);
  freshStatus.settings.enabled_profiles=freshStatus.settings.enabled_profiles.filter(id=>id!==second.profile_id);freshStatus.settings.enabled_profiles.push("unrelated_future_profile");
  await T.saveClubSelection();
  ok(requests.length===2&&requests[0].url.startsWith("/api/status")&&requests[1].url==="/api/settings","Save rebases on fresh settings and sends one batch");
  const sent=JSON.parse(requests[1].options.body);
  ok(Object.keys(sent).length===1&&Array.isArray(sent.enabled_profiles),"Save changes only profile enablement");
  ok(existing.every(id=>sent.enabled_profiles.includes(id))&&sent.enabled_profiles.includes("unrelated_future_profile"),"All ten old profiles and unknown future profile IDs preserved");
  ok(!sent.enabled_profiles.includes(first.profile_id)&&sent.enabled_profiles.includes(second.profile_id),"Only explicit enablement edits applied");
  ok(sent.enabled_profiles.length===30&&new Set(sent.enabled_profiles).size===30,"No accidental duplicates or subset replacement");
  ok(requests[1].options.headers["X-Arthur-Token"]==="injected-test-token","Existing same-origin authenticated API helper used");
  ok(T.state.tipsterChanges.size===0&&text("club-save-feedback").includes("elmentve"),"Confirmed save clears only the saved draft");
  reset();T.state.tipsterChanges.set(first.profile_id,false);freshStatus.runtime.running=true;await T.saveClubSelection();
  ok(requests.length===1&&T.state.tipsterChanges.size===1,"A newly started run blocks mutation and preserves draft");
  reset();T.state.tipsterChanges.set(first.profile_id,false);freshStatus.tipster_club.catalogue=[];await T.saveClubSelection();
  ok(requests.length===1&&T.state.tipsterChanges.size===1,"Changed registry prevents stale ID mutation");
  reset();T.state.tipsterChanges.set(first.profile_id,false);failRequest=true;await T.saveClubSelection();
  ok(T.state.tipsterChanges.size===1&&!text("club-save-feedback").includes("DO_NOT_DISPLAY"),"Network failure preserves draft without exposing error/secrets");
  reset();delete T.state.data.settings.enabled_profiles;T.state.tipsterChanges.set(first.profile_id,false);await T.saveClubSelection();
  ok(requests.length===0,"Missing full enabled-profile list cannot be replaced by a guessed subset");
  reset();byId("strictness").value="35";byId("ticket-count").value="2";byId("openai-model").value="test-existing-model";byId("web-search").checked=true;
  await T.saveSettings({preventDefault(){}});
  ok(requests.length===1&&!Object.hasOwn(JSON.parse(requests[0].options.body),"enabled_profiles"),"Old settings form never overwrites the expanded enablement list");
  const css=read("src/goal_analysis/web/style.css");
  ok(css.includes(".club-toggle input:focus-visible+.club-toggle-track"),"Custom switch retains visible keyboard focus");
  ok(css.includes(".club-member-grid{grid-template-columns:1fr"),"Narrow-layout single-column rule exists (browser QA is separate)");
  reset();byId("paper-period").value="month";byId("paper-month").value="2026-10";byId("paper-grouping").value="day";
  T.renderPaperPerformance();ok(byId("paper-content").hidden,"Paper report not fabricated before fetch");
  const paperSummary={records:3,eligible_records:2,excluded_records:1,settled_records:1,pending_records:1,review_records:0,total_stake_eur:2,total_returned_eur:4.2,realized_net_eur:3.2,open_stake_eur:1,cash_balance_eur:2.2,all_record_assumed_stake_eur:3,excluded_assumed_stake_eur:1,complete_result_available:false,full_all_record_realized_net_eur:null};
  const paperRow={...structuredClone(ticket),paper_eligible:true,original_combined_price:4.2,original_combined_price_decimal:"4.2000",settled:true,stake_eur:1,returned_eur:4.2,realized_net_eur:3.2,open_stake_eur:0,cash_balance_eur:3.2,exclusion_reasons:[],legs:[{...ticket.legs[0],price_verified:true,measured_outcome:"WON"}]};
  paperResponse={mode:"HYPOTHETICAL_EUR1_ORIGINAL_QUOTES",unit_stake_eur:1,month:"2026-10",summary:paperSummary,overall_summary:paperSummary,by_day:[{date:day,...paperSummary,cumulative_cash_balance_eur:2.2}],by_month:[{month:"2026-10",...paperSummary}],records:[paperRow,{...structuredClone(paperRow),profile_id:"gauss",recommendation_id:"pending",outcome:"PENDING",settled:false,returned_eur:0,realized_net_eur:null,open_stake_eur:1,cash_balance_eur:-1},{...structuredClone(paperRow),profile_id:"merlin",recommendation_id:"excluded",paper_eligible:false,settled:false,original_combined_price:null,original_combined_price_decimal:null,stake_eur:null,returned_eur:null,realized_net_eur:null,open_stake_eur:null,cash_balance_eur:null,exclusion_reasons:["INCOMPATIBLE_BOOKMAKERS"],exclusion_messages:["Nem azonos irodától származó eredeti árak."]}],notes:["Teszt: eredeti árak, valódi fogadás nélkül."]};
  await T.loadPaperPerformance();
  ok(requests.length===1&&requests[0].url==="/api/paper-performance?month=2026-10"&&!requests[0].options.method,"Paper endpoint is read-only and month-scoped");
  ok(!byId("paper-content").hidden&&byId("paper-kpis").children.length===5,"Five clearly separated accounting KPIs");
  ok(byId("paper-exposure").children[0].children[1].textContent.includes("3"),"All three recommendations receive hypothetical exposure, including excluded record");
  ok(byId("paper-exposure").children[1].children[1].textContent==="—"&&byId("paper-exposure").children[2].children[1].textContent==="—","Partial coverage never claims complete team net or balance");
  ok(text("paper-coverage").includes("2 / 3")&&text("paper-coverage").includes("A teljes eredmény nem ismert"),"Exact original-price coverage and incomplete total explanation");
  ok(byId("paper-ledger").children.length===3,"Paper ledger includes old engines and new personas, also excluded records");
  const excluded=byId("paper-ledger").children.find(node=>node.textContent.includes("Nem azonos irodától"));
  ok(!!excluded&&nodesWith(excluded,"paper-record-facts")[0].children[1].children[1].textContent==="—","Excluded financial result is unknown, not zero");
  ok(nodesWith(excluded,"paper-record-facts")[0].children[0].children[1].textContent==="1 €","Excluded ticket still shows its assumed one-euro stake");
  const pending=byId("paper-ledger").children.find(node=>node.textContent.includes("Mentett ajánlat: pending"));
  ok(nodesWith(pending,"paper-record-facts")[0].children[2].children[1].textContent==="—"&&pending.textContent.includes("nem realizált veszteség"),"Pending stake is not a realized loss");
  ok(text("paper-ledger").includes("4,2000"),"Original decimal price representation retained");
  ok(text("paper-ledger").includes("Hazai klub")&&text("paper-ledger").includes("Hazai győzelem")&&text("paper-ledger").includes("Eredeti szorzó: 2,2"),"All recommendation legs, original individual odds and selections are expandable");
  ok(T.paperPrice(null,null)==="—"&&T.paperPrice(2.2,"<script>unsafe</script>")==="2,2","Missing and hostile price representations are safe");
  ok(T.paperPrice(2.2,"2.123456789012345678901234567890")==="2,123456789012345678901234567890","Exact saved combined-price decimal is not rounded away");
  ok(descendants(byId("paper-summary")).filter(node=>node.tagName==="th").length===8,"Daily table includes cumulative period balance");
  byId("paper-grouping").value="month";T.renderPaperSummary();
  ok(descendants(byId("paper-summary")).filter(node=>node.tagName==="th").length===7,"Monthly table uses backend monthly aggregate, without invented cumulative field");
  ok(!descendants(byId("paper-ledger")).some(node=>node.tagName==="button"),"Paper ledger contains no mutation or wager buttons");
  T.state.paperPerformance.records[0].profile_name="<script>paper-xss</script>";T.state.paperPerformance.records[2].exclusion_messages=["<img src=x onerror=bad>"];T.renderPaperPerformance();
  ok(text("paper-ledger").includes("<script>paper-xss</script>")&&!all.some(node=>node.tagName==="script"||node.tagName==="img"),"Report names and exclusion messages are escaped as text");
  T.state.paperPerformance.records=Array.from({length:215},(_,index)=>({...paperRow,recommendation_id:`row-${index}`}));T.state.paperPage=0;T.renderPaperLedger();
  ok(byId("paper-ledger").children.length===20&&text("paper-ledger-page").includes("1–20 / 215"),"Ledger rendering is capped to twenty records per page");
  ok(/<details id="paper-ledger-details"[^>]*>/.test(read("src/goal_analysis/web/index.html"))&&!/<details id="paper-ledger-details"[^>]*\bopen\b/.test(read("src/goal_analysis/web/index.html")),"Full ticket ledger starts collapsed using a native keyboard-accessible disclosure");
  ok(text("paper-ledger-count").includes("215")&&text("paper-ledger-count").includes("20 / oldal"),"Collapsed ledger heading retains total count and page size");
  byId("paper-ledger-details").open=true;T.state.paperPage=1;T.renderPaperLedger();
  ok(byId("paper-ledger-details").open&&text("paper-ledger-page").includes("21–40 / 215"),"Opening the ledger is preserved across page changes");
  T.state.paperPage=10;T.renderPaperLedger();ok(byId("paper-ledger").children.length===15&&byId("paper-ledger-next").disabled,"Final ledger page contains remaining rows only");
  T.state.paperPerformance.by_day=Array.from({length:70},(_,index)=>({date:`2026-${String(index).padStart(2,"0")}`, ...paperSummary}));byId("paper-grouping").value="day";T.renderPaperSummary();
  ok(descendants(byId("paper-summary")).filter(node=>node.tagName==="tbody")[0].children.length===31,"Aggregate rows also have bounded DOM pagination");
  requests.length=0;byId("paper-period").value="all";paperResponse.month=null;await T.loadPaperPerformance();
  ok(requests[0].url==="/api/paper-performance"&&byId("paper-month-label").hidden,"All-time mode omits month filter");
  ok(T.state.paperPage===0&&T.state.paperSummaryPage===0,"Changing period resets both pagination windows");
  paperResponse.summary={...paperSummary,excluded_records:0,eligible_records:3,complete_result_available:true,full_all_record_realized_net_eur:3.2};await T.loadPaperPerformance();
  ok(byId("paper-exposure").children[1].children[1].textContent!=="—","Complete verified coverage can show full realized result");
  byId("paper-period").value="month";byId("paper-month").value="2026-13";requests.length=0;await T.loadPaperPerformance();
  ok(requests.length===0&&byId("paper-content").hidden,"Invalid month cannot issue a malformed request or show stale results");
  byId("paper-month").value="2026-10";paperResponse.month="2026-10";failRequest=true;await T.loadPaperPerformance();
  ok(byId("paper-content").hidden&&!text("paper-feedback").includes("DO_NOT_DISPLAY_THIS_SECRET")&&text("paper-feedback").includes("újra"),"Offline/old backend shows safe explanation, not raw errors or fake money");
  failRequest=false;paperResponse.mode="ACTUAL";await T.loadPaperPerformance();ok(byId("paper-content").hidden,"Actual financial report cannot accidentally populate hypothetical view");
  paperResponse.mode="HYPOTHETICAL_EUR1_ORIGINAL_QUOTES";let releaseOld;
  deferredPaperResponse=new Promise(resolve=>{releaseOld=resolve;});const oldRequest=T.loadPaperPerformance();await new Promise(resolve=>setImmediate(resolve));
  ok(T.state.paperLoading&&byId("paper-content").hidden,"Loading hides old totals under newly selected period");
  deferredPaperResponse=null;byId("paper-period").value="all";paperResponse.month=null;await T.loadPaperPerformance();
  releaseOld({...paperResponse,month:"2026-10"});await oldRequest;
  ok(T.state.paperPerformance.month===null&&!T.state.paperLoading,"Stale monthly response cannot overwrite a newer all-time response");
  reset();
  T.state.data.tipster_club.recommendations[0] = {...T.state.data.tipster_club.recommendations[0], status:"ABSTAINED", legs:[], combined_price:null, reason_code:"NO_DISTINCT_FIXTURE_WITHIN_QUALITY_RETENTION", abstention_reason:"Ma nincs kellően alátámasztott, más ajánlattól eltérő választás."};
  T.renderTipsters();
  ok(byId("club-members").children[0].textContent.includes("más ajánlattól eltérő"),"Overlap abstention explains why a former tip is withheld");
  ok(nodesWith(byId("club-members").children[0],"club-picks").length===0,"Abstention cannot display an old duplicated pick or its result");
  const withheldModel={profile_id:"gauss",status:"ABSTAINED",model_status:"READY",legs:[],abstention_reason:"Az átfedés miatt nem adunk ki külön ajánlatot."};
  ok(T.modelStatus(withheldModel)==="ABSTAINED","A completed model fit cannot disguise a withheld publication as a ready tip");
  ok(T.modelNote(withheldModel)===withheldModel.abstention_reason,"Model cards explain overlap abstention using the saved publication decision");
  reset();
  const exceptionalLeg = T.state.data.tipster_club.recommendations[0].legs[0];
  exceptionalLeg.contrarian_analysis = {baseline_price:2,current_price:2.4,relative_change:.2,model_probability:.6,expected_return:1.44,explanation:{price_case:"Ellenőrzött ármozgás; oka nem ismert.",math_case:"0,60 × 2,40 = 1,44.",cause_status:"UNKNOWN",cause_source_urls:[]}};
  exceptionalLeg.veto_override = {gates:["HIGH_ODDS"],justification:"Forrásolt, egyedi kivétel <script>unsafe</script>",source_urls:["javascript:alert(1)","https://example.org/source"]};
  T.renderTipsters();
  ok(text("club-members").includes("Miért megy szembe az árral?")&&text("club-members").includes("Mi a matematikai alátámasztás?"),"Contrarian tips show both mandatory structured Astra sections");
  ok(text("club-members").includes("lakossági fogadási irányt nem következtetünk"),"Unknown movement cause stays explicit");
  ok(text("club-members").includes("Forrásolt kivétel")&&text("club-members").includes("egyedi kivétel <script>unsafe</script>"),"Reviewed veto reason renders as safe literal text");
  const exceptionNodes=nodesWith(byId("club-members"),"exception-analysis");
  ok(exceptionNodes.length===1&&!descendants(exceptionNodes[0]).some(node=>node.href?.startsWith("javascript:")),"Exceptional source links reject unsafe URLs");
  T.state.data.runtime.odds_monitor={status:"AVAILABLE",queries:[{status:"QUOTA_RESERVED"}],providers:{}};
  T.renderOddsMonitor();
  ok(text("connections").includes("Külön árfigyelő · 30 perc")&&text("connections").includes("Keret vagy szolgáltatói korlát"),"Monitor quota waiting is visible without an Astra call");
  ok(text("connections").includes("15 perccel lezár; Astrát nem hív"),"Independent monitoring and publication buffer are clear");
  console.log(`${checks} tipster and paper-accounting frontend assertions passed; no network/browser/data writes.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
