"""Pinned, license-checked methodology sources for fictional club styles.

Arthur does not execute downloaded repository code here.  The catalogue adapts
transparent selection ideas to the models and evidence that already exist in
the local pipeline.  A style must abstain when its declared inputs are absent.
"""

from __future__ import annotations

from copy import deepcopy

SOURCE_AUDIT_DATE = "2026-10-16"
STYLE_VERSION = "arthur-open-source-style-map-v2-accumulators"

OPEN_SOURCE_SOURCES = {
    "penaltyblog": {
        "name": "penaltyblog",
        "url": "https://github.com/martineastwood/penaltyblog",
        "commit": "6a51173454cba172f2e7b2d8374c1770f257b86a",
        "license": "MIT",
        "use": "Poisson, Dixon–Coles, bivariate Poisson and Pi-rating model families.",
    },
    "the_open_model": {
        "name": "The Open Model",
        "url": "https://github.com/Hicruben/theopenmodel",
        "commit": "a4f1239a2bc8d0550408a5f8ee1cebb2f1815138",
        "license": "MIT",
        "use": "Rating plus goal-model consensus and pre-kickoff snapshot discipline.",
    },
    "odds_calibration": {
        "name": "odds-calibration",
        "url": "https://github.com/vladapl21/odds-calibration",
        "commit": "d147452283f50da35ad51983c4a2c9cfc96e5fbd",
        "license": "MIT",
        "use": "De-vigged market comparison, minimum-edge gates and chronological validation.",
    },
    "footai": {
        "name": "footAI",
        "url": "https://github.com/pmatorras/footAI",
        "commit": "661b85abf1d2cff2fd01d732d8529d2a67ec4664",
        "license": "MIT",
        "use": "Rating and recent-form perspectives kept separate from goal-model signals.",
    },
}


def style(identifier: str, title: str, family: str, summary: str, *source_ids: str) -> dict:
    """Return immutable-looking JSON data with complete source provenance."""
    if not source_ids or any(source_id not in OPEN_SOURCE_SOURCES for source_id in source_ids):
        raise ValueError("Every style requires audited open-source provenance")
    return {
        "style_id": identifier,
        "title": title,
        "family": family,
        "summary": summary,
        "version": STYLE_VERSION,
        "source_audit_date": SOURCE_AUDIT_DATE,
        "sources": [deepcopy(OPEN_SOURCE_SOURCES[source_id]) for source_id in source_ids],
        "implementation": "LOCAL_ADAPTATION_NO_DOWNLOADED_RUNTIME_CODE",
    }


STYLES = {
    "consensus_accumulator": style(
        "consensus_accumulator", "Modellkonszenzus-kombináció", "CONSENSUS",
        "Profilra szabott meccsszám és összszorzó; minden lábon külön modellkonszenzus.",
        "the_open_model", "penaltyblog",
    ),
    "goal_accumulator": style(
        "goal_accumulator", "Gólpiaci kombináció", "GOAL_GRID",
        "A profil saját gólpiacaiból, két modell alátámasztásával épített kombináció.",
        "penaltyblog", "odds_calibration",
    ),
    "rating_accumulator": style(
        "rating_accumulator", "Ratingalapú kombináció", "RATING_FORM",
        "Pi-rating és pozitív modellérték a profil saját hazai vagy vendég piacán.",
        "penaltyblog", "footai", "odds_calibration",
    ),
    "venue_accumulator": style(
        "venue_accumulator", "Forma és helyszín", "RATING_FORM",
        "Helyszíni előzmények és rating támogatják a kombináció egyedi választásait.",
        "penaltyblog", "footai",
    ),
    "market_accumulator": style(
        "market_accumulator", "Piaci értékek kombinációja", "MARKET_VALUE",
        "Egyedi modellértékkel rendelkező tippek a profil saját történeti vagy diverzitási súlyaival.",
        "odds_calibration", "penaltyblog",
    ),
    "strict_accumulator": style(
        "strict_accumulator", "Szigorú hosszabb kombináció", "CONSENSUS",
        "Magasabb összszorzó, minden lábon külön szigorú számszerű értékkapuval.",
        "the_open_model", "odds_calibration", "penaltyblog",
    ),
    "contrarian_accumulator": style(
        "contrarian_accumulator", "Ellenáramú kombináció", "CONTRARIAN_VALUE",
        "Minden lábnál igazolt áremelkedés, modellérték és forrásolt felülvizsgálat.",
        "odds_calibration", "penaltyblog",
    ),
    "consensus_single": style(
        "consensus_single", "Közös modelljel", "CONSENSUS",
        "Egy választás, ha a rating- és gólmodellek közel azonos következtetésre jutnak.",
        "the_open_model", "penaltyblog",
    ),
    "goal_value_single": style(
        "goal_value_single", "Gólmodell-érték", "GOAL_GRID",
        "Egy gólpiac, amelyet két eltérő Poisson-család és pozitív árérték támogat.",
        "penaltyblog", "odds_calibration",
    ),
    "goal_value_double": style(
        "goal_value_double", "Két független gólkép", "GOAL_GRID",
        "Legfeljebb két külön mérkőzés erős gólpiaci jelből; gyenge második láb nélkül.",
        "penaltyblog", "odds_calibration",
    ),
    "rating_value_single": style(
        "rating_value_single", "Rating és forma", "RATING_FORM",
        "Pi-rating, időrendi forma és pozitív árérték alapján egyetlen oldal.",
        "penaltyblog", "footai", "odds_calibration",
    ),
    "venue_value_double": style(
        "venue_value_double", "Helyszíni forma-pár", "RATING_FORM",
        "Legfeljebb két külön mérkőzés, ahol a ratinget a helyszíni előzmény is erősíti.",
        "penaltyblog", "footai",
    ),
    "market_value_single": style(
        "market_value_single", "Piaccal mért érték", "MARKET_VALUE",
        "A modell és a teljes, marzstól tisztított piac közti eltérést keresi.",
        "odds_calibration", "penaltyblog",
    ),
    "market_value_double": style(
        "market_value_double", "Két piaci eltérés", "MARKET_VALUE",
        "Két külön mérkőzés pozitív modell–piac eltéréssel és egyedi értékkel.",
        "odds_calibration", "penaltyblog",
    ),
    "strict_treble": style(
        "strict_treble", "Szigorú hármas", "CONSENSUS",
        "Három lábat kedvel, de csak külön-külön erős értékjelből; rövidebbet is elfogad.",
        "the_open_model", "odds_calibration", "penaltyblog",
    ),
    "contrarian_single": style(
        "contrarian_single", "Ellenáramú egyes", "CONTRARIAN_VALUE",
        "Igazolt 24–48 órás áremelkedés, modellérték és külön forrásolt felülvizsgálat.",
        "odds_calibration", "penaltyblog",
    ),
}


def get_style(identifier: str) -> dict:
    return deepcopy(STYLES[identifier])
