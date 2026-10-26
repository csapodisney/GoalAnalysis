# Ütemező háttérhibája: minimális javítás, saját elfogadás folyamatban

A valódi ütemező hibáját elkülönített PowerShell-próba igazolta: nightly exit0 és background exit7 mellett a régi ütemező exit0-t adott, háttérhiba-napló nélkül. A javítás külön megőrzi a háttér kilépését, fix szakasznevet és számszerű kódot naplóz. A nightly nemnulla hibája elsőbbségi; egyébként a háttér hibája lesz a végső kilépés. Gyermekkimenet, providerhiba-szöveg és titok nem kerül a naplóba. Minden szakasz egyszer fut, nincs retry.

A jelölt 15 artifact kontrollja PASS, benne négy változatlan eredeti ellenőrzéssel. A végleges éles forrás **67 célzott tesztje (10 új), 10 változatlan adaptertesztje, Ruff, native PowerShell AST, import/root/5936 collection, Dailyguard és az eredeti nyolc replay-ellenőrzés előpróbája PASS**. Egy meglévő PS-fájl és egy új, tíz viselkedési esetet tartalmazó tesztfájl változott; 1235 korábbi fájl és minden eredeti teszt byte-azonos.

A CheckOnly változatlanul a hitelesítő adatok, naplók és feladatok előtt tér vissza. Stderr warning és exit0 nem okoz hibát vagy naplót. Ha az opcionális háttér script és activation egyaránt hiányzik, nincs telepített szakasz: kihagyás történik, sikerállítás nélkül. Létező script egyszer lefut és hibája látható; meglévő activation, de hiányzó script telepítési hiba. Az éles, aktivált háttér hibája nem hallgatható el. Az odds- és nightly-hívás, hitelesítőadat-betöltés, külső hibakezelő és minden napi/adatkvóta-kapu változatlan.

Saját új **5936 teljes teszt és három új actual numerikus replay fut**, egy service-ősű, véges/noRetry workerrel. Ez még nem teljes elfogadás, runtime vagy scopezárás. Az éles módosítás előtt ugyanazon RootRunLock alatt Daily maintenance OFF lett; legacy OFF. 1237 source, 4087 régi ledger-sor és 34 worker-helper/input fagyasztva; futás közben nem írható. Csak saját sikeres teljes ellenőrzés és független readback után lehet az érintett forráskötést revalidálni és a meglévő riport runtime-ját Daily ON mellett visszaigazolni. Korábbi5926 nem új wrapperelfogadás.

A próbákban minden Python-feladat TESTONLY stub volt, valódi appstage, titokbetöltő, HTTP/API/LLM/capture/import nélkül. Fizetős normál generálás nem kell; offline fit kizárólag a három előírt replayben. Matematika, rang, súlyok, profilok, küszöbök, READY, gold, régi riport/quote/szelvény/órák változatlanok. Az első artifact fixture-szülőkönyvtár hibája és a freeze előtti új teszt Ruff-korrekciója eredeti helper/XML/log/hash alatt megmaradt, nem alkalmazás- vagy teljesfutás-retry.

Az eredeti215176 zöld/39 piros-részleges; csak2.2evidence frissült, a többi214sor/státusz/referencia azonos. Külön167136kész/31nyitott, nem összeadandó és nem projekt%. Holnapi átadás2026-10-27 08:00Berlin: ellenőrzött eredmény és pontos maradéklista, teljes projektzárási garancia nélkül.

[Bizonyíték](EVIDENCE-SOURCE-PENDING.json) · [Friss teljes215](STATUS-215-SOURCE-PENDING.json)
