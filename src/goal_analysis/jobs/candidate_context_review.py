"""Existing witnessed facts for the first candidate review, without new weights."""
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization.canonical import utc

VERSION = 'candidate-context-review-v1'
WEATHER_VERSION = 'candidate-context-weather-v1'
CATEGORIES = ('players', 'xg', 'schedule', 'travel', 'rotation', 'weather')


def policy(root):
    path = Path(root) / 'config/candidate-context-review.json'
    if not path.is_file():
        return 'OFF'
    if path.stat().st_size > 1000:
        raise ValueError('OVERSIZED_CANDIDATE_CONTEXT_POLICY')
    value = json.loads(path.read_bytes())
    if (set(value) != {'version', 'mode'} or value['version'] != VERSION
            or value['mode'] not in {'OFF', 'SHADOW', 'ACTIVE'}):
        raise ValueError('INVALID_CANDIDATE_CONTEXT_POLICY')
    return value['mode']


def _urls(value, depth=0):
    if depth > 16:
        raise ValueError('CONTEXT_LINEAGE_TOO_DEEP')
    result = set()
    if isinstance(value, dict):
        url = value.get('source_url')
        if isinstance(url, str) and url.startswith('https://'):
            result.add(url)
        for child in value.values():
            if isinstance(child, (dict, list)):
                result.update(_urls(child, depth+1))
    elif isinstance(value, list):
        for child in value:
            result.update(_urls(child, depth+1))
    return sorted(result)


def _row(category, data=None, *, sources=(), reason=None, fixture_id=None, cutoff=None):
    row = {'category': category, 'status': 'SOURCED' if data is not None else 'UNKNOWN',
           'data': data, 'source_urls': sorted(set(sources)), 'reason': reason}
    row['evidence_id'] = 'context:' + canonical_sha256(
        {'fixture_id': fixture_id, 'cutoff': cutoff, **row})[:16]
    return row


