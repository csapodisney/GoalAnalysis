# DAILY_223 versenysorozat-univerzum — Phase 22

A napi keresés 18, explicit módon összerendelt versenysorozatot használ. Minden
bejegyzéshez API-Football ligaazonosító, The Odds API sportkulcs és versenytípus
tartozik. A ligák és kupák nem osztoznak azonosítón vagy odds-kulcson.

## Ligák

- Anglia: Premier League, Championship
- Németország: Bundesliga, Bundesliga 2
- Spanyolország: La Liga
- Olaszország: Serie A
- Franciaország: Ligue 1
- Hollandia: Eredivisie
- Portugália: Primeira Liga

## Nemzeti kupák

- Anglia: FA Cup, EFL Cup
- Németország: DFB-Pokal
- Spanyolország: Copa del Rey
- Olaszország: Coppa Italia
- Franciaország: Coupe de France

## UEFA

- Champions League
- Europa League
- Europa Conference League

A holland KNVB Beker és a portugál Taça de Portugal nincs a The Odds API
jelenlegi hivatalos sportkulcsai között. E két kupa API-Football-adatát addig
nem használjuk végső lábhoz, amíg nincs hozzájuk ellenőrzött odds-forrás és
egyértelmű eseménypárosítás. A rendszer nem helyettesíti őket más bajnokság
áraival.

A kezdeti történeti feltöltés legfeljebb 36, a napi meccslista legfeljebb 18
API-Football-hívást igényel. Az odds-foglalási keret legfeljebb 36 kredit egy
futásban, de odds-lekérés csak ahhoz a sorozathoz történik, amelyben aznap még
el nem kezdődött mérkőzés van.
