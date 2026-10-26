"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { data: null, date: "", today: "", tab: "tickets", busy: false, teamBusy: false, tipsterBusy: false, tipsterChanges: new Map(), polling: false, performance: null, preferencesLoaded: false, settingsDirty: false, perfRequest: 0, statusRequest: 0, competitionRequest: 0, competitions: [], competitionSelections: new Map() };
  Object.assign(state, {paperPerformance:null, paperLoading:false, paperError:"", paperRequest:0, paperPage:0, paperSummaryPage:0, paperPeriodKey:null});
  Object.assign(state, {nemotron:null, nemotronError:"", nemotronRequest:0, nemotronFetching:false, nemotronKey:null, nemotronChecked:0, nemotronMissing:false, nemotronSignature:null});
  Object.assign(state, {grok:null, grokError:"", grokRequest:0, grokFetching:false, grokFetchingDay:null, grokDay:null, grokChecked:0, grokSignature:null, grokController:null});
  const profileNames = { DAILY_223: "DAILY_223", daily_223: "DAILY_223", daily223: "DAILY_223", kronikas: "Krónikás · Góllánc", ritmusor: "Ritmusőr · Válaszjáték", parharcmester: "Párharcmester · Erő és stílus", orszem: "Őrszem · Kontroll", merlin: "Merlin · Egyensúly", h1_over05: "Nyitány · Első félidő over 0,5", gauss: "GAUSS · Góltérkép", kepler: "KEPLER · Erőviszony és formafordulat" };
  const modelProfiles = {
    gauss: { description: "Góleloszlásból számolt teljes játékidős 1X2, mindkét csapat szerez gólt és over/under 2,5 piacok.", method: "penaltyblog · kétváltozós Poisson, Dixon–Coles referencia" },
    kepler: { description: "Csapaterő és formafordulat alapján számolt teljes játékidős 1X2. Más piacra nem ad modellbecslést.", method: "Pi-rating + CatBoost · csak 1X2" },
    laplace: { description: "Független Poisson-gólmodellből számolt referencia-becslés. A próbatér eredménye külön mérhető.", method: "penaltyblog · független Poisson-referenciamodell" }
  };
  profileNames.laplace = "Laplace · Poisson-referencia";
  const teamStatusNames = { ACTIVE: "Éles tag", PROBATION: "Próbaidő · csak papír", SUSPENDED: "Felfüggesztve" };
  const teamBlockerNames = { NO_PROSPECTIVE_DATA: "Még nincs belépés után, előre rögzített minta.", UNKNOWN_VERSION: "A stratégia verziója nem azonosítható.", AMBIGUOUS_PROVENANCE: "A döntés eredete nem egyértelmű.", MISSING_ODDS: "Hiányoznak az ellenőrizhető szorzók.", UNRESOLVED_RESULTS: "Még vannak lezáratlan eredmények.", INSUFFICIENT_TICKETS: "Még kevés az értékelhető szelvény.", INSUFFICIENT_DAYS: "Még rövid az előre mért időszak.", INSUFFICIENT_FIXTURES: "Még kevés az egyedi mérkőzés.", SHARED_FIXTURES_ACROSS_DAYS: "Napok között ismétlődő meccsek torzíthatják az összevetést.", DECISION_COOLDOWN: "A legutóbbi döntés utáni megfigyelési idő még tart.", INTERVAL_INCONCLUSIVE: "A bizonytalansági tartomány nem ad egyértelmű döntési alapot.", SUSPENDED_REQUIRES_MANUAL_RETURN: "A visszatéréshez tulajdonosi döntés szükséges." };
  const teamReasonNames = { INITIAL_MEMBER: "A műhely induló csapattagja; ez önmagában nem teljesítményigazolás.", MANUAL_OVERRIDE: "Tulajdonosi döntés alapján beállított tagság.", EVIDENCE_POSITIVE: "A rögzített bizonyíték teljesítette az előrelépés feltételeit.", EVIDENCE_NEGATIVE: "A rögzített bizonyíték alapján a vezető szünetelteti az éles részvételt.", KEEP_ACTIVE: "A jelenlegi éles tagság változatlan maradt." };
  teamBlockerNames.CURRENT_VERSION_MISMATCH = "A rögzített minta nem a jelenlegi kódhoz és beállításokhoz tartozik.";
  const modelStatusLabels = { ABSTAINED: "Nem ad ki külön ajánlatot", MODEL_READY: "Modellbecslés kész", ODDS_PENDING: "Modell kész · szorzóra vár", INSUFFICIENT_HISTORY: "Kevés történeti adat", DEPENDENCY_MISSING: "Modellfüggőség hiányzik", MODEL_ERROR: "Modellszámítási hiba", NO_STABLE_SELECTION: "Nincs stabil választás", NO_FIXTURES: "Nincs vizsgálható mérkőzés", NOT_RUN: "Még nincs modellfutás" };
  const modelStatusNotes = {
    ABSTAINED: "A modell nem ad ki külön ajánlatot: nincs megfelelő minőségű, más ajánlattól eltérő választás.",
    MODEL_READY: "A numerikus becslés elkészült. Kísérleti, még nem kalibrált eredmény; nem garantált nyerési esély.",
    ODDS_PENDING: "A modellbecslés elkészült, de a választáshoz nincs megfelelő szorzó. Várható értéket nem állapítunk meg ár nélkül.",
    INSUFFICIENT_HISTORY: "A modellhez nincs elég használható korábbi mérkőzésadat. Ettől még lehetnek meccsek a kiválasztott napon; most nem készült megbízhatóan számítható választás.",
    DEPENDENCY_MISSING: "A helyi számításhoz szükséges programcsomag nem elérhető. Ez nem mérkőzés- vagy szorzóhiány.",
    MODEL_ERROR: "A helyi modellszámítás hibával megállt. Az alábbi diagnosztika mutatja a részleteket; nem helyettesítjük kitalált becsléssel.",
    NO_STABLE_SELECTION: "A modell nem talált a saját stabilitási feltételeinek megfelelő választást. A mérkőzéskínálat ettől még elérhető lehet.",
    NO_FIXTURES: "Ehhez a modellfutáshoz nem érkezett vizsgálható mérkőzés a kijelölt napból és sorozatokból.",
    NOT_RUN: "Erre a napra még nincs elmentett numerikus modellfutás. A korábbi rekordokat változatlanul őrizzük."
  };
  const modelFieldLabels = { model: "Modell", model_name: "Modell", model_version: "Modellverzió", method: "Módszer", status: "Állapot", model_status: "Modellállapot", training_matches: "Tanítóminta · meccsek", training_size: "Tanítóminta mérete", sample_size: "Minta mérete", train_size: "Tanítóminta mérete", train_matches: "Tanítási mérkőzések", history_count: "Történeti mérkőzések", training_cutoff: "Tanítási időhatár", trained_until: "Tanítási időhatár", cutoff: "Adat-időhatár", calibration: "Kalibráció", calibrated: "Kalibrált", home: "Hazai", draw: "Döntetlen", away: "Vendég", probabilities: "Modellvalószínűségek (0–1)", model_probability: "Modellbecslés (0–1)", reference_probability: "Referencia-becslés (0–1)", dixon_coles_probability: "Dixon–Coles-becslés (0–1)", model_stability: "Modellstabilitás", estimated_edge: "Becsült várható érték", home_rating: "Hazai erősség", away_rating: "Vendég erősség", home_lambda: "Hazai gólintenzitás", away_lambda: "Vendég gólintenzitás", expected_home_goals: "Várható hazai gól", expected_away_goals: "Várható vendéggól", warning: "Figyelmeztetés", warnings: "Figyelmeztetések", message: "Magyarázat", code: "Diagnosztikai kód", reason: "Indok", source: "Forrás", fixtures_evaluated: "Értékelt mérkőzések" };
  const stateLabels = { ODDS_PENDING: "Szorzóra vár", PRICED: "Van szorzó", COMPLETE: "Elkészült", READY: "Áttekinthető", DRAFT: "Előzetes", TARGET_PROPOSAL: "Célajánlat", PRELIMINARY: "Előzetes", PARTIAL: "Részben elkészült", CONSTRUCTION_INCOMPLETE: "Hiányos összeállítás", DATA_BLOCKED: "Adathiány", PENDING: "Függőben", WON: "Nyert", LOST: "Vesztett", VOID: "Érvénytelen", REVIEW: "Ellenőrzést igényel", FAILED: "Sikertelen", ERROR: "Hiba", REVIEWED: "Elemzés elkészült", COMPLETED: "Elemzés elkészült", NOT_CONFIGURED: "Nincs kulcs", DISABLED: "Kikapcsolva", SKIPPED: "Nem futott", NO_CANDIDATES: "Nincs jelölt", NOT_RUN: "Még nem futott", RESEARCH_ONLY: "Kutatási", EXCLUDED: "Nem került be" };
  const issueLabels = { NOT_PENDING_ON_TARGET_DAY: "A mérkőzés már elkezdődött, vagy nem a kiválasztott napon van.", NO_BOOKMAKER_CANDIDATES: "Nem érkezett megfelelő, egy fogadóirodától származó szorzókínálat.", MISSING_HISTORY: "A történeti adatok hiányoznak.", HISTORY_UNAVAILABLE: "Egy liga / szezon történeti adatai hiányoznak; a gyűjtés folytatódott.", HISTORICAL_AND_VENUE_SUPPORT_REQUIRED: "A jelenlegi vétószint kizárta a hiányos történeti vagy formaadatú jelölteket. Állítsd 0–30 közé a csúszkát az engedékeny összeállításhoz.", FIRST_HALF_HISTORY_REQUIRED: "Hiányos első félidős minta: engedékeny módban figyelmeztetéssel megjelenhet.", QUOTE_STALE_OR_FROM_FUTURE: "Korábbi futás időbélyeg miatti kizárása. Az új elemzés már minden vétószinten felhasználja a talált szorzót.", BELOW_CONFIGURED_EVIDENCE_THRESHOLD: "A jelölt nem érte el a beállított adatminőségi küszöböt.", NO_MATCHING_ODDS_EVENT: "A mérkőzéshez még nincs egyértelműen párosított szorzó.", ASTRA_NOT_CONFIGURED: "A Codex ChatGPT-bejelentkezése még nincs beállítva.", INSUFFICIENT_CANDIDATES: "A kért szelvényhez kevés használható jelölt maradt.", NO_FIXTURES: "Nem érkezett mérkőzés a kiválasztott napra.", MISSING_EVIDENCE: "Az értékeléshez szükséges bizonyíték hiányzik.", NO_ODDS: "Nem érkezett használható szorzó.", FUTURE_DATE: "Jövőbeli nap: a jelentés előzetes." };
  const evidenceLabels = { historical: "Történeti minta", historical_fit: "Történeti illeszkedés", historical_score: "Történeti illeszkedés", venue_form: "Hazai / idegenbeli forma", venue_form_score: "Hazai / idegenbeli forma", competition_context: "Versenysorozat és meccskörnyezet", load_squad: "Terhelés és keret", motivation: "Motiváció", sample_size: "Minta mérete", home_sample_size: "Hazai minta", away_sample_size: "Vendégminta", recent_form: "Friss forma", source: "Forrás", sources: "Források", sample_count: "Minta mérete", score: "Érvelési pontszám", notes: "Megjegyzések", pending: "Még nem ellenőrzött", source_ids: "Forrásazonosítók", home_rate: "Hazai történeti gyakoriság", away_rate: "Vendég történeti gyakoriság" };
  Object.assign(modelFieldLabels, { fixtures_considered: "Vizsgált kínálat", predictions_received: "Beérkezett becslések", modeled_fixtures: "Modell által értékelt mérkőzések", eligible_selections: "Megfelelő választások", selected_legs: "Kiválasztott tippek", valid_quotes: "Érvényes szorzók", priced_selections: "Szorzóval rendelkező választások", odds_pending_selections: "Szorzóra váró választások", model_versions: "Modellverziók", history_hash: "Történeti adatok azonosítója", window_probabilities: "Időablakonkénti becslések (0–1)", temporal_validation: "Időben elkülönített ellenőrzés · nem profitigazolás", rps: "RPS hibamutató", brier: "Brier hibamutató", log_loss: "Log-loss hibamutató", logloss: "Log-loss hibamutató" });
  Object.assign(stateLabels, { ABSTAINED: "Kihagyta a napot" });
  const learningScopes = { actual: "Ténylegesen megjátszott", research: "Kutatási / papírteszt", frozen: "Rögzített napi csomagok" };
  const learningStatuses = { COMPLETE: "Jelentés elkészült", PARTIAL: "Részleges jelentés", NO_DATA: "Még nincs értékelhető adat" };
  const learningEvidenceKinds = { SCOPE_SUMMARY: "Naplóösszegzés", PROFILE: "Profiladatok", MARKET: "Piaci adatok", VERSION: "Verzióadatok", DATA_QUALITY: "Adatminőség", CALIBRATION: "Kalibrációs ellenőrzés" };
  const learningMetricLabels = { total: "Összes", decided: "Eldőlt", won: "Nyert", lost: "Vesztett", void: "Érvénytelen", pending: "Függő", review: "Ellenőrizendő", hit_rate_pct: "Találati arány (%)", tickets: "Szelvények", legs: "Meccstippek", unique_fixtures: "Egyedi mérkőzések", unique_selections: "Egyedi választások", missing_odds_legs: "Szorzó nélküli meccstippek", small_sample: "Kis minta", financial: "Pénzügyi mutatók", available: "Elérhető", settled_stake_eur: "Lezárt tét (EUR)", payout_eur: "Visszafizetés (EUR)", net_eur: "Nettó eredmény (EUR)", roi_pct: "Hozam (%)", sample_size: "Minta mérete", samples: "Megfigyelések", count: "Darabszám", brier: "Brier hibamutató", brier_score: "Brier hibamutató", log_loss: "Log-loss hibamutató", rps: "RPS hibamutató", mean_probability: "Átlagos modellbecslés (0–1)", observed_rate: "Megfigyelt találati arány (0–1)", calibration_gap: "Becslés és megfigyelés eltérése", runs_total: "Összes naplózott futás", runs_as_of: "Időhatárig figyelembe vett futás", recommendations_total: "Összes mentett ajánlat", recommendations_as_of: "Időhatárig figyelembe vett ajánlat", actual_wagers_as_of: "Figyelembe vett tényleges fogadás", frozen_packages_as_of: "Figyelembe vett rögzített csomag", cutoff_excluded: "Időhatár miatt kihagyott rekord", invalid_records: "Nem értelmezhető rekord" };
  Object.assign(learningMetricLabels, { legacy_observation_fallback: "Korábbi megfigyelési időre támaszkodó rekord", unknown_version_recommendations: "Ismeretlen verziójú ajánlat", shared_fixture_occurrences: "Közös mérkőzések ismételt előfordulásai", open_stake_eur: "Nyitott tényleges tét (EUR)", costs_included: "Szolgáltatási költségek levonva", eligible_selections: "Vizsgálható numerikus választások", validated: "Visszaigazolt kalibráció", brier_binary: "Bináris Brier hibamutató", bins: "Becslési tartományok · feltáró ellenőrzés", earliest_date: "Történet kezdete", latest_date: "Történet utolsó napja" });
  const automationLabels = { PENDING: "Várakozik", RUNNING: "Folyamatban", SUCCESS: "Sikeres", PARTIAL: "Részleges eredmény", RETRY_WAIT: "Újrapróbálásra vár", BLOCKED: "Beavatkozásra vár", FAILED: "Sikertelen", EXPIRED: "Időablak lejárt", SKIPPED: "Kihagyva", COMPLETED: "Befejezve", COMPLETED_WITH_WARNINGS: "Befejezve figyelmeztetéssel" };
  const automationStages = { settlement: "Korábbi eredmények frissítése", generation: "Következő napi elemzés", archive: "Napi csomag archiválása", learning: "Tanulási jelentés" };
  const automationStageNotes = { PENDING: "Ez a szakasz még várakozik.", RUNNING: "A szakasz végrehajtása folyamatban van.", SUCCESS: "A szakasz sikeresen befejeződött.", PARTIAL: "Részleges eredmény készült; nem minden szükséges adat állt rendelkezésre.", RETRY_WAIT: "A következő engedélyezett újrapróbálásra vár.", BLOCKED: "A folytatás feltétele hiányzik; az akadály feloldását igényli.", FAILED: "A szakasz nem fejeződött be sikeresen. Nyers hibanaplót itt nem jelenítünk meg.", EXPIRED: "A futási időablak lejárt; ez nem számít sikeres végrehajtásnak.", SKIPPED: "A szakasz ebben a futásban kimaradt." };
  const nf = new Intl.NumberFormat("hu-HU", { maximumFractionDigits: 2 });
  const currency = new Intl.NumberFormat("hu-HU", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });
  const dateFmt = new Intl.DateTimeFormat("hu-HU", { timeZone: "Europe/Berlin", month: "long", day: "numeric", weekday: "long" });
  const shortDateFmt = new Intl.DateTimeFormat("hu-HU", { timeZone: "Europe/Berlin", month: "short", day: "numeric" });
  const timeFmt = new Intl.DateTimeFormat("hu-HU", { timeZone: "Europe/Berlin", hour: "2-digit", minute: "2-digit" });
  const isNumber = (value) => value !== null && value !== "" && value !== undefined && Number.isFinite(Number(value));
  const num = (value, fallback = 0) => isNumber(value) ? Number(value) : fallback;
  const number = (value) => isNumber(value) ? nf.format(Number(value)) : "—";
  const money = (value) => isNumber(value) ? currency.format(Number(value)) : "—";
  const arr = (value) => Array.isArray(value) ? value : [];
  const label = (value) => stateLabels[value] || modelStatusLabels[value] || value || "Nincs adat";
  const profileName = (ticket) => ticket.profile_name || clubCatalogue().find(person => person.profile_id === ticket.profile_id)?.name || profileNames[String(ticket.profile_id || "").toLowerCase()] || profileNames[ticket.profile_id] || ticket.profile_id || "Arthur";
  const isClubTicket = (ticket) => ticket?.method_kind === "SHARED_EVIDENCE_PERSONA" || clubCatalogue().some(person => person.profile_id === ticket?.profile_id);
  const modelProfile = (ticket) => modelProfiles[String(ticket.profile_id || "").toLowerCase()];
  const isModel = (ticket) => ticket.method_kind === "LOCAL_STATISTICAL_MODEL" || !!modelProfile(ticket);
  const isAudition = (ticket) => ticket?.publication_scope === "PAPER_AUDITION";
  const isTeamOnly = (ticket) => isAudition(ticket) || ticket?.publication_scope === "SUSPENDED";
  const modelPublicationLabel = (ticket) => ticket?.publication_scope === "LIVE" ? "Éles ajánló · nincs automatikus fogadás" : isAudition(ticket) ? "Próbaidő · csak papír" : "Kísérleti modell · nincs tét";
  const finiteModelNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const validProbability = (value) => finiteModelNumber(value) && value >= 0 && value <= 1;
  const probabilityText = (value) => validProbability(value) ? `${number(value * 100)}%` : "Nincs becslés";
  const modelStatus = (ticket) => modelStatusLabels[ticket.status] ? ticket.status : ticket.model_status === "READY" ? "MODEL_READY" : ticket.model_status || "NOT_RUN";
  const modelNote = (ticket) => ticket.status === "ABSTAINED" ? (typeof ticket.abstention_reason === "string" && ticket.abstention_reason.trim() ? ticket.abstention_reason : modelStatusNotes.ABSTAINED) : modelStatusNotes[modelStatus(ticket)] || "A modell állapota további ellenőrzést igényel; ebből nem következtetünk mérkőzéshiányra.";
  const isDaily = (ticket) => String(ticket.profile_id || "").replaceAll("_", "").toLowerCase() === "daily223";
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined && text !== null) node.textContent = String(text); return node; };
  const replace = (id, ...nodes) => $(id).replaceChildren(...nodes.filter(Boolean));
  const append = (parent, ...nodes) => { parent.append(...nodes.filter(Boolean)); return parent; };
  const setText = (id, value) => { $(id).textContent = value ?? "—"; };
  const validDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(String(value || "")) && !Number.isNaN(new Date(`${value}T12:00:00Z`).getTime());
  const shiftDate = (date, days) => { const value = new Date(`${date}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); };
  const todayBerlin = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const when = (value, includeDate = false) => { const date = new Date(value); if (!value || Number.isNaN(date.getTime())) return "Időpont nem ismert"; return `${includeDate ? `${shortDateFmt.format(date)} · ` : ""}${timeFmt.format(date)}`; };
  const badge = (value, custom) => el("span", `badge ${["MODEL_READY", "COMPLETE", "READY", "WON", "COMPLETED", "REVIEWED"].includes(value) ? "success" : ["MODEL_ERROR", "LOST", "ERROR", "FAILED", "DATA_BLOCKED"].includes(value) ? "danger" : ["INSUFFICIENT_HISTORY", "DEPENDENCY_MISSING", "NO_STABLE_SELECTION", "ODDS_PENDING", "DRAFT", "TARGET_PROPOSAL", "PARTIAL", "REVIEW", "CONSTRUCTION_INCOMPLETE", "PRELIMINARY"].includes(value) ? "warning" : ""}`, custom || label(value));
  const safeUrl = (value) => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : null; } catch { return null; } };
  const meaningfulText = (value) => { if (typeof value === "string") return value; if (typeof value === "number") return number(value); if (typeof value === "boolean") return value ? "igen" : "nem"; if (value && typeof value === "object") return value.statement || value.text || value.summary || value.reason || value.description || value.note || value.message || JSON.stringify(value); return ""; };
  let toastTimer;
  function toast(message, error = false) { const node = $("toast"); node.textContent = message; node.classList.toggle("error", error); node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, error ? 9000 : 5500); }
  async function api(path, body) { const options = { credentials: "same-origin", headers: { Accept: "application/json", "X-Arthur-Token": window.ARTHUR_TOKEN || "" }, cache: "no-store" }; if (body !== undefined) { options.method = "POST"; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); } const response = await fetch(path, options); let data; try { data = await response.json(); } catch { throw new Error("A vezérlőpult nem értelmezhető választ kapott. Frissítsd az oldalt."); } if (!response.ok) throw new Error(data.error || data.message || `A kérés nem sikerült (${response.status}).`); return data; }
  function empty(title, description) { return append(el("div", "empty-state panel"), el("span", "empty-symbol", "A"), el("h3", "", title), el("p", "", description)); }
  function metric(title, value, note, color) { const node = el("article", "metric"); append(node, el("span", "", title), el("strong", color || "", value), el("small", "", note)); return node; }
  function strictnessLabel(value) { return value < 34 ? "Kikapcsolva" : value < 67 ? "Kiegyensúlyozott" : "Szigorú"; }
  function updateControlLabels() { setText("strictness-label", `${strictnessLabel(Number($("strictness").value))} · ${$("strictness").value}`); setText("ticket-target-label", $("ticket-count").value); setText("strictness-help", Number($("strictness").value) < 34 ? "Hiányzó háttéradat mellett is ad összeállítást, jól látható jelzéssel. A 10× alatti tartalék és az egymeccses változat is megjelenhet." : "Az adatminőségi feltételek kizárhatnak jelölteket. Engedékeny összeállításhoz húzd 0–30 közé."); }
  function selectedCompetitions() { return state.competitionSelections.get(state.date) || new Set(); }
  function isDefaultCompetition(item) { return typeof item.default_selected === "boolean" ? item.default_selected : item.configured || item.type === "NATIONAL"; }
  function renderCompetitions() { const selected = selectedCompetitions(); const term = $("competition-search").value.trim().toLocaleLowerCase("hu-HU"); const options = state.competitions.filter(item => `${item.name} ${item.country}`.toLocaleLowerCase("hu-HU").includes(term)); setText("competition-count", `${selected.size} kijelölve · ${state.competitions.length} elérhető`); replace("competition-list", ...options.map(item => { const input = el("input"); input.type = "checkbox"; input.checked = selected.has(item.id); input.value = String(item.id); input.addEventListener("change", () => { if (input.checked) selected.add(item.id); else selected.delete(item.id); state.competitionSelections.set(state.date, selected); renderCompetitions(); }); const detail = append(el("span", "competition-copy"), el("strong", "", `${item.name} · ${item.country}`), el("small", "", `${item.type === "NATIONAL" ? "Válogatott" : item.fallback ? "Tartalék sorozat" : item.configured ? "Alap sorozat" : "További sorozat"} · ${item.fixture_count} meccs · ${arr(item.fixtures).map(f => `${f.home}–${f.away}`).join(", ")}`)); return append(el("label", "competition-option"), input, detail); })); if (!options.length) replace("competition-list", el("p", "muted", state.competitions.length ? "Nincs találat a keresésre." : "Erre a napra nem érkezett sorozat.")); }
  async function loadCompetitions(force = false) { if (state.date < state.today) { state.competitions = []; replace("competition-list", el("p", "muted", "Korábbi naphoz az archív eredményeket nézd meg.")); setText("competition-feedback", ""); setText("competition-count", "Archív nap"); return; } const request = ++state.competitionRequest; const day = state.date; setText("competition-feedback", "Az aznapi kínálat lekérése…"); try { const response = await api(`/api/competitions?date=${encodeURIComponent(day)}${force ? "&refresh=1" : ""}`); if (request !== state.competitionRequest || day !== state.date) return; state.competitions = arr(response.competitions); if (!state.competitionSelections.has(day)) state.competitionSelections.set(day, new Set(state.competitions.filter(isDefaultCompetition).map(item => item.id))); else state.competitionSelections.set(day, new Set([...selectedCompetitions()].filter(id => state.competitions.some(item => item.id === id)))); setText("competition-feedback", `${state.competitions.reduce((sum, item) => sum + item.fixture_count, 0)} mérkőzés ${state.competitions.length} sorozatban. A szorzó lefedettsége az elemzéskor derül ki.`); renderCompetitions(); } catch (error) { if (request !== state.competitionRequest) return; state.competitions = []; replace("competition-list", el("p", "muted", "A kínálat nem tölthető be.")); setText("competition-feedback", error.message); setText("competition-count", "Nem elérhető"); } }
  function freezeRevisionText(frozen) { return Number.isInteger(frozen?.revision) && frozen.revision > 1 ? `Javított csomag · ${frozen.revision}. változat · ` : ""; }
  function renderFreezeControls() { const frozen = arr(state.data?.freezes)[0]; const report = reportForDate(); setText("freeze-status", frozen ? `${freezeRevisionText(frozen)}${when(frozen.frozen_at, true)} rögzítve · ${arr(frozen.recommendations).length} ajánlat · ${label(frozen.outcome)}` : "Még nincs rögzített napi csomag."); $("freeze-button").disabled = !!frozen || !report || !arr(report.recommendations).some(r => arr(r.legs).length) || state.data?.runtime?.running || state.busy || arr(report.recommendations).some(r => arr(r.legs).some(l => new Date(l.kickoff).getTime() <= Date.now())); }
  async function freezeDay() { const report = reportForDate(); if (!report) return; state.busy = true; renderFreezeControls(); try { const result = await api("/api/freeze", {run_id: report.run_id}); toast(`${result.recommendation_count} ajánlatból álló napi csomag rögzítve.`); await refreshStatus(); renderAll(); } catch (error) { toast(error.message, true); } finally { state.busy = false; renderFreezeControls(); } }
  function recommendationsForDate() {
    const saved = arr(state.data?.recommendations);
    const report = reportForDate();
    const reported = arr(report?.recommendations);
    if (!saved.length) return reported;
    // Empty model outcomes are intentionally not in the recommendation ledger.
    // Preserve the exact settled snapshot for the same recommendation or run.
    // Only a demonstrably newer run may replace its daily presentation.
    const latestModels = new Map(reported.filter(isModel).map(item => [String(item.profile_id).toLowerCase(), item]));
    const seen = new Set(saved.map(item => String(item.profile_id).toLowerCase()));
    const timestamp = (...values) => {
      for (const value of values) {
        const time = typeof value === "string" && value.trim() ? Date.parse(value) : NaN;
        if (Number.isFinite(time)) return time;
      }
      return NaN;
    };
    return [...saved.map(item => {
      const candidate = latestModels.get(String(item.profile_id).toLowerCase());
      if (!candidate) return item;
      const sameRecommendation = item.recommendation_id && item.recommendation_id === candidate.recommendation_id;
      const candidateRun = candidate.run_id || report?.run_id;
      if (sameRecommendation || (item.run_id && item.run_id === candidateRun)) return item;
      const candidateTime = timestamp(report?.finished_at, candidate.created_at, report?.observed_at);
      const savedTime = timestamp(item.created_at, item.observed_at);
      return Number.isFinite(candidateTime) && Number.isFinite(savedTime) && candidateTime > savedTime ? candidate : item;
    }), ...[...latestModels].filter(([id]) => !seen.has(id)).map(([, item]) => item)];
  }
  function mergeRecommendation(recommendation, ticket) {
    return ticket ? {...ticket, ...recommendation, ticket_id: ticket.ticket_id, played: ticket.played, wager: ticket.wager, financial: ticket.financial, outcome: ticket.outcome || recommendation.outcome, settlement: ticket.settlement || recommendation.settlement, selected_for_portfolio: true} : recommendation;
  }
  function reportTickets() {
    const recommendations = recommendationsForDate();
    if (recommendations.length) return recommendations.filter(r => !isTeamOnly(r) && !isClubTicket(r)).map(r => mergeRecommendation(r, ledgerTickets().find(t => t.ticket_id === r.portfolio_ticket_id)));
    const priced = Array.isArray(state.data?.tickets) ? ledgerTickets().filter(item => !item.date || item.date === state.date) : arr(reportForDate()?.tickets);
    return [...priced, ...arr(reportForDate()?.preview_tickets)].filter(item => !isTeamOnly(item) && !isClubTicket(item));
  }
  function ledgerTickets() { return arr(state.data?.tickets); }
  function reportForDate() { const report = state.data?.latest; return report && (!report.date || report.date === state.date) ? report : null; }
  function marketText(leg) { const market = String(leg.market_key || leg.market || "").toLowerCase(); const choice = String(leg.selection_key || leg.selection || "").toLowerCase(); const period = String(leg.period || "").toLowerCase(); const half = ["h1", "1h", "first_half", "firsthalf"].includes(period) || /_h1|_1h|first_half/.test(market); let result = leg.market_label || ""; if (!result) { if (market.includes("btts") || market.includes("both_teams")) result = /no|nem/.test(choice) ? "Mindkét csapat gólt szerez: nem" : "Mindkét csapat gólt szerez: igen"; else if (/over|under|totals|goals/.test(market) || /over|under/.test(choice)) { const line = leg.line ?? leg.points ?? leg.total ?? `${choice} ${market}`.match(/(?:over|under|totals?)[_ :]*(\d+(?:[._]\d+)?)/)?.[1]?.replace("_", "."); const under = choice.includes("under") || market.startsWith("under"); result = `${under ? "Under" : "Over"} ${line === undefined ? "" : number(line)} gól`.replace(/ +/g, " "); } else if (["draw", "x", "tie"].includes(choice)) result = "Döntetlen"; else if (["home", "1", "home_win"].includes(choice)) result = "Hazai győzelem"; else if (["away", "2", "away_win"].includes(choice)) result = "Vendéggyőzelem"; else result = leg.selection_label || leg.selection_key || leg.market_key || "Piac nem ismert"; } return `${half ? "1. félidő · " : ""}${result}`; }
  function evidenceNodes(evidence) { const rows = []; if (Array.isArray(evidence)) { evidence.forEach((item) => { const text = meaningfulText(item); if (text) rows.push(el("li", "", text)); }); } else if (evidence && typeof evidence === "object") { Object.entries(evidence).forEach(([key, value]) => { if (value == null || (Array.isArray(value) && !value.length)) return; let text; if (Array.isArray(value)) text = value.map(meaningfulText).filter(Boolean).join("; "); else if (typeof value === "object") text = Object.entries(value).map(([subkey, subvalue]) => `${evidenceLabels[subkey] || subkey}: ${meaningfulText(subvalue)}`).join("; "); else text = meaningfulText(value); if (text) rows.push(el("li", "", `${evidenceLabels[key] || key}: ${text}`)); }); } else if (evidence) rows.push(el("li", "", String(evidence))); return rows; }
  function modelValue(value, depth = 0) {
    if (value == null) return el("span", "muted", "Nincs adat");
    if (typeof value !== "object") return el("span", "", typeof value === "number" ? number(value) : typeof value === "boolean" ? (value ? "igen" : "nem") : label(value));
    if (depth >= 4) return el("span", "", JSON.stringify(value));
    if (Array.isArray(value)) return value.length ? append(el("ul", "model-value-list"), ...value.map(item => append(el("li"), modelValue(item, depth + 1)))) : el("span", "muted", "Nincs további adat");
    const list = el("dl", "model-facts");
    Object.entries(value).forEach(([key, item]) => append(list, el("dt", "", modelFieldLabels[key] || key.replaceAll("_", " ")), append(el("dd"), modelValue(item, depth + 1))));
    return Object.keys(value).length ? list : el("span", "muted", "Nincs további adat");
  }
  function modelDetailBlock(title, value) {
    return append(el("details", "model-details"), el("summary", "", title), modelValue(value));
  }
  function modelOverview(ticket) {
    const box = el("aside", "model-overview");
    append(box, append(el("div", "model-tagline"), badge(modelStatus(ticket)), el("span", "badge model-badge", modelPublicationLabel(ticket))), el("p", "", modelNote(ticket)), el("p", "model-disclaimer", "Helyi numerikus számítás. Nem kalibrált, nem garantált becslés; az LLM nem írja át a valószínűségeket. Az éles ajánlói tagság sem jelent tényleges fogadást vagy automatikus plusztétet."));
    if (ticket.model_summary && typeof ticket.model_summary === "object") box.append(modelDetailBlock("Modellfutás és minta", ticket.model_summary));
    if (arr(ticket.model_diagnostics).length) box.append(modelDetailBlock("Modell-diagnosztika", ticket.model_diagnostics));
    return box;
  }
  function modelLegInfo(leg) {
    const node = el("div", "model-leg-info");
    const stats = el("div", "model-metrics");
    stats.append(append(el("div"), el("small", "", "Nem kalibrált modellbecslés"), el("strong", "", probabilityText(leg.model_probability))));
    if (finiteModelNumber(leg.model_stability) && leg.model_stability >= 0 && leg.model_stability <= 1) stats.append(append(el("div"), el("small", "", "Modellváltozatok eltérése"), el("strong", "", `${number(leg.model_stability * 100)} százalékpont`)));
    const hasPrice = isNumber(leg.decimal_price) && Number(leg.decimal_price) > 1;
    if (hasPrice && validProbability(leg.model_probability) && finiteModelNumber(leg.estimated_edge)) stats.append(append(el("div"), el("small", "", "Becsült várható érték · p × szorzó − 1"), el("strong", "", `${leg.estimated_edge > 0 ? "+" : ""}${number(leg.estimated_edge * 100)}%`)));
    append(node, stats, el("p", "model-disclaimer", "A százalék a választás modellbecslése, nem a teljes szelvény nyerési esélye. A várható érték becslés, nem igazolt hozam."));
    if (!hasPrice) node.append(el("p", "quote-note", "Szorzó hiányában nincs becsült várható érték; a találat ettől még követhető."));
    if (leg.model_details && typeof leg.model_details === "object") node.append(modelDetailBlock("Számítás, modellváltozatok és ellenőrzés", leg.model_details));
    return node;
  }
  function modelArchiveDetails(ticket) {
    const details = append(el("details", "model-archive-details"), el("summary", "", "Az elmentett modellbecslés és módszer"), modelOverview(ticket));
    arr(ticket.legs).forEach(leg => details.append(append(el("section", "model-archive-leg"), el("h4", "", `${leg.home_team || "Hazai"} – ${leg.away_team || "Vendég"} · ${marketText(leg)}`), modelLegInfo(leg))));
    return details;
  }
  function modelTicketCard(ticket) {
    const node = el("article", "ticket-card model-ticket-card");
    const profile = modelProfile(ticket);
    const heading = append(el("div", "ticket-heading"), el("h3", "", profileName(ticket)), el("p", "", profile?.method || "Helyi statisztikai modell"));
    append(node, append(el("header", "ticket-header"), el("div", "profile-icon model-icon", String(ticket.profile_id || "M").slice(0, 1).toUpperCase()), heading), modelOverview(ticket));
    if (arr(ticket.legs).length) node.append(append(el("div", "ticket-legs"), ...ticket.legs.map((leg, index) => legCard(leg, index, true))));
    else node.append(append(el("div", "model-empty"), el("strong", "", "Most nincs kiadott modellválasztás"), el("p", "", "A profil látható marad. Az okot a fenti modellállapot és a diagnosztika jelzi; ezt nem tekintjük vesztes tippnek.")));
    const notes = [...arr(ticket.reasoning), ...arr(ticket.quality_warnings), ...arr(ticket.risk_notes)];
    if (notes.length) node.append(append(el("details", "ticket-notes"), el("summary", "", "Kiválasztási indokok és korlátok"), ...notes.map(note => el("p", "", meaningfulText(note)))));
    append(node, append(el("footer", "ticket-footer model-footer"), append(el("div", "ticket-price"), el("small", "", "Tájékoztató összszorzó"), el("strong", "", number(ticket.combined_price))), append(el("div", "ticket-footer-actions"), el("span", "badge model-badge", modelPublicationLabel(ticket)), el("small", "", arr(ticket.legs).length ? "Külön mérhető modellajánlat · nem köt fogadást" : "Állapotjelzés · nem kiadott fogadás"))));
    return node;
  }
  function exceptionalAnalysis(leg) {
    const analysis = leg.contrarian_analysis;
    const override = leg.veto_override || leg.veto_override_proof;
    if (!analysis && !override) return null;
    const box = el("section", "exception-analysis");
    if (analysis) {
      box.append(el("strong", "", "Piaccal szembeni értékkeresés"));
      const explanation = analysis.explanation || {};
      box.append(el("p", "", `Ármozgás: ${number(analysis.baseline_price)} → ${number(analysis.current_price)} · ${number(100 * num(analysis.relative_change))}%.`));
      if (explanation.price_case) box.append(append(el("p"), el("strong", "", "Miért megy szembe az árral? "), el("span", "", explanation.price_case)));
      if (explanation.math_case) box.append(append(el("p"), el("strong", "", "Mi a matematikai alátámasztás? "), el("span", "", explanation.math_case)));
      if (validProbability(analysis.model_probability) && isNumber(analysis.expected_return)) box.append(el("p", "", `Modellbecslés: ${probabilityText(analysis.model_probability)} · p × szorzó = ${number(analysis.expected_return)} > 1,15. Nem kalibrált becslés.`));
      if (explanation.cause_status !== "SOURCED") box.append(el("p", "quote-note", "Az ármozgás oka nem igazolt. Ebből lakossági fogadási irányt nem következtetünk."));
    }
    if (override) {
      box.append(el("strong", "", "Forrásolt kivétel · Astra felülbírálás"));
      box.append(el("p", "", override.justification || "A kivétel indoklása a mentett ellenőrzésben szerepel."));
      const names = {HIGH_ODDS:"Magas szorzó miatti profilszűrés", LEAGUE_BASE_RATE:"Átlagos történeti gyakoriságra épülő szűrés"};
      box.append(el("p", "quote-note", arr(override.gates).map(key => names[key] || key).join(" · ")));
    }
    const links = el("div", "source-links");
    const sources = new Set([...arr(analysis?.explanation?.cause_source_urls), ...arr(override?.source_urls)]);
    sources.forEach(value => { const url = safeUrl(value); if (!url) return; const link = el("a", "", new URL(url).hostname); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; links.append(link); });
    if (links.children.length) box.append(links);
    return box;
  }
  function legCard(leg, index, model = false) {
    const node = el("article", "ticket-leg");
    append(node, append(el("div", "leg-topline"), el("span", "", `${String(index + 1).padStart(2, "0")} · ${leg.competition_name || leg.competition || leg.competition_id || "Mérkőzés"}`), el("time", "", when(leg.kickoff))));
    append(node, el("div", "leg-title", `${leg.home_team || "Hazai csapat"} – ${leg.away_team || "Vendégcsapat"}`));
    append(node, append(el("div", "leg-market"), el("span", "market-pill", marketText(leg)), el("strong", "leg-price", number(leg.decimal_price))));
    if (isNumber(leg.decimal_price)) {
      const quoteAge = leg.quoted_at ? Date.now() - new Date(leg.quoted_at).getTime() : NaN;
      const timing = Number.isFinite(quoteAge) ? (quoteAge < 0 ? "FUTURE" : quoteAge > 300000 ? "OLDER" : "CURRENT") : leg.quote_timestamp_status || "MISSING";
      const info = {OLDER: "Nem friss adat", FUTURE: "Jövőbeli szolgáltatói időbélyeg", MISSING: "Időbélyeg nincs megadva", INVALID: "Az időbélyeg nem értelmezhető"}[timing];
      if (info) node.append(el("p", "quote-note", `${info} · a szorzó szerepel a számításban.`));
    }
    if (model) node.append(modelLegInfo(leg));
    const exceptional = exceptionalAnalysis(leg);
    if (exceptional) node.append(exceptional);
    const evidence = evidenceNodes(leg.evidence || leg.supporting_evidence || leg.reasoning);
    if (isNumber(leg.support_score)) evidence.unshift(el("li", "", `Érvelési pontszám: ${number(leg.support_score)}. Nem nyerési valószínűség.`));
    if (leg.sensitivity_note) evidence.push(el("li", "", meaningfulText(leg.sensitivity_note)));
    const review = leg.astra_review || {};
    arr(review.support_arguments).forEach((argument) => evidence.push(el("li", "", `Astra: ${meaningfulText(argument)}`)));
    arr(review.risk_notes).forEach((risk) => evidence.push(el("li", "risk-note", `Astra megjegyzése: ${meaningfulText(risk)}`)));
    if (arr(leg.missing_support).length) evidence.push(el("li", "", `Még nem alátámasztott: ${leg.missing_support.map((key) => evidenceLabels[key] || key).join(", ")}.`));
    if (evidence.length || arr(review.source_urls).length) {
      const details = append(el("details", "leg-evidence"), el("summary", "", "Mi szól mellette?"));
      append(details, append(el("ul", "evidence-list"), ...evidence));
      const sourceDirectory = new Map(arr(state.data?.latest?.astra?.sources).map((source) => [source.url, source]));
      const sources = [...arr(leg.sources || leg.evidence?.sources), ...arr(review.source_urls).map((url) => sourceDirectory.get(url) || { url })];
      const links = el("div", "source-links");
      const seen = new Set();
      sources.forEach((source) => {
        const url = safeUrl(typeof source === "string" ? source : source.url);
        if (!url || seen.has(url)) return;
        seen.add(url);
        const link = el("a", "", typeof source === "object" ? source.title || source.name || new URL(url).hostname : new URL(url).hostname);
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; links.append(link);
      });
      if (links.children.length) details.append(links);
      if (leg.quoted_at) details.append(el("p", "quote-note", `Szolgáltatói időbélyeg: ${when(leg.quoted_at, true)}`));
      if (leg.quote_source_id) details.append(el("p", "quote-note", `Árforrás: ${leg.bookmaker_name || leg.bookmaker || ""} · ${leg.quote_source_id}`));
      node.append(details);
    }
    const quotes = arr(leg.web_quotes);
    quotes.forEach(q => { const url=safeUrl(q.source_url); if (!url) return; const box=el("p","quote-note"); const link=el("a","",`${q.bookmaker} · ${number(q.decimal_price)}×`); link.href=url; link.target="_blank"; link.rel="noopener noreferrer"; append(box,link,el("span","",` · webes tájékoztató ár · ${when(q.observed_at,true)} · irodánál ellenőrizendő`)); node.append(box); });
    const context=arr(leg.fixture_context);
    if (context.length) { const names={weather:"Időjárás · Open-Meteo",coach:"Edzői hírek",lineup:"Kezdőcsapat",absences:"Hiányzók",workload:"Terhelés",motivation:"Tét / motiváció"}; const details=append(el("details","leg-evidence"),el("summary","","Meccskörnyezet és források")); context.forEach(c=> { const line=el("p","",`${names[c.topic] || c.topic}: ${c.status === "UNKNOWN" ? "Nem ellenőrzött" : c.status === "PROVISIONAL" ? "Előzetes" : "Forrásolt"} — ${c.summary}`); arr(c.source_urls).forEach(u=>{const url=safeUrl(u);if(url){const a=el("a",""," Forrás");a.href=url;a.target="_blank";a.rel="noopener noreferrer";line.append(a);}}); details.append(line); });node.append(details); }
    return node;
  }
  function ticketCard(ticket) { if (isModel(ticket)) return modelTicketCard(ticket); const node = el("article", "ticket-card"); const daily = isDaily(ticket); const recorded = ticket.ticket_id ? ledgerTickets().find((entry) => entry.ticket_id === ticket.ticket_id) : null; const played = !!(recorded?.played || ticket.played); const started = arr(ticket.legs).some((leg) => leg.kickoff && new Date(leg.kickoff).getTime() <= Date.now()); const header = el("header", "ticket-header"); const heading = append(el("div", "ticket-heading"), el("h3", "", profileName(ticket)), el("p", "", `${daily ? "Önálló 2 × 2 × 3" : `${arr(ticket.legs).length} mérkőzés · saját módszer`} · ${ticket.bookmaker || (ticket.odds_pending ? "Szorzó még nincs" : "Fogadóiroda nem ismert")} · ${when(ticket.created_at)}`)); append(header, el("div", `profile-icon${daily ? " daily" : ""}`, daily ? "223" : profileName(ticket).slice(0, 1)), heading, badge(played ? "PENDING" : ticket.status || "DRAFT", played ? "Megjátszva" : undefined)); node.append(header); if (ticket.confidence) { const levels = {LOW:"Gyenge",MEDIUM:"Közepes",HIGH:"Magas"}; node.append(el("p", "ticket-status-note", `${levels[ticket.confidence] || "Gyenge"} bizonyítottság · nem kalibrált nyerési valószínűség${ticket.selected_for_portfolio ? " · Napi csomagba kijelölve" : " · Profilajánlat"}`)); } const qualityWarnings = arr(ticket.quality_warnings); if (qualityWarnings.length) { const quality = append(el("aside", "ticket-quality"), el("strong", "", ticket.odds_pending ? "Szorzóra váró előzetes" : ticket.fallback ? "Tartalék szelvény · eltér a céltól" : "Szelvény adathiánnyal")); qualityWarnings.forEach((warning) => quality.append(el("p", "", meaningfulText(warning)))); node.append(quality); } const legs = el("div", "ticket-legs"); arr(ticket.legs).forEach((leg, index) => legs.append(legCard(leg, index))); node.append(legs); const reasons = arr(ticket.reasoning); const risks = arr(ticket.risk_notes); if (reasons.length || risks.length) { const notes = append(el("details", "ticket-notes"), el("summary", "", "A szelvény gondolatmenete")); reasons.forEach((reason) => notes.append(el("p", "", meaningfulText(reason)))); risks.forEach((risk) => notes.append(el("p", "risk-note", meaningfulText(risk)))); node.append(notes); } if (ticket.requires_refresh) node.append(el("p", "ticket-status-note", "Az ár vagy a mérkőzésállapot ellenőrzést igényel. A szolgáltató utolsó ismert szorzóját mutatjuk; megjátszáskor az irodánál látható árat ellenőrizd.")); else if (["DRAFT", "PRELIMINARY"].includes(ticket.status)) node.append(el("p", "ticket-status-note", "Előzetes összeállítás. A forrásokat és az aktuális szorzókat még ellenőrizni kell.")); const footer = el("footer", "ticket-footer"); append(footer, append(el("div", "ticket-price"), el("small", "", "Összszorzó"), el("strong", "", number(ticket.combined_price)))); const actions = el("div", "ticket-footer-actions"); const button = el("button", `button button-small ${played ? "button-secondary" : "button-primary"}`, ticket.recommendation_id && !ticket.ticket_id ? "Mentett profilajánlat" : ticket.odds_pending ? "Szorzófrissítés szükséges" : played ? "A naplóban rögzítve" : started ? "A kezdés után már lezárva" : "Megjátszottam · 5 €"); button.type = "button"; button.disabled = ticket.odds_pending || played || started || !ticket.ticket_id; button.addEventListener("click", () => recordWager(ticket, button)); append(actions, button, el("small", "", played ? "Manuálisan jelölt, tényleges tét" : ticket.recommendation_id && !ticket.ticket_id ? ticket.odds_pending ? "Eredménye szorzó nélkül is követhető" : "Elmentve az eredménykövetéshez" : "Csak naplózás · nem köt fogadást")); footer.append(actions); node.append(footer); return node; }
  function diagnosticRows(report) { const diagnostics = arr(report?.diagnostics); const signatures = new Set(diagnostics.map(item => JSON.stringify(item))); const list = diagnostics.concat(arr(report?.data_issues).filter(item => !signatures.has(JSON.stringify(item)))); const grouped = new Map(); list.forEach((item) => { const object = typeof item === "string" ? { code: item } : item || {}; const code = object.code || object.status || object.type || "INFO"; const description = object.message || object.reason || object.detail || issueLabels[code] || code; const key = `${code}:${meaningfulText(description)}`; const existing = grouped.get(key); if (existing) existing.count += num(object.count, 1); else grouped.set(key, { code, description: meaningfulText(description), count: num(object.count, 1) }); }); return [...grouped.values()]; }
  function diagnosticCount(report, keys) { const collections = [report, report?.coverage, report?.diagnostics_summary, report?.counts, report?.funnel, report?.usage]; for (const collection of collections) { for (const key of keys) if (collection && isNumber(collection[key])) return Number(collection[key]); } return null; }
  function capacityScopeCard(objectives) {
    const validCount = (value) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
    const countText = (value) => validCount(value) ? number(value === 0 ? 0 : value) : "ismeretlen";
    const explicitScopes = validCount(objectives.ready_live_recommendations) && validCount(objectives.ready_paper_recommendations);
    let status = "PENDING", note = "LIVE/PAPER bontás nem ismert";
    if (explicitScopes) {
      if (objectives.capacity_status === "IN_TARGET") { status = objectives.capacity_status; note = "LIVE célon belül"; }
      else if (objectives.capacity_status === "ABOVE_TARGET") { status = objectives.capacity_status; note = "LIVE cél fölött"; }
      else if (objectives.capacity_status === "BELOW_TARGET" && validCount(objectives.capacity_shortfall)) {
        status = objectives.capacity_status; note = `${countText(objectives.capacity_shortfall)} LIVE ajánlat hiányzik`;
      } else note = "LIVE kapacitásállapot nem ismert";
    }
    return append(el("article", "profile-status"), el("strong", "", "Napi célkeret"),
      el("p", "", `${countText(objectives.ready_live_recommendations)} LIVE · ${countText(objectives.ready_paper_recommendations)} PAPER · LIVE cél ${number(objectives.target_ready_range?.minimum)}–${number(objectives.target_ready_range?.maximum)} · nem DAILY szorzósáv ${number(objectives.non_daily_target_price_range?.minimum)}–${number(objectives.non_daily_target_price_range?.maximum)}`),
      badge(status, note));
  }
  function renderDecisionDataUsage(report) {
    const usage = report?.decision_data_usage;
    const objectives = report?.portfolio_objectives;
    const names = { fixture_calendar:"Meccskínálat", current_odds:"Aktuális szorzók", api_football_match_history:"Korábbi mérkőzések", the_stats_match_history:"Korábbi mérkőzések · TheStats", numeric_models:"Numerikus modellek", numeric_model_history:"Modellek történeti bemenete", daily_historical_form:"2×2×3 történeti alátámasztás", local_odds_history:"Ármozgás", the_stats_team_xg:"Csapat xG", the_stats_player_stats:"Játékosadatok", the_stats_availability:"Sérülések és kezdők", open_meteo_weather:"Időjárás", motivation_and_news:"Motiváció és hírek", astra_codex_candidate_review:"Codex ellenőrzés", grok_package_review:"Grok csomagellenőrzés", settled_performance_feedback:"Eredmény-visszacsatolás", the_stats_and_football_data_archives:"Historikus archívum", the_stats_current_odds:"TheStats szorzó kontroll" };
    const statusNames = { USED_DIRECT:"Közvetlenül használva", USED_RANKING:"Rangsorolásban használva", USED_REVIEW:"Ellenőrzésben használva", STORED_ONLY:"Csak tárolva", MISSING:"Hiányzik" };
    const cards = [];
    if (objectives) cards.push(capacityScopeCard(objectives));
    Object.entries(usage?.sources || {}).forEach(([key, item]) => cards.push(append(el("article", "profile-status"), el("strong", "", names[key] || key), el("p", "", `${statusNames[item.status] || item.status} · ${number(item.observations)} megfigyelés`), badge(item.status, statusNames[item.status] || item.status))));
    replace("decision-data-usage", ...(cards.length ? cards : [el("p", "muted", "Az első új futás után itt külön látszik, mely adatforrás hatott a döntésre.")]));
  }
  function renderDiagnostics(report) { setText("construction-badge", report ? label(report.construction_status) : "Nincs futás"); const fixtures = diagnosticCount(report, ["fixture_count", "input_fixture_count", "fixtures_seen", "fixtures", "total_fixtures"]); const candidates = diagnosticCount(report, ["priced_candidates", "input_candidate_count", "candidate_count", "candidates"]); const eligible = diagnosticCount(report, ["eligible_candidate_count", "eligible_candidates"]); const stages = [["01 · Mérkőzések", fixtures, "A választott nap kínálata"], ["02 · Piaci jelöltek", candidates, "Valódi szorzóval párosítva"], ["03 · Összeállítható", eligible, "A választott vétószint szerint"], ["04 · Szelvények", report ? arr(report.tickets).length + arr(report.preview_tickets).length : null, `${arr(report?.preview_tickets).length} szorzóra váró előzetes`]]; replace("data-funnel", ...stages.map(([title, value, note]) => append(el("div", "funnel-step"), el("small", "", title), el("strong", "", number(value)), el("span", "", note)))); const diagnostics = diagnosticRows(report); if (state.data?.configuration?.error) diagnostics.unshift({code:"CONFIGURATION_ERROR",description:`Beállítási hiba: ${state.data.configuration.error}`,count:1}); if (!diagnostics.length) replace("diagnostics-list", el("p", "diagnostic-row muted", report ? "A riport nem tartalmaz további adatproblémát." : "Az első futás után itt látszik, hány mérkőzésből és milyen adatokkal készült az összeállítás.")); else replace("diagnostics-list", ...diagnostics.slice(0, 18).map((item) => append(el("div", "diagnostic-row"), el("strong", "", item.count > 1 ? `${item.count} előfordulás` : "Adatellenőrzés"), el("span", "", item.description), item.code !== item.description ? el("code", "", ` · ${item.code}`) : null))); renderDecisionDataUsage(report); renderProfileStatuses(report); }
  function astraSummary(data) { const astra = data?.latest?.astra || { status: data?.latest?.astra_status }; if (!data?.credentials?.codex_cli) return { title: "Codex hiányzik", note: "Beállítás: Működés → ChatGPT-bejelentkezés" }; if (astra.scope === "INHERITED_SOURCE_REVIEW") return { title: "Korábbi értékelés", note: astra.message }; const status = astra.status; if (!status || ["NOT_RUN", "SKIPPED"].includes(status)) return { title: "CLI elérhető", note: "A bejelentkezést és a modellhozzáférést teszteld." }; return { title: label(status), note: astra.message || `${astra.model || data.settings?.openai?.model || "Astra"} · ChatGPT/Codex keret` }; }
  function renderProfileStatuses(report) {
    const recommendations = recommendationsForDate();
    const profiles = recommendations.length ? recommendations : Array.isArray(report?.profiles) ? report.profiles : report?.profiles && typeof report.profiles === "object" ? Object.entries(report.profiles).map(([profile_id, info]) => ({profile_id, ...(info && typeof info === "object" ? info : {status: info})})) : [];
    replace("profile-statuses", ...profiles.map(item => {
      const model = isModel(item);
      const reason = model ? modelNote(item) : item.reason || item.message || (recommendations.length ? `${item.confidence === "HIGH" ? "Magas" : item.confidence === "MEDIUM" ? "Közepes" : "Gyenge"} bizonyítottság · ${arr(item.legs).length} mérkőzés · elmentve` : `${number(item.candidate_count ?? item.eligible_candidate_count)} jelölt`);
      return append(el("article", "profile-status"), el("strong", "", profileName(item)), el("p", "", reason), badge(model ? modelStatus(item) : item.status || item.construction_status || "NOT_RUN"));
    }));
    if (!profiles.length) replace("profile-statuses", el("p", "muted", "Profilonkénti eredmény az első teljes futás után lesz elérhető."));
  }
  function automationBadge(status) {
    const style = ["SUCCESS", "COMPLETED"].includes(status) ? "success" : ["FAILED", "BLOCKED"].includes(status) ? "danger" : ["PARTIAL", "RETRY_WAIT", "EXPIRED", "COMPLETED_WITH_WARNINGS"].includes(status) ? "warning" : "";
    return el("span", `badge ${style}`, automationLabels[status] || "Állapot nem ismert");
  }
  function berlinTimestamp(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return "—";
    const time = new Date(value);
    if (!Number.isFinite(time.getTime())) return "—";
    return new Intl.DateTimeFormat("hu-HU", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(time);
  }
  function renderAutomation() {
    const automation = state.data?.automation;
    if (!automation || typeof automation !== "object") {
      setText("automation-status", "Állapot még nem érkezett");
      replace("automation-metrics");
      replace("automation-registration", el("p", "muted", "Az automatizálás állapota még nem érhető el. Ebből nem következtetünk sem sikeres beállításra, sem hibára."));
      replace("automation-current"); replace("automation-stages");
      replace("automation-history", el("p", "muted", "Futástörténet még nem érkezett."));
      return;
    }
    const schedule = automation.schedule || {};
    const registrationLabels = { NOT_REGISTERED: "Nincs regisztrálva", REGISTERED: schedule.enabled === false ? "Regisztrált · letiltva" : "Regisztrált feladat", ERROR: "Regisztráció ellenőrzendő" };
    setText("automation-status", registrationLabels[schedule.status] || "Regisztráció nem ismert");
    const time = typeof automation.scheduled_time === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(automation.scheduled_time) ? automation.scheduled_time : "—";
    const catchup = typeof automation.catchup_until === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(automation.catchup_until) ? automation.catchup_until : "—";
    replace("automation-metrics", metric("Következő tervezett időpont", berlinTimestamp(automation.next_run_at), `Napi ${time} · Europe/Berlin`), metric("Következő elemzés cél-napja", validDate(automation.next_target_date) ? automation.next_target_date : "—", "Nem azonos a futás indításának napjával"), metric("Utolsó sikeres futás", berlinTimestamp(automation.last_success_at), "A hiányzó időpont nem jelent sikeres futást"), metric("Pótlási időablak vége", catchup, "Europe/Berlin · a futási szabály szerint"));
    replace("automation-registration", el("strong", "", registrationLabels[schedule.status] || "Regisztráció nem ismert"), el("p", "", `Utolsó ellenőrzés: ${berlinTimestamp(schedule.verified_at)}. Ez az ellenőrzés pillanatképe, nem folyamatos Windows-állapotfigyelés.`), el("small", "", `Ellenőrzési ciklus: ${finiteModelNumber(schedule.heartbeat_minutes) && schedule.heartbeat_minutes > 0 ? `${number(schedule.heartbeat_minutes)} perc` : "—"}. ${schedule.enabled === true ? "A feladat engedélyezve." : schedule.enabled === false ? "A feladat le van tiltva." : "Az engedélyezés állapota nem ismert."}`));
    const job = automation.current_job;
    if (!job || typeof job !== "object") {
      replace("automation-current", el("p", "muted", automation.store_status === "AUTOMATION_STORE_UNAVAILABLE" ? "Az ütemezett futások helyi naplója nem olvasható. Ellenőrzés szükséges; ez nem üres vagy sikeres futástörténet." : "Még nincs aktuális ütemezett futás. A fenti időpont terv, nem végrehajtási visszaigazolás."));
      replace("automation-stages");
    } else {
      replace("automation-current", append(el("div", "automation-job-heading"), el("h3", "", `Futás cél-napja: ${validDate(job.target_date) ? job.target_date : "—"}`), automationBadge(job.status)), el("p", "muted", `Ütemezési nap: ${validDate(job.slot_date) ? job.slot_date : "—"} · Frissítve: ${berlinTimestamp(job.updated_at)}`), el("p", "", `Következő újrapróbálás: ${berlinTimestamp(job.next_retry_at)}`));
      const stages = arr(job.stages);
      replace("automation-stages", ...Object.entries(automationStages).map(([name, title], index) => {
        const stage = stages.find(item => item?.name === name);
        const card = append(el("article", "automation-stage"), el("small", "automation-stage-index", `0${index + 1}`), el("h4", "", title), automationBadge(stage?.status));
        append(card, el("p", "", stage ? automationStageNotes[stage.status] || "A szakasz állapota nem értelmezhető; nem tekintjük sikeresnek." : "Ehhez a szakaszhoz még nincs állapotadat."), el("small", "", `Próbálkozás: ${finiteModelNumber(stage?.attempts) ? number(stage.attempts) : "—"} / ${finiteModelNumber(stage?.max_attempts) ? number(stage.max_attempts) : "—"}`), el("small", "", `Indítás: ${berlinTimestamp(stage?.last_started_at)}`), el("small", "", `Befejezés: ${berlinTimestamp(stage?.last_finished_at)}`));
        const reasons = { AUTH_REQUIRED: "Hiányzó vagy elutasított hozzáférés; kézi ellenőrzés szükséges.", PROVIDER_LIMIT: "A szolgáltatói keret korlátozott; ma nincs újabb automatikus próbálkozás.", DAILY_REQUEST_BUDGET: "A napi munkához engedélyezett adatlekérési keret elfogyott.", UNKNOWN_EXECUTION_REQUIRES_REVIEW: "Megszakadt vagy nem menthető elemzés; az ismételt AI-fogyasztás elkerülésére kézi ellenőrzés kell.", CHECKPOINT_REQUIRES_REVIEW: "A folytatási mentés nem olvasható; nem indult újragenerálás.", STRATEGY_CHANGED_DURING_RETRY: "Közben megváltozott a stratégia; a napi verziókat nem keverjük.", RESULT_REFRESH_FAILED: "Egyes eredményadatok nem érkeztek meg.", TEMPORARY_FAILURE: "Átmeneti szolgáltatói vagy helyi hiba.", EXISTING_FREEZE_PRESERVED: "A korábban rögzített napi csomag változatlan maradt.", NO_RECOMMENDATIONS: "Nem volt archiválható választás; a teljes futásjelentés megmaradt.", KICKOFF_PASSED: "Már elkezdett mérkőzés miatt nem készült utólagos freeze.", INVALID_CONFIGURATION_OR_DATA: "Beállítási vagy adatellenőrzési hiba; kézi ellenőrzés kell." };
        const reason = reasons[stage?.result?.reason] || reasons[stage?.reason];
        if (reason) card.append(el("p", "", reason));
        if (name === "settlement" && finiteModelNumber(stage?.result?.football_calls)) card.append(el("p", "", `${number(stage.result.football_calls)} eredménylekérés · ${number(stage.result.remaining_fixtures)} további ellenőrzés a következő napi munkákra.`));
        if (name === "generation" && finiteModelNumber(stage?.result?.profile_count)) card.append(el("p", "", `${number(stage.result.profile_count)} módszer állapota mentve · ${number(stage.result.recommendations)} választást tartalmazó ajánlat.`));
        if (stage?.next_retry_at) card.append(el("small", "", `Újrapróbálás: ${berlinTimestamp(stage.next_retry_at)}`));
        return card;
      }));
    }
    const jobs = arr(automation.jobs);
    replace("automation-history", jobs.length ? summaryTable(jobs.slice(0, 14).map(item => [validDate(item.slot_date) ? item.slot_date : "—", validDate(item.target_date) ? item.target_date : "—", automationLabels[item.status] || "Állapot nem ismert", berlinTimestamp(item.updated_at), berlinTimestamp(item.next_retry_at)]), ["Ütemezési nap", "Elemzés cél-napja", "Állapot", "Frissítve · Berlin", "Újrapróbálás · Berlin"]) : el("p", "muted", "Még nincs mentett ütemezett futás."));
  }
  const learningNumber = (value) => finiteModelNumber(value) ? number(value) : "—";
  const learningCount = (value) => Number.isInteger(value) && value >= 0 ? number(value) : "—";
  const learningPercent = (value) => finiteModelNumber(value) && value >= 0 && value <= 100 ? `${number(value)}%` : "—";
  const learningText = (value, fallback = "Nincs leírás") => typeof value === "string" && value.trim() ? value.slice(0, 4000) : fallback;
  const learningReference = (value) => typeof value === "string" && /^[A-Za-z0-9_.:|/-]{1,160}$/.test(value) ? value : "Azonosító nem jeleníthető meg";
  function learningMetricDetails(metrics, depth = 0) {
    const list = el("dl", "learning-evidence-metrics");
    if (!metrics || typeof metrics !== "object" || depth > 2) return el("p", "muted", "Nincs megjeleníthető mutató.");
    Object.entries(metrics).forEach(([key, value]) => {
      if (!Object.hasOwn(learningMetricLabels, key)) return;
      let content;
      if (key === "bins" && Array.isArray(value)) content = value.length ? append(el("div", "table-scroll"), summaryTable(value.map(bin => [`${probabilityText(bin.lower)} – ${probabilityText(bin.upper)}`, learningCount(bin.count), probabilityText(bin.mean_prediction), probabilityText(bin.observed_rate)]), ["Becslési tartomány", "Minta", "Átlagos becslés", "Megfigyelt találat"])) : el("span", "muted", "Nincs tartományonkénti adat");
      else if (["earliest_date", "latest_date"].includes(key)) content = el("span", "", validDate(value) ? value : "—");
      else if (value && typeof value === "object" && !Array.isArray(value)) content = learningMetricDetails(value, depth + 1);
      else content = el("span", "", typeof value === "boolean" ? value ? "igen" : "nem" : learningNumber(value));
      append(list, el("dt", "", learningMetricLabels[key]), append(el("dd"), content));
    });
    return list.children.length ? list : el("p", "muted", "A bizonyítékhoz nincs megjeleníthető numerikus mutató.");
  }
  function learningBreakdown(title, rows, dimension) {
    const block = append(el("section", "learning-breakdown"), el("h4", "", title));
    if (!arr(rows).length) return append(block, el("p", "muted", "Ehhez a naplóhoz nincs részletes bontás."));
    const headers = [title, "Eldőlt szelvény", "Szelvénytalálat", "Eldőlt meccstipp", "Meccstalálat"];
    const table = summaryTable(rows.map(row => [dimension(row), learningCount(row.tickets?.decided), learningPercent(row.tickets?.hit_rate_pct), learningCount(row.legs?.decided), learningPercent(row.legs?.hit_rate_pct)]), headers);
    return append(block, append(el("div", "table-scroll"), table));
  }
  function renderLearning() {
    const latest = state.data?.learning;
    const reports = arr(state.data?.learning_history).filter(item => item && typeof item === "object" && typeof item.report_id === "string");
    const selectedId = $("learning-report").value;
    const unique = [...new Map(reports.filter(item => item.report_id !== latest?.report_id).map(item => [item.report_id, item])).values()].slice(0, 14);
    const latestOption = el("option", "", latest ? `Legfrissebb · ${berlinTimestamp(latest.created_at)}` : "Még nincs jelentés"); latestOption.value = "";
    replace("learning-report", latestOption, ...unique.map(item => { const option = el("option", "", `${validDate(item.date) ? item.date : "Ismeretlen nap"} · ${learningStatuses[item.status] || "Állapot nem ismert"}`); option.value = item.report_id; return option; }));
    $("learning-report").value = unique.some(item => item.report_id === selectedId) ? selectedId : "";
    const report = $("learning-report").value ? unique.find(item => item.report_id === $("learning-report").value) : latest;
    const scope = Object.hasOwn(learningScopes, $("learning-scope").value) ? $("learning-scope").value : "research";
    $("learning-scope").value = scope;
    if (!report || typeof report !== "object") {
      const unreadable = state.data?.learning_error === "LEARNING_REPORT_UNAVAILABLE";
      setText("learning-status", unreadable ? "Ellenőrzés szükséges" : "Még nincs jelentés");
      setText("learning-summary", unreadable ? "A mentett tanulási jelentés nem olvasható vagy nem ellenőrizhető. A helyi adatbázis ellenőrzése szükséges; ezt nem tekintjük üres történetnek." : "A tanulási jelentés még nem készült el. Nem állítunk elő helyette kitalált mintát vagy teljesítményszámot.");
      setText("learning-period", "A havi teljesítménynézet ettől függetlenül használható.");
      setText("learning-sample-note", "Mintanagyság és bizonyíték még nem áll rendelkezésre.");
      ["learning-agent-status", "learning-metrics", "learning-outcomes", "learning-financial", "learning-findings", "learning-proposals", "learning-breakdowns", "learning-evidence", "learning-coverage", "learning-limits"].forEach(id => replace(id));
      return;
    }
    setText("learning-status", learningStatuses[report.status] || "Jelentésállapot nem ismert");
    setText("learning-summary", learningText(report.summary, "A jelentéshez nem érkezett összefoglaló."));
    const coverage = report.coverage || {};
    setText("learning-period", `Teljes elérhető történet: ${validDate(coverage.earliest_date) ? coverage.earliest_date : "—"} – ${validDate(coverage.latest_date) ? coverage.latest_date : "—"}. Vizsgálati időhatár: ${berlinTimestamp(report.as_of)}. Jelentés készült: ${berlinTimestamp(report.created_at)}.`);
    const llm = report.llm || {};
    const llmLabels = { COMPLETE: "Astra értékelése elkészült", SKIPPED_NO_DATA: "Astra: adathiány miatt kihagyva", UNAVAILABLE: "Astra értékelése nem elérhető", INVALID_RESPONSE: "Astra válasza nem volt ellenőrizhető", INTERRUPTED: "Astra értékelése megszakadt" };
    replace("learning-agent-status", el("strong", "", llmLabels[llm.status] || "Astra-értékelés állapota nem ismert"), el("p", "", `Mért hívásszám: ${learningCount(llm.usage?.requests)} · Mért összes token: ${learningCount(llm.usage?.total_tokens)}. A statisztikák a naplóból készülnek; az értékelés nem írja át őket.`));
    const metrics = report.metrics?.[scope];
    const tickets = metrics?.tickets || {};
    const legs = metrics?.legs || {};
    setText("learning-sample-note", !metrics ? `${learningScopes[scope]}: ehhez a naplóhoz nincs összegzés.` : metrics.small_sample === true ? `${learningScopes[scope]} · Kis minta: ${learningCount(tickets.decided)} eldőlt szelvény, ${learningCount(legs.decided)} eldőlt meccstipp. A minták feltáró jellegűek; nem igazolnak stabil előnyt vagy jövőbeli nyereséget.` : `${learningScopes[scope]} · ${learningCount(tickets.decided)} eldőlt szelvény, ${learningCount(legs.decided)} eldőlt meccstipp. ${metrics.small_sample === false ? "A mintanagyság önmagában nem bizonyít jövőbeli pontosságot; a közös meccsek nem független megfigyelések." : "A mintanagyság minősítése nem ismert; az eredmény óvatosan értelmezendő."}`);
    replace("learning-metrics", metric("Naplózott szelvény", learningCount(tickets.total), `${learningCount(tickets.decided)} eldőlt`), metric("Szelvénytalálat", learningPercent(tickets.hit_rate_pct), `${learningCount(tickets.won)} nyert / ${learningCount(tickets.lost)} vesztett`), metric("Naplózott meccstipp", learningCount(legs.total), `${learningCount(metrics?.unique_fixtures)} egyedi mérkőzés`), metric("Meccstalálat", learningPercent(legs.hit_rate_pct), `${learningCount(legs.won)} nyert / ${learningCount(legs.lost)} vesztett`));
    replace("learning-outcomes", summaryTable([["Szelvény", ...["decided", "won", "lost", "void", "pending", "review"].map(key => learningCount(tickets[key]))], ["Meccstipp", ...["decided", "won", "lost", "void", "pending", "review"].map(key => learningCount(legs[key]))]], ["Mérési egység", "Eldőlt", "Nyert", "Vesztett", "Érvénytelen", "Függő", "Ellenőrizendő"]));
    const financial = metrics?.financial || {};
    if (scope === "actual" && financial.available === true) replace("learning-financial", append(el("div", "metric-grid"), metric("Lezárt tényleges tét", finiteModelNumber(financial.settled_stake_eur) ? money(financial.settled_stake_eur) : "—", "Csak megjátszottnak jelölt tétek"), metric("Tényleges visszafizetés", finiteModelNumber(financial.payout_eur) ? money(financial.payout_eur) : "—", "A lezárt fogadásokból"), metric("Tényleges nettó eredmény", finiteModelNumber(financial.net_eur) ? money(financial.net_eur) : "—", "API- és szolgáltatási díjak nélkül"), metric("Hozam a lezárt téten", finiteModelNumber(financial.roi_pct) ? `${number(financial.roi_pct)}%` : "—", "Múltbeli eredmény, nem nyereségígéret")));
    else replace("learning-financial", el("p", "chart-footnote", scope === "actual" ? "Ehhez a jelentéshez nincs értékelhető tényleges pénzügyi eredmény. A hiányzó adat nem 0 €." : "Ebben a naplóban nincs tényleges pénzügyi eredmény: nem rendelünk kitalált tétet vagy nyereséget a kutatási és rögzített ajánlatokhoz."));
    const evidence = arr(report.evidence).filter(item => item && typeof item === "object");
    const evidenceMap = new Map(evidence.map(item => [item.evidence_id, item]));
    const relevantEvidence = item => item.scope === scope || ["all", "global"].includes(item.scope);
    const related = item => arr(item.evidence_ids).some(id => evidenceMap.has(id) && relevantEvidence(evidenceMap.get(id)));
    const evidenceLabel = item => arr(item.evidence_ids).filter(id => evidenceMap.has(id)).map(id => { const ev = evidenceMap.get(id); const name = ev.kind === "PROFILE" ? profileName({profile_id: ev.label}) : learningText(ev.label, "Naplóadat"); return `${learningEvidenceKinds[ev.kind] || "Naplóadat"}: ${name} · ${learningReference(id)}`; }).join(" / ") || "Nincs ellenőrzött hivatkozás";
    const findings = arr(report.findings).filter(item => item && related(item));
    replace("learning-findings", ...(findings.length ? findings.map(item => append(el("article", "learning-card"), badge(item.severity === "WARNING" ? "PARTIAL" : "RESEARCH_ONLY", item.severity === "WARNING" ? "Figyelmeztetés" : "Megfigyelés"), el("h4", "", learningText(item.title, "Megfigyelés")), el("p", "", learningText(item.detail)), el("small", "learning-evidence-reference", `Bizonyíték: ${evidenceLabel(item)}`))) : [el("p", "muted", "Ehhez a naplóhoz nincs bizonyítékkal összekapcsolt megállapítás. Ez önmagában nem bizonyít hibamentességet.")]));
    const proposals = arr(report.recommendations).filter(item => item && item.status === "PROPOSAL_ONLY" && related(item));
    replace("learning-proposals", ...(proposals.length ? proposals.map(item => append(el("article", "learning-card learning-proposal"), badge("RESEARCH_ONLY", "Javaslat · nincs alkalmazva"), el("h4", "", learningText(item.title, "Ellenőrizendő javaslat")), el("p", "", learningText(item.detail)), el("small", "", `${item.source === "codex" ? "Astra által kiválasztott" : item.source === "deterministic" ? "Szabályalapú" : "Nem besorolt"} javaslat · Prioritás: ${{HIGH:"magas",MEDIUM:"közepes",LOW:"alacsony"}[item.priority] || "nem ismert"}`), el("small", "learning-evidence-reference", `Bizonyíték: ${evidenceLabel(item)}`))) : [el("p", "muted", "Nincs megjeleníthető, bizonyítékhoz kötött javaslat. A rendszer nem változtat automatikusan a stratégián.")]));
    replace("learning-breakdowns", learningBreakdown("Profil", metrics?.by_profile, row => profileName(row)), learningBreakdown("Piac", metrics?.by_market, row => learningText(row.market, "Ismeretlen piac")), learningBreakdown("Verzió", metrics?.by_version, row => learningText(row.version, "Ismeretlen verzió")));
    const scopedEvidence = evidence.filter(relevantEvidence);
    replace("learning-evidence", ...(scopedEvidence.length ? scopedEvidence.map(item => {
      const card = append(el("article", "learning-card"), el("small", "learning-evidence-reference", learningReference(item.evidence_id)), el("h4", "", learningText(item.label, "Naplóból számított bizonyíték")), el("p", "muted", `${learningScopes[item.scope] || "Közös adatminőség"} · ${learningEvidenceKinds[item.kind] || "Naplóadat"}`), learningMetricDetails(item.metrics));
      const refs = arr(item.source_refs);
      const refCount = Number.isInteger(item.source_ref_count) ? item.source_ref_count : refs.length;
      const details = append(el("details"), el("summary", "", `Forrásrekordok · ${refCount} hivatkozás`));
      if (refs.length) details.append(append(el("ul", "learning-source-refs"), ...refs.slice(0, 50).map(ref => el("li", "", `${learningReference(ref.table)} / ${learningReference(ref.key)} · hash: ${learningReference(ref.hash)}`))));
      else details.append(el("p", "muted", "Ehhez a bizonyítékhoz nincs külön forrásrekord-hivatkozás."));
      if (refCount > 50) details.append(el("p", "muted", "Itt az első 50 hivatkozás látható; a teljes lista a mentett jelentés része."));
      card.append(details); return card;
    }) : [el("p", "muted", "Ehhez a naplóhoz még nincs bizonyítéklista.")]));
    replace("learning-coverage", learningMetricDetails(coverage), el("p", "chart-footnote", `Szorzó nélküli meccstippek: ${learningCount(metrics?.missing_odds_legs)} · Egyedi választások: ${learningCount(metrics?.unique_selections)}. A több profilban szereplő mérkőzések eredménye összefügghet.`));
    replace("learning-limits", ...arr(report.limits).filter(item => typeof item === "string").map(item => el("li", "", learningText(item))));
  }
  const nemotronStatuses = { COMPLETE: "Értékelés elkészült", PARTIAL: "Részleges értékelés", UNAVAILABLE: "Értékelés nem elérhető", SKIPPED_NO_DATA: "Nincs elég adat az értékeléshez", INTERRUPTED: "Megszakadt · ellenőrizendő", FAILED: "Sikertelen értékelés", ERROR: "Sikertelen értékelés", INVALID_RESPONSE: "A válasz ellenőrzése sikertelen", DISABLED: "Értékelés kikapcsolva", RUNNING: "Értékelés folyamatban", RESERVED: "Értékelés lefoglalva" };
  const nemotronTaskNames = { risk: "Szelvénykockázatok és átfedések", data_quality: "Adatminőség és szorzólefedettség", research_learning: "Történeti hibaminták és tanulási javaslatok" };
  function nemotronReport(report, title) {
    const section = append(el("section", "nemotron-report"), append(el("div", "nemotron-report-heading"), el("h3", "", title), badge(report.status === "COMPLETE" ? "COMPLETE" : "PARTIAL", nemotronStatuses[report.status] || "Ismeretlen állapot")));
    append(section, el("p", "nemotron-reference", `Vizsgált napló: ${learningReference(report.subject_id)} · Adatok időhatára: ${berlinTimestamp(report.evidence_as_of)} · Mentés: ${berlinTimestamp(report.created_at)}`), el("p", "nemotron-reference", `Modell: ${learningText(report.model, "nem ismert")} · Szabályverzió: ${learningText(report.policy_version, "nem ismert")}`));
    if (report.message) section.append(el("p", "nemotron-message", learningText(report.message)));
    if (report.automatic_changes !== false) section.append(el("p", "notice", "A jelentés nem igazolja a kizárólag tanácsadó működést. Ne tekintsd végrehajtott vagy jóváhagyott változtatásnak."));
    const usage = report.usage || {};
    const knownUsage = `Naplózott kérések: ${learningCount(usage.requests)} · Ismert bemeneti token: ${learningCount(usage.known_input_tokens)} · Ismert kimeneti token: ${learningCount(usage.known_output_tokens)}.`;
    section.append(el("p", "nemotron-usage", `${knownUsage} ${usage.usage_complete === true ? "A jelentés szerint a használati adatok teljesek." : "Hiányos használati adat; a hiányzó rész nem nulla."} Pénzbeli költség ebből nem állapítható meg.`));
    const unknown = Array.isArray(usage.unknown_usage_tasks) ? usage.unknown_usage_tasks.length : usage.unknown_usage_tasks;
    if (Number.isInteger(unknown) && unknown > 0) section.append(el("p", "nemotron-usage", `Ismeretlen használatú részfeladat: ${learningCount(unknown)}.`));
    const tasks = arr(report.tasks).filter(task => task && typeof task === "object");
    if (!tasks.length) section.append(el("p", "muted", "Ehhez az állapothoz nincs mentett részfeladat vagy javaslat. Nem pótoljuk kitalált értékeléssel."));
    tasks.forEach(task => {
      const detail = append(el("details", "nemotron-task"), el("summary", "", `${nemotronTaskNames[task.name] || learningText(task.name, "Elemző részfeladat")} · ${nemotronStatuses[task.status] || "Állapot nem ismert"}`));
      const proposals = arr(task.proposals).filter(proposal => proposal && typeof proposal === "object");
      const trustedTask = task.status === "COMPLETE";
      if (!trustedTask) detail.append(el("p", "muted", "Ez a részfeladat nem fejeződött be ellenőrzötten; nem jelenítünk meg tőle kész javaslatot."));
      else if (!proposals.length) detail.append(el("p", "muted", "A részfeladat nem adott megjeleníthető javaslatot. Ez nem bizonyít hibamentességet."));
      else proposals.forEach(proposal => {
        const references = arr(proposal.evidence_ids).filter(id => typeof id === "string" && id.trim());
        if (!references.length) { detail.append(el("p", "muted", "Egy javaslat bizonyítékazonosító nélkül érkezett; nem jelenítjük meg alátámasztott következtetésként.")); return; }
        detail.append(append(el("article", "nemotron-proposal"), el("small", "", `Javaslat · nincs alkalmazva · Prioritás: ${{HIGH:"magas",MEDIUM:"közepes",LOW:"alacsony"}[proposal.priority] || "nem ismert"}`), el("h4", "", learningText(proposal.title, "Ellenőrizendő javaslat")), el("p", "", learningText(proposal.detail)), el("small", "nemotron-reference", `Hivatkozott bizonyíték: ${references.map(learningReference).join(" · ")}`)));
      });
      section.append(detail);
    });
    return section;
  }
  function renderNemotron() {
    const data = state.nemotron;
    setText("nemotron-feedback", state.nemotronError);
    $("nemotron-refresh").disabled = state.nemotronFetching;
    if (!data) {
      setText("nemotron-status", state.nemotronError ? "Nem elérhető" : "Még nincs lekért állapot");
      setText("nemotron-connection", "A Codex helyi beállítása és a sikeres modellkapcsolat még nem ellenőrizhető ebből a nézetből.");
      setText("nemotron-enabled", "Automatikus részvétel: még nincs lekért állapot.");
      setText("nemotron-model", "Modell: —");
      setText("club-nemotron-status", state.nemotronError ? "Codex: az értékelési nézet jelenleg nem érhető el." : "Codex: még nincs lekért értékelési állapot.");
      replace("nemotron-reports");
      return;
    }
    const report = data.report && typeof data.report === "object" ? data.report : null;
    const learning = data.learning && typeof data.learning === "object" ? data.learning : null;
    const successful = [report, learning].some(item => item && arr(item.tasks).some(task => task?.status === "COMPLETE"));
    setText("nemotron-enabled", data.enabled === true ? "Automatikus részvétel: bekapcsolva. Az új futások és napi tanulási körök a megadott kereten belül kérhetnek értékelést; egy régi, lezárt futáshoz a hiányzó értékelés nem jelent hibát." : data.enabled === false ? "Automatikus részvétel: kikapcsolva. A Codex-bejelentkezés megléte önmagában nem kapcsolja be az értékeléseket; a mentett jelentések továbbra is olvashatók." : "Automatikus részvétel: az állapota nem érkezett meg; a helyi hozzáférés meglétéből nem következtetünk bekapcsolt működésre.");
    setText("nemotron-connection", data.configured === true ? `Helyi hozzáférés beállítva. ${successful ? "Van mentett sikeres részfeladat; ez korábbi modellválasz, nem folyamatos elérhetőségi garancia." : "Sikeres modellválasz még nincs igazolva a lekért jelentésekben."}` : "Helyi hozzáférés nincs beállítva vagy nem ellenőrizhető. A korábban mentett értékelés ettől még megtekinthető.");
    setText("nemotron-model", `Modell: ${learningText(data.model, "nem ismert")} · Szabályverzió: ${learningText(data.policy_version, "nem ismert")}`);
    setText("nemotron-status", report ? nemotronStatuses[report.status] || "Ismeretlen állapot" : nemotronStatuses[data.status] || "Még nincs futásértékelés");
    if (!state.nemotronError && data.status === "UNAVAILABLE") setText("nemotron-feedback", "A mentett Codex-értékelések most nem olvashatók. A helyi beállítás önmagában nem igazolja a sikeres működést.");
    setText("club-nemotron-status", `Codex második vélemény: ${report ? nemotronStatuses[report.status] || "állapota nem ismert" : "még nincs mentett futásértékelés"}.`);
    replace("nemotron-reports", report ? nemotronReport(report, "Szelvények és közös kockázatok") : el("p", "muted", "Ehhez a futáshoz még nincs mentett Codex-értékelés. A már kiadott szelvényeket ez nem módosítja."), learning ? nemotronReport(learning, "Legfrissebb történeti tanulási értékelés") : el("p", "muted", "Még nincs mentett Codex tanulási értékelés."));
  }
  async function loadNemotron(force = false) {
    const runId = typeof state.data?.latest?.run_id === "string" ? state.data.latest.run_id : "";
    const key = JSON.stringify([runId, state.date, state.data?.application_version]);
    if (state.nemotronFetching || (!force && key === state.nemotronKey && (state.nemotronMissing || Date.now() - state.nemotronChecked < 30000))) return;
    const request = ++state.nemotronRequest;
    state.nemotronFetching = true; $("nemotron-refresh").disabled = true;
    try {
      const response = await fetch(`/api/codex-advisory${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`, {credentials:"same-origin", headers:{Accept:"application/json"}, cache:"no-store"});
      if (!response.ok) { state.nemotronMissing = response.status === 404; throw new Error("NEMOTRON_UNAVAILABLE"); }
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data) || typeof data.configured !== "boolean") throw new Error("INVALID_NEMOTRON_STATUS");
      if (request !== state.nemotronRequest || key !== JSON.stringify([typeof state.data?.latest?.run_id === "string" ? state.data.latest.run_id : "", state.date, state.data?.application_version])) return;
      state.nemotron = data; state.nemotronError = ""; state.nemotronMissing = false;
    } catch {
      if (request !== state.nemotronRequest || key !== JSON.stringify([typeof state.data?.latest?.run_id === "string" ? state.data.latest.run_id : "", state.date, state.data?.application_version])) return;
      state.nemotron = null;
      state.nemotronError = state.nemotronMissing ? "Az értékelési végpont nem elérhető. Régi Arthur-folyamat esetén újraindítás szükséges: zárd be az Arthur szerverablakát, majd nyisd meg újra a meglévő parancsikont." : "A mentett Codex-állapot nem olvasható. Ez nem bizonyítja a modell sikeres vagy sikertelen futását. Próbáld újra a mentett értékelések frissítését.";
    } finally {
      state.nemotronFetching = false; state.nemotronKey = key; state.nemotronChecked = Date.now(); $("nemotron-refresh").disabled = false;
      const signature = JSON.stringify([state.nemotron, state.nemotronError]);
      if (signature !== state.nemotronSignature) { state.nemotronSignature = signature; renderNemotron(); }
    }
  }
  const grokStatuses = { COMPLETE:"Értékelés elkészült", PARTIAL:"Részleges értékelés", RUNNING:"Értékelés folyamatban", RESERVED:"Értékelés előkészítve", DISABLED:"Kikapcsolva", NOT_CONFIGURED:"Hozzáférés még nincs beállítva", UNAVAILABLE:"Értékelés nem elérhető", FAILED:"Értékelés sikertelen", INTERRUPTED:"Értékelés megszakadt", SKIPPED_NO_DATA:"Nincs vizsgálható szelvény", NO_ELIGIBLE_FIXTURES:"Nincs vizsgálható szelvény", PACKET_TOO_LARGE:"A napi csomag meghaladja a vizsgálati korlátot", BUDGET_EXHAUSTED:"A beállított keret elfogyott", NOT_RUN:"Még nincs mentett értékelés" };
  Object.assign(grokStatuses, {NO_DATA:"Még nincs mentett értékelés", BUSY:"Értékelés folyamatban", BUDGET:"A beállított keret nem enged új hívást", CACHED:"Korábbi értékelés átvéve"});
  function grokSourceUrl(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password || url.port || !["x.com","www.x.com","twitter.com","www.twitter.com"].includes(url.hostname) || !/^\/(?:[A-Za-z0-9_]{1,15}|i\/web)\/status\/\d+\/?$/.test(url.pathname)) return null;
      return `${url.origin}${url.pathname}`;
    } catch { return null; }
  }
  function grokFixtureName(id) {
    const report = reportForDate();
    const fixtures = [...arr(report?.fixtures), ...reportTickets().flatMap(ticket => arr(ticket.legs))];
    const fixture = fixtures.find(item => item && String(item.fixture_id) === String(id));
    return fixture ? `${learningText(fixture.home_team, "Hazai")} – ${learningText(fixture.away_team, "Vendég")}` : `Mérkőzés: ${learningText(String(id ?? ""), "nem ismert")}`;
  }
  function grokCritiques(result) {
    const claimIds = new Set(arr(result?.claims).filter(item => item && item.provenance_status === "CITATION_AND_TIMESTAMP_CHECKED" && grokSourceUrl(item.source_url)).map(item => item.claim_id));
    return arr(result?.critiques).filter(item => {
      if (!item || typeof item !== "object") return false;
      if (item.verdict) return ["KEEP","QUESTIONABLE","INSUFFICIENT_EVIDENCE"].includes(item.verdict) && typeof item.strengths === "string" && typeof item.risks === "string";
      return !isDaily(item) && typeof item.note === "string" && ["REVIEW","NO_NEW_EVIDENCE","CONTEXT_ONLY"].includes(item.disposition) && (item.disposition === "NO_NEW_EVIDENCE" || arr(item.claim_ids).some(id => claimIds.has(id)));
    }).slice(0, 40);
  }
  function grokExclusionReason(code) {
    return {PREMATCH_LOCKOUT:"Kezdés előtti 15 perces zárolás, vagy a mérkőzés már elkezdődött", UNKNOWN_PROFILE:"Nem azonosítható profil", AMBIGUOUS_PROFILE:"A profil szelvénye nem azonosítható egyértelműen", NO_COMPLETE_TICKET:"Nincs teljes szelvény", INVALID_LEG:"Hiányos vagy nem ellenőrizhető meccsadat"}[code] || "Nem teljesültek a vizsgálat feltételei";
  }
  function renderGrokRepair() {
    const summary = reportForDate()?.grok_review;
    if (!summary || summary.mode !== "ONE_BATCH_CONDITIONAL_REPAIR") { replace("grok-repair"); return; }
    const names = values => arr(values).filter(value => typeof value === "string").map(profile_id => profileName({profile_id})).join(", ");
    const changed = names(summary.changed_profiles);
    const reasons = {CACHED_REVIEW_TOO_OLD_FOR_REPAIR:"A korábbi értékelés már túl régi a módosításhoz.", NO_INDEPENDENTLY_CONFIRMED_CONCERN:"Nem volt függetlenül megerősített probléma.", NO_ACTIONABLE_CONCERN:"Nem volt javítást indokló, konkrét jelzés.", UNAVAILABLE_NO_CHANGE:"Az ellenőrzés nem fejeződött be; a csomag változatlan maradt."};
    const panel = append(el("section", "grok-repair-summary"), el("h3", "", "A kiadás előtti ellenőrzés eredménye"), el("p", "nemotron-message", `Grok: ${grokStatuses[summary.status] || "állapota nem ismert"}.${summary.concurrence ? ` Független ellenőrzés: ${grokStatuses[summary.concurrence.status] || "állapota nem ismert"}.` : ""}`), el("p", "nemotron-message", changed ? `Módosult profilok: ${changed}.` : "Ebben a körben nem változott a szelvények összetétele."));
    if (summary.repair_status && reasons[summary.repair_status]) panel.append(el("p", "nemotron-message", reasons[summary.repair_status]));
    const unresolved = arr(summary.repair?.unresolved).filter(item => item && typeof item.profile_id === "string");
    if (unresolved.length) panel.append(el("p", "nemotron-message", `Megoldatlan jelzések: ${unresolved.length} · ${names(unresolved.map(item => item.profile_id))}. Az érintett szelvények eredeti változata megmaradt.`));
    panel.append(el("small", "nemotron-reference", `Javítási körök: ${number(summary.repair_rounds)} / legfeljebb 1 · A védett 2×2×3 változatlan.`));
    replace("grok-repair", panel);
  }
  function grokReport(result) {
    const container = el("div", "grok-findings");
    const kinds = {INJURY:"Sérülés / hiányzó",LINEUP:"Kezdőcsapat",ROTATION:"Rotáció",TRAVEL:"Utazás / terhelés",COACH_STATEMENT:"Edzői nyilatkozat",OTHER:"Meccskörnyezet"};
    const claims = arr(result.claims).filter(item => item && typeof item === "object" && item.provenance_status === "CITATION_AND_TIMESTAMP_CHECKED" && typeof item.claim_id === "string" && typeof item.claim === "string" && grokSourceUrl(item.source_url)).slice(0, 30);
    const news = append(el("section", "nemotron-report"), el("h3", "", "Hivatkozott hírek"));
    if (!claims.length) news.append(el("p", "muted", "Még nincs megjeleníthető, hivatkozással és időbélyeggel ellenőrzött hír."));
    claims.forEach(item => {
      const article = append(el("article", "nemotron-task"), el("h4", "", grokFixtureName(item.fixture_id)), el("small", "nemotron-reference", `${kinds[item.kind] || "Meccskörnyezet"} · A hír tartalma további ellenőrzést igényel`), el("p", "nemotron-message", learningText(item.claim)));
      const source = el("a", "grok-source", `${learningText(item.author, "X-bejegyzés")} · Forrás megnyitása ↗`);
      source.href = grokSourceUrl(item.source_url); source.target = "_blank"; source.rel = "noopener noreferrer";
      append(article, source, el("small", "nemotron-reference", `Közzététel: ${berlinTimestamp(item.published_at)} · Lekérés: ${berlinTimestamp(item.fetched_at)}`));
      news.append(article);
    });
    const critiques = grokCritiques(result);
    const opinions = append(el("section", "nemotron-report"), el("h3", "", "Szelvényenkénti mérleg"));
    if (!critiques.length) opinions.append(el("p", "muted", "Még nincs megjeleníthető szelvényértékelés."));
    critiques.forEach(item => {
      const verdict = {KEEP:"Megtartható",QUESTIONABLE:"Kérdéses",INSUFFICIENT_EVIDENCE:"Kevés bizonyíték"}[item.verdict];
      const disposition = verdict || {REVIEW:"Áttekintésre javasolt",NO_NEW_EVIDENCE:"Nincs új bizonyíték",CONTEXT_ONLY:"Kiegészítő körülmény"}[item.disposition];
      const article = append(el("article", "nemotron-task"), el("h4", "", profileName(item)), el("small", "nemotron-reference", `${disposition} · Grok-vélemény`));
      if (isDaily(item) || item.protected_profile === true) article.append(el("p", "grok-protected", "Védett 2×2×3: csak vélemény, változtatás nélkül."));
      if (item.review_validation === "PARTIAL") article.append(el("p", "nemotron-message", "A vélemény egyik hivatkozása nem ellenőrizhető; ebből automatikus módosítás nem indul."));
      if (verdict) {
        append(article, append(el("p", "nemotron-message"), el("strong", "", "Mellette: "), el("span", "", learningText(item.strengths, "Nincs közölt érv."))), append(el("p", "nemotron-message"), el("strong", "", "Ellene: "), el("span", "", learningText(item.risks, "Nincs közölt érv."))));
        const flagged = arr(item.flagged_legs).filter(leg => leg && Number.isInteger(leg.leg_index) && leg.leg_index >= 0 && typeof leg.reason === "string").slice(0, 10);
        if (flagged.length) {
          const details = append(el("details", "grok-flagged"), el("summary", "", `Kérdéses választások (${flagged.length})`));
          flagged.forEach(leg => details.append(el("p", "nemotron-message", `${leg.leg_index + 1}. választás · ${grokFixtureName(leg.fixture_id)}: ${learningText(leg.reason)}`)));
          article.append(details);
        }
      } else article.append(el("p", "nemotron-message", learningText(item.note)));
      opinions.append(article);
    });
    append(container, news, opinions);
    return container;
  }
  function renderGrok() {
    renderGrokRepair();
    const data = state.grokDay === state.date ? state.grok : null;
    const latest = data?.latest && typeof data.latest === "object" ? data.latest : null;
    const result = latest?.result && typeof latest.result === "object" ? latest.result : null;
    setText("grok-day", `${state.date || "Kiválasztott nap"} · Mentett hírek és szelvényértékelés`);
    setText("grok-feedback", state.grokDay === state.date ? state.grokError : "");
    $("grok-refresh").disabled = state.grokFetching && state.grokFetchingDay === state.date;
    const coverage = latest?.coverage || {};
    const reviewed = grokCritiques(result).length;
    const partial = result && ["COMPLETE","PARTIAL"].includes(result.status) && Number.isInteger(coverage.profile_count) && reviewed < coverage.profile_count;
    setText("grok-status", !data ? (state.grokError && state.grokDay === state.date ? "Nem elérhető" : "Még nincs lekért állapot") : grokStatuses[partial ? "PARTIAL" : latest?.status || data.status] || "Állapot ellenőrzésre vár");
    const wholePack = ["ALL_TICKETS_ONE_BATCH","ALL_NONEMPTY_TICKETS_ONE_BATCH","ALL_READY_TICKETS_ONE_BATCH"].includes(coverage.scope);
    replace("grok-meta", ...(data ? [el("span", "", data.enabled === true ? "Automatikus véleményezés: bekapcsolva" : "Automatikus véleményezés: kikapcsolva"), ...(latest ? [...(wholePack ? [el("span", "", `Profilok a csomagban: ${number(coverage.ready_profile_count)}`)] : []), el("span", "", `${wholePack ? "Értékelésre átadva" : "Korábbi részvizsgálat"}: ${number(coverage.profile_count)} szelvény · ${number(coverage.fixture_count)} külön meccs`), el("span", "", `Beérkezett értékelés: ${reviewed} / ${number(coverage.profile_count)} szelvény`), el("span", "", `Mentés: ${berlinTimestamp(latest.completed_at || latest.created_at)}`), ...(latest.cache_stale === true ? [el("span", "", "Korábbi hírkép · több mint 60 perce készült, nincs új hírlekérés")] : [])] : [])] : []));
    const excluded = arr(coverage.excluded).filter(item => item && typeof item.profile_id === "string");
    const exclusions = excluded.length ? append(el("details", "grok-excluded"), el("summary", "", `Kihagyott szelvények (${excluded.length})`)) : null;
    if (exclusions) excluded.forEach(item => exclusions.append(el("p", "nemotron-message", `${profileName(item)} · ${grokExclusionReason(item.reason_code)}`)));
    const received = new Set(grokCritiques(result).map(item => item.profile_id));
    const missing = arr(coverage.profile_ids).filter(id => typeof id === "string" && !received.has(id));
    const missingNotice = result && missing.length ? el("p", "nemotron-message", `Még nincs feldolgozható vélemény: ${missing.map(profile_id => profileName({profile_id})).join(", ")}. A szelvényeket tartalmazta az elküldött csomag.`) : null;
    replace("grok-reports", exclusions, missingNotice, result && ["COMPLETE","PARTIAL"].includes(result.status) ? grokReport(result) : el("p", "muted", latest ? "Ehhez a futáshoz még nincs megjeleníthető Grok-értékelés." : "Még nincs mentett Grok-értékelés erre a napra."));
    const usage = data?.usage;
    const usd = value => typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${new Intl.NumberFormat("hu-HU", {maximumFractionDigits:4}).format(value)} USD` : "nem ismert";
    setText("grok-usage", usage ? `Hívások ezen a napon: ${number(usage.day_requests)} / ${number(usage.max_daily_requests)} · Elszámolt keret (${validDate(usage.budget_day) ? usage.budget_day : state.date}): ${usd(usage.day_charged_usd)} / ${usd(usage.daily_limit_usd)} · A kiválasztott hónapban: ${usd(usage.month_charged_usd)} / ${usd(usage.monthly_limit_usd)}. Az elszámolt keret ismeretlen költségű hívásra félretett összeget is tartalmazhat.` : "");
  }
  async function loadGrok(force = false) {
    const day = state.date;
    if (!validDate(day) || (state.grokFetching && state.grokFetchingDay === day) || (!force && state.grokDay === day && Date.now() - state.grokChecked < 30000)) return;
    state.grokController?.abort();
    const controller = new AbortController(); state.grokController = controller;
    const request = ++state.grokRequest;
    state.grokFetching = true; state.grokFetchingDay = day; $("grok-refresh").disabled = true;
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(`/api/grok?date=${encodeURIComponent(day)}`, {credentials:"same-origin", headers:{Accept:"application/json"}, cache:"no-store", signal:controller.signal});
      if (!response.ok) throw new Error("GROK_UNAVAILABLE");
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data) || data.mode !== "SHADOW" || data.automatic_changes !== false || data.day !== day) throw new Error("INVALID_GROK_STATUS");
      if (request !== state.grokRequest || day !== state.date) return;
      state.grok = data; state.grokError = "";
    } catch {
      if (request !== state.grokRequest || day !== state.date) return;
      state.grok = null; state.grokError = "A mentett Grok-értékelés most nem olvasható. A szelvények ettől továbbra is elérhetők.";
    } finally {
      clearTimeout(timer);
      if (request === state.grokRequest) {
        state.grokFetching = false; state.grokController = null;
        if (day === state.date) { state.grokDay = day; state.grokChecked = Date.now(); }
        const signature = JSON.stringify([state.date, state.grok, state.grokError]);
        if (signature !== state.grokSignature) { state.grokSignature = signature; renderGrok(); }
        $("grok-refresh").disabled = false;
      }
    }
  }
  const teamText = (value, fallback = "Nincs közölt magyarázat") => learningText(value, fallback);
  const teamStatusBadge = (status) => badge(status === "ACTIVE" ? "READY" : status === "PROBATION" ? "DRAFT" : "PENDING", teamStatusNames[status] || "Állapot nem ismert");
  const teamMembers = () => arr(state.data?.team?.members).filter(item => item && typeof item.profile_id === "string");
  const teamCanAct = () => state.data?.team?.mode === "LIVE_PROFILE_SUPERVISION" && Array.isArray(state.data?.team?.members) && !state.teamBusy && !state.busy && !state.data?.runtime?.running;
  const teamBlank = (text) => el("p", "team-inline-empty", text);
  const teamReason = (item) => teamText(item?.reason, teamReasonNames[item?.reason_code] || teamBlockerNames[item?.reason_code] || "Ehhez a döntéshez nincs megjeleníthető indok.");
  const teamValue = (title, value) => append(el("div"), el("small", "", title), el("strong", "", value));
  function teamEvidence(ids, learningReportId) {
    const evidence = new Map(arr(state.data?.team?.latest_evaluation?.evidence).filter(item => item && typeof item.evidence_id === "string").map(item => [item.evidence_id, item]));
    const learningReport = [state.data?.learning, ...arr(state.data?.learning_history)].find(report => report && report.report_id === learningReportId);
    arr(learningReport?.evidence).filter(item => item && typeof item.evidence_id === "string").forEach(item => evidence.set(item.evidence_id, item));
    const references = arr(ids).filter(id => typeof id === "string" && evidence.has(id));
    if (!references.length) return el("p", "team-reference", "Ehhez a bejegyzéshez nincs a legfrissebb értékelésben ellenőrizhető bizonyítékhivatkozás.");
    const details = append(el("details", "team-details"), el("summary", "", `Naplóbizonyíték · ${references.length} hivatkozás`));
    references.slice(0, 8).forEach(id => {
      const item = evidence.get(id);
      const metrics = item.metrics || {};
      append(details, el("p", "team-reference", learningReference(id)), el("p", "", `${item.scope === "PROSPECTIVE_PAPER_UNIT" ? "Előre mért egységtétes papírminta" : learningScopes[item.scope] || "Naplóbizonyíték"} · ${learningCount(finiteModelNumber(metrics.tickets) ? metrics.tickets : metrics.tickets?.decided)} lezárt szelvény · ${learningCount(metrics.days)} nap · ${learningCount(metrics.unique_fixtures)} egyedi meccs. Verzió: ${teamText(item.version, "nem ismert")}.`));
      const refs = arr(item.source_refs).slice(0, 5);
      if (refs.length) details.append(el("p", "team-reference", refs.map(ref => `${learningReference(ref.table)} / ${learningReference(ref.key)}`).join(" · ")));
    });
    return details;
  }
  function teamProgress(title, value, target) {
    const row = append(el("div", "team-progress-row"), el("span", "", title), el("span", "", `${learningCount(value)} / ${learningCount(target)}`));
    const fill = el("span");
    fill.style.width = `${finiteModelNumber(value) && finiteModelNumber(target) && target > 0 ? Math.max(0, Math.min(100, value / target * 100)) : 0}%`;
    const track = append(el("div", "team-progress-track"), fill); track.setAttribute("aria-hidden", "true");
    return append(row, track);
  }
  function teamMemberCard(member, scope, policy) {
    const statusClass = {ACTIVE: "status-active", PROBATION: "status-probation", SUSPENDED: "status-suspended"}[member.status] || "";
    const card = el("article", `team-member ${statusClass}`);
    const model = modelProfiles[member.profile_id];
    const persona = clubCatalogue().find(person => person.profile_id === member.profile_id);
    append(card, append(el("header", "team-member-head"), persona ? clubAvatar(persona.profile_id, "team-persona-avatar") : el("div", "team-member-avatar", profileName(member).slice(0, 1).toUpperCase()), append(el("div"), el("h3", "", profileName(member)), el("p", "", persona ? "Fiktív tipster · közös adat, saját stílus" : model ? "Helyi numerikus módszer" : "Önálló elemzőprofil"))));
    const badges = append(el("div", "team-member-status"), teamStatusBadge(member.status));
    if (member.enabled_by_user === false) badges.append(badge("DISABLED", "Beállításból kikapcsolva"));
    if (member.metrics_current_epoch === false) badges.append(badge("PENDING", "Új ciklus · értékelésre vár"));
    if (member.qualification?.eligible === true) badges.append(badge("READY", "Belépési feltételek teljesültek"));
    card.append(badges);
    const m = member.metrics || {};
    const facts = el("div", "team-member-metrics");
    if (scope === "actual") {
      const actual = m.actual || {}, financial = actual.financial || {};
      append(facts, teamValue("Tényleges ROI", financial.available === true ? learningPercent(financial.roi_pct) : "—"), teamValue("Tényleges nettó", financial.available === true && finiteModelNumber(financial.net_eur) ? money(financial.net_eur) : "—"), teamValue("Eldőlt valódi szelvény", learningCount(actual.tickets?.decided)), teamValue("Szelvénytalálat", learningPercent(actual.tickets?.hit_rate_pct)));
    } else append(facts, teamValue(member.metrics_current_epoch === false ? "Korábbi papírhozam · 1 egység" : "Papírhozam · 1 egység / szelvény", learningPercent(m.roi_pct)), teamValue(member.metrics_current_epoch === false ? "Korábbi értékelés szelvénye" : "Értékelhető papírszelvény", learningCount(m.tickets)), teamValue("Megfigyelt nap", learningCount(m.days)), teamValue("Egyedi mérkőzés", learningCount(m.unique_fixtures)));
    card.append(facts);
    const explanation = append(el("div", "team-member-explanation"), el("strong", "", member.status === "ACTIVE" ? "Éles ajánlás ≠ tényleges fogadás" : member.status === "PROBATION" ? "Próbaajánlatok a külön próbatérben" : member.status === "SUSPENDED" ? "Jelenleg nem készít új ajánlatot" : "Tagsági állapot ellenőrzésre vár"), el("p", "", teamReason(member)));
    if (scope === "research") {
      explanation.append(el("p", "", finiteModelNumber(m.ci_low_pct) && finiteModelNumber(m.ci_high_pct) ? `Papírhozam bizonytalansági tartománya: ${learningPercent(m.ci_low_pct)} – ${learningPercent(m.ci_high_pct)}. Ez nem jövőbeli hozamígéret.` : "A papírhozam bizonytalansági tartománya még nem áll rendelkezésre."));
      if (member.metrics_current_epoch === false) explanation.append(el("p", "", "A tagsági döntés új megfigyelési ciklust indított. A látható számok az előző értékelésből származhatnak; nem az új ciklus teljesített feltételei. Az új ciklus a következő értékelésre vár."));
      else append(explanation, teamProgress("Lezárt szelvény", m.tickets, policy.min_tickets), teamProgress("Megfigyelési nap", m.days, policy.min_days), teamProgress("Egyedi meccs", m.unique_fixtures, policy.min_unique_fixtures));
    } else explanation.append(el("p", "", m.actual?.financial?.available === true ? "Csak rögzített valódi fogadások, szolgáltatási költségek levonása nélkül." : "Nincs ellenőrizhető pénzügyi mérleg. Hiányzó vagy régi, becsült árhoz nem írunk 0 € eredményt."));
    const blockers = arr(member.qualification?.blockers);
    const detail = append(el("details", "team-details"), el("summary", "", "Döntési feltételek és mérési alap"));
    if (blockers.length) detail.append(append(el("ul", "team-blockers"), ...blockers.map(code => el("li", "", teamBlockerNames[code] || "További ellenőrzési feltétel nem teljesült."))));
    else detail.append(el("p", "", member.qualification?.eligible === true ? "A vezető által ellenőrzött belépési feltételek teljesültek." : "A feltételek ellenőrzése még nem adott minősített eredményt."));
    append(detail, el("p", "", `Papírkohorsz: ${learningCount(m.total_tickets)} rögzített szelvény, ${learningCount(m.pending)} függő eredmény, ${learningCount(m.missing_odds)} hiányzó szorzó. Csak a belépés vagy újrakezdés után, azonos stratégia-verzióval kiadott döntésekből.`), el("p", "", `Megőrzött teljes kutatási előzmény: ${learningCount(m.research?.tickets?.total)} szelvény, ${learningCount(m.research?.tickets?.decided)} eldőlt. Rögzített csomagokban: ${learningCount(m.frozen?.tickets?.total)} szelvény. Ez a két kör átfedhet, nem összeadandó és nem helyettesíti az új döntési mintát.`), el("p", "team-reference", `Aktuális mérési ciklus kezdete: ${berlinTimestamp(member.evidence_since || member.started_at)} · Értékelés időhatára: ${berlinTimestamp(m.as_of)} · Verzió: ${teamText(m.version, "nem ismert")}`), teamEvidence(member.evidence_ids));
    explanation.append(detail); card.append(explanation);
    const button = el("button", "button button-secondary team-member-control", "Tagság kezelése"); button.type = "button"; button.disabled = !teamCanAct(); button.addEventListener("click", () => openTeamDecision(member.profile_id));
    append(card, append(el("footer", "team-member-foot"), el("small", "", `Állapot kezdete\n${berlinTimestamp(member.status_since)}`), button));
    return card;
  }
  function renderTeamPodium(members, policy) {
    // A good-looking trophy is not evidence. Rank and awards must be explicitly earned.
    const places = members.filter(member => member.status === "ACTIVE" && member.qualification?.eligible === true && member.podium?.earned === true && member.podium?.qualified === true && Number.isInteger(member.podium.rank) && member.podium.rank >= 1 && member.podium.rank <= 3).sort((a, b) => a.podium.rank - b.podium.rank);
    const ranks = new Set();
    const qualified = places.filter(member => { if (ranks.has(member.podium.rank)) return false; ranks.add(member.podium.rank); return true; });
    setText("team-competition-status", qualified.length ? "Bizonyítékkal odaítélt helyezések" : "Még nincs odaítélt helyezés");
    if (qualified.length) replace("team-podium", ...qualified.map(member => append(el("article", `team-podium-place rank-${member.podium.rank}`), el("span", "team-podium-rank", `${member.podium.rank}.`), el("h3", "", profileName(member)), el("p", "", teamText(member.podium.title, "Minősített papírteljesítmény")), teamEvidence(member.evidence_ids))));
    else {
      const gates = append(el("div", "team-gates"), ...[[policy.min_tickets, "szelvény"], [policy.min_days, "nap"], [policy.min_unique_fixtures, "egyedi meccs"]].map(([value, labelText]) => append(el("span"), el("strong", "", learningCount(value)), el("small", "", labelText))));
      replace("team-podium", append(el("div", "team-podium-empty"), append(el("div"), el("h3", "", "A következő cím még gazdára vár."), el("p", "", "Nem nevezünk győztest kevés vagy össze nem vethető megfigyelésből. A jogosultság és a díj odaítélése két külön lépés; az éles tagság önmagában nem bizonyít eredményességet.")), gates));
    }
    const awards = members.flatMap(member => arr(member.awards).filter(award => member.status === "ACTIVE" && award?.earned === true && award.qualified === true && member.qualification?.eligible === true).map(award => el("span", "team-award", `${profileName(member)} · ${teamText(award.title, "Ellenőrzött elismerés")}`)));
    replace("team-awards", ...awards);
  }
  function renderTeamAuditions(team) {
    const records = (Array.isArray(team.latest_auditions) ? team.latest_auditions : arr(state.data?.latest?.recommendations)).filter(isAudition);
    setText("team-audition-count", `${records.length} próbaajánlat`);
    replace("team-auditions", ...(records.length ? records.map(ticket => {
      const card = append(el("article", "team-audition"), badge("DRAFT", "Próbaidő · nem éles szelvény"), el("h3", "", profileName(ticket)), el("p", "", `${validDate(ticket.date) ? ticket.date : "Nap nem ismert"} · ${arr(ticket.legs).length} választás · ${isModel(ticket) ? modelStatusLabels[modelStatus(ticket)] || "Modellállapot ellenőrzendő" : label(ticket.status)}`));
      if (arr(ticket.legs).length) card.append(append(el("ul"), ...ticket.legs.map(leg => el("li", "", `${leg.home_team || "Hazai"} – ${leg.away_team || "Vendég"} · ${marketText(leg)} · szorzó: ${number(leg.decimal_price)}`))));
      else card.append(el("p", "", isModel(ticket) ? modelNote(ticket) : ticket.status === "ABSTAINED" ? clubAbstention(ticket) : "Még nincs kiadott próbakiválasztás; ez nem vesztes szelvény."));
      if (isModel(ticket)) card.append(modelArchiveDetails(ticket));
      return append(card, el("p", "team-reference", "Csak előre rögzített papírmérés. Nincs tét és nincs megjátszásvezérlés."));
    }) : [teamBlank("Még nincs mentett próbaajánlat. A próbaidős tag a következő alkalmas elemzésben, valós adatokból dolgozhat; a hiányzó eredményt nem pótoljuk bemutatómintával.")]));
  }
  function renderTeamSources(scout) {
    const labels = {COMPLETE: "Forrásvizsgálat elkészült", PARTIAL: "Részleges forráslefedettség", UNAVAILABLE: "Forrásvizsgálat nem elérhető", NO_SNAPSHOT: "Még nincs forrásvizsgálat", IN_PROGRESS: "Forráskeresés folyamatban", INTERRUPTED: "A forráskeresés megszakadt"};
    setText("team-scout-status", labels[scout?.status] || "Még nincs forrásvizsgálat");
    setText("team-scout-note", scout && scout.status !== "NO_SNAPSHOT" ? `Heti nyilvános forráskeret: ${learningCount(scout.request_count)} / ${learningCount(scout.request_limit)} kérés. Utolsó pillanatkép: ${berlinTimestamp(scout.created_at)}. Nincs automatikus kódfuttatás vagy csomagtelepítés.` : "Nincs még mentett forráslista vagy korábbi pillanatkép. A keresés nem telepít vagy futtat külső kódot, és önmagában nem vesz fel új tagot.");
    const candidates = arr(scout?.candidates).filter(item => item?.repository && typeof item.repository === "object");
    replace("team-sources", ...(candidates.length ? candidates.map(item => {
      const repo = item.repository;
      const card = append(el("article", "team-source"), badge("PENDING", item.onboarding_stage === "EXISTING_INTEGRATION" ? "Már beépített forrás · nem új felvétel" : "Forrásvizsgálat · még nem futtatható"), el("h3", "", `${teamText(repo.owner, "Ismeretlen tulajdonos")} / ${teamText(repo.name, "Ismeretlen forrás")}`), el("p", "", teamText(repo.description, "A forráshoz nem érkezett leírás.")));
      append(card, append(el("div", "team-source-facts"), el("span", "", `Licenc: ${teamText(repo.license?.spdx_id, "nem igazolt")}`), el("span", "", `Nyilvános csillag: ${learningCount(repo.stars)}`), el("span", "", repo.archived === true ? "Archivált forrás" : repo.disabled === true ? "Letiltott forrás" : "Forrásmetaadat")), el("p", "", "A licenc a szolgáltató metaadata, nem jogi vagy biztonsági tanúsítás. Futtatható illesztés és ellenőrzött papírpróba nélkül nincs éles csapattagság."), el("p", "team-reference", `Rögzített kódverzió: ${typeof repo.pinned_commit === "string" && /^[a-f0-9]{40,64}$/i.test(repo.pinned_commit) ? repo.pinned_commit : "nem igazolt"}`));
      const url = safeUrl(repo.url); if (url) { const link = el("a", "", "Nyilvános forrás megnyitása ↗"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link); }
      return card;
    }) : [teamBlank("Még nincs ellenőrzésre felvett külső forrás. Ez nem jelenti azt, hogy nincs más módszer; csak azt, hogy itt még nincs mentett jelölt.")]));
    const integrated = arr(scout?.integrated_profiles);
    replace("team-integrated", el("p", "", integrated.length ? integrated.map(item => profileName(typeof item === "string" ? {profile_id: item} : item || {})).join(" · ") : "A beépített módszerek listája még nem érkezett meg."));
  }
  function syncTeamControls() {
    const disabled = !teamCanAct();
    $("team-review").disabled = disabled; $("team-scout").disabled = disabled || state.data?.team?.scouting?.status === "IN_PROGRESS";
    document.querySelectorAll(".team-member-control").forEach(button => { button.disabled = disabled; });
  }
  const expertStatuses = {
    AVAILABLE: "Mentett elemzés elérhető", COMPLETE: "Elemzés elkészült",
    NOT_RUN: "Még nincs mentett elemzés", DIFFERENT_RUN: "Másik elemzéshez tartozik",
    INSUFFICIENT_DATA: "Még kevés az adat", INSUFFICIENT_HISTORY: "Még kevés az előzmény",
    UNVALIDATED: "Minta látható · ellenőrzésre vár", NO_SIGNAL: "Nincs elkülöníthető hatás",
    VALIDATED: "Az ellenőrzési feltételek teljesültek", ACTIVE: "Bekapcsolva",
    READY: "Elérhető", CONFIGURED: "Beállítva", LOCAL: "Helyi modul", PROTECTED: "Védett 2×2×3",
    ADVISORY: "Szakértői vélemény", ADVISORY_ONLY: "Szakértői vélemény",
    SHADOW: "Megfigyelés és próbamérés", PROBATION: "Próbaidő", SUSPENDED: "Felfüggesztve",
    DISABLED: "Kikapcsolva", UNAVAILABLE: "Most nem elérhető", UNKNOWN: "Még nem ellenőrzött",
    REVIEW: "Ellenőrzést igényel", SOURCED_CLAIM_UNVERIFIED: "Forrásolt, ellenőrzendő állítás",
    RUNNING: "Elemzés folyamatban", PARTIAL: "Részleges elemzés", CODEX: "Codex motor",
  };
  const expertReasons = {
    UNKNOWN_MATCH_IDENTITY: "A mérkőzés vagy a csapatok azonosítója még nem ellenőrizhető.",
    PREMATCH_LOCKOUT: "A kezdés előtti 15 perces zárolás már érvényes.",
    FORECAST_FIXTURE_MISMATCH: "Az előrejelzés másik mérkőzéshez tartozik.",
    INSUFFICIENT_COMPARABLE_HISTORY: "Még kevés az azonos csapathoz és hasonló ellenfélhez tartozó időjárási előzmény.",
    FORECAST_WEIGHTING_NOT_VALIDATED: "Az időjárási mintát a korabeli előrejelzéseken is ellenőrizni kell.",
    ARCHIVED_FORECAST_HOLDOUT_IMPROVED: "A külön félretett előrejelzési mintán javult a becslés.",
    INTERVAL_INCLUDES_ZERO: "A megfigyelt eltérés még nem különíthető el a véletlen ingadozástól.",
    CONFLICTING_TEAM_SIGNALS: "A két csapat megfigyelt időjárási mintája eltérő irányba mutat.",
    MISSING_FORECAST: "Nincs megfelelő, időbélyegzett előrejelzés.",
    MISSING_WEATHER: "Nincs a mérkőzéshez használható időjárási adat.",
    UNKNOWN_OR_FUTURE_WEATHER_RECEIPT: "Az időjárási adat lekérési ideje még nem ellenőrizhető.",
    WEATHER_TIME_MISMATCH: "Az időjárási adat más időponthoz tartozik.",
    UNSOURCED_WEATHER: "Az időjárási adat forrása vagy teljessége nem igazolt.",
    NOT_A_FORECAST: "A következő mérkőzéshez előrejelzés szükséges.",
    FORECAST_RECEIVED_AFTER_KICKOFF: "Ez az előrejelzés csak a mérkőzés kezdete után érkezett.",
    MISSING_OR_INVALID_WEATHER_VALUES: "Az előrejelzésből hiányzik egy szükséges, ellenőrizhető időjárási érték.",
    INVALID_HUMIDITY: "A közölt páratartalom nem értelmezhető.",
    STALE_FORECAST: "Az előrejelzés már nem elég friss.",
    NO_STANDINGS: "Hiányzik a mérkőzéshez tartozó ellenőrzött tabella.",
  };
  function expertStatus(value) { return expertStatuses[value] || "Állapot még nem ellenőrzött"; }
  function expertBadge(value) {
    return badge(["VALIDATED", "AVAILABLE", "COMPLETE"].includes(value) ? "COMPLETE" : ["INSUFFICIENT_DATA", "UNVALIDATED", "REVIEW"].includes(value) ? "DRAFT" : "PENDING", expertStatus(value));
  }
  function expertSourceLinks(sources) {
    const links = el("div", "expert-source-links"), used = new Set();
    arr(sources).forEach(source => {
      const raw = typeof source === "string" ? source : source?.url || source?.source_url;
      const safe = safeUrl(raw); if (!safe) return;
      const url = new URL(safe); if (url.username || url.password || used.has(url.href)) return;
      used.add(url.href);
      const link = el("a", "", teamText(source?.title || source?.name, url.hostname));
      link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; links.append(link);
    });
    return links;
  }
  function expertRows(section) { return arr(section?.fixtures).filter(row => row && typeof row === "object" && row.fixture_id !== undefined); }
  function expertFixtureName(id) {
    const fixtures = arr(state.data?.latest?.fixtures);
    const key = value => String(value ?? "").replace(/^api_football:/, "");
    const fixture = fixtures.find(item => item && key(item.fixture_id) === key(id));
    return fixture ? `${teamText(fixture.home_team, "Hazai")} – ${teamText(fixture.away_team, "Vendég")}` : `Mérkőzés: ${teamText(String(id ?? ""), "nem ismert")}`;
  }
  function expertSummary(row, kind) {
    if (typeof row?.summary === "string" && row.summary.trim()) return row.summary;
    if (kind === "motivation") {
      const standing = arr(row?.standing_evidence).length, claims = arr(row?.human_factors).length;
      if (row?.review_required === true) return "Több, egyeztetést igénylő állítás érkezett ugyanarról a témáról.";
      if (row?.standings_context) {
        const teams = arr(row.standings_context.teams).filter(item => item && typeof item.team_name === "string");
        const table = teams.map(item => `${item.team_name}: ${learningCount(item.rank)}. hely, ${learningCount(item.points)} pont, ${learningCount(item.played)} lejátszott meccs`).join("; ");
        return `${table || "A tabella rendelkezésre áll."}. ${standing ? `${standing} szabályokkal igazolt tabellatét.` : "A matematikai tét még nem igazolt a versenyszabályokkal."} ${claims} forrásolt, tartalmilag ellenőrzendő állítás.`;
      }
      if (!standing && !claims) return "Nincs ellenőrzött tabella vagy forrásolt hír a motiváció megítéléséhez.";
      return `${standing} tabellatét és ${claims} forrásolt, tartalmilag még ellenőrzendő állítás.`;
    }
    return expertReasons[row?.reason] || "A csapatokra vonatkozó időjárási hatás még nem igazolt.";
  }
  function expertCard(kind, section, available) {
    const weather = kind === "weather", rows = available ? expertRows(section) : [];
    const card = append(el("article", `expert-card expert-${kind}`), el("p", "eyebrow", weather ? "IDŐJÁRÁS ÉS TÖRTÉNETI MINTÁK" : "TÉT, KERET ÉS HÍREK"), el("h3", "", weather ? "Időjárási szakértő" : "Motivációs szakértő · Grok"));
    const active = rows.filter(row => row.live_weight_eligible === true).length;
    const status = !available ? "NOT_RUN" : section?.status || (weather ? active ? "VALIDATED" : rows.some(row => row.status === "UNVALIDATED") ? "UNVALIDATED" : rows.some(row => row.status === "NO_SIGNAL") ? "NO_SIGNAL" : "INSUFFICIENT_DATA" : rows.some(row => row.review_required === true) ? "REVIEW" : rows.some(row => row.standings_context || arr(row.standing_evidence).length || arr(row.human_factors).length) ? "ADVISORY" : "INSUFFICIENT_DATA");
    card.append(expertBadge(status));
    const coverage = section?.coverage || {}, counts = section?.counts || {};
    const metrics = weather ? [
      ["Lezárt meccselőzmény", available ? coverage.completed_matches : null],
      ["Történeti időjárással", available ? coverage.matches_with_reanalysis : null],
      ["Korabeli előrejelzéssel", available ? coverage.matches_with_prematch_forecast : null],
      ["Súlyozásra alkalmas ma", available ? active : null],
    ] : [
      ["Vizsgált mérkőzés", available ? counts.fixtures ?? rows.length : null],
      ["Friss tabellával", available ? rows.filter(row => row.standings_context).length : null],
      ["Szabályokkal igazolt tét", available ? counts.with_standings : null],
      ["Forrásolt hírrel", available ? counts.with_claims : null],
      ["További ellenőrzés", available ? counts.review_required : null],
    ];
    card.append(append(el("div", "expert-card-metrics"), ...metrics.map(([title, value]) => teamValue(title, learningCount(value)))));
    card.append(el("p", "expert-card-note", available ? teamText(section?.summary, weather ? "Csapat, ellenfélerő és helyszín szerint keres hasonló korábbi mérkőzéseket. Éles súlyozás csak ellenőrzött előrejelzési mintából." : "A tabellatétet és a forrásolt állításokat közös véleményezéshez rendezi össze.") : "Ehhez a napi csomaghoz még nincs mentett szakértői elemzés."));
    const gaps = arr(section?.gaps || section?.missing_data).filter(value => typeof value === "string");
    if (available && gaps.length) card.append(append(el("ul", "expert-gaps"), ...gaps.map(value => el("li", "", expertReasons[value] || (/^[A-Z0-9_]+$/.test(value) ? "További forrásadat szükséges." : value)))));
    if (available) append(card, el("small", "expert-meta", `Utolsó elemzés: ${berlinTimestamp(section?.observed_at || state.data?.expert_advisory?.observed_at)}`), expertSourceLinks([...arr(section?.sources), ...arr(section?.source_urls)]));
    return card;
  }
  function sportsCoverageText(collection) {
    if (!collection) return "TheStatsAPI: ehhez a csomaghoz még nincs kiegészítő adatlekérés.";
    const coverage = collection.coverage || {};
    const count = value => Number.isInteger(value) && value >= 0 ? String(value) : "—";
    const status = {AVAILABLE:"Adatok átadva",PARTIAL:"Részleges adatok",UNAVAILABLE:"Adatok nem elérhetők"}[collection.status] || "Lefedettség ellenőrzésre vár";
    const gaps = coverage.gaps && typeof coverage.gaps === "object" ? Object.keys(coverage.gaps).length : 0;
    return `TheStatsAPI · ${status} · Mérkőzés: ${count(coverage.matched)} / ${count(coverage.fixtures)} · Csapat: ${count(coverage.teams)} · Keretjátékos: ${count(coverage.players)} · Játékosstatisztika: ${count(coverage.players_with_season_stats)} · xG: ${count(coverage.with_xg)} adatblokk · Kezdőcsapat: ${count(coverage.with_lineups)} adatblokk · Hiányzó adat: ${count(coverage.missing_facts)} (${gaps} kategória).`;
  }
  function sourceHealthMetric(title, value) {
    return append(el("div", "source-health-metric"), el("span", "", title), el("strong", "", learningCount(value)));
  }
  function renderSourceHealth() {
    const candidate = state.data?.source_health;
    const health = candidate && typeof candidate === "object" && !Array.isArray(candidate) && candidate.mode === "SHADOW" ? candidate : null;
    const statuses = {AVAILABLE:"Árnyékellenőrzés elkészült",MISSING:"Még nincs mentett forrásellenőrzés",UNAVAILABLE:"A forrásellenőrzés nem elérhető"};
    setText("source-health-status", health ? statuses[health.status] || "A forrásállapot nem ellenőrizhető" : "Még nincs forrásadat");
    const origin = {RECORDED_RUN:"A napi futásban rögzített ellenőrzés",ARCHIVED_INPUT_AUDIT:"Archív bemenetek utólagos ellenőrzése"};
    setText("source-health-context", health ? `${origin[health.origin] || "Az ellenőrzés eredete nem ismert"} · Nap: ${validDate(health.date) ? health.date : "—"} · Adatpillanat: ${berlinTimestamp(health.as_of)} · Futás: ${teamText(health.run_id, "—")}` : "Ehhez a csomaghoz még nincs mentett forrásellenőrzés. A lefedettség nem ismert.");
    const dataPathNote = health?.effective_data_path === "LEGACY_UNCHANGED" ? "Az ajánlók a korábbi, változatlan adatútból dolgoznak. Az itt kiválasztott forrás nem jelent modellben felhasznált adatot." : "A modellek adatfelhasználása ebből nem állapítható meg.";
    setText("source-health-note", `SHADOW · Árnyékellenőrzés. ${dataPathNote} Az eredeti jelöltadatok és a kiegészítő mezők ellenőrzése; a szelvények végső árellenőrzése külön fut.`);
    const domains = {sports:"Sportadatok",odds:"Szorzók",weather:"Időjárás",news:"Hírek"};
    const providers = {the_stats_api:"TheStatsAPI",api_football:"API-Football",football_data_co_uk:"Football-Data.co.uk",the_odds_api:"The Odds API",open_meteo:"Open-Meteo",grok:"Grok / X"};
    const rows = arr(health?.rows).filter(row => row && typeof row === "object" && !Array.isArray(row));
    replace("source-health-rows", ...(rows.length ? rows.map(row => {
      const role = {PRIMARY:"Elsődleges forrás",FALLBACK:"Tartalék forrás"}[row.role] || "A forrás szerepe nem ismert";
      const card = append(el("article", "source-health-card"), append(el("div", "source-health-card-heading"), append(el("div"), el("p", "eyebrow", domains[row.domain] || teamText(row.domain, "Adatkör nem ismert")), el("h4", "", providers[row.provider] || teamText(row.provider, "Forrás nem ismert"))), el("span", "badge", role)));
      card.append(append(el("div", "source-health-metrics"), ...[
        ["Várt mező",row.expected_fields], ["Elérhető mező",row.available_fields],
        ["Árnyékellenőrzésben kiválasztva",row.selected_fields], ["Hiányzó mező",row.missing_fields],
        ["Tartalékból választott mező",row.fallback_fields], ["Kizárt megfigyelés",row.rejected_observations],
      ].map(([title,value]) => sourceHealthMetric(title,value))));
      if (row.expected_fields === 0) card.append(el("p", "source-health-card-note", "Ebben az adatkörben nem volt vizsgált mező; a 0 nem teljes lefedettség."));
      return card;
    }) : [el("p", "source-health-empty", health?.status === "UNAVAILABLE" ? "A forrásellenőrzés most nem elérhető. A hiányzó számlálók értéke ismeretlen." : "Nincs megjeleníthető forrásmátrix. A hiányzó adat nem nulla lefedettség.")]));
    replace("source-health-conflicts", sourceHealthMetric("Eltérő forrásértékkel érintett mező",health?.conflict_fields), sourceHealthMetric("Feloldatlan eltérés",health?.unresolved_conflict_fields));
    const coverage = health?.league_coverage;
    $("source-health-leagues-details").hidden = !health;
    setText("source-health-leagues-summary", coverage ? `Ligák lefedettsége · ${learningCount(coverage.active_competitions)} sorozat` : "Ligák lefedettsége · még nincs mentett térkép");
    if (!coverage || typeof coverage !== "object" || !Array.isArray(coverage.leagues)) {
      replace("source-health-leagues", el("p", "source-health-empty", "Ehhez az ellenőrzéshez még nincs ligánkénti lefedettségi adat."));
      return;
    }
    const leagues = coverage.leagues.filter(row => row && typeof row === "object" && !Array.isArray(row));
    const routed = leagues.filter(row => row.collector_enabled === true).length;
    const proposed = leagues.filter(row => row.mapping_status === "MAPPED" && row.collector_enabled === false).length;
    const missing = leagues.filter(row => ["MISSING_LINK","AMBIGUOUS"].includes(row.mapping_status)).length;
    const overview = append(el("div", "source-health-league-counts"), sourceHealthMetric("Meglévő gyűjtési út",routed), sourceHealthMetric("Új párosítási javaslat",proposed), sourceHealthMetric("Hiányzó vagy többértelmű kapcsolat",missing));
    const note = el("p", "source-health-note", `A katalógus hirdetett képességei külön szerepelnek a futásban megfigyelt adatoktól. Az új párosítási javaslat nem kapcsol be adatgyűjtést. A meglévő gyűjtési út és a kapcsolat állapota külön jelzés. Adatpillanat: ${berlinTimestamp(coverage.as_of)}.`);
    const table = el("table", "source-health-table");
    table.append(append(el("thead"), append(el("tr"), ...["Sorozat","Kapcsolat és gyűjtés","Hirdetett képességek","Ebben a futásban"].map(title => { const th = el("th", "", title); th.scope = "col"; return th; }))));
    const body = el("tbody");
    const competitionNames = {EN_EFL_CUP:"Angol Ligakupa",PT1:"Portugál élvonal",UEFA_CL:"Bajnokok Ligája",UEFA_EL:"Európa-liga",UEFA_ECL:"Konferencialiga",NATIONAL_FRIENDLIES:"Válogatott barátságos mérkőzések",NATIONAL_NATIONS_LEAGUE:"Nemzetek Ligája",NATIONAL_WORLD_CUP_QUALIFIERS:"Világbajnoki selejtező",IT3_A:"Serie C · A csoport",IT3_B:"Serie C · B csoport",IT3_C:"Serie C · C csoport"};
    const capabilityFields = [["has_team_stats","Csapatstatisztika"],["has_player_stats","Játékosstatisztika"],["xg_available","xG"],["odds_available","Szorzó"],["live_odds_available","Élő szorzó"]];
    leagues.forEach(row => {
      const scope = {DEFAULT:"Alap sorozat",CONDITIONAL:"Feltételes tartalék",SELECTED_EXTRA:"Külön kiválasztott sorozat"}[row.scope] || "A kínálat szerepe nem ismert";
      const name = teamText(row.the_stats?.name, Object.hasOwn(competitionNames,row.competition_id) ? competitionNames[row.competition_id] : teamText(row.competition_id, "Sorozat neve nem ismert"));
      const identity = append(el("td"), el("strong", "", name), el("small", "", `${scope} · API-Football: ${learningCount(row.api_football_id)}`), el("small", "", teamText(row.the_stats?.country, "Ország: —")));
      const mapped = row.mapping_status === "MAPPED";
      const link = mapped ? row.collector_enabled === true ? "Igazolt kapcsolat" : "Párosítási javaslat" : {MISSING_LINK:"Hiányzó kapcsolat",AMBIGUOUS:"Többértelmű kapcsolat"}[row.mapping_status] || "Kapcsolat: —";
      const route = row.collector_enabled === true ? "Meglévő gyűjtési út" : row.collector_enabled === false ? "Nincs bekapcsolva" : "Gyűjtési út: —";
      const connection = append(el("td"), el("span", `source-health-link${mapped ? "" : " source-health-gap"}`, link), el("small", "", route));
      const advertised = el("td", "source-health-capabilities");
      capabilityFields.forEach(([field,title]) => advertised.append(el("span", "", `${title}: ${row.the_stats?.advertised?.[field] === true ? "igen" : row.the_stats?.advertised?.[field] === false ? "nem" : "—"}`)));
      const observed = append(el("td", "source-health-observed"), el("span", "", `Mérkőzés: ${learningCount(row.observed_in_run?.fixtures)}`), el("span", "", `Egyeztetve: ${learningCount(row.observed_in_run?.matched)}`));
      body.append(append(el("tr"), identity, connection, advertised, observed));
    });
    table.append(body);
    const scroll = append(el("div", "source-health-scroll"),table);
    scroll.tabIndex = 0; scroll.setAttribute("role","region"); scroll.setAttribute("aria-label","Ligák lefedettsége · görgethető táblázat");
    replace("source-health-leagues", overview, note, leagues.length ? scroll : el("p", "source-health-empty", "A ligatérkép még nem tartalmaz sorozatokat."));
  }
  function renderExpertAdvisory() {
    renderSourceHealth();
    const advisory = state.data?.expert_advisory, latest = state.data?.latest;
    const sameRun = !advisory?.run_id || !latest?.run_id || advisory.run_id === latest.run_id;
    const sameDate = !advisory?.date || !latest?.date || advisory.date === latest.date;
    const status = advisory?.status === "AVAILABLE" && (!sameRun || !sameDate) ? "DIFFERENT_RUN" : advisory?.status || "NOT_RUN";
    const available = status === "AVAILABLE";
    replace("expert-advisory-status", expertBadge(status));
    setText("expert-advisory-meta", available ? `${validDate(advisory.date) ? advisory.date : "Nap nem ismert"} · Mentve: ${berlinTimestamp(advisory.observed_at)} · A vélemény a kijelzett csomaghoz tartozik.` : status === "DIFFERENT_RUN" ? "A mentett vélemény másik napi csomaghoz tartozik. Az alábbi nézet nem mutatja azt a jelenlegi szelvények ellenőrzéseként." : "Még nincs ehhez a csomaghoz mentett közös szakértői vélemény.");
    setText("expert-sports-coverage", sportsCoverageText(latest?.sports_supplement?.the_stats_api));
    replace("expert-advisory-cards", expertCard("weather", advisory?.weather, available), expertCard("motivation", advisory?.motivation, available));
    const weather = new Map((available ? expertRows(advisory.weather) : []).map(row => [String(row.fixture_id), row]));
    const motivation = new Map((available ? expertRows(advisory.motivation) : []).map(row => [String(row.fixture_id), row]));
    const fixtureIds = [...new Set([...weather.keys(), ...motivation.keys()])];
    setText("expert-fixture-count", `${fixtureIds.length} meccs`);
    replace("expert-advisory-fixtures", ...(fixtureIds.length ? fixtureIds.map(id => {
      const detail = append(el("details", "expert-fixture"), el("summary", "", expertFixtureName(id)));
      [["weather", "Időjárás", weather.get(id)], ["motivation", "Motiváció · Grok", motivation.get(id)]].forEach(([kind, title, row]) => {
        const rowStatus = row?.status || (row ? row.review_required === true ? "REVIEW" : kind === "motivation" && (row.standings_context || arr(row.human_factors).length || arr(row.standing_evidence).length) ? "ADVISORY" : "INSUFFICIENT_DATA" : "NOT_RUN");
        const assessment = append(el("section", "expert-fixture-opinion"), append(el("div", "expert-opinion-heading"), el("h4", "", title), expertBadge(rowStatus)), el("p", "", row ? expertSummary(row, kind) : "Még nincs mentett vélemény."));
        if (kind === "weather" && row) assessment.append(el("small", "expert-weight-note", row.live_weight_eligible === true ? "Az ellenőrzési feltételek alapján súlyozásra alkalmas." : "A szelvények súlyát jelenleg nem módosítja."));
        if (row) assessment.append(expertSourceLinks([...arr(row.sources), ...arr(row.source_urls), row.source_url, row.forecast?.source_url, row.weather?.source_url, row.standings_context?.source_url, ...arr(row.human_factors).map(item => item?.source_url), ...arr(row.standing_evidence).flatMap(item => [item?.source_url, item?.rules_source_url])]));
        detail.append(assessment);
      });
      return detail;
    }) : [teamBlank("Mérkőzésenkénti vélemény még nem érkezett.")]));
    const network = arr(state.data?.expert_network).filter(item => item && typeof item.id === "string" && typeof item.name === "string");
    const unique = [...new Map(network.map(item => [item.id, item])).values()];
    setText("expert-network-count", unique.length ? `${unique.length} szereplő` : "A szereplőlista még nem érkezett meg");
    replace("expert-network", ...(unique.length ? unique.map(item => append(el("article", "expert-network-node"), el("h4", "", item.name), el("p", "", teamText(item.role, "Szerepkör még nem érkezett")), expertBadge(item.status), el("small", "", teamText(item.detail, "Még nincs közölt állapotmagyarázat.")))) : [teamBlank("A teljes névsort a műhely beállításai adják. A meglévő tipsterek részletes kártyái lent találhatók.")]));
  }
  function renderTeam() {
    renderExpertAdvisory();
    const team = state.data?.team;
    const supported = team?.mode === "LIVE_PROFILE_SUPERVISION" && Array.isArray(team.members);
    $("team-content").hidden = !supported;
    $("team-availability").hidden = supported;
    replace("team-mode", el("span", "team-signal"), el("span", "", supported ? "Élő, szabályozott csapatirányítás" : team ? "Csapatállapot ellenőrzésre vár" : "Újraindításra váró csapatnézet"));
    if (!supported) {
      setText("team-availability", team ? "A csapat állapota most nem ellenőrizhető. A vezérlés letiltva marad; nem állítunk helyette elő tagokat vagy eredményeket." : "A futó Arthur még nem küldi a Szent Grál csapatadatait. Zárd be a régi Arthur-kiszolgáló ablakát, majd nyisd meg újra a megszokott parancsikont és frissítsd ezt az oldalt. A régi napló és szelvények változatlanul megmaradnak.");
      replace("team-hero-facts", el("span", "", "Nincs kitalált rangsor"), el("span", "", "Nincs automatikus fogadás"));
      syncTeamControls(); return;
    }
    const members = teamMembers(), policy = team.policy || {};
    const count = status => members.filter(member => member.status === status).length;
    const enabledActive = members.filter(member => member.status === "ACTIVE" && member.enabled_by_user !== false).length;
    replace("team-hero-facts", ...[[count("ACTIVE"), "éles tag"], [count("PROBATION"), "próbaidős"], [count("SUSPENDED"), "felfüggesztve"]].map(([value, title]) => append(el("span"), el("strong", "", number(value)), el("span", "", title))));
    setText("team-policy-note", `A vezető a rögzített mérésből változtathat tagságot; a korábbi szelvények nem módosulnak. Legutóbbi értékelés: ${berlinTimestamp(team.latest_evaluation?.as_of)}. Az újraértékelés helyi számítás, nem új AI-hívás.`);
    replace("team-metrics", metric("Éles, nincs kikapcsolva", number(enabledActive), "Az ajánlás nem automatikus fogadás"), metric("Külön próbatérben", number(count("PROBATION")), "Nincs valódi tét vagy éles szelvény"), metric("Belépési feltételeket teljesít", number(members.filter(member => member.qualification?.eligible === true).length), "Nem azonos a díjazottak számával"), metric("Megőrzött döntés a nézetben", number(arr(team.events).length), "Indokkal és időponttal"));
    renderTeamPodium(members, policy);
    const scope = $("team-cohort").value === "actual" ? "actual" : "research";
    setText("team-cohort-note", scope === "actual" ? "Tényleges mérleg: kizárólag rögzített fogadások és visszaigazolt árak. Nincs hozzáadott papíreredmény; hiányzó ROI = —, nem 0%. A szolgáltatási költségek nincsenek levonva." : "Előre mért papírkohorsz: 1 hipotetikus egység / szelvény, csak a belépés vagy újrakezdés után, ugyanazzal a verzióval kiadott ajánlatok. A papírhozam nem valódi pénznyereség; a hiányos és függő rekord nem lezárt megfigyelés.");
    const oldResearch = state.data?.learning?.metrics?.research?.tickets;
    setText("team-history-context", `A korábbi történet nem veszett el. A legutóbbi teljes történeti tanulási pillanatképben ${learningCount(oldResearch?.total)} kutatási szelvény szerepel, ebből ${learningCount(oldResearch?.decided)} eldőlt. Az új csapatverseny külön, a belépéstől induló mintát gyűjt; ezért az induló 0 nem törölt előzmény. A teljes korábbi mérleg a Működés oldalon továbbra is elérhető.`);
    replace("team-members", ...(members.length ? members.map(member => teamMemberCard(member, scope, policy)) : [teamBlank("A csapat még nem tartalmaz ellenőrizhető tagot.")]));
    renderTeamAuditions(team);
    const plans = members.flatMap(member => arr(member.training_plan).filter(item => item && typeof item === "object").map(plan => append(el("article", "team-plan"), el("small", "", `${profileName(member)}${plan.source === "codex" ? " · Astra által rangsorolt terv" : ""}${plan.plan_scope === "ALL_TEAM" ? " · közös csapatfeladat" : ""}`), el("h3", "", teamText(plan.title, "Következő ellenőrzés")), el("p", "", teamText(plan.detail)), el("p", "team-reference", "Ellenőrzési terv; nem automatikus modellsúly-változtatás."), teamEvidence(plan.evidence_ids, plan.learning_report_id))));
    replace("team-training", ...(plans.length ? plans : [teamBlank("Még nincs bizonyítékhoz kötött képzési terv. Nem állítunk automatikus tanulást vagy súlymódosítást mentett feladat nélkül.")]));
    const events = arr(team.events).filter(item => item && typeof item === "object");
    replace("team-decisions", ...(events.length ? events.slice(0, 20).map(event => {
      const card = append(el("article", "team-decision"), append(el("div", "team-decision-header"), el("small", "", berlinTimestamp(event.effective_at)), badge("PENDING", event.source === "MANUAL" ? "Tulajdonosi döntés" : event.source === "AUTOMATIC" ? "Szent Grál döntése" : "Naplóbejegyzés")), el("h3", "", profileName(event)), append(el("div", "team-decision-transition"), teamStatusBadge(event.from_status), el("span", "", "→"), teamStatusBadge(event.to_status)), el("p", "", teamReason(event)));
      if (Object.hasOwn(teamStatusNames, event.from_status) && members.some(member => member.profile_id === event.profile_id && member.status !== event.from_status)) { const undo = el("button", "button button-secondary team-member-control", "Korábbi állapot előkészítése"); undo.type = "button"; undo.disabled = !teamCanAct(); undo.addEventListener("click", () => openTeamDecision(event.profile_id, event.from_status)); card.append(undo); }
      return card;
    }) : [teamBlank("Még nincs tagsági döntés a naplóban. A tagok induló állapota nem díj és nem teljesítményigazolás.")]));
    renderTeamSources(team.scouting); syncTeamControls();
  }
  let pendingTeamAction = null;
  function updateTeamOverride() {
    const member = teamMembers().find(item => item.profile_id === pendingTeamAction?.profile_id);
    const needsOverride = pendingTeamAction?.kind === "status" && $("team-member-status").value === "ACTIVE" && member?.status !== "ACTIVE" && member?.qualification?.eligible !== true;
    $("team-override-warning").hidden = !needsOverride;
    $("team-owner-override").required = needsOverride;
    if (!needsOverride) $("team-owner-override").checked = false;
  }
  function openTeamDecision(profileId, nextStatus) {
    if (!teamCanAct()) return;
    const member = teamMembers().find(item => item.profile_id === profileId); if (!member) return;
    pendingTeamAction = {kind: "status", profile_id: profileId};
    setText("team-dialog-title", "Tagság módosítása"); setText("team-dialog-profile", profileName(member));
    setText("team-dialog-description", "A döntés a következő futások részvételét módosítja. Próbaidőben csak külön papírajánlat készül, felfüggesztve nem generál. A korábbi eredmények és rögzített csomagok nem változnak.");
    $("team-status-fields").hidden = false; $("team-member-status").value = Object.hasOwn(teamStatusNames, nextStatus) ? nextStatus : member.status;
    $("team-member-reason").value = ""; $("team-member-reason").required = true; $("team-owner-override").checked = false;
    setText("team-dialog-confirm", "Tagsági döntés rögzítése"); setText("team-dialog-feedback", ""); updateTeamOverride(); $("team-dialog").showModal();
  }
  function openTeamReview() {
    if (!teamCanAct()) return;
    pendingTeamAction = {kind: "review"};
    setText("team-dialog-title", "Élő csapatértékelés"); setText("team-dialog-profile", "A Szent Grál új döntési köre");
    setText("team-dialog-description", "A mentett eredmények helyi újraértékelése tagsági állapotot és képzési tervet módosíthat a vezető szabályai szerint. Nem indít új AI-kérést, nem keres külső forrást, nem köt fogadást és nem emel tétet.");
    $("team-status-fields").hidden = true; $("team-member-reason").required = false; setText("team-dialog-feedback", ""); setText("team-dialog-confirm", "Értékelés és döntések futtatása"); updateTeamOverride(); $("team-dialog").showModal();
  }
  async function postTeamAction(path, body, success) {
    if (!teamCanAct()) return false;
    state.teamBusy = true; syncTeamControls(); $("team-dialog-confirm").disabled = true; setText("team-action-feedback", "A csapatművelet feldolgozása folyamatban…");
    try { await api(path, body); setText("team-action-feedback", success); toast(success); await refreshStatus(); return true; }
    catch { setText("team-action-feedback", "A csapatművelet nem fejeződött be visszaigazoltan. Frissítsd az állapotot a következő próbálkozás előtt; a korábbi napló megmaradt."); setText("team-dialog-feedback", "A kérés nem sikerült. Ellenőrizd a csapat aktuális állapotát és az indokot."); return false; }
    finally { state.teamBusy = false; $("team-dialog-confirm").disabled = false; syncTeamControls(); }
  }
  async function confirmTeamAction(event) {
    event.preventDefault(); if (!pendingTeamAction || !teamCanAct()) return;
    let path = "/api/team/review", body = {}, message = "Az új értékelési kör kérését a műhely átvette. A döntések a naplóban követhetők.";
    if (pendingTeamAction.kind === "status") {
      const reason = $("team-member-reason").value.trim(), status = $("team-member-status").value;
      if (reason.length < 5 || reason.length > 500 || !Object.hasOwn(teamStatusNames, status)) { setText("team-dialog-feedback", "Adj meg 5–500 karakteres döntési indokot és érvényes állapotot."); return; }
      updateTeamOverride(); if ($("team-owner-override").required && !$("team-owner-override").checked) { setText("team-dialog-feedback", "A bizonyítás nélküli élesítéshez külön tulajdonosi megerősítés szükséges."); return; }
      path = "/api/team/status"; body = {profile_id: pendingTeamAction.profile_id, status, reason}; message = "A tagsági döntés rögzítve. A korábbi szelvények változatlanok.";
    }
    if (await postTeamAction(path, body, message)) { $("team-dialog").close(); pendingTeamAction = null; $("main").focus({preventScroll: true}); }
  }
  function renderModelWorkbench() {
    const environment = state.data?.model_environment;
    const environmentState = environment?.status === "INSTALLED" ? "A numerikus programcsomagok telepítve" : environment?.status === "DEPENDENCY_MISSING" ? "Hiányzó numerikus programcsomag" : "A futtatókörnyezet még nincs ellenőrizve";
    const packages = environment?.packages && typeof environment.packages === "object" ? Object.entries(environment.packages) : [];
    replace("model-environment", el("strong", "", environmentState), el("p", "", packages.length ? packages.map(([name, version]) => `${name}: ${version == null ? "hiányzik" : String(version)}`).join(" · ") : "Verzióadat még nem érkezett a háttérből."), el("small", "", `${state.data?.application_version ? `Arthur ${state.data.application_version} · ` : ""}Kísérleti numerikus módszerek · a telepítés és az éles tagság sem jelent igazolt modellpontosságot.`));
    const enabled = state.data?.settings?.enabled_profiles;
    const knownIds = ["daily223", "kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "h1_over05", "gauss", "kepler", "laplace"];
    const enabledIds = new Set(arr(enabled).map(id => String(id).toLowerCase()));
    const profiles = [...new Set([...knownIds, ...clubCatalogue().map(person => person.profile_id), ...enabledIds])];
    setText("enabled-profile-count", Array.isArray(enabled) ? `${enabledIds.size} engedélyezett profil` : "Beállítás még nem érkezett");
    replace("configured-profiles", ...profiles.map(profile_id => append(el("li"), el("span", "", profileName({profile_id})), badge(Array.isArray(enabled) ? enabledIds.has(profile_id) ? "READY" : "DISABLED" : "NOT_RUN", Array.isArray(enabled) ? enabledIds.has(profile_id) ? "Engedélyezve" : "Kikapcsolva" : "Állapot ismeretlen"))));
    replace("model-workbench", ...Object.entries(modelProfiles).map(([profile_id, info]) => {
      const recommendation = recommendationsForDate().find(item => String(item.profile_id).toLowerCase() === profile_id);
      const card = append(el("article", "model-workbench-card"), el("h3", "", profileName({profile_id})), el("p", "model-method", info.method), el("p", "", info.description));
      append(card, el("p", "model-disclaimer", "Kísérleti módszer. Kalibráció és jövőbeli teljesítmény még nem igazolt. Az éles vagy próbaidős tagság a Szent Grál oldalon látható; a helyi számítás nem indít külön LLM-hívást."), badge(recommendation ? modelStatus(recommendation) : "NOT_RUN"), el("p", "", recommendation ? modelNote(recommendation) : modelStatusNotes.NOT_RUN));
      if (recommendation?.model_summary) card.append(modelDetailBlock("A kiválasztott napi futás adatai", recommendation.model_summary));
      if (arr(recommendation?.model_diagnostics).length) card.append(modelDetailBlock("A kiválasztott napi futás diagnosztikája", recommendation.model_diagnostics));
      return card;
    }));
  }
  function renderFixtures(report) {
    const fixtures = arr(report?.fixtures);
    const coverage = new Map(arr(report?.odds_coverage).map(item => [item.fixture_id, item]));
    $("fixture-panel").hidden = !fixtures.length;
    setText("fixture-count", `${fixtures.length} lekért mérkőzés`);
    replace("fixture-list", ...fixtures.map((fixture) => {
      const row = el("article", "fixture-row");
      append(row, el("time", "", when(fixture.kickoff)), append(el("div", "fixture-teams"), el("strong", "", `${fixture.home_team} – ${fixture.away_team}`), el("small", "muted", `${fixture.competition} · ${fixture.provider_status}`)), badge(fixture.odds_status));
      if (fixture.weather) { const weather=fixture.weather; const line=el("small","muted",`Időjárás · Open-Meteo: ${weather.summary}`); const url=safeUrl(weather.source_url); if(url){const source=el("a",""," Forrás");source.href=url;source.target="_blank";source.rel="noopener noreferrer";line.append(source);} row.querySelector(".fixture-teams").append(line); }
      const found = coverage.get(fixture.fixture_id);
      if (found) {
        const names = {api_football:"API-Football", the_odds_api:"The Odds API"};
        const sources = arr(found.providers).map(p => `${names[p.provider] || p.provider}: ${arr(p.bookmakers).join(", ")}`).join("; ");
        const gaps = arr(found.missing_products);
        const text = gaps.length ? `Hiányzik: ${[...new Set(gaps.map(p => marketText({market_key:p.market, selection_key:p.selection, period:p.period})))].join("; ")}` : "Minden kért piachoz érkezett ár.";
        const detail = el("details", "leg-evidence");
        append(detail, el("summary", "", "Szorzóforrások és hiányok"), el("p", "quote-note", sources || "Egyik szolgáltatótól sem érkezett használható szorzó."), el("p", "quote-note", text));
        row.querySelector(".fixture-teams").append(detail);
      }
      return row;
    }));
  }
  function renderTickets() { const report = reportForDate(); const tickets = reportTickets(); const hasData = Boolean(report || tickets.length); const astra = astraSummary(state.data); setText("nav-ticket-count", tickets.length); setText("metric-tickets", hasData ? `${tickets.filter(t => arr(t.legs).length).length} ajánlat` : "—"); setText("metric-tickets-note", report ? `${tickets.length} profil · ${tickets.filter(t => arr(t.legs).length && t.odds_pending).length} szorzóra vár · ${when(report.observed_at || report.created_at, true)}` : "Még nincs elemzés erre a napra"); setText("metric-planned", hasData ? money(ledgerTickets().filter((item) => !item.odds_pending && !isModel(item)).reduce((sum, item) => sum + num(item.stake_eur, 1), 0)) : "—"); const fixtures = diagnosticCount(report, ["fixture_count", "input_fixture_count", "fixtures_seen", "fixtures", "total_fixtures"]); setText("metric-universe", fixtures === null ? `${number(state.data?.configuration?.competition_count)} sorozat` : `${number(fixtures)} meccs`); setText("metric-universe-note", `${number(diagnosticCount(report, ["priced_candidates", "input_candidate_count", "candidate_count", "candidates"]))} piaci jelölt`); setText("metric-astra", astra.title); setText("metric-astra-note", astra.note); setText("tickets-day-label", dateFmt.format(new Date(`${state.date}T12:00:00Z`))); $("export-button").href = `/api/export?date=${encodeURIComponent(state.date)}`; $("export-button").hidden = !report && !tickets.length; if (tickets.length) replace("ticket-list", ...tickets.map(ticketCard)); else { const hasRun = !!report; const title = hasRun ? "Erre a napra még nincs kész szelvény" : "Készen áll a következő elemzésre"; let description = hasRun ? "Az alábbi adatút megmutatja, mi hiányzik az összeállításhoz. A Működés oldalon a részletes futási napló is elérhető." : "Válassz napot és szelvényszámot, majd indítsd el az elemzést. A profilok közös adatgyűjtésből, önálló szempontok szerint dolgoznak."; if (state.date < state.today) description = "Erre a korábbi napra nincs rögzített elemzés. A múltbeli szelvényeket a rendszer nem állítja elő utólag."; const node = empty(title, description); if (hasRun) { const button = el("button", "button button-secondary", "Futási napló megnyitása"); button.addEventListener("click", () => selectTab("operations")); node.append(button); } replace("ticket-list", node); } renderOverlapNotice(report); renderDiagnostics(report); renderFixtures(report); }
  function renderOverlapNotice(report) {
    const pairs = arr(report?.recommendation_overlap).filter(item => num(item.shared_fixtures) > 0 || arr(item.fixture_ids).length);
    if (pairs.length) {
      $("overlap-notice").hidden = false;
      setText("overlap-notice", `${pairs.length} profilpár ajánlatában közös mérkőzés szerepel. Az eltérő módszer nem jelent független eredményt; a közös tévedések a Teljesítmény oldalon követhetők.`);
      return;
    }
    const overlaps = report?.overlap;
    const count = Array.isArray(overlaps) ? overlaps.length : num(overlaps?.shared_fixture_count ?? overlaps?.count);
    $("overlap-notice").hidden = !count;
    setText("overlap-notice", `${count} közös mérkőzés szerepel a szelvények között. Az eltérő módszerek mellett ezek eredménye összefügghet.`);
  }
  function renderResults() {
    const recommendations = arr(state.data?.recommendations).length ? state.data.recommendations : arr(reportForDate()?.recommendations);
    const linked = new Set(recommendations.map(r => r.portfolio_ticket_id).filter(Boolean));
    const tickets = [...recommendations.map(r => { const ticket = ledgerTickets().find(t => t.ticket_id === r.portfolio_ticket_id); return ticket?.played ? mergeRecommendation(r, ticket) : r; }), ...ledgerTickets().filter(t => !linked.has(t.ticket_id))].filter(item => !item.date || item.date === state.date);
    const issued = tickets.filter(ticket => arr(ticket.legs).length);
    const won = issued.filter(ticket => ticket.outcome === "WON").length;
    const lost = issued.filter(ticket => ticket.outcome === "LOST").length;
    const played = tickets.filter(ticket => ticket.played);
    const net = played.reduce((sum, ticket) => sum + (ticket.financial?.settled ? num(ticket.financial.net_eur) : 0), 0);
    $("ticket-text-button").href = `/api/tickets.txt?date=${encodeURIComponent(state.date)}`;
    $("ticket-text-button").hidden = !issued.length;
    replace("results-metrics", metric("Kiadott ajánlat / szelvény", number(issued.length), `${played.length} megjátszott · ${tickets.length - issued.length} választás nélküli profilstátusz`), metric("Nyert", number(won), "Teljes összeállítás eredménye", "positive"), metric("Vesztett", number(lost), "Választás nélküli állapotok nélkül", lost ? "negative" : ""), metric("Megjátszott nettó eredmény", money(net), "Csak a lezárt, megjátszott szelvények", net < 0 ? "negative" : net > 0 ? "positive" : ""));
    if (!tickets.length) { replace("results-list", empty("Még nincs naplózott szelvény", "Az elemzéskor rögzített szelvények és későbbi eredményeik itt jelennek meg. A dátumválasztóval korábbi napra is visszaléphetsz.")); return; }
    replace("results-list", ...tickets.map(ticket => {
      const card = el("article", "result-card");
      const finance = ticket.financial || {};
      const model = isModel(ticket);
      const paper = isAudition(ticket) || (model && !ticket.played);
      const hasLegs = arr(ticket.legs).length > 0;
      const financialLabel = paper ? "Papírteszt · nincs pénzügyi eredmény" : ticket.recommendation_id && !ticket.played ? "Csak találati eredmény" : finance.settled ? money(finance.net_eur) : "Függőben";
      const summary = append(el("div", "result-summary"),
        append(el("div"), el("h3", "", profileName(ticket)), el("small", "", `${isAudition(ticket) ? "Próbaidős papírajánlat" : ticket.played ? "Megjátszott" : model ? "Numerikus kutatási ajánlat" : "Kutatási"} · ${ticket.bookmaker || "—"} · ${arr(ticket.legs).length} meccs`)),
        append(el("div"), el("small", "", paper ? "Tét nélküli követés / szorzó" : "Tét / szorzó"), el("strong", "", `${paper ? "Nincs tét" : money(finance.stake_eur ?? ticket.wager?.stake_eur ?? ticket.stake_eur)} / ${number(ticket.wager?.actual_combined_price ?? ticket.combined_price)}`)),
        append(el("div"), el("small", "", ticket.played ? "Nettó eredmény" : "Kutatási eredmény"), el("strong", !paper && finance.settled ? num(finance.net_eur) < 0 ? "negative" : "positive" : "", financialLabel)),
        badge(!hasLegs && model ? modelStatus(ticket) : ticket.outcome || "PENDING"));
      card.append(summary);
      const details = append(el("details"), el("summary", "", "Mérkőzések és elszámolás"));
      const table = el("table", "result-legs");
      const body = el("tbody");
      arr(ticket.legs).forEach(leg => {
        const settled = arr(ticket.settlement?.legs).find(entry => entry.fixture_id === leg.fixture_id);
        const goals = settled && isNumber(settled.home_goals) && isNumber(settled.away_goals) ? `${settled.home_goals}–${settled.away_goals}` : "—";
        body.append(append(el("tr"), el("td", "", `${leg.home_team || "Hazai"} – ${leg.away_team || "Vendég"}`), el("td", "", marketText(leg)), el("td", "", goals), append(el("td"), badge(settled?.outcome || "PENDING"))));
      });
      table.append(body);
      details.append(hasLegs ? table : el("p", "muted", "Nem készült kiadott választás; nincs elszámolandó fogadás."));
      if (ticket.ticket_id && !model && !paper && !isClubTicket(ticket) && !ticket.played && !arr(ticket.legs).some(leg => leg.kickoff && new Date(leg.kickoff).getTime() <= Date.now())) {
        const button = el("button", "button button-secondary button-small", "Megjátszottam · 5 €");
        button.addEventListener("click", () => recordWager(ticket)); details.append(button);
      }
      if (ticket.ticket_id && ticket.outcome === "REVIEW" && !paper) {
        const action = el("button", "button button-secondary button-small", "Irodai elszámolás");
        action.addEventListener("click", () => openSettlement(ticket)); details.append(action);
      }
      card.append(details);
      if (model) card.append(modelArchiveDetails(ticket));
      return card;
    }));
  }
  function renderConnections() { const credentials = state.data?.credentials || {}; const model = state.data?.settings?.openai?.model || "gpt-6-astra"; const rows = [["TheStatsAPI", "Meccsek, eredmények, szorzók, xG és játékosadatok", credentials.the_stats_api, false], ["Astra / Codex", `${model} · meglévő ChatGPT/Codex keret`, credentials.codex_cli, true]]; replace("connections", ...rows.map(([title, detail, present, codex]) => append(el("div", "connection-row"), append(el("div", ""), el("strong", "", title), el("small", "", detail)), badge(present ? "PENDING" : "NOT_CONFIGURED", codex ? (present ? "CLI elérhető" : "Nincs CLI") : (present ? "Kulcs megadva" : "Nincs kulcs"))))); if (state.data?.configuration?.setup_command) setText("setup-command", state.data.configuration.setup_command); }
  function renderRuntime() { const runtime = state.data?.runtime || {}; const running = !!runtime.running; $("check-openai-button").disabled = running || state.busy; if (runtime.operation === "check-openai") { $("check-openai-button").disabled = running; if (!running && runtime.last_exit_code !== null && runtime.last_exit_code !== undefined) setText("openai-check-result", runtime.last_output || (runtime.last_exit_code === 0 ? "A kapcsolatpróba sikeres." : "A kapcsolatpróba hibát jelzett.")); } $("runtime-banner").hidden = !running; setText("runtime-heading", runtime.operation === "settle" ? "Eredmények frissítése folyamatban" : runtime.operation === "check-openai" ? "Astra-kapcsolat ellenőrzése" : "Az elemzés folyamatban"); setText("runtime-description", runtime.message || "A program adatokat, friss szorzókat és az elérhető Astra-elemzést gyűjti. Ez néhány percig is eltarthat."); $("generate-button").disabled = running || state.busy || state.date < state.today; $("generate-button").querySelector("span").textContent = running ? "Folyamatban…" : "Elemzés indítása"; $("settle-button").disabled = running || state.busy; setText("runtime-output", runtime.last_output || "Még nincs futási kimenet."); setText("runtime-exit", running ? "Folyamatban" : runtime.last_exit_code !== null && runtime.last_exit_code !== undefined ? `Kilépési kód: ${runtime.last_exit_code}` : "Még nem futott"); const history = arr(state.data?.history); replace("run-history", ...history.slice(0, 12).map((run) => { const button = el("button", ""); append(button, append(el("span", ""), el("strong", "", `${run.date || "Ismeretlen nap"} · ${label(run.construction_status)}`), el("small", "", run.run_id || "")), el("span", "muted", when(run.observed_at || run.created_at, true))); button.disabled = !validDate(run.date); button.addEventListener("click", () => { changeDate(run.date); selectTab("tickets"); }); return button; })); }
  function renderFreezeArchive() {
    const frozen = arr(state.data?.freezes)[0];
    if (!frozen) { replace("freeze-archive", el("p", "muted", "Erre a napra még nincs rögzített csomag.")); return; }
    const cards = arr(frozen.recommendations).map(item => {
      const model = isModel(item);
      const card = append(el("article", "freeze-item"), append(el("div", "freeze-item-heading"), el("strong", "", profileName(item)), badge(model && !arr(item.legs).length ? modelStatus(item) : item.outcome || "PENDING")), el("small", "", `${arr(item.legs).length} mérkőzés · ${isNumber(item.combined_price) ? `${number(item.combined_price)}×` : "szorzó nélkül"}${model ? " · kísérleti papírteszt" : ""}`));
      const legs = el("ul");
      arr(item.legs).forEach(leg => {
        const settled = arr(item.settlement?.legs).find(result => result.fixture_id === leg.fixture_id);
        legs.append(el("li", "", `${leg.home_team || "Hazai"} – ${leg.away_team || "Vendég"} · ${marketText(leg)} · ${settled && isNumber(settled.home_goals) && isNumber(settled.away_goals) ? `${settled.home_goals}–${settled.away_goals}` : "eredmény függőben"} · ${label(settled?.outcome || "PENDING")}`));
      });
      card.append(legs);
      if (model) card.append(modelArchiveDetails(item));
      return card;
    });
    const history = arr(frozen.revision_history);
    const historyNode = history.length > 1 ? append(el("details"), el("summary", "", "A csomag korábbi változatai megmaradtak"), ...history.map(item => el("p", "muted", `${item.revision}. változat · ${when(item.frozen_at, true)} · ${item.run_id}${item.reason ? ` · ${item.reason}` : " · eredeti csomag"}`))) : null;
    replace("freeze-archive", el("p", "freeze-summary", `${freezeRevisionText(frozen)}${when(frozen.frozen_at, true)} rögzítve · ${cards.length} profil · ${number(frozen.won)} nyert / ${number(frozen.lost)} vesztett · teljes csomag: ${label(frozen.outcome)}`), ...(historyNode ? [historyNode] : []), ...cards);
  }
  const paperMoney = value => finiteModelNumber(value) ? money(value) : "—";
  const paperMonthValid = value => /^\d{4}-(0[1-9]|1[0-2])$/.test(String(value || ""));
  const paperExclusionNames = {MISSING_ORIGINAL_ODDS:"Hiányzik az eredeti szorzó.", MISSING_ORIGINAL_PRICE:"Hiányzik az eredeti szorzó.", INVALID_ORIGINAL_PRICE:"Az eredeti szorzó nem érvényes.", UNVERIFIED_ORIGINAL_ODDS:"Az eredeti szorzó nem ellenőrizhető.", UNVERIFIED_PRICE:"Az eredeti ár nem igazolt.", NO_LEGS:"Nincs kiadott meccsválasztás.", UNSUPPORTED_OUTCOME:"Az eredmény további ellenőrzést igényel.", AMBIGUOUS_PROVENANCE:"Az eredeti döntés nem azonosítható egyértelműen."};
  function paperPrice(value, decimal) {
    // Retain the saved decimal representation; do not display a newly fetched price.
    if (typeof decimal === "string" && /^\d{1,8}(?:\.\d{1,80})?$/.test(decimal) && Number(decimal) > 1) return decimal.replace(".", ",");
    return finiteModelNumber(value) && value > 1 ? String(value).replace(".", ",") : "—";
  }
  function paperPageBounds(length, page, size) {
    const pages = Math.max(1, Math.ceil(length / size));
    const current = Math.max(0, Math.min(Number.isInteger(page) ? page : 0, pages - 1));
    return {page:current, pages, start:current * size, end:Math.min(length, (current + 1) * size)};
  }
  function paperPagination(kind, length, bounds) {
    $("paper-" + kind + "-prev").disabled = bounds.page === 0;
    $("paper-" + kind + "-next").disabled = bounds.page + 1 >= bounds.pages;
    setText("paper-" + kind + "-page", length ? `${bounds.start + 1}–${bounds.end} / ${number(length)} · ${bounds.page + 1}. oldal` : "Nincs megjeleníthető sor");
  }
  function paperRecordCard(row) {
    const eligible = row.paper_eligible === true;
    const card = el("article", `paper-record${eligible ? "" : " paper-record-incomplete"}`);
    const identity = append(el("div", "paper-record-identity"), el("small", "", validDate(row.date) ? row.date : "Dátum nem ismert"), el("h4", "", profileName(row)), el("span", "paper-record-scope", row.publication_scope === "PAPER_AUDITION" ? "Eredeti próbaajánlat" : row.publication_scope === "LIVE" ? "Eredeti éles ajánlás · itt csak próbamérés" : "Korábbi kutatási ajánlat"));
    const price = append(el("div", "paper-record-price"), el("small", "", "Eredeti árak szorzata"), el("strong", "", paperPrice(row.original_combined_price, row.original_combined_price_decimal)));
    const recordOutcome = ["WON", "LOST", "VOID", "PENDING", "REVIEW"].includes(row.outcome) ? row.outcome : "REVIEW";
    append(card, append(el("header", "paper-record-head"), identity, price, badge(recordOutcome)));
    const facts = append(el("div", "paper-record-facts"), teamValue("Elméleti tét", "1 €"), teamValue("Visszatérült", eligible ? paperMoney(row.returned_eur) : "—"), teamValue("Lezárt nettó", eligible && row.settled === true ? paperMoney(row.realized_net_eur) : "—"), teamValue("Nyitott tét", eligible ? paperMoney(row.open_stake_eur) : "—"));
    card.append(facts);
    if (!eligible) card.append(el("p", "paper-record-warning", "A szelvény szerepel az összes elméleti tétben, de az ár vagy az eredeti adat nem ellenőrizhető teljesen. Nincs hozzáképzelt visszatérülés vagy nyereség."));
    else if (row.settled !== true) card.append(el("p", "paper-record-pending", "Még nincs lezárt nettó eredmény. A nyitott tét nem realizált veszteség."));
    const detail = append(el("details", "paper-record-details"), el("summary", "", `${arr(row.legs).length} meccstipp · eredeti szorzók és elszámolás`));
    const legs = el("ol", "paper-record-legs");
    arr(row.legs).filter(leg => leg && typeof leg === "object").forEach(leg => {
      const measured = typeof leg.measured_outcome === "string" ? leg.measured_outcome : leg.measured_outcome?.outcome;
      const item = append(el("li"), el("strong", "", `${teamText(leg.home_team, "Hazai")} – ${teamText(leg.away_team, "Vendég")}`), el("p", "", marketText(leg)), append(el("div", "paper-leg-meta"), el("span", "", `Eredeti szorzó: ${paperPrice(leg.decimal_price, leg.decimal_price_decimal)}`), badge(["WON", "LOST", "VOID", "PENDING", "REVIEW"].includes(measured) ? measured : "PENDING")));
      append(item, el("small", "", leg.price_verified === true ? "Az eredeti ár ellenőrzött." : "Az eredeti ár nem teljesen ellenőrizhető."), leg.kickoff ? el("small", "", when(leg.kickoff, true)) : null);
      legs.append(item);
    });
    detail.append(legs.children.length ? legs : el("p", "muted", "A meccsenkénti eredeti adatok nem állnak rendelkezésre."));
    const messages = arr(row.exclusion_messages).filter(message => typeof message === "string");
    if (messages.length || arr(row.exclusion_reasons).length) detail.append(append(el("ul", "paper-reasons"), ...(messages.length ? messages : row.exclusion_reasons.map(reason => paperExclusionNames[reason] || "Hiányos vagy nem ellenőrizhető eredeti ár / szelvényadat.")).map(message => el("li", "", message))));
    append(detail, el("p", "paper-record-ref", `Mentett ajánlat: ${teamText(row.recommendation_id, "azonosító nem ismert")} · Futás: ${teamText(row.run_id, "azonosító nem ismert")}`));
    return append(card, detail);
  }
  function renderPaperLedger() {
    const rows = arr(state.paperPerformance?.records).filter(row => row && typeof row === "object").slice().sort((a,b) => String(b.date || "").localeCompare(String(a.date || "")) || String(a.profile_id || "").localeCompare(String(b.profile_id || "")));
    const bounds = paperPageBounds(rows.length, state.paperPage, 20); state.paperPage = bounds.page;
    setText("paper-ledger-count", `${number(rows.length)} szelvény · 20 / oldal`);
    replace("paper-ledger", ...(rows.length ? rows.slice(bounds.start, bounds.end).map(paperRecordCard) : [el("p", "paper-empty", "Erre az időszakra nincs kiadott napi szelvény a naplóban. Kihagyásból és utólagos tippből nem készítünk 1 €-s fogadást.")]));
    paperPagination("ledger", rows.length, bounds);
  }
  function renderPaperSummary() {
    const monthly = $("paper-grouping").value === "month";
    const rows = arr(state.paperPerformance?.[monthly ? "by_month" : "by_day"]).filter(row => row && typeof row === "object").slice().sort((a,b) => String(b[monthly ? "month" : "date"] || "").localeCompare(String(a[monthly ? "month" : "date"] || "")));
    const bounds = paperPageBounds(rows.length, state.paperSummaryPage, 31); state.paperSummaryPage = bounds.page;
    const table = el("table", "paper-summary-table");
    const header = el("tr");
    [monthly ? "Hónap" : "Nap", "Árral értékelhető / összes", "Összes elméleti tét", "Visszatérült¹", "Lezárt nettó¹", "Nyitott tét¹", "Egyenleg¹", ...(!monthly ? ["Összesített egyenleg¹"] : [])].forEach(title => { const th = el("th", "", title); th.setAttribute("scope", "col"); header.append(th); });
    table.append(append(el("thead"), header));
    const body = el("tbody");
    rows.slice(bounds.start, bounds.end).forEach(row => body.append(append(el("tr"), el("td", "", teamText(row[monthly ? "month" : "date"], "—")), el("td", "", `${learningCount(row.eligible_records)} / ${learningCount(row.records)}`), el("td", "", paperMoney(row.all_record_assumed_stake_eur)), el("td", "", paperMoney(row.total_returned_eur)), el("td", "", paperMoney(row.realized_net_eur)), el("td", "", paperMoney(row.open_stake_eur)), el("td", "", paperMoney(row.cash_balance_eur)), !monthly ? el("td", "", paperMoney(row.cumulative_cash_balance_eur)) : null)));
    table.append(body);
    replace("paper-summary", ...(rows.length ? [table, el("p", "paper-table-footnote", "¹ Csak az árral ellenőrizhető minta. Az összesített pénzmozgási egyenleg a kiválasztott időszak kezdetétől számítódik; hiányos áradat mellett nem a teljes csapat igazolt eredménye. A legfrissebb időszak van felül.")] : [el("p", "paper-empty", "Még nincs összesíthető szelvény ebben az időszakban.")]));
    paperPagination("summary", rows.length, bounds);
  }
  function renderRollingTicketKpis(data, targetId) {
    const value = data?.rolling_kpis;
    if (!value || value.status !== "AVAILABLE") { replace(targetId, el("p", "muted", value?.status === "FUTURE_PERIOD" ? "A kiválasztott jövőbeli hónap még nem mérhető." : "A gördülő mutatók most nem érhetők el.")); return; }
    const windows = arr(value.windows), percent = n => isNumber(n) ? `${number(n)}%` : "—";
    const financialValue = (moneyData, key, subsetKey) => isNumber(moneyData?.[key]) ? money(moneyData[key]) : isNumber(moneyData?.[subsetKey]) ? `— · ismert rész: ${money(moneyData[subsetKey])}` : "—";
    const table = summaryTable(windows.map(row => { const g = row.goals || {}, f = row.financial || {}; return [`${number(row.window_days)} nap`, `${number(row.observed_days)} / ${number(row.window_days)}`, number(g.observed_open_days), `${number(g.count_goal_met_closed_days)} / ${number(g.observed_closed_days)}`, percent(g.count_goal_closed_observed_rate_pct), g.joint_goal_unknown_closed_days ? `— · ${number(g.joint_goal_unknown_closed_days)} árhiányos nap` : percent(g.joint_goal_closed_observed_rate_pct), financialValue(f, "settled_net_eur_to_date", "known_subset_settled_net_eur"), financialValue(f, "max_drawdown_eur", "known_subset_max_drawdown_eur")]; }), ["Ablak", "Naplózott nap / ablak", "Nyitott nap", "Legalább 3 nyertes / lezárt nap", "Célarány", "3 nyertes + 30 szorzó", "Lezárt nettó", "Max. visszaesés"]);
    const basis = value.mode === "actual" ? "A rögzített valós tétekkel." : value.mode === "paper" ? "1 €-s hipotetikus elszámolás, csak igazolt eredeti árakkal." : value.mode === "research" ? "Tájékoztató kutatási szimuláció; nem valódi pénzmozgás." : "Az ajánlati nézethez nem feltételezünk tétet vagy profitot.";
    const note = el("p", "chart-footnote", `Időablak vége: ${value.window_end_date}. A százalék csak a lezárt, naplózott napokból készül; a hiányzó és nyitott nap nem vesztes nap. Jelenlegi eredményekkel számolunk. ${basis} A visszaesés az időablak saját, nulláról indított mintájára vonatkozik.`);
    const profiles = windows.map(row => { const details = el("details", "paper-method"), items = arr(row.profile_reliability); details.append(el("summary", "", `${number(row.window_days)} nap · profilok és módszerváltozatok`)); const grid = summaryTable(items.slice(0,40).map(r => [profileName(r), r.strategy_version === "UNKNOWN_LEGACY" ? "Ismeretlen régi változat" : `…${String(r.strategy_version || "").slice(-10)}`, number(r.decided_ticket_days), `${number(r.won)} / ${number(r.lost)}`, percent(r.decided_win_rate_pct), financialValue(r.financial, "settled_net_eur_to_date", "known_subset_settled_net_eur")]), ["Profil", "Változat", "Eldőlt nap", "Nyert / vesztett", "Találati arány", "Lezárt nettó"]); append(details, grid, el("p", "chart-footnote", `A módszerváltozatokat külön mérjük. A találati arány leíró adat, nem igazolt előrejelző megbízhatóság.${items.length > 40 ? " Az első 40 változat látható, nem teljesítményrangsor." : ""}`)); return details; });
    replace(targetId, table, note, ...profiles);
  }
  function renderDailyTicketKpis(data, targetId) {
    renderRollingTicketKpis(data, targetId === "daily-ticket-kpis" ? "rolling-ticket-kpis" : "paper-rolling-ticket-kpis");
    const kpi = data?.daily_kpis;
    if (!kpi || kpi.status !== "AVAILABLE") { replace(targetId, el("p", "muted", "A napi nyertesek és szorzók most nem mérhetők.")); return; }
    const rows = arr(kpi.days).filter(row => row && validDate(row.date)).slice().sort((a,b) => b.date.localeCompare(a.date));
    if (!rows.length) { replace(targetId, el("p", "muted", "Ebben az időszakban nincs naplózott szelvény. Ez nem nulla találatú fogadási nap.")); return; }
    const table = summaryTable(rows.slice(0,31).map(row => [row.date, number(row.confirmed_winning_tickets), row.winning_odds_complete === true ? number(row.winning_combined_odds_sum) : "—", row.winning_odds_complete === false ? number(row.known_winning_odds_sum) : "—", `${number(row.pending_tickets)} / ${number(row.review_tickets)}`, row.day_results_complete === true ? "Lezárt" : "Még változhat"]), ["Nap", "Nyertes szelvény", "Nyertes szorzók összege", "Ismert részösszeg", "Függő / ellenőrzés", "Állapot"]);
    const note = `A nyertes szelvények szorzóit összeadjuk. Hiányos árnál a teljes összeg —; az ismert rész külön látszik. Ez nem nettó profit.${kpi.mode === "paper" ? " Az ár nélkül igazolt papírnyertesek is beleszámítanak a darabszámba, pénzügyi eredményt viszont nem rendelünk hozzájuk." : ""}${rows.length > 31 ? " A legutóbbi 31 naplózott nap látható; a korábbiakat hónapválasztással érheted el." : ""}${kpi.all_input_days_assigned === false ? " Vannak naphoz nem rendelhető sorok." : ""}`;
    replace(targetId, table, el("p", "chart-footnote", note));
  }
  function renderPaperPerformance() {
    const data = state.paperPerformance;
    $("paper-month-label").hidden = $("paper-period").value === "all";
    $("paper-refresh").disabled = state.paperLoading;
    $("paper-content").hidden = !data || state.paperLoading || !!state.paperError;
    setText("paper-feedback", state.paperLoading ? "Az eredeti szelvények és az 1 €-s próbamérleg betöltése…" : state.paperError || (data ? `${data.month ? data.month : "Teljes történet"} · kizárólag hipotetikus elszámolás, valódi tét és új elemzés nélkül.` : "A próbamérleg a klub megnyitásakor töltődik be."));
    if (!data || state.paperLoading || state.paperError) return;
    const summary = data.summary || {}, complete = summary.complete_result_available === true;
    replace("paper-exposure", teamValue("Összes elméleti feltett tét", paperMoney(summary.all_record_assumed_stake_eur)), teamValue("Teljes lezárt eredmény", complete ? paperMoney(summary.full_all_record_realized_net_eur) : "—"), teamValue("Teljes pénzmozgási egyenleg", complete ? paperMoney(summary.cash_balance_eur) : "—"));
    setText("paper-coverage", `${learningCount(summary.eligible_records)} / ${learningCount(summary.records)} árral ellenőrizhető szelvény · ${learningCount(summary.excluded_records)} hiányos árú / nem értékelhető sor · ${learningCount(summary.pending_records)} függő · ${learningCount(summary.review_records)} ellenőrzendő. ${complete ? "Az eredeti árak alapján teljes az elszámolható kör; a nyitott eredmények még változhatnak." : "A teljes eredmény nem ismert. Az alábbi pénzügyi számok kizárólag az értékelhető mintára vonatkoznak; a hiányos sorok a naplóban megmaradnak."}`);
    $("paper-coverage").classList.toggle("incomplete", !complete);
    replace("paper-kpis", metric("Feltett tét · értékelhető minta", paperMoney(summary.total_stake_eur), "Minden ár szerint értékelhető szelvényre 1 €"), metric("Visszatérült · értékelhető minta", paperMoney(summary.total_returned_eur), "Téttel együtt visszakapott összeg"), metric("Lezárt nettó · értékelhető minta", paperMoney(summary.realized_net_eur), `${learningCount(summary.settled_records)} lezárt szelvény`), metric("Még nyitott tét · értékelhető minta", paperMoney(summary.open_stake_eur), "Nem realizált veszteség"), metric("Egyenleg · értékelhető minta", paperMoney(summary.cash_balance_eur), "Visszatérült − összes értékelhető tét"));
    replace("paper-notes", ...arr(data.notes).filter(note => typeof note === "string").map(note => el("li", "", note)));
    renderDailyTicketKpis(data, "paper-daily-ticket-kpis");
    renderPaperSummary(); renderPaperLedger();
  }
  async function loadPaperPerformance() {
    const request = ++state.paperRequest, allTime = $("paper-period").value === "all", month = $("paper-month").value;
    if (!allTime && !paperMonthValid(month)) { state.paperLoading = false; state.paperError = "Válassz érvényes hónapot a próbamérleghez."; renderPaperPerformance(); return; }
    const periodKey = allTime ? "all" : month;
    if (periodKey !== state.paperPeriodKey) { state.paperPage = 0; state.paperSummaryPage = 0; }
    state.paperPeriodKey = periodKey; state.paperLoading = true; state.paperError = ""; renderPaperPerformance();
    try {
      const data = await api(`/api/paper-performance${allTime ? "" : `?month=${encodeURIComponent(month)}`}`);
      if (request !== state.paperRequest) return;
      if (data?.mode !== "HYPOTHETICAL_EUR1_ORIGINAL_QUOTES" || data.unit_stake_eur !== 1 || !Array.isArray(data.records) || !data.summary || (allTime ? !!data.month : data.month !== month)) throw new Error("Unsupported paper report");
      state.paperPerformance = data;
    } catch {
      if (request !== state.paperRequest) return;
      state.paperPerformance = null; state.paperError = "Az 1 €-s próbamérleg most nem érhető el. Régi Arthur-verziónál indítsd újra a frissített alkalmazást, majd próbáld újra. Hiányzó adatból nem mutatunk nulla eredményt.";
    } finally { if (request === state.paperRequest) { state.paperLoading = false; renderPaperPerformance(); } }
  }
  function clubCatalogue() {
    const seen = new Set();
    return arr(state.data?.tipster_club?.catalogue).filter(person => {
      if (!person || typeof person.profile_id !== "string" || !person.profile_id || seen.has(person.profile_id)) return false;
      seen.add(person.profile_id); return true;
    });
  }
  function clubRecommendations() {
    if (state.data?.selected_date !== state.date) return [];
    return arr(state.data?.tipster_club?.recommendations).filter(item => item && (!item.date || item.date === state.date));
  }
  function clubEnabled(id) {
    if (state.tipsterChanges.has(id)) return state.tipsterChanges.get(id);
    const enabled = state.data?.settings?.enabled_profiles;
    return Array.isArray(enabled) ? enabled.includes(id) : null;
  }
  function clubCanSave() {
    return Array.isArray(state.data?.settings?.enabled_profiles) && !!clubCatalogue().length && !state.tipsterBusy && !state.busy && !state.teamBusy && !state.data?.runtime?.running;
  }
  function clubAvatar(id, className = "club-avatar") {
    // Local illustration, not a photograph or an indication of performance.
    const seed = [...String(id)].reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 7);
    const palettes = [["#eed9bd", "#285c52", "#463329"], ["#e3ddd2", "#9c5f47", "#292d2d"], ["#e8c8b6", "#494b69", "#714d35"], ["#d9e3d3", "#a47c43", "#392d2e"], ["#e7d9c7", "#695747", "#b97e4f"]];
    const [background, coat, hair] = palettes[seed % palettes.length];
    const skin = ["#e9b894", "#bf8b69", "#f0c9ad", "#966b50"][Math.floor(seed / 5) % 4];
    const hairStyle = Math.floor(seed / 19) % 5;
    const portrait = svgNode("svg", {viewBox:"0 0 100 112", class:className, "aria-hidden":"true", focusable:"false"});
    const shape = (tag, attrs) => portrait.append(svgNode(tag, {stroke:"none", ...attrs}));
    shape("rect", {width:100,height:112,rx:22,fill:background});
    shape("circle", {cx:77,cy:22,r:29,fill:"#fff",opacity:.15});
    if (hairStyle === 1 || hairStyle === 4) shape("path", {d:"M23 84V48C23 11 77 13 77 47V85Z",fill:hair});
    shape("path", {d:"M11 112V99C11 80 34 77 50 77S89 80 89 99V112Z",fill:coat});
    shape("path", {d:"M42 68H58V85L50 92 42 85Z",fill:skin});
    shape("path", {d:"M37 79 50 89 45 100 32 82M63 79 50 89 55 100 68 82",fill:"#f6eddf"});
    shape("ellipse", {cx:27,cy:51,rx:5,ry:8,fill:skin}); shape("ellipse", {cx:73,cy:51,rx:5,ry:8,fill:skin});
    shape("rect", {x:28,y:24,width:44,height:51,rx:21,fill:skin});
    const hairPaths = ["M26 45V32C26 11 70 11 75 34L70 46 63 31C50 40 40 29 29 45Z", "M26 52V31C29 8 75 12 74 34L68 50 63 28C49 37 40 32 31 50Z", "M27 39C23 13 69 9 75 31L68 36C55 24 39 28 27 39Z", "M25 40 28 27 32 32 39 16 46 23 57 14 62 23 70 22 75 40 63 32 52 35 41 30Z", "M26 49C24 15 73 11 74 44L63 35 57 27C51 45 32 39 26 49Z"];
    shape("path", {d:hairPaths[hairStyle],fill:hair});
    shape("path", {d:"M36 46h8m12 0h8",stroke:hair,"stroke-width":2});
    shape("circle", {cx:41,cy:51,r:1.8,fill:"#35322c"}); shape("circle", {cx:60,cy:51,r:1.8,fill:"#35322c"});
    shape("path", {d:"m50 51-2 8h4",fill:"none",stroke:"#805f4a","stroke-width":1.2,opacity:.6});
    shape("path", {d:seed % 2 ? "M43 65q7 5 14-1" : "M44 65q6 3 12 0",fill:"none",stroke:"#78513f","stroke-width":1.7});
    if (seed % 3 === 0) { shape("rect", {x:32,y:47,width:16,height:12,rx:4,fill:"none",stroke:coat,"stroke-width":2}); shape("rect", {x:53,y:47,width:16,height:12,rx:4,fill:"none",stroke:coat,"stroke-width":2}); shape("path", {d:"M48 50h5",stroke:coat,"stroke-width":2}); }
    if (seed % 4 === 0) shape("circle", {cx:74,cy:61,r:3,fill:"none",stroke:"#c99c52","stroke-width":2});
    if (seed % 4 === 2) shape("path", {d:"M36 63q14 16 29 0v9q-15 13-29-1Z",fill:hair,opacity:.7});
    shape("path", {d:"M50 99v13",stroke:"#ffffff33","stroke-width":1});
    return portrait;
  }
  function clubAbstention(ticket, member, enabled) {
    if (ticket?.status === "ABSTAINED") return teamText(ticket.abstention_reason, {NO_FIXTURES:"A vizsgált kínálatban nem volt mérkőzés.", NO_VALID_QUOTES:"Nem érkezett megfelelő, ellenőrizhető szorzó.", INSUFFICIENT_EVIDENCE:"A döntéshez kevés a használható bizonyíték.", NO_COMPLIANT_COMBINATION:"Nem állt össze a karakter szabályainak megfelelő szelvény.", NO_DISTINCT_FIXTURE_WITHIN_QUALITY_RETENTION:"Ma nincs kellően alátámasztott, más ajánlattól eltérő választás.", NO_QUALITY_PRESERVING_ASSIGNMENT:"Ma nincs kellően alátámasztott, más ajánlattól eltérő választás.", OVERLAP_RESERVED_BY_DAILY:"A megfelelő mérkőzést már a 2×2×3 képviseli; nem ismételjük meg."}[ticket.reason_code] || "A karakter ezen a napon nem adott ki megfelelő összeállítást.");
    if (ticket?.publication_scope === "SUSPENDED") return "Ebben a futásban felfüggesztett tagság miatt nem készült új ajánlat.";
    if (state.data?.selected_date !== state.date) return "A kiválasztott nap mentett ajánlatai még töltődnek.";
    if (state.date < state.today) return "Erre az archív napra nincs elmentett karakterajánlat. Nem gyártunk visszamenőleges tippet.";
    if (enabled === false) return "Most ki van kapcsolva, és erre a napra nincs mentett ajánlata. A korábbi naplóját megőrizzük.";
    if (member?.status === "SUSPENDED") return "Jelenleg felfüggesztett tag; nem készít új ajánlatot.";
    return "Erre a napra még nincs mentett összeállítás. A következő alkalmas elemzésben, valós adatokból választhat; ez nem kihagyási döntés vagy veszteség.";
  }
  function clubTicket(ticket, member, enabled) {
    const box = el("section", "club-ticket");
    const legs = arr(ticket?.legs);
    if (!legs.length) return append(box, append(el("div", "club-ticket-heading"), el("span", "club-ticket-mark", "—"), el("h4", "", ticket?.status === "ABSTAINED" ? "A kihagyás a döntés" : "Még nincs mentett szelvény")), el("p", "club-abstention", clubAbstention(ticket, member, enabled)));
    const scope = ticket.publication_scope === "LIVE" ? "Éles ajánlás · nem fogadás" : isAudition(ticket) ? "Papírpróba · nincs valódi tét" : "Mentett kutatási ajánlat";
    append(box, append(el("div", "club-ticket-heading"), el("h4", "", `${legs.length} meccses összeállítás`), append(el("span", "club-ticket-odds"), el("small", "", "Összszorzó"), el("strong", "", finiteModelNumber(ticket.combined_price) ? number(ticket.combined_price) : "—"))), append(el("div", "club-ticket-badges"), badge(isAudition(ticket) ? "DRAFT" : "READY", scope), badge(ticket.outcome || "PENDING")));
    const detail = append(el("details", "club-ticket-details"), el("summary", "", "A választások és az eredmények"));
    const list = el("ol", "club-picks");
    legs.forEach(leg => {
      const settled = arr(ticket.settlement?.legs).find(item => item.fixture_id === leg.fixture_id);
      const row = append(el("li"), el("strong", "", `${teamText(leg.home_team, "Hazai")} – ${teamText(leg.away_team, "Vendég")}`), el("p", "", marketText(leg)), append(el("div", "club-pick-meta"), el("span", "", `Szorzó: ${finiteModelNumber(leg.decimal_price) && leg.decimal_price > 1 ? number(leg.decimal_price) : "—"}`), el("span", "", when(leg.kickoff, true))));
      row.append(el("small", "club-estimate", validProbability(leg.model_probability) ? `Közös modell becslése: ${probabilityText(leg.model_probability)} · nem kalibrált, nem garantált.` : "Ehhez a választáshoz nincs számszerű valószínűségi becslés."));
      const exceptional = exceptionalAnalysis(leg);
      if (exceptional) row.append(exceptional);
      if (settled) append(row, badge(settled.outcome || "PENDING"), finiteModelNumber(settled.home_goals) && finiteModelNumber(settled.away_goals) ? el("span", "club-score", `${number(settled.home_goals)}–${number(settled.away_goals)}`) : null);
      list.append(row);
    });
    append(detail, list, el("p", "club-abstention", "Ez a mentett ajánlat, nem az aktuális irodai ár. A szelvény nyerési esélyét nem számoljuk ki a meccsenkénti becslések összeszorzásával."));
    if (arr(ticket.reasoning).length) detail.append(append(el("ul", "evidence-list"), ...ticket.reasoning.map(item => el("li", "", meaningfulText(item)))));
    return append(box, detail);
  }
  function clubMemberCard(person, ticket, member) {
    const enabled = clubEnabled(person.profile_id), m = member?.metrics || {}, financial = m.actual?.financial || {};
    const card = el("article", `club-member${enabled === false ? " club-disabled" : ""}`);
    append(card, append(el("header", "club-member-head"), clubAvatar(person.profile_id), append(el("div", "club-member-identity"), el("span", "club-fiction", "FIKTÍV TIPSTER"), el("h3", "", teamText(person.name, person.profile_id)), el("p", "club-tagline", teamText(person.tagline)))));
    append(card, append(el("div", "club-member-status"), member ? teamStatusBadge(member.status) : badge("PENDING", "Tagság még nem érkezett")), el("p", "club-personality", teamText(person.personality)));
    const preferenceList = arr(person.preferences).filter(item => typeof item === "string");
    card.append(append(el("ul", "club-preferences"), ...preferenceList.map(item => el("li", "", item))));
    const sourceStyle = person.source_style;
    if (sourceStyle && typeof sourceStyle === "object") {
      const method = append(el("details", "club-stats club-method"), el("summary", "", `Módszer: ${teamText(sourceStyle.title, "nyílt forrású stílus")}`));
      method.append(el("p", "", teamText(sourceStyle.summary)));
      const links = arr(sourceStyle.sources).filter(source => source && typeof source.url === "string" && source.url.startsWith("https://github.com/"));
      if (links.length) method.append(append(el("p", "club-method-links"), ...links.map((source, index) => { const link = el("a", "", teamText(source.name, "GitHub-forrás")); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; return append(document.createDocumentFragment(), index ? document.createTextNode(" · ") : null, link); })));
      card.append(method);
    }
    const band = person.target_odds_band || {};
    const noOddsMinimum = person.target_odds_minimum_enforced === false;
    const oddsCondition = noOddsMinimum ? `Nincs minimum${finiteModelNumber(band.max) ? ` · legfeljebb ${number(band.max)}` : ""}` : finiteModelNumber(band.min) && finiteModelNumber(band.max) ? `${number(band.min)}–${number(band.max)}` : "—";
    const style = append(el("div", "club-style"), teamValue(noOddsMinimum ? "Szorzófeltétel" : "Célzott összszorzó", oddsCondition), teamValue("Összeállítás", finiteModelNumber(person.min_legs) && finiteModelNumber(person.max_legs) ? `${number(person.min_legs)}–${number(person.max_legs)} meccs` : "—"));
    append(card, style, el("p", "club-risk", `${{LOWER:"Visszafogottabb",MEDIUM:"Közepes",HIGHER:"Merészebb"}[person.risk] || "Egyedi"} stílus a klubon belül — nem alacsony veszteségkockázat.`), clubTicket(ticket, member, enabled));
    const stats = append(el("details", "club-stats"), el("summary", "", "Papírpróba és valódi eredmény · külön mérve"));
    append(stats, append(el("div", "club-stat-grid"), teamValue(member?.metrics_current_epoch === false ? "Korábbi papírhozam · egységtét" : "Papírhozam · 1 egység / szelvény", learningPercent(m.roi_pct)), teamValue("Értékelhető papírszelvény", learningCount(m.tickets)), teamValue("Tényleges ROI", financial.available === true ? learningPercent(financial.roi_pct) : "—"), teamValue("Tényleges nettó eredmény", financial.available === true && finiteModelNumber(financial.net_eur) ? money(financial.net_eur) : "—")), el("p", "", "A papírminta nem pénznyereség. Hiányzó adat = —, nem nulla. A valódi mérleg csak rögzített fogadásokból készül, szolgáltatási költségek levonása nélkül."), el("p", "", member?.qualification?.eligible === true ? "A vezető szerint teljesült a döntési minta feltétele; ez sem jövőbeli profitígéret." : "Még nincs teljesített minősítési minta. Kevés vagy hiányzó megfigyelésből nem nevezünk nyertest."), el("p", "club-stat-cutoff", `Mérési időhatár: ${berlinTimestamp(m.as_of || state.data?.tipster_club?.metrics_as_of)}. A mérleg nem a fenti dátumra szűrt napi eredmény.`));
    if (member?.metrics_current_epoch === false) stats.append(el("p", "", "Új mérési ciklus indult; a látható papíradat az előző értékelésé, nem az új ciklus teljesítménye."));
    card.append(stats);
    const input = el("input", "club-enable"); input.type = "checkbox"; input.checked = enabled === true; input.disabled = !clubCanSave(); input.dataset.profileId = person.profile_id; input.setAttribute("aria-label", `${teamText(person.name, person.profile_id)} részvétele a következő elemzésben`);
    const indicator = el("span", "club-toggle-track"); indicator.setAttribute("aria-hidden", "true");
    const statusText = el("small", "club-enable-state", enabled === null ? "Beállítás nem ismert" : enabled ? "Bekapcsolva" : "Kikapcsolva");
    input.addEventListener("change", () => { if (!clubCanSave()) return; const saved = state.data.settings.enabled_profiles.includes(person.profile_id); if (input.checked === saved) state.tipsterChanges.delete(person.profile_id); else state.tipsterChanges.set(person.profile_id, input.checked); statusText.textContent = input.checked ? "Bekapcsolásra kijelölve" : "Kikapcsolásra kijelölve"; setText("club-save-feedback", "A módosítás még nincs elmentve. A tagsági állapot és a korábbi napló változatlan."); syncClubControls(); });
    append(card, append(el("footer", "club-member-foot"), append(el("label", "club-toggle"), input, indicator, append(el("span"), el("strong", "", "Napi részvétel"), statusText))));
    return card;
  }
  function syncClubControls() {
    const canSave = clubCanSave(), dirty = state.tipsterChanges.size;
    $("club-save").disabled = !canSave || !dirty; $("club-reset").disabled = state.tipsterBusy || !dirty;
    setText("club-save", state.tipsterBusy ? "Mentés…" : dirty ? `Kiválasztás mentése · ${dirty}` : "Kiválasztás mentése");
    document.querySelectorAll(".club-enable").forEach(input => { input.disabled = !canSave; });
  }
  function renderTipsters() {
    const club = state.data?.tipster_club, catalogue = clubCatalogue(), supported = Array.isArray(club?.catalogue);
    $("club-content").hidden = !supported; $("club-availability").hidden = supported;
    if (!supported) { setText("club-availability", "A futó Arthur még nem küldi a Tipster klub adatait. A frissített alkalmazás újraindítása után jelenik meg a névsor; nem pótoljuk kitalált szelvényekkel vagy eredményekkel."); replace("club-hero-facts"); replace("club-cover-portraits"); syncClubControls(); return; }
    const records = clubRecommendations(), byId = new Map(records.map(item => [item.profile_id, item]));
    const members = new Map(arr(club.members).filter(Boolean).map(item => [item.profile_id, item]));
    const complete = catalogue.filter(person => arr(byId.get(person.profile_id)?.legs).length).length;
    const dayLoaded = state.data?.selected_date === state.date;
    const selectedFixtures = new Set(records.flatMap(item => arr(item.legs).map(leg => leg.fixture_id)).filter(Boolean));
    const coverage = records.map(item => item.selection_diagnostics).find(item => item && finiteModelNumber(item.input_fixture_count));
    replace("club-hero-facts", ...[[catalogue.length,"karakter"], [dayLoaded ? complete : null,"mentett összeállítás"], [dayLoaded ? catalogue.filter(person => byId.get(person.profile_id)?.status === "ABSTAINED").length : null,"kihagyási döntés"], [dayLoaded ? selectedFixtures.size : null,"külön mérkőzés"]].map(([value,title]) => append(el("span"), el("strong", "", number(value)), el("small", "", title))));
    replace("club-cover-portraits", ...[0,7,13].filter(index => catalogue[index]).map(index => clubAvatar(catalogue[index].profile_id, "club-cover-avatar")));
    setText("club-day-note", `${validDate(state.date) ? dateFmt.format(new Date(`${state.date}T12:00:00Z`)) : "Nap kiválasztására vár"} · ${state.data?.selected_date !== state.date ? "A mentett napi adatok töltődnek…" : state.date < state.today ? "Archív nap — csak az eredetileg mentett döntések." : "A kiválasztott nap mentett döntései; nincs automatikus fogadás."}`);
    const search = $("club-search").value.trim().toLocaleLowerCase("hu"), filter = $("club-filter").value;
    const shown = catalogue.filter(person => { const ticket = byId.get(person.profile_id); return (!search || [person.name, person.personality, person.tagline, ...arr(person.preferences)].filter(value => typeof value === "string").join(" ").toLocaleLowerCase("hu").includes(search)) && (filter === "ticket" ? arr(ticket?.legs).length > 0 : filter === "abstained" ? ticket?.status === "ABSTAINED" : filter === "enabled" ? clubEnabled(person.profile_id) === true : true); });
    setText("club-result-count", `${shown.length} / ${catalogue.length} klubtag`);
    replace("club-members", ...(shown.length ? shown.map(person => clubMemberCard(person, byId.get(person.profile_id), members.get(person.profile_id))) : [empty("Nincs ilyen klubtag a szűrésben", "Próbálj más nevet, stílust vagy napi szűrőt. Ez nem a mérkőzéskínálat állapota.")]));
    const overlaps = state.data?.selected_date === state.date ? arr(club.overlap).filter(item => item && finiteModelNumber(item.shared_fixtures) && item.shared_fixtures > 0) : [];
    $("club-overlap-panel").hidden = !overlaps.length;
    replace("club-overlap", ...overlaps.slice().sort((a,b) => b.shared_fixtures - a.shared_fixtures).slice(0,10).map(item => append(el("div"), el("span", "", `${profileName({profile_id:item.profile_a})} + ${profileName({profile_id:item.profile_b})}`), el("strong", "", `${number(item.shared_fixtures)} közös meccs`))));
    const engines = ["daily223", "kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "h1_over05", "gauss", "kepler", "laplace"];
    const team = new Map(teamMembers().map(member => [member.profile_id,member]));
    replace("club-engines", ...engines.map(id => append(el("span"), el("strong", "", profileName({profile_id:id})), el("small", "", teamStatusNames[team.get(id)?.status] || "Állapot még nem érkezett"))));
    const coverageNote = dayLoaded && coverage ? ` A közös numerikus modellek ${number(coverage.shared_model_fixture_count)} / ${number(coverage.input_fixture_count)} mérkőzéshez adtak használható adatot. ${number(coverage.club_selected_fixture_count)} külön meccsből készültek a klub ajánlatai; a legtöbbet használt meccs ${number(coverage.club_max_fixture_profile_count)} kiadott összeállításban szerepel. ` : " ";
    setText("club-metrics-note", `A klub legfeljebb ${learningCount(club.max_daily)} külön karakterajánlatot adhat egy napra; ez nem a megjátszható szelvények vagy a tétek számának növelése.${coverageNote}A teljesen azonos szelvény kiesik, az 50% fölötti meccsátfedés pontbüntetést kap, és a végső rögzített csomag meccskitettsége korlátozott. Indokolt közös választás maradhat. A közös adatbázis miatt a külön meccsek sem jelentenek független módszereket.`);
    syncClubControls();
  }
  async function saveClubSelection() {
    if (!clubCanSave() || !state.tipsterChanges.size) return;
    state.tipsterBusy = true; syncClubControls();
    try {
      // Rebase only explicit edits on the freshest full registry. Never replace it
      // with the 20-person subset, and never touch unrelated settings or statuses.
      const latest = await api(`/api/status?date=${encodeURIComponent(state.date)}`);
      if (!Array.isArray(latest.settings?.enabled_profiles) || latest.runtime?.running) { setText("club-save-feedback", "A mentés most nem végezhető el: az aktuális beállítás nem ellenőrizhető vagy elemzés fut. A kijelölésed megmaradt."); return; }
      const known = new Set(arr(latest.tipster_club?.catalogue).map(person => person?.profile_id));
      if ([...state.tipsterChanges.keys()].some(id => !known.has(id))) { setText("club-save-feedback", "A klub névsora megváltozott. Frissítsd az oldalt és ellenőrizd a kijelölést; nem írtunk át beállítást."); return; }
      const enabled = new Set(latest.settings.enabled_profiles);
      state.tipsterChanges.forEach((value,id) => { if (value) enabled.add(id); else enabled.delete(id); });
      await api("/api/settings", {enabled_profiles:[...enabled]});
      state.tipsterChanges.clear();
      state.data.settings = {...state.data.settings, enabled_profiles:[...enabled]};
      setText("club-save-feedback", "A következő elemzés részvételi listája elmentve. A többi profil, a tagsági státusz és a régi szelvények nem változtak.");
      await refreshStatus();
    } catch { setText("club-save-feedback", "A mentést nem sikerült visszaigazolni. A kijelölés megmaradt; frissítés után ellenőrizd az állapotot. Nem indult elemzés vagy fogadás."); }
    finally { state.tipsterBusy = false; renderTipsters(); }
  }
  function renderOddsMonitor() {
    const monitor = state.data?.runtime?.odds_monitor;
    if (!monitor) return;
    const paused = [...arr(monitor.queries), ...Object.values(monitor.providers || {})].some(row => ["DAILY_BUDGET","QUOTA_PAUSED","QUOTA_RESERVED","QUOTA_EXHAUSTED","PROVIDER_BACKOFF","RATE_LIMITED","UNEXPECTED_COST"].includes(row.status));
    const lastSuccess = arr(monitor.queries).map(row => row.last_success_at).filter(Boolean).sort().at(-1);
    const note = monitor.status === "NOT_STARTED" ? "Az első alkalmas ármegfigyelésre vár." : monitor.status === "UNAVAILABLE" ? "Az árfigyelő állapota most nem érhető el." : paused ? "Keret vagy szolgáltatói korlát miatt egyes lekérdezések várakoznak." : lastSuccess ? `Utolsó sikeres ármegfigyelés: ${when(lastSuccess, true)}.` : "Jelenleg nincs új ármegfigyelés.";
    $("connections").append(append(el("div", "connection-row"), append(el("div"), el("strong", "", "Külön árfigyelő · 30 perc"), el("small", "", `${note} Kezdés előtt 15 perccel lezár; Astrát nem hív.`)), badge(paused ? "DRAFT" : "PENDING", paused ? "Várakozás" : "Árfigyelés")));
  }
  function renderAll() { renderTickets(); renderFreezeControls(); renderResults(); renderFreezeArchive(); renderConnections(); renderOddsMonitor(); renderAutomation(); renderLearning(); renderModelWorkbench(); renderRuntime(); renderTeam(); renderTipsters(); renderPaperPerformance(); renderGrok(); }
  function applyDateControls() { $("selected-date").value = state.date; $("selected-date").max = shiftDate(state.today, 7); $("next-day").disabled = state.date >= shiftDate(state.today, 7); $("past-date-note").hidden = state.date >= state.today; $("future-date-note").hidden = state.date <= state.today; setText("date-context", state.date === state.today ? "Ma · Europe/Berlin" : state.date < state.today ? "Archív nap · Europe/Berlin" : "Előzetes · Europe/Berlin"); }
  async function changeDate(value) { if (!validDate(value) || value > shiftDate(state.today, 7)) { toast("Legfeljebb hét nappal előre választhatsz napot.", true); applyDateControls(); return; } state.date = value; state.renderSignature = null; state.data = state.data ? { ...state.data, selected_date: null, latest: null, tickets: [], recommendations: [], freezes: [] } : null; if ($("paper-period").value !== "all" && $("paper-month").value !== value.slice(0,7)) { $("paper-month").value = value.slice(0,7); state.paperPerformance = null; state.paperPeriodKey = null; if (state.tab === "tipsters") loadPaperPerformance(); } applyDateControls(); renderAll(); await Promise.all([refreshStatus(), loadCompetitions(), loadGrok()]); }
  async function refreshStatus() { const request = ++state.statusRequest; const date = state.date; try { const data = await api(`/api/status?date=${encodeURIComponent(date)}`); if (request !== state.statusRequest || date !== state.date) return; const signature = JSON.stringify({latest:data.latest,tickets:data.tickets,recommendations:data.recommendations,freezes:data.freezes,configuration:data.configuration,credentials:data.credentials,settings:data.settings,model_environment:data.model_environment,application_version:data.application_version,automation:data.automation,learning:data.learning,learning_history:data.learning_history,expert_advisory:data.expert_advisory,expert_network:data.expert_network,source_health:data.source_health,team:data.team,tipster_club:data.tipster_club,history:data.history}); const contentChanged = signature !== state.renderSignature; state.renderSignature = signature; state.data = data; const wasToday = state.date === state.today; state.today = validDate(data.today) ? data.today : todayBerlin(); if (wasToday && !state.date) state.date = state.today; if (!state.preferencesLoaded) { const settings = data.settings || {}; $("strictness").value = settings.strictness ?? 35; $("ticket-count").value = settings.target_ticket_count ?? 2; $("openai-model").value = settings.openai?.model || "gpt-6-astra"; $("web-search").checked = settings.openai?.web_search !== false; state.preferencesLoaded = true; updateControlLabels(); } $("connection-error").hidden = true; $("sidebar-dot").classList.remove("offline"); setText("sidebar-status", data.runtime?.running ? "Elemzés folyamatban" : "A műhely elérhető"); setText("refresh-time", `Frissítve ${timeFmt.format(new Date())}`); applyDateControls(); if (contentChanged) renderAll(); else { renderRuntime(); syncTeamControls(); syncClubControls(); } } catch (error) { if (request !== state.statusRequest) return; setText("connection-error", `A helyi vezérlőpult nem érhető el: ${error.message}`); $("connection-error").hidden = false; $("sidebar-dot").classList.add("offline"); setText("sidebar-status", "A kapcsolat megszakadt"); } }
  async function run() { if (state.busy || state.data?.runtime?.running || state.date < state.today) return; const selected = [...selectedCompetitions()]; if (!selected.length || !state.competitions.length) { toast("Előbb válassz legalább egy elérhető sorozatot.", true); return; } state.busy = true; renderRuntime(); try { const result = await api("/api/run", { date: state.date, strictness: Number($("strictness").value), target_ticket_count: Number($("ticket-count").value), competition_ids: selected }); toast(result.message || "Az elemzés elindult."); await refreshStatus(); } catch (error) { toast(error.message, true); } finally { state.busy = false; renderRuntime(); } }
  let pendingWager = null;
  function recordWager(ticket) { if (isTeamOnly(ticket) || isModel(ticket) || isClubTicket(ticket)) { toast("Ez nem megjátszásra kijelölt pénzügyi szelvény. Az éles ajánlói tagság sem hoz létre automatikusan valódi fogadást.", true); return; } if (!ticket.ticket_id) return; pendingWager = ticket; setText("wager-profile", profileName(ticket)); $("wager-price").value = isNumber(ticket.combined_price) ? Number(ticket.combined_price).toFixed(2) : ""; setText("wager-feedback", ""); $("wager-dialog").showModal(); $("wager-price").focus(); $("wager-price").select(); }
  async function confirmWager(event) { event.preventDefault(); if (!pendingWager) return; const price = Number($("wager-price").value); if (!Number.isFinite(price) || price <= 1) { setText("wager-feedback", "Adj meg 1-nél nagyobb, érvényes összszorzót."); return; } $("wager-confirm").disabled = true; try { const result = await api("/api/wager", { ticket_id: pendingWager.ticket_id, stake_eur: 1, actual_combined_price: price }); $("wager-dialog").close(); $("main").focus({preventScroll:true}); pendingWager = null; toast(result.message || "Az 1 € tétet és a tényleges szorzót rögzítettük."); await refreshStatus(); if (state.tab === "performance") await loadPerformance(); } catch (error) { setText("wager-feedback", error.message); } finally { $("wager-confirm").disabled = false; } }
  let settlementTicket = null;
  let settlementReturn = false;
  function openSettlement(ticket) { settlementTicket = ticket; settlementReturn = ticket.settlement?.reason === "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION"; setText("settlement-profile", profileName(ticket)); setText("settlement-explanation", settlementReturn ? "A részben érvénytelenített szelvénynél a tényleges visszafizetés nem számolható ki az eredeti összszorzóból. Az iroda által jóváírt teljes összeget rögzítsd." : "Halasztás vagy félbeszakítás önmagában nem jelent visszatérítést. Csak a fogadóiroda által már érvénytelenített választást rögzítsd."); $("void-fixture-label").hidden = settlementReturn; $("return-amount-label").hidden = !settlementReturn; $("return-amount").required = settlementReturn; $("return-amount").value = ""; $("settlement-note").value = ""; setText("settlement-feedback", ""); replace("void-fixture", ...arr(ticket.legs).map((leg) => { const option = el("option", "", `${leg.home_team || "Hazai"} – ${leg.away_team || "Vendég"}`); option.value = leg.fixture_id; return option; })); $("settlement-dialog").showModal(); }
  async function confirmSettlement(event) { event.preventDefault(); if (!settlementTicket) return; $("settlement-confirm").disabled = true; const body = { ticket_id: settlementTicket.ticket_id, note: $("settlement-note").value.trim() }; if (settlementReturn) body.payout_eur = Number($("return-amount").value); else body.fixture_id = $("void-fixture").value; try { const response = await api(settlementReturn ? "/api/bookmaker-return" : "/api/void", body); $("settlement-dialog").close(); $("main").focus({preventScroll:true}); toast(response.message || "Az irodai elszámolást rögzítettük."); await refreshStatus(); } catch (error) { setText("settlement-feedback", error.message); } finally { $("settlement-confirm").disabled = false; } }

  async function settle() { state.busy = true; renderRuntime(); try { const result = await api("/api/settle", {}); toast(result.message || "Az eredmények frissítése elindult."); await refreshStatus(); } catch (error) { toast(error.message, true); } finally { state.busy = false; renderRuntime(); } }
  async function checkOpenAI() { const button = $("check-openai-button"); button.disabled = true; setText("openai-check-result", "Kapcsolat és modellhozzáférés ellenőrzése…"); try { const result = await api("/api/check-openai", {}); setText("openai-check-result", result.started ? "A kapcsolatpróba elindult. A tényleges eredmény a feladat befejezése után jelenik meg." : result.message || result.error || `Astra: ${label(result.status || "NOT_RUN")}`); await refreshStatus(); } catch (error) { setText("openai-check-result", error.message); } finally { button.disabled = !!state.data?.runtime?.running; } }
  async function saveSettings(event) { event.preventDefault(); const button = $("settings-form").querySelector("button"); button.disabled = true; try { const result = await api("/api/settings", { strictness: Number($("strictness").value), target_ticket_count: Number($("ticket-count").value), openai: { model: $("openai-model").value.trim(), web_search: $("web-search").checked } }); state.settingsDirty = false; setText("settings-feedback", result.message || "A következő futás ezekkel az alapbeállításokkal indul."); await refreshStatus(); } catch (error) { setText("settings-feedback", error.message); } finally { button.disabled = false; } }
  function svgNode(tag, attrs = {}, text) { const node = document.createElementNS("http://www.w3.org/2000/svg", tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (text !== undefined) node.textContent = String(text); return node; }
  function renderProfitChart(series) { const points = arr(series).filter((item) => validDate(item.date) && isNumber(item.cumulative_net_eur)); if (!points.length) { replace("profit-chart", append(el("div", "chart-empty"), el("strong", "", "A görbe az első eredménnyel indul"), el("p", "", "Még nincs lezárt szelvény ebben a hónapban és naplóban."))); return; } const width = 640; const height = 245; const left = 62; const right = 18; const top = 18; const bottom = 40; const values = points.map((item) => Number(item.cumulative_net_eur)); let min = Math.min(0, ...values); let max = Math.max(0, ...values); const margin = Math.max(5, (max - min) * 0.18); min -= margin; max += margin; const x = (index) => left + (points.length === 1 ? (width - left - right) / 2 : index / (points.length - 1) * (width - left - right)); const y = (value) => top + (max - value) / (max - min) * (height - top - bottom); const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-labelledby": "profit-chart-title profit-chart-desc" }); append(svg, svgNode("title", { id: "profit-chart-title" }, "Havi kumulált nettó eredmény"), svgNode("desc", { id: "profit-chart-desc" }, points.map((item) => `${item.date}: ${money(item.cumulative_net_eur)}`).join("; "))); const gridValues = [min + (max - min) * 0.2, min + (max - min) * 0.6, max]; gridValues.forEach((value) => { const py = y(value); append(svg, svgNode("line", { x1: left, x2: width - right, y1: py, y2: py, stroke: "#e7ebe2", "stroke-width": 1, "stroke-dasharray": "3 5" }), svgNode("text", { x: left - 10, y: py + 4, "text-anchor": "end" }, `${nf.format(Math.round(value))} €`)); }); svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(0), y2: y(0), stroke: "#b8c6b7", "stroke-width": 1 })); const path = points.map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(Number(item.cumulative_net_eur)).toFixed(2)}`).join(" "); const area = `${path} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`; svg.append(svgNode("path", { d: area, fill: "#e4f0de", opacity: .75 })); svg.append(svgNode("path", { d: path, fill: "none", stroke: "#23645c", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" })); const labelIndexes = new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]); points.forEach((point, index) => { const dot = svgNode("circle", { cx: x(index), cy: y(Number(point.cumulative_net_eur)), r: points.length > 12 ? 2.5 : 3.5, fill: "#23645c", stroke: "#fff", "stroke-width": 1.5 }); dot.append(svgNode("title", {}, `${point.date}: ${money(point.cumulative_net_eur)}`)); svg.append(dot); if (labelIndexes.has(index)) svg.append(svgNode("text", { x: x(index), y: height - 12, "text-anchor": "middle" }, shortDateFmt.format(new Date(`${point.date}T12:00:00Z`)))); }); replace("profit-chart", svg); }
  function renderOutcomeChart(summary) { const parts = [{ title: "Nyert", count: num(summary.won), color: "#23645c" }, { title: "Vesztett", count: num(summary.lost), color: "#b66b56" }, { title: "Függő / ellenőrzés", count: num(summary.pending) + num(summary.review), color: "#d4ba7d" }, { title: "Érvénytelen", count: num(summary.void), color: "#ced6c8" }]; const total = parts.reduce((sum, part) => sum + part.count, 0); const ring = el("div", "outcome-ring"); const svg = svgNode("svg", { viewBox: "0 0 140 140", role: "img", "aria-label": parts.map((part) => `${part.title}: ${part.count}`).join(", ") }); svg.append(svgNode("circle", { cx: 70, cy: 70, r: 56, fill: "none", stroke: "#edf0e8", "stroke-width": 12 })); let offset = 0; const circumference = 2 * Math.PI * 56; if (total) parts.forEach((part) => { if (!part.count) return; const length = circumference * part.count / total; svg.append(svgNode("circle", { cx: 70, cy: 70, r: 56, fill: "none", stroke: part.color, "stroke-width": 12, "stroke-dasharray": `${length} ${circumference - length}`, "stroke-dashoffset": -offset })); offset += length; }); append(ring, svg, append(el("div", "outcome-ring-center"), el("strong", "", number(total)), el("small", "", "szelvény"))); const rows = el("div", "outcome-rows"); parts.forEach((part) => { const dot = el("i", ""); dot.style.background = part.color; rows.append(append(el("div", "outcome-row"), append(el("span", ""), dot, el("span", "", part.title)), el("strong", "", number(part.count)))); }); replace("outcome-chart", ring, rows); }
  function renderPerformance() { const data = state.performance; if (!data) return; if (data.mode === "recommendations") { renderRecommendationPerformance(data); return; } const summary = data.summary || {}; const isResearch = data.mode === "research"; $("performance-note").hidden = !isResearch; setText("performance-note", "Kutatási napló: meg nem játszott szelvények számított eredménye. Ez nem tényleges pénzügyi nyereség."); replace("performance-metrics", metric("Lezárt nettó eredmény", money(summary.net_eur), `${money(summary.open_stake_eur)} tét még függőben`, num(summary.net_eur) < 0 ? "negative" : num(summary.net_eur) > 0 ? "positive" : ""), metric("Összes tét", money(summary.stake_eur), `${number(summary.tickets)} szelvény a hónapban`), metric("Visszafizetés", money(summary.payout_eur), "A lezárt szelvények bruttó visszafizetése"), metric("Hozam a lezárt téten", isNumber(summary.yield_pct) ? `${number(summary.yield_pct)}%` : "—", `Legnagyobb visszaesés: ${money(summary.max_drawdown_eur)}`)); renderProfitChart(data.series); renderOutcomeChart(summary); const usage = data.api_usage || {}; replace("usage-metrics", metric("Astra bemeneti token", number(usage.input_tokens), "A naplózott API-válaszok szerint"), metric("Astra kimeneti token", number(usage.output_tokens), "A naplózott API-válaszok szerint"), metric("Futballadat-kérések", number(usage.football_calls), `${number(usage.runs)} naplózott futás`), metric("Szorzóadat-kérések", number(usage.odds_calls), "A kérésszám nem azonos a felhasznált kredittel")); const knownCosts = usage.known_costs_by_currency || {}; const costText = Object.entries(knownCosts).filter(([, amount]) => isNumber(amount)).map(([unit, amount]) => `${number(amount)} ${unit}`).join(" · "); setText("usage-cost-note", costText ? `Visszaigazolt API-költség: ${costText}. A további előfizetési és nem naplózott díjak nincsenek levonva a fenti eredményből.` : "Pénzben kifejezett API-költség még nincs visszaigazolva. Az előfizetési és API-díjak nincsenek levonva a fenti eredményből."); const profiles = arr(data.by_profile); if (!profiles.length) replace("profile-performance", el("p", "chart-empty", "Az első naplózott szelvények után itt külön mérhető mindegyik tipster.")); else { const table = el("table", "performance-table"); const head = el("thead", ""); append(head, append(el("tr", ""), ...["Tipster", "Szelvény", "Nyert / vesztett", "Tét", "Nettó eredmény", "Hozam"].map((title) => el("th", "", title)))); const body = el("tbody", ""); profiles.forEach((profile) => { body.append(append(el("tr", ""), el("td", "", profileName(profile)), el("td", "", number(profile.tickets)), el("td", "", `${number(profile.won)} / ${number(profile.lost)}`), el("td", "", money(profile.stake_eur)), el("td", num(profile.net_eur) < 0 ? "negative" : num(profile.net_eur) > 0 ? "positive" : "", money(profile.net_eur)), el("td", "", isNumber(profile.yield_pct) ? `${number(profile.yield_pct)}%` : "—"))); }); append(table, head, body); replace("profile-performance", table); } }
  function renderRecommendationPerformance(data) {
    const summary = data.summary || {};
    $("performance-note").hidden = false;
    setText("performance-note", "A szelvénytalálat az egész összeállítás eredménye; a meccstalálat az egyes tippeké. Ezek nem felcserélhető arányok és nem pénzügyi hozamok. Az éles ajánlói tagság nem tényleges fogadás. Profilonként a kezdés előtti legutóbbi verzió számít; a régi rekordok nem ismert mutatója —.");
    replace("performance-metrics",
      metric("Mentett ajánlat", number(summary.tickets), "Kiadott profilajánlatok"),
      metric("Szelvény nyert / vesztett", `${number(summary.won)} / ${number(summary.lost)}`, "Teljes összeállítás eredménye"),
      metric("Szelvénytalálati arány", isNumber(summary.hit_rate_pct) ? `${number(summary.hit_rate_pct)}%` : "—", "Csak a nyert és vesztett összeállításokból"),
      metric("Meccstalálati arány", isNumber(summary.leg_hit_rate_pct) ? `${number(summary.leg_hit_rate_pct)}%` : "—", `${number(summary.leg_won)} nyert / ${number(summary.leg_lost)} vesztett tipp`),
      metric("Függő / ellenőrzés · szelvény", `${number(summary.pending)} / ${number(summary.review)}`, "Hiányzó eredmény nem veszteség"),
      metric("Függő / ellenőrzés · meccs", `${number(summary.leg_pending)} / ${number(summary.leg_review)}`, "Hiányzó régi adat nem nulla"));
    renderOutcomeChart(summary);
    replace("profit-chart", summaryTable(arr(data.series).map(row => [row.date, number(row.tickets), `${number(row.won)} / ${number(row.lost)}`, isNumber(row.hit_rate_pct) ? `${number(row.hit_rate_pct)}%` : "—"]), ["Nap", "Ajánlat", "Szelvény nyert / vesztett", "Szelvénytalálat"]));
    replace("profile-performance", summaryTable(arr(data.by_profile).map(row => [profileName(row), number(row.tickets), `${number(row.won)} / ${number(row.lost)}`, isNumber(row.hit_rate_pct) ? `${number(row.hit_rate_pct)}%` : "—", `${number(row.leg_won)} / ${number(row.leg_lost)}`, isNumber(row.leg_hit_rate_pct) ? `${number(row.leg_hit_rate_pct)}%` : "—", `${number(row.leg_pending)} / ${number(row.leg_review)}`]), ["Profil", "Ajánlat", "Szelvény nyert / vesztett", "Szelvénytalálat", "Meccs nyert / vesztett", "Meccstalálat", "Meccs függő / ellenőrzés"]));
    const shared = arr(data.shared_failures).slice().sort((a, b) => num(b.both_lost) - num(a.both_lost) || num(b.shared_settled_fixtures) - num(a.shared_settled_fixtures));
    $("shared-failures-panel").hidden = false;
    replace("shared-failures", shared.length ? summaryTable(shared.slice(0, 10).map(row => [profileName({profile_id: row.profile_a}), profileName({profile_id: row.profile_b}), number(row.shared_settled_fixtures), number(row.both_lost), number(row.both_won)]), ["Profil", "Másik profil", "Közös lezárt meccs", "Mindkettő vesztett", "Mindkettő nyert"]) : el("p", "muted", "Még nincs párosított eredményadat. Hiányzó adatból nem következtetünk függetlenségre."));
    replace("usage-metrics", metric("Astra bemeneti token", number(data.api_usage?.input_tokens), "Mért használat"), metric("Astra kimeneti token", number(data.api_usage?.output_tokens), "Mért használat"));
    setText("usage-cost-note", "A papírteszthez nem adunk tényleges tétet vagy nyereséget. A meccstalálati arány önmagában nem bizonyít jövedelmezőséget.");
  }
  function summaryTable(rows, headers) { const tableNode = el("table", "performance-table"); tableNode.append(append(el("thead"), append(el("tr"), ...headers.map(h => el("th", "", h))))); const body = el("tbody"); rows.forEach(row => body.append(append(el("tr"), ...row.map(value => el("td", "", value))))); tableNode.append(body); return tableNode; }
  function renderFreezePerformance(data) { const summary = data.summary || {}; $("performance-note").hidden = false; setText("performance-note", "A mérés csak a meccsek előtt külön rögzített csomagokra vonatkozik. A szorzó nélküli tippeknek nincs számított pénzügyi hozama."); replace("performance-metrics", metric("Rögzített nap", number(summary.packages), `${number(summary.package_won)} teljesen nyert / ${number(summary.package_lost)} vesztett`), metric("Rögzített ajánlat", number(summary.tickets), "Minden kiválasztott profil"), metric("Nyert / vesztett", `${number(summary.won)} / ${number(summary.lost)}`, `${number(summary.pending)} függő / ${number(summary.review)} ellenőrzendő`), metric("Találati arány", isNumber(summary.hit_rate_pct) ? `${number(summary.hit_rate_pct)}%` : "—", "Csak eldőlt ajánlatokból")); document.querySelector(".chart-panel h2").textContent = "Rögzített napok eredménye"; document.querySelector(".outcome-panel h2").textContent = "Csomagok mérlege"; replace("profit-chart", summaryTable(arr(data.series).map(p => [p.date, number(p.tickets), `${number(p.won)} / ${number(p.lost)}`, label(p.outcome)]), ["Nap", "Ajánlat", "Nyert / vesztett", "Teljes csomag"])); replace("outcome-chart", append(el("p", "freeze-summary", `${number(summary.package_won)} teljesen nyert · ${number(summary.package_lost)} vesztett · ${number(summary.packages - summary.package_won - summary.package_lost)} függő vagy ellenőrzendő`))); replace("profile-performance", summaryTable(arr(data.packages).flatMap(p => arr(p.recommendations).map(r => [p.date, profileName(r), label(r.outcome || "PENDING"), `${arr(r.legs).length} meccs`])), ["Nap", "Profil", "Eredmény", "Méret"])); replace("usage-metrics"); setText("usage-cost-note", "Ebben a nézetben találati eredmény szerepel, pénzügyi hozam nem."); }
  async function loadPerformance() { const request = ++state.perfRequest; replace("daily-ticket-kpis", el("p", "muted", "A napi mutatók betöltése…")); renderRollingTicketKpis({}, "rolling-ticket-kpis"); try { const data = await api(`/api/performance?month=${encodeURIComponent($("performance-month").value)}&mode=${encodeURIComponent($("performance-mode").value)}`); if (request !== state.perfRequest) return; state.performance = data; $("daily-kpi-panel").hidden = data.mode === "freezes"; renderDailyTicketKpis(data, "daily-ticket-kpis"); $("shared-failures-panel").hidden = true; if (data.mode === "freezes") renderFreezePerformance(data); else { document.querySelector(".chart-panel h2").textContent = "Ahogy a hónap alakul"; document.querySelector(".outcome-panel h2").textContent = "Szelvényeredmények"; renderPerformance(); } } catch (error) { if (request !== state.perfRequest) return; replace("daily-ticket-kpis", el("p", "muted", "A kiválasztott napló napi mutatói nem érhetők el.")); renderRollingTicketKpis({}, "rolling-ticket-kpis"); replace("profit-chart", el("p", "chart-empty", `A havi mérleg nem tölthető be: ${error.message}`)); toast(error.message, true); } }
  function selectTab(tab, updateHash = true) { if (!["tickets", "results", "performance", "tipsters", "team", "operations"].includes(tab)) tab = "tickets"; const changed = state.tab !== tab; state.tab = tab; if (changed) window.scrollTo({top:0,left:0,behavior:"instant"}); document.querySelectorAll(".tab-panel").forEach((node) => { node.hidden = node.id !== `tab-${tab}`; }); document.querySelectorAll(".nav-item").forEach((node) => { const active = node.dataset.tab === tab; node.classList.toggle("active", active); if (active) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current"); }); const titles = { tickets: ["Szelvények", "Napi elemzőasztal", "Önálló tipsterek és numerikus modellek. Visszakövethető döntések."], results: ["Eredmények", "A döntések mérlege", "Az eredmény ugyanúgy része a módszernek, mint a kiválasztás."], performance: ["Teljesítmény", "A számok meséljenek", "Külön kutatási és megjátszott napló, tipsterenként visszakövethető eredmény."], tipsters: ["Tipster klub", "Ismerd meg a klubot", "Személyiségek a tippek mögött. Döntések és kihagyások, napra visszakereshetően."], team: ["Szent Grál", "A csapat, amelynek mércéje van", "Élő profilirányítás. Külön próbatér. Kiérdemelt elismerés."], operations: ["Működés", "A műhely vezérlése", "Napi ütemezés, teljes történeti tanulás, numerikus modellek és futási napló."] }; setText("section-breadcrumb", `Műhely / ${titles[tab][0]}`); setText("page-title", titles[tab][1]); setText("page-description", titles[tab][2]); setText("page-eyebrow", tab === "tipsters" ? "ARTHUR · HÚSZ FIKTÍV NÉZŐPONT" : tab === "team" ? "A SZENT GRÁL · CSAPATKÖZPONT" : "ÖNÁLLÓ ELEMZŐPROFILOK"); $("selected-date").closest(".date-control").hidden = ["performance", "team", "operations"].includes(tab); if (updateHash && window.location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`); if (tab === "performance") loadPerformance(); }
  state.today = todayBerlin(); state.date = state.today; $("selected-date").value = state.date; $("performance-month").value = state.today.slice(0, 7); $("paper-month").value = state.today.slice(0, 7); $("performance-mode").querySelector('option[value="research"]').textContent = "Kutatási (meg nem játszott)";
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.tab)));
  $("selected-date").addEventListener("change", (event) => changeDate(event.target.value)); $("previous-day").addEventListener("click", () => changeDate(shiftDate(state.date, -1))); $("next-day").addEventListener("click", () => changeDate(shiftDate(state.date, 1))); $("refresh-button").addEventListener("click", async () => { await refreshStatus(); if (state.tab === "performance") await loadPerformance(); }); $("strictness").addEventListener("input", () => { state.settingsDirty = true; updateControlLabels(); }); $("ticket-count").addEventListener("change", () => { state.settingsDirty = true; updateControlLabels(); }); $("generate-button").addEventListener("click", run); $("settle-button").addEventListener("click", settle); $("check-openai-button").addEventListener("click", checkOpenAI); $("settings-form").addEventListener("submit", saveSettings); $("wager-form").addEventListener("submit", confirmWager); $("settlement-form").addEventListener("submit", confirmSettlement); $("settlement-cancel").addEventListener("click", () => $("settlement-dialog").close()); $("wager-cancel").addEventListener("click", () => $("wager-dialog").close()); $("performance-month").addEventListener("change", loadPerformance); $("performance-mode").addEventListener("change", loadPerformance); window.addEventListener("hashchange", () => selectTab(window.location.hash.slice(1), false));
  $("competition-search").addEventListener("input", renderCompetitions); $("competition-default").addEventListener("click", () => { state.competitionSelections.set(state.date, new Set(state.competitions.filter(isDefaultCompetition).map(item => item.id))); renderCompetitions(); }); $("competition-clear").addEventListener("click", () => { state.competitionSelections.set(state.date, new Set()); renderCompetitions(); }); $("competition-refresh").addEventListener("click", () => loadCompetitions(true)); $("freeze-button").addEventListener("click", freezeDay);
  $("learning-scope").addEventListener("change", renderLearning); $("learning-report").addEventListener("change", renderLearning);
  $("nemotron-refresh").addEventListener("click", () => loadNemotron(true));
  $("refresh-button").addEventListener("click", () => { loadNemotron(true); loadGrok(true); });
  $("grok-refresh").addEventListener("click", () => loadGrok(true));
  $("team-cohort").addEventListener("change", renderTeam); $("team-review").addEventListener("click", openTeamReview); $("team-scout").addEventListener("click", () => postTeamAction("/api/team/scout", {}, "A heti kereten belüli forráskeresés kérését a műhely átvette. A forráslista külön frissül.")); $("team-action-form").addEventListener("submit", confirmTeamAction); $("team-member-status").addEventListener("change", updateTeamOverride); $("team-dialog-cancel").addEventListener("click", () => { $("team-dialog").close(); pendingTeamAction = null; }); $("team-dialog").addEventListener("cancel", () => { pendingTeamAction = null; });
  $("club-search").addEventListener("input", renderTipsters); $("club-filter").addEventListener("change", renderTipsters); $("club-save").addEventListener("click", saveClubSelection); $("club-reset").addEventListener("click", () => { state.tipsterChanges.clear(); setText("club-save-feedback", "A nem mentett kijelölést elvetettük. A beállítások nem változtak."); renderTipsters(); });
  $("club-jump").addEventListener("click", () => { $("club-selection-title").scrollIntoView({block:"start",behavior:"instant"}); $("club-selection-title").focus({preventScroll:true}); });
  $("paper-period").addEventListener("change", loadPaperPerformance); $("paper-month").addEventListener("change", loadPaperPerformance); $("paper-refresh").addEventListener("click", loadPaperPerformance);
  $("paper-grouping").addEventListener("change", () => { state.paperSummaryPage = 0; renderPaperSummary(); });
  $("paper-ledger-prev").addEventListener("click", () => { state.paperPage--; renderPaperLedger(); }); $("paper-ledger-next").addEventListener("click", () => { state.paperPage++; renderPaperLedger(); });
  $("paper-summary-prev").addEventListener("click", () => { state.paperSummaryPage--; renderPaperSummary(); }); $("paper-summary-next").addEventListener("click", () => { state.paperSummaryPage++; renderPaperSummary(); });
  document.querySelectorAll(".nav-item").forEach(button => { if (button.dataset.tab === "tipsters") button.addEventListener("click", loadPaperPerformance); });
  window.addEventListener("hashchange", () => { if (window.location.hash === "#tipsters") loadPaperPerformance(); });
  $("refresh-button").addEventListener("click", () => { if (state.tab === "tipsters") loadPaperPerformance(); });
  updateControlLabels(); applyDateControls(); selectTab(window.location.hash.slice(1) || "tickets", false); refreshStatus().then(() => Promise.all([loadNemotron(), loadGrok()])); loadCompetitions();
  if (state.tab === "tipsters") loadPaperPerformance();
  setInterval(async () => { if (document.hidden || state.polling) return; state.polling = true; const wasRunning = state.data?.runtime?.running; try { await refreshStatus(); await Promise.all([loadNemotron(), loadGrok()]); if (wasRunning && !state.data?.runtime?.running) { if (state.tab === "performance") await loadPerformance(); if (state.tab === "tipsters") await loadPaperPerformance(); } } finally { state.polling = false; } }, 6000);
})();
