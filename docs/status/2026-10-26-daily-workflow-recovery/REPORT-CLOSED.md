# Arthur — meglévő normál futás egyszeri workflow-helyreállítása

A saját fejlesztési kör és az egyszeri művelet ellenőrzése lezárható; a GitHub távoli commitjának igazolása külön záró lépés. A teljes projekt és az adatfedési követelmények nyitottak.

## Eredmény és határ

Az eredeti `2026-10-26-cc3b72cd5b81` normál futás egyszer került be a napi workflow-ba. Új normál generálás nem történt. Az eredeti szelvények, árak, értékelések, válaszok, azonosítók, generálási idő és producer-verzió változatlanok. A helyreállítás és a rögzítés új, tényleges műveleti időt kapott; ez nem új ajánlat vagy friss fogadhatósági állítás.

| Lépés | Tényleges eredmény |
|---|---|
| Eredményfrissítés | PARTIAL: 20 lekérés, 20 kapott eredmény; 74 mérkőzés maradt a sorban. |
| Generálási lépés | PARTIAL: a meglévő normál futás újrafelhasználása, 0 új generálás. |
| Archiválás | SUCCESS: tényleges rögzítés 2026-10-26T10:33:14.472134Z-kor, a kezdések és a 15 perces határ előtt. |
| Tanulás | SUCCESS: fizikailag visszaolvasott jelentés és hash; tényleges Astra-értékelés. A csapatértékelés NO_DATA, nem előléptetés. |
| Teljes workflow | COMPLETED_WITH_WARNINGS; a részleges eredményfrissítés és az eredeti részleges generálás megmaradt. |

A szükséges üzemi eredményfrissítés és tanulás tényleges művelet volt, a meglévő költségkorlátokkal. A nulla új normál generálás nem nulla összes API/LLM-hívást jelent. Időjárási claim és kézi történeti import nem indult; a történeti lépés a validált háttérfolyamatra halasztott.

## Megvalósítás és teszt

Öt forrás-/tesztfájl változott: új `jobs/nightly_recovery.py`; a meglévő `nightly.py` explicit helyreállítási határideje és checkpointvédelme; háttérfolyamati forráskötés; a CLI két pontos hash-t kérő `--recover-run` útja; 23 új teszt. A további 1226 régi fájl byteazonos. Az eredeti 22:00–06:00 ütemezés és a csak holnapra szóló `--run-now` szabály megmaradt. A külön helyreállítási ablak véges, legfeljebb egy óra és minden érintett kezdés előtt legalább 15 perccel lezárul. Nincs generálási fallback, konfliktusos job/freeze felülírás vagy előre beállított siker.

- Saját teljes teszt: **5874 PASS**, 44 figyelmeztetés, 2682.62 másodperc.
- Célzott tesztek: **96 PASS**; adapter: **10 PASS**; Ruff PASS.
- Három új, ténylegesen újraszámolt numerikus replay: **3 × 8 PASS**. A 97/33/1 DatasetStore-manifest és az adatok fizikai visszaolvasása is megtörtént.
- 1231 fagyasztott forrás, 4016 régi ledger-sor és 6894 korábbi archívum ellenőrzött. A régi sorok és fájlok változatlanok; a tényleges eredmény-, workflow-, rögzítési, tanulási és csapatadat-bővülések külön leltárban szerepelnek. Nincs teljes adatbázis-byteazonossági állítás.
- Az eredeti matematika, rangsor, súlyok, profilküszöbök, READY-kapu és aranyreferenciák változatlanok.

A háttérfolyamat és a tőle függő prospektív odds-aktiváció forráshash-e kizárólag saját teljes elfogadás után lett újraigazolva. A régi aktivációs byte-ok és az eredeti aktiválási idők megmaradtak; az újraigazolás valós mostani időt és saját bizonyítékot használ.

## Megőrzött hibák és külön ellenőrzés

Az eredeti szolgáltatás-követő **exit1** eredménnyel állt meg, miután az éles helyreállító CLI már **exit0**-val befejeződött. A követő csak olvasó SQLite-segédje tuple-sorokat adott a szöveges kulcsokat váró, változatlan freeze-olvasónak. Az eredeti log, helper, hash, intent és hiba megmaradt. A műveletet, aktiváció-újraigazolást és stage-eket nem futtattuk újra. Külön, csak olvasó `sqlite3.Row` adapteres egyeztetés valamennyi eredeti ellenőrzést teljesítette. Ezután az első runtime-visszaállítás külön exit0-val zárult. Az eredeti követőnek nem tulajdonítunk exit0-t.

Az első ROOT csak olvasó véglegesítő a régi N2 transport-névtér miatt a forrásellenőrző kapun megállt, Windows/HTTP-művelet előtt. Ez a guard helyesen utasította el a rossz bizonyítéknévteret. Az eredeti fájl és readback megmaradt; az új változat a saját fagyasztott forrásnévteret használta, azonos szigorú ellenőrzésekkel. Éles runtime- vagy helyreállítási ismétlés nem történt.

## Runtime és fennmaradó munka

Az elfogadott aktuális kóddal futó felület helyben és Tailscale-en az eredeti cc3b riportot és a jelenlegi JavaScriptet szolgálja ki. Az aktuális tanulási jelentés és csapatadat ellenőrzött. **Daily ON, legacy Daily223 OFF**. Az eredeti csomag továbbra is PARTIAL, 1 LIVE / 0 PAPER / 19 hiány; ez a művelet nem növelte a kapacitást. A háttérfolyamat publikációs kapuja AVAILABLE volt a ROOT visszaolvasáskor; ez nem kvóta- vagy teljes adatfedési igazolás.

Az eredeti 215 követelmény státusza **176 ZÖLD / 39 PIROS-RÉSZLEGES** maradt. Öt érintett sorban csak a forrás-/műveleti bizonyíték frissült; a többi 210 sor változatlan. A külön 167-es munkalista 136 kész / 31 nyitott, a két lista nem összeadandó. A teljes ötéves fedés, a hat időjárási rés, az empirikus kalibráció/előléptetés és a specifikációs ütközések nyitottak. Az eredeti N9-hibát és a korábbi receipt-író STOPPED eredményét nem értelmezzük át; új claim/batch-felhatalmazás nincs.

Következő egyetlen pont: a háttérfolyamat jelenlegi kvóta-/tartalék-/sorállapotának csak olvasó leltára. Ez nem új HTTP-batch vagy fizetős generálás engedélye.

Részletes adatok: [EVIDENCE-CLOSED.json](EVIDENCE-CLOSED.json), [teljes 215-ös riport](STATUS-215.md), [teljes státusz JSON](STATUS-215.json).