def _optional_stage(callback, *args, fallback, **kwargs):
    try:
        return callback(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 - independent optional evidence must remain isolated
        return {**deepcopy(fallback), 'status': 'UNAVAILABLE',
                'failure_class': type(error).__name__}


def _compact_player(row, wanted):
    data, kind = deepcopy(row['payload']), row['category']
    if kind == 'squad':
        players = data.get('players', [])
        data['players'] = [p for p in players if p.get('player_id') in wanted]
        data['omitted_other_roster_players'] = len(players)-len(data['players'])
    elif kind == 'injuries_suspensions':
        for name in ('injuries', 'suspensions'):
            values = data.get(name, [])
            data[name] = [p for p in values if p.get('active') is True]
            data['omitted_inactive_'+name] = len(values)-len(data[name])
    return {'kind': kind, 'payload': data, 'source_sha256': row['source_sha256'],
            'observed_at': row['observed_at'], 'available_at': row['available_at'],
            'source_url': row['source_url']}


def verified_supplement(root, collection, fixtures, cutoff):
    """Reconstruct eligible facts from real bytes, never trust a rehashed envelope."""
    from goal_analysis.agents.grok_stats_data import validate_stats_context
    from goal_analysis.jobs.fixture_contexts import raw_source
    from goal_analysis.jobs.the_stats_context import _canonical
    from goal_analysis.storage.db import Database

    database = Database(Path(root)/'data/goal_analysis.sqlite3')
    output, issues = {}, Counter()
    for fixture in fixtures:
        fid = fixture['fixture_id']
        candidate = collection.get('context_by_fixture', {}).get(fid)
        if not candidate:
            continue
        try:
            value = validate_stats_context({**candidate, 'version': collection['version'],
                'provider': collection['provider'], 'observed_at': collection['observed_at']},
                fixture, cutoff)
            if value['status'] != 'MATCHED':
                continue
            mapping = value['mapping']
            # Native fixture linkage is independently checked by link_report and
            # the existing target reader. This validates every fact used below.
            rows = []
            for fact in value['facts']:
                if fact['status'] != 'AVAILABLE' or fact['category'] == 'competition':
                    continue
                from goal_analysis.jobs.player_specialist_shadow import TTL

                ttl = timedelta(seconds=TTL.get(fact['category'], 21600))
                if not timedelta(0) <= cutoff-utc(fact['source']['observed_at']) <= ttl:
                    issues['STALE_REUSED_'+fact['category'].upper()] += 1
                    continue
                body = raw_source(Path(root), database, fact['source'], cutoff)
                if _canonical(fact['category'], body, fact['subject_id'], mapping) != fact['data']:
                    raise ValueError('CONTEXT_FACT_DIFFERS_FROM_ORIGINAL_BYTES')
                rows.append(fact)
            output[fid] = rows
        except (ValueError, KeyError, TypeError, OSError) as error:
            issues[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return output, dict(issues)


def context_scope_packets(fixtures, packets, eligible_candidates, cutoff):
    """Freeze facts for possible later selections without expanding model inputs."""
    from goal_analysis.engine.publication_window import allows_new_leg

    by_id = {fixture['fixture_id']: fixture for fixture in fixtures}
    scope = {}
    for packet in packets:
        item = {'fixture_id': packet['fixture_id']}
        if 'weather_forecast' in packet:
            item['weather_forecast'] = deepcopy(packet['weather_forecast'])
        scope[packet['fixture_id']] = item
    for candidate in eligible_candidates:
        fid = candidate['fixture_id']
        fixture = by_id.get(fid)
        if fixture is not None and allows_new_leg(fixture, cutoff):
            scope.setdefault(fid, {'fixture_id': fid})
    return list(scope.values())


def _forecast_scope(fixtures, scope_packets, provider, clock):
    """One lookup per eligible fixture; preserve original receipts on reuse."""
    from goal_analysis.engine.publication_window import allows_new_leg
    from goal_analysis.providers.open_meteo import unknown

    by_id = {f['fixture_id']: f for f in fixtures}
    forecasts, attempted, skipped = {}, [], []
    for packet in scope_packets:
        fid = packet['fixture_id']
        fixture = by_id.get(fid)
        started = utc(clock())
        if fixture is None or not allows_new_leg(fixture, started):
            skipped.append(fid)
            continue
        if isinstance(packet.get('weather_forecast'), dict):
            forecasts[fid] = deepcopy(packet['weather_forecast'])
            continue
        attempted.append(fid)
        try:
            lookup = getattr(provider, 'forecast_for_candidate_context', provider.forecast)
            forecast = lookup(deepcopy(fixture))
            if not isinstance(forecast, dict) or forecast.get('fixture_id') != fid:
                raise ValueError('WEATHER_FIXTURE_IDENTITY_MISMATCH')
        except Exception:  # noqa: BLE001 - isolate each optional forecast, never retry
            forecast = unknown(fixture, 'Az Open-Meteo lekérés nem sikerült; az időjárás ismeretlen.')
            forecast.update(weather_kind='FORECAST', provider_issue_time=None,
                observed_at=started.isoformat(), kickoff=fixture.get('kickoff'),
                time_basis='LOOKUP_ATTEMPT_AT')
        forecasts[fid] = deepcopy(forecast)
        packet['weather_forecast'] = deepcopy(forecast)
    return forecasts, attempted, skipped


def frozen_weather_fixtures(report, now):
    """Validate the same-run frozen attempts before suppressing later lookups."""
    value = report.get('candidate_context_weather')
    try:
        if (not isinstance(value, dict) or value['version'] != WEATHER_VERSION
                or not report.get('run_id') or value['run_id'] != report['run_id']
                or utc(value['as_of']) > utc(now)):
            return set()
        ids = value['fixture_ids']
        attempted, skipped = value['attempted_fixture_ids'], value['skipped_fixture_ids']
        if (not isinstance(ids, list) or ids != sorted(set(ids))
                or attempted != sorted(set(attempted)) or skipped != sorted(set(skipped))
                or not set(attempted).issubset(ids) or set(ids) & set(skipped)):
            return set()
        fixtures = [f for f in report.get('fixtures', []) if f['fixture_id'] in ids]
        if len(fixtures) != len(ids):
            return set()
        forecasts = {f['fixture_id']: f['weather'] for f in fixtures}
        if any(not isinstance(w, dict) or w.get('fixture_id') != fid
               or utc(w['observed_at']) > utc(value['as_of']) for fid, w in forecasts.items()):
            return set()
        if canonical_sha256(forecasts) != value['forecasts_sha256']:
            return set()
        return set(ids)
    except (KeyError, TypeError, ValueError):
        return set()


def prepare(root, fixtures, packets, *, run_id, eligible_candidates=(), weather_provider=None,
            clock=lambda: datetime.now(UTC)):
    """One bounded acquisition before review; the same collection is reused later."""
    from goal_analysis.jobs.calendar_collection_shadow import collect as calendars
    from goal_analysis.jobs.calendar_collection_shadow import enabled as calendars_enabled
    from goal_analysis.jobs.expert_advisory import enrich_sports_supplement
    from goal_analysis.jobs.fixture_contexts import link_report
    from goal_analysis.jobs.player_collection_shadow import collect as players
    from goal_analysis.jobs.player_collection_shadow import enabled as players_enabled
    from goal_analysis.jobs.xg_specialist_shadow import _targets

    root = Path(root).resolve(); started = utc(clock())
    scope_packets = context_scope_packets(fixtures, packets, eligible_candidates, started)
    wanted = {p['fixture_id'] for p in scope_packets}
    by_id = {f['fixture_id']: f for f in fixtures if f['fixture_id'] in wanted}
    selected = [deepcopy(by_id[p['fixture_id']]) for p in scope_packets
                if p['fixture_id'] in by_id]
    seed = {'run_id': run_id, 'date': started.date().isoformat(), 'fixtures': selected}
    collected = enrich_sports_supplement(root, seed, clock=clock, context_shadow=True)
    collection = collected['context_collection_shadow']['collection']
    linking = link_report(root, {**seed, 'sports_supplement': {'the_stats_api': collection}},
                          clock=clock, include_saved_context=False)
    by_id = {f['fixture_id']: f for f in selected}
    targets, _proofs, _issues = _targets(root, by_id, utc(clock()))
    qualified = [by_id[fid] for fid in targets]
    stopped = bool(collection.get('usage', {}).get('stopped_reason')) or collection.get('usage', {}).get('requests') is None
    player_collection = _optional_stage(players, root, qualified, clock=clock, fallback={}) if (
        qualified and not stopped and players_enabled(root)) else {}
    calendar_collection = _optional_stage(calendars, root, qualified, clock=clock, fallback={}) if (
        qualified and not stopped and calendars_enabled(root)) else {}
    weather = None
    if weather_provider is not None:
        weather, attempted, skipped = _forecast_scope(selected, scope_packets, weather_provider, clock)
    venue_collection = {}
    if weather_provider is not None and hasattr(weather_provider, 'collect_previous_venues'):
        venue_collection = _optional_stage(weather_provider.collect_previous_venues, selected, fallback={})
    cutoff = utc(clock())
    prepared = compose(root, selected, scope_packets, collection, as_of=cutoff)
    if weather is not None:
        prepared['weather_by_fixture'] = weather
        prepared['frozen_weather'] = {'version': WEATHER_VERSION, 'run_id': run_id,
            'as_of': cutoff.isoformat(), 'fixture_ids': sorted(weather),
            'attempted_fixture_ids': sorted(attempted), 'skipped_fixture_ids': sorted(skipped),
            'forecasts_sha256': canonical_sha256(weather)}
    return {**prepared, 'started_at': started.isoformat(), 'canonical_linking': linking,
            'player_collection': player_collection, 'calendar_collection': calendar_collection,
            'previous_venue_collection': venue_collection,
            'scope_packets': scope_packets,
            'initial_review_fixture_ids': sorted({p['fixture_id'] for p in packets})}


def compose(root, fixtures, packets, collection, *, as_of):
    """Read original stored facts at an explicit cutoff; no requests or receipts."""
    from goal_analysis.jobs.player_specialist_shadow import _contexts
    from goal_analysis.jobs.rotation_shadow import inspect_rotation
    from goal_analysis.jobs.schedule_shadow import inspect_schedule
    from goal_analysis.jobs.xg_specialist_shadow import _targets

    root, cutoff = Path(root).resolve(), utc(as_of)
    wanted = {p['fixture_id'] for p in packets}
    by_id = {f['fixture_id']: f for f in fixtures if f['fixture_id'] in wanted}
    targets, proofs, target_issues = _targets(root, by_id, cutoff)
    qualified = [by_id[fid] for fid in targets]
    fresh = {f['fixture_id']: f for f in qualified
             if cutoff < utc(f['kickoff'])-timedelta(minutes=15)
             and timedelta(0) <= cutoff-utc(f['fixture_source']['observed_at']) <= timedelta(minutes=30)}
    contexts, player_issues, _ = _contexts(root, fresh, cutoff) if fresh else ({}, {}, 0)
    sports, sports_issues = verified_supplement(root, collection, list(fresh.values()), cutoff)
    schedule = _optional_stage(inspect_schedule, root, list(fresh.values()), as_of=cutoff,
                               fallback={'features': []})
    rotation = _optional_stage(inspect_rotation, root, list(fresh.values()), as_of=cutoff,
                               fallback={'features': []})
    schedules = {f['fixture_id']: f for f in schedule['features']}
    rotations = {f['fixture_id']: f for f in rotation['features']}
    weather = {p['fixture_id']: p.get('weather_forecast') for p in packets}
    result = {}
    for fid in wanted:
        native = fid.partition(':')[2]
        args = {'fixture_id': fid, 'cutoff': cutoff.isoformat()}
        rows = []; accepted = contexts.get(fid, [])
        absent = {p.get('player_id') for r in accepted if r['category'] == 'injuries_suspensions'
                  for name in ('injuries', 'suspensions') for p in r['payload'].get(name, []) if p.get('active') is True}
        values = [_compact_player(r, absent) for r in accepted]
        rows.append(_row('players', values or None, sources=_urls(values),
                         reason=None if values else 'NO_FRESH_VERIFIED_PLAYER_CONTEXT', **args))
        xg = [{'subject_id': f['subject_id'], 'data': f['data'], 'source': f['source']}
              for f in sports.get(fid, []) if f['category'] == 'team_season_stats']
        rows.append(_row('xg', xg or None, sources=_urls(xg),
                         reason='SEASON_XG_FACTS_NOT_A_CALIBRATED_FORECAST' if xg else 'NO_VERIFIED_TEAM_XG', **args))
        value = schedules.get(native)
        teams = value.get('teams', []) if value else []
        valid = [t for t in teams if t['status'] == 'AVAILABLE_SHADOW']
        schedule_data = [{k: v for k, v in t.items() if k not in {'previous_fixture', 'next_fixture'}} |
                         {k: t[k]['fixture_id'] if t.get(k) else None for k in ('previous_fixture', 'next_fixture')}
                         for t in valid]
        sources = _urls(value.get('dataset', {}).get('lineage', [])) if value else []
        rows.append(_row('schedule', schedule_data or None, sources=sources,
                         reason='KICKOFF_GAPS_NOT_ACTUAL_REST; INCOMPLETE_TEAMS_REMAIN_UNKNOWN', **args))
        distances = [{k: t.get(k) for k in ('team_id', 'side', 'travel_status',
                     'previous_to_target_venue_great_circle_km')} for t in valid
                     if t.get('previous_to_target_venue_great_circle_km') is not None]
        rows.append(_row('travel', distances or None, sources=sources,
                         reason='GREAT_CIRCLE_VENUE_DISTANCE_PROXY_NOT_ACTUAL_ITINERARY' if distances else 'MISSING_VERIFIED_VENUE_DISTANCE', **args))
        value = rotations.get(native)
        comparisons = [t for t in value.get('teams', []) if t['status'] == 'XI_COMPARISON_SHADOW'] if value else []
        rows.append(_row('rotation', comparisons or None,
            sources=_urls(value.get('dataset', {}).get('lineage', [])) if value else [],
            reason='CONFIRMED_STARTER_CHANGES_NOT_CAUSE_OR_STRENGTH' if comparisons else 'NO_CONFIRMED_CURRENT_AND_PREVIOUS_XI_PAIR', **args))
        forecast = weather.get(fid)
        try:
            valid_weather = (isinstance(forecast, dict) and forecast.get('status') == 'SOURCED'
                and forecast.get('weather_kind') == 'FORECAST'
                and timedelta(0) <= cutoff-utc(forecast['observed_at']) <= timedelta(hours=6))
        except (ValueError, TypeError, KeyError):
            valid_weather = False
        rows.append(_row('weather', deepcopy(forecast) if valid_weather else None,
            sources=[forecast['source_url']] if valid_weather else [],
            reason='FORECAST_CONTEXT_NOT_AN_ESTIMATED_GOAL_PROBABILITY_EFFECT' if valid_weather else 'NO_SOURCED_PREMATCH_FORECAST', **args))
        content = {'version': VERSION, 'fixture_id': fid, 'as_of': cutoff.isoformat(), 'categories': rows}
        result[fid] = {**content, 'context_id': canonical_sha256(content)}
    return {'version': VERSION, 'as_of': cutoff.isoformat(),
            'contexts': result, 'collection': deepcopy(collection),
            'target_issues': target_issues, 'player_issues': player_issues,
            'sports_issues': sports_issues, 'source_views': {'schedule': schedule, 'rotation': rotation},
            'target_proofs': proofs, 'new_probability_weights': False}


def attach(packets, prepared):
    output = deepcopy(packets)
    for packet in output:
        if packet['fixture_id'] in prepared.get('weather_by_fixture', {}):
            packet['weather_forecast'] = deepcopy(prepared['weather_by_fixture'][packet['fixture_id']])
        context = prepared.get('contexts', {}).get(packet['fixture_id'])
        if context is None:
            # Follow-ups share the frozen source cutoff; uncovered facts stay unknown.
            cutoff = prepared['as_of']
            content = {'version': VERSION, 'fixture_id': packet['fixture_id'], 'as_of': cutoff,
                'categories': [_row(name, fixture_id=packet['fixture_id'], cutoff=cutoff,
                    reason='FOLLOW_UP_FIXTURE_NOT_IN_FROZEN_CONTEXT_SCOPE') for name in CATEGORIES]}
            context = {**content, 'context_id': canonical_sha256(content)}
        packet['decision_context'] = deepcopy(context)
        for row in context['categories']:
            if row['status'] == 'SOURCED':
                packet.setdefault('evidence', []).append({'evidence_id': row['evidence_id'],
                    'category': row['category'], 'title': 'Verified '+row['category']+' context',
                    'source_url': row['source_urls'][0] if row['source_urls'] else None})
                packet.setdefault('evidence_ids', []).append(row['evidence_id'])
    return output


def usage(packets, review):
    """Record declared consideration and existing caution application separately."""
    by_id = {r['candidate_id']: r for r in review.get('reviews', [])}
    rows = []
    for packet in packets:
        response = by_id.get(packet['candidate_id'], {})
        assessments = {r['category']: r for r in response.get('context_assessments', [])}
        for context in packet.get('decision_context', {}).get('categories', []):
            considered = assessments.get(context['category'], {})
            eid = context['evidence_id']
            rows.append({'candidate_id': packet['candidate_id'], 'fixture_id': packet['fixture_id'],
                'category': context['category'], 'source_status': context['status'],
                'sent': True, 'response_considered': considered.get('status') == 'considered',
                'context_evidence_cited': eid in considered.get('evidence_ids', []),
                'overall_evidence_cited': eid in response.get('evidence_ids', []),
                'existing_caution_assessment_applies': response.get('assessment') == 'caution'
                    and eid in response.get('evidence_ids', []),
                'counterfactual_causality_or_predictive_gain_proven': False})
    return rows
