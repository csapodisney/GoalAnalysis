# Arthur fejlesztési és végrehajtási feladatlista

Állapot: 2026-10-26
Aktuális számozott lista: **136 kész / 31 nyitott / 167 tétel**. Az eredeti 215-ös
követelménylista külön **176 zöld / 39 piros-részleges**; a két lista nem összeadandó.
A meglévő normál futás egyszeri workflow-helyreállítása és elfogadott runtime-ja
lezárt: Daily ON, legacy Daily223 OFF, 0 új normál generálás. A 74 függő eredmény,
a részleges generálási csomag és az eredeti ellenőrzősegéd exit1 hibája megmaradt;
külön csak olvasó egyeztetés és első runtime-visszaállítás igazolta a tényleges állapotot.
[Részletes riport és aktuális teljes 215-ös leltár](status/2026-10-26-daily-workflow-recovery/REPORT-CLOSED.md).
A háttérkapu leltára elkészült: a normál időzítő 19 friss deltaoldalt feldolgozott;
a régi ötéves kurzor 496/1220 maradt. Az audit óráján COOLDOWN, a másodlagos kvóta
QUOTA_REFRESH_REQUIRED; ebből nem következik új kézi HTTP-engedély.
[Friss csak olvasó bizonyíték](status/2026-10-26-historical-background-current-allowance/REPORT.md).
A fogyasztói leltár elkészült: az új 19 oldal 271 kanonikus rekordja fizikai egyezésű,
de raw/result availability-receipt nélkül maradt; a strict eredményolvasó 1655-öt fogad el.
A kombinált natív SHADOW olvasó a 10001. sornál fail-closed; külön csoportleltár nem modellpromóció.
[Friss fogyasztói bizonyíték](status/2026-10-26-historical-admission-consumer-coverage/REPORT.md).
A külön row-budget jelölt 11 595 forrásborítékot veszteségmentesen megőrzött; 2 613 kiválasztott matematikai input egyezik. 69 eredeti + 13 új kontroll PASS. A minimális új forrás saját 5894-es teljes és három új replay elfogadása lezárt; a runtime igazolt, Daily ON.
[Aktuális forrás és folyamatban lévő ellenőrzés](status/2026-10-26-native-history-row-budget/SOURCE-VALIDATION-PENDING.md).
Saját natív row-budget scope: 5894 teljes / 89 célzott / 10 adapter / 3 új actual replay ×8 PASS; root/runtime/Daily ON/legacy OFF igazolt. Nem teljes 5 éves/PIT-fedés vagy LIVE-promóció.
[Lezárt saját bizonyíték és aktuális 215](status/2026-10-26-native-history-row-budget/REPORT-CLOSED.md).
Következő egyetlen pont: historical-availability-producer-contract, először csak olvasó/artifact kontraktus; nincs új capture/importbatch/HTTP-claim.

Cél: a rendelkezésre álló, mérkőzés előtt ismert adatok tényleges és
ellenőrizhető felhasználása a szelvényképzésben, a meglévő működő logikák
védelmével.

## Nem változó termékkövetelmények

- [ ] Napi célcsomag: **20–30 READY szelvény**, ha van hozzá elegendő,
  időben érvényes és szakmailag alátámasztott jelölt.
- [ ] Nem-DAILY szelvények cél-összszorzója: **10–50**. A rendszer nem tehet
  gyenge lábat egy szelvényre csak azért, hogy elérje a 10-es szorzót.
- [ ] Eredménycél: **legalább 3 nyertes szelvény naponta**, nyújtott cél 4.
  Ezt napi és gördülő 30 napos KPI-ként kell mérni; csak lezárt eredményből.
- [ ] Alapértelmezett elszámolási tét: **1 EUR szelvényenként**.
- [ ] A `DAILY_223` teljes logikája, küszöbei és kiválasztása változatlan.
- [ ] Azonos mérkőzés több szelvényen szerepelhet, de a napi READY csomagban
  a mérkőzéskitettség legfeljebb 40–50%.
- [ ] Teljesen azonos szelvény nem publikálható kétszer; 50% feletti
  hasonlóság lágy pontbüntetés, nem automatikus törlés.
- [ ] A rendszer adat- vagy jelölthiány esetén nem gyárt töltelékszelvényt.
  A hiányzó darabszámot és annak okát külön jelenti.

## Kötelező mérnöki védőkorlátok

- [ ] Minden fejlesztés kis, visszafordítható egységben készüljön.
- [ ] Egy munkafázis egyszerre csak egy adat- vagy döntési réteget változtathat.
- [ ] Adatbázis-módosítás csak bővítő, idempotens `expand → backfill → validate
  → switch` migráció lehet. Oszlop vagy tábla törlése külön későbbi feladat.
- [ ] Minden új döntési hatás feature flag mögött, először `SHADOW` módban
  fusson.
- [ ] A régi és új számítás ugyanazon változatlan napi bemeneten legyen
  összehasonlítható.
- [ ] Minden modell- és szabályverzió, adat-időhatár, forrás és konfigurációs
  hash kerüljön a rögzített futásba.
- [ ] Sikertelen új modul esetén az előző stabil útvonal maradjon használható;
  részleges eredmény nem írhatja felül a stabil adatot.
- [ ] Egy szakértő hibája nem állíthatja le a teljes csomagot, de az érintett
  adat nem válhat automatikusan nullává vagy pozitív jellé.
- [ ] Nincs korlátlan LLM-párbeszéd: Astra/Codex jelöltellenőrzés, egy Grok
  csomagreview, legfeljebb egy ellenőrzött javítási kör.
- [ ] A 15 perces zárolás adatlekéréskor, kiválasztáskor és publikáláskor is
  kötelező.
- [ ] A már rögzített vagy megjátszott napi csomag visszamenőleg nem írható át.

## Vétók és hibás szelvények elleni szabályok

### Kemény vétó – nem kerülhető meg

- [ ] Ismeretlen vagy ütköző mérkőzésazonosító.
- [ ] Lejárt, jövőből származó vagy időbélyeg nélküli adat.
- [ ] Már elkezdődött, törölt vagy a 15 perces zárolásban lévő mérkőzés.
- [ ] Nem ellenőrizhető odds, bookmaker vagy piaci kimenetel.
- [ ] Ugyanaz a mérkőzés kétszer ugyanazon a szelvényen.
- [ ] Teljesen azonos szelvényduplikáció.
- [ ] Modellvalószínűség forrásadat vagy modellverzió nélkül.

### Lágy kockázati jel – rangsorol, de önmagában nem töröl

- [ ] Magas odds, ritka bajnoksági esemény, részleges csapattörténet.
- [ ] Más szelvényekkel való részleges átfedés.
- [ ] Bizonytalan kezdő, motivációs vagy híralapú információ.
- [ ] Kupa-, válogatott- vagy rotációs kockázat.
- [ ] Astra vagy Grok kifogás, ha nincs hozzá ellenőrizhető adatforrás.

### Veto-Override

- [ ] Csak lágy vétót írhat felül.
- [ ] Strukturált indoklás, forrás, időbélyeg és konkrét mérkőzés/piac kell.
- [ ] A felülbírálás nem módosíthat oddsot vagy modellvalószínűséget.
- [ ] A `DAILY_223` módosítására nem használható.

---

## 0. fázis – Stabil kiindulópont

- [x] **0.1** A tényleges szelvény-előállítási folyamat dokumentálása.
  - Bizonyíték: `docs/ARTHUR_TICKET_GENERATION_FLOW.md`.
- [x] **0.2** A DAILY_223 változatlanságát ellenőrző regressziós tesztek.
- [x] **0.3** Jelenlegi játékos/xG rangsorolási útvonal és forrásnyom.
- [x] **0.4** Arany bemeneti csomagok készítése legalább három napra:
  sokmeccses hétvége, kevésmeccses hétköznap, vegyes kupa/válogatott nap.
  - 2026-10-20: három offline csomag; 3/3 valid, 12/12 hibateszt.
    Riport: `artifacts/golden-inputs-20261020/REPORT.md`.
    DAILY_223 7/7 hash egyezik; 343 meglévő fájl és 984 ledger-sor változatlan.
    A 0.5 külön felhatalmazásig nem indul.
- [x] **0.5** Az arany csomagokhoz rögzíteni a jelenlegi szelvényeket,
  adatlefedettséget, futási időt és szolgáltatói hívásszámot.
  - 2026-10-20: jelenlegi koordinátor és helyi modellek 3×2 offline futása;
    minden pár teljes kimenete azonos. Riport:
    `artifacts/golden-baseline-20261020/REPORT.md`.
    Új külső kérés 0; az archivált számlálók külön megőrizve.
    DAILY_223 előtte/utána 7/7; nincs éles publikáció. Megállás a 0.5 után.
- [x] **0.6** Egyetlen géppel olvasható `feature_usage` jelentés létrehozása:
  `USED_DIRECT`, `USED_RANKING`, `USED_REVIEW`, `STORED_ONLY`, `MISSING`.
  - Megvalósítás: `decision_data_usage`; a dashboard „Felhasznált adatok és
    napi célkeret” részében is látható.

**Kilépési feltétel:** minden további módosítás előtt reprodukálható a jelenlegi
eredmény, és byte-szinten igazolható a DAILY_223 változatlansága.

## 1. fázis – Egységes adat- és azonosítómodell

- [x] **1.1** Kanonikus entitások: versenysorozat, szezon, csapat, játékos,
  mérkőzés, piac, kimenetel és bookmaker.
- [x] **1.2** Szolgáltatói azonosítótérkép TheStats ↔ API-Football ↔
  Football-Data ↔ The Odds API között.
- [x] **1.3** Csak determinisztikus párosítás: szolgáltatói ID vagy ellenőrzött
  csapatnév + liga + kezdési idő. Fuzzy találat legfeljebb review-jelölt.
- [x] **1.4** Forráselsőbbségi resolver mezőnként:
  - sportadat: TheStats → API-Football;
  - odds célállapot: TheStats → The Odds API → API-Football;
  - időjárás: Open-Meteo;
  - hírek: Grok/X és ellenőrzött nyilvános források.
- [x] **1.5** Ütköző forrásoknál megőrizni mindkét értéket; a kiválasztott érték
  mellett `resolution_reason` és frissességi sorrend legyen.
- [x] **1.6** A teljes aktív ligakészlet TheStats-lefedettségi térképe.
- [x] **1.7** Forrásonkénti hiány- és fallback-mátrix a dashboardon.

**Tesztkapu:** nincs név alapján tévesen összekapcsolt mérkőzés; a fallback nem
írja felül a frissebb elsődleges értéket; minden döntési mező forrásolható.

## 2. fázis – Pont-időhelyes történeti adatbázis

- [x] **2.1** A TheStats, Football-Data és API-Football eredmények egységes,
  duplikációmentes mérkőzéstáblába rendezése.
