# Arthur: 30 önálló döntő agent, közös tényalap

Állapot: **rögzített emberi követelmény és megvalósítási terv; még nem éles 30-agent rendszer**. 2026-10-29. A profit a gazdasági cél. A 15–30 külön teljes szelvény napi termékcél, nem profitbizonyíték.

Az ember kifejezetten kérte: „a 30 profil legyen 30 agent”; mind kapja meg a bekötött időjárás-, xG-, csapat-, játékos/XI-, hír-, történeti és szimulációs adatokat, és mind használhassa a historikus szimulátort. Az első 10 meglévő logika marad. A Tippster Klub 20 helyéhez nyílt forrásból keresünk módszerjelölteket. A jelenlegi futás utólag nem nevezhető 30-agent futásnak.

## 1. Ami már létezik, és ami valóban hiányzik

- Megvan a 7 hagyományos + 3 numerikus + 20 klubszerep és a saját szabályú rangsorolás, teljes napi naptár-átadás, sportadat- és retained DB út, modellek, forráskötött kontextus, opciószcenáriók, kanonikus szelvénymentés. A meglévő munkát megtartjuk.
- A klubprofilok közös ciklusa és közös review-ja még nem 30 elkülönült döntési kontextus. Külön agent-állapot, saját eszközkérések, alternatívaértékelés és végső döntési jogosultság hiányzik.
- A jelenlegi historikus replay a GAUSS/KEPLER/LAPLACE modellekre épül. Kész, minden személyiség teljes stratégiáját történeti áron, költségek után visszamérő API nincs igazolva. A jelenkori SHADOW szcenárió nem helyettesíti ezt.
- A forrásauditok: `artifacts/thirty-independent-agents-20261029/ARCHITECTURE-SOURCE-AUDIT.md` és `DATA-SIMULATOR-SOURCE-AUDIT.md`. Ezek forrásvizsgálatok, nem új futási bizonyítékok.

## 2. Változatlan termékszabályok

Mind a 30 ugyanazt a teljes, hozzáférhető, hiánytalanul kilapozott célnapi szolgáltatói meccshalmazt kapja. Modellhiány, hiányos xG/XI vagy más agent választása miatt nem tűnhet el meccs a közös kínálatból. A saját stratégia utána szűrhet; az elutasítás oka saját agenthez tartozik.

Napi minimum 15, maximum 30 külön teljes, valós lábú és árú kártya. Elsődleges összodds 15–40; kizárólag az engedélyezett hat óvatos karakter saját teljes keresésének igazolt sikertelensége után 5–15. A DAILY eredeti 2×2×3/tolerancia külön védett. Kevés adat vagy kevés igazolt előny esetén a darabszámhiány látható; nem készül hamis árral vagy hamis p-vel pótlék. DRAFT/PAPER és valódi pénzügyi jogosultság külön marad.

Az első 10 matematikája, piacai és minimumai megmaradnak. A klub jelenlegi szabálya külön baseline-verzió. A lent felkutatott módszerjelölt nem írhatja át csendben sem a baseline-t, sem a modellek valószínűségeit. Kutatási challenger külön verzió, és eredményével együtt értékelendő. Daily és legacy OFF; nincs automatikus fogadás, nincs új adat-/LLM-előfizetés vagy néma keretemelés.

## 3. Mit jelent itt az önálló agent?

Egy `agent_id = profile_id`, saját módszerverzió, elkülönült döntési kontextus, állapot, kérésnapló és végső eredmény. Az agent a saját személyisége szerint hoz döntést; nem kap egy másik agent által előszűrt rövidlistát vagy kész szelvényt. Külön OS-folyamat és külön alapmodell nem szükséges. Harminc név egyetlen közös LLM-promptban viszont nem elfogadható izoláció.

A nem triviális döntést saját izolált reasoning-hívás szolgálja ki a meglévő engedélyezett háttéren. A DAILY determinisztikus választását a környezeti értelmezés nem írja felül. Modell-/piaci valószínűséget az LLM nem találhat ki vagy módosíthat. Szállítói batch csak ténylegesen külön kontextusú kérések szállítása lehet. Közös mutable scratchpad nincs.

