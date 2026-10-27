# Helyi HTTP-elutasítás: javított forrás, új aggregált elfogadás folyamatban

2026-10-27T19:02:57.724276+00:00. A 403 auth-elutasítás után a szerver explicit lezárást, flush/half-close és legfeljebb
16 KiB /1 másodperces abszolút DRAIN szakaszt használ. A jogosultsági döntés továbbra is a JSON,
üzleti és adatbázisműveletek előtt történik. A normál 16 KiB-os bodylimit, státuszok, matematika,
rang, súly, READY és providerkeretek változatlanok. Az 1 másodperc a DRAIN szakaszt korlátozza.

A változatlan régi handler külön, szétválasztott fejléc/body diagnosztikája reprodukálta a
WinError10053 mechanizmust; ez nem bizonyítja a történeti egyetlen hiba kizárólagos okát.
A javítás19 izolált, másolt forrású kontrollja PASS, köztük ugyanez a késleltetett body-sorrend teljes403-mal.
Az egyetlen telepített19 futás19pont/100% után JUnit hostname→Windows-verzió alfolyamatát a guard blokkolta:
natív1,38byte részlegesXML, finalIOproof nélkül. Ez nem strukturált19PASS, és nem igazolt APP-logikai hiba.
Az eredeti outcome/log/XML változatlan; nincs új standalone19 futtatás vagy szintetikus PASS/XML.
Root-default Ruff,10adapter,
az eredeti nyolc AST/require/Dailyguard és az új authority import/reject kapui PASS.
Az első collection a subprocess-osztály helyére tett függvény miatt natív2-vel megállt, teszttörzs nélkül.
Eredeti naplója/eredménye változatlan; a külön osztályazonosságot megőrző completion natív0-ja adta
a pontos 6445 teljes gyűjtést. Ez nem az eredeti hibás collection0-ra átnevezése.

**Az első új saját 6445 teljes ellenőrzés és három új actual replay most fut.**
Nincs még célzott19-, full-, időjárási fizikai consumer-, runtime- vagy scope-elfogadás. Az új teljes XML minden
azonossága, a 284 weather-eset, a 19 új HTTP-eset és a korábban hibás régi HTTP-eset külön pontos
ellenőrzést kap. A célzott19 elfogadásának kizárólagos alapja az új teljes XML pontos19 részhalmaza
(FULL_XML_SELECTED_SUBSET); a korábbi standalone natív1 változatlan marad. Saját worker0 után kizárólag az új teljes futás 28 weather-fixture-gyökere /26 DB-je
olvasható vissza; a marker régi weather scope-neve szándékosan változatlan. Ez TESTONLY adatfolyam.

A korábbi weather full6426 natív1 és standalone target124 megmaradt. Nincs régi full/target/replay
újraindítás, kézzel zöld XML vagy az eredeti hiba0-ra átnevezése. Az elfogadott történeti forrás az
Astra22b8; a weather750 és a b950 docs-only publikáció nem kapott önálló elfogadást.

1271 jelenlegi forrásból csak a dashboard és egy új teszt változott a mostani telepítésben;
1269 másik fájl byte-exact. A publikáció az összes nyolc aggregált forrást tartalmazza.
4092 ledger-sor, négy main-store, 155 stadionfájl és a D57/date27 riport 28 fájlja/három hash-szerepe védett.
Saját teljes siker és fizikai consumer0 után jöhet Background első CAS és meglévő riport metadata-runtime;
Prospective teljes RAW változatlan. Daily és legacy OFF, nincs új üzleti/provider/LLM-futás.

Csak11.2 evidence változott; a másik214 sor és minden státusz/reference exact. 215:176GREEN/39RED;
a külön lista136kész/31nyitott/167tétel, nem projektarány. Következő elfogadás után: ticket-quality-post-weather-normal-validation-preflight;
ez új preflight, nem üzleti futtatási engedély. [Bizonyíték](EVIDENCE-SOURCE-PENDING.json) · [215](STATUS-215-SOURCE-PENDING.json)
