# Arthur — a történeti adatok tényleges fogyasztói lefedettsége

Adatpillanat: 2026-10-26T11:13:23.439834+00:00; független visszaolvasás: 2026-10-26T11:21:28.291357+00:00. Csak olvasó leltár, modellillesztés és új rendelkezésreállási bizonylat nélkül.

## A letöltés, a tárolt verzió és az elfogadott modellbemenet külön szám

| Mért állomány | Tényleges érték / határ |
|---|---|
| TheStats kanonikus állomány | 15859 verzió / 15463 natív ID. Nem globális, szolgáltatók között egyesített mérkőzés- vagy PIT-tanítómintaszám. |
| Kanonikus verzió eredmény-availability receipt nélkül | 14103. |
| A szigorú időjárási eredményolvasó elfogadott natív ID-jei | 1655, ugyanazzal a tartalom- és lineage-hashsel, mint az új normál batch előtt. |
| Korábbi, 08:29:48.446228 UTC cutoff | Pontosan ugyanaz az 1143 eredmény és lineage. Új adat nem jelent meg visszamenőleg. |
| Elfogadott időjárási eredménypárok | 9 reanalysis, 0 prematch forecast, 0 mindkettő, 0 explicit félidei eredmény. |

Az első új normál batch 19 oldalához **271 kanonikus rekord** tartozik. Mind a 271 pontosan egyezik az eredeti fizikai nyers válasz megfelelő natív rekordjával, és a változatlan normalizálóval újra előáll. A cohort 271 külön natív ID-t tartalmaz; közülük a normál batch korábbi számlálója 179 új ID-t jelzett, a többi meglévő ID új verziója. Ez nem 271 új mérkőzés.

Viszont **mind a 19 nyers forrás és mind a 271 kanonikus verzió explicit availability-receiptje hiányzik**. A strict source reader 271 × UNWITNESSED_SOURCE eredményt ad. A fizikailag meglévő body és a kvótakérés hiteles HTTP200 receiptje önmagában nem helyettesíti ezt a két fogyasztói forrás-/rendelkezésreállási szerződést. Az audit nem hívott Temporal.capture-t, nem írt receiptet, és nem másolt régi observed_at / fetched_at / available_at órát.

A forráskód szerint a HistoricalResultSink → HistoricalResults.ingest a kanonikus verziót tárolja, de ezen az úton nincs automatikus Temporal-admission. Ez konkrét integrációs rés, nem új súlyképlet vagy új letöltés hiánya. A korábbi, egyszeri 7 raw + 512 result-admission műveletet nem futtattuk újra, és az sem ad felhatalmazást egy új kézi batch-re.

## A külön natív nyers SHADOW olvasó másik akadálya

Az eredeti cc3b futás mind a hét mentett fixture-jét használtuk, eredmény szerinti szűrés és új forrás nélkül. Öt target forrásazonossága igazolt; két targetnél a valódi nyers is_neutral és a mentett neutral_venue is ismeretlen. A szigorú olvasó nem alakítja ezt False értékké. Ez nem írja át az eredeti READY szelvényt: egy külön, döntési hatás nélküli kutatási adatút feltételeiről van szó.

Az összes fixture-t együtt olvasó hívás **LIMIT_REACHED**: 194 történeti forrásjelölt, 110 igazolt forrás és 10001 beolvasott sor után a meglévő ROW_LIMIT guard elutasítja az egész részleges történeti mintát. A visszaadott történet 0 sor, és az új 19 oldalból nincs befogadott raw source key. Ugyanez igaz az új batch előtti cutoffra is. Nem tekintjük pozitív kombinált modellbemenetnek.

Külön, csak diagnosztikai olvasás történt az öt pontos competition-csoporttal, változatlan forrás- és sorkorlátok mellett:

| Competition | Igazolt targetek | Történeti sor-előfordulás | Külön natív történeti ID |
|---|---:|---:|---:|
| comp_0256 | 1 | 2903 | 618 |
| comp_615520 | 0 | 0 | 0 |
| comp_8385 | 1 | 2648 | 620 |
| comp_8814 | 1 | 3902 | 755 |
| comp_9777 | 2 | 2107 | 620 |

A sor-előfordulás nem külön mérkőzésszám: a megőrzött, különböző forrásverziók ugyanazt a natív eseményt többször tartalmazhatják. A csoportonkénti eredmény nem egy új kombinált minta, nem előrejelzés vagy modellillesztés, és nem LIVE-előléptetés. A konfliktusos/ellentmondó/ismeretlen verziókat egy későbbi javítás sem dobhatja el kényelmi szűréssel.

## Normál háttérhaladás és megőrzés

A külön kontextusolvasás órája 2026-10-26T11:22:22.520772+00:00. A normál időzítő 11:12-kor újabb 19 oldalt feldolgozott; a friss delta **20/20**, a régi ötéves terv **514/1220** ablaknál tartott. Ez automatikus üzemi haladás, nem audit által indított API-batch. A még hátralévő 706 főablak nem garantált HTTP-hívásszám, és a teljes ötéves fedés továbbra sem kész.

1231 forrásfájl byteazonos, 4087 aktuális ledger-sor az audit előtt/után változatlan. A korábbi cutoff tartalmi és lineage-hash-ei pontosan egyeznek. Az N9 három hibás reanalysis-provenance sora továbbra is REANALYSIS_ACTUAL_EVENT_STADIUM_UNPROVEN elutasítást kap; a 9 régi elfogadott pár nem lett 12. UNKNOWN félidei eredményt, időjárást, venue-t vagy receiptórát nem pótoltunk.

Az első auditpróbát a normál job által foglalt RootRunLock szabályosan elutasította, még a baseline létrehozása előtt. A guard kimenete és a helper SHA megmaradt; a csak olvasó snapshot a meglévő normál folyamat befejezése után készült. Nem indítottunk új production jobot, régi műveletet vagy paid generálást.

Az auditban 0 API/LLM/modellfit/generálás/Temporal.capture és 0 üzletiadat-írás történt. Csak saját artifactokat írtunk és a kizáró RootRunLock runtime-byte-jait használtuk. **Daily ON, legacy Daily223 OFF** maradt.

Az eredeti 215 követelmény státusza **176 zöld / 39 piros-részleges**, csak a 2.2 bizonyítéka frissült; a többi 214 sor bitazonos. A külön 167-es lista 136 kész / 31 nyitott, nem összeadandó. A teljes ötéves/PIT-fedés és a modellpromóció nem igazolt.

Következő egyetlen pont: **native-history-row-budget-contract**. Előbb artifact-only kontraktus és retained SHADOW ellenpróba a forrásverziók/duplikációk és a globális sorkorlát kapcsolatára. Nincs automatikus budgetemelés, korai truncation, konfliktus-/UNKNOWN-elhagyás vagy előre pozitívnak minősített új minta. Az availability-receipt producer-rés külön nyitott marad; ez a leltár nem engedélyez új capture/import batch-et. Tényleges productionforrás-javítás csak indokolt minimális változással, előzetes RootRunLock maintenanceOFF-fal, saját teljes + három új actual replay elfogadással és igazolt runtime/DailyON-nal következhet.

[Bizonyítékkivonat](EVIDENCE-CLOSED.json) · [teljes aktuális 215-ös státusz](STATUS-215.json)
