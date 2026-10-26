# Arthur — a háttérimport aktuális kapuinak csak olvasó leltára

Audit: 2026-10-26T10:55:43.687468+00:00. Független visszaolvasás: 2026-10-26T10:57:49.440362+00:00. Ez a jelentés az itt rögzített órák állapotát igazolja; későbbi időpontra nem ad HTTP- vagy kvótaengedélyt.

## Tényleges állapot

A napi workflow helyreállítása után a **normál időzítő automatikusan újra elindította** a háttérimportot. A 2026-10-26T10:41:09.169582Z..10:41:23.081032Z közötti batch 19 kérést és 19 oldalt dolgozott fel. Mind a 19 eredeti oldal-manifest, fizikai nyers fájl SHA256-azonossága és az azonos megfigyelési időhöz kötött hitelesített HTTP200 kvóta-receipt külön visszaellenőrzött. Ezeket a kéréseket nem az audit indította.

| Kapu | Az audit óráján igazolt állapot |
|---|---|
| Napi csomag / job / freeze | A meglévő cc3b napi job és tényleges rögzítés igazolt; publikációs kapu AVAILABLE. |
| Forrásaktiváció | Háttérfolyamat és prospektív odds a saját elfogadott aktuális forrásra kötve. |
| Első tényleges háttérkapu | **COOLDOWN**, következő próbálkozás legkorábban **2026-10-26T11:11:09.169582Z**. |
| Másodlagos kvóta, amit a tick a szünet alatt nem ér el | QUOTA_REFRESH_REQUIRED, 0 engedélyezett kérés. Az előző perc resetje lejárt; régi headerből nem számolunk mai kapacitást. |
| Napi időzítő | Daily ON, legacy Daily223 OFF, változtatás nélkül. |

Az utolsó batch REQUEST_LIMIT eredménye a saját 19-es kéréskeretének elfogyását jelenti; nem szolgáltatói tiltás vagy forrásaktivációs hiba. A kapukat és a tartalékokat nem lazítottuk. A korábbi DAILY_WORK_HAS_PRIORITY diagnózis a helyreállítás előtti állapotra volt érvényes; most nem ez az első blokkoló feltétel.

## Melyik sor haladt?

A fagyasztott napi queue sorrendje `RECENT_DELTA_THEN_RETAINED_CURSOR`. A friss, 2026-10-18..2026-10-25 közötti delta kapott prioritást; ez nem azonos a régi ötéves terv kurzorával.

| Terv | Feldolgozott / összes query-ablak | Maradék |
|---|---:|---:|
| Friss napi delta | 19 / 20 | 1 query-ablak |
| Megtartott ötéves terv, 2021-10-18..2026-10-17 | 496 / 1220 | 724 query-ablak |

Az ablakok száma nem garantált HTTP-hívásszám: lapozás és cache is számít. A következő rendes tick előbb a megmaradt deltát dolgozhatja fel, és csak annak tényleges befejezése után folytathatja a régi kurzort, az akkor friss kvóta-, idő- és napi munka-kapuk szerint. Az audit ezt nem indítja el és nem ígér befejezési időt.

A normál batch kimenete SHADOW módban 179 új natív azonosítót és 271 kanonikus verziót jelentett; a régi modelladatút változatlan. Ez a batch saját leltári számlálója, nem ugyanennyi időhelyes tanítóminta, globálisan egyedi mérkőzés, új modell-előléptetés vagy LIVE-kapacitás.

## Bizonyítás és határok

- 1231 forrásfájl byteazonos; 244824916 forrásbyte független hash-visszaolvasással, 4MiB-os darabokban, 512MiB összkorlát alatt.
- Az audit előtt/után mind a 4087 aktuális ledger-sor változatlan. A 19 oldal és 19 eredeti nyers fájl fizikai SHA-ja és kvóta-receipt-kötése igazolt.
- Hat pure negatív kapukontroll PASS: a napi elsőbbség, szünet, stale kvóta, hiányzó terv, előrefutó óra és eltérő forráshash nem változhat pozitív kapacitássá.
- SQLite közvetlen `mode=ro`, `query_only`, `BEGIN`; a write-inicializáló SharedQuota-kapcsolat és a hálózat az auditban tiltott. Nem hívtunk tick/import/save/capture/allowance-write-init utat.
- Az első független olvasó a nyersoldal-/állapotolvasó 32MiB korlátját tévesen a teljes forrásfa nagy golden inputjaira is alkalmazta, ezért a guard megállította. Az eredeti helper SHA és diagnózis megmaradt. Az új verzió kizárólag a forrásokat hash-eli streamelve; a nyersoldal-/állapotkorlát változatlan, nincs forrás- vagy aranyreferencia-módosítás.
- Az auditban **0 új API/LLM/fit/generálás és 0 productionírás**. A korábbi normál időzítő 19 kérését külön tartjuk ettől. A Daily és a legacy időzítő állapota változatlan. A kizáró RootRunLock ideiglenes runtime-byte-jai és a saját auditartifactok nem üzletiadat-írások.

Az eredeti 215-ös lista **176 zöld / 39 piros-részleges** maradt; csak a 2.2 követelmény bizonyítéka frissült, a többi 214 sor bitazonos. A külön 167-es munkalista **136 kész / 31 nyitott**, nem összeadandó. A teljes ötéves fedés továbbra sem igazolt; 2.2 PIROS. Nincs új claim/receipt-batch vagy kézi HTTP-import felhatalmazás, az N9-hiba és minden korábbi STOPPED bizonyíték megmarad.

Következő egyetlen pont: a meglévő történeti adatok fogyasztónkénti, tényleges source/availability/PIT-alkalmasságának csak olvasó leltára. A mostani szabályos szünet miatt nem indokolt új háttérimport-kódjavítás.

Részletek: [bizonyítékkivonat](EVIDENCE-CLOSED.json), [teljes 215-ös státusz](STATUS-215.json).
