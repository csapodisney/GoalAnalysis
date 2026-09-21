# DAILY_223 történeti adatréteg — Phase 19

## Elkészült

- API-Football lezárt meccsek gyűjtése, explicit liga–szezon párok alapján;
- közös, hatórás SQLite-cache, lekérésenként nyers pillanatfelvétel;
- 20 meccses történeti, 5 meccses aktuális és 10 meccses helyszíni profil;
- piacspecifikus bizonyíték és összekötés az önálló DAILY_223 konstruktorral;
- az elemzett meccsek és források azonosítói, mintaméretek és ismeretlen értékek;
- API-keret előzetes ellenőrzése; meglévő kimeneti fájl nem írható felül.

A meglévő öt blokk, valamint a v2/v2.1 master prompt szövege változatlan.
Az új ág nem használja a régi rövidlista döntéseit.

## API és időrend

A szolgáltató [hivatalos példája](https://www.api-football.com/news/post/how-to-get-all-fixtures-data-from-one-league)
szerinti `fixtures?league=...&season=...&status=FT-AET-PEN` lekérést használjuk,
UTC időzónával. Így egy liga összes csapata megoszthatja ugyanazt a történetet.
A szezon explicit paraméter, nincs minden ligára ráerőltetett júliusi évváltás.
A megadott liga–szezon párok a tényleges lefedettséget jelentik.

Először történetgyűjtés, utána friss meccs- és árpillanat, végül elemzés.
A történeti adat felvételi ideje nem lehet későbbi, mint a döntés `observed_at`
értéke. Egy ma letöltött eredményhalmazból nem készül igazolt tegnapi visszateszt.
A jövőből származó cache-bejegyzés nem használható korábbi döntéshez.

A `score.fulltime` és `score.halftime` külön mezők. Hosszabbításos vagy
tizenegyeses mérkőzésnél a 90 perces eredményt nem helyettesítjük a végső
összesített gólszámmal. Hiányzó félidei eredmény ismeretlen marad.
Hibás pontszám, eltérő liga/szezon, ellentmondó ismételt meccs vagy váratlan
lapozott válasz hibát ad, nem észrevétlenül csonkított történetet.

## Kiválasztási számítás

Csapatonként legfeljebb az előző 730 napot vizsgáljuk, a jelölt saját
sorozatában. Opcionális `home_history_since` / `away_history_since` időponttal
például dokumentált edzőváltásnál szűkíthető az ablak. Edzőváltást nem találunk ki.

- `historical_20`: utolsó 20 meccs, mindkét helyszín;
- `recent_5`: utolsó 5 meccs, mindkét helyszín;
- `venue_10`: a hazai csapat utolsó 10 hazai, a vendégcsapat utolsó 10 idegenbeli meccse.

Az ablakokat a meccsek időrendje alapján választjuk ki, nem csak a sikeres
vagy teljes adattal rendelkező meccseket gyűjtjük. A kiválasztott ablak hiányzó
periódusadatait a riport külön felsorolja. Mindkét oldalon legalább 3 használható
meccs szükséges a kapcsolódó bizonyíték képzéséhez.

Támogatott történeti piacok: `h2h` (`home`, `draw`, `away`), `btts` (`yes`, `no`),
illetve `totals_N_5` (`over`, `under`), például `totals_2_5`.
Periódus: `FULL_TIME` vagy `FIRST_HALF`. A történeti meccsben a csapat tényleges
oldalát vesszük figyelembe; hazai győzelem jelöltjénél a hazai csapat győzelmei
és a vendégcsapat vereségei a két megfelelő megfigyelés. Ezek nem független,
kalibrált esélybecslések. Más piacot ez a fázis nem helyettesít támogatott piaccal.

Történeti erősség: a két oldal megfigyelt piaci aránya közül a kisebb.
A megbízhatósági szorzó a kisebb mintaméret / 20, legfeljebb 1.
Helyszíni/aktuális erősség: oldalanként 60% `venue_10` + 40% `recent_5`,
majd a két oldal közül a kisebb. Megbízhatóság: a helyszíni minta / 10 és az
aktuális minta / 5 közül a legkisebb lefedettség. E heurisztikákból a master
30 és 25 pontos dimenziója kapja meg a hozzájárulást. A minták átfedhetnek;
a pontszám nem nyerési valószínűség vagy validált value.

A régi, kézzel megadott történeti/helyszíni érveket a gyűjtött pillanatból
számítottak váltják fel. A hiányzó sorozathelyzet-, keret- és motivációérv
továbbra is hiányzó marad; az érvényes, külön forrásolt kontextus megmarad.

## Terhelés: amit az adat ténylegesen megmutat

Az összes begyűjtött sorozatból számoljuk a célesemény előtti 7/14 nap ismert,
lezárt mérkőzéseit, és a legutóbbi megfigyelt kezdés és a célesemény kezdése
közti óraszámot. Ez nem tényleges pihenőidő és nem játékosonkénti játékperc.
A még be nem fejezett vagy nem begyűjtött meccsek miatt a lefedettség lehet
hiányos. A riport nem állít automatikus fáradtságot, utazási terhet vagy
motiválatlanságot, és nem ad ezekre automatikusan pozitív pontot.

## Futtatás

A helyi API-kulcs marad az `API_FOOTBALL_KEY` környezeti változóban.
Példa explicit szezonokra; csak a saját csomagoddal elérhető évadot használd:

```powershell
.\.venv\Scripts\python.exe scripts\collect-daily-223-history.py --league-season 78:2025 --league-season 78:2026 --max-api-calls 2 --output data\daily223-history-2026-09-21.json
```

Legfeljebb a megadott számú új API-hívás engedélyezett. A cache-találat nem
fogyaszt hívást. A `--max-api-calls 0` kizárólag már meglévő, érvényes cache-t
enged. API-tervkorlát esetén `DATA_BLOCKED` hiba lesz, nincs automatikus
előfizetésváltás vagy kerülőút. Hibás gyűjtés után nem marad késznek látszó üres
kimeneti fájl. A már elmentett nyers válasz és cache megmaradhat a hibakereséshez.

A Phase 18 jelölt-JSON mezői mellett minden jelöltnél négy explicit mező kell:
`api_football_fixture_id`, `api_football_home_team_id`,
`api_football_away_team_id`, `api_football_league_id`.
Ezek pozitív API-Football azonosítók; a program nem találja ki őket csapatnévből.
A bemenet előállítója felel a napi meccs és az odds-szolgáltató közti helyes
azonosságért. A történeti adat önmagában nem ellenőrzi ezt a napi párosítást.

```powershell
.\.venv\Scripts\python.exe scripts\run-daily-223-history.py --input inputs\daily223-candidates.json --history data\daily223-history-2026-09-21.json --output reports\daily223-2026-09-21.json
```

Kimenet: a Phase 18 riport, kiegészítve `history_analysis` részével.
Kilépési kód: 0 = teljes kutatási hármas; 2 = hiányos, de elmentett riport;
1 = feldolgozási vagy fájlhiba. A jelölt árát és időbélyegét nem frissítjük
önkényesen. Nincs automatikus fogadás, minden eredmény `RESEARCH_ONLY`.

## Ellenőrzés és hátralévő munka

A tesztek szintetikus adatot és hálózatmentes API-helyettesítőt használnak.
A valódi előfizetés/adatelérés ezzel nincs igazolva. A kulcsot nem kell chatbe
másolni; a Windows-gépen futó gyűjtő olvassa a saját környezetéből.

Hátravan a napi eseménykör és friss odds automatikus összerendelése, a
dokumentált hírek és kupa/UEFA-szituációk bekötése, valamint a kezdés előtti
frissítés és az önálló eredménynapló. A reggeli ChatGPT-feladat továbbra is
külön szolgáltatás, nem a helyi Python-parancs automatikus futtatása.
