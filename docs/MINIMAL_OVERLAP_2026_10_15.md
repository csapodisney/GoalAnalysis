# Minimális átfedés — 2026. október 15.

A tulajdonos kevesebb, eltérő és jól alátámasztott ajánlatot kér; minimális
átfedést megenged, a DAILY_223 szabályait változatlanul hagyja.

## Kiválasztási szabály

- A DAILY_223 teljes szelvénye változatlan: nem cserélünk vagy törlünk belőle lábat.
- A többi profil között egy mérkőzés legfeljebb egyszer szerepelhet, piactól,
  kimeneteltől vagy fogadóirodától függetlenül. Ugyanannak a profilnak a ticket,
  ajánlás és proposal másolatai ugyanazt a kiosztást képviselik.
- A DAILY-val legfeljebb egy éles főszelvény oszthat meg összesen egy meccset,
  ha nincs megfelelő különálló főajánlat. A klub- és papírprofilok nem kapnak
  kivételt. Ez felső korlát, nem kitöltési cél.
- Egy alternatíva őrizze meg a profil saját legjobb pontszámának legalább
  90%-át, az eredeti teljes érvényes készlethez képest. A nevezőt az elfoglalt
  meccsek kiszűrése előtt rögzítjük. Ez heurisztikus pontszámarány, nem 90%-os
  nyerési valószínűség és nem kalibrált megbízhatóság.
- Az eredeti minőségi kapuk megmaradnak. A modell nélküli történeti klubötlet
  nem tölthet fel gyengén alátámasztott extra helyet. A különböző családok
  nyers pontszámai nem összeadhatók; a kiosztás profilon belüli relatív
  minőségveszteséget és az előzetesen rögzített publikációs prioritást használja.
- Megfelelő eltérő ötlet hiányában a profil explicit ABSTAINED döntést ad.
  Nincs szorzó- vagy darabszámkényszer. A régi, meg nem játszott és el nem
  kezdődött aktuális duplikátum visszavonható; az eredeti bizonyíték megmarad.

## Megvalósíthatóság az eredeti reggeli adatokkal

A 5669f0838071 mentett futás teljes jelöltkörét szolgáltatói vagy modellhívás
nélkül vizsgáltuk. A 90%-os saját minőségmegtartás mellett nincs a DAILY három
meccsétől eltérő megfelelő extra ajánlat. A Hajduk–Ajax alternatívája Ritmusőrnél
64,43%, Nyitánynál 40,07% arányt ér el; pusztán a változatosságért nem kerül be.
Ez az offline bizonyíték nem friss délutáni ár vagy új ajánlat.

A megengedett egyetlen főszelvény-kivétel alapján várhatóan a DAILY és egy erős
Nyitány-ajánlat maradhat, tizennyolc további klubismétlés helyett. A friss futás
ettől eltérhet; kész szelvény csak tényleges ellenőrzés után állítható.

## Megőrzés és elfogadás

A módosítás előtt tizenhárom forrás/beállítás/állapotfájl és mind a hat adatbázis
konzisztens mentése készült: `backups/minimal-overlap-20261015/`.
Az eredeti reggeli DAILY teljes ticket és recommendation objektuma mentve:
`analysis/minimal-overlap-20261015/daily223-exact-baseline.json`.

## Ellenőrzött eredmény

A friss délutáni futás `2026-10-15-d4c4dde8cb97`, 18:03:01–18:07:12 Berlin.
Két READY ajánlat maradt, mind a négy választás review-val:

- DAILY_223: Brann–Lincoln BTTS igen 2.12, FTC–Plzeň BTTS nem 2.30,
  Dinamo–Anderlecht vendég 3.70; összszorzó **18.0412**.
- Nyitány: FTC–Plzeň, első félidő 0.5 gól felett, **1.36**.

Mindhárom mérkőzés 21:00 Berlin. Az egyetlen közös meccs az FTC; ez valódi
függőség, a szabály megengedett felső korlátja. További ismétlődő klub-,
numerikus vagy specialistakártya nem maradt aktív. A 30 profilból kettő publikál,
27 explicit tartózkodik, KEPLER-nél kevés a történeti adat. A klub 18 ismétlődő
ajánlata helyett nulla kiadott tipp maradt; nincs gyenge kitöltés.

A DAILY teljes ticket/recommendation objektuma a reggeli és a friss bemeneten
is pontosan azonos a korábbi és új motor között. Hét pipeline-fájl hash-e
változatlan. A reggeli első választás helyére a friss árak és review alapján
Brann került; ezt a változatlan korábbi algoritmus is pontosan így választja.
Az átfedésszűrés nem cserélt lábat a DAILY-ban. Bizonyíték:
`daily223-exact-verification.json` és `fresh-daily-unchanged.json`.

A webes adapter négy befejezett keresést, 61 visszaadott URL-t és öt elfogadott
forrást igazol. A 18 háttértéma közül három SOURCED, három PROVISIONAL és 12
UNKNOWN. A READY nem a kezdőcsapat teljes bizonyosságát vagy kalibrált
nyereségességet jelenti.

Az **5. javított fagyasztott verzió 18:08:16-kor** rögzítve, két ajánlással.
Hash: `c14b714263703e50d8561dcb686e40c7328a0299863434e5e2ed86a7bcf6e108`.
Az eredeti és a 2./3./4. javított verzió megmaradt. Az öt történeti cutoff
24/19/19/27/2 ajánlatot választ, mindegyik 15 effektív csomag és nulla hibás
rekord mellett. Mind a hat adatbázis minden eredeti sora megmaradt; csak a
megengedett aktuális jelölők változhattak. A tényleges fogadások száma négy.

Az élesítési guard két régi Arthur-példányt talált: az ütemezett PID6476 és
a 17:37-kor kézzel indított PID23972 kiszolgálót. A Windows CIM lekérdezése
összevonta a közös végpontot; a netstat mindkét példányt kimutatta. Az első
guard módosítás nélkül megállt, majd mindkét folyamat pontos azonosítása után
az eredeti bejelentkezési feladat egyetlen friss kiszolgálót indított: PID19036.
A kézi indító működését ez a feladat nem változtatta meg.

Helyi és privát HTTPS root/status: 200; azonos 16,629,854 bájtos válasz,
5. fagyasztás/két READY/0 klubtipp, új felület és betöltött kulcsok. A gateway
PID19668, a Project2030 PID11800/HTTPS443 200 megmaradt. Telefonos cím:
[Arthur](https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/).
Teljes Windows újraindítás vagy új fizikai telefonos próba nem történt.

**1103 Python-teszt sikeres**, 96.61 s, 32 külső DeprecationWarning. Ruff és
JavaScript syntax tiszta; 92 frontend állítás és független végső review sikeres.
A reviewer 500 kis párosítási gráfot teljes kereséssel is összevetett. A régi
tesztek üres profilokra vonatkozó elvárását és két mockaláírását az új szerződéshez
igazítottuk; az ár-, időbélyeg-, odds- és pontos kickoff-védelmek megmaradtak.

A mentési baseline 13 pre-edit fájlt és hat konzisztens adatbázist tartalmaz;
a publikus forwarding wrapper eredetije további, a mentett 5669 manifest
hash-ével byte-pontosan igazolt másolat. Konfiguráció, tét, hitelesítő adat,
korábbi jelentés és fogadás nem módosult.
