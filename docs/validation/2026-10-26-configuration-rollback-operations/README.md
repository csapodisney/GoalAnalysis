# 11.7 műveleti ellenőrzők — pontos referenciamásolatok

A manifest minden fájlt az eredeti elfogadási könyvtárához és byteSHA-jához köt. A végleges alkalmazáskód a szokásos src/scripts/tests útvonalakon van; ezek a saját elfogadás és üzemi adatmegőrzési bizonyítás kiegészítő segédjei.

A műveleti entrypointok kizárólag az eredeti privát artifactgyökérben, a saját5776elfogadás/forrás/napló/megőrzött inputfeltételekkel használhatók. A reviewmásolatok áthelyezett könyvtárából nem indítunk üzemi visszaállítást. Nem tartalmaznak API-kulcsot vagy privát adatbázis-/riporttartalmat.

A generikus fizikai adatmegőrző ellenőrző13külön tesztje önálló ideiglenes SQLite/WAL-adatbázisokon futtatható:

```powershell
.venv/Scripts/python.exe -B -m pytest -q docs/validation/2026-10-26-configuration-rollback-operations/test_physical_preservation_artifact.py
```

Ez a metaadatellenőrző reprodukciója, nem a teljes sportadatcsomag vagy teljesprojekt clean-clone elfogadása. Az eredeti titkosmintára hasonlító külső stadionfixture biztonságos publikus reprodukciója külön következő feladat.

Módszer: minden nyersfájl, SQLite-main és teljes WAL exactbyteSHA-ja és tranzakciós schema/COUNT. A bájtazonosság minden committedsort lefed, sor-mintavétel vagy nemvégzett per-rowhash-állítás nélkül. SHM/runtime-lock koordinációs állapot külön szerepel. Checkpointból eredő fizikai eltérés kötelező szemantikai felülvizsgálatot igényel; nem automatikus elfogadás vagy adatvesztés-állítás.
