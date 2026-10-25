**Frissítés — 2026-10-25: az N6 saját hatókörében lezárt. 5590 teljes teszt, 241 célzott teszt, három tényleges replay; új 2026-10-25-3472f0b6fb86 run, két mentett szelvény kilenc igazolt lábbal. [N6 végleges eredmény és valódi maradék](../2026-10-25-n6-closed/README.md). A teljes projekt és a 7.4 szülő nincs kész; az alábbi régebbi pillanatképek dátumhoz kötöttek.**

**Frissítés — 2026-10-25: az N6 teljes tesztje 5590/5590, célzott tesztje 241/241, három tényleges numerikus replay sikeres. Az új generálás és publikációs bizonyítás még folyamatban; N6 nincs készre jelölve. [Aktuális N6 bizonyíték és maradék](../2026-10-25-n6-tested/README.md). Az alábbi 160/55 követelménymátrix korábbi pillanatkép, nem teljes projektzárás.**

# Arthur – teljes zöld/piros státuszlista, 215 tétel

Állapot: 2026-10-25. 🟩 160 kész a saját bizonyított hatókörében; 🟥 55 részleges/hiányzó. **A teljes projekt nincs kész.**

A tömör index minden eredeti követelményt felsorol; a tétel linkje a részletes bizonyítékra és maradékra mutat. Az N8 teljes teszt/generálás még folyamatban, eredménye nincs készre jelölve.

