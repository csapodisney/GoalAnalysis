"""The requested match day is separate from the real acquisition clock."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.grok_stats_data import stats_contexts
from goal_analysis.jobs import candidate_context_review as job


@pytest.mark.parametrize('offset', [0, 1, 7])
@pytest.mark.parametrize('observed', [
    datetime(2026, 10, 25, 21, 53, tzinfo=UTC),
    datetime(2026, 10, 24, 23, 30, tzinfo=UTC),
])
def test_requested_day_does_not_replace_real_receipt_or_context_cutoff(
    tmp_path, monkeypatch, offset, observed,
):
    from zoneinfo import ZoneInfo

    from goal_analysis.jobs import expert_advisory, fixture_contexts

    target = observed.astimezone(ZoneInfo('Europe/Berlin')).date() + timedelta(days=offset)
    calls = []
    fixture = {'fixture_id': 'explicit-synthetic-fixture', 'kickoff':
               (observed + timedelta(days=8)).isoformat()}
    before = deepcopy(fixture)
    monkeypatch.setattr(job, 'context_scope_packets', lambda *_a: [fixture])

    def collect(_root, seed, **kwargs):
        calls.append(deepcopy(seed))
        assert kwargs['clock']() == observed
        return {'context_collection_shadow': {'collection': {
            'date': seed['date'], 'observed_at': observed.isoformat(),
            'usage': {'requests': 0}, 'context_by_fixture': {},
        }}}

    monkeypatch.setattr(expert_advisory, 'enrich_sports_supplement', collect)
    monkeypatch.setattr(fixture_contexts, 'link_report', lambda *_a, **_k: {})
    monkeypatch.setattr('goal_analysis.jobs.xg_specialist_shadow._targets',
                        lambda *_a: ({}, {}, {}))
    monkeypatch.setattr(job, 'compose', lambda _root, _fixtures, _packets, collection,
                        *, as_of: {'collection': deepcopy(collection), 'as_of': as_of.isoformat()})
    prepared = job.prepare(tmp_path, [fixture], [], run_id='synthetic-target-day',
                           target_date=target, clock=lambda: observed)
    assert len(calls) == 1 and calls[0]['date'] == target.isoformat()
    assert prepared['collection']['date'] == target.isoformat()
    assert prepared['collection']['observed_at'] == prepared['as_of'] == observed.isoformat()
    assert prepared['started_at'] == observed.isoformat()
    assert fixture == before


@pytest.mark.parametrize('invalid', ['2026-10-26', 'tomorrow', 0,
                                   datetime(2026, 10, 26, tzinfo=UTC)])
def test_invalid_explicit_day_rejected_before_acquisition(tmp_path, monkeypatch, invalid):
    from goal_analysis.jobs import expert_advisory

    def forbidden(*_a, **_k):
        pytest.fail('Invalid date must not authorize a source collection')

    monkeypatch.setattr(expert_advisory, 'enrich_sports_supplement', forbidden)
    with pytest.raises(ValueError, match='INVALID_CONTEXT_TARGET_DAY'):
        job.prepare(tmp_path, [], [], run_id='invalid-target', target_date=invalid)


def test_actual_portfolio_forwards_its_validated_target_day(tmp_path, monkeypatch):
    import json

    from test_daily_223_live import NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg, football, odds, cache, *_ = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3', now=lambda: NOW)
    folder = tmp_path/'config'
    folder.mkdir(exist_ok=True)
    (folder/'candidate-context-review.json').write_text(json.dumps({
        'version': job.VERSION, 'mode': 'SHADOW'}))
    seen = []

    def prepare(*_a, **kwargs):
        seen.append(kwargs)
        return {'contexts': {}, 'as_of': NOW.isoformat(), 'collection': {'usage': {'requests': 0}}}

    monkeypatch.setattr(job, 'prepare', prepare)
    run_portfolio(cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(), ledger,
                  clock=lambda: NOW)
    assert len(seen) == 1 and seen[0]['target_date'] == NOW.date()
    assert seen[0]['clock']() == NOW


def test_strict_grok_scope_still_rejects_wrong_day_and_future_receipt():
    now = datetime(2026, 10, 25, 22, tzinfo=UTC)
    supplement = {'version': 'thestatsapi-context-v1', 'provider': 'the_stats_api',
                  'date': '2026-10-26', 'observed_at': now.isoformat(),
                  'context_by_fixture': {}}
    report = {'date': '2026-10-26', 'fixtures': [],
              'sports_supplement': {'the_stats_api': supplement}}
    assert stats_contexts(report, now.isoformat()) == {}
    before = deepcopy(report)
    supplement['date'] = '2026-10-25'
    with pytest.raises(ValueError, match='Invalid sports supplement scope'):
        stats_contexts(report, now.isoformat())
    report = before
    report['sports_supplement']['the_stats_api']['observed_at'] = (now+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match='Invalid sports supplement scope'):
        stats_contexts(report, now.isoformat())
