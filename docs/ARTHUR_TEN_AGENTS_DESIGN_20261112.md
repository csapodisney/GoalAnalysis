# Arthur: tíz önálló agent — 2026-11-12-i módosítás

**Újabb emberi pontosítás:** a [2026-11-13-i tizenkét főhelyes szerződés](ARTHUR_TWELVE_CARD_DASHBOARD_CONTRACT_20261113.md) írja le a tíz agent mellé kerülő Daily 11. és legacy 12. szelvényt, az új dashboardot és a közös 08:00-s indítást. Az alábbi minimum15 és 5%-os stop a korábbi döntés történeti szövege; az 5%-os stopot az ember külön feloldotta. A régi Daily/legacy feladatok OFF követelménye továbbra is érvényes.

Az ember jóváhagyta: „ok, legyen 10 akkor, kezd a munkat”. Ez feloldja az október29-i munkaszünetet és a korábbi30-agent követelmény helyett pontosan10 aktív döntési agentet ír elő. Az5%-os keretstop és a Daily/legacy OFF változatlan. Ez megvalósítási szerződés, nem élesítési vagy profitállítás.

## A tízes döntési csapat

| Azonosító | Személyiség és saját választási út |
|---|---|
| kronikas | Krónikás: összefüggő mérkőzéskép, forrásolt események és saját piaci érvek. |
| ritmusor | Ritmusőr: mérkőzésritmus és gólkép; saját meglévő piaci szabályok. |
| parharcmester | Párharcmester: párosítás és ellenfelek viszonya; saját összevetési út. |
| orszem | Őrszem: bizonytalanság és ellenérvek, óvatos saját konstrukció. |
| merlin | Merlin: eltérő forgatókönyvek és a meglévő saját összetett választási logika. |
| tipster_archiv_nora | Archív Nóra: hosszabb történeti minta, szezon- és ligaváltások. |
| tipster_forma_soma | Forma Soma: friss forma és annak eltérése a hosszabb háttértől. |
| tipster_ertek_milan | Érték Milán: valódi ár és rendelkezésre álló modellbecslés összevetése; hiányzó edge UNKNOWN. |
| tipster_szines_reka | Színes Réka: saját kártyán belüli függőség és változatosság. |
| tipster_fek_tade | Fék Tádé: pesszimista forgatókönyv, közös veszteségkockázat, óvatos döntés. |

A nevek nem tíz igazoltan nyereséges stratégiát jelentenek. A részletes saját út a forrásalapú rosterben verziózott. A Gauss/Kepler/Laplace modellek közös eszközök mind a tíz számára. A DAILY223/H1 és a többi korábbi profil szabálya, adata és története megmarad; az új tízes futási módban nem indulnak plusz rejtett agentként. Meglévő matematika, súly, minimum, piac és READY-kapu nem módosul pusztán a roster szűkítésétől.

## Adat- és döntési szerződés

1. Mind a tíz ugyanazt a teljes, hozzáférhető célnapi szolgáltatói naptárat kapja. A normalizált/modellképes/árazott részhalmaz nem helyettesítheti ezt.
2. Mind a12 adatcsalád elérhető: naptár; retained történet; odds/ártörténet; csapat/xG; játékos/keret/XI; terhelés/venue/pálya; időjárás; forrásolt hír; numerikus modellek; történeti stratégiaértékelés; opciószcenáriók; saját eredmények/költségek. A saját módszer tényt, ellenvetést vagy kockázatot kapcsol hozzájuk; a hiány helyi UNKNOWN.
3. Egy agent saját izolált állapotot, saját opciókat, teljesnapi elszámolást és saját végső döntést ad. Más agent kész rövidlistáját nem kapja kezdőkínálatként.
4. Mindkét szimulátorszerepet ténylegesen kéri. A válasz saját owner/snapshot/opció-hashhez kötött eredmény, megfelelő cache-eredmény vagy pontos UNKNOWN. Mai árakkal visszavetített múlt nem történeti ROI; hiányzó költség/FX nem0.
5. Egy agent legfeljebb3, egymástól gazdasági választásban eltérő saját végső szelvényt adhat. Ez kifejezetten felülírja a korábbi „egy profil egy szelvény” korlátot. Ugyanazon lábak új neve/sorrendje/friss ára nem új szelvény. A közös gyűjtemény maximum30, célminimum15; a darabszám nem írja felül a valós ár/identitás/saját lényegi feltétel kapuját.15alatt a napi cél hiánya látható, töltelék nem készül.
6. A15–40 összodds és a korábbi név szerint engedélyezett óvatos kivétel marad. A tízesből Őrszem és Fék Tádé jogosult a saját sikertelen teljes15–40 keresés után az igazolt5–15 kivételre. Közös alternatívaválasztó nem írja át csendben az agent döntését.
7. A fizetett futás külön friss keret/óra/forrás/budget/once-döntéshez kötött. A programozás és a fixture-rendszerteszt nem éles tízagent-futás. A meglévő host marad, nincs új API-előfizetés, globális maximum2 egyidejű hosthívás és nincs automatikus bizonytalan újraküldés.