- [ ] **2.2** Első körben a jelenlegi naptól öt évre visszafelé haladó import.
  - 2026-10-20: implementáció validált (2692 teszt, 3 arany visszajátszás); a teljes adatimport MÉG NYITOTT: 1180 főablak + 20 friss deltaablak. Nincs október 20-i SUCCESS archív bizonyíték; a napi elsőbbségi kaput nem kerültük meg. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/2.2/REPORT.md`.
  - 2026-10-21: a fizikai import elindult, de az öt év nincs kész: főcursor 60/1220, friss delta 2/20. A validált, kvóta- és napi rögzítési kapus automata folytatás aktív (2.8); bizonyíték `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/2.8/live-proof.json`.
- [x] **2.3** Külön megfigyelési idő: `event_time`, `published_at`,
  `observed_at`, `available_at`.
- [x] **2.4** Odds-pillanatképek megőrzése bookmaker, piac és kimenetel szerint.
- [x] **2.5** Játékos-, hiányzó-, kezdő-, időjárás- és híresemények kapcsolása a
  kanonikus mérkőzéshez.
- [x] **2.6** Minden tanítóhalmazhoz verzió és tartalmi hash.
- [x] **2.7** Look-ahead leakage teszt: a modell semmilyen, a döntési idő után
  elérhető adatot nem kaphat.
  - 2026-10-21: a 2.7 szigorú SHADOW teszt-/modellbemeneti rétege validált (3056 PASS, 3 golden); ez nem az éles portfólió átkapcsolása. A régi aktív adatút és Pi/Elo holdoutok időhelyességi integrációja a 11.2/11.3/11.6 bevezetési kapuknál nyitott; lásd 2.7/REPORT.md és progress.pending_live_integrations.
- [x] **2.8** Háttérimport csak a napi csomag rögzítése után és a maradék
  API-kapacitásból futhat.

**Tesztkapu:** időrendi visszajátszáskor ugyanaz a futás ugyanazt az adatnézetet
kapja; jövőbeli adat esetén kemény teszthiba keletkezik.

## 3. fázis – TheStats odds elsődlegességének árnyékpróbája

- [x] **3.1** TheStats 1X2, totals, BTTS, DNB, handicap és támogatott félidős
  piacainak normalizálása a jelenlegi belső odds-sémára.
- [x] **3.2** Bookmaker- és quote-időbélyeg megőrzése.
- [ ] **3.3** Legalább hét egymást követő napos párhuzamos összehasonlítás a
  The Odds API és API-Football értékeivel.
  **2026-10-22 — nyitott kapu:** a későbbi felhasználói utasítás mindkét régi élő API-t
  kivezette; a párhuzamos próba és a régi szolgáltatókra visszakapcsolás nem indítható el.
  Csak megőrzött archív ár vizsgálható. A hét napos feltétel nincs teljesítve
  (3.3/REPORT.md); az adatleltár után a további alpontok sorosan folytathatók.
- [ ] **3.4** Eseménylefedettség, piaclefedettség, áreltérés, késés és hibaarány
  mérése ligánként.
  - 2026-10-22: a SHADOW mérési réteg validált (3186 teszt, 3 tényleges modell-replay, DAILY-guard és 3745 ledger-sor változatlan). 36/36 árazott esemény, natív 216/216 piaccsalád/esemény hely. A teljes 3.4 NYITOTT: szolgáltatói quote-idő, igazolt kortárs szolgáltatóközi árpár és teljes ligánkénti HTTP-attempt napló hiányzik. A 1600 tömör totals-alias külön UNSUPPORTED; a nyers árak megvannak. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/3.4/REPORT.md`.
- [ ] **3.5** Csak azon ligáknál előléptetni, ahol az egzakt eseményfedettség
  legalább 95%, és az időbélyeg-frissesség megfelel.
  - 2026-10-22: kapuvizsgálat kész, előléptetés 0; a teljes feltétel NYITOTT. EL18/18 és ECL18/18 esemény árazott, mindkét liga18/18 valós receiptje friss, de a szolgáltatói quote-idő ismeretlen. A receipt nem helyettesíti a quote-clockot; 3.3/3.4 adatkapuk nyitottak. A korábban engedélyezett natív migráció változatlan. 7 határeset PASS; változatlan forráshoz korábbi3186teszt+3tényleges replay újrahasznosítva, nem újrafuttatva. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/3.5/REPORT.md`.
- [x] **3.6** Piaconkénti fallback; egy hiányzó BTTS ne váltsa vissza az egész
  mérkőzést a régi szolgáltatóra.
  - 2026-10-22: kész a későbbi TheStats-only userutasítás szerint. Egy hiányzó/hibás BTTS külön hiányjelzést ad, az 1X2/összgól és más elérhető piac megmarad; régi élő API nem hívódik. Az üres opcionális választ korábban sikernek számoló hibát egyetlen adapterfeltétel javítja. 97 célzott és3195teljes teszt PASS,3tényleges modell-replay PASS;36valós payload pontos egyezése és36külön szintetikus hiánypróba. DAILY-guard+3745ledger-sor változatlan. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/3.6/REPORT.md`.

**Visszaállítás:** egy konfigurációs kapcsolóval azonnal visszaállítható a
The Odds API → API-Football jelenlegi útvonal.

**2026-10-22 — elsőbbségi userutasítás:** a fenti eredeti többprovideres visszaállítási tervet a későbbi kivezetés felülírja: The Odds API és API-Football élő használata tiltott. A natív TheStats-út marad, a hiányzó piac külön hiány; a régi parserek kizárólag archív/injektált offline használatra maradnak meg.

## 4. fázis – xG és játékosok beemelése az alapmodellekbe

- [x] **4.1** A mostani ±4%-os xG-rangsorjel megtartása kontrollágként.
- [x] **4.2** Csapat támadó- és védekező xG-ráták időbeli, hazai/idegen és
  ellenfélerősség szerinti normalizálása.
  - 2026-10-22: a SHADOW ráta-normalizálás kész és validált. 90napos időfelezés,365naplookback, külön liga/szezon/hazai-idegen alap, az éppen vizsgált meccset kizáró ellenfélerősség, explicit prior és hiányjelzés. 18valós StatsHTTP/18újstats,21valós availability-receipt;19valós match-xG a kontrollmintában. Werder3hazai+3idegen jellemző kvalifikált; nem teljes ligatörténet vagy kalibrált előrejelzés. 39újteszteset,84célzott és3234teljes PASS,3tényleges kontroll-replay, DAILY+3745ledger-sor változatlan. Éles döntési hatás0; a4.3és további kalibráció/bekötés nyitott. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/4.2/REPORT.md`.
- [ ] **4.3** A gólalapú GAUSS/KEPLER/LAPLACE eredmények és az xG-modell külön
  valószínűségeinek kalibrált ensemble-ja. Nyers pontszámokat tilos összeadni.
  - 2026-10-22: a 4.3 teljes követelménye NYITOTT. Izolált SHADOW Poisson-probabilitások, családonkénti temperature-kalibráció, purged kronologikus holdout és adatfüggő ensemble-súlyozás implementálva; liga<3 xG-meccsnél súly0. 32 új határteszt, 116 célzott és 3266 teljes teszt PASS, 3 tényleges kontrollmodell-replay PASS; DAILY és 3745 ledger-sor változatlan. A tényleges19 xG-meccses mintában0 különböző kvalifikált csapatpár és0 valós kalibrációs pár. Nincs valós walk-forward javulás igazolva, nincs live bekötés/előléptetés. A külön szintetikus próbák nem futballeredmény-bizonyítékok. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/4.3/REPORT.md`.
  - [x] **4.3.N1** Célzott, kvótavédett match-xG forráspótlás a már igazolt natív csapattörténetekhez; cache-first, valódi nyugták, pontos szabályos játékidő, SHADOW; hiány nem válhat nullává.
    - 2026-10-23: lezártSHADOWforráság;19→73valósxGmeccs,54újelfogadott/6kizárt/61GET,1nyersxGbecslés16hiányoscélmellett.4845full/143target/22új/3újactualreplayPASS. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/4.3.N1/REPORT.md`.
  - [x] **4.3.N2** Újonnan számolt külön xG-előrejelzések hiteles prospektív mentése tényleges forrás-/dataset-/modellnyugtával, mérkőzés előtt; régi artifact nem válhat visszadátumozott jóslattá, SHADOW.
    - 2026-10-23: valódi prospektívxGcapture/calibrationinput7piac/1meccs/0eredmény,73forrásmeccs;1frissmetadataGET,0odds/LLM/ledgerírás.4874full/200target/29új/3ÚJactualreplayPASS.4.3ensemble/LIVE nyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/4.3.N2/REPORT.md`.
- [ ] **4.4** Több kulcsjátékos kiválasztása: támadó, kreatív játékos,
  védekező kulcsember és kapus.
- [ ] **4.5** Játékosjellemzők: gól, xG, gólpassz, xA, játékperc, kezdések,
  csapatrészesedés és rendelkezésre állás.
- [ ] **4.6** Posztonkénti hiányhatás és helyettesíthetőség; több hiányzó
  összhatása nem lehet egyszerű összegzés.
- [x] **4.7** Prediktált kezdő csak bizonytalansági jel. Megerősített kezdő
  erősebb, időbélyeghez kötött jel.
- [ ] **4.8** Piaconkénti felhasználás: támadóhiány Over/BTTS, kapus- és
  védőhiány Over/BTTS/1X2, nem egyetlen univerzális szorzó.
- [x] **4.9** Missingness-feature: a hiányzó adat ne jelentsen egészséges vagy
  gyenge játékost.

**Tesztkapu:** walk-forward próbán javuló kalibráció vagy információs érték;
romló Brier/log-loss esetén az új hatás SHADOW marad.

## 5. fázis – Mérkőzéstípus, terhelés és motiváció

