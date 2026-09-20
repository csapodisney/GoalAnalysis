# DAILY_223 — önálló napi konstrukció, Phase 18

## Státusz és hatókör

Az Arthur Pentagram v2.1 új 16. szakasza önálló napi 2×2×3 blokkot ad hozzá.
A v2 eredeti fájlja, valamint az 1–15. szakasz szabályai változatlanok maradtak.
A v2.1 elsőbbségi megjegyzése csak az új blokk kivételeit rendezi.
A manifest állapota `versioned_not_activated_in_live_pipeline`: a prompt
ellenőrizhető és betölthető, de a régi élő parancsba még nincs aktiválva.

Implementált komponensek:

- `goal_analysis.engine.daily_223`: determinisztikus, önálló konstrukciókészítő;
- `scripts/build-daily-223.py`: azonosított JSON-adatpillanatból kutatási riport;
- tesztek a szorzókra, bizonyítéksúlyokra, függetlenségre és adatérvényességre;
- külön ChatGPT-feladat: „Önálló napi 2×2×3 szelvény”, napi kutatás
  2026-09-21-től, Europe/Berlin szerint reggel 08:00 körül.

A ChatGPT-feladat nem Windows Feladatütemező-bejegyzés. Nem futtatja a
felhasználó gépén ezt a szkriptet, nem olvassa annak API-kulcsait, és nem végez
automatikus fogadást. A reggeli feladat önmagában nem jelent későbbi,
kezdés előtti automatikus frissítést. A másik öt szelvény feladata nem változott.

## Konstrukció és döntési sorrend

Pontosan három külön esemény. Elsődleges minimumok: 2,00 / 2,00 / 3,00;
elméleti összszorzó legalább 12,00. A lábszám szorzószerep, nem kezdési sorrend.

A „minimálisan alatta” verziózott alapértelmezése legfeljebb 2% eltérés.
Csak szigorú konstrukció hiányában megengedett 1,96 / 1,96 / 2,94,
és ekkor is legalább 11,76 szorzat szükséges. A három külön minimum szorzata
önmagában nem elegendő. A tolerancia nullára is állítható, 2% fölé nem.

Azonos iroda, régió és pénznem szükséges. Az egyedi árak szorzata nem bizonyítja,
hogy az iroda ezt a kötést ténylegesen elfogadja; kombinált ajánlat-ellenőrzés
még szükséges. A konstrukció nem használ más szelvények döntéseit vagy foglalását.

Az árfeltételeket teljesítő hármasok közül a leggyengébb láb támogató pontját,
majd a három láb összpontját maximalizáljuk. Holtversenyben stabil azonosítósorrend
dönt. A nagyobb szorzó nem ad több bizonyítékpontot.

| Bizonyítékdimenzió | Induló súly |
| --- | ---: |
| `historical` — piacspecifikus történeti háttér | 30 |
| `venue_form` — hazai/vendég forma | 25 |
| `competition_context` — liga, kupa vagy UEFA helyzete | 20 |
| `load_and_squad` — terhelés, pihenő, utazás és keret | 15 |
| `documented_motivation` — dokumentált motiváció | 10 |

Dimenziónként a legerősebb `strength × reliability` kapja meg a dimenzió súlyát.
Az érvek puszta darabszáma nem növeli a pontot. A hiányzó dimenzió 0; történeti
és hazai/vendég alátámasztás nélkül nincs érvényes jelölt. A pontszám heurisztika,
nem kalibrált nyerési valószínűség vagy value-minősítés. A kód a beküldött
bizonyítékokat pontozza: a forrás tartalmi hitelességét nem ellenőrzi automatikusan.

## Bemeneti szerződés

A JSON gyökere: `schema_version: 1`, `date` (berlini célnap, YYYY-MM-DD),
`observed_at` (időzónás ISO-időpont), `candidates` (lista), opcionális `policy`.
A policy alapértékei: `tolerance: 0.02`, `quote_max_age_seconds: 300`,
`context_max_age_hours: 48`, `timezone_name: "Europe/Berlin"`.

