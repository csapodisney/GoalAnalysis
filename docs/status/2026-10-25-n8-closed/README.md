# Arthur – N8 igazolt új szelvény és adatfelhasználás

Rögzítés: 2026-10-25T09:22:22.782086+00:00. **Az N8 saját adatútja lezárt; az egész projekt még nincs kész.**

[Teljes eredeti 215 tétel, zöld/piros jelöléssel](../2026-10-25-0915/STATUS-LISTA-215.md). Az eredeti követelmények száma továbbra 🟩160 / 🟥55; a szülő 5.2 és minden profil teljes bekötése nincs ezzel lezárva.

## Igazolt eredmény

- Teljes **5493** teszt és **415** célzott teszt PASS; **három ténylegesen újraszámolt modellreplay PASS**.
- Új normál futás: `2026-10-25-d112d6937b35`. Két szelvény: DAILY223 három láb és Krónikás hat láb.
- Mind a **kilenc kiválasztott lábon** fizikailag igazolt a pontos forrás → elküldött adat → tényleges modellválasz → szelvény → archívum/ledger kapcsolat.
- A DAILY Sparta Rotterdam–Ajax lába igazolt időjárási adatot is használt. A többi bizonyított kategória játékosadat és xG; az adott lábak pontos bontása az alábbi JSON-ban.
- A dashboard újraindítása, az új run és jelentéshash helyi/Tailscale kiszolgálása igazolt. A napi időzítő visszaállítása után ugyanazon zárolás alatt ismét OFF a következő engedélyezett kódmódosításhoz; legacyDaily223 OFF.
- Az N8 előtti **3911** sor és a korábbi archívumok megmaradtak; az eredeti **3816** sor változatlan. Az eredeti 2×2×3 alapelv megmaradt. Valódi fogadást nem helyezett el a rendszer.

[Részletes, publikálható ellenőrzési összefoglaló és bizonyítékhash-ek](N8-VALIDATION-AND-PUBLICATION.json).

## Megőrzött hiba és helyreállítás

Az első fizikai auditor a naptárrekordok újraolvasási sorrendjének eltérése miatt állt meg. Mind a 39 eredeti kontextus teljesen egyezett; 39 naptárrekord és három lineage-elem tartalma és darabszáma is azonos volt. Az auditor helyreállítása mindkét sorrend saját manifestjét validálja, majd a teljesen azonos ellenőrzött elemekből visszaállítja az eredeti fogyasztási sorrendet és az eredeti dataset-ID-t. Egyetlen forrásmezőt vagy időpontot sem hagy figyelmen kívül. Tizennyolc valós/offline és manipulációt elutasító ellenőrzés PASS. Az eredeti hibát, forráskódot, szelvényeket és archívumokat megőriztük; ehhez nem történt új API-/modellhívás vagy generálás.

## Valódi maradék

- A mai 39 meccses körben a naptárgyűjtés 78 darab `QUOTA_REFRESH_REQUIRED` kaput jelzett, nulla kéréssel; ezért naptár, utazás és rotáció ezen a futáson UNKNOWN. Ez külön vizsgálandó adatfrissítési akadály, nem készre jelentett pozitív forráshasználat.
- A kiválasztott lábakon utazási távolság nem bizonyított; tényleges útvonalat nem állítunk. A rendelkezésre álló időjárás és játékos/xG adat sem jelent igazolt prediktív vagy pénzügyi nyereséget.
- A Grok futása `PACKET_TOO_LARGE` eredménnyel nem adott teljes ellenőrzést; ezt a csomagméret/kimaradás feladatnál javítani kell. A többi modell/DRAFT/tipster ajánlat teljes kontextusfogyasztása továbbra külön fejlesztés.
- Egy hivatalos HTML-archívum harmadik fél tokenanyagát tartalmazza, ezért a nyers fixture helyben, változatlanul megmaradt, de GitHubra nem került. A tiszta Git-klón önmagában e fixture nélkül nem teljes tesztreprodukció; a biztonságos publikus fixture-kezelés külön maradék.

A teljes lista további maradéka: profilszintű fogyasztók, LIVE/papír kapacitás elszámolása, csomagkimaradások, adatmegőrző rollback, új szelvények elavult sensitivity_note javítása, majd friss teljes riport. A későbbi 7/30 napos teljesítménymérés optimalizálás, nem fejlesztési várakozás.
