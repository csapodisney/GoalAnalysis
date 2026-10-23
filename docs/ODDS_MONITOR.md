# Arthur: külön szorzófigyelő

A meglévő Windows ellenőrző percenként indít olcsó helyi ellenőrzést, és a szorzófigyelőt az éjszakai szelvénykészítéstől
függetlenül hívja meg. A figyelő nem hív Astrát, nem készít új szelvényt és nem
módosít rögzített ajánlatot, szelvényt vagy szorzót az eredeti naplóban.

- Az azonos szolgáltatói lekérdezés eredményét sikeres befejezésétől számítva
  legalább **30 percig** használja. A percenkénti helyi ellenőrzés nem jelent
  percenkénti API-kérést; normálisan 30–31 perc után kerül sor új lekérdezésre.
  A tényleges frissítés a hálózattól és a kerettől is függ.
- Csak a már mentett naptárból ismert mérkőzéseket és azonosított termékeket
  figyeli. **Kezdés előtt 15 perctől** nem indít hozzájuk új lekérdezést, és a
  közben lezárt mérkőzés új válaszát sem adja ki új ármegfigyelésként.
- API-Football és The Odds API támogatott; új előfizetés vagy külön kvótakeresés
  nincs. A név, kezdés, szolgáltatói azonosító, piac és árforrás ellenőrzése
  ugyanazokat a meglévő feldolgozókat használja.
- A figyelő saját, naponta összesített felső korlátként alkalmazza a meglévő
  `max_football_calls` és `max_odds_credits` értékeket. Ezek az eredeti generátorban
  futásonkénti limitek voltak: a figyelő ettől külön, konzervatív napi keretet
  tart fenn. A szolgáltató visszaadott fennmaradó kvótáját is figyelembe veszi;
  ismert alacsony kvótánál legalább egy beállított generálás keretét megőrzi az
  éjszakai feladatnak, ezért a figyelő előbb is megállhat.
  A többi alkalmazás teljes fogyasztását helyi számlálóból nem állítja ismerni.
- Szolgáltatónként alapból legfeljebb három kérés indulhat percenként, összesen
  hat lekérdezés egy ellenőrzéskor. A régebben vagy még egyáltalán nem frissített
  lekérdezések kapnak elsőbbséget. A keret betelte látható várakozás, nem siker.
- Párhuzamos ellenőrzésnél tartós foglalás előzi meg a dupla lekérést. HTTP 429
  után a `Retry-After` értéke és növekvő visszavárakozás érvényesül. A hibák nem
  írnak sikeres frissítési időt; nyers hibaszöveg és hitelesítő adat nem kerül a
  figyelő állapotába.

Az új ármegfigyelések külön, csak bővülő pillanatképek a
`data/arthur/odds-monitor.sqlite3` állományban. A későbbi kontrariánus elemzés
ebből is olvashat 24–48 órás, összehasonlítható árelőzményt. A dátum szerinti
visszaolvasás nem használhat későbbi pillanatképet. A megfigyelési előzmény nem
jelent automatikus fogadást vagy igazolt nyereségességet.

Belépési pont: `scripts/run-arthur-odds-monitor.py`. Állapotot és előzményt a
`monitor_status`, illetve a `monitor_history` csak olvasó függvények szolgáltatnak.
A telepítő az egyetlen meglévő Windows ellenőrző percenkénti ritmusát használja;
külön szorzófigyelő időzítőt nem hoz létre. A regisztráció ellenőrzött alkalmazása
külön művelet; a forrásfájl módosítása önmagában nem változtatja meg a Windows feladatot.
