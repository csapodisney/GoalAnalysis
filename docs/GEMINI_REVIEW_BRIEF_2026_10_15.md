# Arthur / Goal Analysis — rövid átadó Gemini-felülvizsgálathoz

**Dátum:** 2026. október 15. Az alábbi állapot a legutóbb ellenőrzött, 18:03–18:07 közötti berlini futásra vonatkozik.

## A felülvizsgálat célja

A felhasználó nem ugyanannak a három mérkőzésnek harmincféle újracsomagolását szeretné, hanem külön-külön értelmes, jól alátámasztott szelvényeket. **A szelvények között lehet közös mérkőzés.** A legutóbbi módosítás ezt túl szigorúan tiltotta, és indokolatlanul beszűkítette a kínálatot. Kérünk kritikai véleményt és arányosabb szabályjavaslatot; ez az átadó még nem új megvalósítás.

## Mi a projekt?

Az Arthur helyi Windows/Python futball-elemző és szelvényajánló rendszer, magyar dashboarddal, naplózott napi futásokkal és privát telefonos eléréssel. Nem köt automatikusan fogadásokat. A ténylegesen megjátszott szelvényeket, a kutatási ajánlatokat és a rögzített napi csomagokat külön kezeli; a korábbi állapotok megmaradnak.

Mérkőzés-, történeti és oddsadatokat az API-Football/API-Sports és a The Odds API szolgáltat; kiegészítő időjárási adatot az Open-Meteo. Az Astra nyelvi modell forrásos webes ellenőrzést és értékelést végez, de nem helyettesíti a numerikus modelleket vagy a valós árakat.

**Harminc profil van, nem harminc független modell:**

- 1 DAILY_223, azaz a védett 2×2×3 konstrukció.
- 6 specialista: gólok felett/alatt, mindkét csapat szerez gólt, hazai/vendég győzelem, döntetlen, első félidei gól.
- 3 numerikus profil: GAUSS — kétváltozós Poisson; KEPLER — Pi-rating és CatBoost; LAPLACE — kísérleti, független Poisson-alapmodell.
- 20 fiktív klubkarakter, eltérő preferenciákkal, de közös adatokra és modellekre támaszkodva; alapvetően próba/kutatási státuszban.

## Szelvényképzés és megőrzendő feltételek

**A 2×2×3 módszere változatlan maradjon:** három külön mérkőzés, két legalább 2-es és egy legalább 3-as egyedi szorzó szerepével, a meglévő szabályokkal és toleranciával, egységes fogadóiroda/régió/pénznem mellett. A friss napi adatok természetesen más választásokat eredményezhetnek. Azonos bemeneten a módosítás előtti és utáni DAILY-eredmény egyezését ellenőriztük.

A többi profilnál már nincs kötelező 10-es összszorzó vagy szorzónövelés céljából hozzáadott választás. Valós ár, helyes meccs/piacazonosítás, megfelelő történeti adatok és minőségi kapuk továbbra is szükségesek. A numerikus becslések és a specialisták heurisztikus pontszámai nem azonos mérőszámok; nem szabad őket közvetlenül összeadni.

A kiemelt főszelvények konfigurált célszáma 2, felső korlátja 5. **Ez különbözik a profilok által kínált összes kutatási és alternatív ajánlat számától.** Harminc profil nem jelent harminc kötelező fogadást.

## Mi változott, és hol lett túlkorrekció?

1. **Eredeti probléma:** sok profil ugyanarra a néhány meccsre épített, miközben a közös adatforrások és a hiányos modell-lefedettség korlátozták a valódi változatosságot.
2. **A 10-es minimum eltörlése után:** a rangsor először a leggyengébb, majd az átlagos alátámasztást, végül a rövidebb szelvényt preferálta. Így a legerősebb egymeccses ajánlat dominálta a kombinációkat. Ez megvalósítási következmény, nem a felhasználó kérése minden profil egymeccsessé alakítására. Tizennyolc klubajánlat ugyanarra az FTC–Plzeň meccsre koncentrálódott.
3. **A legutóbbi átfedésszűrés:** nem DAILY profilok között meccsenként csak egy publikációt enged, eltérő piac esetén is. A DAILY-val egy éles főszelvény oszthat meg egy meccset; a kutatási profilok nem. Az alternatívának meg kell őriznie saját eredeti legjobb pontszáma 90%-át. **Ezek választott fejlesztői szabályok, nem kifejezett felhasználói előírások.** A 90% heurisztikus pontszámarány, nem nyerési valószínűség.

| Legutóbbi két ellenőrzött futás | Reggeli | Délutáni |
|---|---:|---:|
| Választásokat tartalmazó profilajánlat | 27 | 2 |
| Ebből klubajánlat | 18 | 0 |
| READY főszelvény | 2 | 2 |
| Különböző mérkőzés az ajánlatokban | 6 | 3 |

A délutáni futásban 27 profil tartózkodott, a KEPLER történeti adathiányos volt. A két megmaradt ajánlatot az 5. csomagverzió rögzíti. A technikai tesztek sikeresek voltak; **ettől az átfedési szabály üzletileg még lehet rosszul megválasztott.**

## A kívánt középút és a nyitott kérdések

Minden szelvényt önálló összeállításként kell értékelni. Például **A+B+C és A+D+E** lehet elfogadható; **A+B+C sokszori átnevezése** kevés új értéket ad. Ettől a közös meccseket tartalmazó szelvények eredménye statisztikailag még összefügghet.

Kérjük a Gemini véleményét:

1. Hogyan különítsük el a teljes/közel teljes szelvényismétlést, az azonos meccs–piac–kimenetel ismétlését és a pusztán közös mérkőzést? Milyen fokozatos korlát vagy büntetés indokolt a teljes tiltás helyett?
2. Milyen szelvényszintű rangsor enged értelmes többmeccses kombinációkat, kényszerű szorzónövelés és az egymeccses ajánlatok automatikus dominanciája nélkül?
3. Hogyan mérjük a napi kínálat koncentrációját és az összefüggő kockázatokat úgy, hogy egy erős meccs indokolt többszöri szereplése megmaradhasson?
4. Mikor indokolt tartózkodni, és mikor túl szigorú a szűrés? Kérünk indokolt szabályváltozatokat és teszteseteket, új önkényes darabszám vagy pontszámküszöb helyett.

**Korlát:** a legutóbbi webes vizsgálatban 18 háttértémából 3 forrásolt, 3 ideiglenes és 12 ismeretlen maradt; a modell-lefedettség is szűk. Nincs bizonyított kalibráció vagy nyereségesség. A cél több valóban értelmes ajánlat, nem a harminc profil mindenáron feltöltése. Kérjük a javaslatokat a 2×2×3 módosítása nélkül, a szükséges adatjavításokat és a kiválasztási szabály változtatásait külön indokolva.