| Állapot | Tétel | Eredeti követelmény |
|---|---|---|
| 🟩 | [STRICT SINGLE-TASK RULE:](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-12) | Az LLM/Agent **egyszerre csak egyetlen egy alpontot** (pl. csak a 0.4-et, vagy csak az 1.1-et) valósíthat meg. Tilos a teljes listát vagy egész fázisokat egyszerre kódolni. |
| 🟩 | [guard@13](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-13) | Minden egyes kódgenerálási vagy mérnöki fázis után a futásnak le kell állnia, és meg kell várnia a manuális jóváhagyást vagy a tesztelési visszajelzést. |
| 🟩 | [guard@14](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-14) | Minden új kód futtatása vagy módosítása előtt kötelező bemutatni a `DAILY_223` változatlanságát igazoló byte-szintű vagy hash-alapú teszt kimenetét. |
| 🟩 | [Időzóna Normalizálás (1. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-17) | Minden bejövő szolgáltatói időbélyeget (TheOddsAPI UTC, Football-Data helyi idő, Open-Meteo UTC) a beolvasás legelső nanoszekundumában kötelezően ISO 8601 UTC formátumra kell hozni. Belső kalkuláci… |
| 🟩 | [Fuzzy-Match Vétó (1. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-18) | Ha az 1.3-as pontban a csapatnév párosítás nem 100%-os (pl. "Man Utd" vs "Manchester United"), és nincs előre rögzített ID térkép, a mérkőzés automatikusan kiesik a READY portfólióból, és átkerül a… |
| 🟥 | [Data Leakage Retrospektív Védőfal (2. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-19) | A történeti adatbázisban minden sorhoz el kell menteni az `ingested_at` (beérkezési) időbélyeget. Amikor a 11.3-as REPLAY fázis fut, az adatbázis-lekérdezésnek tartalmaznia kell a `WHERE observed_a… |
| 🟥 | [Dinamikus Ensemble-Súlyozás (4. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-20) | A 4.3-as pontban az ensemble valószínűségek kombinálásakor tilos fix súlyokat használni. Az xG modell súlyát dinamikusan kell skálázni a bajnokság adatsűrűsége alapján (ha egy ligában < 3 meccsnyi … |
| 🟥 | [Hiányzó Adat Szigor (4. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-21) | Ha egy kulcsjátékos hiányáról nincs megbízható hír (4.9-es pont), a rendszer nem rendelhet hozzá "átlagos" vagy "alapértelmezett" értéket. A bizonytalansági faktort (`entropy_score`) növelni kell, … |
| 🟩 | [Személyiség-Zárolás (7. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-22) | A klubtipzter profilok (7.5-ös pont) matematikai magját (súlyok, belépési küszöbök, preferált piacok) egy rögzített konfigurációs fájlból (`profiles.json`) kell beolvasni. A futó rendszer nem módos… |
| 🟥 | [Töltelék-visszautasítás (8. fázis kiegészítés):](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-23) | Ha a 8.7-es `CAPACITY_SHORTFALL` aktiválódik (pl. csak 12 READY szelvény van a 20-30 helyett), a rendszernek szigorúan tilos lazítania a belépési küszöbökön. A hiányzó szelvények helyét üresen kell… |
| 🟥 | [guard@29](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-29) | Napi célcsomag: **20–30 READY szelvény**, ha van hozzá elegendő, időben érvényes és szakmailag alátámasztott jelölt. |
| 🟥 | [guard@30](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-30) | Nem-DAILY szelvények cél-összszorzója: **10–50**. A rendszer nem tehet gyenge lábat egy szelvényre csak azért, hogy elérje a 10-es szorzót. |
| 🟩 | [guard@31](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-31) | Eredménycél: **legalább 3 nyertes szelvény naponta**, nyújtott cél 4. Ezt napi és gördülő 30 napos KPI-ként kell mérni; csak lezárt eredményből. |
| 🟩 | [guard@32](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-32) | Alapértelmezett elszámolási tét: **1 EUR szelvényenként**. |
| 🟩 | [guard@33](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-33) | A `DAILY_223` teljes logikája, küszöbei és kiválasztása változatlan. |
| 🟥 | [guard@34](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-34) | Azonos mérkőzés több szelvényen szerepelhet, de a napi READY csomagban a mérkőzéskitettség legfeljebb 40–50%. |
| 🟩 | [guard@35](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-35) | Teljesen azonos szelvény nem publikálható kétszer; 50% feletti hasonlóság lágy pontbüntetés, nem automatikus törlés. |
| 🟥 | [guard@36](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-36) | A rendszer adat- vagy jelölthiány esetén nem gyárt töltelékszelvényt. A hiányzó darabszámot és annak okát külön jelenti. |
| 🟩 | [guard@40](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-40) | Minden fejlesztés kis, visszafordítható egységben készüljön. |
| 🟩 | [guard@41](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-41) | Egy munkafázis egyszerre csak egy adat- vagy döntési réteget változtathat. |
| 🟩 | [guard@42](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-42) | Adatbázis-módosítás csak bővítő, idempotens `expand → backfill → validate → switch` migráció lehet. Oszlop vagy tábla törlése külön későbbi feladat. |
| 🟩 | [guard@43](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-43) | Minden új döntési hatás feature flag mögött, először `SHADOW` módban fusson. |
| 🟩 | [guard@44](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-44) | A régi és új számítás ugyanazon változatlan napi bemeneten legyen összehasonlítható. |
| 🟩 | [guard@45](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-45) | Minden modell- és szabályverzió, adat-időhatár, forrás és konfigurációs hash kerüljön a rögzített futásba. |
| 🟥 | [guard@46](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-46) | Sikertelen új modul esetén az előző stabil útvonal maradjon használható; részleges eredmény nem írhatja felül a stabil adatot. |
| 🟩 | [guard@47](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-47) | Egy szakértő hibája nem állíthatja le a teljes csomagot, de az érintett adat nem válhat automatikusan nullává vagy pozitív jellé. |
| 🟩 | [guard@48](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-48) | Nincs korlátlan LLM-párbeszéd: Astra/Codex jelöltellenőrzés, egy Grok csomagreview, legfeljebb egy ellenőrzött javítási kör. |
| 🟩 | [guard@49](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-49) | A 15 perces zárolás adatlekéréskor, kiválasztáskor és publikáláskor is kötelező. |
| 🟩 | [guard@50](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-50) | A már rögzített vagy megjátszott napi csomag visszamenőleg nem írható át. |
| 🟩 | [guard@55](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-55) | Ismeretlen vagy ütköző mérkőzésazonosító. |
| 🟩 | [guard@56](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-56) | Lejárt, jövőből származó vagy időbélyeg nélküli adat. |
| 🟩 | [guard@57](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-57) | Már elkezdődött, törölt vagy a 15 perces zárolásban lévő mérkőzés. |
| 🟩 | [guard@58](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-58) | Nem ellenőrizhető odds, bookmaker vagy piaci kimenetel. |
| 🟩 | [guard@59](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-59) | Ugyanaz a mérkőzés kétszer ugyanazon a szelvényen. |
| 🟩 | [guard@60](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-60) | Teljesen azonos szelvényduplikáció. |
| 🟩 | [guard@61](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-61) | Modellvalószínűség forrásadat vagy modellverzió nélkül. |
| 🟩 | [guard@64](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-64) | Magas odds, ritka bajnoksági esemény, részleges csapattörténet. |
| 🟩 | [guard@65](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-65) | Más szelvényekkel való részleges átfedés. |
| 🟩 | [guard@66](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-66) | Bizonytalan kezdő, motivációs vagy híralapú információ. |
| 🟩 | [guard@67](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-67) | Kupa-, válogatott- vagy rotációs kockázat. |
| 🟩 | [guard@68](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-68) | Astra vagy Grok kifogás, ha nincs hozzá ellenőrizhető adatforrás. |
| 🟩 | [guard@71](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-71) | Csak lágy vétót írhat felül. |
| 🟩 | [guard@72](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-72) | Strukturált indoklás, forrás, időbélyeg és konkrét mérkőzés/piac kell. |
| 🟩 | [guard@73](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-73) | A felülbírálás nem módosíthat oddsot vagy modellvalószínűséget. |
| 🟩 | [guard@74](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-74) | A `DAILY_223` módosítására nem használható. |
| 🟥 | [0.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-80) | A tényleges szelvény-előállítási folyamat dokumentálása. |
| 🟩 | [0.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-82) | A DAILY_223 változatlanságát ellenőrző regressziós tesztek. |
| 🟩 | [0.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-83) | Jelenlegi játékos/xG rangsorolási útvonal és forrásnyom. |
| 🟩 | [0.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-84) | Arany bemeneti csomagok készítése legalább három napra: sokmeccses hétvége, kevésmeccses hétköznap, vegyes kupa/válogatott nap. |
| 🟩 | [0.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-86) | Az arany csomagokhoz rögzíteni a jelenlegi szelvényeket, adatlefedettséget, futási időt és szolgáltatói hívásszámot. |
| 🟩 | [0.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-88) | Egyetlen géppel olvasható `feature_usage` jelentés létreховаása: `USED_DIRECT`, `USED_RANKING`, `USED_REVIEW`, `STORED_ONLY`, `MISSING`. |
| 🟩 | [1.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-95) | Kanonikus entitások: versenysorozat, szezon, csapat, játékos, mérkőzés, piac, kimenetel és bookmaker. |
| 🟩 | [1.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-96) | Szolgáltatói azonosítótérkép TheStats ↔ API-Football ↔ Football-Data ↔ The Odds API között. |
| 🟩 | [1.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-97) | Csak determinisztikus párosítás: szolgáltatói ID vagy ellenőrzött csapatnév + liga + kezdési idő. Fuzzy találat legfeljebb review-jelölt. |
| 🟩 | [1.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-98) | Forráselsőbbségi resolver mezőnként: |
| 🟩 | [1.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-103) | Ütköző forrásoknál megőrizni mindkét értéket; a kiválasztott érték mellett `resolution_reason` és frissességi sorrend legyen. |
| 🟩 | [1.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-104) | A teljes aktív ligakészlet TheStats-lefedettségi térképe. |
| 🟩 | [1.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-105) | Forrásonkénti hiány- és fallback-mátrix a dashboardon. |
| 🟩 | [2.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-111) | A TheStats, Football-Data és API-Football eredmények egységes, duplikációmentes mérkőzéstáblába rendezése. |
| 🟥 | [2.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-112) | Első körben a jelenlegi naptól öt évre visszafelé haladó import. |
| 🟩 | [2.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-115) | Külön megfigyelési idő: `event_time`, `published_at`, `observed_at`, `available_at`. |
| 🟩 | [2.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-116) | Odds-pillanatképek megőrzése bookmaker, piac és kimenetel szerint. |
| 🟩 | [2.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-117) | Játékos-, hiányzó-, kezdő-, időjárás- és híresemények kapcsolása a kanonikus mérkőzéshez. |
| 🟩 | [2.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-118) | Minden tanítóhalmazhoz verzió és tartalmi hash. |
| 🟩 | [2.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-119) | Look-ahead leakage teszt: a modell semmilyen, a döntési idő után elérhető adatot nem kaphat. |
| 🟩 | [2.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-121) | Háttérimport csak a napi csomag rögzítése után és a maradék API-kapacitásból futhat. |
| 🟩 | [3.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-127) | TheStats 1X2, totals, BTTS, DNB, handicap és támogatott félidős piacainak normalizálása a jelenlegi belső odds-sémára. |
| 🟩 | [3.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-128) | Bookmaker- és quote-időbélyeg megőrzése. |
| 🟥 | [3.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-129) | Legalább hét egymást követő napos párhuzamos összehasonlítás a The Odds API és API-Football értékeivel. |
| 🟥 | [3.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-134) | Eseménylefedettség, piaclefedettség, áreltérés, késés és hibaarány mérése ligánként. |
| 🟥 | [3.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-136) | Csak azon ligáknál előléptetni, ahol az egzakt eseményfedettség legalább 95%, és az időbélyeg-frissesség megfelel. |
| 🟩 | [3.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-138) | Piaconkénti fallback; egy hiányzó BTTS ne váltsa vissza az egész mérkőzést a régi szolgáltatóra. |
| 🟩 | [4.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-147) | A mostani ±4%-os xG-rangsorjel megtartása kontrollágként. |
| 🟩 | [4.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-148) | Csapat támadó- és védekező xG-ráták időbeli, hazai/idegen és ellenfélerősség szerinti normalizálása. |
| 🟥 | [4.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-150) | A gólalapú GAUSS/KEPLER/LAPLACE eredmények és az xG-modell külön valószínűségeinek kalibrált ensemble-ja. Nyers pontszámokat tilos összeadni. |
| 🟩 | [4.3.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-152) | Célzott, kvótavédett match-xG forráspótlás a már igazolt natív csapattörténetekhez; cache-first, valódi nyugták, pontos szabályos játékidő, SHADOW; hiány nem válhat nullává. |
| 🟩 | [4.3.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-154) | Újonnan számolt külön xG-előrejelzések hiteles prospektív mentése tényleges forrás-/dataset-/modellnyugtával, mérkőzés előtt; régi artifact nem válhat visszadátumozott jóslattá, SHADOW. |
| 🟥 | [4.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-156) | Több kulcsjátékos kiválasztása: támadó, kreatív játékos, védekező kulcsember és kapus. |
| 🟥 | [4.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-157) | Játékosjellemzők: gól, xG, gólpassz, xA, játékperc, kezdések, csapatrészesedés és rendelkezésre állás. |
| 🟥 | [4.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-158) | Posztonkénti hiányhatás és helyettesíthetőség; több hiányzó összhatása nem lehet egyszerű összegzés. |
| 🟩 | [4.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-159) | Prediktált kezdő csak bizonytalansági jel. Megerősített kezdő erősebb, időbélyeghez kötött jel. |
| 🟥 | [4.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-160) | Piaconkénti felhasználás: támadóhiány Over/BTTS, kapus- és védőhiány Over/BTTS/1X2, nem egyetlen univerzális szorzó. |
| 🟩 | [4.9](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-161) | Missingness-feature: a hiányzó adat ne jelentsen egészséges vagy gyenge játékost. |
| 🟩 | [5.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-167) | Versenytípus: liga, hazai kupa, nemzetközi klubtorna, selejtező, válogatott és barátságos mérkőzés. |
| 🟥 | [5.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-169) | Pihenőnapok, utazási távolság, előző/következő mérkőzés és menetrendi sűrűség. |
| 🟩 | [5.2.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-171) | A hiányzó stadionkoordináták megbízható ingyenes forrásának tényleges auditja és pontos natív meccshelyszínhez kötése; az igazolt helyszínadat eljuttatása a meglévő időjárás/utazás SHADOW-olvasókho… |
| 🟩 | [5.2.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-173) | A hat ismert korábbi stadion és a többi napi helyszín földrajzi lefedettségének soros bővítése, eredeti ingyenes forrásokkal és pontos natív meccshelyszín-kötéssel; a hiányzó előző–célmeccs távolsá… |
| 🟩 | [5.2.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-175) | A már azonosított12hiányzó korábbi natív meccsrészlet pontos cache-/kvótavédett megszerzése és eredeti receipt/azonosság ellenőrzése; nincs helyszínkitalálás, LLM vagy szelvényátírás. |
| 🟩 | [5.2.N4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-177) | A 11 újonnan igazolt korábbi létesítmény ingyenes koordinátaforrásának pontos auditja és időhelyes regiszterbővítése. A régi hatos és 23-as kiadás cutoff-története maradjon meg; null venue nem hely… |
| 🟩 | [5.2.N5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-179) | A14hiányzó teljes versenyközi csapatnaptár pontos cache-/kvóta-/receiptvédett beszerzése, teljes lapozás és célmeccs/időablak ellenőrzése; nincs helyszínkitalálás, LLM vagy szelvényátírás. |
| 🟩 | [5.2.N6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-181) | A11újonnan azonosított hiányzó korábbi natív meccsrészlet cache-/kvóta-/receiptvédett begyűjtése és pontos azonossági ellenőrzése; már ismert szolgáltatói null helyszínt ne gyűjtsön újra, nincs hel… |
| 🟩 | [5.2.N7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-183) | A10ténylegesen azonosított korábbi stadion ingyenes koordinátaforrásának pontos ellenőrzése és időhelyes regiszterbővítése; korábbi kiadások/hash-ek megőrzése, szolgáltatói null marad UNKNOWN. Új f… |
| 🟥 | [5.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-185) | Rotációs kockázat a versenytípus, kezdőváltozás és meccsterhelés alapján. |
| 🟥 | [5.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-187) | Tabellahelyzetből csak ellenőrizhető tét: kiesés, feljutás, nemzetközi hely, bajnoki esély, már eldőlt helyzet. |
| 🟩 | [5.4.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-189) | Hivatalos versenyszabályok szezon/csoport/tie-break és időbélyegzett forráskötött SHADOW regisztere a tabellatét olvasóhoz; csak igazolt szabály, UNKNOWN megőrzése, nincs automatikus motivációs súl… |
| 🟩 | [5.4.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-191) | Hivatalos hazai bajnoki szabályforrások pontos szezon-/szakasz-/tie-break kötött SHADOW-regiszterbővítése a jelenlegi natív célmeccsekhez, először 2. Bundesliga2026/27; korábbi kiadások/időhatárok … |
| 🟩 | [5.4.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-193) | A két DE2-célmeccs tényleges natív fázisforrásának receiptvédett ellenőrzése és a dokumentált explicit null szemantika bekötése; hiányzó kulcs UNKNOWN, nincs liga-/dátum-/forduló-/tabellainferencia. |
| 🟥 | [5.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-195) | Fizetési probléma, edzőváltás, csapatkonfliktus és egyéb humán állítás kizárólag időbélyeges, hivatkozható forrással. |
| 🟥 | [5.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-197) | Grok strukturált pro/kontra értékelés, majd Codex-egyetértési kapu. |
| 🟥 | [5.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-199) | A visszamért motivációs jelekből legfeljebb korlátozott, verziózott rangsorhatás; mentális állapotot nem szabad kitalálni. |
| 🟥 | [5.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-201) | Nap-jellemzők – péntek, szombat, vasárnap – csak történeti ligánkénti validáció után, nem globális szabályként. |
| 🟩 | [6.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-205) | A jelenlegi három tanulmányból származó hipotézisek változatlan tudásbázisa. |
| 🟩 | [6.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-206) | Csapat- és stadionazonosítóval kapcsolt történeti időjárás. |
| 🟥 | [6.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-207) | Hőmérséklet, csapadék, szél, széllökés és pályaállapot külön jellemzőként. |
| 🟥 | [6.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-208) | Piaconkénti validáció: Over/Under **kész és LIVE-kapuval védett**; BTTS, 1X2 és félidős piacokhoz külön holdout-validáció még szükséges. |
| 🟩 | [6.4.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-210) | Natív TheStats időjárási történeti bemenet SHADOW-ban; pontos identitás és eredmény/időjárás availabilityreceipt-védelem. |
| 🟩 | [6.4.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-212) | BTTS,1X2 és félidős piacok saját SHADOW cél-/időhelyes holdout-szerződése; OU-hatás nem örökölhető, hiányzó adatnál nincs promóció. |
| 🟩 | [6.4.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-214) | Valódi prospektív időjárási FORECAST-jellemző és már beengedett natív baseline-előrejelzés pontos forráskötött SHADOW-párjainak rögzítése. Csak tényleges elérhetőségi idő és15min-zárolás, eredeti r… |
| 🟩 | [6.4.N4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-216) | Az első natív történeti időjárási tanítóminta valódi forrásainak beszerzése pontos eseményhelyszínnel, meglévő olvasókkal, boundedkvóta/cache és aktuális receipt mellett. Reanalízis nem prematch fo… |
| 🟩 | [6.4.N5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-218) | AzN4-ben dokumentált14pontos natív stadionalias ingyenes, hivatalos létesítményazonossággal és Wikidata WGS84-entitásverzióval felülvizsgált koordinátaforrás-bővítése. Régi packet/PIT megőrzendő; ú… |
| 🟩 | [6.4.N6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-220) | A meglévő natív történeti REANALYSIS-olvasó nyerspayload/UTC-óra/mértékegység/érték/kérési koordináta/tényleges eseménystadion és saját availability szerinti szemantikai megerősítése, fit előtt. Cs… |
| 🟥 | [6.4.N7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-222) | Az N5tizenkét új pontos stadionjához az eredetiN4húszas eseménykörben legfeljebb12ingyenes ERA5kéréssel új valódi történeti időjáráspárok;0paidGET/LLM/fit/protokoll/rangsúly/LIVE. N6szigorúforráska… |
| 🟩 | [6.4.N8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-224) | A meglévő időjárási piaci SHADOW-protokoll valódi immutable tárolói provenance és első saját receipt/idempotensóra/readonlysource/PIT/resource ellenőrzése. Nincs dummyproductionprotokoll/fit/probab… |
| 🟥 | [6.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-225) | Hasonló ellenfélerősség és hazai/idegen szerep szerinti összehasonlítható történeti minta. |
| 🟩 | [6.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-226) | Többszörös összehasonlítás és kis minta elleni védelem. |
| 🟩 | [6.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-227) | Csak előre rögzített holdouton javuló hatás kerülhet LIVE módba. |
| 🟩 | [7.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-231) | Minden klubprofilhoz verziózott stratégiaszerződés: preferált piac, elemszám, odds-sáv, bajnokságtípus és bizonyítékküszöb. |
| 🟩 | [7.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-233) | Legalább egy állandó 2×2×3 és egy történeti Over 2.5 karakter. |
| 🟥 | [7.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-235) | 3–4 contrarian/value profil, igazolt ármozgással és `EV > 1.15` belépési feltétellel. |
| 🟩 | [7.3.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-237) | A valódi natív 24–48 órás ármegfigyelési párok fizikai ellenőrzése; előbb cache és receipt, szükség esetén egy meglévő kvótavédett friss megfigyelés. Ár-idő nem található ki; nincs küszöblazítás, ú… |
| 🟩 | [7.3.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-239) | A 36–48 órával előre történő, pontos natív meccsazonosságú első ármegfigyelés takarékos SHADOW-adatútja, napi csomagtól függetlenül, a meglévő kvóta/receipt/cache/timer réteggel. Nincs küszöblazítá… |
| 🟩 | [7.3.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-241) | A külön előzetes SHADOW-ármegfigyelések szigorú, nyugtavédett olvasói bekötése a natív SHADOW-opcióelemzésbe. Meglévő modellek/ármozgás-képlet újrahasználata, 0 új fit/API/Grok, LIVE-history változ… |
| 🟥 | [7.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-244) | Külön under-, BTTS-, félidő-, 1X2-, kupa-, forma-, xG-, játékos- és motivációspecialisták. |
| 🟩 | [7.4.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-245) | Kupa-specialista: forras- es receipt-kotott, kulon kapcsolhato SHADOW-kivalaszto; eles publikacio nelkul. |
| 🟩 | [7.4.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-247) | Kulon xG-specialista SHADOW-kivalasztasa a validalt xG-adatutbol. |
| 🟩 | [7.4.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-249) | Kulon jatekos-specialista SHADOW-kivalasztasa igazolt hianyzo/kezdo/jatekosadatbol. |
| 🟩 | [7.4.N4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-251) | Kulon motivaciospecialista SHADOW-kivalasztasa ellenorzott tet/hirek alapjan. |
| 🟩 | [7.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-253) | Egy profil ugyanazon szabályokból dolgozzon minden nap; a napi kínálat csak a jelölteket változtathatja, a személyiséget nem. |
| 🟥 | [7.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-255) | A nem-DAILY profil csak 10–50 cél-összszorzójú kombinációt publikálhat, vagy dokumentáltan tartózkodik. |
| 🟩 | [7.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-256) | Nem lehet gyenge lábbal mesterségesen feltölteni a szorzót. |
| 🟥 | [7.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-258) | Profilonként külön walk-forward teljesítmény és kalibráció. |
| 🟩 | [7.8.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-259) | A hiányzó, profil- és matematikai verzióhoz kötött walk-forward kalibrátor implementálása külön SHADOW valószínűségi rétegben. Csak ténylegesen időhelyes előrejelzés/eredmény/dataset/receipt párokb… |
| 🟩 | [7.8.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-260) | A napi natív SHADOW előrejelzések prospektív, saját valódi időnyugtával kötött mentése és kalibrációs olvasóbekötése; worker analysis/summary, saját fit/dataset, profil/model/termék és eredményazon… |
| 🟩 | [8.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-266) | A profilok először egymástól függetlenül állítsák elő a saját legjobb opcióikat. |
| 🟩 | [8.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-268) | Szelvénypont: normalizált profilpont × `sqrt(meccsszám)`. |
| 🟩 | [8.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-269) | 100%-os mérkőzés+kimenetel egyezés: duplikáció, törlendő. |
| 🟩 | [8.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-270) | 50% feletti mérkőzésátfedés: 0,7-es lágy büntetés. |
| 🟥 | [8.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-271) | Mérkőzéskitettség: legfeljebb 40–50% a READY csomagban. |
| 🟥 | [8.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-273) | A 20–30 darab cél elérése előtt a rendszer új profilopciókat vizsgáljon, ne ugyanazt a három mérkőzést kombinálja át. |
| 🟩 | [8.6.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-274) | Rögzített profilkapuk melletti, korlátos újmeccs-lefedettségi keresés SHADOW összevetése; változatlan éles opciók és forráskötött bizonyíték. |
| 🟥 | [8.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-276) | Ha nincs húsz bizonyított szelvény, a rendszer kevesebbet ad és `CAPACITY_SHORTFALL` jelentést készít a hiány okáról. |
| 🟩 | [8.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-277) | Minden szelvényhez géppel ellenőrizhető bizonyítékcsomag: odds, modell, forma, xG, játékos, kontextus és forráshiányok. |
| 🟥 | [8.9](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-278) | Portfólió-szintű becsült találati eloszlás és várható napi nyertesszelvény-szám; ez nem helyettesíti a későbbi tényleges KPI-t. |
| 🟩 | [8.9.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-280) | Korlátos, közös meccskimenetelt használó SHADOW találatiszám-forgatókönyv; külön modell/publikációs csomag, explicit hiány és feltételes eloszlás. |
| 🟩 | [9.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-284) | Astra/Codex a jelölteket a szelvényképzés előtt ellenőrzi. |
| 🟩 | [9.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-285) | Grok egyetlen közös csomagban kapja az összes READY szelvényt és a döntéshez szükséges, már letöltött adatot. |
| 🟥 | [9.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-286) | A csomagban minden kihagyott vagy tömörített adatkategória darabszámmal szerepeljen. |
| 🟩 | [9.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-287) | Grok minden szelvényhez pro, kontra és kérdéses láb listát adjon. |
| 🟩 | [9.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-288) | Codex csak konkrét, forrásolt kifogással érthet egyet. |
| 🟥 | [9.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-289) | Javítás kizárólag ugyanazon futás már ellenőrzött alternatívájából. |
| 🟩 | [9.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-290) | Legfeljebb egy javítási kör; nincs végtelen újragenerálás. |
| 🟩 | [9.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-291) | DAILY_223 a review-ban látható, de nem módosítható. |
| 🟩 | [10.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-295) | Valós, kutatási és papírszelvények külön eredménysora. |
| 🟩 | [10.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-296) | Profil, piac, liga, versenytípus, odds-sáv és elemszám szerinti találati és ROI-bontás. |
| 🟩 | [10.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-297) | Szelvényeredmény mellett lábszintű hibaanalízis. |
| 🟩 | [10.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-298) | Brier score, log-loss, kalibrációs görbe és odds-záróárhoz mért érték, ahol rendelkezésre áll. |
| 🟩 | [10.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-299) | Napi KPI: nyertes szelvények száma és nyertes összszorzó. |
| 🟩 | [10.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-300) | Gördülő 7/30/90 napos KPI: legalább 3 nyertes/nap cél teljesülési aránya, profit, drawdown és profilmegbízhatóság. |
| 🟥 | [10.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-301) | A tanulóréteg csak javaslatot készít; paramétermódosítás külön verzióban, walk-forward és SHADOW kapun keresztül léphet életbe. |
| 🟩 | [10.7.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-302) | Learning nem végrehajtó határa igazolt: 70 célzott PASS, ebből 21 artifact-határteszt; 0 paraméter/döntési diff. A szülő 10.7 NYITOTT: integrált verzióhoz kötött walk-forward/SHADOW release-kapu mé… |
| 🟩 | [10.7.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-303) | Integrált, eredeti profil-/modell-/dataset-/kalibrációverzióhoz és bizonyítékhoz kötött SHADOW release-kapu; időhelyes minták, független proper-score értékelés és nyitott adatkapuk ellenőrzése. Hiá… |
| 🟩 | [10.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-304) | Egyetlen rossz vagy kiugró nap nem változtathat stratégiát. |
| 🟩 | [11.1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-308) | UNIT: új modul saját határ- és hibatesztekkel. |
| 🟥 | [11.2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-309) | INTEGRATION: forrás → normalizálás → modell → profil → portfólió → rögzítés teljes adatút. |
| 🟩 | [11.2.N1](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-310) | Natív forrás→normalizálás→kiválasztott termék→tesztnapló audit és numerikus blokkolás reprodukció: 51 eredeti forrás,36 meccs,1340 előzmény,3040 ár; 136 célzott PASS. A teljes 11.2 NYITOTT; riport:… |
| 🟩 | [11.2.N2](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-311) | Semleges pályás történeti rekordok külön mintapolitikája: verziózott, kapcsolható SHADOW adatút és határtesztek; semleges mai meccs és kevés csapatelőzmény nem kerülhető meg, LIVE előléptetés nélkül. |
| 🟩 | [11.2.N3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-312) | Kontextuskeret érdemi, méltányos prioritása és cache: a teljes meccskör kezdőcsapat/sérülés/játékos/xG adatai ne maradjanak pusztán a lekérési sorrend miatt REQUEST_BUDGET állapotban; valódi hiányt… |
| 🟩 | [11.2.N4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-313) | Játékosspecialista tényleges SHADOW bekötése: várható/megerősített kezdőcsapat, sérülés/eltiltás és játékosstatisztika forrásaihoz kötött opcióhatás és lábszintű bizonyíték; hiány nem nulla, várhat… |
| 🟩 | [11.2.N5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-314) | Natív modell-előzmény adatút: a már letöltött, fizikailag rendelkezésre álló adatbázisból csapat/szezon/időkapu szerint ellenőrzött SHADOW modellbemenet. Nincs 2.4 újraimport, név szerinti azonossá… |
| 🟩 | [11.2.N6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-315) | Natív újraszámolt modellbecslésekből forráskötött SHADOW szelvényopciók és a meglévő játékosspecialista opcióvéleményének bekötése. Változatlan ID/quote/idő/10–50/minimumkapuk, nincs éles előléptet… |
| 🟩 | [11.2.N7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-316) | TheStats élő raw/HTTP-beérkezési receipt adatút append-only bekötése: tényleges új beérkezés, azonos payload ismételt fogadása és cache-hit elkülönítése. Régi snapshot/arany/időbélyeg nem írható fe… |
| 🟩 | [11.2.N8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-317) | Szigorú natív forrásolvasók és forrásaliasok bekötése az új tényleges HTTP-megfigyelési bizonyítékra a napi SHADOW láncban; azonos payload új fogadása, ismert régi receipt és cache-idő elkülönítése… |
| 🟩 | [11.2.N9](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-318) | A natív modell → forrásolt ár/opció → játékosspecialista SHADOW-lánc kontrollált napi bekötése a Windows-automatán futó pipeline-ba. Friss tényleges források, API/CPU-keret, egyetlen modellilleszté… |
| 🟩 | [11.2.N10](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-319) | Az éles natív célmeccs/történeti forrásbizonyíték- és frissességlefedettség rendezése: 36 jelenleg UNPROVEN célmeccs és 14 régi UEFA raw hiányának pontos fizikai auditja. Történeti raw legkorábban … |
| 🟩 | [11.2.N11](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-320) | A már implementált kupaspecialista bekötése az időkorlátos napi natív SHADOW-workerbe, ugyanazon frissen számolt natív modellek/opciók felhasználásával. Fizikai taxonomy/target/price bizonyíték, me… |
| 🟩 | [11.2.N12](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-321) | A már implementált xG-specialista bekötése az időkorlátos napi natív SHADOW-workerbe. Ugyanazon natív kontrollmodellek és forrásolt, időhelyes xG-adatok használata, meglévő saját xG-kihívó számítás… |
| 🟩 | [11.2.N13](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-322) | A már implementált humán/motivációspecialista SHADOW felülvizsgálati választójának bekötése az időkorlátos napi natív workerbe, ugyanazon kontrollmodellekkel. Valódi forrásolt hír-/tabellakontextus… |
| 🟩 | [11.2.N14](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-323) | A két éles UEFA TheStats-tabella 72-72 sorának forrás- és scopeszintű auditja: AMBIGUOUS_STANDINGS_TEAM_WITHIN_GROUP okának azonosítása. Kiolvasási javítás csak a tényleges raw-ban igazolt tábla-, … |
| 🟩 | [11.2.N15](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-324) | Napi natív mérkőzésenkénti időjárási kontextus és forráshasználati audit SHADOW-bekötése a meglévő olvasókkal: pontos helyszín/koordináta, kezdés, forecast és valódi rendelkezésreállási receipt öss… |
| 🟩 | [11.2.N16](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-325) | Teljes napi natív SHADOW lábankénti adathasználati audit: forrás/dataset/receipt → modell → profil/specialista → opció tényleges kötése. Numerikus múlt, odds, játékos, xG, kupa/naptár/rotáció, hír/… |
| 🟩 | [11.2.N17](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-326) | A decision_data_usage lefedettség- és felhasználásjelöléseinek javítása az N16 bizonyítékai alapján: UNKNOWN/üres időjárási sor ne legyen felhasznált forecast, AVAILABLE statisztika ne bizonyítson … |
| 🟩 | [11.2.N18](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-327) | A meglévő naptár-/rotációs olvasó napi natív opciókhoz kötött SHADOW-kontextusa: pontos meccs/csapat/kezdés/dataset/receipt, minden lábhoz ellenőrizhető hivatkozás. Részleges naptár vagy hiányzó ig… |
| 🟩 | [11.2.N19](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-328) | Naptár-/XI-forrásolvasók igazítása a már működő TheStats HTTP-megfigyelés/receipt-alias szerződéshez: az első raw-metaadat átírása nélkül legyen olvasható a későbbi, valóban tanúsított azonos URL/b… |
| 🟩 | [11.2.N20](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-329) | A teljes, szűretlen csapatnaptárak kvóta- és cache-korlátos gyűjtésének bekötése a napi natív SHADOW-előkészítésbe. A javított olvasó ne csak kézi próbán kapjon friss forrást. Determinisztikus, mél… |
| 🟩 | [11.2.N21](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-330) | A historikus háttérimport már N20 előtt eltérő aktiválási forráshash-ének önálló auditja és helyreállítása. Előbb a régi engedélyezett forráscsomag és a jelenleg validált kód eltérésének ellenőrzés… |
| 🟩 | [11.2.N22](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-331) | A történeti TheStats-szinkron321-es importjának pontos raw/HTTP-alias/archív rekordkötése: a bájtazonos raw első daily_context kategóriája önmagában ne legyen hamis hiány, de kategórialazítás helye… |
| 🟥 | [11.2.N23](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-332) | Natív stadion/venue-azonosság és koordináta forráskötés: a napi célmeccsek tényleges TheStats/archív helyszínadataihoz pontos venue-ID és ellenőrizhető koordinátapár SHADOW-olvasója. Előbb a fizika… |
| 🟩 | [11.2.N24](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-333) | A teljes aktuális kutatási csomag forrás→specialista→opció→lábszintű tényleges adathasználatának olvasási auditja, beleértve a meglévő Grok-adatcsomagot. Numerikus bemenet, SHADOW-vélemény és hiány… |
| 🟩 | [11.2.N25](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-335) | A Grok független ellenőrző 11 kifogást blokkoló INVALID_PACKET hibájának javítása: az opcionális ismeretlen model_stability=null ne okozzon teljes csomagelutasítást. Ne találjon ki/nullázzon metrik… |
| 🟩 | [11.2.N26](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-337) | Az N25 lezárása után külön alpontban a játékosspecialista jelöltlábainak időjárási SHADOW-véleményezési bekötése a natív opcióépítőbe, pozitív/nemüres specialista kontrollal; nincs új numerikus súl… |
| 🟩 | [11.2.N27](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-339) | Az N25/N26 javítások után a forrás–specialista–szelvény tényleges adathasználat és a fennmaradó lefedettségi/numerikus kapuk read-only ellenőrzése; következő meglévő szűk javítás kijelölése. |
| 🟩 | [11.2.N28](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-341) | Célzott, cache-first, kvóta- és időkorlátos TheStats játékosforrás-gyűjtés SHADOW: igazolt aktív hiányzók kerete/posztja és szezonadata, append-only kanonikus forráskötéssel és szigorú olvasói pozi… |
| 🟩 | [11.2.N29](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-343) | A már elfogadott, friss kanonikus játékosadatok átadása a következő Grok-bemenethez elkülönített SHADOW-csomagon igazolva; pontos forrás/TTL/receipt, veszteségmentes tömörítés, nincs új adatgyűjtés… |
| 🟩 | [11.2.N30](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-344) | Friss, csak olvasó forrás→modell→szelvény adatfelhasználási audit az új játékos/xG/tét/korai ár adatút után. Tényleges éles numerikus, SHADOW numerikus és kontextushasználat elkülönítése, gyűjtött … |
| 🟩 | [11.2.N31](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-345) | A végső bizonyítékcsomag lezárási sorrendjének javítása a nightly-run-id és kártya method_version véglegesítése után.291forráspayload ép,2kártyahash és a csomag run-id eltér; a szigorú validator me… |
| 🟩 | [11.2.N32](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-347) | A6megtartott történeti/venue-forma bizonyíték3valós tételen MISSINGforrásjelölésének javítása a számított fresh-jelző mellett. Pontosazonosság/hash/idő/eredeti frissességi szemantika,0védettDAILYfo… |
| 🟩 | [11.2.N33](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-349) | A két forráskötési javítás utáni, kizárólag olvasó adatfelhasználási végellenőrzés: valós forrás → időhatár → kiválasztott tétel mátrix; numerikus/rangsorolási/forma/review/tárolt/hiányzó adatok kü… |
| 🟩 | [11.2.N34](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-351) | A meglévő natív prospektív rögzítő félbemaradt lezárásának külön SHADOW-helyreállítása: eredeti203sor/15fixture/65input megőrzése, valós új saját jelenléti igazolás és csak az újclock+15min utáni c… |
| 🟥 | [11.3](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-355) | REPLAY: legalább 30 korábbi nap pont-időhelyes visszajátszása. |
| 🟥 | [11.4](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-356) | SHADOW: legalább hét egymást követő élő nap publikálás nélkül. |
| 🟥 | [11.5](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-357) | PAPER: legalább 30 nap 1 EUR egységtétes papírmérés. |
| 🟥 | [11.6](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-358) | CANARY: egyszerre csak egy új feature-család LIVE módba emelése. |
| 🟥 | [11.7](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-359) | ROLLBACK: konfigurációs visszaállítás az előző modell- és adatútvonalra adatvesztés nélkül. |
| 🟥 | [11.8](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-360) | PROMOTION: csak dokumentált lefedettség-, kalibráció-, stabilitás- és portfólióminőség-javulás után. |
| 🟩 | [guard@364](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-364) | DAILY_223 byte-szinten változatlan. |
| 🟥 | [guard@365](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-365) | Nincs adatvesztő migráció vagy beragadt félállapot. |
| 🟩 | [guard@366](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-366) | Ismételt futtatás idempotens. |
| 🟥 | [guard@367](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-367) | Hiba esetén az előző stabil útvonal működik. |
| 🟥 | [guard@368](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-368) | Nincs jövőbeli vagy mérkőzés utáni adat a döntésben. |
| 🟥 | [guard@369](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-369) | A döntési hatás forrása és mértéke látható a riportban. |
| 🟩 | [guard@370](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-370) | A teljes tesztcsomag sikeres. |
| 🟩 | [guard@371](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-371) | Az arany hétvégi, hétköznapi és kupa/válogatott replay sikeres. |
| 🟩 | [guard@372](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-372) | A 20–30 szelvényes cél nem okoz gyenge töltelékszelvényt. |
| 🟥 | [guard@373](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-373) | Minden nem-DAILY READY szelvény betartja a profil odds-sávját. |
| 🟥 | [guard@374](ARTHUR_GEMINI_COMPARISON_2026_10_24.md#requirement-374) | A dashboard a lefedettséget, fallbacket, aktív feature-öket és tartózkodási okokat őszintén mutatja. |
