# Arthur Goal Analysis vezérlőpult — Phase 23

A helyi vezérlőpult a `127.0.0.1:8765` címen fut, ezért csak azon a Windows
gépen érhető el, amely elindította. A felület megmutatja a konfigurált
versenysorozatok és API-kulcsok állapotát, az utolsó DAILY_223 riportot, a három
lábat, a kiválasztott irodát, az összszorzót, a futási naplót és a korábbi
riportokat.

Az `Elemzés indítása` gomb háttérben futtatja a meglévő élő parancsot. Egyszerre
csak egy futás indulhat. A módosító végpont egy munkamenetenként véletlen helyi
tokent kér; az API-kulcsok értéke nem kerül a böngésző válaszába.

A `Másolás Astrának` gomb csak a tömör riportot teszi a vágólapra: dátum,
állapot, iroda, összszorzó, három láb és darabszámok. A nyers válaszok és teljes
történeti készletek kimaradnak, így a chatben végzett szakértői felülvizsgálat
lényegesen kisebb bemenetből dolgozhat.

## Windows-integráció

Az installáló PowerShell-szkript:

1. a két sportadat-kulcsot Windows DPAPI-val, az aktuális felhasználóhoz kötve
   titkosítja a `data/secrets/` könyvtárba;
2. `Arthur Goal Analysis` néven asztali parancsikont készít;
3. `Arthur Goal Analysis Daily 223` néven 07:30-as napi feladatot regisztrál;
4. elindítja a helyi vezérlőpultot.

A feladat bejelentkezett felhasználói munkamenetben fut. Ha a gép 07:30-kor nem
elérhető, a `StartWhenAvailable` beállítás miatt a következő lehetséges
időpontban indul. A futás naplója a `reports/daily223/scheduler.log`, a riportok
pedig külön, időbélyeges könyvtárakba kerülnek.

Az OpenAI/Astra automatikus szerepköre még nincs bekötve ebbe a DAILY_223
vezérlőpultba. A Phase 23 a tömör vágólapcsomaggal kapcsolja össze a helyi
adatgyűjtést és a chatben indított Astra-felülvizsgálatot. A helyi determinisztikus
szelvény ettől függetlenül elkészül, amikor van három megfelelő, ugyanazon
irodától árazott láb.
