# Arthur — szelvényképzés, kivételek és árfigyelés

2026. október 15. A módosítások a helyi kódba be vannak építve; az alábbi részleteket nem kell újra bemásolni. A régi jelentéseket és rögzített napi csomagokat nem írjuk át.

## Pontos fájlhelyek

Minden útvonal a `C:\AI-Work\GoalAnalysis` repóhoz viszonyított.

| Feladat | Fájl / belépési pont |
|---|---|
| Pontszám normalizálása és hosszúsági tényező | `src/goal_analysis/engine/ticket_scoring.py`: `normalize_score`, `ticket_score`, `ticket_quality` |
| Specialista többmeccses alternatívák | `src/goal_analysis/engine/portfolio.py`: `_specialist_scoring`, `_specialist_options` |
| Közös, korlátos kombinációkeresés | `src/goal_analysis/engine/ticket_options.py`: `bounded_ticket_groups` |
| Numerikus profilok | `src/goal_analysis/engine/model_recommendations.py`: `_normalized_support`, `_options` |
| Klubprofilok és három contrarian karakter | `src/goal_analysis/tipsters/catalog.py`, `tipsters/recommendations.py`: `_contrarian_analysis`, `_ranked`, `_options` |
| Duplikáció, hasonlóság, végleges csomag | `src/goal_analysis/engine/diversification.py`: `ticket_signature`, `ticket_similarity`, `ready_exposure`, `allocate` |
| Forrásolt vétófelülbírálás | `src/goal_analysis/engine/veto_overrides.py`: `verified_veto_override` |
| Astra strukturált válasz és forrásellenőrzés | `src/goal_analysis/agents/astra_review.py` |
| Ármozgás bizonyítéka | `src/goal_analysis/engine/odds_movement.py` |
| 15 perces publikálási zár | `src/goal_analysis/engine/publication_window.py` |
| Előzetes és végső ellenőrzések összekötése | `src/goal_analysis/jobs/portfolio.py` |
| Oddsfigyelő állapot, cache, keretek | `src/goal_analysis/jobs/odds_monitor.py`: `MonitorPolicy`, `OddsMonitor.tick` |
| API-Sports / The Odds API adapterek | `src/goal_analysis/jobs/odds_monitor_providers.py` |
| Meglévő Windows-ellenőrző bekötése | `scripts/run-arthur-odds-monitor.py`, `run-arthur-scheduled.ps1`, `register-arthur-schedule.ps1` |
| Dashboardon a két indoklás és a kivétel | `src/goal_analysis/web/app.js`: `exceptionalAnalysis` |

## 1. Rangsorolás — a DAILY változtatása nélkül

A specialisták eredeti támogatási pontszáma 100-as skálán van. A normalizálás ezért állandó 100-as osztót használ; a gyenge készlet legjobb eleme nem válik automatikusan 100%-ossá.

```python
# engine/ticket_scoring.py — a megvalósított képlet lényege
normalized = [normalize_score(value, scale=100) for value in raw_support]
score = math.fsum(normalized) / len(normalized) * math.sqrt(len(normalized))
```

A numerikus valószínűség eleve 0–1 közötti; a pozitív becsült előny külön `edge / (1 + edge)` skálát kap. A klub külön normalizálja a modell-, történeti, helyszíni és preferenciaösszetevőket, utána alkalmazza a profilsúlyokat. Az eredeti `model_probability` és `estimated_edge` nem változik.

A végeredmény **rangsorolási heurisztika, nem szelvényszintű nyerési valószínűség**. A √n tényező tudatos preferencia, nem bizonyított optimális fogadási képlet. A korábbi adat-, ár-, mintaméret- és modellkapuk megmaradnak. Egy szelvényen egy mérkőzésből egy választás lehet, egységes iroda/régió/pénznem mellett. Nincs 10-es alsó összszorzó vagy kötelező lábszám.

A keresés korlátos és determinisztikus: a legerősebb különböző hosszúságú kombinációkat és rövid tartalékokat is megőrzi. Nem állítjuk, hogy minden lehetséges összeállítást kimerítően vizsgál.

## 2. Duplikáció és lágy átfedési büntetés

