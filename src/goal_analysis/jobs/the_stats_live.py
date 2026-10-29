"""Native TheStats fixtures/history for the existing, unchanged ticket engines.

Only real GETs/cache receipts are sources. List pagination is verified, partial
history remains explicit, and receipt time is never a bookmaker quote time.
"""
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import exact_name, field_id, native_id
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.the_stats_leagues import LEAGUES

BERLIN = ZoneInfo('Europe/Berlin')


def extra_routing(identifier, target_date):
    from .league_scope import SERIE_C_GROUPS, _fallback_config

    if identifier in SERIE_C_GROUPS:
        return _fallback_config(identifier, target_date.year)
    if str(identifier) not in LEAGUES:
        raise ValueError('No verified TheStats competition routing')
    return {'competition_id': 'THE_STATS_' + LEAGUES[str(identifier)][0],
            'api_football_id': identifier, 'season': target_date.year,
            'history_seasons': [target_date.year], 'odds_sport_key': f'soccer_arthur_{identifier}',
            'competition_type': 'NATIONAL' if identifier in {5, 10} else 'UEFA'
                if identifier in {2, 3, 848} else 'CUP' if identifier in {45, 48, 66, 81, 137, 143} else 'LEAGUE'}


def _page(resources, query, page):
    ttl = timedelta(hours=6) if query.get('status') == 'finished' else timedelta(minutes=5)
    data, source, error = resources.get('/football/matches', {**query, 'page': page, 'per_page': 100}, ttl=ttl)
    if error:
        raise ProviderError('TheStats fixtures: ' + error)
    payload = json.loads(resources.snapshots.read(source['content_hash']))
    meta = payload.get('meta', {})
    if (not isinstance(data, list) or type(meta.get('page')) is not int or meta['page'] != page
            or meta.get('per_page') != 100 or type(meta.get('total_pages')) is not int
            or not 0 <= meta['total_pages'] <= 100000 or len(data) > 100
            or (data and page > meta['total_pages']) or (not data and meta['total_pages'] > 0)):
        raise ProviderError('TheStats incomplete or invalid fixture pagination')
    return data, source, meta['total_pages']


def _identity(row):
    match = native_id(row['id'], 'mt')
    comp, season = native_id(field_id(row, 'competition'), 'comp'), native_id(field_id(row, 'season'), 'sn')
    home, away = (native_id(row[side + '_team']['id'], 'tm') for side in ('home', 'away'))
    if home == away:
        raise ValueError('Identical TheStats participants')
    for side in ('home', 'away'):
        exact_name(row[side + '_team']['name'])
    return match, comp, season, home, away


def _neutral_venue(row, operating_contract_version):
    """Preserve legacy bytes; v2 distinguishes observed bool from UNKNOWN."""
    if type(operating_contract_version) is not int or operating_contract_version not in (1, 2):
        raise ValueError('Unsupported operating contract version')
    if operating_contract_version == 1:
        return row.get('is_neutral', False)
    value = row.get('is_neutral')
    return value if type(value) is bool else None


