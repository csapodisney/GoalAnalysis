# Adatvesztés nélküli konfigurációs rollback — tesztelkülönítési hiba, még nincs saját elfogadás

Az előző, valóban elfogadott7.4.N6konfigurációt az akkori fizikai source-manifesthez kötöttük. A mostani és korábbi modell-/livebeállítások és7policyfájl bájtjai megegyeznek; két később hozzáadott ACTIVEpolicy korábbi hiánya bizonyított. A visszaállítás ezeknél a meglévő SHADOW alapértelmezést adja, nem kitalált korábbi modellt vagy kivezetett élőprovidert.

| Státusz | Bizonyíték / valódi maradék |
|---|---|
| 🟩 |75célzott teszt,köztük34újrollback/atomic/crash/admission/CLI-kontroll,10különeredeti replay-adapter ellenpróba,RuffPASS. |
| 🟩 |Valódi korábbi/currentconfig izolált másolatának oda-vissza SHADOW-ja PASS:9aktuálisconfig exactbytehelyreállítva,2priorabsence→SHADOW→currentACTIVE,régi valósriport és3modellfájl változatlan.0API/LLM/fit/generálás/productionconfig-írás. |
| 🟩 |Újadmineszköz:szűk9configallowlist,hashkötöttimmutablebundle,exactbyte+absence,CASésközösRunLock,atomikusexkluzívnapló/preparedintent,hibás/megszakadtműveletre recovery,idempotensismétlés. Adat/report/SQLite/raw/source/secretfájlnemírhatómanifestből. |
| 🟩 |Félbeszakadt restore a normál közös lockon belépés előtt blokkol; helyreállítás külön visszaellenőrzi az ismert before/targetbájtokat. Külső,oda nem tartozó szerkesztést nem ír felül. |
| 🟩 |A normálCLI a lock megszerzése után újraolvas modell-éslivebeállítást; a dashboardsettingsíró is ugyanazt a keresztfolyamat-lockot használja.0újmodell/súly/rang/választási képlet. |
| 🟩 |A saját három ténylegesen újraszámolt numerikus replay mind8ellenőrzése PASS;97/33/1tényleges DatasetStore-manifeszt és3modellhash külön root-visszaolvasva. Ez nem teljes suite-elfogadás. |
| 🟥 |A saját5776teljes kör még fut, de a megőrzött részlogban már13hiba látszik. A végleges XML és teljes eredmény még nincs meg; ezért sem teljes elfogadás, sem üzemi rollback nem állítható. |
| 🟩 |A két új CLI-teszt által továbbadott munkakönyvtár-hiba külön sorrendi ellenpróbában reprodukált:2új teszt után4eredeti promptteszt hibázik. A két cleanup-sort kizárólag memóriabeli jelöltként hozzáadva2jelölt+13eredeti regresszió PASS;mind37eredeti assert és minden egyéb AST változatlan. A fagyasztott forrás még nincs módosítva. |
| 🟩 |Külön artifact-adatmegőrzési reader13ellenpróba PASS:valódi SQLite/WAL committedadat,typedérték/duplikátum/rowid/schema és nyersriport megőrzése,határértékek és változáselutasítás. Ez nem üzemi adatmegőrzési drill. |
| 🟥 |A valódi üzemi prior→currentkonfigurációsdrill,adatmegőrzés/runtime/checkpoint/11.7lezárás még hátravan; csak saját teljeselfogadás után. Műveleti rollbackhez nem indítunk fizetős sports/LLMgenerálást. |
| 🟥 |A teljesprojekt és az eredeti215követelményfrisszöld/pirosauditja külön nyitott. Munkalista136lezárt/31nyitott/167nem teljeskészség. |

A módosítás6forrás/testfájlt érint; fagyasztott1219forrás/4016korábbi ledger-sor/5735védettarchívum. A meglévőworker tényleges importja,könyvtárfeloldása,eredetiDaily-guardja,5776tesztgyűjtése és mind8numerikus ellenőrzés/require AST-egyezése indítás előtt PASS. Nincs új általános jelentéseltérési engedmény vagy aranyreferencia-átírás. A teljeskör állapotát az aktuális bizonyítékból kell olvasni; duplikált teszt,fit,worker vagy automatikusretry nem indul.

A visszaállítható csomag megőrzi a kötelező exactquote-review ACTIVEkapukat,nativeGrokSHADOW-t,egyeztetettgpt-6-astra/grok-4.7modelleket és eredetiGrokkeretet. A snapshot nem kalibrációs promotion-jóváhagyás. KizárólagTheStatsélősport/odds; eredeti2×2×3/numerikusrang/súly/aranyhash/régiszelvény/quoted_atnull-policy változatlan.

Az első izolált helper indokolatlan whole-fingerprint összehasonlítása saját capture-idő miatt megállt; az eredeti log/script megmaradt, a végleges próba külön mentett before/afterforrás-ésledgerrészeket exactmódon ellenőriz. Egy korai atomikus-tempfájlnév a mély Windows-tesztgyökérben túl hosszú volt,33setuphiba bizonyítéka megmaradt; a végleges rövid exkluzív tempnevekkel mindeneredetiellenőrzés és75célzott teszt PASS. Ezek nem tényleges üzemi restore-kísérletek vagy adatvesztési hibák.

Daily jelenleg a fejlesztéshezOFF,legacyDaily223mindigOFF. Saját elfogadott forráson üzemi próba és igazoltDailyONhelyreállítás szükséges,majd a következő engedélyezett forrásponthoz sameRunLockOFF. Eredeti2026-10-24határidő nem teljesült;2026-10-26 17:00Berlin munkacél,nem előre garantált készültség.

A futó5776kört és22rögzített worker-inputot nem módosítottuk, új teljes teszt vagy fit nem indult. A külön diagnosztika és a még nem alkalmazott két soros javítás nem helyettesíti az új változat saját teljes elfogadását. Következő: az eredeti kör végleges XML/log megőrzése és ellenőrzése; csak utána a tesztadapter javítása, új verziózott validálás. Az üzemi visszaállítás továbbra blokkolt. A részletes pillanatkép: `docs/validation/2026-10-26-configuration-rollback-cwd-diagnosis.json`.
