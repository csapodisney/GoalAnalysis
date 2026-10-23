"""Already collected stats cannot be invented, future-dated or self-authorized."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.grok_stats_data import VERSION, stats_contexts, validate_stats_context

NOW = datetime(2026, 10, 21, 10, tzinfo=UTC)


def fixture(number=1, *, kickoff=None):
    return {'fixture_id': f'the_stats_api:mt_{number}',
            'kickoff': (kickoff or NOW + timedelta(hours=1)).isoformat()}


def test_missing_supplement_is_empty_not_fabricated_statistics():
    assert stats_contexts({'fixtures': [fixture()]}, NOW) == {}


def test_unavailable_context_remains_unknown_and_excludes_started_fixtures():
    report = {'fixtures': [fixture(), fixture(2, kickoff=NOW)],
              'sports_supplement': {'the_stats_api': {
                  'status': 'UNAVAILABLE', 'observed_at': NOW.isoformat()}}}
    before = deepcopy(report)
    contexts = stats_contexts(report, NOW)
    assert set(contexts) == {'the_stats_api:mt_1'}
    assert contexts['the_stats_api:mt_1']['facts'] == []
    assert contexts['the_stats_api:mt_1']['status'] == 'UNAVAILABLE'
    assert contexts['the_stats_api:mt_1']['missing'] == ['THE_STATS_CONTEXT_UNAVAILABLE']
    assert report == before


def test_future_unavailable_context_is_rejected_too():
    report = {'fixtures': [fixture()], 'sports_supplement': {'the_stats_api': {
        'status': 'UNAVAILABLE', 'observed_at': (NOW + timedelta(seconds=1)).isoformat()}}}
    with pytest.raises(ValueError, match='Future unavailable'):
        stats_contexts(report, NOW)


def test_unmatched_fixture_cannot_claim_available_facts():
    context = {'version': VERSION, 'provider': 'the_stats_api', 'status': 'UNMATCHED',
        'observed_at': NOW.isoformat(), 'facts': [
            {'category': 'team_profile', 'status': 'AVAILABLE', 'subject_id': 'tm_1'}]}
    with pytest.raises(ValueError, match='lack verified fixture mapping'):
        validate_stats_context(context, fixture(), NOW)


@pytest.mark.parametrize('changes', [
    {'fixture_id': 'the_stats_api:mt_2'}, {'provider': 'untrusted'},
    {'observed_at': NOW.replace(tzinfo=None).isoformat()},
    {'observed_at': (NOW + timedelta(seconds=1)).isoformat()},
])
def test_context_identity_or_time_mismatch_cannot_enter_review(changes):
    context = {'version': VERSION, 'provider': 'the_stats_api', 'status': 'UNAVAILABLE',
               'observed_at': NOW.isoformat(), 'facts': [], **changes}
    with pytest.raises(ValueError):
        validate_stats_context(context, fixture(), NOW)