Minden agent életciklusa:

1. Megkapja a teljes naptárat, saját szabályát, közös snapshotot és minden adatcsalád értékeit/hiányait.
2. A saját módszerével végignézi és elszámolja a teljes kínálatot; saját opciókat képez.
3. A rendelkezésre álló háttérből a releváns tényeket ténylegesen értelmezi. Megőrzi, mi szól mellette, ellene, illetve mi ismeretlen.
4. Saját opcióira lekéri a historikus/szcenárió eredményt; azonos forrású korábbi számítást a közös cache szolgálhat ki.
5. Saját választást vagy indokolt tartózkodást ad. A döntéshez rövid ellenőrizhető indok tartozik, nem privát belső gondolatmenet.
6. Duplikáció/konstrukcióhiba esetén a validátor visszajelzését maga értékeli, legfeljebb egy előre költségkeretezett javítási körben. A gyűjtő nem választ helyette csendben másik opciót.
7. Saját, immutable eredménye a közös kanonikus kártyagyűjteménybe kerül. Az allokátor külön pénzügyi részhalmazt kezel; kutatási kártyát nem töröl az összportfólió kedvéért.

## 4. Közös adat és tényleges felhasználás

Egy `SharedSnapshot` készül: `run_id`, `target_date`, `as_of`, forrásmanifest, teljes `fixture_ids`, adatcsaládok, quote-ok, modellek és eredetük. Egy közös gyűjtés és modellillesztés van. Az elemzési értékek eljutnak minden agenthez; a nagy nyers történet lapozható, csak olvasható eszközből hozzáférhető. A promptban való bájtismétlés helyett ugyanaz a változatlan tényalap oszlik meg.

Kötelező családok: teljes naptár; eredmények és retained történet; odds/ármozgás; csapatstatisztika és xG; játékos/keret/igazolt vagy becsült XI; terhelés/venue/pálya; időjárás; forrásolt hírek; három numerikus modell; történeti/szcenárió szimuláció; saját korábbi döntések, eredmények és ismert költségek.

Minden családhoz agentenként külön állapot kell: `AVAILABLE_AND_CONSIDERED`, `AVAILABLE_NOT_RELEVANT` indokkal, `MISSING`, `STALE`, `CONFLICTING` vagy `UNUSABLE_FOR_THIS_METHOD`. Elérhető és releváns tény mellé konkrét láb/alternatíva/kockázat/ellenérv kapcsolat kerül. A puszta hash, kézbesítés, hivatkozásszám vagy eszközhívás nem érdemi fogyasztási bizonyíték. Tesztben a releváns tény változtatására a megfelelő indoknak/kockázatnak reagálnia kell; nem kötelező mindig másik kártyának születnie.

UNKNOWN nem False, 0, LOW, 50% vagy odds-inverzből képzett modellbecslés. Az időjárás/hír szöveges kockázati értelmezése nem önkényes p-korrekció. Az adatcsalád hiánya helyi; a másik agent hiteles útja folytatódhat.

## 5. Historikus szimulátor minden agentnek

Két külön eszközszerep lesz, egy közös számítási háttérrel:

- `candidate_scenarios(agent_id, snapshot_id, own_option_ids)`: a jelenlegi valós modell- és közös fixture-kimenetekből a saját opciók kockázata, függősége, ellenpéldái. Ez nem historikus profitállítás.
- `historical_strategy_evaluation(agent_id, method_version, cutoff, window, policy_hash)`: a döntési időpontban hozzáférhető bemenetekkel, változatlan stratégiaszabállyal és valódi történeti árakkal végzett időrendi visszamérés; meglévő eredmény olvasása vagy külön engedélyezett megosztott számítás eredménye.