Azonos teljes választáshalmaz esetén az alacsonyabb rangú profil a következő alkalmas alternatívát próbálja. A személynév vagy más fogadóiroda nem teszi új ötletté ugyanazt a szelvényt. Eltérő piac/kimenetel külön választás; a közös meccs továbbra is kitettség.

```python
# engine/diversification.py
fixture_jaccard = len(candidate_fixtures & selected_fixtures) / len(
    candidate_fixtures | selected_fixtures
)
candidate_overlap = len(candidate_fixtures & selected_fixtures) / len(candidate_fixtures)
penalty_factor = 0.70 if candidate_overlap > 0.50 else 1.0
adjusted_score = score * penalty_factor
```

A két mutató szándékosan külön szerepel: ABC és ABD Jaccard-indexe 2/4 = 0,5, az új szelvény meccseinek átfedése 2/3. A kért példa alapján az utóbbi váltja ki a büntetést. ABC és ADE csak 1/3 arányban fed át, ezért nem kap büntetést. A büntetés egyszer alkalmazott tényező, nem minden hasonló profil után újra felszorzott levonás. A magasabb eredeti rangú kiválasztott szelvény az összehasonlítás alapja.

## 3. Kitettség a tényleges READY főcsomagban

```python
# engine/diversification.py: ready_exposure
ready = [ticket for ticket in tickets if ticket.get("status") == "READY"]
allowed = max(1, math.floor(len(ready) * 0.50)) if ready else 0
```

Két vagy három kész főszelvénynél egy meccs egyszer szerepelhet; négy vagy öt kész főszelvénynél kétszer. Az egyetlen kész szelvény külön jelzett kivétel: ott az 50%-os korlát matematikailag teljesíthetetlen, a védett DAILY-t nem töröljük emiatt.

A nevezőt nem növelik a kutatási/probaajánlatok, a DRAFT-kombinációk és a szelvények ajánláskártya-másolatai. A rendszer közösen keresi a végleges csomagot, majd annak tényleges darabszámával is ellenőrzi a korlátot. A következő legjobb teljes alternatívát választja vagy tartózkodik. Hiányos review-jú DRAFT nem kerülhet a kész főcsomagba.

## 4. Contrarian profilok

Három meglévő karakter alakult át: **Érték Milán, Remi Doma, Merész Hunor**. Nincs három új, külön betanított modell.

Kötelező az azonos meccs/piac/kimenetel/időszak/elszámolás/iroda/régió/pénznem/szolgáltató szerint összehasonlítható árpár. A korábbi szolgáltatói időbélyeg 24–48 órával korábbi, az emelkedés legalább 10%. Ez a „jelentős” mozgás választott, módosítható heurisztikus határa, nem statisztikailag validált optimum.

```python
# tipsters/recommendations.py: _contrarian_analysis
expected_return = probability * current
if expected_return <= policy["contrarian_min_expected_return"]:  # 1.15
    return None
```

Legalább egy megfelelő GAUSS/LAPLACE becslés, a meglévő modellkapuk és tényleges strukturált Astra-válasz is kell. A dashboardon megjelenik:

1. **Miért megy szembe az árral?** Ármozgás és korlátai; az ok forrás hiányában ismeretlen.
2. **Mi a matematikai alátámasztás?** Modellbecslés, aktuális ár és a p × odds számítás.

Az emelkedő odds önmagában nem igazolja a lakossági fogadási irányt. Hiányzó árpár, modell vagy Astra-indoklás esetén a profil tartózkodik. Az árfigyelő külön helyi előzményt gyűjt; nem ír vissza korábbi jelentésbe.

## 5. Astra Veto-Override

```python
# A fogyasztói oldal mintája; a bizonyíték teljes ellenőrzése a helperben van.
proof = verified_veto_override(candidate, review=actual_astra_review, now=now)
if proof and "LEAGUE_BASE_RATE" in proof["gates"]:
    # A megnevezett puha gyakorisági küszöb kivétele engedhető.
    candidate["veto_override"] = proof
```

