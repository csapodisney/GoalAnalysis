# Odds-pillanatképek — SHADOW archívum

A `normalization/odds_snapshots.py` a már letöltött API-Football, The Odds API,
TheStats és mentett árfigyelő-válaszokat dolgozza fel. A
`normalization/football_data_odds.py` a meglévő historikus CSV-archívumból
képez áradatokat. Nincs új lekérés, modellhívás vagy módosított szelvényszabály.

Minden árhoz megmarad a szolgáltatói mérkőzés-, bookmaker-, piac- és
kimenetelazonosság; a periódus, elszámolás, Decimal-vonal, árjelleg, régió és
pénznem külön mező. Ismeretlen mező nem kap kitalált értéket. Az azonos nevű
irodák külön szolgáltatónál nem válnak automatikusan azonos termékké.
Az ismeretlen piac is megőrizhető natív néven, `UNSUPPORTED` jelöléssel.
Ez nem READY-kizárási szabály és nem egy új piac éles engedélyezése.
Az ilyen piac számmal jelölt kimenetele külön JSON-számtípussal marad meg;
a numerikus `0` és a szöveges `"0"` külön termék. A nem üres, szóközzel
kezdődő/végződő natív kimenetelszöveg pontosan megmarad. Ez nem lazítja
a mérkőzés-, iroda- és piacazonosítók ellenőrzését.

Az `OPENING`, `LAST_SEEN`, `CURRENT`, `PRE_CLOSING`, `CLOSING` árak külön
típusok. A TheStats tárolt nyitó-/legutóbb látott ára nem igazol önmagában
24–48 órás mozgást; ezt a szolgáltató [historikus odds leírása](https://www.thestatsapi.com/odds-api/historical-football-odds)
is tárolt opening/last-seen adatokként mutatja be. A CSV pre-closing mezője
nem automatikusan nyitóár. A CSV átlag- és maximumérték `AGGREGATE`, nem
konkrét iroda végrehajtható ajánlata; a handicapvonal és irodaszám nem ár.

A letöltési idő sosem helyettesít árfrissítési időpontot. A hiányzó, hibás
vagy jövőbeli áridő külön státuszú, az eredeti a nyers bizonyítékban marad.
A The Odds API piac saját frissítési időpontja megelőzi az általános bookmaker-
idő használatát. A mentett monitorból natív mérkőzésazonosság csak pontos,
ismert szolgáltatói URL-ből származhat; egyébként `UNKNOWN_NATIVE_ID` marad.

A `storage/odds_snapshots.py` négy bővítő, append-only táblát vezet:

- `sports_odds_sources`: eredeti forrás-hash, publikus URL, forráshivatkozás,
  átvételi idő és a helyi ingestion megkezdésekor rögzített idő;
- `sports_odds_products`: szolgáltatóhoz kötött termékleírás;
- `sports_odds_quotes`: pontos decimális ár, árjelleg, időbizonyíték és natív
  forrásrészlet/pozíció;
- `sports_odds_publications`: a teljes forrás lezárása, sorainak száma és a
  már commitált adatok elolvasása után mért `sealed_at` tanúidő.

A nagy forrás legfeljebb 1000 áras rövid tranzakciókban épül. A részleges
betöltést a publikus `quotes()` és `summary()` nem mutatja kész adatnak.
Külön commit utáni lezárás szükséges; az időbeli lekérdezés a `sealed_at`
határt is megköveteli. A már lezárt forráshoz új ár nem fűzhető hozzá,
csak az egzakt ismétlés fogadható el. Az egyidejű ellentmondó árak külön
sorok maradnak; az archívum nem választ belőlük önkényes „aktuális” árat.

A `jobs/odds_archive.py` a nyers fájl helyét, SHA-256 hashét és méretét,
a CSV-archívum sorhashét/scope-ját/darabszámát és a monitor eredeti
snapshot-hashét ellenőrzi. A folytatási index csak azonos bemeneti manifesttel
használható. A parser forrás-hashe a feldolgozási változat része; a korábbi
változat megmarad. Hibás forrás nem kap új lezárást. Sikertelen köteg után
a részleges, rejtett betöltés ugyanazon bizonyítékból idempotensen folytatható.

A kész archívum olvasási példa:

```python
from pathlib import Path
from goal_analysis.storage.db import Database
from goal_analysis.storage.odds_snapshots import OddsSnapshots

archive = OddsSnapshots(Database(Path("data/goal_analysis.sqlite3")))
quotes = archive.quotes(
    as_of="2026-10-21T12:00:00+00:00",
    provider="api_football",
    fixture_id="123",  # valódi, natív ID szükséges
    limit=100,
)
```

Az archívumnézet nem odds-eligibility, frissességi vagy legjobbár-resolver.
Több parser-verzió tudatosan több archív feldolgozást jelenthet ugyanarról
a nyers válaszról; ezek nem új piaci megfigyelések. Éles TheStats-prioritás,
új piac használata és ármozgásos döntés csak a későbbi roadmap-ellenőrzések
után kerülhet erre az adatra. A DAILY_223 útvonala változatlan.