Mind a 30 a véglegesítés előtt ténylegesen kéri és elszámolja mindkét eszközszerep eredményét: saját kérés és konkrét eredmény, cache-eredmény vagy pontos UNKNOWN-ok szükséges. Az elérhető historikus megállapítás legalább egy saját opcióhoz, ellenérvhez vagy kockázati indokhoz kötődik. Csak a lehetőség vagy a tool neve nem teljesítés. Saját kérés/eredményazonosság kötelező; ugyanazon tartalom cache-találata megosztható. Cache-kulcs: bemenetek és modellhash, módszerverzió, opciók, időkorlát, minta, árforrás és számítási konfiguráció. Nincs harmincszoros automatikus fit/replay. `replay_models` nem adható ki olcsó read-only eszközként, mert taníthat.

Történeti odds vagy kellő PIT-bizonyíték nélkül a profit/ROI `UNKNOWN`; a találati arány vagy jelenlegi árral újraszámolt régi eredmény nem ROI. Költség/FX hiány esetén gross és after-cost külön; after-cost nem nullaköltségű közelítés. Rövid vagy torz minták, szezon/ligaváltás és közös meccsek miatti függőség látható. A profitcélhoz időrendi holdout, kalibráció, nettó eredmény, drawdown és profilok közti hibakorreláció szükséges; 30 agent önmagában nem jelent 30 független előnyt.

## 6. Előre rögzített kód- és illesztési sorrend

| Lépés | Változás | Bizonyíték / megállási feltétel |
|---|---|---|
| A | A jelenlegi egyszeri Oct30 business és a már előkészített első poststop-audit lezárása. | Régi input/outcome/report változatlan; tényleges kártyaszám és adathiányok. Nem indítunk új generálást a design kedvéért. |
| B | Új vékony `jobs/agent_snapshot.py`: immutable teljes naptár és adatcsalád-értékek; valódi read-only hozzáférés. | Mind30 ugyanazt a meccshalmazt kapja; nincs Store-konstruktoron át rejtett DB-írás. |
| C | Új `agents/profile_decision.py`:30 saját session/state, typed döntés és tulajdonosi hibahatár; meglévő runneradapter. |30külön hívási kontextus/eredmény, legfeljebb2 párhuzamos; nincs30-as fetch/fit vagy közös rejtett shortlist. |
| D | Új vékony `agents/profile_tools.py`: tényolvasás, meglévő modellkimenet, saját opciószcenárió és historikus eredmény. | Snapshot/owner/cutoff/cost kötés; cache-hit és actual számítás külön; hiányzó eredmény UNKNOWN. |
| E | `jobs/portfolio.py`, `engine/portfolio.py`, `engine/model_recommendations.py`, `tipsters/recommendations.py` adapterei. | Első10szabály változatlan; a20klub saját módszere dönt, meglévő baseline és kutatási jelölt külön verzió. |
| F | `generated_tickets.py`, `decision_trace.py`, report/ledger, dashboard/export. | Mind30agentstátusz megmarad; a kanonikus számolható kártyák és a pénzügyi vetület elkülönül. |
| G | Izolált integrációs és rendszerpróbák, majd egy ellenőrzött telepítés. | Csak új változás megfelelő tesztje; régi full/replay/gen nem ismétlődik automatikusan. Daily/legacy OFF. |
| H | Következő genuine NORMAL külön friss döntéssel, teljes új kohorszaudit. |30valódi session, teljes napi input, tényleges adat/szimulációs használat;15alatti szám külön hibás napi cél. |

Új általános agent-framework vagy30külső repo telepítése nem kell. A pontos meglévő runnerillesztést a `RUNNER-INTEGRATION-SOURCE-ADDENDUM.md` forrásaudit rögzíti a C lépés előtt. A forrásjelöltek kiválasztása nem API-hívási vagy új modellezési felhatalmazás. A működő backend/modell és a meglévő költségkorlát változatlan; hiányzó szabad keret esetén saját `BUDGET` állapot, nem hamis30/30teljesítés.

