"use strict";

// Run separately after source review: node tests/ten_agent_club_frontend_check.cjs
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
const context = {
  document:{getElementById:byId, createElement:tag=>new Node(tag), createElementNS:(_,tag)=>new Node(tag), createDocumentFragment:()=>new Node("#document-fragment"), createTextNode:value=>{const node=new Node("#text");node.textContent=value;return node;}, querySelectorAll:selector=>all.filter(node=>node.className.split(/\s+/).includes(selector.slice(1)))},
  window:{location:{hash:""}, scrollTo(){}, ARTHUR_TOKEN:"injected-test-token"}, history:{replaceState(){}},
  URL, Intl, Date, console, setTimeout:()=>1, clearTimeout(){},
  fetch:async () => { requests.push("FORBIDDEN_API"); throw new Error("TESTONLY display must not invoke any API"); }
};
vm.createContext(context);
const source = read("src/goal_analysis/web/app.js");
const sentinel = "  state.today = todayBerlin();";
assert(source.includes(sentinel), "App initializer test seam changed");
vm.runInContext(source.replace(sentinel, "  globalThis.T={state,renderTipsters,clubRecommendations,clubCardsByProfile,ticketCard,agentDecisionNotes}; return;\n" + sentinel).replace("  async function refreshStatus() {", "  async function refreshStatus() { return; "),context);
const T = context.T;
let checks = 0;
const ok = (condition, message) => { assert(condition, message); checks++; };
const ownerIds = ["kronikas", "ritmusor", "parharcmester", "orszem", "merlin",
  "tipster_archiv_nora", "tipster_forma_soma", "tipster_ertek_milan",
  "tipster_szines_reka", "tipster_fek_tade"];
const day = "2026-11-12";
const catalogue = ownerIds.map((profile_id, index) => ({profile_id,
  name:`TESTONLY Owner ${index}`, personality:`Own method ${index}`,
  preferences:["All daily fixtures", "Shared models"],
  target_odds_band:{min:15,max:40}, max_variants:3}));
const cards = ownerIds.flatMap((profile_id, owner) => [0,1].map(variant => ({
  profile_id, ticket_id:`TESTONLY-${owner}-${variant}`, agent_option_id:`own-${owner}-${variant}`,
  date:day, status:"DRAFT", publication_scope:"RESEARCH", playable:false,
  agent_decision:{option_reason:`TESTONLY own option reason ${owner}-${variant}`,
    reason:`TESTONLY final own reasoning ${owner}`,
    tool_receipts:[{role:"candidate_scenarios",status:"AVAILABLE",
      interpretation:"TESTONLY actual option scenario interpretation"},
      {role:"historical_strategy_evaluation",status:"UNKNOWN",
        interpretation:"TESTONLY historical prices and cost remain unknown"}]},
  combined_price:16, outcome:"PENDING", legs:[{fixture_id:`TESTONLY-match-${owner}-${variant}`,
    home_team:`Home-${owner}-${variant}`, away_team:`Away-${owner}-${variant}`,
    kickoff:"2026-11-12T21:00:00+00:00", market_key:"totals_2_5", selection_key:"over",
    decimal_price:2, model_probability:null}],
})));
const base = () => ({selected_date:day, settings:{enabled_profiles:[...ownerIds,"daily223","gauss"]},
  runtime:{running:false}, team:{members:[]}, tipster_club:{
    decision_mode:"TEN_INDEPENDENT_OWNERS", catalogue:structuredClone(catalogue),
    recommendations:structuredClone(cards), members:[], max_daily:30, overlap:[],
    owner_states:ownerIds.map(profile_id => ({profile_id,status:"COMPLETE",ticket_count:2})),
  }});
