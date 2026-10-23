"""Native receipt URLs retain identity and reject route/query ambiguity."""
import pytest

from goal_analysis.normalization.live_source import stats_source_url


def test_valid_source_preserves_exact_query_order_and_receipt_identity():
    value = 'https://api.thestatsapi.com/api/football/matches?status=scheduled&page=2&per_page=5'
    assert stats_source_url(value) == value


@pytest.mark.parametrize('value', [
    'http://api.thestatsapi.com/api/football/matches',
    'https://api.thestatsapi.com.evil.test/api/football/matches',
    'https://user@api.thestatsapi.com/api/football/matches',
    'https://api.thestatsapi.com:443/api/football/matches',
    'https://api.thestatsapi.com/api/football/matches#fragment',
    'https://api.thestatsapi.com/api/football/matches?page=1&page=2',
    'https://api.thestatsapi.com/api/football/matches?page=wrong',
    'https://api.thestatsapi.com/api/football/matches?page=0',
    'https://api.thestatsapi.com/api/football/matches?per_page=999999',
    'https://api.thestatsapi.com/api/football/matches?unknown=value',
    'https://api.thestatsapi.com/api/football/matches?api_key=synthetic',
    'https://api.thestatsapi.com/api/football/seasons?current=TRUE',
    'https://api.thestatsapi.com/api/not-a-route',
])
def test_invalid_source_url_or_ambiguous_parameter_is_rejected(value):
    with pytest.raises(ValueError):
        stats_source_url(value)
