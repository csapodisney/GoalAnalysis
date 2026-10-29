"""TheStats live client. Exposes native REST endpoints and actual quota facts."""
from datetime import timedelta
from pathlib import Path

from .the_stats_api import TheStatsApiClient, TheStatsApiError


class TheStatsPrimaryClient(TheStatsApiClient):
    provider = 'the_stats_api'

    def __init__(self, root, **kwargs):
        from goal_analysis.storage.provider_quota import observer_for

        self.root = Path(root)
        # An explicit observer owns its tracking policy. None uses the ordinary
        # factory too, so it cannot silently bypass installed tracking.
        if kwargs.get('quota_observer') is None:
            kwargs['quota_observer'] = observer_for(root, clock=kwargs.get('clock'))
        super().__init__(**kwargs)

    def request(self, endpoint, params=None):
        now = self._clock()
        usage, receipt = self.last_usage, self.last_response
        if receipt and endpoint != '/health':
            if (usage.monthly_remaining == 0
                    and (usage.monthly_reset is None or now.timestamp() < usage.monthly_reset)):
                raise TheStatsApiError('USAGE_LIMIT_EXCEEDED', usage=usage)
            from goal_analysis.normalization.canonical import utc

            reset = usage.minute_reset or (utc(receipt['observed_at']) + timedelta(minutes=1)).timestamp()
            if usage.minute_remaining is not None and usage.minute_remaining <= 2 and now.timestamp() < reset:
                raise TheStatsApiError('RATE_LIMITED', usage=usage)
        return super().request(endpoint, params)