A puszta `True` flag nem elegendő. Szükséges az adott jelöltet és aktuális árat azonosító lenyomat, COMPLETE review, támogató értékelés, konkrét indok és ténylegesen visszakeresett, hivatkozott forrás. Csak két megnevezett puha ok írható felül: `HIGH_ODDS`, `LEAGUE_BASE_RATE`.

A magas odds kivétele a konkrét jóváhagyott klubválasztást engedi át a karakter ársávján, egymeccses formában. Nem jelent korlátlan kombinált szorzóengedélyt. A történeti gyakorisági kivételhez a valódi, megfelelő mintájú és forrásazonosított történet továbbra is kell; hiányzó történetet egy szöveg nem pótolhat. Az eredeti pontszámot nem emeljük meg.

Ár-/meccsazonosítás, hiányzó minták, időbélyegek, modellküszöbök, contrarian 1,15-ös kapu, csapatjogosultság, kitettségi korlát és a DAILY szabályai nem kerülhetők meg.

## 6. Az árfigyelő ciklus és a 15 perces zár

```python
# jobs/odds_monitor.py: MonitorPolicy
interval_seconds = 1800       # ugyanaz a külső lekérdezés: legalább 30 perc
lockout_seconds = 900         # kezdés előtt 15 perc

# engine/publication_window.py
allowed = kickoff > now + timedelta(minutes=15)  # pontosan 15 percnél már zárt
```

A meglévő Windows-ellenőrző helyi ébresztése egyperces. Ez nem egyperces API-hívás: a tartós cache az utolsó **sikeres** lekérdezés befejezésétől számít 1800 másodpercet. A helyi ellenőrző felbontása és a keretek miatt a tényleges frissítés később történhet. A 15 perces korábbi ébresztés a befejezési idő kis eltérése miatt akár 45 perces frissítéseket is okozhatott volna.

A lekérdezési kulcs a tényleges szolgáltatói végpontot és paramétereket azonosítja. A mérkőzéslista változása önmagában nem nullázza az azonos kérés cache-ét. Tartós, tranzakciós foglalás akadályozza a párhuzamos dupla lekérést. A percenkénti és napi helyi keret, a szolgáltatói maradék, a 429/Retry-After és a hibák utáni várakozás mind megelőzi az új kérést. A hiba nem számít sikeres frissítésnek.

Az árfigyelés nem hív Astrát és nem generál/rögzít új szelvényt. Az Astra a meglévő szelvénygenerálási folyamat része marad. A zárolás kérés előtt és után, a szelvényvizsgálat előtt és a végső publikálásnál is érvényes; egy lezárt láb miatt az egész új szelvény kiesik. Az időszűrő a DAILY algoritmusán kívüli publikálási védelem, nem annak új kiválasztási szabálya.

A 30 perc kiinduló beállítás, nem mért átlagos piaci ingadozási ablak vagy ellenőrzött előfizetési optimum. A szolgáltatói adatfrissítés és a saját lekérdezési gyakoriság külön fogalom. A kvóta fogyásakor ritkább frissítés vagy szünet következik, nem kerettúllépésre törekvő újrapróbálás.

Hivatalos háttér: [The Odds API kvóták és válaszfejlécek](https://the-odds-api.com/liveapi/guides/v4/index.html), [szolgáltatói frissítési ütemek](https://the-odds-api.com/sports-odds-data/update-intervals.html), [API-Football rate limit](https://www.api-football.com/news/post/how-ratelimit-works).

## Ellenőrzési bizonyíték

- `analysis/soft-overlap-20261015/protected-daily-verification.json`: ugyanazon mentett bemeneten teljes DAILY-szelvény és ajánlás egyezik; hét védett fájl bytehash-e változatlan.
- `analysis/soft-overlap-20261015/saved-input-replay.json`: a régi 27, majd szigorúan szűrt 2 helyett az új szabállyal 16 profilajánlat, ebből 10 klubprofil; 1–4 lábú összeállítások. Ez offline összehasonlítás, nem új napi ajánlat vagy 16 kész főszelvény. A READY főcsomagban a védett DAILY marad; a többi státusza kutatási/előkészítési.
- A működési telepítés és az összesített teszt eredményét a `CURRENT_STATE.md` aktuális bejegyzése rögzíti.
