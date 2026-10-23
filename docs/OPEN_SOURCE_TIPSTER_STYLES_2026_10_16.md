# Nyílt forrású Tipster klub-stílusok

Audit ideje: 2026-10-16. A klub nem futtat frissen letöltött GitHub-kódot. A
helyi Arthur-modellekhez adaptált módszertani szabályokat használ, és minden
forrás commitra, licencre és URL-re rögzítve szerepel a katalógusban.

## Elfogadott források

| Forrás | Rögzített commit | Licenc | Arthurban használt elv |
|---|---|---|---|
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | `6a51173454cba172f2e7b2d8374c1770f257b86a` | MIT | Poisson, Dixon–Coles, bivariáns Poisson, Pi-rating |
| [The Open Model](https://github.com/Hicruben/theopenmodel) | `a4f1239a2bc8d0550408a5f8ee1cebb2f1815138` | MIT | rating + gólmodell-konszenzus, időbélyegzett előrejelzés |
| [odds-calibration](https://github.com/vladapl21/odds-calibration) | `d147452283f50da35ad51983c4a2c9cfc96e5fbd` | MIT | teljes piac marzstalanítása, értékkapu, időrendi mérés |
| [footAI](https://github.com/pmatorras/footAI) | `661b85abf1d2cff2fd01d732d8529d2a67ec4664` | MIT | rating és friss forma külön nézőpontként |

A nyilvános BetX repót nem használtuk: a saját LICENSE fájlja szerint
proprietary/all rights reserved. A socceraction xT/VAEP módszerét sem kapcsoltuk
be, mert Arthur jelenlegi napi bemenetében nincs a szükséges eseményszintű adat.

## Aktív szelvényformák — javítva 2026-10-17

A v5 tévesen 11 profilt egyesre, nyolcat legfeljebb két meccsre és csak egyet
három meccsre korlátozott. A kiírt 7+ referencia-szorzók nem voltak tényleges
szelvényfeltételek. Ez a tulajdonos nagyobb kifizetésű, állandó tipsterlogikájú
kombinációkra vonatkozó kérésétől eltért.

A v6 minden klubprofilhoz állandó meccsszámot és összszorzót rendel. A kereső
csak a kész kombinációkon alkalmazza az alsó korlátot, így a még alacsonyabb
szorzójú részszelvények tovább bővíthetők. A korlát az alternatív szelvényekre
is érvényes; a portfólió kiosztó újra ellenőrzi a mindenkori katalógust.

| Tippster | Meccsek | Preferált meccsszám | Összszorzó |
|---|---:|---:|---:|
| Bástya Bence | 3–5 | 4 | 10–16 |
| Gólvadász Lili | 4–6 | 5 | 10–22 |
| Hazai Márk | 3–5 | 4 | 10–24 |
| Vándor Zóra | 3–5 | 3 | 12–35 |
| Remi Doma | 3–4 | 3 | 18–50 |
| Csendes Mira | 3–5 | 4 | 10–18 |
| Dupla Áron | 4–6 | 5 | 10–24 |
| Archív Nóra | 3–6 | 4 | 10–22 |
| Forma Soma | 3–5 | 4 | 10–26 |
| Konszenzus Vera | 3–5 | 4 | 10–20 |
| Érték Milán | 3–5 | 4 | 12–35 |
| Rövid Lotti | 2–3 | 3 | 10–14 |
| Fűző Oszkár | 4–6 | 5 | 18–45 |
| Színes Réka | 4–5 | 4 | 12–30 |
| Fék Tádé | 4–6 | 5 | 10–14 |
| Góltérkép Emese | 3–6 | 4 | 10–26 |
| Merész Hunor | 3–5 | 4 | 25–50 |
| Pajzs Dorka | 3–6 | 4 | 10–20 |
| Roham Levi | 4–6 | 5 | 15–40 |
| Stabil Irma | 3–5 | 4 | 10–18 |

A piaci specializációk és az egyedi modell-/értékkapuk a v5-höz képest nem
lazultak. Lili kizárólag Over 2,5, Márk hazai, Zóra vendég, Áron BTTS Yes,
Mira Under/BTTS No tippeket használ. Minden láb pozitív becsült modellértéket
igényel. Ha nincs megfelelő kombináció, a profil tartózkodik; nem ad ki a
saját céljától eltérő egyes szelvényt. Egy konkrét, forrással igazolt Astra
HIGH_ODDS kivétel továbbra is csak az adott egyetlen magas szorzójú választást
engedélyezheti, más kaput nem kerülhet meg.

A szorzótartományok és a kombinációs formák **helyi Arthur-tervezési döntések**,
nem a GitHub-források által bizonyított nyereséges stratégiák. A nyílt források
a modellezési és értékvizsgálati elveket támasztják alá.

Azonos szelvény nem ismételhető; nagy meccsátfedés lágy pontszámbüntetést kap.
Emellett a klubos kiosztás a már sokszor használt egyedi mérkőzést is lágyan
hátrébb sorolja: a pontszám szorzója `1 / (1 + 0.12 × max_használat +
0.05 × átlagos_használat)`. Ez a végső kiosztási sorrend része; nem változtatja
meg az egyedi modellértéket, a profil alakját vagy a DAILY_223 választását.
A szorzó emelkedése nem bizonyítja, hogy az egész kombináció várható értéke
pozitív: az egyedi modellek nem kalibráltak és az események összefügghetnek.

A DAILY_223 kódja, szabályai és a mai elmentett teljes szelvénye változatlan.
A klubos újraösszeállítás a korábbi forrásidőket megőrzi és új rögzítési
revíziót készít, így az előző csomag visszakereshető. A Nemotron nem kap új
kérést ebben a javításban.
