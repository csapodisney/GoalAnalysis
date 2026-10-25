"""Plan bounded source-exact DAILY review requests; never select ticket legs."""
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.daily_223 import FLOORS, Daily223Policy
from goal_analysis.engine.portfolio import prepare_portfolio_candidate
from goal_analysis.engine.publication_window import allows_new_leg

VERSION = 'daily-review-cohort-v1'
COHORT = ('bookmaker', 'region', 'currency')


def policy(root):
    path = Path(root) / 'config/daily-review-cohort.json'
    if not path.is_file():
        return 'SHADOW'
    if path.stat().st_size > 1000:
        raise ValueError('INVALID_DAILY_REVIEW_COHORT_POLICY')
    value = json.loads(path.read_bytes())
    if value not in ({'version': VERSION, 'mode': mode} for mode in ('OFF', 'SHADOW', 'ACTIVE')):
        raise ValueError('INVALID_DAILY_REVIEW_COHORT_POLICY')
    return value['mode']


def plan(enriched, report, reviewed_ids, missing_ids, *, maximum, now):
    """Selected gaps first, then eligible alternatives from their exact cohort.

    This changes request coverage only. Original arithmetic, solver pools,
    evidence weights and DRAFT/READY safety remain the engine's responsibility.
    """
    if type(maximum) is not int or not 1 <= maximum <= 30:
        raise ValueError('INVALID_DAILY_REVIEW_BATCH_LIMIT')
    rows = enriched['candidates']
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('INVALID_DAILY_REVIEW_SOURCE_POOL')
    counts = Counter(row['candidate_id'] for row in rows)
    if any(count != 1 for count in counts.values()):
        raise ValueError('AMBIGUOUS_DAILY_REVIEW_QUOTE_ID')
    index = {row['candidate_id']: row for row in rows}
    if (len(missing_ids) > maximum or len(set(missing_ids)) != len(missing_ids)
            or any(identifier not in index or identifier in reviewed_ids for identifier in missing_ids)):
        raise ValueError('INVALID_MISSING_DAILY_REVIEW_QUOTES')
    selected_ids = {leg['candidate_id'] for ticket in report.get('tickets', [])
                    if ticket['profile_id'] == 'daily223' for leg in ticket.get('legs', [])}
    if not set(missing_ids) <= selected_ids:
        raise ValueError('MISSING_QUOTE_NOT_IN_SELECTED_DAILY')
    daily_policy = Daily223Policy(**enriched.get('policy', {}))
    floor = min(FLOORS) * (1 - Decimal(str(daily_policy.tolerance)))
    cohorts = {tuple(index[identifier].get(key) for key in COHORT) for identifier in missing_ids}
    eligible = {}
    for identifier, row in index.items():
        if identifier in reviewed_ids or tuple(row.get(key) for key in COHORT) not in cohorts:
            continue
        try:
            prepared = prepare_portfolio_candidate(row, date.fromisoformat(enriched['date']), now,
                                                    daily_policy, report['policy'])
            if Decimal(str(prepared['decimal_price'])) < floor or not allows_new_leg(row, now):
                continue
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        eligible[identifier] = prepared
    if not set(missing_ids) <= set(eligible):
        raise ValueError('SELECTED_DAILY_QUOTE_NO_LONGER_REVIEW_ELIGIBLE')
    alternatives = sorted(set(eligible) - set(missing_ids),
                          key=lambda identifier: (-eligible[identifier]['support_score'], identifier))
    chosen = [*missing_ids, *alternatives][:maximum]
    return {'version': VERSION, 'candidate_ids': chosen, 'selected_missing_ids': list(missing_ids),
            'additional_cohort_quotes': len(chosen) - len(missing_ids),
            'eligible_unreviewed_cohort_quotes': len(eligible),
            'unrequested_eligible_quotes': len(eligible) - len(chosen),
            'source_quote_sha256': {identifier: canonical_sha256(index[identifier]) for identifier in chosen},
            'maximum_candidates': maximum, 'new_sports_requests': 0, 'new_numeric_fits': 0,
            'solver_or_probability_changed': False, 'convergence_guaranteed': False}
