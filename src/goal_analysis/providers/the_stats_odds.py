"""Native prematch prices from TheStats. Receipt time is not quote update time.

The feed-shaped events below are an internal normalization interface, not
responses attributed to The Odds API. Every market retains its real source.
"""
import hashlib
import math
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.providers.base import ProviderError

QUOTE_PROVENANCE = ('quote_observed_at', 'quote_source_sha256', 'quote_time_basis',
                    'provider_bookmaker', 'provider_market_id')
FAMILIES = {'h2h': 'match_odds', 'totals': 'total_goals', 'btts': 'btts',
            'h2h_3_way_h1': 'first_half_result', 'totals_h1': 'first_half_total_goals',
            'btts_h1': 'btts_first_half'}


def bookmaker_key(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError('Invalid TheStats bookmaker')
    slug = re.sub(r'[^a-z0-9]+', '_', name.casefold()).strip('_')
    if not slug:
        raise ValueError('Invalid TheStats bookmaker')
    return slug + '_thestats_' + hashlib.sha256(name.encode()).hexdigest()[:8]


def _price(value):
    value = value.get('last_seen') if isinstance(value, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 1 < number <= 1000 else None


def event_from_prices(raw, fixture, source, families):
    match = fixture_identity(fixture)
    if not isinstance(raw, dict) or raw.get('match_id') != match or not isinstance(raw.get('bookmakers'), list):
        raise ValueError('TheStats odds fixture identity mismatch')
    counts = Counter(bookmaker_key(book.get('bookmaker')) for book in raw['bookmakers'])
    books = []
    for book in raw['bookmakers']:
        name, markets = book['bookmaker'], book.get('markets')
        key = bookmaker_key(name)
        if counts[key] != 1 or not isinstance(markets, dict):
            continue
        parsed = {}
        for family in sorted(set(families) & FAMILIES.keys()):
            native_market = FAMILIES[family]
            values = markets.get(native_market)
            groups = values.items() if family in {'totals', 'totals_h1'} and isinstance(values, dict) else [(None, values)]
            for line, outcomes in groups:
                if not isinstance(outcomes, dict):
                    continue
                point = None
                if line is not None:
                    try:
                        decimal = Decimal(str(line))
                        if not decimal.is_finite() or not 0 < decimal < 20 or decimal % 1 != Decimal('.5'):
                            continue  # Asian integer/quarter lines have different settlements.
                        point = float(decimal)
                    except (ValueError, InvalidOperation):
                        continue
                wanted = {'home', 'away', 'draw'} if family in {'h2h', 'h2h_3_way_h1'} else {'over', 'under'} if line is not None else {'yes', 'no'}
                if not wanted <= outcomes.keys():
                    continue
                prices = {selection: _price(outcomes[selection]) for selection in wanted}
                if any(price is None for price in prices.values()):
                    continue
                normalized = []
                for selection, price in sorted(prices.items()):
                    label = fixture[selection + '_team'] if selection in {'home', 'away'} else selection.title()
                    outcome = {'name': label, 'price': price}
                    if point is not None:
                        outcome['point'] = point
                    normalized.append(outcome)
                market = parsed.setdefault(family, {'key': family, 'outcomes': [], 'last_update': None,
                    'quote_provider': 'the_stats_api', 'quote_source_id': source['source_url'],
                    'quote_source_sha256': source['content_hash'], 'quote_observed_at': source['observed_at'],
                    'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',
                    'provider_market_id': native_market, 'provider_bookmaker': name})
                market['outcomes'].extend(normalized)
        if parsed:
            books.append({'key': key, 'title': name, 'last_update': None, 'markets': list(parsed.values())})
    return {'id': match, 'source_provider': 'the_stats_api', 'sport_key': fixture['sport_key'],
            'home_team': fixture['home_team'], 'away_team': fixture['away_team'],
            'commence_time': fixture['kickoff'], 'bookmakers': books}


def valid_quote_source(quote, fixture=None):
    from urllib.parse import urlsplit

    try:
        identifier = (fixture or quote)['fixture_id']
        if not re.fullmatch(r'the_stats_api:mt_[0-9]{1,30}', identifier):
            return False
        source = urlsplit(quote['quote_source_id'])
        if (source.scheme != 'https' or source.netloc != 'api.thestatsapi.com'
                or source.path != '/api/football/matches/' + identifier.split(':')[1] + '/odds'
                or source.query or source.fragment
                or quote.get('quote_provider') != 'the_stats_api'
                or not re.fullmatch(r'[0-9a-f]{64}', str(quote.get('quote_source_sha256')))
                or quote.get('quote_time_basis') != 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME'
                or bookmaker_key(quote.get('provider_bookmaker')) != quote.get('bookmaker')):
            return False
        utc(quote['quote_observed_at'])
        return True
    except (ValueError, TypeError, KeyError):
        return False


class TheStatsOddsFeed:
    provider = 'the_stats_api'

    def __init__(self, root, region, max_credits, *, client=None, clock=lambda: datetime.now(UTC)):
        from goal_analysis.jobs.the_stats_context import Resources

        from .the_stats_primary import TheStatsPrimaryClient

        self.region, self.max_credits, self.clock = region, max_credits, clock
        self.client = client or TheStatsPrimaryClient(root, max_requests=max(1, max_credits), clock=clock)
        self.resources = Resources(root, self.client, clock, max_credits)
        self.fixtures, self.usage, self.issues = {}, [], []
        self.calls = self.reserved_credits = 0

    def bind_fixtures(self, fixtures):
        counts = Counter(row.get('fixture_id') for row in fixtures)
        self.fixtures = {fixture_identity(row): row for row in fixtures
                         if row.get('source_provider') == 'the_stats_api' and counts[row['fixture_id']] == 1}

    def reserve_check(self, cost):
        if self.resources.usage['stopped_reason']:
            raise ProviderError('TheStats odds: ' + self.resources.usage['stopped_reason'])

    def _event(self, fixture, families):
        if utc(fixture['kickoff']) <= utc(self.clock()) + timedelta(minutes=15) or fixture['provider_status'] != 'NS':
            raise ProviderError('TheStats odds pre-match lockout')
        match = fixture_identity(fixture)
        before = self.resources.usage['requests']
        data, source, error = self.resources.get('/football/matches/' + match + '/odds', ttl=timedelta(minutes=5))
        self.calls = self.reserved_credits = self.resources.usage['requests']
        if self.resources.usage['requests'] > before:
            self.usage.append({'provider': 'the_stats_api', 'unit': 'HTTP_REQUESTS',
                'remaining': self.client.last_usage.monthly_remaining,
                'used': self.calls, 'last_cost': self.resources.usage['requests'] - before})
        if error:
            raise ProviderError('TheStats odds: ' + error)
        if utc(source['observed_at']) >= utc(fixture['kickoff']) - timedelta(minutes=15):
            raise ProviderError('TheStats odds receipt inside lockout')
        return event_from_prices(data, fixture, source, families)

    def bulk(self, sport, target_date):
        result = []
        for fixture in sorted(self.fixtures.values(), key=lambda row: row['fixture_id']):
            if fixture['sport_key'] != sport or fixture['target_date'] != target_date.isoformat():
                continue
            try:
                result.append(self._event(fixture, {'h2h', 'totals'}))
            except (ProviderError, ValueError, TypeError, KeyError):
                self.issues.append({'fixture_id': fixture['fixture_id'], 'status': 'THE_STATS_ODDS_UNAVAILABLE'})
        return result

    def extra(self, sport, event_id, markets):
        fixture = self.fixtures.get(event_id)
        if not fixture or fixture['sport_key'] != sport:
            raise ProviderError('Unknown native odds fixture')
        event = self._event(fixture, markets)
        if not event['bookmakers']:
            # A successful HTTP response is not a successfully priced product.
            # Keep any other requested family, and leave existing base odds alone.
            raise ProviderError('TheStats requested markets unavailable')
        return event


def attach_provenance(report, candidates):
    """Keep supplementary source fields around the unchanged DAILY solver."""
    by_id = {row['candidate_id']: row for row in candidates}
    for group in ('legs', 'candidate_shortlist'):
        for leg in report.get(group, []):
            original = by_id.get(leg.get('candidate_id'), {})
            if original.get('quote_provider') == 'the_stats_api':
                for field in QUOTE_PROVENANCE:
                    if field in original:
                        leg[field] = original[field]
