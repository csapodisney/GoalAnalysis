# Tíz önálló agent — megvalósítás és ellenőrzés, 2026-11-12

Állapot: a munkapéldány kódja és célzott rendszertesztje elkészült. Ez nem éles telepítés, nem valódi modellfuttatás és nem profitigazolás. Daily és legacy kikapcsolva; az automatizálás szünetel.

## Megvalósítva

- Tíz külön döntési tulajdonos, saját módszerrel és személyiséggel; legfeljebb három saját, különálló szelvény fejenként. A napi 15–30 cél nem írja felül a bizonyítékot vagy a READY feltételeit.
- Minden agent az egész hozzáférhető napi naptárból indul. Mind a tizenkét bekötött adatcsalád ugyanabból a változatlan pillanatképből elérhető; nincs közös előszűrt meccslista.
- A nagy forrást nem kell tízszer a promptba másolni. Saját, korlátozott olvasóeszköz ad pontos adatoldalakat és ellenőrzött olvasási naplót. Minden kiválasztott eredeti ajánlat teljes rekordját igazoltan vissza kell adnia az eszköznek a terméképítés előtt.
- Saját opciókra kérhető aktuális szcenárió és leíró történeti eredményszimuláció. Hiányzó korabeli ár, XI vagy kalibráció UNKNOWN marad; nincs kitalált történeti profit.
- Több saját változat kanonikus ellenőrzése, SQLite-mentése, ajánlatindexe, exportja és klubmegjelenítése; a részleges döntés nem törli önmagában a korábbi ajánlatokat.
- CLI és Nightly illesztés az új végrehajtóhoz, automatikus fizetett újrapróbálás nélkül. Az időzítők ettől nem lettek bekapcsolva.
- Windows alatt rövidebb naplókönyvtár: az észlelt 261 karakteres út 217 karakterre csökkent. A teljes SHA256 kötés változatlan; mappaütközéskor nincs felülírás.

## Tényleges ellenőrzések

- Korábbi mentés/export/klub/éjszakai fixture-illesztés: 170 sikeres eset két megőrzött futásból; 35 állítás a valódi JavaScript injektált DOM-ellenőrzésében. Ez nem valódi böngészős teszt.
- A további történeti/bemeneti ellenőrzés eredeti 206 esetéből 196 PASS, majd a tíz javított fixture külön PASS. Az eredeti hibák és kilépési kódok megőrizve.
- A forrásolvasási integráció eredeti V8 futása 109/111 PASS, natív1. Az eredeti két sikertelenséget nem írtuk át.
- Külön natív stdio-ellenőrzés: natív0, két pontos olvasás, egy helyes helyi elutasítás, leállt saját folyamatfa; nincs Codex/model/API hívás.
- Külön eredeti folyamat-környezet teszt: 1 PASS, natív0, kulcsmentes környezet és parancsként nem értelmezett bemenet, 0 tiltott művelet. A V8 folyamatvédelmi hibája megőrzött.
- A V9 első teljes forrásolvasásos rendszerteszt tényleges Windows útvonalhibával állt le; eredeti natív1 és minden bizonyíték megmaradt.
- A javított forráson V10: 5/5 PASS, natív0, 16.688 másodperc, 0 tiltott művelet, változatlan forrás és leállt saját folyamatfa. Az esetek: tíz agent × két saját kártya teljes lánca; hosszú út; ütközés/felülírástilalom; hibás fázis helyi elszigetelése; titok miatti elutasítás még könyvtárkészítés előtt.
- A húszkártyás tesztben 80 napi meccs, mind a tíz tulajdonos, saját olvasási bizonyítékok, mindkét valódi helyi eszköz, audit, tényleges külön SQLite-mentés és húsz ajánlat visszaolvasása szerepelt. A nyelvi válaszok tesztadatok; minden kártya DRAFT, valódi fogadás 0.

A V8 régi fájlvédelmi eseményének pontos útvonala nem került naplóba, ezért annak történeti oka továbbra UNKNOWN. Ugyanaz az elszigetelési eset a javított forráson külön sikeres; ez nem az eredeti esemény utólagos átminősítése.

## Hátralévő éles bevezetés — ebben a sorrendben

1. A jelenlegi elfogadott production forrás és a mentett worktree-változás pontos összevetése, az ottani külön javítások megőrzésével. Friss OFF/folyamat/RootLock és védett adatok ellenőrzése a telepítés előtt.
2. A minimális forrásváltozás integrálása és az érintett éles forráskötések, megjelenítés, szolgáltatások ellenőrzése. A helyi tesztek nem jelentenek production-elfogadást.
3. Új, rendes üzleti futás előtt aktuális dátum, teljes napi naptár, szolgáltatói kvóta, költség és szokásos modelkeretek ellenőrzése. Korábbi napi futást vagy befagyasztott riportot nem írunk át.
4. Valódi mentett új futáson bizonyítani a Codex–olvasóeszköz együttműködését és a tíz agent tényleges adat-/eszközhasználatát. Jelenleg a natív eszköz működése és a fixture-host együttműködése igazolt, valódi reasoning-hívás még nincs.
5. A valódi kártyák számát és fontos hiányait külön közölni. Tizenöt alatti eredményt nem szabad töltelékkel vagy hamis READY-val elfedni. Profit, minden nyers sor elolvasása és oksági modellhatás nincs automatikusan bizonyítva.

A korábbi 31 adat-/specifikáció-/PIT-/weather challenger-/holdout-/kalibrációs követelmény ettől nem válik teljesítetté. A költség/FX hiány nem nulla. A folytatáshoz a mentett checkpoint és a saját aktuális fájlok az irányadók. Az 5%-os account-határ érvényben marad.
