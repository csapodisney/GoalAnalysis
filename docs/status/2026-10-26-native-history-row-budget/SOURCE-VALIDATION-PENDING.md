# Arthur — veszteségmentes natív SHADOW bemenet: új forrás ellenőrzése fut

Állapot: 2026-10-26T12:38:00.751149+00:00. **Az új 5 894-es teljes futás és három új tényleges numerikus replay folyamatban; még nincs saját teljes elfogadás vagy runtime-visszaállítás.** Daily maintenance OFF, legacy Daily223 OFF. A visszaállítást csak saját sikeres teljes + worker exit0 után engedi az új authority.

Minimális változás: egy meglévő natív SHADOW olvasó, egy új tiszta tömörítő és egy új tesztfájl. A többi 1 230 régi APP/config/eredeti teszt/script/gold fájl byteazonos. A reader minden más függvényének AST-je változatlan. Minden eredeti teszt megmaradt; a 89 célzott esetből 20 új. Ruff és 10 változatlan replay-adapter kontroll PASS; 5 894 teszt gyűjtése PASS. Az eredeti nyolc replay-ellenőrzés és minden require byteazonos; nincs új riporteltérés-engedmény.

A globális azonosságmap teljes másolatok helyett kanonikus boríték-SHA-t tárol. Az összes eredeti alias teljes provenienciája külön megmarad, saját teljes borítékhashsel; payload-, receipt-, forrás- vagy óraütközés explicit elutasítást kap. Azonos ID/eltérő tartalom kemény hiba, nem kihagyott sor. UNKNOWN és különböző truth-verzió nem egyesül. Az egyetlen reprezentáns eredeti, teljes, legkorábbi boríték; órát nem vesz át másikból.

A végleges éles kóddal végzett retained, csak olvasó SHADOW 11 595 eredeti borítékot pontosan visszaállít; 2 622 distinct truth és 2 613 kiválasztott matematikai bemenet egyezik a teljes referenciával. Öt target igazolt, két venue UNKNOWN marad. A későbbi metadata miatt 197 forrásjelölt és 63 nem igazolt történeti forrás volt ezen a retained-cutoff olvasáson; nincs új receipt, kényelmi forrásszűrés vagy feltételezett availability. Ez nem új fit/PIT/LIVE-promóció vagy aktuális quote.

A 10 000-es sorhatár most distinct truth-t számol, a nyers munka külön legfeljebb a meglévő 200 forrás × 100 sorból adódó 20 000 előfordulás. Ez explicit munkaegység-váltás; nem a régi nyers 10 000-es guard változatlanságának állítása. A metadata 2 000, forrás 200, oldal 100, forrásbyte 5 000 000 korlát és az összes source/clock/konfliktus/UNKNOWN guard megmarad. Korlátelérés továbbra is teljes történet-elutasítás. A meglévő 64 MiB JSON-kimeneti korlát nem RAM-cap, a 180 másodperces külső worker-időkorlát változatlan.

Az egyszer indított Windows Validation Task a szolgáltatás folyamatfájából fut, Codex-függetlenül. Legfeljebb 75 perc, nincs automatikus retry. 1 233 forrás/gold fájl, 4 087 ledger-sor, 7 197 régi bizonyíték/archívum és 31 worker-input fagyasztott. A külön saját closure 16 inputja előpróbázott és pinnelt; indítása után csak saját full5894 és worker exit0 enged új független bemeneti/archívum-readbacket és az exact meglévő cc3b reporttal metadata-runtime/DailyON-t. Nem új normál generálás, paid metadata-kérés vagy receipt-batch.

Az első új Ruff két importsorrend-jelzése még freeze előtt javult. A 85 és 89 korábbi, sikeres célzott XML, az extra boríték/clock-binding előtti saját forrásmásolat és a régi V1/V2 artifactok megmaradtak. Az eredeti LF sortörést a végleges reader megőrzi; a minimális meglévő diff 18 új / 5 módosított-törölt sor. Ez nem failed full vagy újrafuttatott régi task.

Az eredeti 215 státusza 176 zöld / 39 piros-részleges; a 2.2 továbbra is piros, másik 214 sora változatlan. A külön 167-es munkalista 136 kész / 31 nyitott. A teljes öt év, availability-producer, N9 és promóció külön nyitott. Régi recovery/full/model/closure/receipt művelet nem indult újra.

[Új forrás bizonyítéka](EVIDENCE-SOURCE-PENDING.json) · [teljes 215 státusz](STATUS-215-SOURCE-PENDING.json) · [korábbi artifact-kontraktus](REPORT-INTERIM.md)
