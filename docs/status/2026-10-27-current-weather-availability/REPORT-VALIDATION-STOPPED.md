# Az időjárási bekötés teljes elfogadása megállt

2026-10-27T16:55:26.851997+00:00. A saját teljes futás natív kilépése **1**. A valós XML pontosan 6426 tesztesetet tartalmaz: 6425 PASS és egy FAIL. A hiba a helyi HTTP tokenellenőrzési tesztben jelentkező WinError10053 kapcsolatmegszakadás, a HTTP-státuszsor olvasása előtt.

A 284 kiválasztott időjárási eset, köztük 34 új eset egyedileg PASS ebben a **sikertelen teljes futásban**. Ez nem teljes, célzott vagy runtime-elfogadás. A handler és az érintett teszt byte szerint egyezik a korábbi elfogadott Astra-forrással; időjárási regressziót vagy a konkrét hálózati hiba kizárólagos okát ebből nem állítunk.

Az elutasított POST törzsének olvasása előtti kapcsolatlezárás szállítási versenyhelyzetének lehetősége forrásból azonosított. Az [RFC 9112 9.6. szakasza](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6) tárgyalja a túl korai lezárás miatti elvesző válasz problémáját. Ez a konkrét eseményre nézve vizsgálandó mechanizmus, nem bizonyított kizárólagos ok.

Minden eredeti XML, log, natív eredmény, forrás és 392 pin megmarad. Nincs eredeti full/target/replay újrafuttatás, kihagyott teszt, lazított assertion vagy sikerre átírt eredmény. Az új HTTP-forrásjavítás és külön, korlátos TESTONLY diagnosztika még csak előkészítés. Nem indult follower, aktiválás, runtime-elfogadás, új éles időjárás-letöltés vagy új üzleti futás. **Daily és legacy OFF.**

Az eredeti 31 nyitott követelmény továbbra is külön adat-, mérési, specifikációs és fejlesztési maradék; nem 31 programhiba és nem projektkészültségi százalék. A következő lezárásról rövid kész/maradék státusz készül.
