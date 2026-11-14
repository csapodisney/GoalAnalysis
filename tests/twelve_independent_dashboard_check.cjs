"use strict";
// Injected DOM only. No browser, network, job, provider, model or mutations.
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, ".."), ids = new Map(), all = [];
class Element {
  constructor(tag = "div") {
    Object.assign(this, {tagName: tag, children: [], _text: "", className: "", value: "", hidden: false,
      disabled: false, dataset: {}, attrs: {}, style: {}, listeners: {}, checked: false});
    this.classList = {add() {}, remove() {}, toggle() {}}; all.push(this);
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(value => value.textContent || "").join(" "); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = [...nodes]; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener(key, value) { this.listeners[key] = value; }
  set innerHTML(_) { throw new Error("UNSAFE_HTML"); }
}
const html = fs.readFileSync(path.join(root, "src/goal_analysis/web/index.html"), "utf8");
for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
  assert(!ids.has(match[1]), "Duplicate HTML ID"); ids.set(match[1], new Element());
}
const byId = id => { assert(ids.has(id), `Missing HTML ID ${id}`); return ids.get(id); };
let network = 0;
const context = {Node: Element, document: {getElementById: byId, createElement: tag => new Element(tag),
  createElementNS: (_, tag) => new Element(tag), querySelectorAll: () => []},
  window: {ARTHUR_TOKEN: "TESTONLY", location: {hash: ""}}, location: {hash: ""},
  Date, Intl, URL, console, setTimeout: () => 1, clearTimeout() {},
  fetch() { network++; throw new Error("NETWORK_FORBIDDEN"); }};
vm.createContext(context);
const source = fs.readFileSync(path.join(root, "src/goal_analysis/web/app.js"), "utf8");
const seam = '  $("selected-date").value = state.date; $("performance-month").value = state.date.slice(0, 7); readPending();';
assert.equal(source.split(seam).length, 2, "Exact pre-initializer seam required");
vm.runInContext(source.replace(seam,
  "  globalThis.T={state,AGENTS,PAIR_AGENTS,renderCards,renderWorkshop,renderAgents,hydrateCard,canWager,ledgerHistoryAvailable}; return;\n" + seam), context);
const T = context.T, ownerIds = [...T.AGENTS, ...T.PAIR_AGENTS];
const day = "2026-11-14", run = `${day}-TESTONLY-pair`;
const card = id => ({ticket_id: `ticket-${id}`, profile_id: id, date: day, run_id: run,
  status: "DRAFT", publication_scope: "RESEARCH", playable: false, combined_price: 16,
  legs: [{fixture_id: `fixture-${id}`, home_team: `Home ${id}`, away_team: "Away",
    kickoff: "2099-11-14T18:00:00+00:00", market_key: "totals", selection_key: "over", decimal_price: 16}],
  agent_decision: {option_reason: `Own ${id}`, reason: `Final ${id}`, tool_receipts: [
    {role: "candidate_scenarios", status: "UNKNOWN", interpretation: "Missing scenario remains UNKNOWN"},
    {role: "historical_strategy_evaluation", status: "AVAILABLE", interpretation: "Own historical interpretation"}]}});
