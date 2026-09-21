# Arthur v3.4 — Windows és ChatGPT/Codex


## v3.4: automatikus frissítés, szorzó nélküli előzetesek

Állítsd le a futó Arthur-szervert (`Ctrl+C`), töltsd le az
`Arthur-v3.4-frissites.ps1` fájlt, és futtasd a letöltési mappádból:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\Arthur-v3.4-frissites.ps1"
```

A frissítő a `C:\AI-Work\GoalAnalysis` mappába ír, előtte mentést készít a
`backups` almappába. Ellenőrzi a csomag és a programfájlok SHA-256 lenyomatát;
írási hiba esetén visszaállítja az előző fájlokat. A kulcsok, saját konfiguráció,
adatbázis és riportok megmaradnak. A frissítés végén elindítja Arthurt.
Eltérő célmappához `-ProjectRoot "D:\GoalAnalysis"`; indítás nélkül `-NoLaunch`.

Ezután **Vétó = 0**, válaszd a kívánt napot, és indíts **új elemzést**.
A korábbi riportok változatlan archívumok. Ha az Odds API nem ad szorzót,
megjelennek a valós mérkőzések és a „Szorzóra váró előzetes” összeállítások.
Az ár helyén „—” látszik, a hiányzó háttéradatot a kártya jelzi. Ezekhez
friss szorzóig nincs megjátszási naplózás vagy számított kifizetés.

A naptárhoz API-Football-kulcs szükséges; az Odds-kulcs hiánya már nem állítja
le ezt a folyamatot. A Codex továbbra is a ChatGPT-bejelentkezést használja.

A GitHub-csatlakozó írási kísérlete 403 hibát adott; a távoli kód jelenleg nem
frissült. Az itt futó munkakörnyezet nem a Windowsos projektmappa. Közvetlen
helyi szerkesztéshez a Windowsos Codexben ezt a projektet kell munkamappaként
megnyitni; a frissítő a jelenlegi átadást teszi automatikussá.

## Nem indul: Cannot decrypt THE_ODDS_API_KEY

A v3.3.1 javítócsomag tartalmát bontsd ki a meglévő
`C:\AI-Work\GoalAnalysis` mappába, felülírva a programfájlokat. Utána:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\configure-arthur-sports.ps1" -Launch
```

A javító először a meglévő, titkosított Odds-kulcsot olvassa be a fájlvégi sorvég
nélkül. Ha sikerül, nem kér új kulcsot. Ha nem, csak a **The Odds API** kulcsát
kéri rejtett bevitellel; a beillesztés nem látszik, Enterrel fejezd be.
Az új mentést a lemezről visszaolvassa és ellenőrzi, majd elindítja Arthurt.
Nem kell teljes telepítés, OpenAI-kulcs vagy új ChatGPT-bejelentkezés.

A másik sportkulcs célzott javítása: ugyanaz a parancs
`-Name API_FOOTBALL_KEY` kapcsolóval. Tudatos kulcscseréhez `-Replace` is megadható.
Ha a javítóhoz `-Launch` nélkül nyitottad meg a konzolt, a futó dashboardot
indítsd újra a javítás után, hogy betöltse a kulcsot.

A frissített indító kulcshibánál is megnyitja a korábbi riportokat. A konzolban
és a Működés oldalon látszik a hiányzó kapcsolat; élő adatlekéréshez működő kulcs
szükséges. Az ütemezett futás továbbra sem kér interaktív kulcsbevitelt.

## A vétó javításának telepítése meglévő Arthurra

Állítsd le a szerver PowerShell-ablakát (`Ctrl+C`), és bontsd ki az
`Arthur-v3.3-veto.zip` tartalmát a `C:\AI-Work\GoalAnalysis` mappába,
felülírva a programfájlokat. A saját konfiguráció, kulcsok és napló megmaradnak.
Ehhez a frissítéshez nem kell újra futtatni a telepítőt vagy bejelentkezni.
Indítsd az asztali parancsikonnal, vagy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\start-arthur.ps1"
```

A felületen állítsd az **Vétó / szigor** csúszkát **0–30 közé**, majd
indíts új elemzést a kívánt napra. A korábbi üres riport nem számolódik újra
pusztán az oldal frissítésétől.

Engedékeny módban hiányzó statisztika, forma vagy friss csapathír mellett is
készülhet szelvény. A régebbi, szolgáltatótól kapott ár tájékoztató szorzóként
megmarad, ellenőrzési jelzéssel. A hiányok közvetlenül a szelvényen látszanak.
Ha nem érhető el a 10× cél, kisebb szorzójú tartalék vagy egymeccses változat
is megjelenhet. A DAILY_223 címke továbbra is csak valódi 2/2/3 összeállításra
kerül. Hiányzó/érvénytelen szorzót és nem létező mérkőzést nem pótol a rendszer.

34–66 között a korábbi kiegyensúlyozott adatküszöb, 67–100 között szigorúbb
küszöb működik. A csúszka 5-ös lépésekben állítható, ezért 30 és 35 között
vált módot. A reggeli futás a mentett beállítást használja.

Arthur a hivatalos Codex CLI-n át használja a ChatGPT-fiókod Codex-keretét.
Nem kér OpenAI API-kulcsot és nem vált külön számlázott API-ra. A korábban
titkosítva elmentett OpenAI-kulcsot az új indítók nem töltik be.
A két sportadat-szolgáltató kulcsa továbbra is szükséges.

## Frissítés és telepítés

1. Állítsd le a régi Arthur-szervert a PowerShell-ablakában `Ctrl+C`-vel.
2. Bontsd ki az `Arthur-v3.2-ChatGPT.zip` tartalmát a meglévő
   `C:\AI-Work\GoalAnalysis` mappába, engedélyezve a programfájlok felülírását.
   A csomag a projekt fájljait közvetlenül tartalmazza; nem kell új almappa.
   A korábbi v3.1 javítócsomag nem szükséges.
3. Python 3.11+ és Node.js LTS/npm szükséges. A meglévő Python-környezetet a
   telepítő megtartja. Ha nincs Node.js, telepítsd a [hivatalos oldalról](https://nodejs.org/),
   majd nyiss új PowerShell-ablakot.
4. Futtasd:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\install-arthur.ps1"
```

