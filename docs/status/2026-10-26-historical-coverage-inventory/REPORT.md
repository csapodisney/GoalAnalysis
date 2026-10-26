# Történeti adatfedés – tényleges, csak olvasó leltár

Az eredeti **2.2 követelmény továbbra piros/részleges**: a jelenlegi naptól öt évre visszamenő teljes import nincs igazolva. A leltár saját szűk feladata kész; ez nem a teljes projekt lezárása. Az eredeti215 aktuális állapota **176 zöld / 39 piros-részleges**, a külön167 munkalista **136 kész / 31 nyitott**. A két lista nem összeadandó.

A saját RO tranzakció 2026-10-26T08:03:51.212138+00:00 és 2026-10-26T08:04:17.245124+00:00 között 122948 kanonikus eredményverzió 239420362 JSONbyte-ját olvasta, 26.031 másodperc alatt. A későbbi független SQL visszaolvasás ugyanazokat a szolgáltatónkénti darabszámokat adta. A SQLite `mode=ro`, `query_only`, `BEGIN` a committed WAL-adatot is látja; teljes13.8GB DB-hash vagy minden üzleti tábla újraauditálása nincs állítva.

## Kanonikus archívum

| Forrás | Saját forráson belüli ID | Verzió | Meccsdátum | UTC nélküli verzió |
|---|---:|---:|---|---:|
| api_football | 14006 | 29666 | 2025-01-02 – 2026-10-18 | 0 |
| football_data_co_uk | 78235 | 78235 | 2016-07-29 – 2026-10-14 | 78235 |
| the_stats_api | 14750 | 15047 | 2024-10-01 – 2026-10-21 | 0 |

Ezek külön források saját ID-i, nem források között deduplikált egyedi globális meccsszámok vagy igazolt időhelyes tanítóminták. A CSV-archívum régebbi dátumai nem bizonyítanak ötéves TheStats-fedést. A CSV mind78235verziója `SOURCE_FULL_TIME_UNVERIFIED`, UTC kickoffjuk hiányzik; ebből nem gyártunk UTC-t vagy igazolt 90perces eredményt. A jelenlegi gördülő 2021-10-26..2026-10-25 ablakba38183CSV-ID esik, TheStatsból14750. A régi élő szolgáltatókat nem kapcsoljuk vissza.

## TheStats liga szerinti fizikai állomány

| Eredeti natív comp ID | ID | Verzió | Dátumtartomány |
|---|---:|---:|---|
| comp_0196 | 1150 | 1173 | 2024-10-01 – 2026-10-21 |
| comp_0256 | 628 | 638 | 2024-10-04 – 2026-10-18 |
| comp_0406 | 625 | 631 | 2024-10-04 – 2026-10-18 |
| comp_0976 | 969 | 981 | 2024-10-04 – 2026-10-19 |
| comp_2837 | 780 | 786 | 2024-10-04 – 2026-10-18 |
| comp_2977 | 790 | 823 | 2024-10-04 – 2026-10-19 |
| comp_302263 | 573 | 585 | 2025-01-24 – 2026-10-20 |
| comp_3039 | 770 | 780 | 2024-10-05 – 2026-10-19 |
| comp_3809 | 662 | 685 | 2024-10-04 – 2026-10-18 |
| comp_4023 | 1159 | 1183 | 2024-10-01 – 2026-10-20 |
| comp_451101 | 612 | 626 | 2025-01-24 – 2026-10-19 |
| comp_4643 | 625 | 645 | 2024-10-04 – 2026-10-18 |
| comp_5450 | 739 | 745 | 2024-11-02 – 2026-10-18 |
| comp_5840 | 770 | 782 | 2024-10-04 – 2026-10-19 |
| comp_615520 | 593 | 607 | 2025-01-25 – 2026-10-19 |
| comp_8321 | 1162 | 1200 | 2024-10-01 – 2026-10-20 |
| comp_8338 | 166 | 168 | 2025-04-05 – 2026-10-19 |
| comp_8814 | 769 | 780 | 2024-10-04 – 2026-10-21 |
| comp_9766 | 574 | 593 | 2024-10-05 – 2026-10-18 |
| comp_9777 | 634 | 636 | 2024-10-04 – 2026-10-19 |