def normalize_fixture(row, league, target_date, source, *, operating_contract_version=1, native_route=None):
    match, comp, season, home, away = _identity(row)
    if native_route is not None:
        if (operating_contract_version != 2 or league is not None
                or native_route.get('source_provider') != 'the_stats_api'
                or native_route.get('provider_league_id') != comp
                or season not in native_route.get('provider_season_ids', [])
                or 'the_stats_api:' + match not in native_route.get('fixture_ids', [])
                or native_route.get('routing_key') != 'soccer_the_stats_' + comp
                or {'source_id': source['source_url'], 'content_hash': source['content_hash']}
                   not in native_route.get('source_receipts', [])):
            raise ValueError('Unproven observed native competition route')
        league = {'api_football_id': None, 'competition_id': comp, 'season': None,
                  'competition_type': None, 'odds_sport_key': native_route['routing_key']}
        mapping = (comp, None, None)
    else:
        mapping = LEAGUES[str(league['api_football_id'])]
    kickoff = utc(row['utc_date'])
    if comp != mapping[0] or kickoff.astimezone(BERLIN).date() != target_date:
        raise ValueError('Wrong TheStats competition or Berlin day')
    status = {'scheduled': 'NS', 'live': 'LIVE', 'finished': 'FT',
              'postponed': 'PST', 'cancelled': 'CANC'}.get(row.get('status'), 'UNKNOWN')
    venue = row.get('venue') or {}
    if not isinstance(venue, dict):
        raise TypeError('Invalid venue')
    return {'fixture_id': 'the_stats_api:' + match, 'source_provider': 'the_stats_api',
            'provider_fixture_id': match, 'provider_league_id': comp,
            'provider_season_id': season, 'provider_home_team_id': home, 'provider_away_team_id': away,
            'config_league_id': str(league['api_football_id']) if league['api_football_id'] is not None else None,
            'home_team': row['home_team']['name'], 'away_team': row['away_team']['name'],
            'competition': league['competition_id'], 'competition_type': league['competition_type'],
            'sport_key': league['odds_sport_key'], 'kickoff': kickoff.isoformat(),
            'provider_status': status, 'season': league['season'],
            'venue_name': venue.get('name'), 'venue_city': venue.get('city'), 'venue_country': mapping[2],
            'neutral_venue': _neutral_venue(row, operating_contract_version), 'target_date': target_date.isoformat(),
            'source_id': source['source_url'], 'fixture_source': source,
            **({'native_competition_route': deepcopy(native_route),
                'competition_name': native_route.get('competition_name'),
                'metadata_status': 'UNKNOWN'} if native_route is not None else {})}


def normalize_history(row, comp, source, as_of, *, operating_contract_version=1):
    match, actual_comp, season, home, away = _identity(row)
    kickoff, observed = utc(row['utc_date']), utc(source['observed_at'])
    if (actual_comp != comp or row.get('status') != 'finished'
            or not kickoff + timedelta(minutes=90) <= observed <= as_of):
        raise ValueError('Invalid TheStats historical scope or chronology')
    score = row.get('score') or {}
    full = score.get('regulation') or {}
    half = {'home': score.get('half_time_home'), 'away': score.get('half_time_away')}
    # Never use aggregate, after-extra-time or shootout scores for 90-minute bets.
    def goals(group, side):
        value = group.get(side)
        if value is not None and (type(value) is not int or not 0 <= value <= 100):
            raise ValueError('Invalid period score')
        return value
    result = {'provider': 'the_stats_api', 'fixture_id': match, 'league_id': comp, 'season': season,
              'kickoff': kickoff.isoformat(), 'observed_at': observed.isoformat(), 'status': 'FT',
              'home_team_id': home, 'away_team_id': away, 'source_id': source['source_url'],
              'full_time_home': goals(full, 'home'), 'full_time_away': goals(full, 'away'),
              'first_half_home': goals(half, 'home'), 'first_half_away': goals(half, 'away'),
              'neutral_venue': _neutral_venue(row, operating_contract_version)}
    for side in ('home', 'away'):
        first, final = result['first_half_' + side], result['full_time_' + side]
        if first is not None and final is not None and first > final:
            raise ValueError('Half-time exceeds regulation score')
    return result


def calendar(resources, target_date):
    query = {'date_from': target_date.isoformat(), 'date_to': target_date.isoformat(),
             'utc_offset': '+00:00'}
    # Offset belongs to the TARGET day, including across the DST boundary.
    offset = datetime.combine(target_date, time(12), BERLIN).strftime('%z')
    query['utc_offset'] = offset[:3] + ':' + offset[3:]
    rows, sources, total = [], [], 1
    for page in range(1, 11):
        batch, source, total = _page(resources, query, page)
        rows.extend(batch)
        sources.append(source)
        if page >= total:
            break
    else:
        raise ProviderError('TheStats daily calendar exceeds the bounded page budget')
    return rows, sources


