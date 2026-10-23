"""Explicit routing verified against the archived complete TheStats catalogue.

The integer keys are existing Arthur configuration keys, not TheStats IDs.
Catalogue evidence: roadmap-execution-20261020/1.6 and competition_catalogue
raw snapshots. Native match/season/team identities are never inferred here.
"""
LEAGUES = {
    '39': ('comp_3039', 'Premier League', 'England'),
    '40': ('comp_8321', 'Championship', 'England'),
    '45': ('comp_7428', 'FA Cup', 'England'),
    '48': ('comp_2504', 'EFL Cup', 'England'),
    '78': ('comp_4643', 'Bundesliga', 'Germany'),
    '79': ('comp_0406', '2. Bundesliga', 'Germany'),
    '80': ('comp_2837', '3. Liga', 'Germany'),
    '81': ('comp_3620', 'DFB Pokal', 'Germany'),
    '140': ('comp_8814', 'LaLiga', 'Spain'),
    '143': ('comp_7915', 'Copa del Rey', 'Spain'),
    '61': ('comp_0256', 'Ligue 1', 'France'),
    '62': ('comp_9777', 'Ligue 2', 'France'),
    '66': ('comp_4750', 'Coupe de France', 'France'),
    '135': ('comp_5840', 'Serie A', 'Italy'),
    '136': ('comp_5450', 'Serie B', 'Italy'),
    '137': ('comp_8525', 'Coppa Italia', 'Italy'),
    '138': ('comp_451101', 'Serie C, Girone A', 'Italy'),
    '942': ('comp_302263', 'Serie C, Girone B', 'Italy'),
    '943': ('comp_615520', 'Serie C, Girone C', 'Italy'),
    '88': ('comp_3809', 'Eredivisie', 'Netherlands'),
    '89': ('comp_2977', 'Eerste Divisie', 'Netherlands'),
    '94': ('comp_8385', 'Liga Portugal Betclic', 'Portugal'),
    '2': ('comp_3498', 'UEFA Champions League', None),
    '3': ('comp_7739', 'UEFA Europa League', None),
    '848': ('comp_408698', 'UEFA Conference League', None),
    '5': ('comp_574977', 'UEFA Nations League', None),
    '10': ('comp_29967', 'International Friendly Games', 'World'),
}
