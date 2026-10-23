# Időbélyegek és helyi elérhetőség — SHADOW

A `normalization/temporal.py` tiszta projekciója nem olvas órát, nem kér le
adatot, és nem írja át a forrásrekordot. A hiányzó időpontok `None` értékűek.

| Mező | Jelentés |
| --- | --- |
| `event_time` | Eredménynél igazolt UTC-kezdés; időjárásnál az előrejelzés/mérés célideje; hírnél ismert eseményidő. |
| `published_at` | Igazolt forrásközlés vagy időjárási modellkiadás; letöltésből nem következtetjük ki. |
| `observed_at` | Eredeti forrásátvétel; régi időjárásnál a megőrzött, külön jelölt felső időkorlát is lehet. |
| `ingested_at` | Csak a forrásban ténylegesen rögzített helyi beérkezési idő. Ez rögzített falióra, nem pontos tranzakció-commit. |
| `available_at` | A pontos forrásváltozat bizonyított helyi jelenlétének konzervatív felső időkorlátja. |

A `storage/temporal.py` új, append-only `temporal_receipts` táblája a már
commitált forrássor elolvasása után méri a tanúidőt. A nyers adatoknál előbb
ellenőrzi az archívumon belüli fájlutat, a bájtok SHA-256 hashét és a méretét.
A tanúidő nem helyettesíti az ismeretlen eredeti ingestiont vagy publikálást.
Új példányban is elutasítja a már tárolt tanúidő mögé visszaállított órát.

A `TemporalStore.records(namespace, as_of)` lekérdezés megköveteli a tanúidő,
a megfigyelési idő és az ismert publikálási/beérkezési idő cutoff előtti voltát.
Újraellenőrzi a forrássor teljes tartalmi hashét és a nyers fájlt. Megváltozott,
eltűnt vagy bizonyítatlan forrás nem örökölhet egy korábbi tanúsítást.
Ez a nézet nem tesz közös, bizonyítatlan mérkőzést két szolgáltatói rekordból.

Az előrejelzés célideje lehet jövőbeli, az eredménye és az újraelemzésé nem.
A Football-Data ismeretlen időzónájú helyi órája külön marad; nincs kitalált UTC.
A hírek tanúsítása az adott import–claim–assertion kapcsolatra vonatkozik,
így egy újabb import nem vetíti vissza a későbbi átvételt egy régi hírre.
Később hozzáadott időjárási futáskapcsolat nem része ennek a bizonyítéknak.

Az offline, korlátozott `jobs/temporal.py` visszatérési értéke sorlimitet,
folytatási kurzorokat és külön elutasításokat tartalmaz. Külső API-hívás nincs.
A kurzor csak bejárási pozíció; nem időbizonyíték. A napi ütemező bekötése
a 2.8-as pont feladata, ebben a pontban nem módosult.

A régi források és lekérdezéseik változatlanok. A döntési útvonal nem vált át
erre az indexre; nincs visszamenőleges múltbeli elérhetőség vagy szintetikus
backtest. Az index a helyi jelenlétet igazolja, a szolgáltatói állítás
helyességét önmagában nem.
