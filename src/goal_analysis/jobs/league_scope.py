"""Prospective Arthur calendar expansion, without changing ticket rules or budgets."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import field_id, native_id
from goal_analysis.providers.api_football_history import aware_time
from goal_analysis.providers.the_stats_leagues import LEAGUES

from .portfolio_calendar import collect_calendar

MINIMUM_PENDING_FIXTURES = 15
SERIE_C_GROUPS = {138: "IT3_A", 942: "IT3_B", 943: "IT3_C"}


def _calendar_rows(payload):
    """Keep malformed venue structures from escaping the shared normalizer."""
    rows = payload.get("response", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    # The normalizer rejects malformed identities, teams, dates and seasons.
    # Its optional venue field assumes an object; invalidate those rows while
    # retaining their identity so duplicate exclusions still apply to both rows.
    result = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("fixture"), dict):
            venue = row["fixture"].get("venue")
            if venue is not None and not isinstance(venue, dict):
                row = {**row, "fixture": {**row["fixture"], "date": None, "venue": None}}
        result.append(row)
    return result


def _pending(rows, leagues, target_date, now):
    fixtures, _, _ = collect_calendar(None, leagues, target_date, {"response": rows})
    cutoff = now + timedelta(minutes=15)
    return [fixture for fixture in fixtures
            if fixture["provider_status"] == "NS" and aware_time(fixture["kickoff"]) > cutoff]


def _fallback_config(identifier, season):
    return {
        "competition_id": SERIE_C_GROUPS[identifier],
        "api_football_id": identifier,
        "season": season,
        "history_seasons": [season - 1, season] if season > 1900 else [season],
        # This internal routing key skips unverified The Odds API sport names;
        # the existing API-Football odds collector remains responsible for prices.
        "odds_sport_key": f"soccer_arthur_{identifier}",
        "competition_type": "LEAGUE",
    }


def resolve_default_leagues(payload, target_date, configured, now: datetime) -> list[dict]:
    """Add available Serie C groups only below 15 valid, unlocked default fixtures.

    The caller uses this only for automatic Arthur scope selection. Explicit user
    selections and the independent DAILY_223 collector do not use this function.
    It consumes the already fetched calendar and performs no provider requests.
    """
    aware_time(now.isoformat())
    result = deepcopy(list(configured))
    rows = _calendar_rows(payload)
    if len(result) >= 40 or len(_pending(rows, result, target_date, now)) >= MINIMUM_PENDING_FIXTURES:
        return result

    existing = {int(league["api_football_id"]) for league in result}
    possible = {}
    for row in rows:
        try:
            league = row["league"]
            identifier, season = league["id"], league["season"]
            if (type(identifier) is not int or identifier not in SERIE_C_GROUPS
                    or identifier in existing or type(season) is not int
                    or not 1900 <= season <= 2200):
                continue
            possible[(identifier, season)] = _fallback_config(identifier, season)
        except (KeyError, TypeError):
            continue

    available = {}
    for (identifier, season), league in sorted(possible.items()):
        # Normalize against the whole payload so duplicate fixture identities are
        # excluded in full, including duplicates in an otherwise unrelated league.
        fixtures = _pending(rows, [league], target_date, now)
        if any(fixture["season"] == season for fixture in fixtures):
            available.setdefault(identifier, []).append(league)
    for identifier in sorted(available):
        choices = available[identifier]
        if len(choices) == 1 and len(result) < 40:
            result.append(choices[0])
    return result


def _page_proof(rows, receipts):
    """Describe already collected native pages; never perform a second collection."""
    pages, sources, issues = [], [], []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            issues.append('INVALID_PAGE_RECEIPT')
            continue
        page = deepcopy(receipt)
        pages.append(page)
        count = page.get('returned_rows')
        if type(count) is int and 0 <= count <= 100:
            sources.extend([page] * count)
        else:
            issues.append('INVALID_PAGE_ROW_COUNT')
    if not pages:
        return 'UNKNOWN', pages, {}, ['PAGE_PROOF_UNAVAILABLE']
    totals = {page.get('total_pages') for page in pages
              if type(page.get('total_pages')) is int}
    total = next(iter(totals)) if len(totals) == 1 else None
    if (total is None or total < 0
            or any(type(page.get('total_pages')) is not int for page in pages)):
        issues.append('UNSTABLE_OR_INVALID_PAGE_TOTAL')
    expected = list(range(1, max(1, total or 0) + 1))
    if (any(type(page.get('page')) is not int for page in pages)
            or [page.get('page') for page in pages] != expected):
        issues.append('MISSING_OR_DUPLICATE_CALENDAR_PAGE')
    if any(type(page.get('per_page')) is not int or page['per_page'] != 100
           or not isinstance(page.get('source_id'), str) or not page['source_id']
           or not isinstance(page.get('content_hash'), str) or not page['content_hash']
           for page in pages):
        issues.append('INVALID_PAGE_METADATA_OR_SOURCE')
    if len(sources) != len(rows) or total == 0 and rows:
        issues.append('PAGE_ROWS_DO_NOT_MATCH_CALENDAR')
    if total and any(page.get('returned_rows', 0) == 0 for page in pages):
        issues.append('EMPTY_NONEMPTY_CALENDAR_PAGE')
    # Attribution is only safe when the page row counts reproduce the input.
    by_row = dict(enumerate(sources)) if len(sources) == len(rows) else {}
    return ('PARTIAL' if issues else 'COMPLETE'), pages, by_row, issues


def resolve_full_day_scope(rows, target_date, configured, *, operating_contract_version=1,
                           selected_competition_ids=None, page_receipts=()):
    """Reuse the native day's complete inventory, separately exposing routing gaps.

    ``page_receipts`` describe the raw pages already read by ``collect_live``:
    page/per_page/total_pages, returned_rows, source_id and content_hash. Native
    identities come exclusively from those rows; LEAGUES remains the sole
    verified bridge to existing integer configuration keys. Unknown mappings
    stay in ``events`` for every profile, without invented routing identifiers.
    The v1 collector retains its existing legacy selection path unchanged.
    """
    if type(operating_contract_version) is not int or operating_contract_version not in (1, 2):
        raise ValueError('operating_contract_version must be 1 or 2')
    if operating_contract_version == 1:
        return deepcopy(list(configured)), {
            'operating_contract_version': 1, 'scope': 'LEGACY_CONFIGURED',
            'status': 'LEGACY_RESTRICTED', 'full_product_run': False,
        }
    if not isinstance(rows, list):
        raise TypeError('Native calendar rows must be a list')
    from .the_stats_live import _identity, extra_routing

    manual = selected_competition_ids is not None
    if manual and (not isinstance(selected_competition_ids, list)
                   or not 1 <= len(selected_competition_ids) <= 40
                   or any(type(identifier) is not int for identifier in selected_competition_ids)
                   or len(set(selected_competition_ids)) != len(selected_competition_ids)):
        raise ValueError('Select 1-40 distinct configured competitions')
    calendar_status, pages, source_by_row, page_issues = _page_proof(rows, page_receipts)
    mappings = {value[0]: int(key) for key, value in LEAGUES.items()}
    existing = {item['api_football_id']: deepcopy(item) for item in configured}
    counts = Counter(row.get('id') for row in rows
                     if isinstance(row, dict) and isinstance(row.get('id'), str))
    events, rejected, day_competitions = [], [], set()
    for index, row in enumerate(rows):
        source = source_by_row.get(index, {})
        event = {'row_number': index + 1, 'source_id': source.get('source_id'),
                 'source_content_hash': source.get('content_hash')}
        try:
            identifier = native_id(row['id'], 'mt')
            event.update(provider_fixture_id=identifier, fixture_id='the_stats_api:' + identifier)
            comp = native_id(field_id(row, 'competition'), 'comp')
            event['provider_league_id'] = comp
            kickoff = utc(row['utc_date'])
            event['kickoff'] = kickoff.isoformat()
            if kickoff.astimezone(ZoneInfo('Europe/Berlin')).date() != target_date:
                event['status'] = 'OUTSIDE_TARGET_DAY'
                rejected.append(event)
                continue
            day_competitions.add(comp)
            nested = row.get('competition')
            name = nested.get('name') if isinstance(nested, dict) else None
            name = name or row.get('competition_name')
            if not isinstance(name, str) or not name.strip():
                mapping = LEAGUES.get(str(mappings.get(comp)))
                name = mapping[1] if mapping else None
            event.update(competition_name=name, name_status='KNOWN' if name else 'UNKNOWN',
                         provider_status=row.get('status'))
            _, _, season, home, away = _identity(row)
            event.update(provider_season_id=season, provider_home_team_id=home,
                         provider_away_team_id=away, home_team=row['home_team']['name'],
                         away_team=row['away_team']['name'])
            if counts[identifier] != 1:
                event['status'] = 'DUPLICATE_CALENDAR_FIXTURE'
            elif manual and (comp not in mappings or mappings[comp] not in selected_competition_ids):
                event['status'] = 'MANUAL_RESEARCH_EXCLUDED'
            else:
                event['status'] = 'ROUTED'
            events.append(event)
        except (KeyError, TypeError, ValueError, OverflowError):
            event['status'] = 'INVALID_CALENDAR_FIXTURE'
            rejected.append(event)
    native_routes = []
    if not manual:
        for comp in sorted(day_competitions - mappings.keys()):
            native_events = [event for event in events
                             if event.get('provider_league_id') == comp and event['status'] == 'ROUTED']
            if native_events:
                native_routes.append({
                    'source_provider': 'the_stats_api', 'provider_league_id': comp,
                    'provider_season_ids': sorted({event['provider_season_id'] for event in native_events}),
                    'fixture_ids': sorted(event['fixture_id'] for event in native_events),
                    'routing_key': 'soccer_the_stats_' + comp,
                    'source_receipts': [{'source_id': event['source_id'],
                        'content_hash': event['source_content_hash']} for event in native_events],
                    'competition_name': next((event['competition_name'] for event in native_events
                                              if event.get('competition_name')), None),
                    'metadata_status': 'UNKNOWN',
                })
    requested = (selected_competition_ids if manual else
                 sorted(mappings[comp] for comp in day_competitions if comp in mappings))
    leagues = []
    for identifier in requested:
        leagues.append(existing[identifier] if identifier in existing else extra_routing(identifier, target_date))
    missing = [{'provider_league_id': comp,
                'name': next((event.get('competition_name') for event in events
                              if event.get('provider_league_id') == comp
                              and event.get('competition_name')), None),
                'fixture_ids': sorted({event['fixture_id'] for event in events
                                       if event.get('provider_league_id') == comp}),
                'reason': 'NO_VERIFIED_COMPETITION_ROUTING'}
               for comp in sorted(day_competitions - mappings.keys()
                                  - {route['provider_league_id'] for route in native_routes})]
    routing_status = 'PARTIAL' if missing else 'COMPLETE'
    identity_errors = rejected or any(event['status'] == 'DUPLICATE_CALENDAR_FIXTURE'
                                      for event in events)
    status = ('MANUAL_RESEARCH' if manual else 'PARTIAL' if missing or identity_errors
              or calendar_status != 'COMPLETE' else 'COMPLETE')
    return leagues, {
        'operating_contract_version': 2,
        'scope': 'MANUALLY_RESTRICTED_RESEARCH' if manual else 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
        'status': status, 'provider_calendar_status': calendar_status,
        'routing_status': routing_status, 'full_product_run': status == 'COMPLETE',
        'date': target_date.isoformat(), 'timezone': 'Europe/Berlin',
        'pages': pages, 'page_issues': page_issues, 'returned_fixtures': len(rows),
        'configured_competition_ids': sorted(existing),
        'selected_competition_ids': [item['api_football_id'] for item in leagues],
        'native_competition_routes': native_routes,
        'selected_native_competition_ids': sorted(route['provider_league_id'] for route in native_routes),
        'provider_competition_ids': sorted(day_competitions),
        'provider_fixture_ids': sorted({event['fixture_id'] for event in events}),
        'routed_fixture_ids': sorted({event['fixture_id'] for event in events if event['status'] == 'ROUTED'}),
        'uncovered_competitions': missing, 'events': events, 'rejected_rows': rejected,
        'unobserved_events': 'UNKNOWN',
    }