A C lépés konkrét meglévő határa a `agents/codex_review.py` ephemeral Codex-hostja. Új `agents/profile_decision_runner.py` publikus, tesztelt adapter készül fölé; a modellel nem kapcsoljuk vissza a shell/DB/plugin/multi-agent jogokat. A gazda közvetíti az allowlistes eszközkérést, és csak ugyanazon owner bounded transcriptjét adja a következő külön contextnek. `FINAL_DECISION`, `ABSTAIN`, `TOOL_REQUEST` külön sémák. Egy globális maximum2-es sor a review/advisory hívásokra is vonatkozik. Durable request-claim után0host-resubmit; bizonytalan completion nem számlázható újra. A30kezdeti kör és az eszközfolytatások tényleges request/time kerete minden genuine futás előtt külön kötendő a meglevő maradékhoz. A jelenlegi8000output-token kérés nem bizonyított CLI által kikényszerített költségplafon. Backend/API-előfizetésváltás nincs; a történeti nevű `nemotron` modul nem állítható NVIDIA-API-nak, mert jelenleg Codex-alias.

## 7. Kötelező tesztek és rendszerátvétel

Új célzott tesztfájlok: `test_profile_agent_decisions.py`, `test_profile_agent_tools.py`, `test_thirty_agent_portfolio_system.py`. A meglévő full-day, profile-scope, generated-ticket és scenario regressziókat a ténylegesen érintett úton kapcsoljuk hozzá.

1.30izolált state/context; agentA módosítása, időtúllépése és hibája nem szivárog B-be.
2.A teljes, részben adathiányos naptár meccsazonosítói mind30-nál egyeznek; saját szűrés és indok külön.
3.Minden releváns adatcsalád értékkel és hibás/hiányzó/stale ellenpéldával; `cited_only` nem teljesítés.
4.Minden agent saját opciójára mindkét szimulátorszerepet ténylegesen kéri és eredmény/UNKNOWN-receipttel elszámolja; shared-cache-hit exactprovenance. Elérhető historikus eredmény megváltoztatása a megfelelő saját indokban/kockázatban látható; a tool nevének felsorolása nem PASS. Nincs új fit és nincs jövőbe látó historikus eredmény.
5.Konkrét személyiségenként saját eltérő választási út, owner-only alternatíva; külső profil vétója/waivere elutasítva.
6.Eredeti10matematika/DAILY/gold változatlan; ismeretlen p nem becsülhető odds-inverzből.
7.Ár/kezdés/piac/láb/15–40/óvatosfallback-validáció;14érvényes külön kártya nem15, duplikált kártya nem új szelvény.
8.Keret/időhiba és5%hardstop: mentett részállapot, nincs automatikus fizetős ismétlés vagy hamis siker.
9.Valódi termékút fakekülső határokkal:CLI→30agent→szimuláció→végső kártya→ledger→API→UI/export; id-k és forráshivatkozások egyeznek.
10.Egy későbbi valódi, engedélyezett kohorsznál a30session és minden eredményfájl fizikailag visszaolvasható. A forrás-/fixture-teszt nem helyettesíti ezt.

## 8. Nyílt forrású20jelölt és döntési határ

A konkrét jelöltlista a `ARTHUR_TIPSTER_20_SOURCE_CANDIDATES_20261029.md` dokumentumban lesz, pontos upstreamforrással, megfigyelt licenccel, adatigénnyel, saját választási úttal és a jelenleg nem teljesített feltételekkel. Ezek építőelemekből kialakítandó Arthur-agentjelöltek; nem állítjuk, hogy20kész, igazoltan nyereséges agentet találtunk.

Az első megvalósítás az agentizolációt és a teljes tény/szimuláció eszközutat teszi valódivá a meglevő módszerekkel. Új tanult numerikus módszer csak külön verziózott, időrendi kutatási értékelés után válthat ki baseline-t. A licencforrások a kutatáskor megfigyelt állapotot írják le; kódátvétel előtt exactverzió/SHA és licence notice rögzítendő. Egy repo nyílt kódlicence nem biztosít jogot a mellékelt külső sportadat újrafelhasználására.

5%-os tényleges számlaszintű keretmaradéknál mentés és megállás. A készültségből csak tényleges eredmény jelenthető; a design nem telepítés és nem profitígéret.
