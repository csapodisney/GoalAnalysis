"use strict";

// Local-only UI checks: no provider, browser, ledger or credential access.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
process.chdir(path.resolve(__dirname, ".."));
const nodes = [], ids = new Map(), requests = [];
class Node {
  constructor(tagName = "div") { Object.assign(this, {tagName, children:[], _text:"", disabled:false}); nodes.push(this); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent || "").join(" "); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ""; this.children = items; }
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
let response;
const context = {document, window:{}, URL, Intl, Date, AbortController, console, setTimeout:() => 1, clearTimeout(){}, fetch:async (url, options) => { requests.push({url, options}); return typeof response === "function" ? response(options) : response; }};
vm.createContext(context);
const source = fs.readFileSync("src/goal_analysis/web/app.js", "utf8");
vm.runInContext(source.replace("  state.today = todayBerlin();", "  globalThis.T={state,renderGrok,loadGrok,grokSourceUrl}; return;\n  state.today = todayBerlin();"), context);
const T = context.T;
const text = id => ids.get(id).textContent;
let checks = 0;
function ok(value, message) { assert(value, message); checks++; }
function has(id, fragment, message) { ok(text(id).includes(fragment), message || fragment); }
const payload = (day = "2026-10-18") => ({
  enabled:true, mode:"SHADOW", automatic_changes:false, day, status:"COMPLETE",
  usage:{day_requests:1, max_daily_requests:3, day_charged_usd:0.2, daily_limit_usd:1, month_charged_usd:0.2, monthly_limit_usd:20},
  latest:{status:"COMPLETE", created_at:"2026-10-17T21:00:00Z", coverage:{profile_count:2, fixture_count:4}, result:{status:"COMPLETE", claims:[{
    claim_id:"c1", fixture_id:123, kind:"ROTATION", claim:"A klub pihenteti a csatárt.", source_url:"https://x.com/club/status/123456789", author:"club", published_at:"2026-10-17T20:00:00Z", fetched_at:"2026-10-17T21:00:00Z", content_status:"UNVERIFIED", provenance_status:"CITATION_AND_TIMESTAMP_CHECKED",
  }], critiques:[{profile_id:"forma_soma", disposition:"REVIEW", note:"A kezdőcsapatot érdemes ellenőrizni.", claim_ids:["c1"]}, {profile_id:"other", disposition:"NO_NEW_EVIDENCE", note:"Nincs új hír.", claim_ids:[]}]}}
});
function setData(data) { T.state.grok = data; T.state.grokDay = T.state.date; T.renderGrok(); }

