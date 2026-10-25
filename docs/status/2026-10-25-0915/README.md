**Frissítés — 2026-10-25: az N6 saját hatókörében lezárt. 5590 teljes teszt, 241 célzott teszt, három tényleges replay; új 2026-10-25-3472f0b6fb86 run, két mentett szelvény kilenc igazolt lábbal. [N6 végleges eredmény és valódi maradék](../2026-10-25-n6-closed/README.md). A teljes projekt és a 7.4 szülő nincs kész; az alábbi régebbi pillanatképek dátumhoz kötöttek.**

**Frissítés — 2026-10-25: az N6 teljes tesztje 5590/5590, célzott tesztje 241/241, három tényleges numerikus replay sikeres. Az új generálás és publikációs bizonyítás még folyamatban; N6 nincs készre jelölve. [Aktuális N6 bizonyíték és maradék](../2026-10-25-n6-tested/README.md). Az alábbi 160/55 követelménymátrix korábbi pillanatkép, nem teljes projektzárás.**

**Legfrissebb: az N5 naptárjavítás is igazolt új publikációval lezárt. [N5 eredmény és valódi maradék](../2026-10-25-n5-closed/README.md). A korábbi N8 és a teljes 215 tételes lista alább elérhető; ez továbbra nem teljes projektzárás.**

**Frissítés: az N8 teljes tesztje és új fizikai publikációja már lezárt. [Aktuális N8 eredmény és maradék](../2026-10-25-n8-closed/README.md). Az alábbi szöveg a korábbi, futás közben kiadott pillanatkép; a 160/55 követelményszám változatlan.**

# Arthur – aktuális teljes státusz

Rögzítés: 2026-10-25T08:11:30.652800+00:00. **A projekt még nincs kész. A 2026-10-24 08:00 Europe/Berlin határidő nem teljesült.**

[Tömör, teljes 215 tételes zöld/piros lista](STATUS-LISTA-215.md).

[Teljes eredeti 215 követelmény, zöld/piros jelöléssel, bizonyítékokkal és maradékkal](ARTHUR_GEMINI_COMPARISON_2026_10_24.md).

- 🟩 160 követelmény bizonyítottan teljesült a saját hatókörében.
- 🟥 55 követelmény részleges, adatfüggő, fejlesztendő vagy még nem végleg igazolt.
- Az N35 lezárt: 5402 teljes teszt, 178 célzott teszt és három valódi numerikus replay PASS. A 2026-10-24-449dc2b4c80d run mindhárom DAILY lábán igazolt játékos/xG/naptár → modellválasz → szelvény → archívum/ledger kapcsolat. Ez nem minden profil teljes igazolása.
- Az aktív N8 415 célzott tesztje PASS. A nem publikáló valós forráspróba 11 meccs időjárását és 4 meccs stadiontávolságát igazolta; 342443/360000 bájt input, nulla modellhívás. Ez nem új szelvény.
- Az N8 5493 tesztes teljes suite és három újraszámolt replay ténylegesen fut, Windows szolgáltatásból indított egyszeri workerben. A futás indulása 2026-10-25T08:06:32Z. **Végső PASS és új N8 szelvény még nem igazolt.** Siker után egyszeri mai normál generálás és fizikai forrásellenőrzés következik.
- A normál napi időzítő a forrásvalidálás alatt kikapcsolva, legacyDaily223 kikapcsolva. Visszaállítás csak elfogadott adatút után.
- Következő fejlesztési maradék: minden ajánlatprofil tényleges kontextusfogyasztása; LIVE/papír kapacitásszámolás; Grok csomagkimaradások számlálása; adatmegőrző rollback-bizonyítás; új szelvények elavult sensitivity_note javítása; végső teljes riport és végleges forrás-push.

A hiányzó fizikai adatok és a későbbi empirikus optimalizálás a tételes listában piros maradnak. A 7/30 napos teljesítménymérés nem kötelező fejlesztési várakozás. Ez a commit köztes státuszriport, nem a még futó N8 forrás elfogadása és nem végső projektzárás.
