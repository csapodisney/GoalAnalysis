const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ids = new Map();
class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this._text=""; this.attrs={}; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c?.textContent ?? "").join(" "); }
  append(...items) { this.children.push(...items.filter(Boolean)); }
  replaceChildren(...items) { this._text=""; this.children=items; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  set innerHTML(value) { throw Error("Unsafe HTML: "+value); }
}
const html=fs.readFileSync("src/goal_analysis/web/index.html","utf8");
for (const match of html.matchAll(/<([\w:-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
  assert(!ids.has(match[3]), "Duplicate HTML ID"); ids.set(match[3],new Node(match[1]));
}
const context={document:{getElementById:id=>{assert(ids.has(id),id);return ids.get(id);},
  createElement:tag=>new Node(tag)},window:{},Intl,Date,URL,console,
  fetch(){throw Error("Rendering cannot fetch data");}};
vm.createContext(context);
const source=fs.readFileSync("src/goal_analysis/web/app.js","utf8");
const seam="  state.today = todayBerlin();";
assert(source.includes(seam));
vm.runInContext(source.replace(seam,"  globalThis.T={renderDailyTicketKpis};return;\n"+seam),context);
const show=(data,id="daily-ticket-kpis")=>{context.T.renderDailyTicketKpis(data,id);return ids.get(id).textContent;};
const row={date:"2026-10-21",confirmed_winning_tickets:3,winning_odds_complete:true,
  winning_combined_odds_sum:30,pending_tickets:0,review_tickets:0,day_results_complete:true};
let value=show({daily_kpis:{status:"AVAILABLE",days:[row],all_input_days_assigned:true}});
assert(value.includes("Nyertes szorzók összege") && value.includes("30") && value.includes("Lezárt"));
value=show({daily_kpis:{status:"AVAILABLE",mode:"paper",days:[{...row,winning_odds_complete:false,
  winning_combined_odds_sum:99999,known_winning_odds_sum:18,day_results_complete:false,pending_tickets:2}]}},"paper-daily-ticket-kpis");
assert(!value.includes("99999"));assert(value.includes("18") && value.includes("—"));
assert(value.includes("Még változhat") && value.includes("ár nélkül igazolt papírnyertesek"));
value=show({daily_kpis:{status:"AVAILABLE",days:[]}});
assert(value.includes("nincs naplózott szelvény") && !value.includes("Lezárt"));
value=show({daily_kpis:{status:"UNAVAILABLE"}});
assert(value.includes("nem mérhetők") && !value.includes("30"));
value=show({});assert(value.includes("nem mérhetők"));
const days=Array.from({length:32},(_,i)=>({...row,date:new Date(Date.UTC(2026,8,i+1)).toISOString().slice(0,10)}));
value=show({daily_kpis:{status:"AVAILABLE",days,all_input_days_assigned:false}});
assert(value.includes("31 naplózott nap") && value.includes("naphoz nem rendelhető"));
assert(!value.includes("2026-09-01") && value.includes("2026-10-02"));
const dataRows=ids.get("daily-ticket-kpis").children[0].children[1].children;
assert.equal(dataRows.length,31);
assert(source.includes('renderDailyTicketKpis(data, "paper-daily-ticket-kpis")'));
assert(source.includes('renderDailyTicketKpis(data, "daily-ticket-kpis")'));
console.log("DAILY_KPI_FRONTEND_PASS: mode binding, missing/zero/partial, bounded dates, no fetch or unsafe HTML");