function reset() {
  T.state.connected = true; T.state.date = day; T.state.cardsRun = run; T.state.cardsNext = null;
  T.state.cards = ownerIds.map(id => ({ticket: card(id), state: {played: false}})); T.state.cardsTotal = 12;
  T.state.workshop = {schema_version: 1, run: {run_id: run, construction_status: "COMPLETE", verification_state: "PARTIAL"},
    capabilities: {financial_recording: true, ledger_ticket_history: true, team_status: true},
    agents: ownerIds.map(agent_id => ({agent_id, name: agent_id, max_variants: T.PAIR_AGENTS.includes(agent_id) ? 1 : 3,
      personality: `Own ${agent_id}`, decision_status: "DECISION_COMPLETED", selection_path: ["All daily fixtures", "Own evidence"],
      proposed_count: 1, admitted_count: 1, published_count: 1})),
    capacity: {version: "TWELVE_INDEPENDENT_AGENT_SLOTS_V2", main_slot_count: 12, maximum_agent_variants: 32,
      maximum_companion_tickets: 0, generated_ticket_count: 12, agent_variant_count: 12,
      filled_main_slots: 12, filled_agent_owner_slots: 12, filled_core_owner_slots: 10, filled_pair_owner_slots: 2,
      filled_companion_slots: 0, filled_agent_owner_ids: ownerIds, invalid_card_ids: [], identity_issues: [],
      target_met: true, completed_owner_count: 12, abstained_owner_count: 0, failed_owner_count: 0}};
  for (const key of ["card-search", "card-agent", "card-status", "card-odds"]) byId(key).value = "";
  byId("card-sort").value = "original";
}
function test_twelve_independent_display_and_historical_owners_stay_separate() {
  reset(); T.renderWorkshop(); T.renderCards(); T.renderAgents();
  assert.equal(byId("cards").children.length, 12); assert.equal(byId("agents").children.length, 12);
  assert.equal(byId("companion-section").hidden, true);
  const buttons = value => [value, ...value.children.flatMap(buttons)].filter(item => item.tagName === "button");
  assert(byId("agents").children.slice(0, 10).every(item => buttons(item).some(button => !button.disabled)));
  assert(byId("agents").children.slice(10).every(item => buttons(item).every(button => button.disabled)));
  assert.match(byId("cards").children[10].textContent, /11\. daily_portfolio/);
  assert.match(byId("cards").children[11].textContent, /12\. legacy_daily223/);
  for (const id of ownerIds) assert(byId("cards").textContent.includes(`Own ${id}`));
  assert.match(byId("card-filter-note").textContent, /^12 agentváltozat/);
  assert.match(byId("minimum-note").textContent, /Mind a 12/);
  byId("card-agent").value = "daily_portfolio"; T.renderCards();
  assert.equal(byId("cards").children.length, 1);
  assert.match(byId("cards").textContent, /11\. daily_portfolio/);
  assert.match(byId("card-filter-note").textContent, /^1 agentváltozat/);
  T.state.workshop.capacity.filled_agent_owner_ids = [...T.AGENTS, "daily_portfolio", "daily_portfolio"];
  T.renderWorkshop(); assert.match(byId("minimum-note").textContent, /nem igazolt/);
  reset(); T.state.workshop.capacity = {version: "TWELVE_MAIN_SLOTS_V1"};
  T.state.workshop.agents = T.state.workshop.agents.slice(0, 10);
  T.state.workshop.companion_channels = T.PAIR_AGENTS.map((id, index) => ({role: index ? "LEGACY" : "DAILY",
    run_id: run, status: "DRAFT", ticket_ids: [`ticket-${id}`]}));
  T.state.cards.slice(10).forEach(item => {
    const oldId = item.ticket.profile_id;
    Object.assign(item.ticket, {profile_id: "daily223", producer_profile_id: "daily223", channel_id: oldId,
      profile_name: `Historical ${oldId}`, companion_decision: {channel_id: oldId, run_id: run}});
    delete item.ticket.agent_decision;
  });
  T.renderCards(); T.renderAgents();
  assert.equal(byId("cards").children.length, 10); assert.equal(byId("agents").children.length, 10);
  assert.equal(byId("companion-section").hidden, false);
  assert.equal(byId("companion-cards").children.length, 2);
  assert(byId("companion-cards").textContent.includes("Historical daily_portfolio"));
}
function test_lossless_state_hydration_preserves_financial_and_unknown_values() {
  reset(); const ticket = {...card("daily_portfolio"), played: true, active: false};
  const raw = {ticket, state: {ticket_id: ticket.ticket_id, financial: {net: null}, outcome: "VOID"},
    state_projection: "CANONICAL_TICKET_PLUS_STATE_DELTA_V2", decision_projection: "CANONICAL_TICKET_V2"};
  const before = JSON.stringify(raw), actual = T.hydrateCard(raw);
  assert.equal(actual.state.played, true); assert.equal(actual.state.active, false);
  assert.equal(actual.state.financial.net, null); assert.equal(actual.state.outcome, "VOID");
  assert.equal(JSON.stringify(raw), before); assert.equal(T.canWager(actual), false);
  const plain = {ticket, state: null, state_projection: "STATE_FULL_V1"};
  assert.equal(T.hydrateCard(plain), plain); assert.equal(T.hydrateCard(plain).state, null);
  assert.throws(() => T.hydrateCard({...raw, state: null}), /vetület/);
  delete T.state.workshop.capabilities.ledger_ticket_history;
  assert.equal(T.ledgerHistoryAvailable(), true);
  T.state.cards[10] = actual; T.renderCards();
  assert(byId("cards").textContent.includes("Megjátszva"));
  assert(byId("cards").textContent.includes("Missing scenario remains UNKNOWN"));
}
test_twelve_independent_display_and_historical_owners_stay_separate();
test_lossless_state_hydration_preserves_financial_and_unknown_values();
assert.equal(network, 0);
console.log(JSON.stringify({status: "PASS", test_cases: 2, network_attempts: network,
  actual_browser: false, boundary: "INJECTED_DOM_ONLY"}));
