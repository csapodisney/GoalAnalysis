# Codex advisory

Arthur kiegészítő kockázati és klubstílus-értékelése a hivatalos Codex CLI-n,
a gépen meglévő ChatGPT-bejelentkezéssel fut. Nem használ NVIDIA/Nemotron vagy
OpenAI Platform API-kulcsot, és nincs fizetős API-visszaesés.

Az engedélyezés helyi fájlja `config/codex-advisory.json`. A mintafájl:
`config/codex-advisory.example.json`. A mentett eredmények külön adatbázisokba
kerülnek: `data/arthur/codex-advisory.sqlite3` és
`data/arthur/codex-stylist.sqlite3`.

A Codex csak Arthur által előre létrehozott és ellenőrzött alternatívák közül
választhat. Nem találhat ki meccset, piacot, szorzót, valószínűséget vagy tétet.
Hiba, keretkorlát vagy érvénytelen strukturált válasz esetén Arthur megtartja a
helyi rangsort, és a már archivált szelvényeket nem módosítja.

Ellenőrzés:

```powershell
.venv\Scripts\python.exe scripts\run-arthur.py --check-codex-login
.venv\Scripts\python.exe scripts\run-arthur.py --check-openai
```

Mentett futás külön értékelése:

```powershell
.venv\Scripts\python.exe scripts\run-arthur-codex-advisory.py --run-id <run-id>
```

A dashboard a csak olvasható `GET /api/codex-advisory` végpontot használja.
Az örökölt `/api/nemotron` végpont és indító átmenetileg kompatibilitási alias;
ezek is kizárólag Codexet hívnak.
