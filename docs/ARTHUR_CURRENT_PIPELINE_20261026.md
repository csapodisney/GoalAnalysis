# Arthur tényleges szelvény-előállítási útvonala — 2026-10-26

Ez a leírás a `build/token-efficient-pipeline` ág `be795d743909e77cbc460ff30a65ad16d1f9396a` alkalmazásforrását és a lezárt saját bizonyítékokat írja le. A dokumentálás nem indított új generálást, modellillesztést vagy szolgáltatói hívást. A jelentés időpontjában a normál napi automata engedélyezett, a külön régi Daily223 tiltott. Egy korábbi szelvény READY állapota nem igazol mostani megjátszhatóságot vagy friss árat.

## Belépés és egyetlen koherens futás

A dashboard és a Windows napi automata közös belépése a `scripts/run-arthur.py`. A CLI ellenőrzi a cél dátumát, majd megszerzi a `jobs.portfolio.RunLock` közös OS-zárát. A tényleges beállításokat és az élő konfigurációt a zár megszerzése után újraolvassa. Függő konfiguráció-visszaállítás mellett az admission fence megakadályozza az új normál futást.

A `--settle` út a már mentett szelvényekhez kér eredményfrissítést. A `--live` út új elemzési csomagot épít a cél napjára. Egyik sem ad automatikus fogadásfeladási jogosultságot. A CLI a hibás vagy adatblokkolt kört is látható, titokmentes hibajelentésként menti.

## Forrás, azonosság és idő

Az élő sport- és oddsforrás kizárólag TheStats: `providers/the_stats_primary.py`, `providers/the_stats_odds.py` és a hozzájuk tartozó meglévő cache/kvóta/nyugta réteg. Az API-Football, Football-Data és The Odds API archív parserképessége megmarad; ez nem engedély élő visszakapcsolásukra. Külön ingyenes stadion-/időjárási forrás csak pontos helyszín- és forrásnyugtával lehet kontextus.

A kanonikus entitásokat a `normalization/canonical.py`, az igazolt párosítást az `identity.py` és `live_identity.py` kezeli. A névhasonlóság nem helyettesít meccs-, csapat-, liga- és kezdésazonosságot. A piac, kimenetel, periódus, bookmaker/régió/deviza, ár és eredeti quote együtt adja a kiválasztott termék kötését.

Az `event_time`, `published_at`, `observed_at`, `available_at`, az eredeti HTTP-body hash és az átvételi nyugta külön szerepet kap. Valódi szolgáltatói quote-idő hiányában `quoted_at` null; `quote_observed_at` saját megfigyelési idő, nem kitalált szolgáltatói óra. A cache olvasási ideje nem változtatja meg a régi forrás ismertté válásának idejét. A döntés előtti 15 perces lockout és a PIT-olvasók a korai és a végső kapukon is megmaradnak.

## Napi jelöltek és változatlan matematika

A `jobs/portfolio.py` koherens `run_portfolio` futást épít. A napi jelöltek, a forrásolt oddsok, történeti adatok és az engedett kontextusok kerülnek a megfelelő, elkülönített fogyasztókhoz. A numerikus modell és az xG/játékos rangsorjel nem azonos az LLM szöveges véleményével.

A `engine/daily_223.py` eredeti 2×2×3 matematikája változatlan. A motor SHA-256 értéke `d7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c`. A szabályos játékidő, minimum minta, profilküszöb, numerikus rang és aranyreferencia nem lazul a kívánt darabszámért.

Az önálló profilok előbb saját opcióikat készítik. A `tipsters/profile_config.py` hashkötött profilkontraktusai és a `tipsters/recommendations.py` kapui megőrzik a karaktereket. A v7 klubprofilok alapsávjai 10–50 közöttiek, minimumáruk kötelező. A kódban megmaradt szűk, forrásolt HIGH_ODDS singletonkivétel miatt az abszolút minden-esetre-érvényes 10–50 szöveg teljesülése külön nyitott specifikációs kérdés; ezt nem módosítja ez a dokumentum.

## Háttéradat és Astra előzetes ellenőrzés

A `candidate_context_review.py` és `profile_context_stage.py` a pontos quote-hoz fagyasztja a tényleges kontextust. A cél napját explicit kapja a producer; az aktuális UTC nap nem helyettesíti a portfólió cél napját. A `profile_context_review.py` ugyanazt az eredeti quote-ot, kontextust, tényleges kérést és tényleges választ köti a kiválasztott profilhoz. Más quote vagy csak hasonló termék nem örökölheti a választ.

Az Astra citation-sémában a megengedett hivatkozások az igazolt URL-ek pontos enumjai. Forrás nélkül üres tömb szükséges; a szigorú helyi utóellenőrzés megmarad. Nincs utólagos URL-normalizálás, hibás válasz átírása vagy automatikus újrapróbálás.

