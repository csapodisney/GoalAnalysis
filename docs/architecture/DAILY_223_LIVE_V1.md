# DAILY_223 — napi események és szorzók, Phase 21

## Elkészült folyamat

Az önálló futtató összeköti a Phase 19 történeti gyűjtőjét az aznapi
API-Football-meccsekkel, a The Odds API friss áraival és a Phase 18
háromlábú konstruktorával. A régi öt szelvény döntései nem bemenetek.
A master promptok és a korábbi élő parancs működése változatlan.

1. Konfiguráció és lekérési keretek ellenőrzése.
2. Explicit liga–szezon történet, hatórás megosztott cache-sel.
3. Friss napi mérkőzéslista a berlini naptári napra.
4. Az adott sorozat alappiacainak árai az EU-régió elérhető irodáitól.
5. Egyértelmű csapat-, sorozat- és időpont-egyezés; opcionális extra piacok.
6. Önálló jelöltlista, számított történeti/formabizonyíték és 2×2×3 konstrukció.
7. JSON-adatcsomag és olvasható Markdown-riport, új futási könyvtárban.

Ez LLM-hívás nélküli alapkutatás. A három láb megléte nem jelenti a
friss kerethírek, motiváció vagy kupa/UEFA-versenyhelyzet teljes értékelését.
Az eredmény `RESEARCH_ONLY`; nincs automatikus fogadás.

## Azonosítás

Az API-Football saját ligaazonosítóját egy explicit The Odds API `soccer_...`
kulcshoz rendeljük. A csapatneveket Unicode NFKC, kis-/nagybetű és szóköz
normalizálás után hasonlítjuk össze. A hazai–vendég sorrendnek egyeznie kell,
a kezdési idő eltérése legfeljebb 60 másodperc. Mindkét forrás szerint aznap,
még a kezdés előtt kell lennünk; a football-státusz kizárólag `NS` lehet.

Eltérő névírás esetén a `team_aliases` térkép API-Football csapatazonosítóhoz
rendelhet ismert alternatív neveket. Nincs fuzzy névpárosítás vagy automatikus
hazai–vendég csere. Több azonos lehetséges esemény, ismétlődő azonosító vagy
egy odds-eseményhez illeszkedő több football-meccs nem hoz létre párosítást.
A riport megőrzi a párosításokat és az adathibákat.

## Piacok és árak