- [x] **5.1** Versenytípus: liga, hazai kupa, nemzetközi klubtorna,
  selejtező, válogatott és barátságos mérkőzés.
  - 2026-10-22: kész a SHADOW versenytípus-jellemző és a mentés utáni archívum. 29 tényleges katalógusazonossággal igazolt natívID-s szabály; család, résztvevő, szakasz és selejtező/barátságos jelző külön. 61 új eset,78 célzott és3327 teljes teszt PASS,3 tényleges kontrollmodell-replay PASS. A napi36 meccs családja igazolt INTERNATIONAL_CLUB;36 szakasz továbbra UNKNOWN, nem kitalált. 1 tényleges mostani availability-witness,0 új API/LLM/ledgerírás; DAILY+3745 ledger-sor változatlan. Új napi források folyamatos tanúsítása2.7LIVEPIT maradék, a besorolás nem új éles döntési hatás. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.1/REPORT.md`.
- [ ] **5.2** Pihenőnapok, utazási távolság, előző/következő mérkőzés és
  menetrendi sűrűség.
  - 2026-10-22: az5.2 teljes követelménye NYITOTT. SHADOW menetrendi réteg és post-save archívum validált; 36/36fixture és72/72csapat teljesnek jelzett TheStats-naptára fizikailag megvan. 72újHTTP,72valós availability-receipt,2cachehit;0külsőelemzőLLM/legacyAPI/ledgerírás. 103célzott és3355teljes teszt PASS,3tényleges kontrollmodell-replay PASS;DAILY+3745ledger-sor változatlan. Valódi stadion-ID/koordináta0, útvonal/végsőlefújás nem kitalált; gap és távolság csak proxy. Folyamatos kvótavédett frissítés és cache/PIT receiptgeneráció még nincs aktiválva;multi-page elsőoldal nem teljes naptár. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2/REPORT.md`.
  - [x] **5.2.N1** A hiányzó stadionkoordináták megbízható ingyenes forrásának tényleges auditja és pontos natív meccshelyszínhez kötése; az igazolt helyszínadat eljuttatása a meglévő időjárás/utazás SHADOW-olvasókhoz. Forrás/azonosság/availability igazolás nélkül UNKNOWN, nincs városközpont vagy hazai alapstadion mint meccshelyszín-fallback.
    - 2026-10-22: hat forrás-/hash-/availabilitykötött WikidataWGS84helyszín, külön hivatalos azonosságforrás;6valódi stadionforecast/6weatherreceipt/19kutatásilábkontextus.1igazolt0kmhelyszínproxyazonosstadionhoz,nemcsapatútvonal. 4714full/198target/33új/3actualgolden/RuffPASS.14sportGET+6freeforecast,LLM/ledgerírás0. Lefedettség és teljes5.2/N23NYITOTT. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N1/REPORT.md`.
  - [x] **5.2.N2** A hat ismert korábbi stadion és a többi napi helyszín földrajzi lefedettségének soros bővítése, eredeti ingyenes forrásokkal és pontos natív meccshelyszín-kötéssel; a hiányzó előző–célmeccs távolságok tényleges ellenőrzése. Nincs névfuzzy/home-stadium/city-centroid fallback vagy automatikus modellhatás.
    - 2026-10-23:23igazoltstadion/17napi helyszín+forecast/66kutatásilábkontextus/7távolságproxy.46target/13új/4727full/3ÚJactualgolden/RuffPASS;DAILY7guard/math és3816ledger változatlan.18TheStatsGET+27freeforecastkísérlet,LLM0. Teljes5.2/LIVEés27travelhiány nyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N2/REPORT.md`.
  - [x] **5.2.N3** A már azonosított12hiányzó korábbi natív meccsrészlet pontos cache-/kvótavédett megszerzése és eredeti receipt/azonosság ellenőrzése; nincs helyszínkitalálás, LLM vagy szelvényátírás.
    - 2026-10-23: 12/12 tényleges korábbi natív részlet/HTTP-receipt/identity ellenőrzött; 11 helyszínnév+város, 1 szolgáltatói null UNKNOWN.12 GET, LLM/ledgerírás0; alkalmazáskód változatlan, korábbi 4727teszt/3actualreplay csak azonos forrásra hivatkozva, nem új futásként. DAILY/3816ledger megmaradt; új koordináta0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N3/REPORT.md`.
  - [x] **5.2.N4** A 11 újonnan igazolt korábbi létesítmény ingyenes koordinátaforrásának pontos auditja és időhelyes regiszterbővítése. A régi hatos és 23-as kiadás cutoff-története maradjon meg; null venue nem helyettesíthető home-stadium/city/fuzzy becsléssel.
    - 2026-10-23:34igazoltstadion/17aktuálisweathercontext/66kutatásiláb/19távolságproxy; három eredetiPITkiadás.66target/20új/4747full/3ÚJactualgolden/RuffPASS;DAILY 7 guard/math és3816ledger változatlan.18TheStatsGET,forecast/LLM/ledgerírás 0.15travelhiány:14MISSING_CALENDAR+1providerNULL. Teljes5.2/LIVE nyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N4/REPORT.md`.
  - [x] **5.2.N5** A14hiányzó teljes versenyközi csapatnaptár pontos cache-/kvóta-/receiptvédett beszerzése, teljes lapozás és célmeccs/időablak ellenőrzése; nincs helyszínkitalálás, LLM vagy szelvényátírás.
    - 2026-10-23:14/14új teljes naptár,34/34csapatoldal/66kutatási láb igazolt SHADOW-használat;21távolságproxy.14GET,0LLM/ledgerírás/kódmódosítás;4747teszt/3actualreplay csak bitazonos N4forrásra hivatkozva,0új futás. DAILY és3816ledgerstabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N5/REPORT.md`.
  - [x] **5.2.N6** A11újonnan azonosított hiányzó korábbi natív meccsrészlet cache-/kvóta-/receiptvédett begyűjtése és pontos azonossági ellenőrzése; már ismert szolgáltatói null helyszínt ne gyűjtsön újra, nincs helyszínkitalálás vagy új modellhatás.
    - 2026-10-23:11/11valós natív részlet/receipt/identity PASS;10helyszínnév+város/1új szolgáltatói null.11GET,0LLM/ledgerírás/kódmódosítás;4747full/3actualreplay csak bitazonos N4forrásra hivatkozva. DAILY/3816ledgerstabil;17célhelyszínóra lejárt,nem állít friss legkontextust. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N6/REPORT.md`.
  - [x] **5.2.N7** A10ténylegesen azonosított korábbi stadion ingyenes koordinátaforrásának pontos ellenőrzése és időhelyes regiszterbővítése; korábbi kiadások/hash-ek megőrzése, szolgáltatói null marad UNKNOWN. Új forrásdiffhez teljes teszt és3új tényleges modellreplay; aktuális lábhasználathoz valódi friss céladat kell.
    - 2026-10-23:44igazoltstadion/17weather/34calendarteams/66weather+calendarlegcontexts/32distanceproxies;4eredetiPITrelease.4766full/85target/19újteszt/3ÚJactualgolden/RuffPASS;DAILY/3816ledgerstabil.18TheStatsGET/37publicGET/0újforecastLLMledger.2providerNULLUNKNOWN. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N7/REPORT.md`.
- [ ] **5.3** Rotációs kockázat a versenytípus, kezdőváltozás és meccsterhelés
  alapján.
  - 2026-10-22: SHADOW rotációs diagnosztika és post-save archívum validált; 72/72 napi csapatterhelés, de0 megerősített aktuális–előzőXI-pár. 32új eset,135célzott és3387teljes teszt PASS,3tényleges kontrollmodell-replay PASS. DAILY+3745ledger-sor változatlan;0API/LLM/újreceipt/ledgerírás. Nincs kupa=B-csapat következtetés vagy új éles hatás. Friss igazoltXI-k és folyamatosPIT gyűjtés nyitott, ezért a teljes5.3nem kész. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.3/REPORT.md`.
- [ ] **5.4** Tabellahelyzetből csak ellenőrizhető tét: kiesés, feljutás,
  nemzetközi hely, bajnoki esély, már eldőlt helyzet.
  - 2026-10-22: az5.4teljes követelménye NYITOTT. SHADOW tabelladiagnosztika és tiszta konzervatív top-K matematikai szerződés validált.2újTheStatsGET/2receipt igazolta a két72soros tabella35/36ellentmondó csapatduplikátumát;72napi csapat UNKNOWN,0tényleges tétszámítás. Hivatalos szabályregiszter NINCSintegrálva. 40új eset,125célzott és3427teljes teszt PASS,3valódi kontrollreplay PASS. DAILY+3745ledger-sor változatlan;0legacyAPI/LLM/ledgerírás,újéleshatás0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.4/REPORT.md`.
  - [x] **5.4.N1** Hivatalos versenyszabályok szezon/csoport/tie-break és időbélyegzett forráskötött SHADOW regisztere a tabellatét olvasóhoz; csak igazolt szabály, UNKNOWN megőrzése, nincs automatikus motivációs súly vagy LIVE.
    - 2026-10-22: N1forráskötött SHADOW-regiszter integrálva két eredeti UEFA2026/27kiadásra és pontos natív szezonra; 4681full/197target/42új/3actualgolden/RuffPASS. A teljes5.4és másligák lefedettsége nyitott, nincs új LIVEhatás. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.4.N1/REPORT.md`.
  - [x] **5.4.N2** Hivatalos hazai bajnoki szabályforrások pontos szezon-/szakasz-/tie-break kötött SHADOW-regiszterbővítése a jelenlegi natív célmeccsekhez, először 2. Bundesliga2026/27; korábbi kiadások/időhatárok megőrzése, UNKNOWN nem válhat kitalált tétté.
    - 2026-10-23: aktuális2026-08-04DFLszabály+natív2026/27scope;2célmeccskörnyezet,0valóspontkorlátmertfázisUNKNOWN.4903full/111target/29új/3ÚJactualreplayPASS;2sport+6publicGET,0LLM/ledgerírás. UEFA/PIT/DAILY/3816ledgerstabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.4.N2/REPORT.md`.
  - [x] **5.4.N3** A két DE2-célmeccs tényleges natív fázisforrásának receiptvédett ellenőrzése és a dokumentált explicit null szemantika bekötése; hiányzó kulcs UNKNOWN, nincs liga-/dátum-/forduló-/tabellainferencia.
    - 2026-10-23: dokumentált explicit null fázis szemantikája; 2 valódi meccs/4 csapatoldal konzervatív SHADOW pontkorlát. 4930 full/199 célzott/27 új/3 ÚJ actual replay PASS; 1 public GET, 0 sport/LLM. Korábbi prefix0, DAILY/3816 ledger stabil; LIVE nyitott. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.4.N3/REPORT.md`.
- [x] **5.5** Fizetési probléma, edzőváltás, csapatkonfliktus és egyéb humán
  állítás kizárólag időbélyeges, hivatkozható forrással.
  - 2026-10-22: SHADOW humánforrás-szerződés validált; közösGrok/NewsStore SALARY_PAYMENT/COACH_CHANGE/TEAM_CONFLICT/OTHER_HUMAN_FACTOR, explicitmetaadat és receiptvédelem. Valódi hírassertion/import/newsreceipt0,36napi fixture NO_ACCEPTED_HUMAN_CLAIMS; ez nem kockázatmentesség. A3legutóbbiGrok eredmény claims0;nemindultújfizetőskeresés. 29új eset,148célzott/3456teljesPASS+3valódi kontrollreplayPASS; DAILY+3745ledger-sor változatlan,API/LLM/ledgerírás0. Fizikaiadat és2.7LIVEPITgeneráció nyitott,ezért teljes5.5nincs kipipálva. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.5/REPORT.md`.
- [x] **5.6** Grok strukturált pro/kontra értékelés, majd Codex-egyetértési kapu.
  - 2026-10-22: javítva a natívTheStats Codex-adatátadási hiba: hiányzó7árforrásmező és quoted_atNone parserhiba. Exactpersistedrequest/pro-contra/teljes szelvény és receiptkötés, új configgrok-native-gate csakSHADOW;pozitívvéleményből csakwould_repair,nincs natív éles javítás. Valódi mentettGrokreview1daily223/actionabilityFalse,ezért helyesen0Codexkör; valódiárrekord adatátadásaPASS,nemvalódiújvélemény. 27új eset,128célzott/3483teljesPASS+3valódi modellreplayPASS; DAILY+3745ledger-sor változatlan,újAPI/LLM/ledgerírás0. Friss nemDAILY végigfutás ésforrásidő/tartalombizonyíték/LIVEpromóciónyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.6/REPORT.md`.
