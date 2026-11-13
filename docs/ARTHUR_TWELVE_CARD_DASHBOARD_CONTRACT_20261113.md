# Arthur: tíz agent, Daily 11., legacy 12.

A 2026-11-13-i közvetlen emberi pontosítás a jelenlegi szerződés: „daily es a legacy lesznek a 11 es 12. szelveny”. Ez a korábbi tízes design kiegészítése, nem kész termék vagy éles működés igazolása.

## Főnézet és előállítás

1. Az első tíz hely a tíz meglévő agenté. Mindegyik a teljes napi szolgáltatói naptárból, saját választási úttal, az összes bekötött adatcsaládhoz és a két meglévő szimulátorhoz hozzáférve dolgozik.
2. A 11. hely a meglévő portfolio Daily, a 12. az eredeti legacy DAILY_223 terméke. Két meglévő konstrukciós út, közös 2×2×3 maggal; nem két új agent vagy két bizonyítottan független stratégia. Azonos kitettséget jelölni kell.
3. A közös adatgyűjtés, modellezés és hírelőkészítés egyszer fut. Daily/legacy nem indít új adatgyűjtő programot. Saját csatornaazonosítójuk lesz, hogy azonos `daily223` profilnévvel se írják felül egymást.
4. További valódi agentváltozatok a szerző alatt lenyithatók. A főnézet 12 helyes; a régi minimum15 mezők történeti eredményként megmaradnak. Hiányzó tényleges kártya helyén konkrét ok látszik, nem mintaszelvény vagy más napi pótlás.
5. A régi konstruktorok által nem fogyasztott adatot nem nevezzük felhasználtnak. A közös forrás, a tényleges konstruktorbemenetek, az opcionális diagnosztika és a szimulátoreredmények külön követhetők. Nincs hamis 11./12. agent-eszközigazolás.

## Dashboard és ütemezés

- Új, interaktív dashboard: napi csomag, agentenkénti indoklás/forrás/hiány, kézi indítás és folyamatállapot, valódi eredmények, profit- és sikerességi grafikon, export.
- Új közös automatikus indítás: **aznap 08:00, Europe/Berlin**. Kézi és időzített indítás ugyanazt a tartós igényazonosságot használja; nem indul két párhuzamos napi csomag.
- A régi Daily és legacy Windows-feladatok **OFF** maradnak. Az új ütemezés külön, csak a kész közös út tesztelése után aktiválható.
- A grafikon külön mutatja a tényleges fogadások pénzügyi eredményét és az elméleti követést. Ismeretlen költség, árfolyam vagy lezárás nem nulla; READY nem profitbizonyíték.

## Adatmennyiség és helyi hibák

A célzott Python-kereső a már gyűjtött teljes pillanatképen dolgozik. Az agent saját feltételeivel kérdez és lapoz; nincs közös TOP-N meccsszűrés. Az eredeti sor/forrásazonosság, az összes vizsgált és ismeretlen elem száma megmarad. A keresési kivonat nem helyettesíti a kiválasztott tippek pontos eredeti adatainak ellenőrzését.

Hibás lekérdezés javítható a meglévő kereten belül. Opcionális adat hiánya helyi UNKNOWN; másik forrás, kombináció vagy a megjelölt városi időjárás-közelítés használható. Hibás ár, kevert meccsazonosság és ellentmondó termék nem válik érvényessé a darabszám kedvéért. Nincs végtelen hurok vagy bizonytalan fizetett kérés automatikus ismétlése.

## Ellenőrizhető megvalósítási sorrend

1. A teljes napot elakasztó numerikus bemeneti korlát javítása: globális identitás-, idő- és konfliktusvédelem után versenysorozatonkénti kapacitás; minden megmaradó adat elszámolva.
2. Célzott kereső, javítható lekérdezések és időtúllépéskor résznapló-megőrzés. Izolált tesztek, majd tényleges mentett bemenetes ellenőrzés modell/provider újrafuttatás nélkül.
3. Minimális rendszerillesztés, friss védettadat-ellenőrzés, új forrás elfogadása. Az új fizetett napi futás előtt a következő pont két csatornáját és a városi fallbacket is illesztjük, hogy ne kelljen egy újabb, eleve hiányos csomagot ismét előállítani.
4. Daily/legacy csatornaazonosság, mentés, ajánlatindex, export és szimulációs diagnosztika illesztése; régi jelentések változatlan ágon maradnak. Ezek rendszerellenőrzése után új valódi napi üzleti futás és a mentett eredmény ellenőrzése.
5. Új dashboard és közös kézi/08:00 út; rendszer- és böngészőteszt, valós kártyák és grafikonok visszaellenőrzése. Ekkor zárható a jelenlegi termékfázis.

Pontos source-terv: `artifacts/dashboard-renewal-after-ten-agents-20261113/companion-ticket-channels-source-review-v1/DESIGN-AMENDMENT.md`. A napi aktuális állapotot a saját futás kimenete és a `CONTINUATION.md` teteje adja; a tervek jelenléte nem telepítési bizonyíték.

## Városi időjárás-közelítés

A pontos stadionág elsőbbséget élvez. Ennek hiányában a szolgáltató által gyűjtött, az adott meccshez kötött város és — ha ismert — ország alapján egyértelmű geokódolás használható. A kapott értékek CITY_APPROXIMATION jelzéssel kerülnek az agentek közös adatcsomagjába. Nem készül belőlük szigorú stadion-receipt vagy visszamenőleges időjárássor.

A városi ág közös, monoton órával mért 120 másodperces keret után nem indít új hálózati kérést; a már meglévő cache eredeti időbélyeggel használható. A már megkezdett kérés túlfutása nem garantáltan korlátos összidő: az eredeti socket-timeout marad. Az ismeretlen város, kétértelmű találat vagy lejárt keret helyi hiány; más meccs és más adatcsalád feldolgozása folytatódhat. A forráskód- és tesztellenőrzés nem bizonyít tényleges új szolgáltatói előrejelzést.
