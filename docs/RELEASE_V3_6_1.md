# Arthur v3.6.1 — a talált szorzók teljes felhasználása

Minden érvényes, mérkőzéshez és piachoz párosított API-szorzó bekerül a
számításba, a szelvényekbe, Astra bemenetébe és a mentett ajánlatokba. Ez minden
vétószinten működik. The Odds API és API-Football esetén is érvényes.

A régi, jövőbeli, hiányzó vagy hibás időbélyeg nem kizárási ok, nem csökkenti az
ajánlat minősítését és nem teszi tervezetté a kész szelvényt. A felületen csak
semleges információ jelenik meg, például „Nem friss adat · a szorzó szerepel a
számításban.” Az eredeti időbélyeg megmarad, az alkalmazás nem írja át frissre.
Astra hosszabb futása és a későbbi oldalfrissítés sem érvényteleníti az árat.

A profilajánlat összszorzója akkor is megjelenik, ha nem került a kijelölt napi
szelvények közé: minden meglévő árat felhasznál az elméleti szorzathoz.

A hiányzó piacokra továbbra is fut a meglévő API-Football pótlási ág, a meglévő
kulccsal és hívási kerettel. A változás nem igényel előfizetést, extra modellhívást
vagy új beállítást. Csak a ténylegesen megtalált szorzóból számol.

## Frissítés

1. Zárd be Arthur futó szerverének PowerShell-ablakát.
2. Töltsd le az `Arthur-v3.6.1-frissites.ps1` fájlt, majd a letöltés mappájában futtasd:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Arthur-v3.6.1-frissites.ps1
```

3. Indíts új elemzést a kívánt napra, a megszokott vétószinttel.

A frissítő biztonsági mentést készít, ellenőrzi a programfájlokat, majd elindítja
Arthurt. A kulcsok, bejelentkezés, beállítások, adatbázis és jelentések megmaradnak.
Az előző elemzések naplója változatlan: az új működés az új futásban látszik.

## Ellenőrzés

A regressziótesztek mindkét API-t ellenőrzik 0, 35 és 100 szigorúságnál, 30 napos,
jövőbeli, üres, hiányzó, hibás és időzóna nélküli időbélyeggel. Azonos árak és
adatok mellett az ajánlat, összszorzó, státusz és minősítés megegyezik a friss
időbélyeggel kapott eredménnyel. A próbák Astra bemenetét, az SQLite-mentést,
a dashboard visszaolvasását és az exportot is ellenőrzik.

A szolgáltatói válaszok és modellválaszok tesztadatok; élő sportkulcs és a
felhasználó Windows-telepítése ebben a környezetben nem érhető el.

453 Python-teszt sikeres; a Windows DPAPI-próba Linuxon kihagyva. Ruff, JavaScript
szintaxis és Chromium asztali/mobil ellenőrzés sikeres.