## Megvalósítás és ellenőrzés sorrendje

1. Tízes immutable roster és a meglévő SharedSnapshot/session szerződés átállítása; több saját végső változat, duplicate-exposure elutasítás.
2. A tényleges NORMAL producermezők adaptere a meglévő `jobs/agent_snapshot.py` modulban. Teljesnaptár, forrás- és külön tudásidők, támogatott modellpiacok, nested DatasetStore-hivatkozások. Sem DB-konstruktor, sem új gyűjtés nem része az adapternek.
3. `agents/profile_tools.py`: meglévő tiszta opciószcenárió-fogyasztó és hitelesen kötött történeti eredmény. Fit/replay nem olvasóeszköz. A még hiányzó történeti értékelő pontos UNKNOWN-állapotot ad.
4. `agents/profile_decision_runner.py`: izolált ownertervezés → saját eszközeredmények → saját végső választás. Tartós egyszeri kérésazonosság, helyi hiba, keret és időhatár. Az összes agent nem kerül egyetlen közös döntési promptba.
5. Valódi portfolio/kanonikus kártya/ledger/API/UI/export illesztés. Agentazonosság és szelvényváltozat külön azonosító;10session nem10kártyás plafon.
6. Új változásra célzott negatív és integrációs/rendszertesztek:10izolált session, teljes naptár mindenkinél,12család és mindkét saját szimulátor, többváltozat/duplikáció/hiány, hosthiba izolációja, megjelenítési egyezés. A korábbi frozen futások és eredmények megmaradnak.
7. Csak ellenőrzött új termékút után telepítés és friss genuine NORMAL. Korábbi18kártya nem nevezhető tízagent-futásnak; minden tényleges éles eredményt külön kell igazolni.

Az október29-i30-agent design és bizonyítékok történeti forrásként változatlanok. A jelen dokumentum és az új scope saját állapotfájljai irányadók. A hátralévő adat/PIT/kalibráció/weatherchallenger/holdout és költségkövetelmények nem tűnnek el az agentszám csökkentésével.

## Pontos rendszerillesztés

