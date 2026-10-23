import json
from datetime import UTC, datetime

from goal_analysis.jobs.context_backfill import backfill_context
from goal_analysis.storage import Database
from goal_analysis.storage.sports_weather import WeatherStore


def test_legacy_weather_uses_report_end_never_start_or_import_time(tmp_path):
    report = {'observed_at': '2026-10-01T09:00:00+00:00',
              'finished_at': '2026-10-01T10:00:00+00:00', 'run_id': 'legacy',
              'fixtures': [{'fixture_id': 'api_football:1', 'kickoff': '2026-10-01T15:00:00+00:00',
                            'weather': {'fixture_id': 'api_football:1', 'provider': 'open_meteo',
                                        'status': 'SOURCED', 'forecast_time': '2026-10-01T15:00:00+00:00',
                                        'temperature_c': 18, 'source_url': 'https://api.open-meteo.com/v1/forecast?latitude=50&longitude=8'}}]}
    path = tmp_path/'reports/arthur/legacy/report.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding='utf-8')
    first = backfill_context(tmp_path)
    second = backfill_context(tmp_path)
    assert first['weather_inserted'] == 1 and second['weather_inserted'] == 0
    store = WeatherStore(Database(tmp_path/'data/goal_analysis.sqlite3'))
    assert store.as_of(datetime(2026,10,1,9,30,tzinfo=UTC)) == []
    records = store.as_of(datetime(2026,10,1,10,tzinfo=UTC))
    assert records[0]['time_basis'] == 'REPORT_AVAILABLE_AT_UPPER_BOUND'
    assert records[0]['weather']['latitude'] == 50
    assert records[0]['raw_payload'] is None
    del report['finished_at']
    path.write_text(json.dumps(report), encoding='utf-8')
    assert backfill_context(tmp_path)['weather_skipped'] == 1
