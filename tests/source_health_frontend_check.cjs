"use strict";

// Run: node tests/source_health_frontend_check.cjs
// Render-only contract: no browser, dependencies, requests, or persistent writes.
// The tiny DOM catches unsafe HTML and tests meaning; CSS is inspected, not laid out.
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const nodes = [], ids = new Map(), requests = [];
class Node {
  constructor(tagName = "div") {
    Object.assign(this, {tagName, children: [], _text: "", className: "", attrs: {},
      hidden: false, open: false, style: {}, dataset: {}});
    this.classList = {add() {}, remove() {}, toggle() {}};
    nodes.push(this);
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child?.textContent ?? String(child ?? "")).join(" "); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ""; this.children = items; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  set innerHTML(value) { throw new Error("Unsafe HTML attempted: " + value); }
}
const html = read("src/goal_analysis/web/index.html");
const css = read("src/goal_analysis/web/style.css");
const source = read("src/goal_analysis/web/app.js");
for (const match of html.matchAll(/<([\w:-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
  assert(!ids.has(match[3]), "Duplicate HTML ID: " + match[3]);
  const node = new Node(match[1].toLowerCase());
  node.id = match[3];
  node.className = /\bclass="([^"]*)"/.exec(match[2])?.[1] || "";
  node.open = /\sopen(?:\s|=|$)/.test(match[2]);
  ids.set(node.id, node);
}
const byId = id => { assert(ids.has(id), "Missing HTML ID: " + id); return ids.get(id); };
const text = id => byId(id).textContent;
const descendants = node => [node, ...node.children.filter(child => child instanceof Node).flatMap(descendants)];
const leaves = node => descendants(node).filter(item => item.children.length === 0).map(item => item.textContent.trim());
const context = {
  document: {getElementById: byId, createElement: tag => new Node(tag)},
  window: {}, URL, Intl, Date, console,
  fetch(...args) { requests.push(args); throw new Error("Render must not issue a request"); },
};
vm.createContext(context);
const sentinel = "  state.today = todayBerlin();";
assert(source.includes(sentinel), "App initializer test seam changed");
vm.runInContext(source.replace(sentinel,
  "  globalThis.T={state,renderSourceHealth,renderExpertAdvisory}; return;\n" + sentinel), context);
const T = context.T;
let checks = 0;
const ok = (condition, message) => { assert(condition, message); checks++; };
const matches = (value, pattern, message) => ok(pattern.test(value), message);
const has = (id, value) => ok(text(id).includes(value), id + " contains " + value);
const allText = () => ["source-health-status", "source-health-context", "source-health-note",
  "source-health-rows", "source-health-conflicts", "source-health-leagues-summary", "source-health-leagues"].map(text).join(" ");

for (const id of ["source-health-panel", "source-health-status", "source-health-context", "source-health-note",
  "source-health-rows", "source-health-conflicts", "source-health-leagues-details", "source-health-leagues-summary", "source-health-leagues"]) byId(id);
ok(byId("source-health-leagues-details").tagName === "details", "League inventory uses a native disclosure");
ok(byId("source-health-leagues-summary").tagName === "summary", "League disclosure has a native keyboard-operable summary");
ok(!byId("source-health-leagues-details").open, "Full league inventory starts compact and collapsed");
ok(html.indexOf('id="source-health-panel"') > html.indexOf('id="team-experts"') &&
  html.indexOf('id="source-health-panel"') < html.indexOf('id="expert-network"'), "Source health is included in the shared expert panel");
matches(css, /\.source-health-scroll\s*\{[^}]*overflow(?:-x)?\s*:\s*(?:auto|scroll)/, "Wide source tables have contained horizontal scrolling");
matches(css, /\.source-health[^{]*\{[^}]*min-width\s*:\s*0/, "Source panel can shrink inside the page layout");

T.state.data = {};
T.renderSourceHealth();
matches(text("source-health-status"), /nincs|hiány|vár|nem.*érkez/i, "Missing backend projection has an explicit missing state");
ok(!/\b0\b/.test(text("source-health-rows")), "Missing report cannot fabricate measured zero counters");
matches(allText(), /SHADOW|árnyék/i, "Missing state retains the shadow-only interpretation");
ok(!byId("source-health-panel").hidden, "Missing data remains visible and explained");

const metrics = {expected_fields: 7, available_fields: 4, selected_fields: 3,
  missing_fields: 3, fallback_fields: 0, rejected_observations: 2};
const flags = {has_team_stats: true, has_player_stats: false, xg_available: null,
  odds_available: true, live_odds_available: null};
const leagueRows = Array.from({length: 28}, (_, index) => {
  const existing = index < 10, proposal = index >= 10 && index < 17;
  const name = existing ? "Existing league " + index : proposal ? "Proposal league " + index : "Missing league " + index;
  return {api_football_id: 1000 + index, competition_id: name, scope: index > 24 ? "CONDITIONAL" : "DEFAULT",
    mapping_status: existing || proposal ? "MAPPED" : "MISSING_LINK",
    mapping_method: existing ? "EXISTING_VERIFIED_MAPPING" : proposal ? "EXACT_NAME_COUNTRY_PROPOSAL" : null,
    collector_enabled: existing,
    the_stats: existing || proposal ? {id: "comp_" + index, name, country: "Test country", advertised: {...flags}} : null,
    observed_in_run: index === 17 ? {fixtures: null, matched: null} : {fixtures: existing ? 3 : 0, matched: existing ? 2 : 0},
    available_fact_categories: existing ? {team_statistics: 1} : {}};
});
const projection = {
  mode: "SHADOW", status: "AVAILABLE", run_id: "run-source-health-current", date: "2026-10-20",
  as_of: "2026-10-19T20:15:00+00:00", origin: "RECORDED_RUN", effective_data_path: "LEGACY_UNCHANGED",
  rows: [
    {domain: "sports", provider: "the_stats_api", role: "PRIMARY", ...metrics},
    {domain: "sports", provider: "api_football", role: "FALLBACK", expected_fields: 7, available_fields: 2,
      selected_fields: 1, missing_fields: 5, fallback_fields: 1, rejected_observations: 0},
    {domain: "weather", provider: "unknown-counters", role: "PRIMARY", ...Object.fromEntries(Object.keys(metrics).map(key => [key, null]))},
    {domain: "news", provider: "measured-zero", role: "PRIMARY", ...Object.fromEntries(Object.keys(metrics).map(key => [key, 0]))},
  ],
  conflict_fields: 2, unresolved_conflict_fields: 1,
  league_coverage: {as_of: "2026-10-19T20:00:00+00:00", status: "PARTIAL", active_competitions: 28,
    counts: {MAPPED: 17, MISSING_LINK: 11}, leagues: leagueRows},
};
T.state.data = {latest: {date: projection.date, run_id: projection.run_id, recommendations: [
  {profile_id: "DAILY_223", legs: [{fixture_id: "unchanged-fixture", decimal_price: 2.2}]}]}, source_health: projection};
const before = JSON.stringify(T.state.data);
T.renderSourceHealth();
ok(JSON.stringify(T.state.data) === before, "Rendering preserves source evidence and DAILY_223 recommendations");
ok(requests.length === 0, "Rendering performs no fetch, POST, provider, or model request");
has("source-health-context", projection.run_id);
has("source-health-context", projection.date);
matches(text("source-health-context"), /20[:.]15|22[:.]15/, "Projection cutoff remains visible with its time");
matches(text("source-health-context"), /mentett|rögzített/i, "Recorded-run provenance is identified");
matches(text("source-health-note"), /SHADOW|árnyék/i, "Panel explicitly declares shadow mode");
has("source-health-note", "Az eredeti jelöltadatok és a kiegészítő mezők ellenőrzése");
has("source-health-note", "a szelvények végső árellenőrzése külön fut");
matches(text("source-health-note"), /változatlan|nem.*módos|nem.*használ|nem.*hat|csak.*(?:ellenőrz|megfigyel)/i,
  "Panel explains that the shadow audit does not change effective model inputs");
ok(!/(?:modell|tipster|szelvény).{0,35}(?:már használja|már használják|éles forrása)/i.test(allText()), "Shadow evidence cannot claim model adoption");
ok(byId("source-health-rows").children.length === projection.rows.length, "Every provider row is visible");
matches(text("source-health-rows"), /elsődleges|főforrás/i, "Primary source role is readable in Hungarian");
matches(text("source-health-rows"), /tartalék|kiegészítő/i, "Fallback source role is readable in Hungarian");
const providerRow = provider => byId("source-health-rows").children.find(node => node.textContent.includes(provider));
const nullRow = providerRow("unknown-counters"), zeroRow = providerRow("measured-zero");
ok(Boolean(nullRow && zeroRow), "Diagnostic provider names are rendered as text");
ok(leaves(nullRow).filter(value => value === "—").length === 6, "Each unknown metric is an em dash");
ok(!leaves(nullRow).some(value => value === "0"), "Null metrics never become zero");
ok(leaves(zeroRow).filter(value => value === "0").length === 6, "Measured zero remains zero in all six metric columns");
ok(!leaves(zeroRow).some(value => value === "—"), "Measured zero is not reported as unknown");
matches(zeroRow.textContent, /nem volt vizsgált mező/i, "Zero expected fields explicitly means no field was examined");
matches(zeroRow.textContent, /nem teljes lefedettség/i, "Zero expected fields cannot imply complete coverage");
ok(byId("source-health-rows").children.some(node => JSON.stringify(leaves(node).filter(value => /^\d+$/.test(value))) === JSON.stringify(["7", "4", "3", "3", "0", "2"])),
  "Expected, available, selected, missing, fallback, and rejected counts retain their individual values");
matches(text("source-health-conflicts"), /eltérés|ellentmond|ütköz/i, "Conflict summary uses an explicit conflict label");
ok(JSON.stringify(leaves(byId("source-health-conflicts")).filter(value => /^\d+$/.test(value))) === JSON.stringify(["2", "1"]),
  "Total conflict fields and unresolved conflicts keep separate counts");

const renderedLeagues = () => descendants(byId("source-health-leagues")).filter(node => node.tagName === "tr" && node.textContent.includes("league "));
ok(renderedLeagues().length === 28, "All 28 configured and conditional leagues render without truncation");
for (const row of leagueRows) has("source-health-leagues", row.competition_id);
has("source-health-leagues-summary", "28");
matches(text("source-health-leagues"), /meglévő gyűjtési út\s*:?\s*10/i, "Ten existing collector routes remain distinct from catalogue matches");
matches(text("source-health-leagues"), /(?:új )?párosítási javaslat\s*:?\s*7/i, "Seven new exact matches are proposals");
matches(text("source-health-leagues"), /hiányzó vagy többértelmű kapcsolat\s*:?\s*11/i, "Eleven unresolved links remain explicit");
const existingLeague = renderedLeagues().find(row => row.textContent.includes("Existing league 0"));
const proposalLeague = renderedLeagues().find(row => row.textContent.includes("Proposal league 10"));
const missingLeague = renderedLeagues().find(row => row.textContent.includes("Missing league 17"));
matches(existingLeague.textContent, /igazolt|meglévő/i, "Existing mapped league keeps verified routing context");
matches(proposalLeague.textContent, /javaslat/i, "New mapping is never promoted to activated collection");
matches(proposalLeague.textContent, /nincs bekapcsolva|nem aktív|inaktív/i, "Proposed league explicitly lacks an activated collector");
matches(missingLeague.textContent, /hiányzó|nincs.*kapcsolat/i, "Missing league link is explained per row");
matches(existingLeague.textContent, /(?:mérkőzés|meccs)\s*:?\s*3/i, "Observed fixture count is separate from catalogue capabilities");
matches(existingLeague.textContent, /egyeztet(?:ve|ett)\s*:?\s*2/i, "Only the two observed matched contexts are reported as matched");
matches(existingLeague.textContent, /igen/i, "Advertised true capability remains affirmative");
matches(existingLeague.textContent, /nem/i, "Advertised false capability remains negative");
matches(existingLeague.textContent, /xG\s*:\s*—/, "Unknown xG capability stays unknown");
matches(existingLeague.textContent, /élő szorzó\s*:\s*—/i, "Unknown live-odds capability never becomes false");
matches(missingLeague.textContent, /(?:mérkőzés|meccs)\s*:\s*—/i, "Unobserved fixture coverage remains unknown");
matches(missingLeague.textContent, /egyeztet(?:ve|ett)\s*:\s*—/i, "Unobserved matched coverage remains unknown");
matches(text("source-health-leagues"), /hirdetett|deklarált|katalógus/i, "Capabilities are explicitly identified as provider claims");
matches(text("source-health-leagues"), /megfigyelt|futásban|ebben a futásban/i, "Observed run data has its own label");
const scrollRegions = descendants(byId("source-health-leagues")).filter(node => node.className.split(/\s+/).includes("source-health-scroll"));
ok(scrollRegions.length === 1, "The long league table has one contained scroll region");
const leagueScroll = scrollRegions[0];
ok(leagueScroll.tabIndex === 0 || leagueScroll.attrs.tabindex === "0", "Keyboard users can focus and scroll the league inventory");
ok((leagueScroll.attrs.role || leagueScroll.role) === "region", "Scrollable league inventory exposes a region role");
matches(leagueScroll.attrs["aria-label"] || "", /liga|sorozat|lefedettség/i, "Scrollable region has a descriptive accessible label");
ok(!byId("source-health-leagues-details").open, "Rendering does not force the long league inventory open");
byId("source-health-leagues-details").open = true;
T.renderSourceHealth();
ok(byId("source-health-leagues-details").open, "Refreshing preserves the user's expanded inventory");

projection.origin = "ARCHIVED_INPUT_AUDIT";
T.renderSourceHealth();
matches(text("source-health-context"), /archív|archiv|utólagos/i, "Archive-based diagnostic provenance is explicit");
has("source-health-context", projection.run_id);
leagueRows[0].mapping_status = "MISSING_LINK";
leagueRows[0].the_stats = null;
T.renderSourceHealth();
matches(text("source-health-leagues"), /meglévő gyűjtési út\s*:?\s*10/i, "Collector availability is independent of catalogue mapping status");
matches(text("source-health-leagues"), /hiányzó vagy többértelmű kapcsolat\s*:?\s*12/i, "An unresolved existing route also remains a mapping gap");
leagueRows[0].mapping_status = "MAPPED";
leagueRows[0].the_stats = {id: "comp_0", name: "Existing league 0", country: "Test country", advertised: {...flags}};

const attack = '<img src=x onerror="globalThis.compromised=true">';
projection.rows[0].provider = attack;
leagueRows[0].the_stats.name = "<script>globalThis.compromised=true</script>";
leagueRows[17].competition_id = "<svg onload=bad()>";
T.renderSourceHealth();
has("source-health-rows", attack);
has("source-health-leagues", "<script>globalThis.compromised=true</script>");
has("source-health-leagues", "<svg onload=bad()>");
ok(!nodes.some(node => ["img", "script", "svg"].includes(node.tagName)), "Untrusted provider and league strings cannot create active elements");
ok(context.compromised !== true && requests.length === 0, "Malicious text cannot execute code or issue requests");

projection.status = "MISSING";
T.renderSourceHealth();
matches(text("source-health-status"), /nincs|hiány|vár|nem.*érkez/i, "Explicit MISSING status is distinguished from a successful empty audit");
projection.status = "UNAVAILABLE";
T.renderSourceHealth();
matches(text("source-health-status"), /nem.*érhető|nem.*elérhető|hiba|nem.*olvasható|ellenőrizhetetlen/i, "Unavailable audit is not presented as healthy");
T.state.data = {};
T.renderExpertAdvisory();
matches(text("source-health-status"), /nincs|hiány|vár|nem.*érkez/i, "Expert rendering refreshes source health even without a saved advisory");
ok(!allText().includes(attack), "Missing projection clears stale provider and league evidence");
for (const invalid of [null, [], "not-a-report", {mode: "LIVE", status: "AVAILABLE", rows: projection.rows}]) {
  T.state.data = {source_health: invalid};
  T.renderSourceHealth();
  matches(text("source-health-status"), /nincs|hiány|vár|nem.*érkez/i, "Unrecognized projection cannot impersonate a valid SHADOW report");
  ok(!text("source-health-rows").includes(attack), "Invalid projection clears untrusted diagnostic rows");
}
ok(requests.length === 0, "All rendering states remain free of requests and writes");
console.log(checks + " source-health frontend assertions passed; no browser, requests, or persistent writes.");