- [ ] **5.7** A visszamért motivációs jelekből legfeljebb korlátozott,
  verziózott rangsorhatás; mentális állapotot nem szabad kitalálni.
  - 2026-10-22: csak audit, nem kész rangsormodell. 107 célzott PASS; változatlan alkalmazás és 3745 ledger-sor. Tényleges elfogadott hír/receipt0, 72 csapat tabellatétje UNKNOWN; visszamért motivációs modell nincs. Nincs önkényes rangsúly vagy mentálisállapot-becslés; checkbox nyitva. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.7/REPORT.md`.
- [ ] **5.8** Nap-jellemzők – péntek, szombat, vasárnap – csak történeti
  ligánkénti validáció után, nem globális szabályként.
  - 2026-10-22: offline leíró audit 39522 receipt-igazolt öt éves eredménysorral, 42 külön forrás/verseny/óraszemantika csoportban; TheStats1143, CSV38379 külön. 633 receipt nélküli sor kizárt, 31746 tanulókohortból0 igazolt a történeti referenciahatáron. Nincs prediktív/oksági/profitvalidáció vagy új naphatás; checkbox nyitva. 59 célzott PASS; alkalmazás, DAILY és3745 ledger-sor változatlan, API/LLM0. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.8/REPORT.md`.
## 6. fázis – Időjárási szakértő kiterjesztése

- [x] **6.1** A jelenlegi három tanulmányból származó hipotézisek változatlan
  tudásbázisa.
- [x] **6.2** Csapat- és stadionazonosítóval kapcsolt történeti időjárás.
- [ ] **6.3** Hőmérséklet, csapadék, szél, széllökés és pályaállapot külön
  jellemzőként.
- [ ] **6.4** Piaconkénti validáció: Over/Under **kész és LIVE-kapuval védett**; BTTS, 1X2 és félidős piacokhoz külön holdout-validáció még szükséges.
  - 2026-10-22:6.4nem kész.106 célzott PASS(16új piac/periodhatárpróba); OU validációs kapu implementált,de nem igazolt natív piaci előny. Aktívweatherhistory14015legacy/0native;1776natív eredményből0 időjárásazonosság, 6 reanalízises és26forecastoslegacymeccs(nem26teljespár),natívkanonikusfélidő0. Következő javítás6.4.N1:strict natívweatherhistory SHADOW; nemlépünk még7.1-re. App/DAILY/3745ledger változatlan,API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4/REPORT.md`.
  - [x] **6.4.N1** Natív TheStats időjárási történeti bemenet SHADOW-ban; pontos identitás és eredmény/időjárás availabilityreceipt-védelem.
    - 2026-10-22:N1 natív SHADOW-olvasó kész;1143igazolt eredmény,633receipt-hiány, 0natívidőjáráspár/0félidő.27új/116célzott/3510teljesPASS+3ténylegesmodellreplayPASS;DAILY/3745ledger változatlan,API/LLM0. Szülő6.4nyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N1/REPORT.md`.
  - [x] **6.4.N2** BTTS,1X2 és félidős piacok saját SHADOW cél-/időhelyes holdout-szerződése; OU-hatás nem örökölhető, hiányzó adatnál nincs promóció.
    - 2026-10-22:N2hatpiac külön SHADOW cél-/rögzített időbeli holdout-szerződése kész. 33új/99célzott/3543teljesPASS+3újvalósmodellreplayPASS. Valódipiaci forecast0,prematchweatherpár0,protocol/forecaststore nemintegrált;szülő6.4nyitva. Mindenéleshatás0,DAILY/3745ledger változatlan. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N2/REPORT.md`.
  - [x] **6.4.N3** Valódi prospektív időjárási FORECAST-jellemző és már beengedett natív baseline-előrejelzés pontos forráskötött SHADOW-párjainak rögzítése. Csak tényleges elérhetőségi idő és15min-zárolás, eredeti receipt/fixture/venue-azonosság; nincs új időjárási valószínűség, illesztés, döntési hatás vagy késznek jelentett kalibráció.
    - 2026-10-23:5122full/287target/41új/3ÚJactualreplay/RuffPASS; 203valódi baseline-weather featurepár/15fixture,weatherp/fit/LIVE0. Régi baseline/DAILY7/3816ledger stabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N3/REPORT.md`.
  - [x] **6.4.N4** Az első natív történeti időjárási tanítóminta valódi forrásainak beszerzése pontos eseményhelyszínnel, meglévő olvasókkal, boundedkvóta/cache és aktuális receipt mellett. Reanalízis nem prematch forecast; nincs illesztés, kitalált modellhatás vagy LIVE-promóció.
    - 2026-10-23: az első20legfrissebb jogosult natív eredményből3valódi eredmény–ERA5pár/újreceipt;17sportGET+3freehistoryGET,0fit/LLM/ledger.14reviewedcoordinate-alias és3nativevenue/neutralhiány. VáltozatlanN3source:5122teszt/3actualreplay bizonyíték újrahasználva, nem újfutás. DAILY7/math/3816ledger stabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N4/REPORT.md`.
  - [x] **6.4.N5** AzN4-ben dokumentált14pontos natív stadionalias ingyenes, hivatalos létesítményazonossággal és Wikidata WGS84-entitásverzióval felülvizsgált koordinátaforrás-bővítése. Régi packet/PIT megőrzendő; újrelease valós availability, nincs fuzzy/hazaistadion/városközpont vagy hiányzó venue pótlása. Csak forrásregistry, nem újweatherbatch/fit/döntés/LIVE.
    - 2026-10-23:14jelölt korlátozottreview/12elfogadott/2UNKNOWN(Troyes287mkonfliktus,LaCartujaofficialURLError);regiszter44→56,actualeventbinding3→15/20,régiweatherpár3marad.5147full/benne208target/25új/3ÚJactualreplay/RuffPASS. DAILY7/math/3816ledgerstabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N5/REPORT.md`.
  - [x] **6.4.N6** A meglévő natív történeti REANALYSIS-olvasó nyerspayload/UTC-óra/mértékegység/érték/kérési koordináta/tényleges eseménystadion és saját availability szerinti szemantikai megerősítése, fit előtt. Csak meglévő olvasó/raw/receipt/PIT, nincs hálózat/újweather/illesztés/protokoll/rangsúly/LIVE; eredeti3pár/203feature invariancia és teljes teszt+3ÚJactualreplay.
    - 2026-10-23:nyersUTCóra/egység/érték/kérési koordináta/actualeventHTTP/result/stadion/ownavailabilitykapu validált;eredeti3valódipár,203featureexacthash/régi126fájl/DAILY7/math/3816ledgerstabil.5217full/97különtarget/70új/3ÚJactualreplay/RuffPASS. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N6/REPORT.md`.
  - [ ] **6.4.N7** Az N5tizenkét új pontos stadionjához az eredetiN4húszas eseménykörben legfeljebb12ingyenes ERA5kéréssel új valódi történeti időjáráspárok;0paidGET/LLM/fit/protokoll/rangsúly/LIVE. N6szigorúforráskapu/újvalósavailability/régi3pár+203feature/hashguard/bizonyíték/riport.2koordináta+3nativemetadataUNKNOWN külön marad.
    - 2026-10-23:RÉSZLEGES/DATA_PENDING:12tervezettből6igazolt új pár,összesen9.7freeGET/6siker/1URLErrorSTOP,5nemkért,0retry/sport/LLM/fit/ledger.6weatherhiány nyitott;2koordináta+3nativemetadata különUNKNOWN. Régi88weather/3pár/203feature/DAILY7/math/3816ledger stabil. VáltozatlanN6source5217full/97target/3actualreplay biz. REUSED,nemújfutás. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N7/REPORT.md`.
  - [x] **6.4.N8** A meglévő időjárási piaci SHADOW-protokoll valódi immutable tárolói provenance és első saját receipt/idempotensóra/readonlysource/PIT/resource ellenőrzése. Nincs dummyproductionprotokoll/fit/probability/API/LIVE. N7hat weatherforrás továbbDATA_PENDING,nincs retryitt. Újfull/3ÚJactualreplay/hashguard/fizikaibizonyíték szükséges.
    - 2026-10-23:valódi immutable protocolstore/elsőreceipt/actualraweredmény+weather+schedulekötés/PIT kész;actualprotocol0,rawboundtraining9,modelp0.5303full/216target/86új/3ÚJactualreplay/RuffPASS;95weather/203feature/DAILY7/math/3816ledgerstabil. N7hatforrásDATA_PENDING. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/6.4.N8/REPORT.md`.
  - [ ] **6.4.N9** Egy külön, legalább60perc valós cooldown utáni új claim:csakN7hat hiányzó weatherforrás,max6freeERA5/0sports/web/LLM/fit;hibaSTOP,nincs ismétlődőretryloop. Legkorábban2026-10-23T14:00:25.636384Z. N7csakmind12pár fizikaiteljességévelkész;eredeti95weather/9pár/203feature/receipt/hashguardmegmarad.
- [ ] **6.5** Hasonló ellenfélerősség és hazai/idegen szerep szerinti
  összehasonlítható történeti minta.
- [x] **6.6** Többszörös összehasonlítás és kis minta elleni védelem.
- [x] **6.7** Csak előre rögzített holdouton javuló hatás kerülhet LIVE módba.

## 7. fázis – Klubtipsterek állandó, eltérő módszertana

- [x] **7.1** Minden klubprofilhoz verziózott stratégiaszerződés:
  preferált piac, elemszám, odds-sáv, bajnokságtípus és bizonyítékküszöb.
  - 2026-10-22:20aktuális klubstratégia tartalom-/implementációhashhez kötött deklarációja és dashboardmetaadat kész;13új/85célzott/3556teljesPASS+3újmodellreplayPASS. Bajnokságtípus jelenlegUNSPECIFIED,nincs újfilter. Primary/alternatívhistorykapu külön jelölt; nem utólagos szelvénykötés. Valódi20napi klubkártya mindABSTAINED,nem20READY. DAILY/3745ledger változatlan,API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.1/REPORT.md`.
