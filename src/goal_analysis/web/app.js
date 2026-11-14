/* Arthur dashboard candidate. Stored-data reads only; mutations require explicit action. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const arr = value => Array.isArray(value) ? value : [];
  const obj = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const text = value => typeof value === "string" ? value : finite(value) ? String(value) : "";
  const nf = new Intl.NumberFormat("hu-HU", { maximumFractionDigits: 2 });
  const moneyFmt = new Intl.NumberFormat("hu-HU", { style: "currency", currency: "EUR" });
  const dateFmt = new Intl.DateTimeFormat("hu-HU", { timeZone: "Europe/Berlin", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const number = value => finite(value) ? nf.format(value) : "—";
  const money = value => finite(value) ? moneyFmt.format(value) : "Nem ismert";
  const when = value => typeof value === "string" && value && Number.isFinite(Date.parse(value)) ? dateFmt.format(new Date(value)) : "Időpont nem ismert";
  const validDate = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T12:00:00Z`)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value;
  const berlinToday = () => {
    const parts = new Intl.DateTimeFormat("en", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
    return ["year", "month", "day"].map(key => parts.find(part => part.type === key).value).join("-");
  };
  const AGENTS = ["kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "tipster_archiv_nora", "tipster_forma_soma", "tipster_ertek_milan", "tipster_szines_reka", "tipster_fek_tade"];
  const PAIR_AGENTS = ["daily_portfolio", "legacy_daily223"];
  const ALL_AGENT_IDS = [...AGENTS, ...PAIR_AGENTS];
  const independentPair = () => state.workshop?.capacity?.version === "TWELVE_INDEPENDENT_AGENT_SLOTS_V2";
  const visibleAgentIds = () => independentPair() ? ALL_AGENT_IDS : AGENTS;
  const FAMILIES = {
    calendar: "Teljes napi naptár", retained_history: "Historikus meccsadatok", odds_and_price_history: "Szorzók és ártörténet",
    team_stats_xg: "Csapatstatisztika és xG", players_squad_xi: "Játékosok, keret, kezdőcsapat", schedule_venue_pitch: "Program, stadion, pálya",
    weather: "Időjárás", sourced_news: "Forrással ellátott hírek", numeric_models: "Közös numerikus modellek",
    historical_strategy_evaluation: "Történeti szimuláció", candidate_scenarios: "Saját forgatókönyvek", own_outcomes_and_costs: "Saját eredmények és költségek"
  };
  const LABELS = {
    AVAILABLE: "Elérhető", UNKNOWN: "Ismeretlen", STALE: "Elavult", CONFLICTING: "Ellentmondásos", CACHED: "Egyező mentett eredmény",
    NOT_RUN: "Még nem futott", NOT_ATTEMPTED: "Nem indult el", UNATTEMPTED: "Nem indult el", RESERVED: "Indítás lefoglalva", RUNNING: "Folyamatban",
    VERIFYING: "Ellenőrzés alatt", SUCCEEDED: "Ellenőrizve, cél teljesült", COMPLETE: "Befejezett", COMPLETED: "Befejezett", DECISION_COMPLETED: "Döntés befejezve",
    ABSTAIN: "Tartózkodott", ABSTAINED: "Tartózkodott", PARTIAL: "Részleges", FAILED: "Sikertelen", STOPPED: "Leállt", DATA_BLOCKED: "Adathiány miatt megállt",
    TEAM_PAUSED: "Agent szünetel", READY: "READY", DRAFT: "DRAFT", PAPER: "PAPER", LIVE: "LIVE", WON: "Nyert", LOST: "Vesztett", VOID: "Érvénytelen",
    PENDING: "Függőben", REVIEW: "Ellenőrzést igényel", ACTIVE: "Aktív", PROBATION: "Próbaidő", SUSPENDED: "Szüneteltetve", MISSED: "Kimaradt indítás",
    DISABLED: "Kikapcsolva", ENABLED: "Bekapcsolva", PASS: "Ellenőrizve", VERIFIED: "Ellenőrizve", NOT_AVAILABLE: "Nem elérhető", LEGACY_REPORT: "Történeti riport"
  };
  const VIEW = {
    workshop: ["Napi műhely", "TIZENKÉT AGENT · EGY TELJES FUTBALLNAP", "A nap kínálata, a saját döntések és az elkészült szelvények egy helyen."],
    agents: ["Tizenkét külön gondolkodásmód", "AZ AGENTEK", "Saját választási út, közös adatbázis és mindenkinél látható döntési állapot."],
    results: ["Eredmények és pénzügy", "A MEGŐRZÖTT EREDMÉNYEK", "A tényleges megjátszás, a papíreredmény és az ismeretlen költség külön mérhető."],
    data: ["Adatok és ellenőrzés", "A DÖNTÉS MÖGÖTT", "Teljes meccsnaptár, adatlefedettség és visszakereshető bizonyíték."],
    operations: ["A műhely működése", "KÉZI ÉS REGGELI INDÍTÁS", "Egyszeri, felügyelt futások. Az állapot a mentett eredményből származik."]
  };
  const busyStates = new Set(["RESERVED", "RUNNING", "VERIFYING"]);
  const terminalStates = new Set(["SUCCEEDED", "PARTIAL", "FAILED", "STOPPED"]);
  const state = {
    date: berlinToday(), view: "workshop", workshop: null, connected: false, busy: false, epoch: 0, refreshSeq: 0,
    cards: [], cardsRun: null, cardsTotal: null, cardsNext: null, cardsLoading: false, cardsSeq: 0,
    calendar: [], calendarNext: null, calendarTotal: null, calendarRun: null, calendarLoading: false, calendarSeq: 0,
    evidence: [], evidenceNext: null, evidenceFamily: null, evidenceSeq: 0, evidenceLoading: false,
    performance: null, performanceMode: null, performanceSeq: 0, pollTimer: null, modal: null, pending: null, settingsDirty: false,
    ledger: [], ledgerNext: null, ledgerTotal: null, ledgerSeq: 0, ledgerLoading: false
  };
  function node(tag, className = "", value) { const element = document.createElement(tag); if (className) element.className = className; if (value !== undefined && value !== null) element.textContent = String(value); return element; }
  function append(parent, ...children) { parent.append(...children.filter(Boolean)); return parent; }
  function replace(id, ...children) { $(id).replaceChildren(...children.filter(Boolean)); }
  function set(id, value) { $(id).textContent = value ?? "—"; }
  function label(value) { return LABELS[value] || text(value) || "Ismeretlen"; }
  function badge(value, title) {
    const kind = ["AVAILABLE", "READY", "WON", "PASS", "VERIFIED", "SUCCEEDED", "DECISION_COMPLETED"].includes(value) ? "success" :
      ["FAILED", "LOST", "DATA_BLOCKED", "CONFLICTING"].includes(value) ? "danger" :
      ["UNKNOWN", "STALE", "PARTIAL", "REVIEW", "DRAFT", "MISSED"].includes(value) ? "warning" : "neutral";
    return node("span", `badge ${kind}`, title || label(value));
  }
  function button(title, handler, className = "secondary small", disabled = false) { const item = node("button", `button ${className}`, title); item.type = "button"; item.disabled = disabled; item.addEventListener("click", handler); return item; }
  function empty(title, detail) { return append(node("div", "empty-state"), node("div", "empty-icon", "A"), node("h3", "", title), node("p", "", detail)); }
  function metric(title, value, detail, className = "") { return append(node("article", "metric"), node("span", "metric-title", title), node("strong", className, value), node("small", "", detail)); }
  function table(headers, rows, caption) {
    const element = node("table"); if (caption) element.append(node("caption", "", caption));
    const heading = node("tr"); headers.forEach(title => { const th = node("th", "", title); th.scope = "col"; heading.append(th); });
    element.append(append(node("thead"), heading)); const body = node("tbody");
    rows.forEach(values => { const row = node("tr"); values.forEach(value => row.append(append(node("td"), value instanceof Node ? value : node("span", "", value ?? "—")))); body.append(row); });
    element.append(body); return element;
  }
  function keyValues(entries) { const list = node("dl", "key-value"); entries.forEach(([key, value]) => list.append(node("dt", "", key), node("dd", "", value ?? "Nem ismert"))); return list; }
  function reasons(values) { return arr(values).map(value => typeof value === "string" ? value : text(value.message || value.reason || value.code)).filter(Boolean).join(" · "); }
  function safeDescription(value) { return typeof value === "string" ? value : text(value?.summary || value?.description || value?.reason || value?.message); }
  function capability(name) { return state.connected && state.workshop?.capabilities?.[name] === true && !state.busy && !busyStates.has(state.workshop?.operation?.state); }
  function ownerStatus(agent) { return agent.final_decision_status === "ABSTAIN" ? "ABSTAINED" : agent.decision_status || "NOT_ATTEMPTED"; }
  function settingsKnown() { const settings = obj(state.workshop?.settings); return count(settings.target_ticket_count) && finite(settings.strictness) && !!text(settings.openai?.model) && typeof settings.openai?.web_search === "boolean"; }
  function ownerName(id) { return arr(state.workshop?.agents).find(agent => agent.agent_id === id)?.name || id || "Agent nem ismert"; }
  function currentRun() { return state.workshop?.run?.run_id || null; }
  let toastTimer;
  function toast(message, danger = false) { set("toast", message); $("toast").className = `toast${danger ? " danger" : ""}`; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, danger ? 12000 : 6500); }
  async function api(path, body, maximumBytes = 2 * 1024 * 1024) {
    if (!path.startsWith("/api/") || path.startsWith("//")) throw new Error("Érvénytelen helyi útvonal.");
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), body === undefined ? 30000 : 60000);
    try {
      const options = { credentials: "same-origin", cache: "no-store", signal: controller.signal, headers: { Accept: "application/json", "X-Arthur-Token": window.ARTHUR_TOKEN || "" } };
      if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
      const response = await fetch(path, options), length = response.headers.get("Content-Length");
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > maximumBytes)) throw new Error("A válasz nagyobb a megjelenítési keretnél. A teljes export külön tölthető le.");
      if (!response.body) throw new Error("Üres szerverválasz.");
      const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true }); let size = 0, source = "";
      try { while (true) { const part = await reader.read(); if (part.done) break; size += part.value.byteLength; if (size > maximumBytes) { await reader.cancel(); throw new Error("A válasz túllépte a megjelenítési keretet; nem csonkítjuk az adatokat."); } source += decoder.decode(part.value, { stream: true }); } source += decoder.decode(); }
      finally { reader.releaseLock(); }
      let data; try { data = JSON.parse(source); } catch { throw new Error("A szerver nem értelmezhető JSON-választ adott."); }
      if (!response.ok) { const error = new Error(text(data.error || data.message) || `A kérés nem sikerült (${response.status}).`); error.status = response.status; error.code = data.code; throw error; }
      return data;
    } catch (error) {
      if (error.name === "AbortError") { const timeoutError = new Error(body === undefined ? "Az állapot lekérdezése túl sokáig tartott." : "A művelet válasza nem érkezett meg. Az állapot ellenőrzése szükséges; nem ismételtük meg a kérést."); timeoutError.mutation_uncertain = body !== undefined; throw timeoutError; }
      if (body !== undefined && (!error.status || error.status >= 500)) error.mutation_uncertain = true;
      throw error;
    }
    finally { clearTimeout(timeout); }
  }
  function rememberPending(value) { state.pending = value; try { if (value) sessionStorage.setItem("arthur.manual-request.v1", JSON.stringify(value)); else sessionStorage.removeItem("arthur.manual-request.v1"); } catch { /* In-memory lock remains; do not invent persistence. */ } }
  function readPending() { try { const value = JSON.parse(sessionStorage.getItem("arthur.manual-request.v1") || "null"); if (value && validDate(value.date) && typeof value.request_id === "string") state.pending = value; } catch { /* Invalid local state is not server evidence. */ } }
  function reconcilePending(data) {
    const pending = state.pending, operation = data.operation;
    if (!pending || !operation || operation.request_id !== pending.request_id || operation.target_date !== pending.date) return;
    if (terminalStates.has(operation.state)) rememberPending(null);
    else if (operation.job_id) rememberPending({ ...pending, job_id: operation.job_id, observed: true });
  }
  async function refresh(forceDetails = false) {
    const sequence = ++state.refreshSeq, day = state.date;
    try {
      const data = await api(`/api/workshop?date=${encodeURIComponent(day)}`, undefined, 1024 * 1024);
      if (sequence !== state.refreshSeq || day !== state.date) return;
      if (data.schema_version !== 1 || data.selected_date !== day || data.timezone !== "Europe/Berlin") throw new Error("Az új műhely adatillesztése nem egyezik a kiválasztott nappal.");
      const previous = currentRun(); state.workshop = data; state.connected = true; reconcilePending(data);
      $("connection-error").hidden = true; $("connection-dot").className = "connection-dot online";
      set("connection-label", busyStates.has(data.operation?.state) ? "A műhely dolgozik" : "Mentett állapot elérhető");
      set("updated-at", `Állapot: ${when(data.server_time)}`); renderWorkshop(); renderAgents(); renderFamilies(); renderOperations();
      if (currentRun() !== previous) { state.cards = []; state.cardsRun = null; state.calendar = []; state.calendarRun = null; state.calendarNext = null; state.calendarLoading = false; state.evidenceLoading = false; ++state.calendarSeq; ++state.evidenceSeq; $("evidence-panel").hidden = true; renderCalendar(); }
      if (currentRun() && (forceDetails || currentRun() !== state.cardsRun)) await loadCards(true);
      else renderCards();
    } catch (error) {
      if (sequence !== state.refreshSeq || day !== state.date) return;
      state.connected = false; $("connection-dot").className = "connection-dot offline"; set("connection-label", "Állapot nem ellenőrizhető");
      set("connection-error", error.status === 404 ? "Az új felület adatillesztése még nem érhető el. Nem indítunk régi generálási vagy adatfrissítési útvonalat." : `Az állapot nem frissült: ${error.message} A korábban betöltött adat nem tekinthető frissnek.`);
      $("connection-error").hidden = false; syncControls();
      if (!state.workshop) { replace("cards", empty("A műhely állapotára várunk", "A mentett adatok betöltése után itt jelennek meg az eredeti szelvények.")); replace("agents", empty("Az agentek névjegyzéke még nem érkezett meg", "Nem pótoljuk kitalált agentekkel vagy korábbi profil-előnézettel.")); }
    }
  }
  function renderWorkshop() {
    const data = state.workshop, run = obj(data.run), capacity = obj(data.capacity), calendar = obj(data.calendar), operation = obj(data.operation);
    replace("run-summary", badge(run.run_id ? run.construction_status || "UNKNOWN" : "NOT_RUN"), badge(run.verification_state || "UNKNOWN", `Ellenőrzés: ${label(run.verification_state || "UNKNOWN")}`));
    set("version-label", text(data.application_version?.display || data.application_version?.version || data.application_version) || "Verzió nem ismert");
    const amount = capacity.generated_ticket_count, filled = capacity.filled_main_slots;
    const companionCount = count(amount) && count(capacity.agent_variant_count) && amount >= capacity.agent_variant_count
      ? amount - capacity.agent_variant_count : null;
    const historical = capacity.state === "HISTORICAL_TEN_OWNER_REPORT" && capacity.target_met === null;
    const legacyTrustworthy = capacity.version === "TWELVE_MAIN_SLOTS_V1" && capacity.main_slot_count === 12 &&
      capacity.maximum_agent_variants === 30 && capacity.maximum_companion_tickets === 2 &&
      [amount, filled, capacity.filled_agent_owner_slots, capacity.filled_companion_slots, capacity.agent_variant_count, companionCount].every(count) &&
      capacity.filled_agent_owner_slots <= 10 && capacity.filled_companion_slots <= 2 &&
      filled === capacity.filled_agent_owner_slots + capacity.filled_companion_slots &&
      capacity.agent_variant_count >= capacity.filled_agent_owner_slots && capacity.agent_variant_count <= capacity.filled_agent_owner_slots * 3 &&
      companionCount === capacity.filled_companion_slots &&
      amount === capacity.agent_variant_count + companionCount && capacity.target_met === (filled === 12);
    const independent = independentPair(), ownerIds = arr(capacity.filled_agent_owner_ids);
    const pairTrustworthy = independent && capacity.main_slot_count === 12 &&
      capacity.maximum_agent_variants === 32 && capacity.maximum_companion_tickets === 0 &&
      [amount, filled, capacity.filled_agent_owner_slots, capacity.filled_core_owner_slots, capacity.filled_pair_owner_slots].every(count) &&
      ownerIds.length === new Set(ownerIds).size && ownerIds.every(id => ALL_AGENT_IDS.includes(id)) &&
      filled === ownerIds.length && filled === capacity.filled_agent_owner_slots &&
      capacity.filled_core_owner_slots === ownerIds.filter(id => AGENTS.includes(id)).length &&
      capacity.filled_pair_owner_slots === ownerIds.filter(id => PAIR_AGENTS.includes(id)).length &&
      capacity.filled_companion_slots === 0 && amount === capacity.agent_variant_count &&
      amount >= filled && amount <= capacity.filled_core_owner_slots * 3 + capacity.filled_pair_owner_slots &&
      arr(capacity.invalid_card_ids).length === 0 && arr(capacity.identity_issues).length === 0 &&
      capacity.target_met === (filled === 12);
    const trustworthy = independent ? pairTrustworthy : legacyTrustworthy;
    set("nav-count", count(amount) ? amount : "—"); set("card-count", count(amount) ? `${amount} mentett` : "számlálásra vár");
    $("minimum-note").className = `notice ${!run.run_id || historical ? "neutral" : trustworthy && filled === 12 ? "" : "warning"}`;
    set("minimum-note", !run.run_id ? "Erre a napra még nincs mentett futás." : historical ? "Történeti tíz-agentes riport. A tizenkét főhely új szerződését nem alkalmazzuk visszamenőleg; a régi kártyák változatlanul megtekinthetők." : !trustworthy ? "A tizenkét főhely lefedettsége hiányos vagy ellentmondásos; a cél teljesülése nem igazolt." : filled < 12 ? `Kitöltött főhelyek: ${filled} / 12 · ${capacity.filled_agent_owner_slots} / ${independent ? 12 : 10} agent${independent ? "" : ` · ${capacity.filled_companion_slots} / 2 külön csatorna`}. ${amount} mentett szelvény; a további saját változatok nem pótolják az üres főhelyeket.` : `Mind a 12 főhelyhez van mentett szelvény · ${amount} szelvény a saját változatokkal. Az ellenőrzés és a kártyák DRAFT/READY állapota külön számít; ez nem profitígéret.`);
    replace("overview", metric("Teljes napi naptár", number(calendar.fixture_count), "Szolgáltató által felsorolt mérkőzések"), metric("Árazott mérkőzések", number(calendar.priced_fixture_count), "A hiányzó ár nem tünteti el a naptár sorát"), metric("Kitöltött főhelyek", trustworthy ? `${filled} / 12` : "—", historical ? "Történeti tíz-agentes szerződés" : `${count(amount) ? amount : "—"} mentett szelvény · ${independent ? "tizenkét önálló agent, legfeljebb 32 saját szelvény" : "legfeljebb 30 agentváltozat és 2 történeti külön csatorna"}`), metric("Befejezett döntések", count(capacity.completed_owner_count) ? `${capacity.completed_owner_count} / ${independent ? 12 : 10}` : "—", `${number(capacity.abstained_owner_count)} tartózkodó · ${number(capacity.failed_owner_count)} hibás`));
    const running = busyStates.has(operation.state); $("run-progress").hidden = !running;
    set("progress-heading", label(operation.state)); set("progress-detail", [text(operation.stage), reasons(run.reason_codes)].filter(Boolean).join(" · ") || "A következő mentett állapotra várunk; nem becsülünk készültségi százalékot.");
    replace("owner-progress", ...arr(data.agents).map(agent => append(node("div"), node("strong", "", agent.name || agent.agent_id), badge(ownerStatus(agent)))));
    const selected = $("card-agent").value; replace("card-agent", option("", "Minden agent"), ...arr(data.agents).map(agent => option(agent.agent_id, agent.name || agent.agent_id))); $("card-agent").value = selected;
    $("export-json").href = `/api/export?date=${encodeURIComponent(state.date)}`; $("export-txt").href = `/api/tickets.txt?date=${encodeURIComponent(state.date)}`;
    set("date-note", state.date < berlinToday() ? "Történeti nap · eredeti mentett adatok" : state.date > berlinToday() ? "Előzetes nap · minden kezdés Berlin ideje szerint" : "Minden kezdés Berlin ideje szerint"); syncControls();
  }
  function option(value, title) { const result = node("option", "", title); result.value = value; return result; }
  function ledgerHistoryAvailable() {
    if (!state.connected) return false;
    const data = state.workshop, flags = data?.capabilities, capacity = data?.capacity;
    if (typeof flags?.ledger_ticket_history === "boolean") return flags.ledger_ticket_history;
    return flags?.ledger_ticket_history === undefined && flags?.financial_recording === true &&
      data?.schema_version === 1 && capacity?.main_slot_count === 12 &&
      ((capacity?.version === "TWELVE_MAIN_SLOTS_V1" && capacity?.maximum_agent_variants === 30 && capacity?.maximum_companion_tickets === 2) ||
       (capacity?.version === "TWELVE_INDEPENDENT_AGENT_SLOTS_V2" && capacity?.maximum_agent_variants === 32 && capacity?.maximum_companion_tickets === 0));
  }
  function syncControls() {
    const pending = state.pending; $("generate").disabled = !capability("manual_generation") || !!pending || state.date < berlinToday();
    $("pending-operation").hidden = !pending;
    if (pending) set("pending-operation", `A ${pending.date} napra küldött indítás ${pending.job_id ? "tartós feladathoz kötött; az állapotát követjük" : "válaszára vagy mentett visszaigazolására várunk"}. Új indítást nem küldünk. Az állapot frissítése nem ismétli meg a műveletet.`);
    $("freeze").disabled = !currentRun() || !capability("financial_recording");
    $("settle").disabled = !capability("result_refresh"); $("settings-save").disabled = !capability("settings_mutation") || !settingsKnown();
    $("ledger-load").disabled = !ledgerHistoryAvailable() || state.ledgerLoading;
    $("schedule-toggle").disabled = !capability("schedule_mutation") || typeof state.workshop?.schedule?.enabled !== "boolean" || !text(state.workshop?.schedule?.revision);
    document.querySelectorAll("[data-explicit-action]").forEach(item => { item.disabled = !capability("explicit_probes"); });
  }
  function pagination(data, offset, runId) {
    if (data.code === "DETAIL_TOO_LARGE") throw new Error("Egy teljes rekord nagyobb a részletkeretnél. A JSON exportban megőrzött adat elérhető; nem csonkítjuk és nem lépünk át rajta.");
    if (data.status === "UNKNOWN") throw new Error(`Nincs igazolt mentett lista: ${text(data.reason) || "az eredeti adat hiányzik"}.`);
    if (data.run_id !== runId || !Array.isArray(data.items) || !count(data.total) || data.offset !== offset || data.items.length + offset > data.total) throw new Error("A mentett lap kötése vagy számlálása hibás.");
    if (data.next_offset !== null && (!count(data.next_offset) || data.next_offset !== offset + data.items.length || data.next_offset <= offset || data.next_offset >= data.total)) throw new Error("A következő lap azonosítója érvénytelen.");
    if (data.next_offset === null && offset + data.items.length !== data.total) throw new Error("A válasz nem számolja el a teljes mentett listát.");
  }
  function hydrateCard(item) {
    if (item.state_projection !== "CANONICAL_TICKET_PLUS_STATE_DELTA_V2") return item;
    if (!item.ticket || !item.state || typeof item.state !== "object" || Array.isArray(item.state)) throw new Error("Érvénytelen szelvényállapot-vetület.");
    return { ...item, state: { ...item.ticket, ...item.state } };
  }
  async function loadCards(reset = false) {
    const runId = currentRun(); if (!runId || (!reset && state.cardsLoading)) return;
    const sequence = ++state.cardsSeq, epoch = state.epoch, offset = reset ? 0 : state.cardsNext;
    if (offset === null) return; state.cardsLoading = true; $("cards-more").disabled = true;
    if (reset) replace("cards", empty("Szelvények betöltése", "Csak a kiválasztott futás kanonikus, mentett kártyáit olvassuk."));
    try {
      const data = await api(`/api/cards?run_id=${encodeURIComponent(runId)}&offset=${offset}&limit=30`);
      if (sequence !== state.cardsSeq || epoch !== state.epoch || currentRun() !== runId) return;
      pagination(data, offset, runId);
      if (count(state.workshop.capacity?.generated_ticket_count) && data.total !== state.workshop.capacity.generated_ticket_count) throw new Error("A kanonikus kártyalista és a napi számlálás eltér.");
      if (state.workshop.run.report_sha256 && data.report_sha256 !== state.workshop.run.report_sha256) throw new Error("A kártyák másik riporthoz tartoznak; frissítsd az állapotot.");
      const incoming = data.items.map(item => { const ticket = item.ticket; if (!ticket || typeof ticket.ticket_id !== "string" || (ticket.date !== undefined && ticket.date !== state.date) || (ticket.run_id !== undefined && ticket.run_id !== runId) || (ticket.agent_product_binding && ticket.agent_product_binding.run_id !== runId)) throw new Error("Hiányos vagy ellentmondásos kanonikus kártyaazonosság."); return hydrateCard(item); });
      const combined = reset ? incoming : [...state.cards, ...incoming];
      if (new Set(combined.map(item => item.ticket.ticket_id)).size !== combined.length) throw new Error("Ismétlődő szelvényazonosító érkezett; nem vonjuk össze a változatokat.");
      state.cards = combined; state.cardsRun = runId; state.cardsNext = data.next_offset; state.cardsTotal = data.total; renderCards();
    } catch (error) { if (sequence === state.cardsSeq && epoch === state.epoch) { if (reset) { state.cards = []; state.cardsRun = null; state.cardsNext = null; replace("cards", empty("A szelvények nem tölthetők be", error.message)); } else toast(error.message, true); } }
    finally { if (sequence === state.cardsSeq) { state.cardsLoading = false; $("cards-more").disabled = false; } }
  }
  function kickoffOf(ticket) { const times = arr(ticket.legs).map(leg => Date.parse(leg.kickoff_utc || leg.kickoff)).filter(Number.isFinite); return times.length ? Math.min(...times) : Infinity; }
  function fixtureName(leg) { return `${text(leg.home_team || leg.home?.name || leg.home) || "Hazai csapat nem ismert"} – ${text(leg.away_team || leg.away?.name || leg.away) || "Vendégcsapat nem ismert"}`; }
  function marketText(leg) { return [text(leg.period || leg.period_key), text(leg.market_label || leg.market_key || leg.market), text(leg.selection_label || leg.selection_key || leg.selection || leg.outcome), leg.line !== undefined && leg.line !== null ? text(leg.line) : ""].filter(Boolean).join(" · ") || "Piac vagy választás nem ismert"; }
  function renderCards() {
    if (!currentRun()) { replace("cards", empty("Erre a napra még nincs mentett szelvény", "Az előállítás a teljes napi kínálatból indul. A felület nem készít profil-előnézetből szelvényt.")); $("cards-more").hidden = true; renderCompanions([]); return; }
    if (state.cardsRun !== currentRun()) return;
    const search = $("card-search").value.trim().toLocaleLowerCase("hu-HU"), owner = $("card-agent").value, status = $("card-status").value, odds = $("card-odds").value;
    let rows = state.cards.filter(item => {
      const ticket = item.ticket, ticketState = obj(item.state), price = ticket.combined_price;
      const haystack = [ownerName(ticket.profile_id), ...arr(ticket.legs).map(leg => `${fixtureName(leg)} ${marketText(leg)}`)].join(" ").toLocaleLowerCase("hu-HU");
      return (!search || haystack.includes(search)) && (!owner || ticket.profile_id === owner) && (!status || (status === "PLAYED" ? ticketState.played === true : [ticket.status, ticket.publication_scope].includes(status))) &&
        (!odds || (odds === "15-20" ? finite(price) && price >= 15 && price <= 20 : odds === "10-15" ? finite(price) && price >= 10 && price < 15 : odds === "20-plus" ? finite(price) && price > 20 : !finite(price) || price < 10));
    });
    const ordering = $("card-sort").value;
    if (ordering !== "original") rows = [...rows].sort((a, b) => ordering === "owner" ? ownerName(a.ticket.profile_id).localeCompare(ownerName(b.ticket.profile_id), "hu") : ordering === "kickoff" ? kickoffOf(a.ticket) - kickoffOf(b.ticket) : (finite(a.ticket.combined_price) ? a.ticket.combined_price : Infinity) - (finite(b.ticket.combined_price) ? b.ticket.combined_price : Infinity));
    set("card-filter-note", `${rows.filter(item => visibleAgentIds().includes(item.ticket.profile_id)).length} agentváltozat felel meg a szűrőnek · ${state.cards.length} összes betöltött / ${number(state.cardsTotal)} mentett. ${independentPair() ? "A szűrő mind a tizenkét önálló agent mentett kártyáira érvényes." : "A szűrő csak az agentkártyák megjelenítését változtatja; a Daily és legacy külön alább szerepel."}`);
    const ownerIndices = new Map(); state.cards.forEach(item => { const index = (ownerIndices.get(item.ticket.profile_id) || 0) + 1; ownerIndices.set(item.ticket.profile_id, index); item.displayIndex = index; });
    const agents = arr(state.workshop?.agents), groups = [];
    visibleAgentIds().filter(id => !owner || id === owner).forEach(id => {
      const agent = agents.find(value => value.agent_id === id), own = rows.filter(item => item.ticket.profile_id === id), allOwn = state.cards.filter(item => item.ticket.profile_id === id), group = node("section", "owner-ticket-group");
      group.append(node("p", "eyebrow", `${visibleAgentIds().indexOf(id) + 1}. ${agent?.name || id}`));
      if (own.length) {
        const primary = allOwn.find(item => own.includes(item)), variants = own.filter(item => item !== primary);
        group.append(ticketCard(primary));
        if (variants.length) { const more = append(node("details", "owner-variants"), node("summary", "", `További ${variants.length} saját változat`)); variants.forEach(item => more.append(ticketCard(item))); group.append(more); }
      } else {
        const title = allOwn.length ? "Nincs kártya ezzel a megjelenítési szűrővel" : state.cardsNext !== null ? "A további mentett lap még nincs betöltve" : "Nincs mentett szelvény ettől az agenttől";
        const detail = allOwn.length ? `${allOwn.length} eredeti változat betöltve; a szűrő elrejti, nem törli őket.` : state.cardsNext !== null ? "Az üres hely nem bizonyít tartózkodást; töltsd be a hátralévő kártyákat." : `${label(agent ? ownerStatus(agent) : "UNKNOWN")}. ${reasons(agent?.reason_codes) || "Nem pótoljuk másik owner vagy régi nap szelvényével."}`;
        group.append(empty(title, detail));
      }
      groups.push(group);
    }); replace("cards", ...groups); renderCompanions(state.cards);
    $("cards-more").hidden = state.cardsNext === null;
  }
  function renderCompanions(cards) {
    $("companion-section").hidden = independentPair();
    if (independentPair()) {
      replace("companion-cards");
      const unmapped = cards.filter(item => !ALL_AGENT_IDS.includes(item.ticket.profile_id));
      $("unmapped-cards-section").hidden = unmapped.length === 0;
      replace("unmapped-cards", ...unmapped.map(ticketCard));
      return;
    }
    const channels = arr(state.workshop?.companion_channels), assigned = new Set(), actual = new Map(cards.map(item => [item.ticket.ticket_id, item]));
    const allIds = channels.filter(channel => ["DAILY", "LEGACY"].includes(channel.role)).flatMap(channel => arr(channel.ticket_ids));
    const groups = [["DAILY", "11. Daily"], ["LEGACY", "12. Legacy"]].map(([role, title]) => {
      const matches = channels.filter(channel => channel.role === role), channel = matches.length === 1 ? matches[0] : null, ids = arr(channel?.ticket_ids), group = node("section", "owner-ticket-group");
      group.append(node("p", "eyebrow", title));
      const bound = channel && Array.isArray(channel.ticket_ids) && channel.run_id === currentRun() && new Set(ids).size === ids.length && ids.every(id => typeof id === "string" && allIds.filter(value => value === id).length === 1 && !assigned.has(id) && (!actual.has(id) || !AGENTS.includes(actual.get(id).ticket.profile_id)));
      const own = bound ? ids.map(id => actual.get(id)).filter(Boolean) : [];
      if (bound) ids.forEach(id => assigned.add(id));
      if (own.length) { group.append(ticketCard(own[0])); if (own.length > 1) { const more = append(node("details", "owner-variants"), node("summary", "", `További ${own.length - 1} mentett változat`)); own.slice(1).forEach(item => more.append(ticketCard(item))); group.append(more); } }
      else group.append(empty("Nincs itt igazolt, aktuális szelvény", bound ? ids.length && state.cardsNext !== null ? "A csatornához tartozó mentett kártya egy további oldalon van; töltsd be a hátralévő lapot." : text(channel.reason) || `Mentett csatornaállapot: ${label(channel.status || "UNKNOWN")}.` : "Az új közös előállítás csatornakötése még nincs igazolva. Nem nevezünk át régi riportot mai kártyává."));
      return group;
    }); replace("companion-cards", ...groups);
    const unmapped = cards.filter(item => !AGENTS.includes(item.ticket.profile_id) && !assigned.has(item.ticket.ticket_id));
    $("unmapped-cards-section").hidden = unmapped.length === 0; replace("unmapped-cards", ...unmapped.map(ticketCard));
  }
  function ticketCard(item) {
    const ticket = item.ticket, saved = obj(item.state), card = node("article", "ticket-card"), name = ownerName(ticket.profile_id);
    const heading = append(node("div", "ticket-heading"), node("h3", "", ticket.profile_name || name), node("p", "", `${ticket.variant_id ? text(ticket.variant_id) : `${item.displayIndex}. megjelenített változat`} · ${arr(ticket.legs).length} választás`));
    const badges = append(node("div", "ticket-badges"), badge(ticket.status || "UNKNOWN"), badge(ticket.publication_scope || "UNKNOWN")); if (saved.played === true) badges.append(badge("PENDING", "Megjátszva"));
    card.append(append(node("header", "ticket-header"), node("div", "owner-monogram", (ticket.profile_name || name).slice(0, 1)), heading, badges));
    const legs = node("div", "ticket-legs");
    arr(ticket.legs).forEach((leg, index) => {
      const details = append(node("div"), node("div", "leg-fixture", fixtureName(leg)), node("div", "leg-choice", marketText(leg)), node("div", "leg-clock", when(leg.kickoff_utc || leg.kickoff)));
      details.append(node("div", leg.quoted_at ? "leg-clock" : "quote-unknown", leg.quoted_at ? `Ár ideje: ${when(leg.quoted_at)}` : "A szolgáltatói árazás ideje ismeretlen"));
      legs.append(append(node("div", "ticket-leg"), node("span", "leg-number", index + 1), details, node("strong", "leg-price", number(leg.price ?? leg.odds ?? leg.decimal_price))));
    }); card.append(legs, decisionNotes(item.agent_decision || ticket.agent_decision));
    const warnings = [...arr(ticket.quality_warnings), ...arr(ticket.risk_notes)].map(safeDescription).filter(Boolean);
    if (warnings.length) card.append(append(node("details", "ticket-notes"), node("summary", "", "Fontos hiányok és kockázatok"), ...warnings.map(warning => node("p", "", warning))));
    const actions = node("div", "ticket-actions");
    if (saved.played === true) {
      actions.append(badge(saved.outcome || "UNKNOWN"), button("Irodai korrekció", () => openSettlement(item), "secondary small", !capability("financial_recording")));
    } else actions.append(button("Megjátszottam · naplózás", () => openWager(item), "secondary small", !canWager(item)));
    card.append(append(node("footer", "ticket-footer"), append(node("div", "ticket-price"), node("small", "", "Eredeti összszorzó"), node("strong", "", number(ticket.combined_price))), actions));
    const fixtureIds = arr(ticket.legs).map(leg => leg.fixture_id), knownFixtureIds = fixtureIds.every(id => typeof id === "string" && id.length > 0), distinctFixtures = knownFixtureIds ? new Set(fixtureIds).size : null;
    const currentOddsMet = finite(ticket.combined_price) && ticket.combined_price >= 10;
    card.append(node("p", currentOddsMet && distinctFixtures !== null && distinctFixtures <= 6 && distinctFixtures === fixtureIds.length ? "ticket-footnote" : "quote-unknown", `Mai összeállítási szabály: minimum 10×, előnyben 15–20×; 20× felett is elfogadható. Legfeljebb 6 külön mérkőzés. Ezen a mentett kártyán: ${distinctFixtures === null ? "a meccsszám nem igazolható" : `${distinctFixtures} külön mérkőzés / ${fixtureIds.length} választás`}. A korábbi kártya eredeti értékei változatlanok.`));
    card.append(node("p", "ticket-footnote", saved.played === true ? "Kézzel rögzített fogadás; az eredmény és az irodai pénzmozgás külön adat." : "A mentett összeállítás nem automatikus fogadás. READY sem jelent profitgaranciát.")); return card;
  }
  function decisionNotes(raw) {
    const decision = obj(raw), details = append(node("details", "ticket-notes"), node("summary", "", "Saját döntési út és szimuláció"));
    details.append(node("p", "", text(decision.option_reason) || "Ehhez a változathoz nincs mentett saját indoklás."));
    if (text(decision.reason)) details.append(node("p", "", `Végső mérlegelés: ${decision.reason}`));
    [["candidate_scenarios", "Aktuális forgatókönyvek"], ["historical_strategy_evaluation", "Történeti szimuláció"]].forEach(([role, title]) => {
      const receipt = arr(decision.tool_receipts).find(value => value?.role === role), box = node("div", "tool-note");
      box.append(node("strong", "", title), badge(receipt?.status || "UNKNOWN"), node("p", "", text(receipt?.interpretation) || "Nincs mentett értelmezés ehhez a döntéshez."));
      if (!["AVAILABLE", "CACHED"].includes(receipt?.status)) box.append(node("p", "muted", "A hiány nem nulla kockázat vagy semleges eredmény.")); details.append(box);
    }); details.append(node("p", "muted", "A történeti eredményszimuláció nem igazolt korabeli árú stratégiai profit. A hivatkozás önmagában nem minden nyers sor tényleges használatának bizonyítéka.")); return details;
  }
  function renderAgents() {
    const agents = arr(state.workshop?.agents);
    if (agents.length !== visibleAgentIds().length || new Set(agents.map(agent => agent.agent_id)).size !== visibleAgentIds().length || agents.some(agent => !visibleAgentIds().includes(agent.agent_id))) { replace("agents", empty("A mentett agentnévjegyzék nem ellenőrizhető", "A névjegyzék hiányos vagy ellentmondásos. A történeti profilokat nem keverjük az aktív agentek közé.")); return; }
    replace("agents", ...agents.map(agent => {
      const card = node("article", "agent-card"); card.append(append(node("header"), node("div", "owner-monogram", (agent.name || agent.agent_id).slice(0, 1)), append(node("div"), node("h3", "", agent.name || agent.agent_id), node("p", "", `Legfeljebb ${number(agent.max_variants)} saját változat`)), badge(ownerStatus(agent))));
      card.append(node("p", "agent-personality", text(agent.personality) || "A személyiségleírás nem érkezett meg."));
      const statistics = node("dl", "agent-stats"); [["Javasolt", agent.proposed_count], ["Elfogadott", agent.admitted_count], ["Publikált", agent.published_count]].forEach(([title, value]) => statistics.append(append(node("div"), node("dt", "", title), node("dd", "", number(value))))); card.append(statistics);
      const focus = node("div", "agent-focus"); arr(agent.focus_families).forEach(id => focus.append(badge("NEUTRAL", FAMILIES[id] || id))); card.append(focus);
      const steps = node("ol", "agent-steps"); arr(agent.selection_path).forEach(step => steps.append(node("li", "", text(step)))); card.append(append(node("details"), node("summary", "small", "A saját választási út"), steps));
      if (arr(agent.reason_codes).length) card.append(node("p", "small muted", reasons(agent.reason_codes)));
      card.append(append(node("footer", "agent-footer"), node("span", "muted", `Csapatállapot: ${label(agent.team_status || "UNKNOWN")}`), button(PAIR_AGENTS.includes(agent.agent_id) ? "Kézi csapatkapcsoló még nem elérhető" : "Állapot módosítása", () => openTeamStatus(agent), "secondary small", !capability("team_status") || PAIR_AGENTS.includes(agent.agent_id)))); return card;
    }));
  }
  function families() { const raw = state.workshop?.families; return Array.isArray(raw) ? raw : Object.entries(obj(raw)).map(([id, value]) => ({ ...obj(value), family_id: id })); }
  function coverageText(value) { if (typeof value === "string") return value; if (!value || typeof value !== "object") return "Lefedettség nem ismert"; const result = Object.entries(value).filter(([, v]) => typeof v === "string" || finite(v) || typeof v === "boolean").map(([key, v]) => `${key}: ${typeof v === "boolean" ? v ? "igen" : "nem" : v}`); return result.join(" · ") || "Részletes, mentett lefedettség"; }
  function renderFamilies() {
    const available = families(), byId = new Map(available.map(row => [row.family_id, row])); set("family-count", `${available.filter(row => row.status === "AVAILABLE").length} elérhető / 12`);
    replace("families", ...Object.entries(FAMILIES).map(([id, title]) => {
      const family = byId.get(id), card = node("article", "family-card"); card.append(node("h3", "", title), badge(family?.status || "UNKNOWN"), node("p", "", text(family?.reason) || coverageText(family?.coverage)));
      card.append(node("p", "", `Megfigyelés: ${when(family?.observed_at)}`));
      if (family?.published_at) card.append(node("p", "", `Forrásközlés: ${when(family.published_at)}`));
      card.append(button("Mentett bizonyíték", () => loadEvidence(id, true), "secondary small", !currentRun())); return card;
    }));
  }
  async function loadCalendar(reset = false) {
    const runId = currentRun(); if (!runId || state.calendarLoading) return; const offset = reset ? 0 : state.calendarNext; if (offset === null) return;
    const sequence = ++state.calendarSeq, epoch = state.epoch; state.calendarLoading = true; $("calendar-load").disabled = true; $("calendar-more").disabled = true;
    try { const data = await api(`/api/calendar?run_id=${encodeURIComponent(runId)}&offset=${offset}&limit=100`); if (sequence !== state.calendarSeq || epoch !== state.epoch || currentRun() !== runId) return; pagination(data, offset, runId);
      if (state.workshop.calendar?.snapshot_id && data.snapshot_id !== state.workshop.calendar.snapshot_id) throw new Error("A naptárlap másik adatpillanathoz tartozik.");
      if (count(state.workshop.calendar?.fixture_count) && data.total !== state.workshop.calendar.fixture_count) throw new Error("A naptárlap és a teljes napi meccsszám eltér.");
      const combined = reset ? data.items : [...state.calendar, ...data.items]; if (new Set(combined.map(item => item.fixture_id)).size !== combined.length || combined.some(item => typeof item.fixture_id !== "string" && typeof item.fixture_id !== "number")) throw new Error("Hiányos vagy ismétlődő naptári azonosító.");
      state.calendar = combined; state.calendarNext = data.next_offset; state.calendarTotal = data.total; state.calendarRun = runId; renderCalendar();
    } catch (error) { if (sequence === state.calendarSeq && epoch === state.epoch) { set("calendar-note", `A naptár nem frissült: ${error.message}`); if (reset) replace("calendar", empty("Nincs betöltött naptár", "Nem kérünk új adatot a szolgáltatótól a hiány pótlására.")); } }
    finally { if (sequence === state.calendarSeq) { state.calendarLoading = false; $("calendar-load").disabled = !currentRun(); $("calendar-more").disabled = false; } }
  }
  function renderCalendar() {
    const term = $("calendar-search").value.trim().toLocaleLowerCase("hu-HU"), rows = state.calendar.filter(row => `${fixtureName(row)} ${text(row.competition_name || row.competition?.name)} ${text(row.eligibility_status)} ${reasons(row.reason_codes)}`.toLocaleLowerCase("hu-HU").includes(term));
    set("calendar-note", state.calendarRun === currentRun() && currentRun() ? `${state.calendar.length} / ${number(state.calendarTotal)} mentett mérkőzés betöltve; ${rows.length} felel meg a megjelenítési keresésnek. Az agentek teljes napi univerzuma nem változik.` : "A mentett teljes naptár külön tölthető be; az oldal megnyitása nem gyűjt új sportadatot.");
    replace("calendar", ...(rows.length ? [table(["Mérkőzés", "Kezdés · Berlin", "Sorozat", "Ár / alkalmasság", "Indok"], rows.map(row => [fixtureName(row), when(row.kickoff_utc || row.kickoff), text(row.competition_name || row.competition?.name) || "Ismeretlen", `${label(row.price_status || "UNKNOWN")} / ${label(row.eligibility_status || "UNKNOWN")}`, reasons(row.reason_codes) || "Nincs mentett indok"]))] : [node("p", "small muted", state.calendar.length ? "Nincs találat a betöltött sorok között." : "Még nincs betöltött naptárlap.")]));
    $("calendar-more").hidden = state.calendarNext === null; $("calendar-load").disabled = !currentRun();
  }
  async function loadEvidence(familyId, reset = false) {
    if (!Object.hasOwn(FAMILIES, familyId) || !currentRun() || state.evidenceLoading) return;
    const runId = currentRun(), sequence = ++state.evidenceSeq, epoch = state.epoch, offset = reset ? 0 : state.evidenceNext; if (offset === null) return;
    state.evidenceLoading = true; $("evidence-panel").hidden = false; set("evidence-title", FAMILIES[familyId]); set("evidence-state", "Mentett bizonyíték olvasása…"); $("evidence-more").disabled = true;
    try { const data = await api(`/api/evidence?run_id=${encodeURIComponent(runId)}&family=${encodeURIComponent(familyId)}&offset=${offset}&limit=25`); if (sequence !== state.evidenceSeq || epoch !== state.epoch || currentRun() !== runId) return; pagination(data, offset, runId); if (data.family_id !== familyId) throw new Error("Másik adatcsalád részlete érkezett.");
      state.evidence = reset ? data.items : [...state.evidence, ...data.items]; state.evidenceFamily = familyId; state.evidenceNext = data.next_offset;
      set("evidence-state", `${state.evidence.length} / ${data.total} mentett részlet. A hivatkozás nem önmagában oksági felhasználási bizonyíték.`);
      replace("evidence-rows", ...(state.evidence.length ? state.evidence.map(row => {
        const record = node("article", "evidence-row"); record.append(badge(row.status || "UNKNOWN"), node("p", "", text(row.summary || row.reason || row.description) || "Mentett részlet"));
        if (row.observed_at) record.append(node("p", "muted", `Megfigyelés: ${when(row.observed_at)}`));
        if (row.detail_omitted) record.append(node("p", "quote-unknown", "Nagy mező nem része ennek a lapnak; a teljes exportban megőrzött adat elérhető."));
        record.append(append(node("details"), node("summary", "", "Mentett mezők"), node("pre", "", JSON.stringify(row, null, 2)))); return record;
      }) : [node("p", "muted", "Ehhez a családhoz nincs mentett részlet; ez UNKNOWN, nem nulla hatás.")])); $("evidence-more").hidden = state.evidenceNext === null;
    } catch (error) { if (sequence === state.evidenceSeq && epoch === state.epoch) { set("evidence-state", error.message); if (reset) replace("evidence-rows"); } }
    finally { if (sequence === state.evidenceSeq) { state.evidenceLoading = false; $("evidence-more").disabled = false; } }
  }
  async function loadPerformance() {
    const mode = $("performance-mode").value, month = $("performance-month").value;
    if (!["actual", "paper", "freezes"].includes(mode) || !/^\d{4}-\d{2}$/.test(month) || !validDate(`${month}-01`)) return;
    const sequence = ++state.performanceSeq; set("performance-state", "Mentett eredmények betöltése…");
    const path = mode === "paper" ? `/api/paper-performance?month=${encodeURIComponent(month)}` : `/api/performance?month=${encodeURIComponent(month)}&mode=${encodeURIComponent(mode)}`;
    try {
      const data = await api(path, undefined, 16 * 1024 * 1024);
      if (sequence !== state.performanceSeq || mode !== $("performance-mode").value || month !== $("performance-month").value) return;
      if (data.month !== month || data.mode !== (mode === "paper" ? "HYPOTHETICAL_EUR1_ORIGINAL_QUOTES" : mode)) throw new Error("Másik időszak vagy pénzügyi nézet adatai érkeztek.");
      state.performance = data; state.performanceMode = mode; set("performance-state", `${month} · mentett eredmények. Ez a lekérdezés nem frissít mérkőzéseredményt.`); renderPerformance();
    } catch (error) { if (sequence === state.performanceSeq) { state.performance = null; state.performanceMode = null; set("performance-state", `Az eredmények nem tölthetők be: ${error.message}`); replace("performance-metrics"); replace("profit-chart", empty("Nincs ellenőrizhető adatsor", "Nem jelenítünk meg kitalált nullás profitgörbét.")); replace("chart-table"); replace("profile-performance"); replace("outcomes"); } }
  }
  async function loadLedger(reset = false) {
    if (!ledgerHistoryAvailable() || state.ledgerLoading) return;
    const day = state.date, sequence = ++state.ledgerSeq, epoch = state.epoch, offset = reset ? 0 : state.ledgerNext; if (offset === null) return;
    state.ledgerLoading = true; syncControls(); $("ledger-more").disabled = true;
    try {
      const data = await api(`/api/ledger-tickets?date=${encodeURIComponent(day)}&offset=${offset}&limit=10`);
      if (sequence !== state.ledgerSeq || epoch !== state.epoch || day !== state.date) return;
      if (data.date !== day || data.monetary_contract !== "CURRENT_HASH_V2") throw new Error("Másik nap vagy eltérő pénzügyi szerződés érkezett.");
      pagination({ ...data, run_id: "LEDGER_PAGE" }, offset, "LEDGER_PAGE");
      const combined = reset ? data.items : [...state.ledger, ...data.items];
      if (combined.some(ticket => typeof ticket.ticket_id !== "string") || new Set(combined.map(ticket => ticket.ticket_id)).size !== combined.length) throw new Error("Ismétlődő vagy hiányos könyvelési kártyaazonosság.");
      state.ledger = combined; state.ledgerNext = data.next_offset; state.ledgerTotal = data.total;
      set("ledger-note", `${day} · ${combined.length} / ${data.total} eredeti könyvelési szelvény. Korábbi futás vagy történeti profil is szerepelhet; ez nem új generált darabszám.`);
      replace("ledger-cards", ...(combined.length ? combined.map((ticket, index) => ticketCard({ ticket, state: ticket, agent_decision: ticket.agent_decision, displayIndex: index + 1 })) : [empty("Nincs könyvelési sor erre a napra", "Ez az eredeti napló állapota; más nap tételeit nem másoljuk ide.")]));
      $("ledger-more").hidden = data.next_offset === null;
    } catch (error) { if (sequence === state.ledgerSeq && epoch === state.epoch) set("ledger-note", `A könyvelési napló nem frissült: ${error.message}`); }
    finally { if (sequence === state.ledgerSeq) { state.ledgerLoading = false; $("ledger-more").disabled = false; syncControls(); } }
  }
  function winRate(row) { if (finite(row.win_rate)) return row.win_rate; return count(row.won) && count(row.lost) && row.won + row.lost > 0 ? row.won / (row.won + row.lost) : null; }
  function renderPerformance() {
    const data = state.performance; if (!data) return; const summary = obj(data.summary), paper = state.performanceMode === "paper", freeze = state.performanceMode === "freezes";
    const net = paper ? summary.full_all_record_realized_net_eur : summary.net_eur, stake = paper ? summary.total_stake_eur : summary.stake_eur, rate = winRate(summary);
    set("performance-basis", paper ? "PAPER · hipotetikus 1 EUR, igazolt eredeti ajánlati árakon. Nem valódi fogadás vagy pénzmozgás. A publikáció napjára csoportosított mai eredmény nem korabeli bankroll-görbe." : freeze ? "Lezárt napi csomagok saját mentett kohorsza. Nem helyettesíti a tényleges megjátszások és pénzmozgások naplóját." : "Ténylegesen naplózott megjátszások · CURRENT_HASH_V2. A lezárt sporteredmény, a függő tét és az irodai pénzjóváírás külön adat; szolgáltatási költség nincs automatikusan levonva.");
    replace("performance-metrics", metric(paper ? "Teljes papíreredmény" : "Lezárt szelvényeredmény", money(net), paper && summary.complete_result_available !== true ? "Hiányos lefedettség · a teljes összeg nem ismert" : "A szolgáltatási költségektől elkülönítve", finite(net) ? net > 0 ? "positive" : net < 0 ? "negative" : "" : "word"), metric(paper ? "Hipotetikus tét" : "Naplózott tét", money(stake), `${money(summary.open_stake_eur)} nyitott tét`), metric("Találati arány", finite(rate) ? `${number(rate * 100)}%` : "Nincs adat", `${number(summary.won)} nyert / ${number(summary.lost)} vesztett`, finite(rate) ? "" : "word"), metric("Hozam a lezárt téten", finite(paper ? summary.roi_pct : summary.yield_pct) ? `${number(paper ? summary.roi_pct : summary.yield_pct)}%` : "Nincs adat", "VOID, függő és hiányos eredmény külön", "word"));
    replace("outcomes", ...[["Nyert", "won", summary.won], ["Vesztett", "lost", summary.lost], ["Függő", "pending", paper ? summary.pending_records : summary.pending], ["Ellenőrzésre vár", "review", paper ? summary.review_records : summary.review], ["Érvénytelen", "void", summary.void]].map(([title, style, amount]) => append(node("div", "outcome-row"), node("i", `outcome-dot ${style}`), node("span", "", title), node("strong", "", number(amount)))));
    renderChart(); renderProfiles(); renderCosts();
  }
  function svgNode(tag, attributes = {}, value) { const element = document.createElementNS("http://www.w3.org/2000/svg", tag); Object.entries(attributes).forEach(([key, v]) => element.setAttribute(key, String(v))); if (value !== undefined) element.textContent = String(value); return element; }
  function renderChart() {
    const data = state.performance; if (!data) return; const paper = state.performanceMode === "paper", metricName = $("chart-metric").value, source = arr(paper ? data.by_day : data.series); let paperGap = false;
    const points = source.map(row => {
      if (paper && row.complete_result_available !== true) paperGap = true;
      const value = metricName === "win_rate" ? winRate(row) : paper ? paperGap ? null : row.cumulative_realized_net_eur : row.cumulative_net_eur;
      return { date: row.date, value: finite(value) ? metricName === "win_rate" ? value * 100 : value : null, won: row.won, lost: row.lost };
    });
    const title = metricName === "win_rate" ? "Napi találati arány" : paper ? "Kumulált papíreredmény" : state.performanceMode === "freezes" ? "Kumulált csomageredmény" : "Kumulált lezárt eredmény";
    set("chart-title", title); const describe = point => `${point.date || "Dátum nem ismert"}: ${point.value === null ? "ismeretlen, nincs összekötve a következő ponttal" : metricName === "win_rate" ? `${number(point.value)}% · ${number(point.won)} nyert, ${number(point.lost)} vesztett` : money(point.value)}`;
    replace("chart-table", ...(points.length ? [table(["Nap", metricName === "win_rate" ? "Találati arány" : "Kumulált eredmény", "Nyert", "Vesztett"], points.map(point => [point.date || "Ismeretlen", point.value === null ? "Ismeretlen" : metricName === "win_rate" ? `${number(point.value)}%` : money(point.value), number(point.won), number(point.lost)]), "Ugyanazok az adatok, mint a grafikonon; az ismeretlen pont nincs nullával pótolva.")] : []));
    const known = points.filter(point => point.value !== null);
    if (!known.length) { replace("profit-chart", empty("Még nincs ábrázolható eredmény", "A hiányzó vagy hiányos pénzügyi adat nem nullás profit.")); set("chart-point", "Nincs ismert pont a kiválasztott időszakban."); return; }
    const width = 680, height = 260, margin = { left: 62, right: 18, top: 20, bottom: 40 }, values = known.map(point => point.value);
    let minimum = Math.min(0, ...values), maximum = Math.max(metricName === "win_rate" ? 100 : 0, ...values); if (minimum === maximum) maximum = minimum + 1;
    const x = index => margin.left + (width - margin.left - margin.right) * (points.length === 1 ? 0.5 : index / (points.length - 1)), y = value => margin.top + (height - margin.top - margin.bottom) * (maximum - value) / (maximum - minimum);
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${title}. ${known.length} ismert pont; részletes adatok a táblázatban.` });
    for (let index = 0; index <= 4; index += 1) { const value = minimum + (maximum - minimum) * index / 4; svg.append(svgNode("line", { x1: margin.left, x2: width - margin.right, y1: y(value), y2: y(value), class: value === 0 ? "chart-grid-line chart-zero" : "chart-grid-line" }), svgNode("text", { x: margin.left - 9, y: y(value) + 4, "text-anchor": "end", class: "chart-axis" }, `${number(value)}${metricName === "win_rate" ? "%" : ""}`)); }
    let path = "", connected = false;
    points.forEach((point, index) => { if (point.value === null) { connected = false; return; } path += `${connected ? "L" : "M"}${x(index)} ${y(point.value)} `; connected = true; });
    svg.append(svgNode("path", { d: path.trim(), class: "chart-line" }));
    points.forEach((point, index) => {
      if (point.value !== null) {
        const circle = svgNode("circle", { cx: x(index), cy: y(point.value), r: 4.5, tabindex: 0, role: "button", class: "chart-point", "aria-label": describe(point) });
        const show = () => set("chart-point", describe(point)); circle.addEventListener("focus", show); circle.addEventListener("pointerenter", show); circle.addEventListener("click", show);
        circle.addEventListener("keydown", event => { const direction = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0; if (direction) { event.preventDefault(); const circles = [...svg.querySelectorAll("circle")], at = circles.indexOf(circle); circles[(at + direction + circles.length) % circles.length]?.focus(); } if (["Enter", " "].includes(event.key)) { event.preventDefault(); show(); } });
        circle.append(svgNode("title", {}, describe(point))); svg.append(circle);
      }
      if (index === 0 || index === points.length - 1 || (points.length > 4 && index === Math.floor(points.length / 2))) svg.append(svgNode("text", { x: x(index), y: height - 13, "text-anchor": "middle", class: "chart-axis" }, text(point.date).slice(5)));
    }); replace("profit-chart", svg); set("chart-point", "Mutass egy pontra, vagy lépj Tab és nyílbillentyűkkel. Az ismeretlen szakasz megszakítja a görbét.");
  }
  function renderProfiles() {
    if (!state.performance) return; const mode = $("profile-scope").value, paper = state.performanceMode === "paper";
    const profiles = arr(state.performance.by_profile).filter(profile => mode === "all" || (mode === "active" ? AGENTS.includes(profile.profile_id) : !ALL_AGENT_IDS.includes(profile.profile_id)));
    replace("profile-performance", ...(profiles.length ? [table(["Agent / eredeti profil", "Szelvény", "Nyert / vesztett", "Lezárt eredmény", "Hozam"], profiles.map(profile => {
      const title = append(node("span"), node("span", "", profile.profile_name || ownerName(profile.profile_id)), !ALL_AGENT_IDS.includes(profile.profile_id) ? append(node("small", "muted"), node("span", "", " · történeti profil")) : null);
      return [title, number(paper ? profile.records : profile.tickets), `${number(profile.won)} / ${number(profile.lost)}`, money(paper ? profile.full_all_record_realized_net_eur : profile.net_eur), finite(paper ? profile.roi_pct : profile.yield_pct) ? `${number(paper ? profile.roi_pct : profile.yield_pct)}%` : "Nem ismert"];
    }))] : [empty("Nincs eredmény ebben a csoportban", "A régi azonosítók saját történeti profiljukkal maradnak meg; nem alakítjuk őket a mai tíz agentté.")]));
  }
  function renderCosts() {
    const usage = obj(state.workshop?.usage), coverage = obj(usage.usage_coverage), knownCosts = obj(usage.known_costs_by_currency || usage.cost_by_currency), entries = Object.entries(knownCosts).filter(([, value]) => finite(value));
    const grid = node("div", "cost-grid"), allCostNet = usage.cost_completeness === "COMPLETE" && usage.fx_status === "KNOWN" && usage.subscription_allocation_status === "KNOWN" ? usage.all_cost_net_eur : null;
    grid.append(append(node("div", "cost-box"), node("small", "", "Igazolt költségrészösszegek"), node("strong", "", entries.length ? entries.map(([currency, amount]) => `${number(amount)} ${currency}`).join(" · ") : "Nincs igazolt pénzösszeg")), append(node("div", "cost-box"), node("small", "", "Előfizetés-felosztás / FX"), node("strong", "", `${label(usage.subscription_allocation_status || "UNKNOWN")} / ${label(usage.fx_status || "UNKNOWN")}`)), append(node("div", "cost-box"), node("small", "", "Minden költség utáni nettó"), node("strong", "", money(allCostNet))));
    replace("costs", grid, node("p", "small muted", `A kiválasztott napi futás költséglefedettsége: ${label(usage.cost_completeness || "UNKNOWN")}. ${number(coverage.complete_count)} teljes / ${number(coverage.record_count)} rekord; ${number(coverage.unknown_count)} ismeretlen. Ez nem a fenti havi grafikon költsége.`), node("p", "small muted", reasons(usage.reason_codes) || "A hiányzó díj, számla és átváltás nem nulla. Az ismert pénznemeket nem adjuk össze átváltási bizonyíték nélkül."));
  }
  function renderOperations() {
    const data = state.workshop; if (!data) return; const schedule = obj(data.schedule), operation = obj(data.operation);
    const scheduleValid = schedule.task_name === "Arthur Ten Agents Morning" && schedule.timezone === "Europe/Berlin" && schedule.local_time === "08:00" && schedule.latest_start_local_time === "08:15" && schedule.target_date_policy === "SAME_BERLIN_DAY";
    replace("schedule-state", badge(scheduleValid && typeof schedule.enabled === "boolean" ? schedule.enabled ? "ENABLED" : "DISABLED" : "UNKNOWN"), keyValues([["Következő időpont", scheduleValid ? when(schedule.next_scheduled_at_utc) : "Az új reggeli definíció nem igazolt"], ["Előző slot", label(schedule.last_slot?.state || "UNKNOWN")]]));
    set("schedule-toggle", schedule.enabled === true ? "Reggeli indítás kikapcsolása" : schedule.enabled === false ? "Reggeli indítás bekapcsolása" : "Ütemezés állapotára vár");
    const legacy = arr(schedule.legacy_tasks);
    replace("legacy-state", ...(legacy.length ? legacy.map(task => append(node("div", "legacy-row"), append(node("div"), node("strong", "", text(task.name) || "Ismeretlen feladat"), node("small", "", when(task.observed_at))), badge(typeof task.enabled === "boolean" ? task.enabled ? "FAILED" : "DISABLED" : "UNKNOWN", task.enabled === true ? "Bekapcsolva · eltérés!" : task.enabled === false ? "OFF · kikapcsolva" : "Állapot ismeretlen"))) : [node("p", "notice warning", "A régi taskok állapota nem érkezett meg. Nem jelöljük őket kikapcsoltnak bizonyíték nélkül.")]));
    replace("operation-details", operation.job_id ? keyValues([["Állapot", label(operation.state)], ["Szakasz", text(operation.stage) || "Nem ismert"], ["Célnap", operation.target_date], ["Kezdés", when(operation.started_at)], ["Befejezés", when(operation.finished_at)], ["Natív kilépés", Number.isInteger(operation.native_exit) ? String(operation.native_exit) : "Nincs rögzített kilépés"], ["Folyamatfa lezárása", operation.tree_stopped === true ? "Igazolt" : operation.tree_stopped === false ? "Még nem zárult le" : "Nem ismert"], ["Ellenőrzés", label(operation.verification_state || data.run?.verification_state || "UNKNOWN")], ["Indok", text(operation.reason_code) || "Nincs mentett indok"]]) : node("p", "muted", "Nincs megjeleníthető tartós feladat. Ez önmagában nem bizonyítja, hogy minden háttérfolyamat áll."));
    const settings = obj(data.settings);
    if (!state.settingsDirty) { $("setting-target").value = count(settings.target_ticket_count) ? settings.target_ticket_count : ""; $("setting-strictness").value = finite(settings.strictness) ? settings.strictness : ""; $("setting-model").value = text(settings.openai?.model); $("setting-web").checked = settings.openai?.web_search === true; }
    set("settings-state", settings.openai ? "Csak explicit mentés módosítja a következő futás rendes beállításait; a tagság és az eredeti matek változatlan." : "A beállítások olvasási vetülete még nem érhető el; mentés tiltva."); syncControls();
    if (!scheduleValid) $("schedule-toggle").disabled = true;
  }
  function field(name, title, type = "text", options = {}) {
    const labelNode = node("label", "", title), input = type === "textarea" ? node("textarea") : type === "select" ? node("select") : node("input");
    if (input.tagName === "INPUT") input.type = type; input.name = name; input.id = `action-${name}`; input.required = options.required !== false;
    for (const key of ["min", "max", "step", "minLength", "maxLength", "value"]) if (options[key] !== undefined) input[key] = options[key];
    if (type === "select") arr(options.values).forEach(([value, caption]) => input.append(option(value, caption)));
    labelNode.append(input); return labelNode;
  }
  function openAction({ title, description, fields = [], submit = "Megerősítés", perform }) {
    if (state.busy) return; state.modal = { perform }; set("action-title", title); set("action-description", description); replace("action-fields", ...fields); set("action-error", ""); set("action-submit", submit); $("action-submit").disabled = false; $("action-dialog").showModal();
  }
  function closeAction() { if (state.busy) return; $("action-dialog").close(); state.modal = null; $("main").focus({ preventScroll: true }); }
  async function submitAction(event) {
    event.preventDefault(); if (!state.modal || state.busy || !$("action-form").reportValidity()) return; const modal = state.modal, values = Object.fromEntries(new FormData($("action-form")));
    state.busy = true; $("action-submit").disabled = true; $("action-close").disabled = true; $("action-cancel").disabled = true; syncControls();
    try { await modal.perform(values); $("action-dialog").close(); state.modal = null; toast("A műveletet rögzítettük; az aktuális állapot külön ellenőrizhető."); await refresh(true); }
    catch (error) { modal.uncertain = error.mutation_uncertain === true; set("action-error", `${error.message}${modal.uncertain ? " A kimenetel nem igazolt. Zárd be az ablakot és ellenőrizd a mentett állapotot; itt nem küldjük el ismét." : ""}`); if (error.status === 409) await refresh(true); }
    finally { state.busy = false; $("action-submit").disabled = modal.uncertain === true; $("action-close").disabled = false; $("action-cancel").disabled = false; syncControls(); }
  }
  function canWager(item) {
    const ticket = item.ticket, saved = obj(item.state);
    return capability("financial_recording") && !!ticket.ticket_id && saved.played !== true && saved.active !== false && ticket.publication_scope === "LIVE" && ticket.status === "READY" && ticket.playable === true && arr(ticket.legs).length > 0 && arr(ticket.legs).every(leg => { const instant = Date.parse(leg.kickoff_utc || leg.kickoff); return Number.isFinite(instant) && instant > Date.now(); });
  }
  function openWager(item) {
    if (!canWager(item)) { toast("A kártya aktuális állapota nem teszi lehetővé a megjátszás naplózását.", true); return; }
    const ticket = item.ticket;
    openAction({ title: "Tényleges megjátszás naplózása", description: `${ticket.profile_name || ownerName(ticket.profile_id)} · csak már megkötött, 1 EUR tétű fogadást rögzíts. Ez a művelet nem küld fogadást az irodának. Az ott ténylegesen elfogadott összszorzót add meg.`, fields: [field("price", "Elfogadott összszorzó", "number", { min: "1.01", step: "any" })], submit: "1 EUR megjátszás rögzítése", perform: values => {
      const price = Number(values.price); if (!Number.isFinite(price) || price <= 1) throw new Error("1-nél nagyobb érvényes összszorzó szükséges.");
      return api("/api/wager", { ticket_id: ticket.ticket_id, stake_eur: 1, actual_combined_price: price });
    } });
  }
  function returnContract(item) {
    const saved = obj(item.state), contract = saved.bookmaker_return_contract;
    if (!contract || contract.eligible !== true || contract.currency !== "EUR" || contract.amount_step !== "0.01" || contract.payout_basis !== "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN" || contract.fee_tax_inclusion !== "EXCLUDED" || typeof contract.expected_settlement_hash !== "string" || !/^[a-f0-9]{64}$/.test(contract.expected_settlement_hash) || contract.expected_settlement_hash !== saved.settlement?.settlement_hash || !finite(contract.minimum_gross_return_eur) || contract.minimum_gross_return_eur <= 0 || contract.minimum_gross_return_eur !== saved.wager?.stake_eur) return null;
    return contract;
  }
  function openSettlement(item) {
    if (!capability("financial_recording") || item.state?.played !== true) return; const ticket = item.ticket, saved = obj(item.state), requiresReturn = saved.settlement?.reason === "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION", contract = returnContract(item);
    if (requiresReturn && !contract) { toast("Az irodai elszámolás kötése hiányos vagy megváltozott. Frissítsd a kártyát.", true); return; }
    const fields = requiresReturn ? [field("amount", "Igazolt bruttó sportelszámolás · EUR", "number", { min: String(contract.minimum_gross_return_eur), step: "0.01" })] : [field("fixture_id", "Az iroda által érvénytelenített választás", "select", { values: arr(ticket.legs).map(leg => [String(leg.fixture_id), fixtureName(leg)]) })];
    fields.push(field("note", "Irodai bizonylat / indoklás", "textarea", { minLength: 3, maxLength: 500 }));
    openAction({ title: requiresReturn ? "Irodai visszatérülés rögzítése" : "Érvénytelen választás rögzítése", description: requiresReturn ? "Az iroda által igazolt, díjak és adók előtti bruttó sportelszámolás. A nettó pénzjóváírás ettől eltérhet; a művelet a megjelenített elszámolási revízióhoz kötött." : "Kizárólag a fogadóiroda által már érvénytelenített választást jelöld. Az eredeti kártya és ára megmarad.", fields, perform: values => {
      const payload = { ticket_id: ticket.ticket_id, note: values.note.trim() };
      if (requiresReturn) { if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(values.amount) || !finite(Number(values.amount)) || Number(values.amount) < contract.minimum_gross_return_eur) throw new Error("A szerződés szerinti minimális, centpontos bruttó összeg szükséges."); Object.assign(payload, { payout_eur: Number(values.amount), expected_settlement_hash: contract.expected_settlement_hash, payout_basis: contract.payout_basis }); }
      else payload.fixture_id = values.fixture_id;
      return api(requiresReturn ? "/api/bookmaker-return" : "/api/void", payload);
    } });
  }
  function openTeamStatus(agent) {
    if (!capability("team_status")) return;
    openAction({ title: `${agent.name || agent.agent_id} · csapatállapot`, description: "Ez a meglévő agent státuszát változtatja. A névjegyzék tagsága és a korábbi szelvények nem változnak.", fields: [field("status", "Állapot", "select", { values: [["ACTIVE", "Aktív"], ["PROBATION", "Próbaidő"], ["SUSPENDED", "Szüneteltetve"]] }), field("reason", "Indoklás", "textarea", { minLength: 3, maxLength: 500 })], perform: values => api("/api/team/status", { profile_id: agent.agent_id, status: values.status, reason: values.reason.trim() }) });
  }
  function openSchedule() {
    if (!capability("schedule_mutation")) return; const schedule = state.workshop.schedule;
    if (typeof schedule.enabled !== "boolean" || !text(schedule.revision)) return;
    openAction({ title: schedule.enabled ? "Reggeli indítás kikapcsolása" : "Reggeli indítás bekapcsolása", description: "Kizárólag az Arthur Ten Agents Morning feladat változik. 08:00–08:15 Europe/Berlin, aznapi céldátum; nincs catchup vagy automatikus ismétlés. A régi Daily és legacy kikapcsolva marad.", perform: () => api("/api/schedule", { enabled: !schedule.enabled, expected_revision: schedule.revision }) });
  }
  async function generate(event) {
    event.preventDefault(); if (!capability("manual_generation") || state.pending || !validDate(state.date) || state.date < berlinToday()) return;
    const day = state.date; openAction({ title: `${day} · teljes napi előállítás`, description: "Mind a tíz agent a teljes szolgáltatói napi naptárból dolgozik, közös adatokkal és saját eszközökkel. Ez valódi adat- és reasoningfogyasztással járhat. Egyszeri indítás; eredményfrissítés és fogadás nem kapcsolódik hozzá.", submit: "Egyszeri előállítás indítása", perform: async () => {
      if (state.pending) throw new Error("Már van visszaigazolásra váró indítás.");
      if (typeof crypto.randomUUID !== "function") throw new Error("Nem érhető el biztonságos kérésazonosító; új indítás tiltva.");
      const requestId = crypto.randomUUID(); rememberPending({ request_id: requestId, date: day, submitted_at: new Date().toISOString() });
      try { const result = await api("/api/run", { date: day, request_id: requestId, mode: "TEN_AGENT_FULL_DAY" });
        if (result.schema_version !== 1 || result.date !== day || typeof result.job_id !== "string") throw new Error("Az indítás tartós visszaigazolása hiányos. Csak olvasási ellenőrzést végzünk tovább.");
        rememberPending({ ...state.pending, job_id: result.job_id, observed: true });
      } catch (error) { if ([400, 403, 409].includes(error.status)) rememberPending(null); throw error; }
    } });
  }
  async function recoverPending() {
    const pending = state.pending; if (!pending) return;
    try { const result = await api(`/api/jobs/by-request/${encodeURIComponent(pending.request_id)}`, undefined, 1024 * 1024);
      if (state.pending?.request_id !== pending.request_id) return; const job = result.job;
      if (result.request_id !== pending.request_id || !job || job.target_date !== pending.date || typeof job.job_id !== "string") return;
      if (terminalStates.has(job.state)) rememberPending(null); else rememberPending({ ...pending, job_id: job.job_id, observed: true }); syncControls();
    } catch { /* Unknown response keeps the once lock. Never resend the POST. */ }
  }
  async function refreshJob() {
    if (state.pending) await recoverPending(); const id = state.pending?.job_id || state.workshop?.operation?.job_id;
    if (!id) { await refresh(); return; }
    try { const result = await api(`/api/jobs/${encodeURIComponent(id)}`, undefined, 1024 * 1024); if (result.job_id !== id) throw new Error("Másik feladat válasza érkezett."); await refresh(); }
    catch (error) { toast(error.message, true); }
  }
  function confirmSimple(kind) {
    const config = {
      settle: ["result_refresh", "/api/settle", "Eredmények frissítése", "Külön szolgáltatói eredményfrissítés indulhat. Ez nem generál új szelvényt, de fogyasztással járhat.", {}],
      freeze: ["financial_recording", "/api/freeze", "Napi csomag rögzítése", "Az eredeti, mentett napi ajánlatcsomag rögzítése. A meglévő kezdési és revíziókapuk változatlanok.", { run_id: currentRun() }],
      "check-openai": ["explicit_probes", "/api/check-openai", "Kapcsolatpróba", "Ez kifejezett kapcsolat-/modellpróba, fogyasztással járhat. Nem történik automatikusan az oldal megnyitásakor.", {}],
      "team-review": ["explicit_probes", "/api/team/review", "Csapatértékelés", "A meglévő csapatértékelési műveletet indítja, külön a napi szelvény-előállítástól.", {}],
      "team-scout": ["explicit_probes", "/api/team/scout", "Scout indítása", "Külön feltáró művelet; adat- és reasoningfogyasztással járhat. A napi generálást nem indítja újra.", {}]
    }[kind];
    if (!config || !capability(config[0]) || (kind === "freeze" && !currentRun())) return;
    openAction({ title: config[2], description: config[3], perform: () => api(config[1], config[4]) });
  }
  function saveSettings(event) {
    event.preventDefault(); if (!capability("settings_mutation") || !settingsKnown() || !$("settings-form").reportValidity()) return;
    const target = Number($("setting-target").value), strictness = Number($("setting-strictness").value), model = $("setting-model").value.trim();
    if (!Number.isSafeInteger(target) || target < 15 || target > 30 || !Number.isFinite(strictness) || strictness < 0 || strictness > 100 || !model) return;
    const patch = { target_ticket_count: target, strictness, openai: { model, web_search: $("setting-web").checked } };
    openAction({ title: "Rendes beállítások mentése", description: "A következő futás alapbeállításai változnak. A tíz agent, a teljes napi univerzum, a modellmatek, a minimum- és READY-kapuk nem kerülhetők meg ezzel.", perform: async () => { await api("/api/settings", patch); state.settingsDirty = false; } });
  }
  function changeDate(value) {
    if (!validDate(value) || value === state.date) return; state.date = value; state.epoch += 1; ++state.refreshSeq; ++state.cardsSeq; ++state.calendarSeq; ++state.evidenceSeq;
    state.workshop = null; state.connected = false; state.cards = []; state.cardsRun = null; state.cardsNext = null; state.cardsTotal = null; state.cardsLoading = false;
    state.calendar = []; state.calendarRun = null; state.calendarNext = null; state.calendarTotal = null; state.calendarLoading = false; state.evidence = []; state.evidenceLoading = false; state.evidenceNext = null;
    ++state.ledgerSeq; state.ledger = []; state.ledgerNext = null; state.ledgerTotal = null; state.ledgerLoading = false; replace("ledger-cards"); $("ledger-more").hidden = true;
    $("selected-date").value = value; $("evidence-panel").hidden = true;
    replace("run-summary", badge("UNKNOWN", "Az új nap állapotára vár")); set("minimum-note", "A kiválasztott nap mentett számlálása még nem érkezett meg."); set("nav-count", "—"); set("card-count", "betöltés alatt"); $("run-progress").hidden = true;
    replace("overview", ...["Teljes napi naptár", "Árazott mérkőzések", "Mentett szelvények", "Befejezett döntések"].map(title => metric(title, "—", "A kiválasztott nap adataira vár")));
    replace("cards", empty("A kiválasztott nap betöltése", "Az előző nap kártyáit nem mutatjuk az új dátum alatt.")); renderCompanions([]); replace("agents", empty("Napi agentállapotok betöltése", "A nap eredeti agentnévjegyzéke és mentett döntései hamarosan megjelennek."));
    $("cards-more").hidden = true; set("ledger-note", `${value} · erre a napra még nincs betöltött könyvelési lap.`); $("export-json").href = `/api/export?date=${encodeURIComponent(value)}`; $("export-txt").href = `/api/tickets.txt?date=${encodeURIComponent(value)}`;
    renderFamilies(); renderCalendar(); syncControls(); refresh();
  }
  function shiftDay(amount) { const date = new Date(`${state.date}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + amount); changeDate(date.toISOString().slice(0, 10)); }
  function selectView(view) {
    if (!Object.hasOwn(VIEW, view)) view = "workshop"; state.view = view;
    Object.keys(VIEW).forEach(key => { $(`view-${key}`).hidden = key !== view; });
    document.querySelectorAll("[data-view]").forEach(link => { const active = link.dataset.view === view; link.classList.toggle("active", active); if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
    const [title, kicker, description] = VIEW[view]; set("page-title", title); set("page-kicker", kicker); set("page-description", description); set("breadcrumb", `Műhely / ${view === "workshop" ? "Napi áttekintés" : title}`); document.title = `Arthur · ${title}`;
    if (view === "results" && !state.performance) loadPerformance();
  }
  function schedulePoll() { clearTimeout(state.pollTimer); state.pollTimer = setTimeout(async () => { if (!document.hidden && !state.busy) { await refresh(); if (state.pending) await recoverPending(); } schedulePoll(); }, busyStates.has(state.workshop?.operation?.state) || state.pending ? 15000 : 60000); }
  $("selected-date").value = state.date; $("performance-month").value = state.date.slice(0, 7); readPending();
  $("refresh").addEventListener("click", async () => { await refresh(true); if (state.pending) await recoverPending(); });
  $("selected-date").addEventListener("change", event => changeDate(event.target.value)); $("previous-day").addEventListener("click", () => shiftDay(-1)); $("next-day").addEventListener("click", () => shiftDay(1));
  $("generate-form").addEventListener("submit", generate); $("card-filters").addEventListener("submit", event => event.preventDefault()); $("card-filters").addEventListener("input", renderCards); $("card-filters").addEventListener("change", renderCards); $("cards-more").addEventListener("click", () => loadCards(false));
  $("calendar-load").addEventListener("click", () => loadCalendar(true)); $("calendar-more").addEventListener("click", () => loadCalendar(false)); $("calendar-search").addEventListener("input", renderCalendar);
  $("evidence-close").addEventListener("click", () => { $("evidence-panel").hidden = true; }); $("evidence-more").addEventListener("click", () => loadEvidence(state.evidenceFamily, false));
  $("performance-form").addEventListener("submit", event => { event.preventDefault(); loadPerformance(); }); $("performance-mode").addEventListener("change", loadPerformance); $("chart-metric").addEventListener("change", renderChart); $("profile-scope").addEventListener("change", renderProfiles);
  $("ledger-load").addEventListener("click", () => loadLedger(true)); $("ledger-more").addEventListener("click", () => loadLedger(false));
  $("schedule-toggle").addEventListener("click", openSchedule); $("job-refresh").addEventListener("click", refreshJob); $("freeze").addEventListener("click", () => confirmSimple("freeze")); $("settle").addEventListener("click", () => confirmSimple("settle"));
  document.querySelectorAll("[data-explicit-action]").forEach(item => item.addEventListener("click", () => confirmSimple(item.dataset.explicitAction)));
  $("settings-form").addEventListener("submit", saveSettings); $("settings-form").addEventListener("input", () => { state.settingsDirty = true; });
  $("action-form").addEventListener("submit", submitAction); $("action-close").addEventListener("click", closeAction); $("action-cancel").addEventListener("click", closeAction); $("action-dialog").addEventListener("cancel", event => { if (state.busy) event.preventDefault(); else state.modal = null; });
  window.addEventListener("hashchange", () => selectView(location.hash.slice(1))); document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); schedulePoll(); });
  replace("overview", ...["Teljes napi naptár", "Árazott mérkőzések", "Mentett szelvények", "Befejezett döntések"].map(title => metric(title, "—", "Mentett adatra vár")));
  replace("cards", empty("A műhely betöltése", "A mentett adatok ellenőrzött vetületére várunk.")); renderCompanions([]); selectView(location.hash.slice(1)); refresh().then(recoverPending).finally(schedulePoll);
})();
