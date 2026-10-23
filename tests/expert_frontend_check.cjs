"use strict";

// Render-only contract tests. No browser, model/API requests, ledger or file writes.
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const assert = require("node:assert/strict");
process.chdir(path.resolve(__dirname, ".."));
const nodes = [], ids = new Map();
class Node {
  constructor(tagName = "div") { Object.assign(this, {tagName, children: [], _text: ""}); nodes.push(this); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || "").join(" "); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ""; this.children = items; }
  set innerHTML(value) { throw new Error("Unsafe HTML: " + value); }
}
const html = fs.readFileSync("src/goal_analysis/web/index.html", "utf8");
for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
  assert(!ids.has(match[1]), "duplicate HTML ID: " + match[1]);
  ids.set(match[1], new Node());
}
const document = {
  getElementById(id) { assert(ids.has(id), "missing HTML ID: " + id); return ids.get(id); },
  createElement(tagName) { return new Node(tagName); },
};
const context = {document, window: {}, URL, Intl, Date, console};
vm.createContext(context);
const source = fs.readFileSync("src/goal_analysis/web/app.js", "utf8");
vm.runInContext(source.replace("  state.today = todayBerlin();", "  globalThis.T={state,renderExpertAdvisory}; return;\n  state.today = todayBerlin();"), context);
const T = context.T, text = id => ids.get(id).textContent;
let checks = 0;
const ok = (condition, message) => { assert(condition, message); checks++; };
const has = (id, value) => ok(text(id).includes(value), id + " contains " + value);

T.state.data = {};
T.renderExpertAdvisory();
has("expert-advisory-status", "Még nincs mentett elemzés");
has("expert-advisory-cards", "Időjárási szakértő");
has("expert-advisory-cards", "Motivációs szakértő · Grok");
has("expert-advisory-cards", "—");
has("expert-network", "A teljes névsort a műhely beállításai adják");
ok(!text("expert-advisory-cards").includes("Súlyozásra alkalmas ma 0"), "missing analysis is unknown, not measured zero");