- [x] **7.2** Legalább egy állandó 2×2×3 és egy történeti Over 2.5 karakter.
  - 2026-10-22:7.2read-onlyaudit kész:DAILY és történetiKrónikás aktív; háromarany+egynatív konstrukcióellenőrzés,134célzottPASS(9új). NatívKrónikás127árból90historyhiány/26küszöbalatt/11maradék;irodánkéntmax3meccs, 4,38–5,15szorzó,nincs10felettiopció,nemátfedésmiatt. NemgarantáltnapiREADY. 7.1teljes3556PASS+3fit/replay változatlanforráshoz újrafelhasználva,nemújfutás. DAILY/3745ledger/staging változatlan,API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.2/REPORT.md`.
- [ ] **7.3** 3–4 contrarian/value profil, igazolt ármozgással és `EV > 1.15`
  belépési feltétellel.
  - 2026-10-22:3meglévőcontrarian karakter natívóra/EV-szabályauditja kész; 118célzottPASS(20új szintetikusnatív határpróba).540monitor/539rawhash/32104árelőfordulás, legrégebbi7,65óra:0valódi24–48hbaseline,0natívreviewedszelvény;checkboxnyitva. MentettmodellekisINSUFFICIENT_HISTORY. DAILY/3745ledger/staging változatlan,API/LLM0; 7.1teljes3556PASS+3fit/replay újrafelhasznált,nemúj. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.3/REPORT.md`.
  - [x] **7.3.N1** A valódi natív 24–48 órás ármegfigyelési párok fizikai ellenőrzése; előbb cache és receipt, szükség esetén egy meglévő kvótavédett friss megfigyelés. Ár-idő nem található ki; nincs küszöblazítás, új modell/Grok vagy LIVE szelvény.
    - 2026-10-23: audit lezárt; 1949 snapshot,54296 raw/receipt-igazolt árelőfordulás,1783 friss ár/26 meccs,0 régi/mai fixture-átfedés. Mind26 első+24h órája zárolás utáni; korábbi első gyűjtés kell. Forrás bitazonos N3:4930full/3actualreplay hivatkozva,0újteszt/API/LLM/fit. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.3.N1/REPORT.md`.
  - [x] **7.3.N2** A 36–48 órával előre történő, pontos natív meccsazonosságú első ármegfigyelés takarékos SHADOW-adatútja, napi csomagtól függetlenül, a meglévő kvóta/receipt/cache/timer réteggel. Nincs küszöblazítás, második sűrű monitor vagy LIVE-promóció; valódi 24h pár később igazolandó.
    - 2026-10-23: korai első SHADOW-árgyűjtő validált; 1 valódi meccs/69 ár36–48h előtt; nem eltelt24hárpár.4963full/134target/33új/3ÚJactualreplayPASS. KülönSHADOWstore,LIVEváltozatlan;400közösplafon,background300,nyugtásújaktiválás. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.3.N2/REPORT.md`.
  - [x] **7.3.N3** A külön előzetes SHADOW-ármegfigyelések szigorú, nyugtavédett olvasói bekötése a natív SHADOW-opcióelemzésbe. Meglévő modellek/ármozgás-képlet újrahasználata, 0 új fit/API/Grok, LIVE-history változatlan; valódi 24h összevetés hiányát nem szabad pozitív jelnek jelenteni.
    - 2026-10-23: szigorú korai SHADOW-forrásolvasó validált;69valódiár/0eltelt24hpár/0mai modellátfedés.4994full/206target/31új/3ÚJactualreplayPASS. Éleskártyák változatlanok. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.3.N3/REPORT.md`.
    - 2026-10-23: implementáció validált:4963full/134target/33új/3ÚJactualreplay/RuffPASS; különSHADOWstore,400kombináltsparecap/background300. Valódi naptár1/9lap tanúsított,0ablakbeli meccs/0elsőár; fizikai árpróba NYITOTT, nem készre pipálva. Árgyűjtő aktivált,backgroundhash újvalidált,ArthurPID18984,napiONlegacyOFF. Következő folytatás ugyanennek az alpontnak a pozitív forrásellenőrzése.
- [x] **7.4** Külön under-, BTTS-, félidő-, 1X2-, kupa-, forma-, xG-,
  játékos- és motivációspecialisták.
  - [x] **7.4.N1** Kupa-specialista: forras- es receipt-kotott, kulon kapcsolhato SHADOW-kivalaszto; eles publikacio nelkul.
    - 2026-10-22: külön default-disabled kupaSHADOW-választó kész, 23új/163célzott/3579teljesPASS+3újmodellreplayPASS. Valós36kupaazonosság, frissár0, ténylegesjelölt0; nemLIVEintegráció. DAILY/3745ledger/staging változatlan. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.4.N1/REPORT.md`.
  - [x] **7.4.N2** Kulon xG-specialista SHADOW-kivalasztasa a validalt xG-adatutbol.
    - 2026-10-22: külön default-disabled xG-SHADOWválasztó kész;24új/118célzott/3603teljesPASS +3újmodellreplayPASS.19valósxGtanítósor artifacttárban visszaolvasva,1kétvenue-képes csapat, 36régi célmetaadat,0ténylegeskombináció. Nemfix xG-ensemble/nemLIVE. DAILY/3745ledger/staging változatlan. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.4.N2/REPORT.md`.
  - [x] **7.4.N3** Kulon jatekos-specialista SHADOW-kivalasztasa igazolt hianyzo/kezdo/jatekosadatbol.
    - 2026-10-22: játékosspecialista default-disabled SHADOW kész;36új/243célzott/3639teljes PASS, 3új modellreplay PASS. Igazolt hiányzó/poszt/statisztika irányszűrés és rangsor; keretbizonytalanság küszöb. 55valós kontextus,40hiányzó/3kezdő lejárt,36régi target,0natív kombináció. Nincs automatikus éles bekötés; végső per-szelvény adatfelhasználási audit kötelező. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.4.N3/REPORT.md`.
  - [x] **7.4.N4** Kulon motivaciospecialista SHADOW-kivalasztasa ellenorzott tet/hirek alapjan.
  - 2026-10-22: N4 külön forrásolt emberi kontextus SHADOW review-választó kész; 27új/294célzott/3666teljesPASS+3új model-fit/replayPASS. Hírek review-körszűrésre, tabella review-kontextusra; nincs kitalált motivációs valószínűség/rangszorzó. Valós0hír,72UNKNOWNtét,36stale target,0kombináció.2valós contextdataset artifacttárban visszaolvasva. A teljes7.4/5.7és az éles integráció nyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.4.N4/REPORT.md`.
- [x] **7.5** Egy profil ugyanazon szabályokból dolgozzon minden nap; a napi
  kínálat csak a jelölteket változtathatja, a személyiséget nem.
  - 2026-10-22:20profil változatlan, pinned profiles.json-ból egyszer betöltött, közösen írásvédett szabályzata kész.49új/409célzott/3715teljesPASS+3új tényleges modellreplayPASS. 20valós kártya és külön7szintetikus kombináció minden alternatívával pontos ekvivalens; wheelasset/izolált import ellenőrizve. Új döntési hatás0, DAILY/3745ledger/staging stabil. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.5/REPORT.md`.
- [ ] **7.6** A nem-DAILY profil csak 10–50 cél-összszorzójú kombinációt
  publikálhat, vagy dokumentáltan tartózkodik.
- [x] **7.7** Nem lehet gyenge lábbal mesterségesen feltölteni a szorzót.
  - 2026-10-22: a meglévő pozitív alátámasztási kapu elsődleges/alternatív paritáshibája javítva, először SHADOW-ban.22új/196célzott/3737teljesPASS+3új tényleges modellreplayPASS. 20natív mentett kártya változatlan;3szintetikus falseREADY kizárva,2pozitív kontroll megmarad. Új önkényes küszöb0; profil/DAILY/3745ledger/staging változatlan. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.7/REPORT.md`.
- [ ] **7.8** Profilonként külön walk-forward teljesítmény és kalibráció.
  - [x] **7.8.N1** A hiányzó, profil- és matematikai verzióhoz kötött walk-forward kalibrátor implementálása külön SHADOW valószínűségi rétegben. Csak ténylegesen időhelyes előrejelzés/eredmény/dataset/receipt párokból, időrendi tanulás és független későbbi értékelés; ismételt lábak deduplikálva. Elégtelen minta esetén NOT_FIT, nincs látszatkalibráció vagy újraszámolt múltbeli jóslat. Brier/log-loss összevetés és verziózott jelöltartifact, automatikus profil-/súlyírás és LIVE előléptetés nélkül. A valódi 30 napos adatkapu és 10.7 release külön nyitott.
  - [x] **7.8.N2** A napi natív SHADOW előrejelzések prospektív, saját valódi időnyugtával kötött mentése és kalibrációs olvasóbekötése; worker analysis/summary, saját fit/dataset, profil/model/termék és eredményazonosság együtt. Ne kelljen LIVE promóció a kalibrációs adatgyűjtéshez. Nincs múltbeli receipt-gyártás fájldátumból, új API/LLM/modellfit vagy élő döntési hatás;15perc lockout és hiányjelzés megmarad. Utána10.7release-kapu; valódi30nap külön nyitott.

  - 2026-10-22: 7.8 NYITOTT.20profil/115ajánlás auditálva, profilonként2–7nap, azonos verzió legfeljebb2nap, aktuális verzió0ajánlás.400eredeti lezárt kiválasztott láb-predikció leíró Brier/log-loss/bins; nem400független/PITtanítópár. Walk-forward kalibrátor/automatikus integráció nincs kész, 30napos kapu nyitott. 43célzottPASS; változatlan forrás3737teljes/3replay ellenőrizve újrahasználva, nem új futás. DAILY/3745ledger stabil, API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/7.8/REPORT.md`.

## 8. fázis – Portfólió: 20–30 változatos szelvény

- [x] **8.1** A profilok először egymástól függetlenül állítsák elő a saját
  legjobb opcióikat.
  - 2026-10-22: meglévő saját opcióképzés függetlensége validált;32artifact-határteszt, 201célzottPASS.29profil nemüres pozitív kontroll;4mentett csomagon116összevetés, 36nemüres opciólista(15klub), saját láb/ár/pont/bizonyíték/sorrend egyezik. Koordinátor: minden selector a végső közös allokáció előtt, eredeti opciótár változatlan. Nemfüggetlen modellek/eredmények. Appdiff0,3737teljes/3valós7.7replay ellenőrizve újrahasználva; DAILY/3745ledger stabil, API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/8.1/REPORT.md`.
- [x] **8.2** Szelvénypont: normalizált profilpont × `sqrt(meccsszám)`.
- [x] **8.3** 100%-os mérkőzés+kimenetel egyezés: duplikáció, törlendő.
- [x] **8.4** 50% feletti mérkőzésátfedés: 0,7-es lágy büntetés.
- [ ] **8.5** Mérkőzéskitettség: legfeljebb 40–50% a READY csomagban.
  - A korlát a teljes READY ajánláscsomagra vonatkozik. Előbb ugyanazon profil
    kész alternatíváját keresi, és csak ennek hiányában tartózkodik.
- [x] **8.6** A 20–30 darab cél elérése előtt a rendszer új profilopciókat
  vizsgáljon, ne ugyanazt a három mérkőzést kombinálja át.
  - [x] **8.6.N1** Rögzített profilkapuk melletti, korlátos újmeccs-lefedettségi keresés SHADOW összevetése; változatlan éles opciók és forráskötött bizonyíték.
    - 2026-10-22:N1SHADOW kész;8.6LIVE nyitott.20/40meccses kontrollban12→20/40lefedettség változatlan128/512kerettel, szabálylazítás nélkül.4valós mentett input/80klubkártya és minden alternatíva OFF/SHADOW döntése pontosan egyezik;7profil/18régi meccs 27új próba-megjelenése,0elveszett.21új/281célzott/3758teljesPASS+3újvalósreplayPASS. DAILY/3745ledger stabil,API/LLM0. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/8.6.N1/REPORT.md`.
  - [x] **8.6.N2** Explicit activation of bounded club fixture-coverage alternatives under unchanged source/profile/128-beam/512-option gates; preserve baseline best, source-bound context review and original DAILY/numeric mathematics. Own retained-input SHADOW/ACTIVE, full suite, three actual replays and new publication/runtime proof before closure.
