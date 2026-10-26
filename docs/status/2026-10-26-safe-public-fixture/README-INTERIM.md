# Biztonságos publikus stadium tesztminta — köztes forráscheckpoint

Külön TESTONLY metaadat-projekció készült a már követett69stadionos metaadatból. Egy128352bájtos,helyben maradó HTML helyett381bájtos,olvasható JSON-identitásprojekció; az új testpacket és forrás külön valódi hash-t és lineage-ot kapott. A builder nem nyitotta meg/nem kódolta a védett HTML-t. Eredeti raw/hash/gold/productionpin/összesrégi teszt változatlan.

Nincs productionforrás/config/rank/model/READYkapu változás vagy sport/LLM-kérés. A fixture csak izolált tesztrootban,explicit pytestmonkeypatch mellett használható; a productionpin szándékosan elutasítja. Más források eredeti nyersbájtjai továbbra a követett archívumokból jönnek. Másolt történeti időmezők teszt-szcenáriók,nem az újprojekció történeti megfigyelési receiptjei.

A26újkontrollból a korábbi25PASS volt. A régi metaadat néhol nem közöl bytehosszt; a helper hash-t és bytekorlátot ellenőriz,a deklarált hosszt csak valódi mező esetén,hiány nem0. Egy újteszt kezdetben az egyetlen új,megmért381bájtos projekcióra is az eredeti ismeretlenhosszt követelte; ezt elkülönítve javítottuk,a regresszió1PASS. Minden korábbi hiba/XML/log megmaradt. A végleges26publikus kontroll tracked-clone ellenőrzése és saját5848full+3újactualreplay még hátra van. Ez nem készscope.

Az eredeti privát archívum szigorú rawbyte/hash tesztjei nem írhatók át a projekció hash-eire. A publikus újkontrolloknak a privát raw fizikailag hiányzó tracked-clone-ban is futniuk kell. A régi,eredeti HTML-byteazonosságot ellenőrző tesztek továbbra a helyi privát archívumot igénylik; az önellátó egészpublikusclone/fullsuite-t nem állítjuk késznek. Sem gzip/base64 megkerülés,sem byte/hash hamisítás nincs.

Munkalista137lezárt/30nyitott/167. Sensitivity-note saját5822full/runtime/root/scope f7d2b9ce2861a562ea1fb30edf58fea91a2e49fe remoteSHA-val lezárt. Teljesprojekt/eredeti215frissaudit még nyitott,a régi160/55 nem frisskészség.
