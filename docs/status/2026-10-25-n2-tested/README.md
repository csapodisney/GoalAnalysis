# Arthur — N2 tesztelt forrás, új publikáció még függő

Állapot: 2026-10-25. **Ez köztes forrás-checkpoint, nem projektzárás.** Az eredeti 2026-10-24 08:00 Europe/Berlin határidő nem teljesült.

## Frissítés: a v2 új generálása elutasítva

A saját teljes teszt sikerét követő egyetlen normál futás, `2026-10-25-ec7edd1a2821`, 2026-10-25 15:43 UTC-kor `CONSTRUCTION_INCOMPLETE` állapottal végződött: **0 szelvény, 0 előnézet**. Az Astra válaszában legalább egy hivatkozás nem szerepelt az igazolt források pontos listájában; a helyi ellenőrzés elutasította az egész választ. A kutatásban négy befejezett keresés, 50 visszaadott és kilenc elfogadott webforrás volt; ez nem teszi automatikusan érvényessé a későbbi értékelés hivatkozásait. A Grok `NO_ELIGIBLE_FIXTURES` nem pozitív szelvényellenőrzés. Újrafuttatás nem indult.

A régi adapter ideiglenes nyers válaszát a hibakezelés nem őrizte meg. Ezért a konkrét hibás URL és az esetleges átirányítási/írásmódbeli eltérés nem rekonstruálható; nem állítunk bizonyítatlan URL-normalizálási hibát. A forráskaput nem lazítjuk. A javítás már a válasz szerkezetében az igazolt URL-ekre korlátozza mindhárom hivatkozási mezőt, és megőrzi a szigorú utóellenőrzést. Újabb elutasításnál titkos adatot vagy nyers választ nem tartalmazó ujjlenyomat segíti a pontos diagnózist.

Az új, külön `followup-v3` javítás **295 célzott tesztje sikeres**. Hatvan korábban elfogadott, tényleges N6 modellválasz változatlan elfogadása és minden meglévő hivatkozás engedélyezettsége külön, új modellhívás nélkül ellenőrzött. Ez nem új modellreplay vagy publikáció. A v3 saját teljes tesztje, három újraszámolt numerikus replaye, új normál publikációja és runtime-lezárása még szükséges; a lent következő 5617/201/3 eredmények kizárólag a v2 rögzített forrására vonatkoznak. Az összes régi hibabizonyíték és a sikertelen futás két új ledger-sora megmarad. A teljes projekt nincs kész.

A v3 négy valós megőrzött inputjának OFF/SHADOW/ACTIVE összehasonlítása is sikeres. A saját teljes 5627 teszt és három új numerikus replay külön véges folyamatban fut; elfogadásuk még nincs meg. A validáló folyamat nem indít publikációt. [Új javítás célzott teszt- és SHADOW-bizonyítéka, a még hiányzó elfogadásokkal](../../validation/2026-10-25-n2-citation-repair-checkpoint.json).

| Állapot | Bizonyított eredmény vagy valódi maradék |
|---|---|
| 🟩 | Saját végleges forráson 5617 teljes teszt, 201 célzott teszt és három ténylegesen újraszámolt numerikus modellreplay sikeres. |
| 🟩 | 27 új határteszt; mind az 1205 rögzített forráshash egyezik. A tesztelfogadás alatt a 3982 korábbi ledger-sor és a megőrzött archívumok változatlanok. |
| 🟩 | Négy valódi megőrzött inputon az OFF/SHADOW teljes primár- és alternatívakártyái változatlanok. Az ACTIVE opciók eredeti quote-jai, profilkapui, formája, cohortja és legfeljebb 512 opció/profil korlátja ellenőrzött. |
| 🟩 | Az október17-i valós input 14 primár+alternatíva keresési hívásában 54 további profil–meccs útvonal-megjelenés, a mintában nulla elveszett. Ez nem 54 új meccs vagy szelvény. |
| 🟩 | Az eredeti DAILY 2×2×3 matematika, numerikus becslések és rangsorfüggvény, profilküszöbök változatlanok. Az N6 pontos kontextus/válasz kapuja megmarad. |
| 🟥 | A megtartott natív N6 inputon nem volt keresési trigger vagy megfigyelt kapacitásbővülés. Ezt nem állítjuk új kapacitásnak. |
| 🟥 | Az új normál publikáció, tényleges mentett szelvényeken végzett N2 bizonyítás és saját runtime/lezárás még külön elfogadásra vár. |
| 🟥 | A teljes 8.6 szülő, minden profil és a teljes eredeti feladatlista nincs automatikusan kész. A köztes 215-ös mátrix 160 zöld/55 piros. |

Az első N2 teljes kör 5613 PASS/4 FAIL eredménnyel megállt, új generálás nélkül. A késleltetést szimuláló régi tesztadapter nem fogadta az új keresési mód keywordjét. A javítás csak a paraméter továbbítása: mind a 33 eredeti állítás és a modul többi szerkezete változatlan. A korábbi hibás XML/log megőrzött; ez az új, külön followup-v2 ellenőrzés eredménye, nem az első hiba átírása.

Az N1 keresése eddig csak SHADOW diagnosztikát adott. Az N2 explicit OFF/SHADOW/ACTIVE policyval az eredeti jogosult sorokból képzett új meccsútvonalakat a primár klubválasztóhoz és az allokátor alternatíváihoz is eljuttatja. Az eredeti legjobb megfelelő opció megmarad a korlátos poolban; a meglévő beam128/max512 keret nem nő. Nincs kapulazítás, új valószínűségi súly, töltelék vagy új LIVE profiljogosultság. Hibás policy már sportadat-/modellhívás előtt leáll; opcionális keresési hiba explicit jelzéssel a stabil alaputat tartja meg.

A megőrzöttinput-próba nulla új sportadat-/modellkérést és nulla új modellillesztést végzett. A nagyobb mintán mért OFF/SHADOW/ACTIVE futásidők a gépi bizonyítékban szerepelnek; megfigyelt mérnöki költségek, nem SLA. A korlátos keresés nem igazol globális optimumot vagy minden korábbi útvonal egyetemes megtartását. Húsz–harminc szelvény nem gyártható hiányos adatokból.

[Hash-ekkel kötött forrás- és tesztbizonyíték](../../validation/2026-10-25-n2-tested-source-checkpoint.json). [Legutóbbi lezárt N6 publikáció](../2026-10-25-n6-closed/README.md). [Teljes 215 tételes zöld/piros index](../2026-10-25-0915/STATUS-LISTA-215.md).

A napi időzítő a validálás alatt OFF, a legacyDaily223 OFF. A tiszta klón teljes tesztje továbbra egy helyben megőrzött eredeti stadion-HTML fixture-höz kötött, amelyben GitHub tokenmintát jelzett; biztonságos publikus reprodukciója külön nyitott feladat. Prediktív vagy pénzügyi javulást ez a checkpoint nem bizonyít.
