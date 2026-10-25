"""Real producer boundaries for the existing shared DAILY follow-up budget."""
import json
from copy import deepcopy

from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.engine import recommendations
from goal_analysis.jobs import candidate_context_review, portfolio, profile_context_stage
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def run(tmp_path, monkeypatch, *, mode='ACTIVE', failure=False, stage=False):
    cfg, football, odds, cache, *_ = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: NOW)
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'config/daily-review-cohort.json').write_text(json.dumps({
        'version': 'daily-review-cohort-v1', 'mode': mode}))
    packets = recommendations.recommendation_packets
    monkeypatch.setattr(recommendations, 'recommendation_packets',
                        lambda *args, **kwargs: packets(*args, **kwargs)[:1])
    remaining = []
    if stage:
        monkeypatch.setattr(candidate_context_review, 'policy', lambda _root: 'ACTIVE')
        monkeypatch.setattr(profile_context_stage, 'policy', lambda _root: 'ACTIVE')
        monkeypatch.setattr(candidate_context_review, 'prepare', lambda *_a, **_k: {
            'as_of': NOW.isoformat(), 'contexts': {}, 'collection': {}, 'weather_by_fixture': {}})

        def observe(report, options, artifacts, prepared, quotes, review, **kwargs):
            remaining.append(kwargs['remaining_rounds'])
            return report, options, review
        monkeypatch.setattr(profile_context_stage, 'execute', observe)

    class Reviewer(RecordedReviewer):
        def review(self, candidates, target_date, feedback=None):
            if failure and self.calls:
                self.calls.append((deepcopy(candidates), target_date, feedback))
                return {'status': 'FAILED', 'reviews': [], 'usage': {'requests': 2, 'input_tokens': 50}}
            return super().review(candidates, target_date, feedback)

    reviewer = Reviewer()
    bundle = portfolio.run_portfolio(cfg, {'enabled_profiles': ['daily223']}, football, odds,
        cache, NOW.date(), reviewer, ledger, clock=lambda: NOW)
    return bundle, reviewer, remaining


def test_active_producer_closes_selected_daily_review_gap_within_existing_budget(tmp_path, monkeypatch):
    bundle, reviewer, _ = run(tmp_path, monkeypatch)
    report = bundle['artifacts']['report']
    daily = next(ticket for ticket in report['tickets'] if ticket['profile_id'] == 'daily223')
    assert len(reviewer.calls) == 2 and len(reviewer.calls[0][0]) == 1
    primary_ids = {row['candidate_id'] for row in reviewer.calls[0][0]}
    selected_gaps = {leg['candidate_id'] for leg in daily['legs']} - primary_ids
    assert len(selected_gaps) == 2
    assert {row['candidate_id'] for row in reviewer.calls[1][0][:2]} == selected_gaps
    assert len(reviewer.calls[1][0]) > len(selected_gaps)  # Actual eligible cohort expansion.
    assert daily['status'] == 'READY' and len(daily['legs']) == 3
    assert report['astra']['daily223_follow_up_attempts'] == 1
    assert report['astra']['daily223_follow_up_rounds'] == 1
    assert report['astra']['daily223_follow_up_failed'] is False
    assert all(len(call[0]) <= 30 for call in reviewer.calls)
    assert all(leg['astra_review']['candidate_id'] == leg['candidate_id'] for leg in daily['legs'])


def test_failed_daily_followup_keeps_primary_and_usage_and_does_not_retry_in_profile_stage(tmp_path, monkeypatch):
    bundle, reviewer, remaining = run(tmp_path, monkeypatch, failure=True, stage=True)
    review = bundle['artifacts']['report']['astra']
    assert len(reviewer.calls) == 2 and remaining == [0]
    assert review['status'] == 'COMPLETE' and len(review['reviews']) == 1
    assert review['daily223_follow_up_rounds'] == 0 and review['daily223_follow_up_attempts'] == 1
    assert review['daily223_follow_up_failed'] is True
    assert review['usage']['requests'] == 3 and review['usage']['input_tokens'] == 160
    daily = next(ticket for ticket in bundle['artifacts']['report']['tickets'] if ticket['profile_id'] == 'daily223')
    assert daily['status'] == 'DRAFT'


def test_off_and_shadow_keep_identical_actual_requests_and_constructed_daily(tmp_path, monkeypatch):
    off, off_reviewer, _ = run(tmp_path / 'off', monkeypatch, mode='OFF')
    shadow, shadow_reviewer, _ = run(tmp_path / 'shadow', monkeypatch, mode='SHADOW')
    # Ledger run UUID differs, but neither request payload nor independent ticket does.
    assert off_reviewer.calls == shadow_reviewer.calls
    assert off['artifacts']['report']['tickets'] == shadow['artifacts']['report']['tickets']
    assert 'daily223_follow_up_attempts' not in shadow['artifacts']['report']['astra']
    assert shadow['artifacts']['daily_review_cohort_plans'][0]['decision_effect'] == 'NONE'


def test_reselection_after_three_cohort_rounds_stays_draft_without_fourth_call(tmp_path, monkeypatch):
    from test_daily_223 import candidate
    from test_portfolio import source

    cfg, football, odds, cache, *_ = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: NOW)
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'config/daily-review-cohort.json').write_text(json.dumps({
        'version': 'daily-review-cohort-v1', 'mode': 'ACTIVE'}))
    synthetic = source([candidate(f'synthetic-{i:03}', (2, 2, 3)[i % 3]) for i in range(130)])
    monkeypatch.setattr(portfolio, 'enrich_daily223_input', lambda *_args: deepcopy(synthetic))
    packets = recommendations.recommendation_packets
    monkeypatch.setattr(recommendations, 'recommendation_packets',
                        lambda *args, **kwargs: packets(*args, **kwargs)[:1])

    class CautiousReviewer(RecordedReviewer):
        def review(self, candidates, target_date, feedback=None):
            result = super().review(candidates, target_date, feedback)
            for row in result['reviews']:
                row['assessment'] = 'caution'
            return result

    reviewer = CautiousReviewer()
    bundle = portfolio.run_portfolio(cfg, {'enabled_profiles': ['daily223']}, football, odds,
        cache, NOW.date(), reviewer, ledger, clock=lambda: NOW)
    report = bundle['artifacts']['report']
    assert len(reviewer.calls) == 4  # One primary plus the original three follow-up attempts.
    assert [len(call[0]) for call in reviewer.calls[1:]] == [30, 30, 30]
    assert report['astra']['daily223_follow_up_attempts'] == 3
    daily = next(ticket for ticket in report['tickets'] if ticket['profile_id'] == 'daily223')
    reviewed = {row['candidate_id'] for row in report['astra']['reviews']}
    assert any(leg['candidate_id'] not in reviewed for leg in daily['legs'])
    assert daily['status'] == 'DRAFT' and len(daily['legs']) == 3