def calendar_options(root, config, target_date, clock):
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

    from .league_scope import SERIE_C_GROUPS
    from .the_stats_context import Resources

    resources = Resources(root, TheStatsPrimaryClient(root, max_requests=10, clock=clock), clock, 10)
    rows, _ = calendar(resources, target_date)
    leagues = list(config['leagues'])
    original_ids = {x['api_football_id'] for x in leagues}
    for key in LEAGUES:
        if int(key) not in original_ids:
            leagues.append(extra_routing(int(key), target_date))
    result = []
    pending = sum(row.get('competition_id') in {
        LEAGUES[str(x['api_football_id'])][0] for x in config['leagues'] if str(x['api_football_id']) in LEAGUES}
        and row.get('status') == 'scheduled' and utc(row['utc_date']) > utc(clock()) + timedelta(minutes=15)
        for row in rows)
    for league in leagues:
        mapping = LEAGUES.get(str(league['api_football_id']))
        matches = [row for row in rows if mapping and row.get('competition_id') == mapping[0]
                   and utc(row['utc_date']).astimezone(BERLIN).date() == target_date]
        if matches:
            fallback = league['api_football_id'] in SERIE_C_GROUPS
            result.append({'id': league['api_football_id'], 'provider_id': mapping[0],
                'name': mapping[1], 'country': mapping[2] or '', 'season': league['season'],
                'type': 'NATIONAL' if league['competition_type'] == 'NATIONAL' else 'CLUB',
                'configured': league['api_football_id'] in original_ids, 'fixture_count': len(matches),
                'default_selected': league['api_football_id'] in original_ids
                    or league['competition_type'] == 'NATIONAL' or fallback and pending < 15,
                'fallback': fallback and pending < 15,
                'fixtures': [{'home': x['home_team']['name'], 'away': x['away_team']['name'],
                              'kickoff': utc(x['utc_date']).isoformat()} for x in matches[:5]]})
    return result