Az alapkérés `h2h` és `totals` piacokat használ. A szolgáltató az
[alap odds-végponton](https://the-odds-api.com/liveapi/guides/v4/#get-odds)
ezeket támogatja, az egyéb piacokhoz külön eseményenkénti lekérés szükséges.
A kód az [elérhető piaci kulcsok](https://the-odds-api.com/sports-odds-data/betting-markets.html)
közül opcionálisan `btts`, `btts_h1`, `totals_h1` és `h2h_3_way_h1` piacot kérhet.
Egy kulcs létezése nem garantálja az adott iroda/liganap lefedettségét.

A győztespiachoz mindkét csapat és a döntetlen kimenetele szükséges. Kétutas
győztes vagy továbbjutás nem címkézhető át 90 perces háromutas eredménnyé.
A gólpiacokból csak félgólos vonalat fogadunk, például 2,5; az egész/ázsiai
vonalakhoz ez a fázis nem talál ki elszámolást. Az `_h1` piac első félidős
bizonyítékot kap. A többi piac nincs automatikusan helyettesítve.

Az ár és a mérkőzéslista legfeljebb 5 perces lehet az értékeléskor.
Az ár időbélyege a piaci `last_update`, vagy az alapkérésben elérhető
irodaszintű `last_update`; nem a program letöltési ideje. Jövőbeli időbélyeg,
nem véges ár, ismétlődő piac vagy több aktuális ajánlat ugyanarra a termékre
nem eredményez kedvezőbb régi árra visszaesést.

Minden kész szelvény ajánlatai ugyanazon kiválasztott irodától származnak.
A program először a `preferred_bookmakers` sorrendjét vizsgálja; a beállított
`betano` név az olyan szolgáltatói kulcsokra is illeszkedik, mint a
`betano_uk`. Ha egy preferált iroda nem szerepel az EU-válaszban, vagy önmagában
nem ad három megfelelő meccset, a teljes konstrukciót biztosító többi iroda
közül a történeti bizonyítékpontok alapján választ. Nem kever különböző
irodáktól származó lábakat, és nem a magasabb ár alapján választ irodát.

A fiókrégió és a pénznem felhasználói beállítás, a feed ezeket nem igazolja. Az elszámolási
mező a piac konvencióját nevezi meg; az iroda saját szabályzatának és a
ténylegesen megköthető kombinált ajánlatnak az ellenőrzése még külön szükséges.
Az árak szorzata elméleti összszorzó.

## Egyszeri beállítás

A csomag `config/daily223-live.example.json` mintát ad. Másold
`config/daily223-live.json` névre, majd állítsd be:

- `odds_region`: a The Odds API régiója; a minta `eu`;
- `preferred_bookmakers`: irodakulcsok vagy kulcselőtagok prioritási sorrendje;
  a minta `betano`, de annak hiánya nem blokkolja a futást;
- `account_region` és `currency`: a saját fiókod köre;
- `leagues`: explicit aktuális szezon, történeti szezonok és szolgáltatói ligakulcs;
- `team_aliases`: csak ismert, ellenőrzött eltérő névírások;
- lekérési keretek, valamint szükség esetén az `extra_markets` lista.

A Phase 22 minta 18 versenysorozatot ad, 2025/2026 történettel: a Premier
League, Championship, Bundesliga, Bundesliga 2, La Liga, Serie A, Ligue 1,
Eredivisie és Primeira Liga mellett az FA Cup, EFL Cup, DFB-Pokal, Copa del
Rey, Coppa Italia, Coupe de France, valamint a három UEFA-klubsorozat szerepel.
A holland és portugál nemzeti kupához a használt odds-feed jelenlegi hivatalos
sportlistája nem ad külön kulcsot, ezért ezeket a konfiguráció nem címkézi át
más versenysorozattá. A hiányzó hozzáférést a program nem kerüli meg.

A személyes kulcsok kizárólag környezeti változók:
`API_FOOTBALL_KEY` és `THE_ODDS_API_KEY`. Ne írd őket a JSON-ba vagy Gitbe.

```powershell
.\.venv\Scripts\python.exe scripts\run-daily-223-live.py --config config\daily223-live.json --check-config
```

Ez hálózat nélkül ellenőrzi a beállításokat és a változók meglétét; nem
igazolja a hitelesítést, az API-előfizetés jogosultságát vagy az iroda kínálatát.
Kilépési kód: 0 = helyi beállítások megvannak; 2 = hiányzó/hibás beállítás.

## Élő futás

```powershell
.\.venv\Scripts\python.exe scripts\run-daily-223-live.py --config config\daily223-live.json --live
```

Ez tényleges API-hívásokat indít, a beállított kereteken belül. A football-
híváskeret a történeti cache-hiányokat és a napi meccslekéréseket együtt számolja.
Az odds-keret a kért piacok számából, egy kiválasztott régió mellett előzetesen
lefoglalt kredit. A tényleges szolgáltatói költséget külön naplózzuk.
Váratlanul magasabb költségnél a feed további kérései leállnak.

Opcionális extra piac elégtelen kerete vagy lekérési hibája mellett a már
ellenőrzött alappiacok megmaradnak. A riport külön jelzi a kért és ténylegesen
feldolgozott piacokat. Ha az extra válaszban megváltozik az esemény azonossága,
az érintett régi eseményt sem használjuk tovább. A 200 jelöltes konstruktorlimit
irodánként érvényes. Egy túl nagy irodai jelöltlista kimarad és adatproblémaként
jelenik meg; nincs észrevétlen lista-csonkítás vagy irodák közötti keverés.

## Kimenet és hibák

Minden új futás új könyvtárat kap a `reports/daily223/` alatt. Opcionálisan
`--output-dir` adható, de meglévő könyvtárat nem írhat felül.
Fájlok: konfiguráció, történeti pillanat, napi meccsek, odds-válaszok,
jelöltbemenet, `report.json` és `report.md`. A nyers API-válaszokat a közös
pillanatfelvétel-tár is őrzi; a forrás-URL-ekből kimarad a hitelesítési kulcs.

- `COMPLETE`: három kutatási láb elkészült, nem teljes szakértői jóváhagyás;
- `CONSTRUCTION_INCOMPLETE`: a feldolgozott kínálatból nem állt össze megfelelő hármas;
- `DATA_BLOCKED`: beállítási, hozzáférési, adat- vagy kerethiba; a szakasz és ok szerepel.

Az élő parancs kilépési kódja 0 teljes konstrukciónál, 2 hiányos/adatblokkolt
riportnál, 1 fájlműveleti hibánál. Hiányzó API-kulcs esetén is készül hiba-riport,
ha az új kimeneti könyvtár létrehozható. Korábbi futás nem íródik felül.

## Ellenőrzés és következő munka

209 hálózatmentes teszt sikeres a teljes projektben. A friss adatokat helyettesítő
tesztválaszokon az egész új lánc működik; valódi fiókkal még nincs igazolt éles
futás. A könyvtári szintű teszt nem helyettesíti a Windows-gépen történő próbát.

Hátravan a dokumentált csapathírek és kupa/UEFA-szituációk értékelése, a
kezdés előtti automatikus frissítés, a helyi napi ütemezés és az önálló
eredménynapló. A már beállított napi ChatGPT-kutatás külön működik;
ez a parancs önmagában nem ütemez feladatot a felhasználó gépére.