A DAILY hiányzó kiválasztott quote-jait a korlátos `daily_review_cohort.py` terv helyezi előre, majd csak az eredeti jogosult kohorszból egészíti ki a csomagot. Legfeljebb 30 jelölt/csomag és az eredeti három közös followup-kísérlet marad; a hibás kísérlet is beleszámít. A primer siker és valós usage nem vész el egy mellékkör hibájától. A keret nem garantál konvergenciát: hiányzó exact review esetén a kártya továbbra DRAFT.

Játékos/xG/naptár, időjárás, utazás, rotáció és humán kontextus külön forrásolt vagy UNKNOWN. Predicted XI nem confirmed XI; üres sérüléslista nem egészséges keret; kupa nem bizonyított rotáció. Sent/cited/considered nem önmagában kalibrált vagy kauzális valószínűséghatás.

## Allokáció, készültség és kapacitás

A portfólió az önálló profilopciókból dolgozik, egzakt teljes szelvényhalmaz-duplikációt szűr, és a szabály szerinti részleges átfedési büntetést alkalmazza. A DRAFT/READY kapu nem változik a darabszámcél miatt. Nincs gyenge töltelékláb vagy PAPER-rel feltöltött LIVE nevező.

A `portfolio_capacity.py` és `portfolio_objectives.py` v2 számláló az allokáció után külön számít LIVE és PAPER szelvényeket. REVIEWED ajánlás csak a már mentett READY LIVE ticket pontos profil/ticket/quote azonosságával számít. Ismeretlen, üres, duplikált vagy suspended sor nem növeli a kész darabszámot. A dashboard fejléc az eredeti v2 LIVE/PAPER értékeket külön, explicit LIVE céllal és hiánnyal mutatja. Hiányzó/érvénytelen vagy csak általános legacy READY mező esetén a bontás ismeretlen; nem feltételezett nulla vagy LIVE darabszám. Ez prezentáció, nem új számlálási képlet.

Egyetlen háromlábas DAILY mellett mindhárom meccs a teljes LIVE csomag 100%-ában szerepel. Ez az abszolút 40–50%-os kitettségszöveggel nem fér össze. A számlálást nem javítjuk látszólagos papírszelvények vagy a DAILY törlése útján.

## Egy közös Grok-ellenőrzés

A `jobs/grok_gate.py` a `grok_advisory.py` segítségével egy közös csomagot állít össze; a kiválasztott tételek és a már letöltött háttéradat kerül bele. A `grok_packet_readable.py` veszteségmentes fallbackje és a `grok_packet_accounting.py` kategóriaszámlálói a mezőket, számokat, forrásokat, quote-okat és időket visszaállíthatóan őrzik. Numerikus értékek JSON-számliterálok maradnak. A 100000 karakteres limit és a 99000 karakteres biztonsági határ nem emelkedett. Korai szerkezeti hiánynál a még össze nem állított kategóriák UNKNOWN állapotúak, nem kitalált nullák.

A Grok pro/kontra/kérdéses láb véleménye után legfeljebb egy független Codex-kör vizsgál konkrét, igazolt forrású kifogást. A forrás nélküli aggály vagy egyet nem értés nem indít javítást. A natív Grok-kapu SHADOW marad; nem kap automatikus éles átírási jogosultságot. Az engedett javítás is csak ugyanazon futás előzetesen ellenőrzött alternatívájából, legfeljebb egyszer történhet. A DAILY látható a review-ban, de nem javítható át.

Az UNAVAILABLE, budget-limit vagy parserelutasítás nem pozitív Grok-ellenőrzés. Régi hibás kört nem változtatunk utólag sikeressé, és a mentett csomagokat nem küldjük újra puszta metadata-igazolásért.

## Mentés, export, eredmény és tanulás

A végleges run-ID, method-version és lábszintű `ticket_evidence` lezárása után a CLI kiszámolja a kanonikus report-hasht, a `write_portfolio_bundle` megírja a reportot és exportjait, a `PortfolioLedger.save_run` pedig a naplóba ment. A fájl nyersbyte-hash és a kanonikus JSON tartalmi hash külön érték; nem cserélhetők fel.

A `sensitivity_notes.py` kizárólag az exportban jelenít meg már létező, pontosan kötött Astra risk note-ot, külön `report-sensitivity-notes.json` eredetlánccal. Hiánynál explicit pending marad. Ez nem új modellválasz, nem új tény, és nem írja át a régi JSON-t, ticketet vagy lábat. A korábbi immutábilis Markdown sem kerül visszamenőleg javításra.

A `PortfolioLedger.refresh_results` és `jobs/the_stats_results.py` a tényleges végleges eredményből számol; félbeszakadt/ismeretlen esemény nem lesz kitalált nyertes vagy vesztes. Valós, kutatási és papírteljesítmény külön kohorsz. Az 1 EUR egységtét és a napi/gördülő KPI mérés, nem hozamígéret.