const reset = () => {
  T.state.data=base(); T.state.date=day; T.state.today=day;
  T.state.tipsterChanges.clear(); T.state.tipsterBusy=false; T.state.busy=false;
  byId("club-search").value=""; byId("club-filter").value="all";
};
reset();
const before=JSON.stringify(T.state.data);
T.renderTipsters();
ok(byId("club-members").children.length===10,"Ten active decision owners are visible");
ok(nodesWith(byId("club-members"),"club-ticket").length===20,"Every own saved variant renders");
ok(byId("club-members").children.every(node=>nodesWith(node,"club-ticket").length===2),"Two cards per owner survive grouping");
ok(cards.every(card=>nodesWith(byId("club-members"),"club-ticket").some(node=>node.dataset.ticketId===card.ticket_id)),"Every exact ticket identity survives display");
ok(cards.every(card=>text("club-members").includes(card.legs[0].home_team)),"All original fixture choices remain visible");
ok(cards.every(card=>text("club-members").includes(card.agent_decision.option_reason)),"Each own option reason is visible in its club card");
ok(text("club-members").includes("TESTONLY final own reasoning"),"Final own reasoning is visible");
ok(text("club-members").includes("Történeti stratégiai próba: ISMERETLEN"),"Unknown historical result is explicitly visible");
ok(text("club-members").includes("TESTONLY historical prices and cost remain unknown"),"The saved tool interpretation remains visible");
ok(text("club-hero-facts").includes("20"),"Hero counts cards rather than owners");
ok(byId("club-engines").children.length===3,"Three common models are not extra decision owners");
ok(nodesWith(byId("club-members"),"club-ticket-badges").every(node=>node.children[0].className.includes("warning")),"Saved research DRAFT is not shown as READY");
ok(JSON.stringify(T.state.data)===before,"Rendering does not rewrite any data or human setting");
ok(nodesWith(byId("club-members"),"club-enable").every(node=>node.disabled),"Fixed ten-owner membership switches are disabled");
ok(byId("club-save").disabled,"Fixed ten-owner roster cannot save a partial registry");
ok(text("club-members").includes("Szent Grál"),"Individual pause uses the existing team controls");
ok(T.clubCardsByProfile(cards).get(ownerIds[0]).length===2,"Pure grouping retains sibling variants");
const ordinaryCard=T.ticketCard(cards[0]);
ok(ordinaryCard.textContent.includes(cards[0].agent_decision.option_reason),"The ordinary ticket card shows the same own option reason");
ok(ordinaryCard.textContent.includes("Történeti stratégiai próba: ISMERETLEN"),"The ordinary card preserves simulator uncertainty");
ok(T.agentDecisionNotes({status:"DRAFT",reasoning:["legacy"]})===null,"Legacy cards gain no invented owner explanation");
byId("club-filter").value="ticket"; T.renderTipsters();
ok(byId("club-members").children.length===10,"Ticket filter counts all owners with variants");
reset();
T.state.data.tipster_club.recommendations=[];
T.state.data.tipster_club.owner_states=[
  {profile_id:ownerIds[0],status:"ABSTAINED",reason:"TESTONLY honest own abstention"},
  {profile_id:ownerIds[1],status:"PARTIAL",reason:"TESTONLY interrupted host"},
  {profile_id:ownerIds[2],status:"NO_COMPLIANT_TICKET",reason:"TESTONLY canonical rejection"},
];
T.renderTipsters();
const memberNodes=byId("club-members").children;
ok(memberNodes[0].textContent.includes("A kihagyás a döntés"),"Explicit abstention remains distinct");
ok(memberNodes[0].textContent.includes("TESTONLY honest own abstention"),"Saved abstention reason is retained");
ok(memberNodes[1].textContent.includes("Befejezetlen döntés"),"Interrupted decision is not an abstention");
ok(memberNodes[1].textContent.includes("TESTONLY interrupted host"),"Incomplete reason is retained");
ok(memberNodes[2].textContent.includes("nincs mentett, érvényes saját szelvény"),"Completed but rejected output is not a successful card");
ok(memberNodes[3].textContent.includes("Még nincs mentett szelvény"),"Missing outcome remains no saved card");
ok(!memberNodes[3].textContent.includes("A kihagyás a döntés"),"Missing outcome never becomes an invented abstention");
byId("club-filter").value="abstained"; T.renderTipsters();
ok(byId("club-members").children.length===1,"Abstention filter excludes incomplete and absent owners");
reset(); T.state.date="2026-11-13"; T.renderTipsters();
ok(nodesWith(byId("club-members"),"club-ticket").every(node=>!node.dataset.ticketId),"Different selected date does not show old cards");
ok(!text("club-members").includes("A kihagyás a döntés"),"Different day does not reuse saved owner outcomes");
reset();
const legacy=JSON.parse(read("src/goal_analysis/tipsters/profiles.json")).profiles;
T.state.data.tipster_club={catalogue:legacy,recommendations:[],members:[],max_daily:20,overlap:[]};
T.renderTipsters();
ok(byId("club-members").children.length===20,"Legacy reports keep the twenty-person catalogue");
ok(byId("club-engines").children.length===10,"Legacy display keeps its original separate profile group");
ok(nodesWith(byId("club-members"),"club-enable").every(node=>!node.disabled),"Legacy participation controls retain their existing edit path");
ok(requests.length===0,"Read-only display never calls an API");
console.log(`${checks} ten-agent club DOM assertions passed; TESTONLY fixture, no real browser`);
