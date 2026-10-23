# Meccskontextus: mit építünk be, és mit csak mérünk

Frissítve: 2026-10-16.

## Beépíthető, forrásolható tényezők

- **Versenytípus:** liga, kupa, UEFA-klubtorna és válogatott mérkőzés külön
  kontextus. Arthur már minden jelöltnél tárolja a `competition_type` mezőt.
- **Mérkőzés tétje:** a kiesés, az európai helyezés, a feljutás és a
  helyezéshez kötött pénzdíj csapatspecifikus ösztönző. A helyes mérőszám nem
  egy kézzel adott "motiváció" címke, hanem annak változása, hogy győzelem és
  vereség esetén mekkora az egyes szezonvégi következmények valószínűsége.
- **Rotáció:** kupában és sűrű programban lehet fontos, de csak hivatalos keret,
  kezdő vagy megbízható csapathír alapján. A versenytípus önmagában nem bizonyít
  B csapatot.
- **Válogatott mérkőzések:** külön rangsor- és csapaterő-modell indokolt. A
  kiszámíthatatlanság önmagában nem teszi értékessé a nagy oddsot.

## Egyelőre csak mérendő hipotézisek

- "Péntek kaotikusabb", "szombaton több a gól", "vasárnap visszafogottabb".
  Ezekre nem találtunk elég erős, általánosítható elsődleges bizonyítékot.
  A hét napját naplózzuk, de addig nem módosít pontszámot, amíg Arthur saját,
  ligánként és piactípusonként bontott, megfelelő mintája nem igazolja.
- "Kevés meccsből könnyebb 30 nagy szorzójú szelvényt választani". A kisebb
  kínálat valójában nagyobb korrelációt és gyengébb választási szabadságot is
  okozhat; ezért kevés napon megengedett az abstention.

## Következő, biztonságosan mérhető fejlesztés

1. A tabella és a versenyszabály alapján számoljuk ki csapatonként a
   győzelem–vereség közötti szezonvégi következménykülönbséget.
2. A rotációs jel csak időbélyegzett, forrásolt keret- vagy kezdőhír után legyen
   aktív, és lejárjon a meccs után.
3. A hét napja csak elemzési dimenzió legyen; legalább liga × piac × nap bontás,
   konfidenciaintervallum és időben elkülönített visszateszt után kaphat súlyt.
4. A kontextusjelek se kerülhessék meg a pozitív érték-, adatfrissességi,
   kitettségi és publikálási zárolást.

## Elsődleges források

- Cisyk, Courty és Kouhbor (2025), *Are English Premier League Teams Paid Like
  Bureaucrats?* — a mérkőzés pénzügyi tétje csapat- és meccsspecifikusan
  számolható a helyezési pénz, UEFA-kvalifikáció és kiesés alapján:
  https://journals.sagepub.com/doi/10.1177/15270025251348181
- Chmura és mtsai. (2016), *Seasonal Pacing – Match Importance Affects Activity
  in Professional Soccer* — a meccs fontossága a szezonvégi következmény
  valószínűségének győzelem–vereség különbségével operacionalizálható:
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0157127
- *Match running performance and technical performance with and without ball of
  the top three ranked teams in the 2018 FIFA World Cup* — a rotáció
  kontextusfüggő, és nem szükségszerűen jelent azonos teljesítményromlást:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC10870587/
- Ley, Van de Wiele és Van Eetvelde (2024), *Alternative ranking measures to
  predict international football results* — válogatott mérkőzésekhez explicit
  rangsor- és predikciós modellek használhatók:
  https://arxiv.org/abs/2405.10247
- *Bayesian outcome-specific ensemble modeling for football outcome prediction*
  (2026) — az időben elkülönített teszten a döntetlenek továbbra is nehezen
  jelezhetők; a valós bizonytalanságot a rendszernek meg kell őriznie:
  https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2026.1754408/full

