"""Curated native-ID taxonomy, separate participant/family/stage dimensions.

Provider format is retained as evidence, not used as a synonym for competition
purpose: TheStats labels International Friendly Games as format 'league'.
Unmapped IDs and renamed/conflicting catalogue identities stay unknown.
"""
from copy import deepcopy

from .training_datasets import sha

VERSION = "competition-types-shadow-v1"
# Identity assertions checked against actual archived native catalogue entries.
# These semantic labels are our versioned taxonomy, not provider capability flags.
RULES = {
    "comp_3039": ("Premier League", "England", None, "league", "LEAGUE", "CLUB"),
    "comp_8321": ("Championship", "England", None, "league", "LEAGUE", "CLUB"),
    "comp_4643": ("Bundesliga", "Germany", None, "league", "LEAGUE", "CLUB"),
    "comp_0406": ("2. Bundesliga", "Germany", None, "league", "LEAGUE", "CLUB"),
    "comp_2837": ("3. Liga", "Germany", None, "league", "LEAGUE", "CLUB"),
    "comp_8814": ("LaLiga", "Spain", None, "league", "LEAGUE", "CLUB"),
    "comp_0256": ("Ligue 1", "France", None, "league", "LEAGUE", "CLUB"),
    "comp_9777": ("Ligue 2", "France", None, "league", "LEAGUE", "CLUB"),
    "comp_5840": ("Serie A", "Italy", None, "league", "LEAGUE", "CLUB"),
    "comp_5450": ("Serie B", "Italy", None, "league", "LEAGUE", "CLUB"),
    "comp_451101": ("Serie C, Girone A", "Italy", None, "league", "LEAGUE", "CLUB"),
    "comp_302263": ("Serie C, Girone B", "Italy", None, "league", "LEAGUE", "CLUB"),
    "comp_615520": ("Serie C, Girone C", "Italy", None, "league", "LEAGUE", "CLUB"),
    "comp_3809": ("Eredivisie", "Netherlands", None, "league", "LEAGUE", "CLUB"),
    "comp_2977": ("Eerste Divisie", "Netherlands", None, "league", "LEAGUE", "CLUB"),
    "comp_8385": ("Liga Portugal Betclic", "Portugal", None, "league", "LEAGUE", "CLUB"),
    "comp_7428": ("FA Cup", "England", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_2504": ("EFL Cup", "England", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_3620": ("DFB Pokal", "Germany", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_7915": ("Copa del Rey", "Spain", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_4750": ("Coupe de France", "France", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_8525": ("Coppa Italia", "Italy", None, "cup", "DOMESTIC_CUP", "CLUB"),
    "comp_3498": ("UEFA Champions League", None, "UEFA", "tournament", "INTERNATIONAL_CLUB", "CLUB"),
    "comp_7739": ("UEFA Europa League", None, "UEFA", "tournament", "INTERNATIONAL_CLUB", "CLUB"),
    "comp_408698": ("UEFA Conference League", None, "UEFA", "tournament", "INTERNATIONAL_CLUB", "CLUB"),
    "comp_574977": ("UEFA Nations League", None, "UEFA", "tournament", "NATIONAL_TEAM", "NATIONAL"),
    "comp_29967": ("International Friendly Games", "World", None, "league", "FRIENDLY", "NATIONAL"),
    "comp_8973": ("World Championship Qual. AFC", None, "FIFA", "tournament", "NATIONAL_TEAM", "NATIONAL"),
    "comp_5720": ("World Championship Qual. CAF", None, "FIFA", "tournament", "NATIONAL_TEAM", "NATIONAL"),
}
STAGES = {"qualifying": "QUALIFIER", "qualification": "QUALIFIER",
          "group_stage": "GROUP_STAGE", "league_phase": "LEAGUE_PHASE",
          "regular_season": "LEAGUE_PHASE", "round_of_16": "KNOCKOUT",
          "quarter_final": "KNOCKOUT", "semi_final": "KNOCKOUT", "final": "FINAL"}
QUALIFIER_COMPETITIONS = frozenset({"comp_8973", "comp_5720"})
RULE_SHA256 = sha({"version": VERSION, "rules": RULES, "stages": STAGES,
                   "qualifier_competitions": sorted(QUALIFIER_COMPETITIONS),
                   "explicit_null_stage": "REVIEWED_PROVIDER_SCHEMA_REQUIRED_DE2_ONLY_V1"})


def unknown(reason):
    return {"status": "UNKNOWN", "family": "UNKNOWN", "participant_type": "UNKNOWN",
            "phase": "UNKNOWN", "is_qualifier": None, "is_friendly": None,
            "reason": reason, "rule_sha256": RULE_SHA256}


def classify_competition(competition, stage_name, *, reviewed_explicit_null=False):
    """Classify exact witnessed catalogue metadata; no names/date/round inference."""
    if type(reviewed_explicit_null) is not bool:
        raise ValueError('BOOLEAN_STAGE_CONTRACT_FLAG_REQUIRED')
    identifier = competition.get("id")
    rule = RULES.get(identifier)
    if rule is None:
        return unknown("UNMAPPED_NATIVE_COMPETITION")
    identity = tuple(competition.get(k) for k in ("name", "country", "confederation", "type"))
    if identity != rule[:4]:
        return unknown("CATALOGUE_IDENTITY_CONFLICT")
    phase = STAGES.get(stage_name, "UNKNOWN") if isinstance(stage_name, str) else "UNKNOWN"
    if reviewed_explicit_null and stage_name is None and identifier=='comp_0406' and rule[4]=='LEAGUE':
        phase='LEAGUE_PHASE'
    qualifier = None if phase == "UNKNOWN" else phase == "QUALIFIER"
    if identifier in QUALIFIER_COMPETITIONS:
        if phase not in {"UNKNOWN", "QUALIFIER"}:
            return unknown("QUALIFIER_STAGE_CONFLICT")
        phase, qualifier = "QUALIFIER", True
    friendly = rule[4] == "FRIENDLY"
    if friendly:
        if phase != "UNKNOWN":
            return unknown("FRIENDLY_STAGE_CONFLICT")
        phase, qualifier = "FRIENDLY", False
    return deepcopy({"status": "CLASSIFIED" if phase != "UNKNOWN" else "FAMILY_KNOWN_PHASE_UNKNOWN",
                     "family": rule[4], "participant_type": rule[5], "phase": phase,
                     "is_qualifier": qualifier, "is_friendly": friendly,
                     "provider_format": competition["type"], "source_stage_name": stage_name,
                     "basis": "CURATED_NATIVE_ID_WITH_EXACT_CATALOGUE_IDENTITY",
                     "rule_sha256": RULE_SHA256})
