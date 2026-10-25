# Arthur — N6 tesztelt forrás, az új publikáció még ellenőrzés alatt

Állapot: 2026-10-25 13:21 Europe/Berlin. **Ez köztes forrás-checkpoint, nem teljes projektzárás.** A 2026-10-24 08:00 határidő nem teljesült.

| Állapot | Bizonyított eredmény vagy valódi maradék |
|---|---|
| 🟩 | Az N6 végleges, rögzített forrásán 5590 teljes teszt sikeres; futásidő 44 perc 50 másodperc. A 44 figyelmeztetés nem teszthiba. |
| 🟩 | 241 célzott teszt és három ténylegesen újraszámolt numerikus modellreplay sikeres. |
| 🟩 | Mind az 1202 rögzített forrásfájl hash-e egyezik; a tesztelés alatt a 3967 korábbi ledger-sor és a megőrzött archívumok változatlanok. |
| 🟩 | A tényleges korábbi N5 inputokon a SHADOW összevetés változatlan teljes riportot és numerikus eredményt igazolt. A tervezett kiegészítő csomagok 28 és 22 eredeti ajánlatot tartalmaztak, az eredeti kereten belül. Ez nem új modellválasz. |
| 🟥 | Az új normál generálás elindult; a tényleges mentett új szelvények forrás → modellnek elküldött adat → valódi válasz → szelvény/ledger igazolása még nincs elfogadva. |
| 🟥 | Az N6 utáni működés-visszaállítás és saját lezáró checkpoint még nincs igazolva. A napi időzítő a validálás alatt OFF; legacyDaily223 OFF. |
| 🟥 | A teljes eredeti feladatlista nincs lezárva. A 215 tételes köztes összevetés 160 zöld/55 piros; ezt az N6 teszteredménye önmagában nem változtatja meg. |

Az N6 a profilok által létrehozott ajánlatot a pontos eredeti szolgáltatói ajánlathoz, ugyanazon befagyasztott kontextushoz és tényleges modellválaszhoz köti. Más ár vagy más bukméker válaszát nem viszi át. A hiányos értékelésű nem DAILY kártya nem válhat publikálhatóvá; a sikertelen kiegészítő értékelés nem írja felül a sikeres DAILY választ. A meglévő három follow-up kör megmarad, új sportadatgyűjtés és önkényes valószínűségi súly nincs. Az eredeti DAILY 2×2×3 matematika változatlan.

Bizonyíték: [hash-ekkel kötött teszteltforrás-checkpoint](../../validation/2026-10-25-n6-tested-source-checkpoint.json). [Teljes 215 tételes zöld/piros index](../2026-10-25-0915/STATUS-LISTA-215.md), [részletes korábbi követelmény-összevetés](../2026-10-25-0915/ARTHUR_GEMINI_COMPARISON_2026_10_24.md), [lezárt N5 publikáció](../2026-10-25-n5-closed/README.md).

Korlátok: a SHADOW nem rekonstruálta minden korábbi alternatíva teljes nyers kártyáját, és nem igazol pénzügyi vagy prediktív javulást. Egy változatlan, eredeti külső stadion-HTML fixture helyben marad, mert GitHub tokenmintát jelzett benne; tiszta klónból a teljes teszt ehhez a helyi fixture-höz kötött. Biztonságos nyilvános reprodukciója külön nyitott feladat. A régi eredeti adatokat és hashreferenciákat nem írtuk át.
