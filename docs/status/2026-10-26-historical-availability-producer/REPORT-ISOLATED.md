# Producer: elkülönített író és normál adatúti előpróba

Az egyoldalas, pontos kulcslistára korlátozott író és a meglévő normál cached-page/Sink út összekapcsolásának TESTONLY próbája igazolt. Az éles producer még nincs megvalósítva vagy elfogadva; a scope nyitott.

- **22 pure +12 fizikai író +4 normál adatúti kontroll PASS.** Ez nem új alkalmazás-full vagy három replay elfogadás.
- Az író a commitált forrást és a fizikai raw bytes/URL/óra/kulcslistát előbb csak olvassa, majd írás előtt újra összeveti. A generikus kurzor helyett kizárólag az egész oldal pontos kulcsai szerepelnek.
- Az elkülönített retained adatbázisban 1 raw +12 result witness után a változatlan strict fogyasztó **1655→1667**. Ez csak a tesztadatbázis eredménye. A korábbi cutoff1655 és az eredeti cutoff1143 matches+lineage egyezése megmaradt; halfscoreUNKNOWN maradt.
- A tényleges, változatlan normál Sink a tizenkét új tesztverziót saját valós ingested_at idővel mentette, a régi observed_at megtartásával. A metadata-hiba nem léptette a checkpointot. Commit előtt minden új witness visszagördült; commit után13 megmaradt és STOP lett, automatikus retry nélkül. A már commitált canonical sorokat nem állítottuk visszagörgetettnek.
- Az első túl hosszú Windows fixture-út és az első túl széles teszthook rekurziós hibája eredeti segéddel/XML/log/hash-sel megmaradt. A V2 javítás csak az elkülönített tesztútvonal és a valódi Sinkre korlátozott hook változása.
- Független fizikai RO visszaolvasás: **1233 forrásfájl /4087 ledger-sor változatlan**, régi receipt- és verzióprefix megőrzött, **az éles19 raw/271 result witness továbbra hiányzik**. Daily ON, legacy OFF. A normál háttér más elfogadott útja tovább dolgozhat; saját nulla éles írásunk nem nulla globális aktivitást jelent.

Az új rendelkezésreállás kizárólag commitált visszaolvasás és fizikai ellenőrzés utáni MOST, SOURCE_PRESENCE_UPPER_BOUND lehet. Nem korábbi publikálás, HTTP-megfigyelés, quote-idő, eredeti ingestion vagy prematch/PIT-ismeret. Nincs új letöltés, éles capture/batch, N9-claim, modell-fit, fizetős generálás, alkalmazásforrás- vagy döntési változás.

Következő egyetlen pont a minimális, elsőosztályú éles exact-page/keyset író és normál Sink-integráció. Az artifact hook nem telepítendő éles megoldásként, a pathguard egyszerű eltávolítása nem elfogadás. Tényleges forrásváltozás előtt ugyanazon RootRunLock alatt maintenance OFF; utána saját végleges célzott/Ruff/import/root/collection/Dailyguard/eredeti8check előpróba, ÚJ full +3 ÚJ actual replay, elfogadott runtime és Daily ON kell. Régi271/181/first512/N9 újrajátszására vagy kézi HTTP/befogadási batchre ez nem felhatalmazás.

Az eredeti215 **176 zöld /39 piros-részleges**, csak2.2 bizonyítéka frissült, státusza RED; a másik214 sor azonos. A külön167 munkalista **136 kész /31 nyitott**, checkboxai változatlanok. Ez nem teljes ötéves fedés, PIT-mintaszám, LIVE-kapacitás, profit- vagy projektkészség.

[Fizikai bizonyítékok és SHA-k](EVIDENCE-ISOLATED.json) · [Friss teljes215, egyetlen érintett delta](STATUS-215-ISOLATED.json)