- A CLI és a Nightly generálási pontja ugyanazt az új `NormalAgentExecutor` példánytípusát adja a `run_portfolio` koordinátornak. Ez forrásmódosítás; az ütemezők kikapcsolva maradnak. A régi közös építő, allokátor és utólagos alternatívaválasztó nem fut az új ágon.
- A `profile_input` nézet minden célnapi fixture-t és tényleges candidate-ID-t megtart, és egyesíti a megismételt adatmásolatokat. A felismert történeti sorok teljes kohorszának összesítése és a meglévő csapat/jelölt jellemzők kerülnek a nézetbe; a teljes eredeti snapshot az eszközök mögött változatlan. Ez nem állítás minden nyers sor nyelvi modell általi elolvasásáról. A változatlan bemeneti korlát túllépése látható hiba, nem első-N szűrés.
- Az `agent_evidence` csak a már tárolt, célnapi fixture-ID-hoz tartozó hírállításokat olvassa, read-only kapcsolattal, eredeti publikáció/fetch/receipt órákkal. Ellenőrzi a tárolt állítás hashét; a modell által összefoglalt, nem ellenőrzött hírt továbbra sem nevezi igazolt ténynek. Nincs új szolgáltatói kérés vagy sémaírás.
- Az `agent_portfolio` a kiválasztott eredeti source-ID-ket vezeti át a meglévő saját profil-, piac-, minimum-, ár- és kanonikus kapukon. Csak elutasíthat, nem helyettesíthet. A végső kártyán megmarad az agent, session, snapshot, saját opció, adatcsalád-használat és eszközeredmény kötése.
- A napi 15-ös cél szelvényszám, nem automatikus READY/profitengedély. Az új fixture-próba DRAFT/RESEARCH kártyákat ellenőriz, 0 fogadással. Éles használatot csak külön friss forrás-/adat-/keretellenőrzés és új normál üzleti futás bizonyíthat.
- A saját történeti stratégiaértékelő hiteles eredmények fogyasztására készül. A jelenlegi gyári összeállítás még nem köt be teljes, saját módszerhez illesztett PIT/ár/költség eredményelőállítót: ez külön nyitott követelmény, az eszköz pontos UNKNOWN-t ad. A meglévő történeti modelladat és az aktuális saját opciószcenárió ettől továbbra is használható.

## Több saját szelvény mentése és megjelenítése

1. Csak a verziózott tízagent-eredményágon változik a profilonkénti korlát: egy tulajdonos legfeljebb három, a saját befejezett döntéséhez kötött opciót menthet. A `profile_id` az agent marad; a változat külön saját opció- és szelvényazonosítót kap. Más ágakon az eredeti egy szelvény/profil szabály marad.
2. Az ajánlatindex új azonosítója a futást, tulajdonost és saját opciót köti össze. Az azonos futásban született testvérváltozatok nem vonják vissza egymást. A teljes mentett halmazt az eredeti döntéshez kell ellenőrizni, hiányos vagy átírt kötés esetén az egész mentés visszagördül.
3. Új futás csak a korábbi, még nem megjátszott és el nem kezdődött ajánlatok aktív indexét válthatja le. Ha az agent korábbi aktív szelvényét már megjátszották vagy bármely mérkőzése elkezdődött, az agent új változatai archiválhatók, de aznap nem lesznek új aktív ajánlatok. Egy friss jogosult futás legfeljebb három testvérváltozata mind aktív lehet; a teljes napi kapacitás marad harminc.
4. Nincs sémamigráció vagy régi pillanatkép-átírás. A csomagfagyasztás és annak kifejezett javítása ugyanazt a teljes többváltozatos ajánlathalmazt ellenőrzi. A régi csomagok ellenőrzése változatlan ágon marad.
5. Az eredmények, export és klubnézet szelvényazonosító szerint őrzi meg a változatokat. A személyiségoldalon külön számlálhatóak, nem írják felül egymást egy profilkulcsos táblában. A befejezett agent állapota nem azonos a szelvény READY vagy fogadható állapotával.
6. A klubnézet csak a mentett `ten-agent-decision-run-v1` riportnál vált a tíz aktív tulajdonosra: öt eredeti specialista és öt kiválasztott klubtag. Mindegyik alatt az összes saját kanonikus változat megjelenik; a számláló szelvényt számol, nem tulajdonost. A közös Gauss/Kepler/Laplace külön eszközcsoport marad. Régi riportnál a húszfős klub és a korábbi megjelenítési szabály változatlan.
7. Az owner mentett `COMPLETE`, `ABSTAINED` és `PARTIAL` állapota külön látható a hiányzó szelvénytől. A befejezett, de kanonikus kártyát nem adó döntés nem lesz kitalált kihagyás vagy sikeres szelvény; a mentett DRAFT kártya nem kap READY jelzést. A nézet nem írja át a felhasználó profilbeállításait. A tízes módban a névsor checkboxai és mentése zároltak: az egyedi szüneteltetés a meglévő Szent Grál tagsági vezérlő feladata. A régi klub explicit részvételi szerkesztése változatlan.
8. Az új backend-vetítés fixture-je konstruktor és DB nélkül ellenőrzi a tíz tulajdonos húsz kártyáját, a státuszokat és a legacy ágat. Az új külön Node-fixture a tényleges `app.js`-t injektált DOM-mal ellenőrzi, API/böngésző/üzleti futás nélkül. Ez megjelenítési szerződésellenőrzés, nem valódi böngészős vizuális elfogadás.
9. A fő szelvénykártya és a klubváltozat ugyanazt a mentett saját opcióindokot, végső mérlegelést és a két szimulátorszerep rövid értelmezését mutatja. A hiányzó történeti eredmény külön ISMERETLEN, nem nulla kockázat. A nézet nem kér új elemzést, nem mutat technikai hashlistát, és a régi, agentmeta nélküli kártyához nem talál ki magyarázatot.
10. Részleges új futásban csak ténylegesen új saját kártyát kiadó tulajdonos válthatja le a korábbi jogosult aktív indexét. A befejezetlen, kártya nélkül maradó vagy kifejezetten kihagyó tulajdonos régi ticket- és ajánlatindexe együtt megmarad; az új kihagyás nem kap külön visszavonási jogot. A régi payload és a legacy visszavonási szabály változatlan.