A húsz versenysor jelenléte és az első/utolsó dátum nem tanúsít minden köztes meccsre teljes szolgáltatói fedést. Az összes15047natív kanonikus verzió rekord-/azonossághashét ellenőriztük. A hozzájuk hivatkozott418külön TheStats rawtest **11148130byte** tartalmát egy független fizikai olvasás pontos SHA/byte egyezéssel igazolta. Ez az archivált testek sértetlensége; nem helyettesíti a fogyasztó pontos URL/HTTP-tanú/fixture/PIT vizsgálatát.

## Rendelkezésre állási bizonylatok és fogyasztói korlát

15047natív verzió közül **13803verzióhoz nincs `result` availabilityreceipt**,1244-hez van. A legutolsó, nem azonos idejű döntetlen verziócsoportokat külön számlálva **13607natív meccsID** hiányos. A változatlan `weather_native_dataset._results` reader ténylegesen **1143eredményt** fogad el; további13607-et a hiányzó bizonylat miatt elutasít. Mind1143elfogadott eredmény félideje UNKNOWN. A különböző nevezők miatt13803verzió és13607meccs nem ellentmondás.

Ez a weather-referencia fogyasztó diagnózisa. A numerikus `native_history_shadow.native_history_view` másik, raw/HTTP-witnesshez kötött forrásutat használ; nincs bizonyítva, hogy a kanonikus resultreceipt pótlása önmagában bármely numerikus modell mintaszámát növeli. Letöltött múltbeli eredmény ma használható lehet egy mai kutatáshoz, de a múltbeli prematch feature-ismeretet és a globális30napos PIT-fedést nem igazolja.

Következő egyetlen feladat: a meglévő kanonikus forrás/raw/HTTP és a hiányzó első availability pontos auditja, új hálózati kérés és írás előtt. Ha később külön bounded receipt-only művelet indokolt, az új `available_at` csak a tényleges akkori committed olvasás utáni **SOURCE_PRESENCE_UPPER_BOUND** lehet; nem régi publikáció/ingestion/quote-idő. Semmilyen eredeti óra, rekord, receipt vagy hiány nem írható át/visszadátumozható.

## A meglévő ötéves terv tényleges előrehaladása

Az immutable `0a6c448137e20951e4f4b513c33a0758f4075ba433954799b73e4df5a4f5474b` terv20comp,2021-10-18..2026-10-17,1220verseny/hónap querywindow. A hashkötött checkpoint **496**, page1, total_pagesUNKNOWN; **496mentett oldalmanifest** igazolt, **724querywindow hátra**. Az utolsó mentett oldal comp_5840/2024október, valódi receipt2026-10-23T07:41:18.624151Z. A jelenlegi gördülő időablakhoz friss napi delta is szükséges. A724nem724biztos fizetős GET és nem befejezési időígéret: cache/pagináció számít.

A meglévő backgroundbeállítás20oldal/55másodperc/1800másodperc intervallum, napi cap300; minden kvóta-, tartalék- és frissességkapu változatlan. Az utolsó mentett háttérállapot2026-10-23T07:41, `REQUEST_LIMIT`; az akkori quota és Oct23dailygate nem mai szabad keret/aktuális hálózati felhatalmazás. A régi progress242/1220pillanatképet e friss496/1220leltár pontosítja.

## Megőrzés, ellenőrzés és határ

1229source/config/test/script/goldbytehash változatlan; mind4016régi ledger-sor megőrzött, az audit alatt0új sor. A496oldal, terv és checkpoint egyezését a leltár után is visszaellenőriztük. DailyON/legacyOFF maradt; nincs új import, API/LLM, receiptírás, modellfit vagy normálgenerálás. A már elfogadott5851full/3actualreplay az előző UI-scope eredménye, ezt az adatleltár nem futtatta újra és nem saját új modellvalidációként számolja.

N9STOPPED marad:4GET/3download/**0újstrictpár**; nincs új weatherclaim, hibás provenanceátírás, hamisFETCHED_AT vagy readerkapu-lazítás. A teljes eredeti215auditot nem indítottuk újra: csak a2.2bizonyíték és hiányleírás frissült, státusza piros, a másik214sor bitazonos. A projekt és a tényleges adat-/kalibrációs-/specifikációs maradékok nyitottak.

Gépi bizonyíték: [EVIDENCE-INVENTORY.json](EVIDENCE-INVENTORY.json). Teljes aktuális215státusz: [STATUS-215.json](STATUS-215.json). A kanonikus stream SHA és418raw-hash a gépi proofban; az eredeti archivált inputokat nem publikáljuk újra.
