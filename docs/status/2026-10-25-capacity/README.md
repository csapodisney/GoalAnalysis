# Arthur 8.7.N1 — LIVE és PAPER kapacitás: javítás validálás alatt

2026-10-25. **28 célzott teszt és Ruff sikeres; a saját5673 teljes teszt és három új numerikus replay fut, még nincs elfogadás vagy runtime-lezárás.** Daily és legacy OFF. Ez kizárólag allokáció utáni diagnosztika: nem változtat kiválasztást, súlyt, valószínűséget, profiljogot vagy mentett szelvényt. Az N2 lezárt állapota és korábbi bizonyítékai megmaradnak.

A READY DAILY ajánlásnézetének címkéje REVIEWED; ezért a régi, szó szerint READY-t számláló kimutatás a tényleges egy READY LIVE DAILY mellett nullát jelzett. A PAPER_AUDITION és LIVE ajánlatokat sem különítette el. Az új verzió a meglévő READY/MODEL_READY címkéket és a külön LIVE/PAPER jogosultságot olvassa. REVIEWED csak egy már létező READY LIVE ticket pontos profil-/ticket-/quote-azonosságával számít. Nem ad publikációs jogot, nem emeli READY állapotba a DRAFT-ot, és nem módosítja a régi árakat vagy snapshotokat.

A20–30 cél LIVE ajánlásokra számít, PAPER külön szerepel. A ténylegesen mentett READY LIVE ticketeknek saját számlálójuk van; az alternatív ajánlások nem új mentett szelvények. Hiányzó/ismeretlen/felfüggesztett scope, üres/duplikált profil vagy ellentmondásos ticket-híd nem töltheti a LIVE célt. A20 PAPER/0 LIVE negatív kontrollnál a LIVE hiány továbbra20. A 28 célzott esetben többek között eltérő quote/iroda/ár/sourcehash, hibás ticketállapot és duplikáció is elutasítva.

Négy tényleges régi riport olvasási SHADOW összevetése sikeres; az eredeti futások/szelvények/ledger változatlanok. Ezek az adott generálás pillanatára vonatkozó számlálási korrekciók, nem új szelvények, friss árak, új aktuális játszhatóság vagy teljesítményjavulás:

| Megőrzött run vége | Régi szó szerinti READY | Új LIVE ajánlat | Külön PAPER ajánlat | Mentett READY LIVE ticket |
|---|---:|---:|---:|---:|
| f20dd091b16b | 11 | 4 | 12 | 2 |
| 3472f0b6fb86 | 1 | 4 | 2 | 2 |
| 0bf89356c40d | 0 | 1 | 0 | 1 |
| f14ad05e1ce7 | 0 | 1 | 0 | 1 |

A f14ad05e1ce7 pillanatképben a megfelelő LIVE hiány19; az eredeti mentett riport20-as értékét nem írtuk át. Egy ellenőrző próba eleinte a fingerprint-boríték eltérő capture-időpontját is összehasonlította, ezért megállt. A hiba megőrzött; a javított összehasonlítás az összes forráshasht és ledger-sort pontosan ellenőrzi, a két eltérő megfigyelési időt nem kezeli adatváltozásként.

A forrásfreeze1212 fájl és3995 korábbi ledger-sor. Öt forrás-/tesztfájl változott, az eredeti pipeline teljes AST-je a végső kapacitásdiagnosztikán kívül azonos. A korábbi objective-tesztek összes eredeti állítása megmaradt; a minimális tesztadapter explicit LIVE címkét kapott. A SHADOW próba nulla provider-/LLM-kérést, modellillesztést vagy ledger-írást végzett. A teljes suite/replay véges és egyszeri; nincs automatikus retry vagy drága új generálás pusztán egy számlálóért.

Az N2 saját scope lezárt: egy READY DAILY3láb bizonyított játékos/xG/naptár felhasználással, [lezáró riport és korlátok](../2026-10-25-n2-closed/REPORT.md). A projekt, minden profil és a teljes eredeti lista továbbra nyitott. A8.7 szülő a kimutatott hiba miatt újranyílt, és a8.7.N1 alpont bekerült: a számozott166 fő-/alpontból135 kipipált/31 nyitott; az eltérő bontás számait nem adjuk össze a régi215-ös követelményaudittal160/55.

[Forrás- és SHADOW-checkpoint](../../validation/2026-10-25-capacity-shadow-checkpoint.json). Saját teljes elfogadás, runtime-projekció és lezáró checkpoint még nincs. Az eredeti határidő nem teljesült; nincs töltelék, garantált nyereség vagy készre jelentés.

## Megőrzött replay-eltérés és ellenőrzősegéd-javítás

Az első saját körben az első numerikus replay6/7 eredeti kontrollja sikeres volt, a numerikus modellek és a tanítási adatok egyeztek. Az új kapacitás/shortfall mezőket a régi teljesriport-összehasonlítás eltérésnek vette; a párhuzamos full36%-nál megállt, nincs teljes tesztelfogadás. Külön adapter mind a hét eredeti check/require állítását megtartja és egy nyolcadik, szigorú kapacitásellenőrzést ad: csak az eredeti változatlan referenciakártyákból pontosan újraszámolt metadata fogadható el, a többi riportmező továbbra teljesen egyező. Tíz ellenpróba igazolja az ár/szelvény/modell/source/egyébdiagnosztika vagy darabszám manipulációjának elutasítását. Az aranyreferenciák változatlanok.

A v2segéd kezdeti útvonalhibája importkor megállt, illesztés előtt; a bizonyíték megmaradt. A v3 csak e segédútvonalat javította, az alkalmazáskód változatlan. Indítás előtt tényleges replay- és workerimport, helyes root, eredeti DAILYguard,1212forrás/3995sor azonosság és tíz új saját ellenpróba sikeres. A saját v3full5673+három új numerikus replay fut; még nincs elfogadás vagy runtime. Nincs automatikus újrapróba, új sport-/LLM-hívás vagy forrás-/kapulazítás.
