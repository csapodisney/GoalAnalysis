"""Native bookmaker price-context binding: exact pure identity and fallback controls."""
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.jobs.expert_advisory import _shadow_quote as candidate
from goal_analysis.providers.the_stats_odds import bookmaker_key

OBSERVED = datetime(2026, 10, 26, tzinfo=UTC).isoformat()


@pytest.fixture(autouse=True)
def forbid_io_and_model_operations(monkeypatch):
    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append('FORBIDDEN_IO_OR_MODEL_OPERATION')
        raise AssertionError(attempts[-1])

    for name in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex',
                 'urllib.request.urlopen', 'sqlite3.connect',
                 'goal_analysis.storage.db.Database.__init__',
                 'goal_analysis.models.predict_models'):
        monkeypatch.setattr(name, forbidden)
    yield attempts
    assert not attempts


def leg():
    return {'candidate_id': 'TESTONLY', 'fixture_id': 'the_stats_api:mt_123',
            'market_key': 'totals_2_5', 'selection_key': 'over', 'period': 'FULL_TIME',
            'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
            'bookmaker': bookmaker_key('Bet365'), 'provider_bookmaker': 'Bet365',
            'provider_market_id': 'total_goals', 'quote_provider': 'the_stats_api',
            'quote_source_id': 'https://api.thestatsapi.com/api/football/matches/mt_123/odds',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': OBSERVED,
            'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',
            'decimal_price': 2.0, 'quoted_at': None}


def signal():
    return {'shadow_odds_quotes': [{'bookmaker': 'Bet365', 'market_key': 'totals',
            'selection_key': 'over', 'point': 2.5, 'opening_price': 1.9, 'last_price': 2.1,
            'observed_at': OBSERVED, 'source_content_hash': 'a' * 64,
            'timestamp_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME'}]}


def test_exact_native_bookmaker_comparison_without_mutation():
    row, context = leg(), signal()
    before = deepcopy([row, context])
    result = candidate(row, context)
    assert result['last_price'] == 2.1 and result['candidate_price'] == 2.0
    assert result['relative_price_difference'] == pytest.approx(.05)
    assert result['shadow_only'] is True
    assert result['observed_at'] == OBSERVED
    assert [row, context] == before and row['quoted_at'] is None


@pytest.mark.parametrize(('field', 'value'), [
    ('bookmaker', 'bet365'),
    ('bookmaker', bookmaker_key('Paddy Power')),
    ('provider_bookmaker', 'Paddy Power'),
    ('provider_bookmaker', None),
    ('quote_source_id', 'https://api.thestatsapi.com/api/football/matches/mt_999/odds'),
    ('quote_source_sha256', 'bad'),
    ('quote_observed_at', None),
    ('quote_time_basis', 'PROVIDER_QUOTE_TIME'),
    ('provider_market_id', 'first_half_total_goals'),
    ('period', 'FIRST_HALF'),
    ('settlement', 'FIRST_HALF_PLUS_STOPPAGE'),
    ('market_key', 'totals_3_5'),
    ('selection_key', 'under'),
])
def test_native_alias_clock_product_or_period_mismatch_is_local_no_comparison(field, value):
    row = leg()
    row[field] = value
    assert candidate(row, signal()) is None


@pytest.mark.parametrize(('field', 'value'), [
    ('bookmaker', 'Paddy Power'), ('bookmaker', 'bet365'),
    ('market_key', 'btts'), ('selection_key', 'under'), ('point', 3.5),
])
def test_other_supplementary_product_never_substitutes(field, value):
    context = signal()
    context['shadow_odds_quotes'][0][field] = value
    assert candidate(leg(), context) is None


def test_duplicate_exact_quote_is_ambiguous_and_empty_is_optional():
    context = signal()
    context['shadow_odds_quotes'] *= 2
    assert candidate(leg(), context) is None
    assert candidate(leg(), {'shadow_odds_quotes': []}) is None


def test_legacy_bookmaker_matching_remains_byte_equal():
    row = {**leg(), 'quote_provider': 'the_odds_api', 'bookmaker': 'bet365'}
    context = signal()
    quote = context['shadow_odds_quotes'][0]
    assert candidate(row, context) == {**quote, 'candidate_price': 2.0,
        'relative_price_difference': 2.1 / 2.0 - 1,
        'opening_to_last_movement': 2.1 / 1.9 - 1, 'shadow_only': True}


@pytest.mark.parametrize(('market', 'provider_market', 'selection'), [
    ('h2h', 'match_odds', 'home'), ('btts', 'btts', 'yes'),
])
def test_other_existing_full_time_markets_bind_exactly(market, provider_market, selection):
    row = {**leg(), 'market_key': market, 'provider_market_id': provider_market,
           'selection_key': selection}
    context = signal()
    context['shadow_odds_quotes'][0].update(market_key=market, selection_key=selection, point=None)
    assert candidate(row, context)['candidate_price'] == 2.0
