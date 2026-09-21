# Phase 18 telepítési javítás

A jelzett hiba: a Python elindult, de nem találta a
`scripts/verify-master-prompt.py` fájlt. A 18. fázis eredeti ZIP-je csak az
új/módosított fájlokat tartalmazta; az ellenőrző szkript és betöltője a 14.
fázisból származik. A telepítési útmutató ezt az előfeltételt nem ellenőrizte.

A `GoalAnalysis-phase18-recovery.zip` a 14–18. fázis fájljait egyesíti,
mindig az adott fájl legutóbbi változatával. Nem kell külön egymás után
kicsomagolni az öt korábbi ZIP-et. A 13. fázis és annak előzményei szükségesek.
A csomag a projekt gyökerébe bontandó ki: nincs közbeiktatott felső mappa.

Telepítés előtt a meglévő `scripts`, `src`, `config`, `tests`, `docs` és
`CURRENT_STATE.md` másolata a repón kívüli, egyedi mentési könyvtárba kerül.
A javítás nem módosítja a `.venv`-et, a Git előzményeit, a környezeti
változókat vagy a rögzített napi riportokat, és nem hív fizetős API-t.
Meglévő kódfájlokat a csomag szerinti változatra állít: egyedi módosítás
esetén annak összevetéséhez a mentett példány megmarad.

A v2 és v2.1 master promptok byte-tartalma változatlan. Mindkét verzió
könyvtárában `.gitattributes` őrzi a byte-alapú hash-t a Git automatikus
Windows-sorvégkonverziójától.

Ellenőrzés a projekt gyökerében:

```powershell
.\.venv\Scripts\python.exe scripts\verify-master-prompt.py --version arthur-pentagram-v2
.\.venv\Scripts\python.exe scripts\verify-master-prompt.py --version arthur-pentagram-v2.1
.\.venv\Scripts\python.exe -m pytest -q
```

Az általunk összeállított 18. fázis tesztkészlete 133 tesztet tartalmaz.
A hiányzó 14. fázis telepítését helyben reprodukáltuk, majd az egyesített
csomaggal mindkét prompt ellenőrzése és a 133 teszt is lefutott. Ez Linuxon
végzett ellenőrzés; a felhasználó Windows-környezetének eredménye külön szükséges.