- [x] **8.7** Ha nincs húsz bizonyított szelvény, a rendszer kevesebbet ad és
  `CAPACITY_SHORTFALL` jelentést készít a hiány okáról.
  - [x] **8.7.N1** Javított, döntést nem módosító LIVE/PAPER kapacitáselszámolás: READY DAILY pontos REVIEWED→mentett ticket hídja, külön PAPER és tényleges LIVE ticket darabszám; nincs töltelék, kapulazítás vagy régi snapshotátírás. Saját valós SHADOW/teszt/replay és adatmegőrző runtime-bizonyítás.
    - 2026-10-25: a szülő újranyitva a pontos számlálási hiba miatt. A friss egy READY DAILY az ajánlásnézetben REVIEWED, ezért a régi számláló0; a PAPER és LIVE összemosódott. Négy megőrzött valós riport SHADOW-projekciója és28célzott teszt/Ruff PASS. Saját teljes5673+3tényleges numerikus replay fut, nem kész. Csak allokáció utáni diagnosztika változik,0új sport/LLM/fit/ledgerírás a próbában.
- [x] **8.8** Minden szelvényhez géppel ellenőrizhető bizonyítékcsomag:
  odds, modell, forma, xG, játékos, kontextus és forráshiányok.
- [ ] **8.9** Portfólió-szintű becsült találati eloszlás és várható napi
  nyertesszelvény-szám; ez nem helyettesíti a későbbi tényleges KPI-t.

  - [x] **8.9.N1** Korlátos, közös meccskimenetelt használó SHADOW találatiszám-forgatókönyv; külön modell/publikációs csomag, explicit hiány és feltételes eloszlás.

## 9. fázis – Grok/Astra/Codex review és javítás

- [x] **9.1** Astra/Codex a jelölteket a szelvényképzés előtt ellenőrzi.
- [x] **9.2** Grok egyetlen közös csomagban kapja az összes READY szelvényt és
  a döntéshez szükséges, már letöltött adatot.
- [x] **9.3** A csomagban minden kihagyott vagy tömörített adatkategória
  darabszámmal szerepeljen.
  - [x] **9.3.N1** A közös Grok-csomag változatlan korlátú, forrásazonos tömörítésének és kategóriánkénti kihagyás-/tömörítésszámlálásának javítása; méret miatti elutasítás is kapjon bizonyított számlálókat. Régi csomagok/árak/eredmények,100000karakterkorlát,modell és budget megmarad; előbb valós megőrzöttinput SHADOW, nincs új sports/LLM-kérés az auditban.
    - 2026-10-25 23:18UTC: sajátv2full5742PASS/44warning/2601.34sec,316target/10adapter/3actualreplay×8rootPASS,1216source/5382archive/8Gitblobegyező. Egyszeri új normálCLI márfut; újpublikáció/READY/Grok/runtime még külön bizonyítandó, nemkész scope/project.
    - 2026-10-25: v2mind3újactualreplay×8PASS/97+33+1datasetrootreadback;5742fullmégfut. Sajátpublisher/closurevár,13fizikaiscopeellenpróbaPASS: UNKNOWNkülönkimutatva,nempozitívadat; nincsproductionkapulazítás vagy újpublikációs készrejelentés.
    - 2026-10-25: followup-v2 célnapjavítás2productionhely/12újregresszió,316target/10adapter/Ruff/4valósSHADOW PASS; saját5742full+3újactualreplay fut, nemelfogadás. Valódi receipt/scopekapu/régiriport változatlan; külön tesztprojekció provider97855karakter, nem újválasz.
    - 2026-10-25: saját5730full/366target/57újcodec/10adapter/3actualreplay×8PASS; rootXML/hash/source/5095archívfájl és finalsource4valósSHADOWPASS. Egyszeri2026-10-26-912b18b94ee3 normál PARTIAL,1READY DAILY3láb,2lábon újadat igazolt/1UNKNOWN; szigorú verifierSTOPPED. Grokdate25/target26 scopehiba helyben reprodukált, UNAVAILABLE/nincsrequest; saját runtime/checkpointnincs, followerekSTOPPED,nincsretry.
    - 2026-10-25: adatvesztésmentes bekötött SHADOW PASS: két régi túlméretes csomag 112616/124158 → 89733/96386 karakter, szigorú provider után90826/97431; összes eredeti összeállított mező/szám/szöveg megmarad.366target/57új/10adapter/RuffPASS; saját5730full+3actualreplay fut, újpublikáció/runtime/lezárás még nincs. Riport: docs/status/2026-10-25-grok-packet-fix/README.md.
- [x] **9.4** Grok minden szelvényhez pro, kontra és kérdéses láb listát adjon.
- [x] **9.5** Codex csak konkrét, forrásolt kifogással érthet egyet.
- [x] **9.6** Javítás kizárólag ugyanazon futás már ellenőrzött alternatívájából.
- [x] **9.7** Legfeljebb egy javítási kör; nincs végtelen újragenerálás.
- [x] **9.8** DAILY_223 a review-ban látható, de nem módosítható.

## 10. fázis – Eredményalapú tanulás és kalibráció

- [x] **10.1** Valós, kutatási és papírszelvények külön eredménysora.
- [x] **10.2** Profil, piac, liga, versenytípus, odds-sáv és elemszám szerinti
  találati és ROI-bontás.
- [x] **10.3** Szelvényeredmény mellett lábszintű hibaanalízis.
- [x] **10.4** Brier score, log-loss, kalibrációs görbe és odds-záróárhoz mért
  érték, ahol rendelkezésre áll.
- [x] **10.5** Napi KPI: nyertes szelvények száma és nyertes összszorzó.
- [x] **10.6** Gördülő 7/30/90 napos KPI: legalább 3 nyertes/nap cél
  teljesülési aránya, profit, drawdown és profilmegbízhatóság.
