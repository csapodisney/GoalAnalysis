# Egységes eredménytár – 2.1, SHADOW

Az új tároló az eredeti sportadatbázisban három hozzáadott táblát használ:
`historical_result_matches`, `historical_result_versions`,
`historical_result_links`. A meglévő archívum és a szelvényképzés történeti
adatútja változatlan. Nincs automatikus szolgáltatói lekérés vagy LIVE kapcsoló.

## Beolvasás és lekérdezés

```python
from datetime import UTC, datetime
from pathlib import Path
from goal_analysis.jobs.historical_results import backfill_existing_results
from goal_analysis.storage.db import Database
from goal_analysis.storage.historical_results import HistoricalResults

root = Path("C:/AI-Work/GoalAnalysis")
report = backfill_existing_results(root)  # meglévő raw/archívum, mentés, 0 API
store = HistoricalResults(Database(root / "data/goal_analysis.sqlite3"))
snapshot = store.view(as_of=datetime.now(UTC))
```

Az import nyers SHA-256-ot, archívumsor-hasht, forrást, scope-ot és időbélyeget
ellenőriz. Hibás API-sor karanténba kerül; érvényes testvérei megmaradnak. Hibás
válasz, útvonal vagy hash esetén az egész forrás kimarad. Az összes eredeti raw
fájl megmarad. Minden írás hozzáfűzés; az azonos forrásreceipt ismétlése nulla új
eredménysort hoz létre. A mentés SQLite backup API-val, a WAL-lal együtt készül.

## Identitás és duplikáció

- A natív szolgáltató + fixture ID tartós. A kanonikus fixture ID képzése az
  1.1-es namespace-kontraktust követi; a registry meglévő bindingjai nem változnak.
- Két forrás csak explicit ID-mappinggel vagy igazolt ligakapcsolat + azonos,
  sorrendhelyes csapatnevek + bizonyított, pontos UTC kezdés mellett kapcsolható.
  Nincs fuzzy vagy dátum–név találgatás.
- Az automatikus job a korábbi tíz ellenőrzött ligamappinget használja. Az 1.6-os
  új név/ország egyezési javaslatokat nem aktiválja.
- Több natív ID ugyanahhoz az egzakt eseménykulcshoz kétértelműség. A karanténban
  lévő score-verziók identitásai is beleszámítanak. Új kétértelműség a korábbi
  névalapú kapcsolatot is felfüggeszti az aktuális nézetben, a régi as-of nézetet
  megőrizve. A hiteles ID-mapping ettől független.
- Egy összekapcsolt komponens nem tartalmazhat két eltérő natív ID-t ugyanattól
  a szolgáltatótól. Kapcsolat külön bizonyítékkal visszavonható, törlés nélkül.
- A `representative_id` a komponens determinisztikus képviselője; új kapcsolatkor
  változhat. Tartós hivatkozásként a `member_ids` natív fixture ID-jait kell tárolni.
- Az `UNLINKED` sorokat nem szabad bizonyítottan különálló, többforrásos
  tanítóeseményként összeadni. A nézet elemszáma nem globális egyediségbizonyíték.

## Eredmény és időhatár

TheStats: kizárólag `score.regulation`; API-Football: `score.fulltime`.
`goals`, AET-végösszeg és büntetőpárbaj nem helyettesítheti ezeket. Hiányzó pár
`None/None`. Football-Data `FTHG/FTAG` külön
`SOURCE_FULL_TIME_UNVERIFIED` scope-ban marad; UTC kezdést nem következtetünk a
helyi időből. Dátum-only sor az azonos UTC nap végéig nem kerül az as-of nézetbe.

A források eredményeit és scope-jait megőrizzük. Összehasonlítható teljes pároknál
TheStats → API-Football → Football-Data a prioritás; eltérő eredmény mellett
`CONFLICT` jelzés marad, egyik adatot sem írjuk át. A scope-ok nem mosódnak össze.

Az `observed_at` a valódi forrásreceipt, az `ingested_at` a mostani tényleges
adatbázis-beérkezés. Mindkettőnek a lekérdezési időhatáron belül kell lennie.
Egy ma behozott régi eredmény tegnapi visszajátszásban nem látható. Ez kutatási
visszatöltés; önmagában nem teremt történeti point-in-time teljesítménybizonyítékot.

## Üzemeltetési határ

A teljes archívum egyszeri, kézi backfillje; nem napi loop-feladat. A publikus
`view()` teljes forrásrekordokat olvas memóriába. Nagy adathalmaznál ezt nem szabad
minden API-oldal után újrafuttatni; a következő, külön importfeladat a korlátozott
batch-es beérkezési utat használja. A napi szelvény és DAILY_223 ettől független.

Visszaállításkor az új SHADOW nézet használatát kell megszüntetni; sem a régi
archívumot, sem a rögzített szelvényeket nem kell visszaírni vagy törölni.
