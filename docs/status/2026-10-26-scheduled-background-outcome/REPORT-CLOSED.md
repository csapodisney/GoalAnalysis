# Ütemező: háttérhibák ellenőrzött jelzése

2026-10-26T18:47:21.063030+00:00. Saját **5936 teljes teszt /67 célzott (10 új) /10 változatlan adapter /Ruff /3 új actual numerikus replay ×8 PASS**. Validation és closure exit0, új független root-readback, a meglévő cc3b riport helyben és Tailscale-en ellenőrzött. Daily ON, legacy OFF. A három modell és97/33/1 tanítóadat-manifeszt és adat fizikailag visszaolvasva.

A régi ütemező elkülönített ellenpéldája: nightly exit0 és background exit7 mellett hamis végső exit0, háttérhiba-napló nélkül. A javítás külön rögzíti a háttér kilépését. A nightly nemnulla hibája elsőbbségi, egyébként a háttérhibát adja vissza. Csak fix szakasznév és numerikus kód kerül a naplóba; sem gyermekkimenet, providerhiba-szöveg vagy titok. Minden szakasz egyszer fut, nincs retry.

CheckOnly továbbra is credential/log/job előtt fut. Stderr warning és exit0 nem hiba. Ha a háttér script és activation egyaránt hiányzik, nincs telepített opcionális szakasz: kihagyás, sikerállítás nélkül. Létező script egyszer fut és hibája látható; activation mellett hiányzó script launcherhiba. Az éles aktivált háttér hibája nem hallgatható el. Odds-, nightly-hívás, hitelesítőadat-kezelés és külső hibakezelő változatlan.

Egy meglévő PowerShell-fájl és egy új, tíz valódi Windows-folyamatviselkedési esetet tartalmazó tesztfájl változott;1235 korábbi fájl és minden eredeti teszt azonos. A próbák gyermekei kizárólag TESTONLY stubok, nem valódi scheduler-stage vagy HTTP/API/LLM/receipt/import/generálás. Offline fit csak a három előírt replayben. Matematika/rang/súly/profil/threshold/READY/gold/régi riport/quote/szelvény/órák változatlanok.

1237 source/4087 korábbi ledger-sor/8342 régi archív fájl/4 védett adattár ellenőrzött. Korábbi source/receipt/verzió prefixek és1143/1655/1963 cutoff tartalom+lineage megőrzött; a régi19raw/271result és181alias nem lett utólag befogadva. Csak a háttér activation kapott own accepted-source CAS-revalidálást; a Prospective activation raw byte-ja és a régi aktiválási idő/flags/limits változatlan.

Az első artifact fixture-parent hibája, a freeze előtti új teszt Ruff-korrekciója és a closure-assembler kétértelmű szövegmarkerének STOP-ja eredeti diagnózisok alatt megmaradt; utóbbi minden closure-file és művelet előtt történt. Nem alkalmazás-, teljes teszt-, replay- vagy éles stage-retry.

Eredeti215:176zöld/39piros-részleges; csak2.2evidence frissült, más214sor/státusz/referencia azonos. Külön167:136kész/31nyitott. Következő pont: az odds-monitor ütemezőbeli hibajelzési útjának RO vizsgálata; nincs kézi odds/API/LLM/gen művelet. Öt év/PIT/calibration/specütközés és projekt nyitott.2026-10-27 07:00Berlin ellenőrzött átadás+maradéklista, nem teljes projektzárási garancia.

[Bizonyíték](EVIDENCE-CLOSED.json) · [Friss teljes215](STATUS-215-CLOSED.json)