A `learning` réteg és a `calibration_release.py` bizonyítékhoz kötött javaslatot készít. A walk-forward/proper-score, elegendő valódi prospektív adat, hét lezárt SHADOW nap és a további promotion feltételek hiánya blokkol. A tanuló nem ír önállóan paramétert és nem engedélyez fogadást. A `daily_native_shadow.py` külön, korlátos, hálózatmentes post-publication worker a már meglévő forrásokat olvassa; nem azonos az éles numerikus fogyasztással.

## Üzem és bizonyított visszaállítás

A dashboard helyi és Tailscale/gateway útját, a kiszolgált kódot, a pontos run/report-hasht, a learning/team végpontokat és a napi automata állapotát a lezárt saját runtime-próbák visszaellenőrizték. A jelenlegi audit csak olvas, ezért Daily ON marad. Valódi új appforrás-fix előtt közös RootRunLock alatti maintenance OFF, elfogadás után igazolt ON kell. A legacy Daily223 mindig OFF.

A lezárt 11.7 próbában kilenc allowlistelt konfiguráció prior→current helyreállítása egyazon zárban történt: hét korábbi byte-egyezés és két korábbi hiány → SHADOW → current ACTIVE. A modell/live beállítások azonosak voltak; nem állítunk meg nem történt modellváltást. A CAS/hashbundle/atomic journal/pending fence/crash recovery a félállapotot védi. Adat, report, nyers archívum, titok vagy forráskód nem írható a configmanifestből.

Az adatmegőrzési próba 10612 nyers fájl, 26 SQLite main és teljes WAL byteazonosságát, sémáját és összesen 18 148 065 committed sor COUNT-ját igazolta, 24 750 867 951 byte leltárral. Ez nem per-row hash számítás, nem 18 millió egyedi meccs, és nem tanításra alkalmas ennyi minta. Az SHM/lock futásidejű koordináció külön hatókör.

## Legutóbbi tényleges normál kör és fennmaradó határok

A `2026-10-26-cc3b72cd5b81` körben egy READY DAILY készült három lábbal; a csomag összességében PARTIAL: 1 REVIEWED, 5 DRAFT, 22 ABSTAINED és 2 NO_STABLE_SELECTION ajánlás. A helyes kapacitás 1 LIVE / 0 PAPER / 19 hiány. Két DAILY-lábon players/xG/schedule considered+cited kötés bizonyított; a harmadik láb mind a hat háttérkategóriája UNKNOWN. Időjárás, utazás és rotáció nem igazolt teljes körben.

Egy tényleges Grok-kérés `3ad19595bb974220be4bfe9e231230ea` COMPLETE, producer wire 97623, provider wire 97696 karakter. A független ellenőrzés COMPLETE, nulla igazolt egyetértéssel és nulla javítással. A két régi túlméretes bemenet 90826/97431 karakteres veszteségmentes rekonstrukciója külön retained-input bizonyíték, nem e régi kérések új hálózati elküldése.

A legutóbbi saját teljes suite 5851 PASS, 44 warning, 2693.00 másodperc; három új, ténylegesen újraszámolt numerikus replay mind a nyolc ellenőrzése PASS. Ez helyi teljes teszt a védett eredeti archívummal. A nyilvános, titokmentes TESTONLY stadionfixture 26 kontrollja a privát HTML nélkül is PASS; két változatlan eredeti raw-integrity teszt a privát archívumot igényli. A teljes önellátó publikus clone fullsuite nincs bizonyítva.

Az ötéves adatfedés, a minden fogyasztóra kiterjedő 30 napos PIT-visszajátszás, a teljes kalibráció/promóció, a 7/30 napos új kohorszok teljesítménye és a 20–30 jogosult LIVE szelvény nincs késznek jelentve. A későbbi mérés külön optimalizálás; a konkrét végrehajtható kód-/dokumentációs-/adatmaradékot az eredeti 215 követelmény friss auditja tételesen különíti el.

## Ellenőrizhető saját scope-riportok

| Hatókör | Végleges, GitHubon ellenőrzött riport |
|---|---|
| N2 exactquote/context és forráshasználat | `3137e0ac55a7be185b5a555ce8df6f66669c2795` |
| LIVE/PAPER kapacitásszámláló (az új felirat külön saját5851/runtime/root bizonyítékkal) | `0e4abf38dda94334b360daa90e5889e5f605a3b3` |
| Grok codec, cél napja és valódi review | `ecefd529a5ce777718ef1cec720e562388f87d91` |
| Adatmegőrző konfigurációs rollback | `264852e0b97d551bbabe29f67e3c1c327f7ddfd4` |
| Forráskötött sensitivity export | `f7d2b9ce2861a562ea1fb30edf58fea91a2e49fe` |
| Titokmentes TESTONLY publikus fixture | `671c5451a3c1c527f6bad77b24c3ab85034ba48a` |

A privát helyi bizonyítékfák és régi sikertelen próbák változatlanok; a nyilvános scope-riportok nem jelentenek teljes projektlezárást.