const network = [
  {id: "arthur", name: "Arthur · Astra", role: "Közös elemzés", status: "CONFIGURED", detail: "Codex"},
  {id: "daniel", name: "Dániel", role: "Adatbeszerzés", status: "ACTIVE", detail: "Meglévő sportszolgáltatók"},
  ...["gauss", "kepler", "laplace"].map(id => ({id, name: id.toUpperCase(), role: "Numerikus modell", status: "PROBATION"})),
  ...Array.from({length: 20}, (_, i) => ({id: "club_" + i, name: "Klubtag " + i, role: "Önálló tipster", status: "ACTIVE"})),
  {id: "daily223", name: "2×2×3", role: "Védett konstrukció", status: "PROTECTED"},
  {id: "grail", name: "Szent Grál", role: "Csapatértékelés", status: "ACTIVE"},
  {id: "grok", name: "Grok", role: "Közös véleményezés", status: "SHADOW"},
  {id: "nemotron", name: "Nemotron szerepkör", role: "Második nézőpont", status: "CONFIGURED", detail: "Aktuális motor: Codex"},
];
const current = {
  date: "2026-10-18", run_id: "current", fixtures: [
    {fixture_id: "api_football:1", home_team: "Hazai FC", away_team: "Vendég FC"},
    {fixture_id: "api_football:2", home_team: "Másik FC", away_team: "Harmadik FC"},
  ],
  recommendations: [{profile_id: "DAILY_223", legs: [{fixture_id: "api_football:1", decimal_price: 2.2}]}],
};
const advisory = {
  status: "AVAILABLE", observed_at: "2026-10-18T00:20:00Z", date: current.date, run_id: "current",
  weather: {
    coverage: {completed_matches: 13015, matches_with_reanalysis: 3, matches_with_prematch_forecast: 23},
    sources: [{title: "PMC-tanulmány", url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC11474995/"}],
    gaps: ["Még kevés az összevethető csapatszintű minta."],
    fixtures: [
      {fixture_id: "api_football:1", status: "INSUFFICIENT_DATA", reason: "INSUFFICIENT_COMPARABLE_HISTORY", live_weight_eligible: false, weather: {source_url: "https://open-meteo.com/"}},
      {fixture_id: "api_football:2", status: "UNVALIDATED", reason: "FORECAST_WEIGHTING_NOT_VALIDATED", live_weight_eligible: false},
    ],
  },
  motivation: {
    counts: {fixtures: 2, with_standings: 0, with_claims: 1, review_required: 1},
    fixtures: [
      {fixture_id: "api_football:1", review_required: true, standing_evidence: [], human_factors: [{source_url: "https://x.com/club/status/123"}]},
      {fixture_id: "api_football:2", standing_evidence: [], human_factors: []},
    ],
  },
};
T.state.data = {latest: current, expert_advisory: advisory, expert_network: network};
const before = JSON.stringify(T.state.data);
T.renderExpertAdvisory();
ok(JSON.stringify(T.state.data) === before, "render leaves original recommendations including DAILY_223 untouched");
has("expert-advisory-meta", "2026-10-18");
has("expert-advisory-meta", "kijelzett csomaghoz");
has("expert-advisory-cards", "13");
has("expert-advisory-cards", "Minta látható · ellenőrzésre vár");
has("expert-advisory-fixtures", "Még kevés az adat");
has("expert-advisory-cards", "Még kevés az összevethető csapatszintű minta.");
has("expert-advisory-cards", "PMC-tanulmány");
has("expert-advisory-fixtures", "Hazai FC – Vendég FC");
has("expert-advisory-fixtures", "A szelvények súlyát jelenleg nem módosítja.");
has("expert-advisory-fixtures", "korabeli előrejelzéseken");
has("expert-advisory-fixtures", "Több, egyeztetést igénylő állítás");
has("expert-advisory-fixtures", "Nincs ellenőrzött tabella");
ok(ids.get("expert-advisory-fixtures").children.every(node => node.tagName === "details"), "each match collapsible");
ok(ids.get("expert-network").children.length === network.length, "all existing and new registry roles visible");
has("expert-network", "Klubtag 19");
has("expert-network", "Aktuális motor: Codex");
ok(!text("expert-network").includes("Nemotron API aktív"), "no invented Nemotron connection");

advisory.run_id = "older";
advisory.weather.summary = "DO_NOT_SHOW_OTHER_RUN";
T.renderExpertAdvisory();
has("expert-advisory-status", "Másik elemzéshez tartozik");
ok(!text("expert-advisory-cards").includes("DO_NOT_SHOW_OTHER_RUN"), "different run not current assessment");
ok(!text("expert-advisory-fixtures").includes("Hazai FC"), "different-run fixture findings not shown");
advisory.run_id = "current";
advisory.date = "2026-10-17";
T.renderExpertAdvisory();
has("expert-advisory-status", "Másik elemzéshez tartozik");
advisory.date = current.date;
advisory.status = "NOT_RUN";
T.renderExpertAdvisory();
ok(!text("expert-advisory-cards").includes("DO_NOT_SHOW_OTHER_RUN"), "NOT_RUN does not expose stale report");

advisory.status = "AVAILABLE";
advisory.weather.summary = '<img src=x onerror="bad()">';
advisory.weather.sources.push({title: "DO_NOT_SHOW_BAD_LINK", url: "javascript:alert(1)"});
advisory.weather.sources.push({title: "DO_NOT_SHOW_CREDENTIAL_LINK", url: "https://secret:password@example.com/"});
advisory.weather.fixtures[0].summary = "<script>bad()</script>";
network.push({id: "attack", name: "<svg onload=bad()>", role: "<b>role</b>", status: "ACTIVE", detail: "Plain text"});
T.renderExpertAdvisory();
has("expert-advisory-cards", '<img src=x onerror="bad()">');
has("expert-advisory-fixtures", "<script>bad()</script>");
has("expert-network", "<svg onload=bad()>");
ok(!text("expert-advisory-cards").includes("DO_NOT_SHOW"), "unsafe links are excluded");
ok(!nodes.some(node => ["img", "svg", "script"].includes(node.tagName)), "untrusted strings remain text only");
ok(nodes.filter(node => node.tagName === "a").every(node => node.target === "_blank" && node.rel === "noopener noreferrer"), "source links isolated safely");

advisory.weather.fixtures[0].live_weight_eligible = true;
advisory.weather.fixtures[0].status = "VALIDATED";
T.renderExpertAdvisory();
has("expert-advisory-fixtures", "súlyozásra alkalmas");
network.push({...network[0]});
T.renderExpertAdvisory();
ok(ids.get("expert-network").children.length === new Set(network.map(row => row.id)).size, "role IDs deduplicated");
advisory.motivation.fixtures[1].standings_context = {
  teams: [{team_name: "Másik FC", rank: 4, points: 20, played: 10}],
  rules_status: "UNVERIFIED", source_url: "https://v3.football.api-sports.io/standings?league=39",
};
T.renderExpertAdvisory();
has("expert-advisory-fixtures", "Másik FC: 4. hely, 20 pont, 10 lejátszott meccs");
has("expert-advisory-fixtures", "A matematikai tét még nem igazolt a versenyszabályokkal.");
has("expert-advisory-cards", "Friss tabellával");
advisory.weather.fixtures[1].source_url = "https://open-meteo.com/en/docs";
advisory.weather.fixtures[1].reason = "MISSING_WEATHER";
T.renderExpertAdvisory();
has("expert-advisory-fixtures", "Nincs a mérkőzéshez használható időjárási adat.");
ok(nodes.some(node => node.tagName === "a" && node.href === "https://open-meteo.com/en/docs"), "top-level forecast source is linked");
ok(source.includes("expert_advisory:data.expert_advisory,expert_network:data.expert_network"), "status refresh includes expert changes");
ok(html.indexOf('id="team-experts"') < html.indexOf('id="team-members"'), "shared panel precedes existing member cards");
console.log(checks + " expert frontend assertions passed; no requests or persistent writes.");