- [x] **10.7** A tanulóréteg csak javaslatot készít; paramétermódosítás külön
  verzióban, walk-forward és SHADOW kapun keresztül léphet életbe.
  - [x] **10.7.N1** Learning nem végrehajtó határa igazolt: 70 célzott PASS, ebből 21 artifact-határteszt; 0 paraméter/döntési diff. A szülő 10.7 NYITOTT: integrált verzióhoz kötött walk-forward/SHADOW release-kapu még nincs. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/10.7/REPORT.md`.
  - [x] **10.7.N2** Integrált, eredeti profil-/modell-/dataset-/kalibrációverzióhoz és bizonyítékhoz kötött SHADOW release-kapu; időhelyes minták, független proper-score értékelés és nyitott adatkapuk ellenőrzése. Hiány esetén blokkolt javaslat; nincs automatikus paraméterírás vagy LIVE-promóció. A7.8.N2 adatgyűjtése már LIVE előtt működik.
- [x] **10.8** Egyetlen rossz vagy kiugró nap nem változtathat stratégiát.

## 11. fázis – Bevezetési kapuk

- [x] **11.1 UNIT:** új modul saját határ- és hibatesztekkel.
- [ ] **11.2 INTEGRATION:** forrás → normalizálás → modell → profil →
  portfólió → rögzítés teljes adatút.
  - [x] **11.2.N1** Natív forrás→normalizálás→kiválasztott termék→tesztnapló audit és numerikus blokkolás reprodukció: 51 eredeti forrás,36 meccs,1340 előzmény,3040 ár; 136 célzott PASS. A teljes 11.2 NYITOTT; riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2/REPORT.md`.
  - [x] **11.2.N2** Semleges pályás történeti rekordok külön mintapolitikája: verziózott, kapcsolható SHADOW adatút és határtesztek; semleges mai meccs és kevés csapatelőzmény nem kerülhető meg, LIVE előléptetés nélkül.
  - [x] **11.2.N3** Kontextuskeret érdemi, méltányos prioritása és cache: a teljes meccskör kezdőcsapat/sérülés/játékos/xG adatai ne maradjanak pusztán a lekérési sorrend miatt REQUEST_BUDGET állapotban; valódi hiányt és kvótát nem szabad elfedni. Külön egyetlen alpont, új döntési hatás előbb SHADOW.
  - [x] **11.2.N4** Játékosspecialista tényleges SHADOW bekötése: várható/megerősített kezdőcsapat, sérülés/eltiltás és játékosstatisztika forrásaihoz kötött opcióhatás és lábszintű bizonyíték; hiány nem nulla, várható XI nem megerősített XI, predikció/ár nélkül nincs töltelékajánlat.
  - [x] **11.2.N5** Natív modell-előzmény adatút: a már letöltött, fizikailag rendelkezésre álló adatbázisból csapat/szezon/időkapu szerint ellenőrzött SHADOW modellbemenet. Nincs 2.4 újraimport, név szerinti azonosságtipp, minimumlazítás vagy receipt-visszadátumozás; hiányzó tényleges minta esetén ezt kell kimutatni.
  - [x] **11.2.N6** Natív újraszámolt modellbecslésekből forráskötött SHADOW szelvényopciók és a meglévő játékosspecialista opcióvéleményének bekötése. Változatlan ID/quote/idő/10–50/minimumkapuk, nincs éles előléptetés vagy töltelék.
  - [x] **11.2.N7** TheStats élő raw/HTTP-beérkezési receipt adatút append-only bekötése: tényleges új beérkezés, azonos payload ismételt fogadása és cache-hit elkülönítése. Régi snapshot/arany/időbélyeg nem írható felül; nincs hamis frissesség, quote-idő vagy visszamenőleges rendelkezésreállás.
  - [x] **11.2.N8** Szigorú natív forrásolvasók és forrásaliasok bekötése az új tényleges HTTP-megfigyelési bizonyítékra a napi SHADOW láncban; azonos payload új fogadása, ismert régi receipt és cache-idő elkülönítése. Nincs quote-idő kitalálás, időkapu-lazítás, aranyfelülírás vagy LIVE döntési előléptetés.
  - [x] **11.2.N9** A natív modell → forrásolt ár/opció → játékosspecialista SHADOW-lánc kontrollált napi bekötése a Windows-automatán futó pipeline-ba. Friss tényleges források, API/CPU-keret, egyetlen modellillesztés, hibaszigetelés, megváltozatlan LIVE szelvények; naplózott valódi végigfutás. Időkapu és további specialisták/adathasználati hiányok nem jelenthetők késznek.
  - [x] **11.2.N10** Az éles natív célmeccs/történeti forrásbizonyíték- és frissességlefedettség rendezése: 36 jelenleg UNPROVEN célmeccs és 14 régi UEFA raw hiányának pontos fizikai auditja. Történeti raw legkorábban a tényleges mostani ellenőrzéstől válhat ismertté; friss célmeccs/ár csak valódi új TheStats-fogadással. Régi receipt/quote-idő/staging/ledger nem írható át. Validált jelenidejű éles SHADOW-próba, nem elszigetelt adatmásolatból való készre jelentés.
  - [x] **11.2.N11** A már implementált kupaspecialista bekötése az időkorlátos napi natív SHADOW-workerbe, ugyanazon frissen számolt natív modellek/opciók felhasználásával. Fizikai taxonomy/target/price bizonyíték, meglévő profil- és 10–50 szorzókorlát, külön naplózott abstain/hiány; nincs új fit/API/LLM, LIVE döntési előléptetés vagy mesterségesen kitöltött szelvény.
  - [x] **11.2.N12** A már implementált xG-specialista bekötése az időkorlátos napi natív SHADOW-workerbe. Ugyanazon natív kontrollmodellek és forrásolt, időhelyes xG-adatok használata, meglévő saját xG-kihívó számítás és gates változatlanul; fizikai célmeccs/ár/xG lábankénti kötés, hiány és abstain külön naplózva. Nincs új API/LLM vagy megosztott modellfit, LIVE előléptetés, adatpótló fikció.
  - [x] **11.2.N13** A már implementált humán/motivációspecialista SHADOW felülvizsgálati választójának bekötése az időkorlátos napi natív workerbe, ugyanazon kontrollmodellekkel. Valódi forrásolt hír-/tabellakontextus, lábankénti kötés és hiánynapló; nincs új API/LLM vagy megosztott modellfit, kitalált mentális állapot, motivációs valószínűségemelés vagy LIVE promóció. Az 5.7 hatásvalidáció külön nyitott marad.
  - [x] **11.2.N14** A két éles UEFA TheStats-tabella 72-72 sorának forrás- és scopeszintű auditja: AMBIGUOUS_STANDINGS_TEAM_WITHIN_GROUP okának azonosítása. Kiolvasási javítás csak a tényleges raw-ban igazolt tábla-, csoport-, típus- és fázisazonossággal; nincs önkényes összes/hazai/vendég választás, sorösszevonás, kitalált szabály vagy motivációs súly. Ha a forrás nem egyértelmű, UNKNOWN maradjon. Utána időjárás és teljes lábankénti adathasználati audit; 5.7 külön nyitott.
  - [x] **11.2.N15** Napi natív mérkőzésenkénti időjárási kontextus és forráshasználati audit SHADOW-bekötése a meglévő olvasókkal: pontos helyszín/koordináta, kezdés, forecast és valódi rendelkezésreállási receipt összekötése. Hiány UNKNOWN, historikus reanalysis nem prematch forecast; nincs igazolatlan valószínűség/rangsúly vagy LIVE promóció. Az éles időjáráslefedettség és 6.4 hatásvalidáció külön marad.
  - [x] **11.2.N16** Teljes napi natív SHADOW lábankénti adathasználati audit: forrás/dataset/receipt → modell → profil/specialista → opció tényleges kötése. Numerikus múlt, odds, játékos, xG, kupa/naptár/rotáció, hír/motiváció és időjárás külön USED_DIRECT/USED_RANKING/USED_REVIEW/STORED_ONLY/MISSING vagy visszatartott állapottal, bizonyíték alapján. Nem igazolt adatra nincs pozitív hatás; SHADOW bekötés nem LIVE felhasználás. A feltárt hiányokból egyetlen következő javítás kijelölése; 11.2, kalibráció és release kapui külön nyitottak.
  - [x] **11.2.N17** A decision_data_usage lefedettség- és felhasználásjelöléseinek javítása az N16 bizonyítékai alapján: UNKNOWN/üres időjárási sor ne legyen felhasznált forecast, AVAILABLE statisztika ne bizonyítson xG/játékos alkalmazást, a historikus bemenet átadása ne bizonyítson numerikus modellfogyasztást. Tényleges review/rangsorolás és a DAILY historikus heurisztika külön maradjon látható. Csak audit/telemetria; nincs döntési, szorzó-, profil- vagy DAILY_223-logikai változás. Előtte/utána guard, forrásdiff validáció és riport; a hiányzó adatbekötés továbbra is külön nyitott.
  - [x] **11.2.N18** A meglévő naptár-/rotációs olvasó napi natív opciókhoz kötött SHADOW-kontextusa: pontos meccs/csapat/kezdés/dataset/receipt, minden lábhoz ellenőrizhető hivatkozás. Részleges naptár vagy hiányzó igazolt XI külön UNKNOWN marad; kupa alapján nincs B-csapat-feltételezés. Nincs új valószínűségi/rangsúly vagy LIVE promóció, a meglévő számítás és más szakértők eredménye változatlan. Fizikai próba és végső frissességi ellenőrzés.
  - [x] **11.2.N19** Naptár-/XI-forrásolvasók igazítása a már működő TheStats HTTP-megfigyelés/receipt-alias szerződéshez: az első raw-metaadat átírása nélkül legyen olvasható a későbbi, valóban tanúsított azonos URL/body lekérés. Előbb offline reprodukció és SHADOW; ezután kvótaellenőrzés mellett legfeljebb egy meccs kontrollált friss forráspróbája. Új receipt nem quote-idő és nem igazolt XI. Régi staging/napló változatlan; nincs LIVE promóció.
  - [x] **11.2.N20** A teljes, szűretlen csapatnaptárak kvóta- és cache-korlátos gyűjtésének bekötése a napi natív SHADOW-előkészítésbe. A javított olvasó ne csak kézi próbán kapjon friss forrást. Determinisztikus, méltányos sorrend; legfeljebb egy naptár csapat/időablak szerint, tényleges HTTP-receipt, részleges lapozás/kvótahiány külön jelölve. Meglévő játékos-/xG-prioritás ne szoruljon ki. Egy adatgyűjtési réteg; nincs új modellfit, XI-megerősítés kitalálása, súlyváltozás vagy LIVE promóció.
  - [x] **11.2.N21** A historikus háttérimport már N20 előtt eltérő aktiválási forráshash-ének önálló auditja és helyreállítása. Előbb a régi engedélyezett forráscsomag és a jelenleg validált kód eltérésének ellenőrzése, majd indokolt esetben új hitelesített aktiválási bizonyíték és legfeljebb két kvótázott kéréses működési próba. Nincs ellenőrzés nélküli hashátírás, keretemelés, 2.4-újraimport vagy régi API-visszakötés. Utána 7.8.N1.
  - [x] **11.2.N22** A történeti TheStats-szinkron321-es importjának pontos raw/HTTP-alias/archív rekordkötése: a bájtazonos raw első daily_context kategóriája önmagában ne legyen hamis hiány, de kategórialazítás helyett pontos hash/URL/beérkezés/receipt és teljes rekordazonosság igazolja a történeti forrást. Eredeti raw/import/receipt nem írható át, nincs hibás import átugrása vagy2.4újraimport. Bővítő idempotens szinkron, valódi fizikai bizonyíték, forrásguard érintésekor új validált aktiválás. Utána7.8.N1; kvóta- és időkapukat ne kerüld meg.
  - [ ] **11.2.N23** Natív stadion/venue-azonosság és koordináta forráskötés: a napi célmeccsek tényleges TheStats/archív helyszínadataihoz pontos venue-ID és ellenőrizhető koordinátapár SHADOW-olvasója. Előbb a fizikai források auditja, hiány külön UNKNOWN; nincs névfuzzy/koordinátatipp, historikus reanalysis prematch forecastként, új modellhatás vagy LIVE-promóció. Új kérés csak indokolt, validált kvóta/cache-keretben. A cél az időjárási és utazási adatút valódi helyszínblokkolásának megszüntetése.
  - [x] **11.2.N24** A teljes aktuális kutatási csomag forrás→specialista→opció→lábszintű tényleges adathasználatának olvasási auditja, beleértve a meglévő Grok-adatcsomagot. Numerikus bemenet, SHADOW-vélemény és hiány/lejárat elkülönítése; eredeti futások változatlanok, nincs új vétó vagy automatikus LIVE-hatás. Az audit után egyszerre csak egy bizonyított hibát javíts.
    - 2026-10-23:ROaudit61fizikaitanítókészlet/66modelboundláb/9egycsomagosGrokvélemény;11függetlenkifogásInvalidPacketreprodukció,nullopcionálisstabilitásmiatt. Másodiklatentplayer-weatherhiány szintetikus66→67kontroll.0API/LLM/fit/kódmódosítás;DAILY/3816ledgerstabil;4766full/3actualreplay bitazonosN7forrásrahivatkozva. Teljesintegrationnyitott. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N24/REPORT.md`.
  - [x] **11.2.N25** A Grok független ellenőrző 11 kifogást blokkoló INVALID_PACKET hibájának javítása: az opcionális ismeretlen model_stability=null ne okozzon teljes csomagelutasítást. Ne találjon ki/nullázzon metrikát, ne lazítson kötelező forrás-/ár-/idő-/azonosságkaput. Előbb eredeti csomag offline reprodukciója, célzott határteszt, teljes teszt és3új tényleges replay; eredeti futás nem módosítható.
    - 2026-10-23:eredeti11kifogásújraépítve/strictvalidatorPASS;csaknullopcionálisstabilitáskihagyva,nem0. Régiérvényescachekulcsmegmarad,eredetiDB/run/3816ledgerstabil.4785full/67target/19új/3véglegesÚJactualreplay/RuffPASS;0valódiAPI/LLM. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N25/REPORT.md`.
  - [x] **11.2.N26** Az N25 lezárása után külön alpontban a játékosspecialista jelöltlábainak időjárási SHADOW-véleményezési bekötése a natív opcióépítőbe, pozitív/nemüres specialista kontrollal; nincs új numerikus súly vagy publikációs vétó.
    - 2026-10-23: lezárt; játékosjelöltek pontos időjárási SHADOW-kötése; 4790 full / 77 célzott / 5 új / 3 új actual numerical replay PASS. Valódi napi játékosjelölt0, pozitív kontrollok3×3; nincs új súly/vétó/LIVE. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N26/REPORT.md`.
  - [x] **11.2.N27** Az N25/N26 javítások után a forrás–specialista–szelvény tényleges adathasználat és a fennmaradó lefedettségi/numerikus kapuk read-only ellenőrzése; következő meglévő szűk javítás kijelölése.
    - 2026-10-23: audit lezárt; 110/110gyűjtési keret elfogyott, 52squad/26XIhiányzó, 0egyéni stat; 61dataset/66modelláb igazolt. N25/N26javítás valid. 0újAPI/LLM/fit; N26bitazonos4790full/3actualreplay hivatkozva. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N27/REPORT.md`.
  - [x] **11.2.N28** Célzott, cache-first, kvóta- és időkorlátos TheStats játékosforrás-gyűjtés SHADOW: igazolt aktív hiányzók kerete/posztja és szezonadata, append-only kanonikus forráskötéssel és szigorú olvasói pozitív kontrollal. Nincs küszöblazítás, numerikus/LIVE hatás vagy visszamenőleges szelvényátírás.
    - 2026-10-23: lezártSHADOWadatgyűjtő;19valódiGET,6keret/7egyénistat valóban fogyasztva,0→1támogatottmeccs.5NOT_FOUNDés teljeslefedettség nyitott.4806full/136target/16új/3újactualreplayPASS. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N28/REPORT.md`.
  - [x] **11.2.N29** A már elfogadott, friss kanonikus játékosadatok átadása a következő Grok-bemenethez elkülönített SHADOW-csomagon igazolva; pontos forrás/TTL/receipt, veszteségmentes tömörítés, nincs új adatgyűjtés, régi batch-újraküldés vagy LIVE-hatás.
  - [x] **11.2.N30** Friss, csak olvasó forrás→modell→szelvény adatfelhasználási audit az új játékos/xG/tét/korai ár adatút után. Tényleges éles numerikus, SHADOW numerikus és kontextushasználat elkülönítése, gyűjtött vagy hiányzó adatok tételes jelzése;0API/LLM/fit, régi szelvények változatlanok. Következő egyetlen javítás kijelölése bizonyíték alapján.
  - [x] **11.2.N31** A végső bizonyítékcsomag lezárási sorrendjének javítása a nightly-run-id és kártya method_version véglegesítése után.291forráspayload ép,2kártyahash és a csomag run-id eltér; a szigorú validator megtartandó. Csak jövőbeli új bizonyíték, régi csomag/napló/döntés változatlan. A külön6rekordos fresh-forma forráskötési hibát még nem ebben az alpontban javítjuk.
    - 2026-10-23: újfinalbundle291source+allcardstrictPASS;5007full/122target/13új/3ÚJactualreplayPASS. Régiadat/döntésváltozatlan; freshforma különN32. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N31/REPORT.md`.
  - [x] **11.2.N32** A6megtartott történeti/venue-forma bizonyíték3valós tételen MISSINGforrásjelölésének javítása a számított fresh-jelző mellett. Pontosazonosság/hash/idő/eredeti frissességi szemantika,0védettDAILYforrás- vagy döntésmódosítás; nincs általános ellenőrzéslazítás.
    - 2026-10-23: 6 forrásrekord /3 valós tétel;295source/allcard strictPASS. 5047full/85target/40új/3ÚJactualreplayPASS. Döntés és régi riport változatlan. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N32/REPORT.md`.
  - [x] **11.2.N33** A két forráskötési javítás utáni, kizárólag olvasó adatfelhasználási végellenőrzés: valós forrás → időhatár → kiválasztott tétel mátrix; numerikus/rangsorolási/forma/review/tárolt/hiányzó adatok külön. Eredeti riportok változatlanok, időkapu nem kerülhető meg; a következő egyetlen végrehajtható hiány kiválasztása.
    - 2026-10-23: ROaudit295source/37card,6formarecord igazolt; valódi1READY/3leg, all-dataFalse. Natív203sor/15fixture receipt nélkül; beengedett natívforecast0. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N33/REPORT.md`.
  - [x] **11.2.N34** A meglévő natív prospektív rögzítő félbemaradt lezárásának külön SHADOW-helyreállítása: eredeti203sor/15fixture/65input megőrzése, valós új saját jelenléti igazolás és csak az újclock+15min utáni célok beengedése. Nincs visszadátumozás, modell/API-ismétlés, döntés/DAILY7/ledger változás vagy általános validatorlazítás.
  - [x] **11.2.N35** Complete the existing qualified-source path BEFORE candidate selection: reuse bounded sports/player/calendar collectors and strict existing readers, avoid duplicate provider budgets, preserve immutable original2x2x3 math and prior issued tickets. Pass genuine current player, xG, schedule, travel-distance proxy, rotation and weather facts into the actual candidate review with explicit missingness and category-specific source citations/assessment. Archive the actual review requests and accepted responses and distinguish sent/cited/applied evidence. Use the same unchanged inputs for a nonpublishing SHADOW comparison before activating the new decision path. Full appropriate tests, three actual numerical replays, strict physical source/evidence proof, then a new2026-10-24 generation without settling or rewriting old tickets. Verify logging, normal daily scheduling, settlement and descriptive learning. Actual unsupported sources remain UNKNOWN; no invented weights, odds, chronology or profitability claim. Later performance monitoring is optimization, not a mandatory idle delay for working integration. Deadline2026-10-24 08:00 Europe/Berlin. N9weather follow-up remains a separate pending data task and is not a prerequisite for this factual integration.
    - 2026-10-23: explicitúj valós presence,203nativeforecast/15fixture/0label,oldprefix0;5081full/81target/34új/3ÚJactualreplayPASS. Eredetifájl/DAILY7/3816ledger stabil. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N34/REPORT.md`.
    - 2026-10-23: lezárt elkülönítettSHADOWbemenet; valódi4relevánskeret/4stat/4hiánylista átadva,2keret/3statnemkiválasztottmeccsé. RégiGrok/ár/szelvényváltozatlan; lejártmetadata/árnemfriss.4823full/160target/17új/3újactualreplayPASS. Riport:`C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N29/REPORT.md`.
    - 2026-10-22: a natív meccshelyszín-metaadat olvasó és lábankénti SHADOW kötés validált (4639full/84target/38újteszt/3actualgolden/Ruff). Koordinátaforrás NYITOTT:0venue-ID/koordinátapár. A teljesN23nincs készre pipálva. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/11.2.N23/REPORT.md`.
