"use strict";
// Exercise the actual renderer in a minimal DOM. No browser or network needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const root = process.cwd();
const mode = process.argv[2];
const sourcePath = process.env.ARTHUR_CAPACITY_UI_SOURCE || path.join(root, "src/goal_analysis/web/app.js");
const source = fs.readFileSync(sourcePath, "utf8");
const ids = new Map();
class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this._text=""; this.attrs={}; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c?.textContent ?? "").join(" "); }
  append(...items) { this.children.push(...items.filter(Boolean)); }
  replaceChildren(...items) { this._text=""; this.children=items; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  set innerHTML(value) { throw Error("Unsafe HTML"); }
}
const html=fs.readFileSync(path.join(root,"src/goal_analysis/web/index.html"),"utf8");
for (const match of html.matchAll(/\bid="([^"]+)"/g)) ids.set(match[1],new Node("div"));
const context={document:{getElementById:id=>{assert(ids.has(id),id);return ids.get(id);},createElement:tag=>new Node(tag)},
  window:{},Intl,Date,URL,console,fetch(){throw Error("Rendering must not fetch");}};
vm.createContext(context);
const seam="  state.today = todayBerlin();";
assert.equal(source.split(seam).length,2);
vm.runInContext(source.replace(seam,"  globalThis.T={renderDecisionDataUsage};return;\n"+seam),context);
function freeze(value) { if(value && typeof value==="object") { Object.freeze(value); for(const v of Object.values(value)) freeze(v); } return value; }
const show=report=>{
  const before=JSON.stringify(report);freeze(report);
  context.T.renderDecisionDataUsage(report);
  assert.equal(JSON.stringify(report),before,"Renderer mutated the original report");
  const node=ids.get("decision-data-usage");return node.children[0]?.textContent || node.textContent;
};
const base={version:"arthur-portfolio-objectives-v2-live-paper",ready_live_recommendations:1,
  ready_paper_recommendations:0,ready_recommendations:99999,ready_recommendations_all_scopes:99998,
  capacity_status:"BELOW_TARGET",capacity_shortfall:19,target_ready_range:{minimum:20,maximum:30},
  non_daily_target_price_range:{minimum:10,maximum:50}};
if(mode==="v2") {
  let text=show({portfolio_objectives:{...base}});
  assert(text.includes("1 LIVE · 0 PAPER") && text.includes("19 LIVE ajánlat hiányzik"));
  assert(text.includes("LIVE cél 20–30") && !text.includes("99999") && !text.includes("99998"));
  text=show({portfolio_objectives:{...base,ready_live_recommendations:0,ready_paper_recommendations:20,capacity_shortfall:20}});
  assert(text.includes("0 LIVE · 20 PAPER") && text.includes("20 LIVE ajánlat hiányzik"));
  text=show({portfolio_objectives:{...base,ready_live_recommendations:20,ready_paper_recommendations:5,capacity_status:"IN_TARGET",capacity_shortfall:0}});
  assert(text.includes("20 LIVE · 5 PAPER") && text.includes("LIVE célon belül"));
  text=show({portfolio_objectives:{...base,ready_live_recommendations:31,capacity_status:"ABOVE_TARGET"}});
  assert(text.includes("LIVE cél fölött"));
} else if(mode==="legacy") {
  for(const objective of [{ready_recommendations:24,capacity_status:"IN_TARGET"},
    {...base,ready_live_recommendations:undefined,ready_paper_recommendations:undefined},
    {...base,ready_live_recommendations:null},{...base,ready_paper_recommendations:null}]) {
    const text=show({portfolio_objectives:objective});
    assert(text.includes("ismeretlen") && text.includes("LIVE/PAPER bontás nem ismert"));
    assert(!text.includes("24 LIVE") && !text.includes("LIVE célon belül") && !text.includes("19 LIVE ajánlat hiányzik"));
  }
  const text=show({});assert(text.includes("első új futás"));
} else if(mode==="invalid") {
  for(const value of [null,undefined,false,true,"0","20",-1,1.5,NaN,Infinity,9007199254740992]) {
    for(const field of ["ready_live_recommendations","ready_paper_recommendations"]) {
      const text=show({portfolio_objectives:{...base,[field]:value}});
      assert(text.includes("ismeretlen") && text.includes("LIVE/PAPER bontás nem ismert"));
      assert(!text.includes("LIVE célon belül") && !text.includes("19 LIVE ajánlat hiányzik"));
    }
  }
  for(const value of [null,undefined,false,"19",-1,1.5,NaN]) {
    const text=show({portfolio_objectives:{...base,capacity_shortfall:value}});
    assert(text.includes("LIVE kapacitásállapot nem ismert") && !text.includes("0 LIVE ajánlat hiányzik"));
  }
  assert(!show({portfolio_objectives:{...base,capacity_status:null}}).includes("LIVE cél fölött"));
} else if(mode==="retained" || mode==="retained-before") {
  const file=process.argv[3];const raw=fs.readFileSync(file,"utf8");const report=JSON.parse(raw);
  const text=show(report);assert.equal(fs.readFileSync(file,"utf8"),raw);
  console.log(JSON.stringify({file,run_id:report.run_id,text,objectives:report.portfolio_objectives,
    original_report_json_unchanged:true,not_new_quote_or_generation:true}));
} else throw Error("Unknown test mode");
console.log("CAPACITY_SCOPE_FRONTEND_PASS:"+mode);
