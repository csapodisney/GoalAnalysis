# Arthur telefonos elérés Tailscale-en

Telefonon, az eddig használt Tailscale-fiókkal és bekapcsolt Tailscale-kapcsolattal:

**https://desktop-jpahkrb.tail7c3a3d.ts.net:8443/**

A Windows gép legyen bekapcsolva, ébren, interneten és Tailscale-en, a HP
felhasználó legyen bejelentkezve. Az Arthur dashboard a bejelentkezés után
automatikusan elindul. A gép alvása alatt ez a helyi szolgáltatás nem érhető el.

## Helyi felépítés

```text
telefon + Tailscale
  -> HTTPS desktop-jpahkrb.tail7c3a3d.ts.net:8443
  -> Tailscale Serve (csak tailnet, nem Funnel)
  -> 127.0.0.1:8770 (külön Arthur-közvetítő)
  -> 127.0.0.1:8765 (meglévő Arthur dashboard)
```

A Project 2030 változatlanul a 443-as porton marad:
`https://desktop-jpahkrb.tail7c3a3d.ts.net/`, a meglévő 8001-es helyi közvetítővel.

Arthur eredeti Host/Origin-védelme elutasítja a közvetlen külső címet. A külön
közvetítő a pontos `desktop-jpahkrb.tail7c3a3d.ts.net:8443` Host értéket és a
Tailscale által igazolt `csapodisney@github` fiókot ellenőrzi. A megfelelő HTTPS
Origin értéket alakítja át a backend számára; idegen Origin tiltott. A meglévő
Arthur munkamenettoken változatlanul jut el a backendhez, amely továbbra is
ellenőrzi. Kulcsot vagy új hitelesítő adatot a közvetítő nem tárol és nem tölt be.

A közvetítő kizárólag loopbacken figyel. A Tailscale hivatalos dokumentációja
alapján a Serve a kliens által beküldött Tailscale-azonosító fejléceket eltávolítja,
és az igazolt felhasználó adataival helyettesíti. A közvetlen helyi folyamatok
bizalmi határa ugyanaz a Windows-felhasználó, mint a meglévő Arthur esetén.
Forrás: [Tailscale Serve, identity headers](https://tailscale.com/docs/features/tailscale-serve#identity-headers).

## Indítás és ellenőrzés

A `Arthur-Dashboard` Windows-feladat a HP bejelentkezése után 10 másodperccel
indul, rejtett ablakban, böngészőnyitás nélkül. A meglévő Arthur-indítót használja
és a már mentett hozzáféréseket csak beolvassa. Ha a helyi dashboard már fut,
nem indít második példányt. Idegen vagy hibás szolgáltatás által foglalt portnál
biztonságosan megáll; semmilyen meglévő folyamatot nem állít le.

A feladat kizárólag a dashboardot indítja. Az elemzések és az esti ütemezés a
korábbi beállítások szerint működnek. A saját indítási naplója:
`logs/arthur-dashboard-startup.log`; ez csak rögzített eseménykódokat és
kilépési állapotot tartalmaz.

A `Arthur-Tailscale-Gateway` Windows-feladat a HP felhasználó bejelentkezésekor,
ablak nélkül, közvetlen `pythonw.exe` folyamattal indul. Nem kér jelszót, nem
használ emelt jogosultságot, és nem
módosítja az Arthur napi ütemezését. A Serve konfiguráció háttérben fut.

A válaszméret korlátja 32 MiB; a jelenlegi állapotválasz körülbelül 12,3 MB.
A közvetítő legfeljebb nyolc egyidejű kérést kezel, rögzített helyi upstreammel
és időkorlátokkal. A telefonos adatforgalom a meglévő dashboard teljes
állapotválaszát követi; a szelvénylogika és az adatok változatlanok.

Kézi közvetítőindítás, ha a feladat nem fut (ne indíts második példányt):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AI-Work\GoalAnalysis\scripts\start-arthur-tailscale.ps1" -Hostname "desktop-jpahkrb.tail7c3a3d.ts.net" -AllowedLogin "csapodisney@github"
```

Hálózati konfiguráció ellenőrzése:

```powershell
& 'C:\Program Files\Tailscale\tailscale.exe' serve status
Get-ScheduledTask -TaskName 'Arthur-Dashboard'
Get-ScheduledTaskInfo -TaskName 'Arthur-Dashboard'
Get-ScheduledTask -TaskName 'Arthur-Tailscale-Gateway'
Get-NetTCPConnection -State Listen -LocalPort 8765,8770,8443
```

A 8765 és 8770 csak `127.0.0.1` címen figyelhet. A 8443 csak a Tailscale
IPv4/IPv6-címeken figyelhet. A Windows tűzfalat nem kell kikapcsolni; nincs új
LAN/publikus engedélyező szabály vagy routeres porttovábbítás.

Ha az Arthur nem fut, a közvetítő 502-es választ adhat. A bejelentkezés utáni
első másodpercekben ez átmenetileg normális. Kézi indítás vagy újrapróbálás:

```powershell
Start-ScheduledTask -TaskName 'Arthur-Dashboard'
```

403 esetén ellenőrizd a pontos HTTPS-címet, a Tailscale-fiókot
és a telefon Tailscale-kapcsolatát. Az IP-cím helyett a fenti nevet használd a TLS
tanúsítvány és a Host-védelem miatt.

## Kikapcsolás

Csak az Arthur távoli elérésének leállítása, a Project 2030 megőrzésével:

```powershell
& 'C:\Program Files\Tailscale\tailscale.exe' serve --https=8443 off
Stop-ScheduledTask -TaskName 'Arthur-Tailscale-Gateway'
Disable-ScheduledTask -TaskName 'Arthur-Tailscale-Gateway'
```

Újraengedélyezés:

```powershell
Enable-ScheduledTask -TaskName 'Arthur-Tailscale-Gateway'
Start-ScheduledTask -TaskName 'Arthur-Tailscale-Gateway'
& 'C:\Program Files\Tailscale\tailscale.exe' serve --bg --https=8443 http://127.0.0.1:8770
```

Ne használd a `serve reset` parancsot: az a Project 2030 meglévő elérését is
törölné. Ne állíts be Funnelt ehhez a dashboardhoz.

Csak a dashboard automatikus indításának kikapcsolása (a futó dashboard és a
napi elemzés megőrzésével):

```powershell
Disable-ScheduledTask -TaskName 'Arthur-Dashboard'
```

Visszakapcsolás: `Enable-ScheduledTask -TaskName 'Arthur-Dashboard'`.

## Ellenőrzés állapota

A 2026-10-14-i ellenőrzési eredmények és a telefonos próba bizonyítékának
határai a `CURRENT_STATE.md` megfelelő bejegyzésében szerepelnek.