def collect_live(config, client, target_date, clock, *, selected_competition_ids=None,
                 construct_legacy=False, operating_contract_version=1):
    from .league_scope import SERIE_C_GROUPS, _fallback_config
    from .the_stats_context import Resources

    resources = Resources(client.root, client, clock, config['max_football_calls'])
    start = utc(clock())
    rows, sources = calendar(resources, target_date)
    source_by_match, page_receipts = {}, []
    for source in sources:
        payload = json.loads(resources.snapshots.read(source['content_hash']))
        page_receipts.append({**payload.get('meta', {}), 'returned_rows': len(payload['data']),
                              'source_id': source['source_url'], 'content_hash': source['content_hash']})
        for row in payload['data']:
            source_by_match[row.get('id')] = source
    issues = []
    leagues = list(config['leagues'])
    full_scope = None
    if type(operating_contract_version) is not int or operating_contract_version not in (1, 2):
        raise ValueError('Unsupported operating contract version')
    if operating_contract_version == 2:
        from .league_scope import resolve_full_day_scope

        leagues, full_scope = resolve_full_day_scope(
            rows, target_date, leagues, operating_contract_version=2,
            selected_competition_ids=selected_competition_ids, page_receipts=page_receipts)
        issues.extend({'status': 'CALENDAR_SCOPE_PARTIAL', **item}
                      for item in full_scope['uncovered_competitions'])
    elif selected_competition_ids is not None:
        requested = set(selected_competition_ids)
        if not requested or len(requested) != len(selected_competition_ids) or len(requested) > 40:
            raise ValueError('Select 1-40 distinct configured competitions')
        for identifier in requested - {x['api_football_id'] for x in leagues}:
            leagues.append(extra_routing(identifier, target_date))
        leagues = [x for x in leagues if x['api_football_id'] in requested]
    elif not construct_legacy:
        active = {LEAGUES[str(x['api_football_id'])][0] for x in leagues if str(x['api_football_id']) in LEAGUES}
        count = sum(row.get('competition_id') in active and row.get('status') == 'scheduled'
                    and utc(row['utc_date']) > utc(clock()) + timedelta(minutes=15) for row in rows)
        if count < 15:
            for key in SERIE_C_GROUPS:
                if key not in {x['api_football_id'] for x in leagues}:
                    leagues.append(_fallback_config(key, target_date.year))
    configured = {}
    for league in leagues:
        mapping = LEAGUES.get(str(league['api_football_id']))
        if mapping:
            configured[mapping[0]] = league
        else:
            issues.append({'status': 'NO_VERIFIED_THE_STATS_COMPETITION', 'config_league_id': league['api_football_id']})
    native_routes = {route['provider_league_id']: route for route in
                     (full_scope or {}).get('native_competition_routes', [])}
    counts = Counter(row.get('id') for row in rows if isinstance(row, dict))
    fixtures = []
    for row in rows:
        if row.get('competition_id') not in configured and row.get('competition_id') not in native_routes:
            continue
        try:
            if counts[row['id']] != 1:
                raise ValueError('Duplicate calendar identity')
            fixtures.append(normalize_fixture(row, configured.get(row['competition_id']), target_date, source_by_match[row['id']],
                operating_contract_version=operating_contract_version,
                native_route=native_routes.get(row['competition_id']) if row['competition_id'] not in configured else None))
        except (ValueError, TypeError, KeyError):
            issues.append({'status': 'INVALID_THE_STATS_FIXTURE'})
    fixture_time = min(utc(source['observed_at']) for source in sources)
    # Round-robin pages: every active competition gets history before any one
    # competition consumes the budget. At most 800 recent matches per league.
    active = sorted({f['provider_league_id'] for f in fixtures
                     if f['provider_status'] == 'NS' and utc(f['kickoff']) > fixture_time + timedelta(minutes=15)})
    records, conflicts, history_sources, totals = {}, set(), [], {}
    history_start_calls = resources.usage['requests']
    for page in range(1, 9):
        for comp in active:
            if page > totals.get(comp, 8):
                continue
            history_query = {'competition_id': comp, 'status': 'finished', 'sort': '-utc_date',
                             'date_from': (start.date() - timedelta(days=730)).isoformat(),
                             'date_to': start.date().isoformat(), 'utc_offset': '+00:00'}
            try:
                batch, source, total = _page(resources, history_query, page)
                totals[comp] = total
                history_sources.append({**source, 'league_id': comp, 'page': page, 'total_pages': total})
                for row in batch:
                    try:
                        record = normalize_history(row, comp, source, utc(clock()),
                            operating_contract_version=operating_contract_version)
                        previous = records.get(record['fixture_id'])
                        if previous is not None and previous != record:
                            conflicts.add(record['fixture_id'])
                        records.setdefault(record['fixture_id'], record)
                    except (ValueError, KeyError, TypeError):
                        issues.append({'status': 'INVALID_THE_STATS_HISTORY', 'league_id': comp})
            except ProviderError:
                issues.append({'status': 'HISTORY_PARTIAL', 'league_id': comp, 'page': page})
                totals[comp] = page - 1
        if resources.usage['requests'] >= resources.usage['max_requests'] or resources.usage['stopped_reason']:
            break
    for key in conflicts:
        records.pop(key, None)
        issues.append({'status': 'CONFLICTING_THE_STATS_HISTORY', 'fixture_id': key})
    for comp, pages in totals.items():
        fetched = sum(x['league_id'] == comp for x in history_sources)
        if pages > fetched:
            issues.append({'status': 'HISTORY_PARTIAL', 'league_id': comp, 'pages_read': fetched, 'total_pages': pages})
    for comp in set(active) - {source['league_id'] for source in history_sources}:
        issues.append({'status': 'HISTORY_UNAVAILABLE', 'league_id': comp})
    history = {'schema_version': 1, 'kind': 'DAILY223_HISTORY', 'provider': 'the_stats_api',
               'collected_at': utc(clock()).isoformat(), 'api_calls': resources.usage['requests'] - history_start_calls,
               'cache_hits': resources.usage['cache_hits'], 'sources': history_sources,
               'records': sorted(records.values(), key=lambda x: (x['kickoff'], x['fixture_id'])),
               'data_issues': issues}
    history['snapshot_sha256'] = canonical_sha256(history)
    config['leagues'] = [x for x in leagues if str(x['api_football_id']) in LEAGUES]
    return {'fixtures': {'observed_at': fixture_time.isoformat(), 'records': fixtures},
            'history': history, 'calendar': {'method': 'THE_STATS_NATIVE_DATE_WIDE',
            'date': target_date.isoformat(), 'returned_fixtures': len(rows), 'configured_fixtures': len(fixtures),
            'sources': sources, **({'scope_receipt': full_scope} if full_scope is not None else {})},
            'sports_primary_usage': resources.usage,
            **({'native_competition_routes': list(native_routes.values())}
               if operating_contract_version == 2 else {})}
