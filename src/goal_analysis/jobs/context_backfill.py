"""Archive already saved context without provider calls or ledger changes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.jobs.sports_context_archive import archive_grok_result
from goal_analysis.storage import Database
from goal_analysis.storage.sports_weather import WeatherStore


def backfill_context(root):
    root = Path(root)
    weather_store = WeatherStore(Database(root / 'data/goal_analysis.sqlite3'))
    weather_store.initialize()
    counts = {'reports': 0, 'weather_linked': 0, 'weather_inserted': 0,
              'weather_skipped': 0, 'news_results': [], 'errors': []}
    for path in sorted((root / 'reports/arthur').glob('*/report.json')):
        report = json.loads(path.read_text('utf-8'))
        counts['reports'] += 1
        for fixture in report.get('fixtures', []):
            if not isinstance(fixture, dict) or not isinstance(fixture.get('weather'), dict):
                continue
            weather = dict(fixture['weather'])
            # A run's start is not an upper bound on a later weather retrieval.
            observed = weather.get('observed_at') or report.get('finished_at')
            if not observed:
                counts['weather_skipped'] += 1
                continue
            basis = weather.get('time_basis', 'FETCHED_AT') if weather.get('observed_at') else 'REPORT_AVAILABLE_AT_UPPER_BOUND'
            weather.setdefault('weather_kind', 'FORECAST')
            weather.setdefault('provider_issue_time', None)
            url = weather.get('source_url')
            if isinstance(url, str):
                params = parse_qs(urlsplit(url).query)
                for field in ('latitude', 'longitude'):
                    if field not in weather and field in params:
                        try:
                            weather[field] = float(params[field][0])
                        except ValueError:
                            pass  # Unknown coordinates remain unknown.
            try:
                result = weather_store.ingest(
                    weather, fixture, observed_at=observed,
                    source_run_id=report.get('run_id') or path.parent.name,
                    time_basis=basis, metadata={
                        'report_path': path.relative_to(root).as_posix(),
                        'original_retrieval_time_known': bool(weather.get('observed_at')),
                        'location_precision': 'VENUE_CITY_GEOCODE',
                        'raw_provider_payload_available': False,
                    },
                )
                counts['weather_linked'] += 1
                counts['weather_inserted'] += int(result['inserted'])
            except ValueError:
                counts['weather_skipped'] += 1
                counts['errors'].append({'source': path.parent.name,
                                          'fixture_id': fixture.get('fixture_id'),
                                          'code': 'INVALID_SAVED_WEATHER'})
    grok = root / 'data/arthur/grok.sqlite3'
    if grok.exists():
        with sqlite3.connect(grok.as_uri() + '?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                'SELECT q.request_id,q.model,r.completed_at,r.result_json '
                'FROM grok_requests q JOIN grok_results r USING(request_id)'
            ).fetchall()
        for row in rows:
            result = archive_grok_result(root, json.loads(row['result_json']), row['request_id'],
                                         row['completed_at'], row['model'])
            counts['news_results'].append({'request_id': row['request_id'], **result})
    counts['weather_summary'] = weather_store.summary()
    return counts
