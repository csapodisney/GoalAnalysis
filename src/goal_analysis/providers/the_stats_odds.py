"""Native prematch prices from TheStats. Receipt time is not quote update time.

The feed-shaped events below are an internal normalization interface, not
responses attributed to The Odds API. Every market retains its real source.
"""
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from copy import deepcopy
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
        self.acquisition_outcomes = []
        self.full_day_acquisition = None
        self.calls = self.reserved_credits = 0

    def bind_fixtures(self, fixtures):
        counts = Counter(row.get('fixture_id') for row in fixtures)
        self.fixtures = {fixture_identity(row): row for row in fixtures
                         if row.get('source_provider') == 'the_stats_api' and counts[row['fixture_id']] == 1}

    def configure_full_day(self, fixtures, authority, markets, *, monotonic=time.monotonic, sleep=time.sleep):
        if (self.full_day_acquisition is not None or self.resources.usage['requests'] != 0
                or getattr(self.client, 'request_count', None) != 0
                or self.resources.usage['stopped_reason'] is not None):
            raise ValueError('FRESH_ONCE_FULL_DAY_ODDS_SCOPE_REQUIRED')
        if (not isinstance(authority, dict) or set(authority) != {'version', 'run_id', 'target_date', 'authority'}
                or authority['version'] != 'NORMAL_FULL_DAY_ODDS_V1'
                or authority['authority'] != 'CURRENT_TEN_AGENT_NORMAL_FACTORY'
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(authority['target_date']))
                or not isinstance(authority['run_id'], str)
                or len(authority['run_id']) > 160
                or not re.fullmatch(r'(?:' + re.escape(authority['target_date'])
                    + r'-[A-Za-z0-9][A-Za-z0-9_.-]*|nightly-'
                    + re.escape(authority['target_date']) + r'-a[1-3])', authority['run_id'])):
            raise ValueError('EXACT_FULL_DAY_ODDS_AUTHORITY_REQUIRED')
        if not set(markets) <= FAMILIES.keys():
            raise ValueError('EXACT_CONFIGURED_MARKETS_REQUIRED')
        counts = Counter(row.get('fixture_id') for row in fixtures)
        expected = {fixture_identity(row): row for row in fixtures
            if row.get('source_provider') == 'the_stats_api' and counts[row['fixture_id']] == 1}
        if self.fixtures != expected:
            raise ValueError('EXACT_BOUND_FULL_DAY_CALENDAR_REQUIRED')
        now = utc(self.clock())
        eligible = [key for key, row in self.fixtures.items()
            if row['target_date'] == authority['target_date'] and row['provider_status'] == 'NS'
            and utc(row['kickoff']) > now + timedelta(minutes=15)]
        eligible.sort()
        count = len(eligible)
        limit = count if count <= 512 else 0
        self._odds_monotonic, self._odds_sleep = monotonic, sleep
        self._odds_deadline = monotonic() + 1800
        self._odds_eligible, self._odds_admitted = set(eligible), set()
        self._odds_markets = {'h2h', 'totals', *markets}
        self.full_day_acquisition = {'version': 'NORMAL_FULL_DAY_ODDS_V1', 'authority': deepcopy(authority),
            'started_at': now.isoformat(), 'new_request_admission_deadline': (now + timedelta(seconds=1800)).isoformat(),
            'admission_seconds': 1800, 'hard_ceiling': 512, 'configured_legacy_max_requests': self.max_credits,
            'effective_max_requests': limit, 'eligible_fixture_count': count, 'eligible_provider_fixture_ids': eligible,
            'calendar_fixture_count': len(fixtures), 'calendar_ids_sha256': hashlib.sha256(json.dumps(
                [row['fixture_id'] for row in fixtures], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'requested_markets': sorted(self._odds_markets), 'new_request_admissions': 0,
            'wait_seconds': 0.0, 'refusal_counts': {}, 'customer_currency_cost': None,
            'quota_observation_count': 0, 'last_quota_observation_sha256': None,
            'scope_reason': 'FULL_DAY_HARD_CEILING' if count > 512 else None}
        self.resources.usage['max_requests'] = limit
        self.client.max_requests = limit

    def _full_day_quota(self, now):
        rows = []
        observer = getattr(self.client, 'quota_observer', None)
        reader = getattr(observer, 'legacy_state', None)
        if callable(reader):
            try:
                state = reader()
            except (ValueError, TypeError, KeyError, OSError, RuntimeError, sqlite3.Error):
                return 'QUOTA_OBSERVATION_UNAVAILABLE', None
            if state is not None:
                if not isinstance(state, dict):
                    return 'QUOTA_OBSERVATION_UNAVAILABLE', None
                saved = state.get('_receipts', [state])
                if not isinstance(saved, list):
                    return 'QUOTA_OBSERVATION_UNAVAILABLE', None
                rows.extend(saved)
        receipt = self.client.last_response
        if isinstance(receipt, dict) and receipt.get('authenticated') is True:
            usage = self.client.last_usage
            rows.append({'provider': 'the_stats_api', 'observed_at': receipt.get('observed_at'),
                'http_status': receipt.get('http_status'), 'usage': {key: getattr(usage, key, None)
                    for key in ('monthly_remaining', 'monthly_reset', 'minute_remaining', 'minute_reset', 'retry_after')}})
        latest = {}
        try:
            for row in rows:
                if not isinstance(row, dict) or row.get('provider') != 'the_stats_api':
                    return 'QUOTA_OBSERVATION_UNAVAILABLE', None
                stamp, usage = utc(row['observed_at']), row['usage']
                if stamp > now or not isinstance(usage, dict):
                    return 'QUOTA_OBSERVATION_UNAVAILABLE', None
                for dimension in ('monthly_remaining', 'minute_remaining', 'retry_after', 'http_status'):
                    value = row.get(dimension) if dimension == 'http_status' else usage.get(dimension)
                    if value is None:
                        continue
                    if type(value) is not int or value < (-1 if dimension == 'monthly_remaining' else 0):
                        return 'QUOTA_OBSERVATION_UNAVAILABLE', None
                    if dimension not in latest or stamp > latest[dimension][0]:
                        latest[dimension] = (stamp, value, usage)
                    elif stamp == latest[dimension][0] and value != latest[dimension][1]:
                        return 'QUOTA_OBSERVATION_CONFLICT', None
        except (ValueError, TypeError, KeyError):
            return 'QUOTA_OBSERVATION_UNAVAILABLE', None
        self.full_day_acquisition['quota_observation_count'] += 1
        self.full_day_acquisition['last_quota_observation_sha256'] = hashlib.sha256(
            json.dumps(rows, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        status = latest.get('http_status')
        if status and status[1] in {401, 403}:
            return 'PROVIDER_ACCESS_REQUIRES_RECHECK', None
        retry = latest.get('retry_after')
        if retry:
            try:
                if now < retry[0] + timedelta(seconds=retry[1]):
                    return 'PROVIDER_RETRY_AFTER', None
            except OverflowError:
                return 'QUOTA_OBSERVATION_UNAVAILABLE', None
        if status and status[1] == 429 and now < status[0] + timedelta(seconds=60):
            return 'RATE_LIMITED', None
        month = latest.get('monthly_remaining')
        if month and month[1] == 0:
            reset = month[2].get('monthly_reset')
            if type(reset) is not int or now.timestamp() < reset:
                return 'USAGE_LIMIT_EXCEEDED', None
        return None, latest.get('minute_remaining')

    def _admit_full_day_request(self, endpoint):
        scope = self.full_day_acquisition
        match = re.fullmatch(r'/football/matches/(mt_[0-9]+)/odds', endpoint)
        key = match[1] if match else None
        reason = None
        if key not in self._odds_eligible:
            reason = 'OUTSIDE_FULL_DAY_ODDS_SCOPE'
        elif key in self._odds_admitted:
            reason = 'FULL_DAY_REQUEST_ALREADY_ADMITTED'
        elif self._odds_monotonic() >= self._odds_deadline:
            reason = 'FULL_DAY_ADMISSION_DEADLINE'
        if reason is None:
            now = utc(self.clock())
            reason, minute = self._full_day_quota(now)
            if reason is None and minute and minute[1] <= 2:
                observed, _, usage = minute
                reset = usage.get('minute_reset')
                if type(reset) is not int:
                    reason = 'QUOTA_PACING_UNKNOWN'
                elif now.timestamp() < reset:
                    if (now - observed).total_seconds() > 120:
                        reason = 'QUOTA_PACING_UNKNOWN'
                    else:
                        wait = reset - now.timestamp()
                        if self._odds_monotonic() + wait >= self._odds_deadline:
                            reason = 'FULL_DAY_ADMISSION_DEADLINE'
                        else:
                            while utc(self.clock()).timestamp() < reset and self._odds_monotonic() < self._odds_deadline:
                                part = min(60.0, reset - utc(self.clock()).timestamp())
                                if part > 0:
                                    self._odds_sleep(part)
                                    scope['wait_seconds'] += part
                                    reason, fresh_minute = self._full_day_quota(utc(self.clock()))
                                    if reason is None and fresh_minute and fresh_minute[1] <= 2:
                                        fresh_reset = fresh_minute[2].get('minute_reset')
                                        if type(fresh_reset) is not int:
                                            reason = 'QUOTA_PACING_UNKNOWN'
                                        elif fresh_reset != reset and utc(self.clock()).timestamp() < fresh_reset:
                                            reason = 'QUOTA_PACING_DEFERRED'
                                    if reason is not None:
                                        break
            if reason is None:
                if self._odds_monotonic() >= self._odds_deadline:
                    reason = 'FULL_DAY_ADMISSION_DEADLINE'
                elif utc(self.fixtures[key]['kickoff']) <= utc(self.clock()) + timedelta(minutes=15):
                    reason = 'PREMATCH_LOCKOUT'
        if reason is not None:
            scope['refusal_counts'][reason] = scope['refusal_counts'].get(reason, 0) + 1
            return reason
        self._odds_admitted.add(key)
        scope['new_request_admissions'] += 1
        return None

    def acquisition_receipt(self):
        if self.full_day_acquisition is None:
            return None
        return {**deepcopy(self.full_day_acquisition),
            'resource_attempts': self.resources.usage['requests'],
            'transport_attempts': getattr(self.client, 'request_count', None),
            'event_calls': len(self.acquisition_outcomes),
            'event_available_calls': sum(row['outcome'] == 'EVENT_AVAILABLE' for row in self.acquisition_outcomes),
            'priced_fixture_ids': sorted({row['fixture_id'] for row in self.acquisition_outcomes if row['priced'] is True}),
            'source_outcomes_artifact': 'odds_acquisition',
            'coverage_is_not_a_guarantee_of_prices': True}

    def reserve_check(self, cost):
        if self.resources.usage['stopped_reason']:
            raise ProviderError('TheStats odds: ' + self.resources.usage['stopped_reason'])

    def _event(self, fixture, families):
        before = self.resources.usage['requests']
        transport_before = getattr(self.client, 'request_count', None)
        entered = False
        row = {'version': 'ODDS_ACQUISITION_OUTCOME_V1',
            'fixture_id': fixture['fixture_id'], 'requested_markets': sorted(set(families)),
            'outcome': 'UNAVAILABLE', 'reason_code': 'PREMATCH_LOCKOUT',
            'request_state': 'NOT_ENTERED', 'resource_attempt_delta': None,
            'transport_attempt_delta': None, 'priced': None, 'source_ref': None,
            'available_markets': [], 'missing_requested_markets': None}
        try:
            if utc(fixture['kickoff']) <= utc(self.clock()) + timedelta(minutes=15) or fixture['provider_status'] != 'NS':
                raise ProviderError('TheStats odds pre-match lockout')
            row['reason_code'] = 'INVALID_NATIVE_FIXTURE_IDENTITY'
            match = fixture_identity(fixture)
            row['reason_code'] = 'UNRECORDED_ACQUISITION_ERROR'
            entered = True
            admission = ({'before_request': self._admit_full_day_request}
                if self.full_day_acquisition is not None else {})
            data, source, error = self.resources.get('/football/matches/' + match + '/odds',
                ttl=timedelta(minutes=5), **admission)
            self.calls = self.reserved_credits = self.resources.usage['requests']
            if self.resources.usage['requests'] > before:
                self.usage.append({'provider': 'the_stats_api', 'unit': 'HTTP_REQUESTS',
                    'remaining': self.client.last_usage.monthly_remaining,
                    'used': self.calls, 'last_cost': self.resources.usage['requests'] - before})
            if error:
                row['reason_code'] = (error if isinstance(error, str)
                    and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', error) else 'UNCLASSIFIED_RESOURCE_ERROR')
                raise ProviderError('TheStats odds: ' + error)
            row['reason_code'] = 'INVALID_OR_LATE_SOURCE_RECEIPT'
            if utc(source['observed_at']) >= utc(fixture['kickoff']) - timedelta(minutes=15):
                raise ProviderError('TheStats odds receipt inside lockout')
            row['reason_code'] = 'SOURCE_OR_MARKET_NORMALIZATION_REJECTED'
            result = event_from_prices(data, fixture, source, families)
            available = {market['key'] for book in result['bookmakers'] for market in book['markets']}
            row.update(outcome='EVENT_AVAILABLE', reason_code=None, priced=bool(result['bookmakers']),
                available_markets=sorted(available), missing_requested_markets=sorted(set(families) - available),
                source_ref={key: source.get(key) for key in ('source_url', 'content_hash', 'observed_at')})
            return result
        finally:
            resource_after = self.resources.usage['requests']
            transport_after = getattr(self.client, 'request_count', None)
            for field, first, last in (
                    ('resource_attempt_delta', before, resource_after),
                    ('transport_attempt_delta', transport_before, transport_after)):
                row[field] = last - first if type(first) is int and type(last) is int and 0 <= first <= last else None
            if entered:
                delta = row['transport_attempt_delta']
                row['request_state'] = ('UNKNOWN' if delta is None else
                    'TRANSPORT_ATTEMPTED' if delta else 'NO_NEW_TRANSPORT')
            self.acquisition_outcomes.append(row)

    def bulk(self, sport, target_date):
        result = []
        for fixture in sorted(self.fixtures.values(), key=lambda row: row['fixture_id']):
            if fixture['sport_key'] != sport or fixture['target_date'] != target_date.isoformat():
                continue
            try:
                families = self._odds_markets if self.full_day_acquisition is not None else {'h2h', 'totals'}
                result.append(self._event(fixture, families))
            except (ProviderError, ValueError, TypeError, KeyError):
                outcome = self.acquisition_outcomes[-1]
                self.issues.append({'fixture_id': fixture['fixture_id'], 'status': 'THE_STATS_ODDS_UNAVAILABLE',
                    'acquisition_index': len(self.acquisition_outcomes) - 1,
                    **{key: outcome[key] for key in ('reason_code', 'request_state',
                        'resource_attempt_delta', 'transport_attempt_delta')}})
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