## A Nightly döntési azonossága

1. A meglévő napi stage-azonosító már a gyűjtés és az agentdöntés előtt bekerül a koordinátorba. Döntés után futásazonosító, kártya vagy annak hashkötése nem írható át.
2. A tízes ág nem készít harminc üres régi profilhelyet, és nem futtat utólag másik választási vagy publikációs szabályt. A teljes tulajdonosi és változathalmaz kerül a ledgerbe, exportba és csomagfagyasztásba.
3. Legalább tizenöt kártya mellett is részleges az eredmény, ha valamelyik résztvevő saját döntése hiányos. A régi, más kártyacsoportokat ellenőrző audit helyett a hiányzó új fogyasztói audit névvel látható; nulla vizsgált kártya nem jelent sikert.
4. Az új ág bizonytalan vagy sikertelen generálása nem vált automatikusan második fizetett kísérletre. A régi ág működése megmarad. Az időzítők ezen illesztés tesztelése közben kikapcsolva maradnak.
5. A fixture-rendszerteszt saját ideiglenes SQLite-adatbázison ellenőrzi a teljes húszkártyás mentést és fagyasztást, a hibás kötés elutasítását, a hiányos tulajdonost és az ismétlés tiltását. Szolgáltatót, modellt vagy valódi éjszakai feladatot nem indít.

## Részleges futás és korábbi ajánlatok

Új aktív ajánlat csak olyan tulajdonos régi, jogosultan lecserélhető indexét válthatja fel, akinek tényleges új kanonikus kártyája született. Félbeszakadt döntés, tartózkodás vagy közzé nem tehető választás önmagában nem törli a korábbi kártyát. Adathiány vagy teljes csapatszünet üres riportja sem vonja vissza a régi ajánlatokat. A ticket- és ajánlatindexnek ebben egyeznie kell; a régi payloadok és kézi fogadások érintetlenek maradnak. A korábbi ág explicit visszavonási viselkedése nem változik.

## Kéréskori historikus eredményszimuláció

A meglévő történeti adatokból mind a tíz ügynök saját opcióira leíró eredményszimulációt kap. A részletes, elfogadott megvalósítási sorrend: `artifacts/ten-independent-agents-20261112/historical-simulator-source-plan/PLAN.md`. A kérés a tulajdonoshoz, módszerhez, saját opciókhoz, időablakhoz és valódi policy-hashhez kötött; a teljes megfelelő minta részt vesz. A pontos piac találat/nem találat/hiány arányai és lefedettsége számolható, történeti profit, korabeli döntés vagy előrejelzési valószínűség nem található ki belőlük. A félidő, venue, óra és eredményhiány helyi UNKNOWN marad. A már meglévő szigorú korabeli áras backtest-fogyasztó külön változatlanul megmarad. Ez az új megvalósítás a korábbi, kizárólag kész backtest-eredményre váró `historical_reader=None` összeállítást egészíti ki; elkészültét és tényleges használatát külön tesztnek kell igazolnia.