- [ ] **11.3 REPLAY:** legalább 30 korábbi nap pont-időhelyes visszajátszása.
- [ ] **11.4 SHADOW:** legalább hét egymást követő élő nap publikálás nélkül.
- [ ] **11.5 PAPER:** legalább 30 nap 1 EUR egységtétes papírmérés.
- [ ] **11.6 CANARY:** egyszerre csak egy új feature-család LIVE módba emelése.
- [x] **11.7 ROLLBACK:** konfigurációs visszaállítás az előző modell- és
  adatútvonalra adatvesztés nélkül.
  - 2026-10-26: saját5776full/75target/15cwdregresszió/10adapter/3ÚJactualreplay×8 és valós9configprior→currentdrill/rootreadbackPASS.10612nyersfájl+26SQLiteDB/18,148,065committedsor main/teljesWAL exactbytehash-sel megőrzött;nemmintavétel/per-rowhashállítás. Dashboardexistingcc3b local+Tailscale/DailyONthen sameRootLockOFFsensitivity_note,legacyOFF. Első5763PASS/13FAIL és előműveleti helperhibák megőrzöttek;matematika/súly/quote/idő/goldváltozatlan. Riport:docs/status/2026-10-26-configuration-rollback/REPORT.md;projekt/szülőknyitottak.
- [ ] **11.8 PROMOTION:** csak dokumentált lefedettség-, kalibráció-,
  stabilitás- és portfólióminőség-javulás után.

## Minden feladat kötelező Definition of Done feltétele

- [ ] DAILY_223 byte-szinten változatlan.
- [ ] Nincs adatvesztő migráció vagy beragadt félállapot.
- [ ] Ismételt futtatás idempotens.
- [ ] Hiba esetén az előző stabil útvonal működik.
- [ ] Nincs jövőbeli vagy mérkőzés utáni adat a döntésben.
- [ ] A döntési hatás forrása és mértéke látható a riportban.
- [ ] A teljes tesztcsomag sikeres.
- [ ] Az arany hétvégi, hétköznapi és kupa/válogatott replay sikeres.
- [ ] A 20–30 szelvényes cél nem okoz gyenge töltelékszelvényt.
- [ ] Minden nem-DAILY READY szelvény betartja a profil odds-sávját.
- [ ] A dashboard a lefedettséget, fallbacket, aktív feature-öket és
  tartózkodási okokat őszintén mutatja.

## Végrehajtási sorrend

1. `0.4–0.6` – reprodukálható baseline és feature-nyom.
2. `1.x` – azonosítók és forrásprioritás.
3. `2.x` – pont-időhelyes történeti adatmodell.
4. `4.x` – xG és játékosok; közben `3.x` odds-árnyékpróba.
5. `5.x–6.x` – mérkőzéskontextus, motiváció és időjárás.
6. `7.x` – állandó tipstermódszerek.
7. `8.x–9.x` – portfólió és közös review.
8. `10.x` – visszamérés és kalibráció.
9. `11.x` – fokozatos LIVE bevezetés.

Ettől a sorrendtől csak dokumentált blokkoló okkal lehet eltérni. Új fázis nem
léphet LIVE módba, amíg az előző fázis adatútja és visszaállítása nincs
ellenőrizve.


  - [x] **5.2.N8** Separately review missing exact daily stadium coordinates and original previous-match venue sources; bounded genuine free/TheStats receipts, no home-ground assumption or fuzzy alias, immutable earlier releases, SHADOW/full/three actual replays, then fresh weather/travel context use proof.
  - N8 lezárt saját scope: full5493/target415/3actual replay, új `2026-10-25-d112d6937b35` selected-weather igazolt. Selected-travel: DATA_PENDING; parent5.2 és profilkör nyitott. Dashboard újraindítva/exactrun helyi+Tailscale; Daily helyreállítás után ugyanazon lock alatt OFF a következő7.4 fejlesztéshez, legacyOFF. Riport: `C:/AI-Work/GoalAnalysis/artifacts/roadmap-execution-20261020/5.2.N8/REPORT.md`.

  - [x] **7.4.N5** Repair bounded daily calendar quota renewal from witnessed actual provider capacity; preserve monthly/player reserves, denial/Retry-After fences, one bootstrap and existing20-request/45-second limits. First PIT diagnosis and nonpublishing source proof; full tests, three actual replays and new normal publication before closure.

  - N5 scope COMPLETE: full5520/target74/3actualreplay, új `2026-10-25-f20dd091b16b` kiválasztott naptárforrás igazolt; Dashboard+Dailyhelyreállítás majd ugyanazonlockOFF a következő7.4fejlesztéshez; legacyOFF.

  - [x] **7.4.N6** Bind every available non-DAILY profile option to its exact original quote, frozen sourced context and actual bounded reviewer response before publication eligibility. First SHADOW; preserve independent numerical rankings/probabilities, profile thresholds, original DAILY math and request limits; explicit unavailable/budget gaps, full tests, three actual replays and new publication proof.

  - N6 scope COMPLETE: full5590/target241/3actualreplay, új `2026-10-25-3472f0b6fb86` profilszintű forrás/válaszkapu igazolt; Dashboard+Dailyhelyreállítás majd ugyanazonlockOFF a következő8.6fejlesztéshez; legacyOFF.

  - N2 scope COMPLETE: full5650/target339/3actualreplay; új `2026-10-25-f14ad05e1ce7` ACTIVE klub-lefedettségi policy és végső profilszintű forráskapcsolat igazolt. Friss elsődleges lefedettségnövekedés megfigyelve: False; ez nem 20–30 szelvény garancia. Daily helyreállítva, majd ugyanazon lock alatt OFF a következő 8.7.N1 fejlesztéshez; legacy OFF.

  - 8.7.N1 own scope COMPLETE: full5673/target28/adapter10/3actual replay8checks; audit-only LIVE/PAPER metadata and immutable `2026-10-25-f14ad05e1ce7` projection1LIVE/0PAPER/19shortfall. Runtime+DailyON restored then same-lockOFF9.3.N1,legacyOFF. Parent8.7 remains open for final fresh normal v2metadata release proof; no extra paid generation just for a counter.

  - 9.3.N1 own followup-v2 scope COMPLETE:5742full/316target/10adapter/3actualreplay8checks/13physicalcontrols; new `2026-10-26-cc3b72cd5b81` READY DAILY exact quote/context/wire/actual answer/ledger; sourced-context-legs=2,allUNKNOWNlegs=1; actualGrok=COMPLETE,positiveReview=True. DailyON then same-lockOFF11.7,legacyOFF; parent9.3/project need final scope audit.

  - 11.7 own followup-v2 verified:5776full/75target/15cwdregressions/10adapter/3actualreplay8checks; real prior/current nine-config restore under one RootRunLock, raw files and committed SQLite data preserved; existing cc3b dashboard local+Tailscale/DailyON then same-lockOFF next sensitivity_note,legacyOFF. Original5763PASS/13FAIL evidence retained; no paid generation or math change.
