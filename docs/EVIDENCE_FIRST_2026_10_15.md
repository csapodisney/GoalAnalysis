# Bizonyítottságra épülő ajánlatválasztás — 2026. október 15.

A korábbi 10-es alsó összszorzó beállítás volt, nem statisztikai követelmény.
A tulajdonos kérésére az összes nem DAILY profilnál megszűnt a szorzó és a
lábszám növelésének kényszere. A 2×2×3 szabályai változatlanok.

## Működés

- Az alap specialisták a már alkalmas jelöltek leggyengébb, majd átlagos
  alátámasztását, végül a rövidebb összeállítást helyezik előre. Egyetlen
  választás is teljes szelvény lehet; nincs kötelező alsó összszorzó.
- A húsz fiktív klubkarakter megtartja a saját piaci, modell- és történeti
  szűrőit. A korábbi alsó szorzósáv, kívánt lábszám és preferált egyedi ár
  nem ad több pontot. A felső korlátok változatlanok.
- A három numerikus profil ajánlatválasztása a leggyengébb, majd átlagos
  becsült előnyt és a kevesebb választást részesíti előnyben. A modellszámítás,
  a legalább 40%-os modellbecslés, a 3%-os becsült előny, a GAUSS stabilitási
  kapuja és az eredeti ár-azonosítási feltételek változatlanok. Ár nélküli
  kutatásnál az eredeti 50%-os kapu megmarad.
- Ezeknél a rangsoroknál a legerősebb alkalmas single dominálja a hosszabb
  összeállítást. A keresés ezért minden érvényes jelöltet pontosan megvizsgál,
  nem szorzócélhoz keres további lábakat.
- A valós szolgáltatói ár, a meccs/piac/idő/iroda/régió/pénznem azonossága,
  a történeti és H1-adatkövetelmények, a 7,2-es alap alátámasztási küszöb
  és a felső szorzókorlátok maradnak. A beállított szigor továbbra is 35.
- A kész portfóliószelvény minden választására kell review. Nem megfelelő
  saját konstrukcióból csak DRAFT/LOW kutatási ötlet maradhat; a tartalék
  `available` single teljes review mellett is figyelmeztetett DRAFT/LOW.
  A klub és a numerikus kutatás továbbra sem automatikus valódi fogadás.

A közös régi `target_min_odds: 10` konfigurációs mező történeti kompatibilitás
miatt megmaradt: a DAILY azonosítója a teljes régi beállításobjektumot is
használja. Az új nem DAILY kiválasztók ezt az alsó korlátot nem alkalmazzák,
és ezt külön, verziózott metaadat rögzíti. A felületen is „Nincs minimum”
látható. A tét, a hitelesítő adatok és a korábbi riportok nem módosultak.

## Bizonyíték és korlátok

Az azonos, mentett cb385563a836 bemeneten a DAILY teljes ticket és recommendation
objektuma pontosan azonos a módosítás előtti mentéssel, az azonosítókkal együtt.
Mind a hét DAILY pipeline-fájl hash-e egyezik az eredeti fagyasztott futáséval.
A bizonyíték: `analysis/evidence-first-20261015/daily223-exact-verification.json`.

Az offline klubpróbán 18 különböző karakter ugyanarra a Ferencváros–Plzeň
mérkőzésre adott egyes alternatívát. A közös adat és szűk numerikus lefedettség
miatt ezek nem független ötletek. A rendszer nem választ szándékosan gyengébb
bizonyítékot pusztán a látszólagos változatosságért. A rövidebb ajánlat és a
kisebb szorzó nem igazol kalibrált nyerési esélyt vagy nyereségességet.

## Friss futás és telepítés

Friss futás: `2026-10-15-5669f0838071`, 07:02:23–07:07:13 Europe/Berlin. Eredmény:
**COMPLETE, két READY portfóliószelvény**, mind a négy választás review-val:

- DAILY_223: ugyanaz a Benfica–Celtic over 3.5 (2.15), FTC–Plzeň BTTS-no
  (2.30), Dinamo Zagreb–Anderlecht vendég (3.75); összesen **18.54375**.
- Nyitány: FTC–Plzeň, első félidő 0.5 gól felett, **1.35**, egyetlen választás.

További megfelelő alap specialisták: Krónikás **1.67**, Ritmusőr **1.57**,
Őrszem **2.25**. Párharcmester és Merlin továbbra is DRAFT kutatás, mivel a
szigor megtartott adatkapuihoz nem készült megfelelő saját konstrukció.
A kepler modellhez továbbra sincs elegendő történeti adat. A klub 18 single
ajánlata a friss futásban is mind az FTC–Plzeň mérkőzésre koncentrálódik.

A webes adapter működése igazolt: 4 befejezett keresés, 93 visszaadott URL,
7 elfogadott forrás. A 30 nem időjárási háttértéma közül 1 SOURCED,
2 PROVISIONAL, 27 UNKNOWN; mind a hat időjárási téma UNKNOWN.
A READY a választások review- és konstrukciós feltételeinek teljesülését jelzi,
nem a csapathírek, a kezdőcsapat vagy a jövőbeli eredmény teljes bizonyosságát.
A kiegészítő Nemotron ticket audit UNAVAILABLE; nem módosította a kiválasztást.

A csomag 07:07:57-kor **4. javított fagyasztott verzióként** rögzítve, 27
ajánlással. Hash: `8d97d8943b5b0f0d4236e426a1e67264a0cb440d565403c4e9cce42f28ddd4ef`.
Az eredeti és a 2./3. verzió megmaradt. A négy időhatáros tanulási olvasat
mind a neki megfelelő verziót választja, 15 effektív napi csomag és nulla hibás
rekord mellett. Mind a hat adatbázis minden korábbi sora megmaradt; csak a
megengedett aktuális megjelenítési jelölők változhattak. A tényleges fogadások
száma továbbra is négy.

Az Arthur bejelentkezési feladat kontrolláltan újraindult, új szerver PID 6476.
Helyi és privát HTTPS gyökér/státusz: 200; mindkét válasz 16,975,988 bájt,
az új futással, két READY szelvénnyel és 4. fagyasztással. A korábbi kulcsbetöltő
működik. A gateway PID 19668 és a Project 2030 PID 11800/HTTPS443 200 maradt.
Telefonos cím bekapcsolt Tailscale mellett:
[Arthur](https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/).
Teljes Windows újraindítás és új fizikai telefonos próba nem történt.

Elfogadás: **1072 Python-teszt sikeres**, 112.94 s, 32 külső csomagból jövő
DeprecationWarning. Ruff és JavaScript syntax rendben, 88 frontend állítás,
független végső review és teljes DAILY-azonosság ellenőrizve.
Az első teljes futás egy elavult verziónév-elvárást jelzett a regressziós
tesztben; az elvárás frissítése után a teljes csomag hibamentesen lefutott.
Megfigyelt eredmények: `analysis/evidence-first-20261015/`.


Forrás- és hat adatbázis-mentés: `backups/evidence-first-20261015/`.
Korábbi fagyasztások és tényleges fogadások megőrzendők; új fogadás nem készül.
