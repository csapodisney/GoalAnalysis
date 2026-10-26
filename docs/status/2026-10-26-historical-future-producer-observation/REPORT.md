# Normál producer: hat oldal igazolt, üres oldalnál konkrét megállás

A visszaállított runtime után a meglévő normál időzítő ténylegesen dolgozott. A 15:39:08UTC-kor indult batch **7 hitelesített HTTP200 kvóta-receiptet**, köztük **6 teljesen igazolt oldalon308 kanonikus verziót,6 raw- és308 result-availability witnesset** hagyott. Minden pontos oldal-kulcslista, fizikai raw hash/méret/URL/óra, canonical commit-idő, journal és immutable receipt forráshash/idő egyezik. A dedicated HTTP-observation ettől külön szerződés; a kvótareceipt nem helyettesíti. Az audit nem indított letöltést vagy befogadást.

A 15:57:33UTC snapshot strict eredményolvasója **1963**, a korábbi1655 és az eredeti1143 cutoff matches+lineage exact. Minden halfscoreUNKNOWN. Ez SHADOW availability-adat, nem308 prematch/PIT-tanítópár, új időjáráspár, modellpromóció vagy LIVE-kapacitás. A korábbi19 raw/271 result cohort továbbra nincs befogadva; mind181 régi alias-clock mismatch receipt nélkül maradt.

A hetedik oldal **üres,70 bájt,0 sor,0 total_pages**. Ennek content-addressed raw bodyja már létezik: eredetileg comp_0406/2026-07 oldal,2026-10-21T22:20:19Z megfigyeléssel; a mostani durable oldal comp_451101/2024-04,2026-10-26T15:39:19Z. Ugyanaz a bodyhash nem ugyanaz a forrásproveniencia. Régi raw-witness is létezik, de az a régi URL/órához kötött, nem az új oldalé.

A producer **RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH** kapuja ezért helyesen elutasítja az új witnesset. A konkrét kódhiba/policy-rés: ez a kapu az üres oldalak NO_ADMISSION ága előtt fut, így a nulla rekordos oldal kurzora sem halad tovább. Nincs hozzá új canonical sor, once-intent vagy witness. A háttérállapot STARTED maradt; a folyamat már nem fut. A scheduled wrapper a nightly exitkódját őrzi, a háttér stdoutját és exitkódját nem emeli át: a LastTaskResult0 nem teljes background-siker bizonyítéka.

A friss delta20/20, a régi ötéves főterv **617/1220**,603 lekérdezési ablak hátra; ez nem603 biztos HTTP-kérés. Az audit óráján publicationAVAILABLE és mindkét activation current, az első kapu **COOLDOWN16:09:08Z-ig**; a másodlagos kvóta **QUOTA_REFRESH_REQUIRED/0request**. Ebből nem következik kézi HTTP/retry vagy importengedély.

Független RO visszaolvasás:1235 source/4087 ledger-sor változatlan,115736 régi receipt és16832 korábbi native-version prefix exact, hat oldal314 witness és az üres frontier fizikai tartalma igazolt. Daily ON, legacy OFF. Az első új RO helper hibásan feltételezte, hogy az üres bodyhoz semmilyen korábbi raw-receipt nem létezik; eredeti helper/baseline/SHA/diagnózis megőrzött. V2 a régi body-witness és az új oldal provenienciáját külön ellenőrzi. Nincs alkalmazás-, eredeti teszt-, forrás-, API-, LLM-, fit-, generálás-, receipt- vagy importmódosítás.

Következő egyetlen pont: **historical-empty-page-no-admission-contract**, előbb artifact/pure ellenpróba. Az üres oldal cursor/no-op szemantikáját kell külön bizonyítani új raw/result witness nélkül, minden nem üres oldal szigorú forráskapujának megtartásával. Nincs régi URL/óra átírása, hamis FETCHED_AT, receipt-backfill,181 alias/first512/N9 újrajátszás vagy kézi HTTP/cached batch. Appfix csak igazolt minimális kontraktus után, RootRunLock maintenanceOFF és saját új teljes/3actual/runtimeON mellett.

Az eredeti215176 zöld/39 piros-részleges; csak2.2 bizonyítéka frissült, más214 sor és minden státusz változatlan. A külön167136 kész/31 nyitott. Teljes öt év/PIT és projekt tovább nyitott; a 17Berlin munkacél nem készültségi garancia.

[Bizonyítékok és SHA-k](EVIDENCE.json) · [Friss teljes215](STATUS-215.json)
