const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const ids=new Map();
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this._text="";this.attrs={};}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c?.textContent??"").join(" ");}
  append(...items){this.children.push(...items.filter(Boolean));}
  replaceChildren(...items){this._text="";this.children=items;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  set innerHTML(value){throw Error("Unsafe HTML "+value);}
}
const html=fs.readFileSync("src/goal_analysis/web/index.html","utf8");
for(const m of html.matchAll(/<([\w:-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){assert(!ids.has(m[3]));ids.set(m[3],new Node(m[1]));}
const c={document:{getElementById:id=>{assert(ids.has(id),id);return ids.get(id);},createElement:tag=>new Node(tag)},
  window:{},Intl,Date,URL,console,fetch(){throw Error("Render cannot fetch");}};
vm.createContext(c);
const source=fs.readFileSync("src/goal_analysis/web/app.js","utf8"),seam="  state.today = todayBerlin();";
vm.runInContext(source.replace(seam,"  globalThis.T={renderRollingTicketKpis};return;\n"+seam),c);
const show=(data,id="rolling-ticket-kpis")=>{c.T.renderRollingTicketKpis(data,id);return ids.get(id).textContent;};
const financial={settled_net_eur_to_date:5,max_drawdown_eur:2};
const window={window_days:7,observed_days:3,goals:{observed_open_days:1,observed_closed_days:2,
  count_goal_met_closed_days:1,count_goal_closed_observed_rate_pct:50,joint_goal_closed_observed_rate_pct:50},financial,
  profile_reliability:[{profile_id:"one",profile_name:"Első szakértő",strategy_version:"UNKNOWN_LEGACY",
  decided_ticket_days:2,won:1,lost:1,decided_win_rate_pct:50,financial}]};
const data={rolling_kpis:{status:"AVAILABLE",mode:"paper",window_end_date:"2026-10-22",windows:[window]}};
let text=show(data);
assert(text.includes("3 / 7") && text.includes("1 / 2") && text.includes("50%"));
assert(text.includes("Ismeretlen régi változat") && text.includes("nem igazolt előrejelző megbízhatóság"));
assert(text.includes("hiányzó és nyitott nap nem vesztes nap"));
data.rolling_kpis.windows[0].financial={settled_net_eur_to_date:null,max_drawdown_eur:null,
  known_subset_settled_net_eur:5,known_subset_max_drawdown_eur:2};
data.rolling_kpis.windows[0].goals.joint_goal_unknown_closed_days=1;
text=show(data,"paper-rolling-ticket-kpis");
assert(text.includes("ismert rész") && text.includes("árhiányos nap") && text.includes("—"));
assert(text.includes("1 €-s hipotetikus"));
text=show({rolling_kpis:{status:"FUTURE_PERIOD"}});assert(text.includes("jövőbeli hónap")&&!text.includes("50%"));
text=show({});assert(text.includes("nem érhetők el")&&!text.includes("50%"));
data.rolling_kpis.windows[0].profile_reliability=Array.from({length:41},(_,i)=>({...window.profile_reliability[0],profile_name:"Szakértő "+i}));
text=show(data);assert(text.includes("első 40 változat")&&!text.includes("Szakértő 40"));
assert(source.includes('renderRollingTicketKpis({}, "rolling-ticket-kpis")'));
console.log("ROLLING_KPI_FRONTEND_PASS: closed denominators, missing vs zero, price subset, future/stale clearing, bounded profiles; no fetch");
