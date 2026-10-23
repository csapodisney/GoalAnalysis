from copy import deepcopy
from datetime import UTC, datetime, timedelta

from goal_analysis.jobs.nightly import NightlyWorkflow

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def job():
    return {'deadline': (NOW + timedelta(hours=2)).isoformat(),
            'stages': [{'name': 'archive', 'status': 'SUCCESS'}]}


def test_optional_history_is_claimed_once_after_publication(tmp_path, monkeypatch):
    from goal_analysis.jobs import weather_history

    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    saved, calls = [], []
    monkeypatch.setattr(workflow, '_save', lambda j: saved.append(deepcopy(j)))

    def fake(root, **kwargs):
        calls.append(kwargs)
        assert saved[0]['weather_history']['status'] == 'STARTED'
        return {'status': 'COMPLETE', 'requests_used': 2}

    monkeypatch.setattr(weather_history, 'backfill_weather', fake)
    state = job()
    assert workflow._weather_history(state)['status'] == 'COMPLETE'
    workflow._weather_history(state)
    assert len(calls) == 1 and calls[0]['max_requests'] == 2


def test_daily_failure_or_no_spare_time_never_fetches(tmp_path, monkeypatch):
    from goal_analysis.jobs import weather_history

    monkeypatch.setattr(weather_history, 'backfill_weather', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network')))
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    state = job()
    state['stages'][0]['status'] = 'PARTIAL'
    assert workflow._weather_history(state)['status'] == 'SKIPPED_DAILY_INCOMPLETE'
    state['stages'][0]['status'] = 'SUCCESS'
    state['deadline'] = (NOW + timedelta(seconds=10)).isoformat()
    assert workflow._weather_history(state)['status'] == 'SKIPPED_NO_SPARE_WINDOW'


def test_history_failure_and_interrupted_claim_do_not_replay(tmp_path, monkeypatch):
    from goal_analysis.jobs import weather_history

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError('sensitive details')

    monkeypatch.setattr(weather_history, 'backfill_weather', fail)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, '_save', lambda j: None)
    state = job()
    assert workflow._weather_history(state) == {'status': 'FAILED', 'error_code': 'WEATHER_HISTORY_UNAVAILABLE'}
    workflow._weather_history(state)
    state['weather_history'] = {'status': 'STARTED'}
    workflow._weather_history(state)
    assert len(calls) == 1