(async () => {
  T.state.date = "2026-10-18";
  T.renderGrok(); has("grok-status", "Még nincs lekért állapot"); has("grok-reports", "Még nincs mentett");
  setData({...payload(), latest:null}); has("grok-reports", "Még nincs mentett");
  setData(payload()); has("grok-status", "Értékelés elkészült"); has("grok-reports", "A klub pihenteti a csatárt."); has("grok-reports", "további ellenőrzést igényel"); has("grok-reports", "Közzététel:"); has("grok-reports", "A kezdőcsapatot érdemes ellenőrizni.");
  has("grok-meta", "Korábbi részvizsgálat: 2 szelvény · 4 külön meccs"); has("grok-usage", "0,2 USD"); has("grok-usage", "félretett összeget"); has("grok-usage", "Elszámolt keret (2026-10-18)"); has("grok-day", "2026-10-18");
  const full = payload();
  full.latest.coverage = {scope:"ALL_TICKETS_ONE_BATCH", ready_profile_count:32, profile_count:30, fixture_count:24, excluded:[{profile_id:"locked", reason_code:"PREMATCH_LOCKOUT"},{profile_id:"invalid", reason_code:"INVALID_LEG"}]};
  full.latest.result.critiques = Array.from({length:30}, (_, i) => ({recommendation_id:`r${i}`, profile_id:i === 0 ? "DAILY_223" : `profile_${i}`, protected_profile:i === 0, advisory_only:true, verdict:["KEEP","QUESTIONABLE","INSUFFICIENT_EVIDENCE"][i % 3], strengths:`Mellette szóló érv ${i}`, risks:`Ellene szóló érv ${i}`, flagged_legs:i === 1 ? [{leg_index:2, fixture_id:123, reason:"A kezdő hiányozhat.", claim_ids:["c1"]}] : [], claim_ids:["c1"]}));
  const preserved = JSON.stringify(full);
  setData(full); has("grok-status", "Értékelés elkészült"); has("grok-meta", "Profilok a csomagban: 32"); has("grok-meta", "Értékelésre átadva: 30 szelvény · 24 külön meccs"); has("grok-meta", "Beérkezett értékelés: 30 / 30 szelvény");
  has("grok-reports", "Kihagyott szelvények (2)"); has("grok-reports", "15 perces zárolás"); has("grok-reports", "nem ellenőrizhető meccsadat");
  has("grok-reports", "Védett 2×2×3: csak vélemény, változtatás nélkül."); has("grok-reports", "Megtartható"); has("grok-reports", "Kérdéses"); has("grok-reports", "Kevés bizonyíték"); has("grok-reports", "Mellette szóló érv 29"); has("grok-reports", "Ellene szóló érv 29"); has("grok-reports", "3. választás");
  ok(JSON.stringify(full) === preserved, "all-ticket rendering leaves input including protected Daily untouched");
  full.latest.cache_stale = true; setData(full); has("grok-meta", "Korábbi hírkép"); has("grok-meta", "nincs új hírlekérés");
  full.latest.result.critiques.splice(27); setData(full); has("grok-status", "Részleges értékelés"); has("grok-meta", "Beérkezett értékelés: 27 / 30 szelvény");
  ok(!text("grok-meta").includes("30 / 30"), "missing critiques never implied reviewed");
  T.state.data = {latest:{date:"2026-10-18", grok_review:{mode:"ONE_BATCH_CONDITIONAL_REPAIR", status:"COMPLETE", repair_rounds:1, changed_profiles:["forma_soma"], concurrence:{status:"COMPLETE"}, repair:{unresolved:[{profile_id:"unresolved"}]}}}};
  T.renderGrok(); has("grok-repair", "Módosult profilok:"); has("grok-repair", "Független ellenőrzés:"); has("grok-repair", "Megoldatlan jelzések: 1"); has("grok-repair", "Javítási körök: 1 / legfeljebb 1"); has("grok-repair", "védett 2×2×3 változatlan");
  T.state.data.latest.grok_review = {mode:"ONE_BATCH_CONDITIONAL_REPAIR", status:"COMPLETE", repair_rounds:0, changed_profiles:[], repair_status:"NO_ACTIONABLE_CONCERN"};
  T.renderGrok(); has("grok-repair", "nem változott a szelvények összetétele"); has("grok-repair", "Nem volt javítást indokló");
  T.state.data.latest.date = "2026-10-17"; T.renderGrok(); ok(!text("grok-repair"), "other date repair outcome never shown"); T.state.data = null;
  for (const value of ["javascript:alert(1)", "http://x.com/club/status/1", "https://x.com.evil.test/club/status/1", "https://x.com@evil.test/club/status/1", "https://user@x.com/club/status/1", "https://x.com:444/club/status/1", "https://x.com/club", "https://twitter.com/club/status/1/evil", null, {}]) ok(T.grokSourceUrl(value) === null, "unsafe or nonpost URL rejected");
  ok(T.grokSourceUrl("https://twitter.com/club/status/123?track=1#fragment") === "https://twitter.com/club/status/123", "tracking stripped from valid source");
  ok(T.grokSourceUrl("https://x.com/i/web/status/123") === "https://x.com/i/web/status/123", "canonical X post URL accepted");
  const attack = payload();
  attack.latest.result.claims[0].claim = '<img src=x onerror="bad()">';
  attack.latest.result.claims[0].author = "<script>bad()</script>";
  attack.latest.result.critiques[0].note = "<svg onload=bad()>";
  attack.latest.result.claims.push({...attack.latest.result.claims[0], claim_id:"bad", claim:"DO_NOT_SHOW_BAD_SOURCE", source_url:"https://evil.test/club/status/123"});
  attack.latest.result.claims.push({...attack.latest.result.claims[0], claim_id:"unchecked", claim:"DO_NOT_SHOW_UNCHECKED", provenance_status:"UNKNOWN"});
  attack.latest.result.critiques.push({profile_id:"DAILY_223", disposition:"REVIEW", claim_ids:["c1"], note:"DO_NOT_SHOW_DAILY_CHANGE"}, {profile_id:"test", disposition:"REVIEW", claim_ids:["missing"], note:"DO_NOT_SHOW_UNSOURCED"}, {profile_id:"test", disposition:"NO_NEW_EVIDENCE", claim_ids:[], note:"Nem talált új bizonyítékot."});
  setData(attack); has("grok-reports", '<img src=x onerror="bad()">', "untrusted claim is plain text"); has("grok-reports", "<svg onload=bad()>", "untrusted critique is plain text"); has("grok-reports", "Nem talált új bizonyítékot.");
  ok(!text("grok-reports").includes("DO_NOT_SHOW"), "unsafe, unverified, protected or unsourced findings hidden");
  ok(!nodes.some(node => ["img","script","svg"].includes(node.tagName)), "no executable model HTML created");
  ok(nodes.filter(node => node.tagName === "a").every(node => node.target === "_blank" && node.rel === "noopener noreferrer" && T.grokSourceUrl(node.href)), "all links have safe target and source");
  const malformed = payload(); malformed.latest.result.claims = [null,"invalid",{}]; malformed.latest.result.critiques = [null,"invalid",{}]; malformed.usage.day_charged_usd = null;
  setData(malformed); has("grok-reports", "Még nincs megjeleníthető"); has("grok-usage", "nem ismert");
  T.state.grokDay = "2026-10-17"; T.renderGrok(); has("grok-reports", "Még nincs mentett", "previous date data not shown");
  response = {ok:true, json:async () => payload()}; await T.loadGrok(true);
  has("grok-status", "Értékelés elkészült"); ok(requests.at(-1).url === "/api/grok?date=2026-10-18", "only selected date fetched");
  const first = ids.get("grok-reports").children[0]; await T.loadGrok(true);
  ok(ids.get("grok-reports").children[0] === first, "same payload preserves current report DOM");
  const count = requests.length; await T.loadGrok(); ok(requests.length === count, "refresh polling throttled");
  response = {ok:false, status:500}; await T.loadGrok(true); has("grok-feedback", "nem olvasható"); ok(!T.state.grok && !ids.get("grok-refresh").disabled, "failure releases control without stale report");
  response = {ok:true, json:async () => ({...payload(), day:"2026-10-17", raw_error:"SECRET_MUST_NOT_BE_DISPLAYED"})}; await T.loadGrok(true);
  ok(!T.state.grok && !text("grok-feedback").includes("SECRET_MUST_NOT_BE_DISPLAYED"), "wrong date or raw errors cannot enter report");
  response = {ok:true, json:async () => ({...payload(), mode:"LIVE", automatic_changes:true})}; await T.loadGrok(true); ok(!T.state.grok, "unexpected live mode not presented as shadow");
  let finish;
  response = () => new Promise(resolve => { finish = resolve; });
  const pending = T.loadGrok(true); const beforeDuplicate = requests.length; await T.loadGrok(true);
  ok(requests.length === beforeDuplicate, "inflight duplicate request suppressed");
  T.state.date = "2026-10-19";
  response = {ok:true, json:async () => payload("2026-10-19")}; await T.loadGrok();
  ok(requests.at(-2).options.signal.aborted, "old request aborted on date change");
  finish({ok:true, json:async () => payload()}); await pending;
  ok(T.state.grok.day === "2026-10-19" && !T.state.grokFetching, "late previous date response cannot overwrite current date");
  has("grok-day", "2026-10-19");
  ok(requests.every(item => !item.options.method && !item.options.body && item.options.credentials === "same-origin" && item.options.cache === "no-store"), "only read-only same-origin GET, no provider request");
  ok(html.includes("nem módosítják a szelvényeket") && html.includes("A 2×2×3 változatlan") && html.includes("nem indít fizetős keresést"), "measurement boundary and readonly refresh clear");
  console.log(`${checks} Grok frontend contract and safety assertions passed; no network/browser/data writes.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
