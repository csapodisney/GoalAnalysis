# Adatvesztés nélküli konfigurációs rollback — saját elfogadás folyamatban

Az előző, valóban elfogadott7.4.N6konfigurációt az akkori fizikai source-manifesthez kötöttük. A mostani és korábbi modell-/livebeállítások és7policyfájl bájtjai megegyeznek; két később hozzáadott ACTIVEpolicy korábbi hiánya bizonyított. A visszaállítás ezeknél a meglévő SHADOW alapértelmezést adja, nem kitalált korábbi modellt vagy kivezetett élőprovidert.

| Státusz | Bizonyíték / valódi maradék |
|---|---|
| 🟩 |75célzott teszt,köztük34újrollback/atomic/crash/admission/CLI-kontroll,10különeredeti replay-adapter ellenpróba,RuffPASS. |
| 🟩 |Valódi korábbi/currentconfig izolált másolatának oda-vissza SHADOW-ja PASS:9aktuálisconfig exactbytehelyreállítva,2priorabsence→SHADOW→currentACTIVE,régi valósriport és3modellfájl változatlan.0API/LLM/fit/generálás/productionconfig-írás. |
| 🟩 |Újadmineszköz:szűk9configallowlist,hashkötöttimmutablebundle,exactbyte+absence,CASésközösRunLock,atomikusexkluzívnapló/preparedintent,hibás/megszakadtműveletre recovery,idempotensismétlés. Adat/report/SQLite/raw/source/secretfájlnemírhatómanifestből. |
| 🟩 |Félbeszakadt restore a normál közös lockon belépés előtt blokkol; helyreállítás külön visszaellenőrzi az ismert before/targetbájtokat. Külső,oda nem tartozó szerkesztést nem ír felül. |
| 🟩 |A normálCLI a lock megszerzése után újraolvas modell-éslivebeállítást; a dashboardsettingsíró is ugyanazt a keresztfolyamat-lockot használja.0újmodell/súly/rang/választási képlet. |
| 🟥 |A végleges forrás saját5776teljes tesztje és3újactualnumerikusreplay márfut,egyszeri75perceskörben. Még nem saját teljeselfogadás. Régi5742teszt nem az új forrás tanúsítványa. |
| 🟥 |A valódi üzemi prior→currentkonfigurációsdrill,adatmegőrzés/runtime/checkpoint/11.7lezárás még hátravan; csak saját teljeselfogadás után. Műveleti rollbackhez nem indítunk fizetős sports/LLMgenerálást. |
| 🟥 |A teljesprojekt és az eredeti215követelményfrisszöld/pirosauditja külön nyitott. Munkalista136lezárt/31nyitott/167nem teljeskészség. |

A módosítás6forrás/testfájlt érint; fagyasztott1219forrás/4016korábbi ledger-sor/5735védettarchívum. A meglévőworker tényleges importja,könyvtárfeloldása,eredetiDaily-guardja,5776tesztgyűjtése és mind8numerikus ellenőrzés/require AST-egyezése indítás előtt PASS. Nincs új általános jelentéseltérési engedmény vagy aranyreferencia-átírás. A teljeskör állapotát az aktuális bizonyítékból kell olvasni; duplikált teszt,fit,worker vagy automatikusretry nem indul.

A visszaállítható csomag megőrzi a kötelező exactquote-review ACTIVEkapukat,nativeGrokSHADOW-t,egyeztetettgpt-6-astra/grok-4.7modelleket és eredetiGrokkeretet. A snapshot nem kalibrációs promotion-jóváhagyás. KizárólagTheStatsélősport/odds; eredeti2×2×3/numerikusrang/súly/aranyhash/régiszelvény/quoted_atnull-policy változatlan.

Az első izolált helper indokolatlan whole-fingerprint összehasonlítása saját capture-idő miatt megállt; az eredeti log/script megmaradt, a végleges próba külön mentett before/afterforrás-ésledgerrészeket exactmódon ellenőriz. Egy korai atomikus-tempfájlnév a mély Windows-tesztgyökérben túl hosszú volt,33setuphiba bizonyítéka megmaradt; a végleges rövid exkluzív tempnevekkel mindeneredetiellenőrzés és75célzott teszt PASS. Ezek nem tényleges üzemi restore-kísérletek vagy adatvesztési hibák.

Daily jelenleg a fejlesztéshezOFF,legacyDaily223mindigOFF. Saját elfogadott forráson üzemi próba és igazoltDailyONhelyreállítás szükséges,majd a következő engedélyezett forrásponthoz sameRunLockOFF. Eredeti2026-10-24határidő nem teljesült;2026-10-26 17:00Berlin munkacél,nem előre garantált készültség.
