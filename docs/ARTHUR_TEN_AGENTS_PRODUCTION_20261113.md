# Tíz agent — éles bevezetés, 2026-11-13

A kód élesben van, de a működő tízagent-szelvénygyártás még nincs igazolva. A két mai tényleges próbából **0 szelvény** készült. Daily és legacy kikapcsolva, az automatizálás szünetel, új fogadás nem történt. A felhasználó által előírt megállási határ 5%.

## Kész és ténylegesen ellenőrzött

- A tíz önálló tulajdonos legfeljebb három saját változatot készíthet ugyanabból a teljes napi adatpillanatképből. Az éles alkalmazás 284 útvonala egyezik a tesztelt munkapéldánnyal; az első telepítés 61 fájlt érintett. A többi éles módosítást megőriztük.
- Az eredeti naptár 73 napi meccset tartalmazott. Külön javítás biztosítja, hogy az agentes kontextus/időjárás út az árazatlan naptári meccseket is megkapja; nem közös, árazott előszűrésből indul.
- A valódi Codex kliens `tools/list` metaadatának kezelése javítva. Modellhívás nélküli valódi klienspróba látja a `read_data` eszközt a 110 608 714 bájtos bemeneten is.
- A kliens lezárása előtti tartós adatolvasási checkpoint javítása: 8/8 célzott eset PASS, natív0, nulla tiltott művelet. A régi sikertelen végső igazolást nem lehet ezzel megkerülni; ismeretlen klienskód nem válik nullává.
- Külön tényleges stdio-folyamatban egy teljes jelöltrekord olvasása után szándékos leállítás történt. A checkpoint és olvasási napló megmaradt. A kiszolgáló valódi kódja 124; a tesztvezérlő sikere ettől külön állítás. Ebben nem futott Codex vagy modell.
- A checkpoint-javítás éles telepítése natív0. Mindkét aktiváció teljes nyers tartalma változatlan, nincs új CAS. Új dashboard PID4680, aktuális JavaScript, ledger/config/aktiváció bájtjai változatlanok; mindkét időzítő Disabled. A forrás helyi commitja `5a09539`.

## A két valódi mai eredmény

1. `2026-11-13-852906067991`: CLI2, leállt folyamatfa, tíz sikertelen döntési út, 0 kártya. Az eredeti napló és eredmény megőrzött. A későbbi külön klienspróba azonosította a `tools/list` metaadat-inkompatibilitást; az eredeti elveszett eseményt nem rekonstruáljuk bizonyítottként.
2. `2026-11-13-5ea3149a910c`: CLI2, leállt folyamatfa, tíz `SOURCE_RECEIPT_MISSING_OR_OVERSIZE`, 0 kártya. Tíz tényleges befejezett modellhívás, 788 742 megfigyelt token; pénzbeli költség UNKNOWN. Egyszeri egyeztetés natív0: 4302 korábbi sor változatlan, csak az új futás és importrekord került be.

Ezeket a futásokat nem írjuk át vagy ismételjük változatlanul. A helyi fixture-rendszerteszt húsz kártyája nem valódi üzleti eredmény.

## Következő, még nem kész

1. A mentett második adatcsomag pontos, korlátozott rekonstrukciója új döntési futáshoz. Eredeti forrás-/ár-/időjárásórák és származási hivatkozások megmaradnak; új lekérés vagy modellillesztés nem szükséges. Az eltelt idő miatti érvénytelenséget a jelenlegi idővel kell ellenőrizni.
2. Egyetlen agent teljes PLAN→saját eszközök→FINAL útja először; csak ténylegesen igazolt, szabályos saját termék után indulhat a másik kilenc. Ennek választható vezérlése elkészült (`bd2a336`): 4/4 célzott eset PASS, natív0, nulla tiltott művelet. Éles telepítés natív0, új runtime PID27316, teljes 284 alkalmazásútvonal és védett adatok pontosak; nincs tényleges új modellhívás. Az első új teszt forrásának Ruff I001 formázási hibája megőrzött; külön, azonos AST-jű tesztforráson Ruff0 és az első tényleges négyes futás sikeres. Az új adatolvasási igazolás valódi reasoning-klienssel még nem elfogadott.
3. Valódi mentett kártyákon teljes forrás→DatasetStore/modell→termék/ár→időjárás/hír/review→szcenárió ellenőrzés. Adat hozzáférhetősége önmagában nem tényleges olvasás vagy oksági hatás.
4. Az aktuálisan megfigyelt időjárás UNKNOWN: a meccsekhez nem volt igazolt pontos stadionkoordináta. A friss hírolvasó nulla hírt adott; a friss hírelőállítás bekötése nyitott. Ezt a mentett adat újragondolása nem pótolja.

A 15–30 kártyás napi cél még nem teljesült. Nem készítünk töltelékkártyát vagy hamis READY-t, és nincs igazolt profit, PIT-backtest vagy minden nyers DB-sor tényleges felhasználása.

Az újraindítási pont és a natív eredmények: `artifacts/ten-agent-production-20261113/`, illetve a production azonos nevű könyvtára. Az eredeti októberi jelentések és javítási követelmények történetiek, változatlanok.