A telepítő megőrzi a helyi konfigurációkat, eredményeket, adatbázist és mentett
sportkulcsokat. Telepíti a Python-függőségeket, futtatja a teszteket, és ha kell,
telepíti a hivatalos Codex CLI 0.155.1 verzióját. Újabb meglévő CLI-t megtart.

A Codex által megnyitott böngészőben **a meglévő ChatGPT-fiókoddal jelentkezz be**.
Az API-kulcsos belépést Arthur elutasítja. A bejelentkezést maga a Codex kezeli;
Arthur nem olvassa vagy másolja a bejelentkezési tokeneket.

Ezután a telepítő egy rövid, valódi `gpt-6-astra` feladattal ellenőrzi az elérést.
Ez a csomagod Codex-keretét fogyasztja. Siker után frissíti az asztali
`Arthur Goal Analysis` parancsikont és a napi 07:30-as Windows-feladatot,
majd megnyitja a vezérlőpultot: `http://127.0.0.1:8765`.

Hiba esetén nem regisztrál új ütemezést. A korábbi DAILY_223-feladatot csak az új
feladat sikeres létrehozása után tiltja le. A modellpróba nem igazolja az adott
nap sportadat- és szorzóellátását: ezt az első napi elemzés ellenőrzi.

## Belépés javítása, korlátok

Ha lejárt a bejelentkezés, vagy másik ChatGPT-fiókkal szeretnél belépni,
használd a hivatalos `codex logout` / `codex login` parancsokat, majd ellenőrizd:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\configure-arthur-codex.ps1"
```

A régi `configure-arthur-openai.ps1` parancs is erre irányít át.
Keretkimerüléskor Arthur leáll; várd meg a Codex-keret megújulását.
Ha a `gpt-6-astra` nem elérhető a fiókodban, ezt külön jelzi, és nem választ
automatikusan másik modellt. A ChatGPT-bejelentkezés önmagában nem igazolja
ennek a modellnek az elérését vagy a korlátlan automatizált használatot.

Az elemzés legfeljebb két Codex-feladatot indít: tömör hírek/források, majd
strukturált jelöltértékelés. Feladatonként öt perc az időkorlát; Arthur nem
indít saját ismétlést, változatlan csomagnál rövid ideig gyorsítótárat használ.
A CLI belső kapcsolat-helyreállítása okozhat ismételt hálózati próbát.
A keresésekre adott négyes cél és a régi `max_output_tokens` beállítás itt
nem kényszerített token- vagy keresési korlát. A tényleges CLI-használatot
naplózzuk, pénzbeli költséget nem találunk ki.

## Napi használat és időzítés

Az asztali parancsikonnal indítsd az alkalmazást. A szerver ablakát használat
közben tartsd nyitva; a böngésző bezárása nem állítja le. A napi ütemező a
vezérlőpulttól függetlenül indul: reggel nem kell nyitva hagyni a böngészőt.

Az alapidőpont **07:30 a Windows helyi időzónája szerint**. A futás frissíti
a függő eredményeket és elkészíti az aznapi jelölteket. Más időpont:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\install-arthur.ps1" -DailyTime "08:00" -NoLaunch
```

A gépnek bekapcsolva, internetre kapcsolódva kell lennie, és ugyanannak a
Windows-felhasználónak bejelentkezve kell maradnia. A beállítás nem ébreszti fel
a gépet. A kihagyott futást a Windows a következő megfelelő alkalommal
megpróbálja pótolni. A Codex mentett belépése szükséges; lejárt belépésnél
a felügyelet nélküli futás hibaüzenettel leáll.

Kézi futás mentett sportkulcsokkal, böngésző nélkül:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\run-arthur-scheduled.ps1"
```

Napló: `logs/arthur-scheduler.log`. Feladatütemező: `Arthur Goal Analysis Daily`.
A `-ReplaceKeys` telepítőkapcsoló csak a két sportkulcsot kéri be újra.
Ezek a `data/secrets/` mappában Windows-felhasználóhoz kötött DPAPI-titkosítással
maradnak; másik gépen/felhasználónál újra meg kell adni őket.

## Ellenőrzés határa

A Python-tesztek és a böngészős próbák nem helyettesítik a tényleges Windowsos
Codex-belépést, Astra-elérést, DPAPI-t és Feladatütemezőt. Ezeket a saját gépeden
a sikeres telepítés és az első napi futás igazolja. Fogadás továbbra is kizárólag
kézzel történik; Arthur elemzést és eredménynaplót készít.

Hivatalos háttér: [Codex-bejelentkezés](https://learn.chatgpt.com/docs/auth),
[szkriptből futtatás](https://learn.chatgpt.com/docs/non-interactive-mode).