| Jelölt mezői | Jelentés |
| --- | --- |
| `candidate_id`, `fixture_id` | Egyedi jelölt, illetve a szolgáltatók közt feloldott eseményazonosító |
| `home_team`, `away_team`, `competition` | Csapatok és sorozat |
| `competition_type` | `LEAGUE`, `CUP`, `UEFA` vagy `OTHER` |
| `kickoff`, `event_status` | Időzónás kezdés; csak `SCHEDULED`, még el nem kezdődött esemény |
| `market_key`, `selection_key`, `period`, `settlement` | Pontos piac/vonal, választás, periódus és elszámolás |
| `bookmaker`, `region`, `currency` | A megfigyelt ajánlat pontos köre |
| `decimal_price`, `quoted_at`, `quote_source_id`, `quote_available` | Valós szám > 1, időzónás időpont, visszakereshető forrás, valódi JSON `true` |
| `sensitivity_note` | Rövid, konkrét bizonytalanság vagy lényegi feltétel |
| `evidence` | Piacspecifikus bizonyítékok listája |

Egy bizonyíték kötelező mezői: `evidence_id`, `category` (a fenti öt egyikét
használva), `statement`, `claim_type` (`FACT` vagy `INFERENCE`), `source_id`,
`observed_at`, `strength` és `reliability` (mindkettő 0–1 közötti szám).
Az adott jelölthöz kapcsolás a beágyazással történik. A félidős jelölt mellé
a bemenet előállítójának félidős bizonyítékot kell gyűjtenie; a konstruktor ezt
szöveges állításból nem tudja szemantikailag ellenőrizni.

A `source_id` a forrásrendszerben visszakereshető azonosító vagy közvetlen URL;
nem lehet kitalált hivatkozás. A tesztek forrásai szándékosan szintetikusak,
nem használhatók napi ajánláshoz. A történeti és formaadatok mintavételi
ablakát, elemszámát a bemenet előállítója dokumentálja az állításban/forrásban.

Az aktuális kontextus 48 órán túli elemei nem kapnak pontot. Jövőbeli
bizonyíték/időbélyeg, hiányzó vagy elavult ár nem fogadható el. Egy termékhez
egy aktuális ajánlat legyen: több azonos termékrekord esetén nincs visszaesés
egy korábbi, esetleg már megszűnt árra. Legfeljebb 200 előelemzett jelölt
adható át egy hívásban; a konstruktor ennél több adatot hibával jelez,
nem csonkítja észrevétlenül.

## Futtatás és riport

```powershell
.\.venv\Scripts\python.exe scripts\verify-master-prompt.py --version arthur-pentagram-v2.1
.\.venv\Scripts\python.exe scripts\build-daily-223.py --input inputs\daily-223.json --output outputs\daily-223-report.json
```

Az `inputs\daily-223.json`-t valódi, azonosított adatból kell előállítani;
ez a fázis még nem szállít élő adatgyűjtőt. A szkript nem hív futball- vagy
odds-API-t. Az `observed_at` egy pillanatfelvétel értékelési ideje: történeti
replay esetén nem jelenti azt, hogy az ár most is élő.

Kimenet: konstrukcióállapot, három láb vagy a hiány pontos jelzése,
összszorzó és toleranciasáv, bizonyítékok és részpontok, a meglevő jelöltlista,
adatérvényességi hibák, következő kutatási lépések és kanonikus SHA-256.
Mindig `selection_status: RESEARCH_ONLY`, `betting_approved: false`,
`real_wager_placed: false`. A közös találati valószínűség nincs számszerűsítve.

Kilépési kód: 0 = teljes kutatási konstrukció, 2 = elmentett, de hiányos
konstrukcióriport, 1 = hibás bemenet vagy fájlművelet. A kimeneti fájl nem
írható felül; új megfigyeléshez új fájlnév szükséges.

Minden nap legyen keresés és riport. Három ellenőrizhető mérkőzés/adatsor
hiányát nem szabad kitalált meccsel, árral vagy motivációval kitölteni.

## Hátralévő integráció

1. Önálló napi adatgyűjtő: történeti és hazai/vendég mutatók, piacspecifikus
   bizonyítékok, sorozathelyzet, terhelés/keret és hivatkozott hírek.
2. Támogatott piacok bővítése, különösen félidős termékek és elszámolások.
3. Napi élő koordinátor, verziózott kezdés előtti frissítés és külön eredménynapló.
4. Tényleges kombinált ajánlat ellenőrzése, majd shadow megfigyelések értékelése.

A régi `run-end-to-end-shadow.py` még a korábbi `complete_shadow_run`
útvonalat hívja. A Phase 17 kontrollált koordinátorának megléte nem jelenti,
hogy ez az élő CLI már minden új kaput használ. Ennek migrációja külön munka,
és nem lehet DAILY_223 készültségeként elszámolni.