## Teljes nap a tényleges bemeneti korláton belül

Az első, mentett nagy napon végzett külön méretellenőrzés 133 meccs és 3533 ajánlat mellett 51 774 465 karaktert mért. Ez nem fért a változatlan 360 000 karakteres hostkorlátba, ezért ez a nézet nem telepíthető. A konkrét következő javítás terve: `artifacts/ten-independent-agents-20261112/decision-view-compact-candidate-v2/DESIGN.md`.

Minden meccs és eredeti ajánlatazonosító megmarad olvasható, oszlopos leírásban; a közös tények és az azonos struktúrák egyszer szerepelnek. A piacfüggő jellemzőket nem vonjuk össze pusztán meccsazonosság alapján. A hosszú nyers sorlisták és ismételt forrástestek helyén névvel jelölt lenyomat, darabszám és eredeti adatkapcsolat marad; ez nem állítás minden nyers sor modell általi elolvasásáról. A saját végső döntéshez az ügynök saját jelöltjeinek részletes értékei tartoznak. A javításnak pontos visszafejtési tesztet, teljes napi lefedettségi tesztet és a nagy mentett inputon új méretellenőrzést kell teljesítenie; első-N szűrés vagy korlátemelés nincs.


## 2026-11-12: köztes rendszerellenőrzés és fennmaradó méretkorlát

A kompakt V2 első mérése minden 133 meccset és 3533 ajánlatot pontosan megtartott, de 9 260 774 karaktert adott. Ez továbbra is túl nagy. A következő, egyszeri V3 átalakítás konkrét terve a `decision-view-semantic-normalization-v3/DESIGN.md` artifactban rögzített: ismételt történeti sorok és modellrészletek név szerinti közös táblái, minden szabad szövegű hír és bizonytalanság megtartása. A 360 000-es korlát nem nő. Ha ez sem fér el, külön adat-hozzáférési és kéréskeret-terv szükséges; további vak tömörítési sorozat nem indítható.

A mentés/export/klubfelület/éjszakai illesztés korábbi 170 tényleges esete és 35 DOM-állítása sikeres. Az új történeti eszköz, teljes input és saját döntésadatok együtt 206 célzott esetből 196-ot teljesítettek. Kilenc sikertelen eset hiányos, kézzel épített klubtesztlapokra, egy eset a már megvalósított tiszta bizonyíték-ellenőrző tesztbeli tiltására vezethető vissza. Az eredeti eredmény megőrzött; külön tízes, módosított fixture-ellenőrzés készül, a valódi szabályok és állítások változatlanok. Ez nem éles rendszerelfogadás és nem új tényleges szelvénygenerálás.


## 2026-11-12: tényleges méretkorlát és adatelérés

A V3 egyszeri mérés 1 670 882 karakter: minden 133 meccs, 3533 ajánlat és eredeti ajánlatmező pontos, de továbbra is túl nagy. További tömörítési sorozat helyett az előre rögzített `artifacts/ten-independent-agents-20261112/source-access-v1/DESIGN.md` szerinti korlátozott, tulajdonoshoz kötött olvasóeszköz készül. Minden agent teljes napi naptárat és 12 adatcsalád-lefedettséget kap; a részletek ugyanabból az immutable pillanatképből kérhetők, saját olvasási naplóval. A 10 korábbi hibás fixture külön ellenőrzése sikeres, az új integrációs ellenőrzés még folyamatban. Éles generálás és termelési elfogadás nincs.


## Megvalósítási checkpoint — 2026-11-12

A teljes helyi forrásolvasásos 10 agent × 2 saját szelvény rendszerellenőrzés sikeres, tényleges SQLite-visszaolvasással. A részletes, tényleges eredmények és az éles bevezetés lépései: `ARTHUR_TEN_AGENTS_VALIDATION_20261112.md`. Ez munkapéldány-elfogadás; production telepítés és valódi új reasoning-futás még nem történt. A történeti sikertelen kísérletek megőrzöttek.
