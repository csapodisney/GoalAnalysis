"""Explicit bounded promotion of already eligible club fixture routes."""
import json
import math
from pathlib import Path

from goal_analysis.agents import canonical_sha256

VERSION = 'club-fixture-coverage-activation-v1'
MODES = {'OFF', 'SHADOW', 'ACTIVE'}


def policy(root):
    path = Path(root) / 'config/club-fixture-coverage.json'
    if not path.exists():
        return 'SHADOW'
    if not path.is_file() or path.stat().st_size > 1000:
        raise ValueError('INVALID_CLUB_COVERAGE_POLICY')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or set(value) != {'version', 'mode'}
            or value['version'] != VERSION or not isinstance(value['mode'], str) or value['mode'] not in MODES):
        raise ValueError('INVALID_CLUB_COVERAGE_POLICY')
    return value['mode']


def _identity(group):
    return tuple(sorted(row['quote']['candidate_id'] for row in group))


def _fixtures(options):
    return {row['quote']['fixture_id'] for group in options for row in group}


def promote(ranked, persona, options, search, *, challenger, rank, maximum):
    """Preserve baseline best, cover supplied routes, then fill by original rank.

    No probability, quality floor, source row or prediction is changed. This
    bounded search does not guarantee a globally optimal or lossless pool.
    """
    if not search['bounded_search'] or not search['eligible_fixtures_without_option_count']:
        return options, search, None
    if type(maximum) is not int or maximum < 1:
        raise ValueError('INVALID_CLUB_COVERAGE_BOUND')
    proposed, extra = challenger()
    supplied = {}
    for row in ranked:
        identifier = row['quote']['candidate_id']
        digest = canonical_sha256(row)
        if identifier in supplied and supplied[identifier] != digest:
            raise ValueError('AMBIGUOUS_CLUB_COVERAGE_SOURCE')
        supplied[identifier] = digest

    def checked(group):
        if not group or any(supplied.get(row['quote']['candidate_id']) != canonical_sha256(row) for row in group):
            raise ValueError('CLUB_COVERAGE_SOURCE_CHANGED')
        quotes = [row['quote'] for row in group]
        prices = [quote['decimal_price'] for quote in quotes]
        fixtures = [quote['fixture_id'] for quote in quotes]
        cohort = {(quote['bookmaker'], quote['region'], quote['currency']) for quote in quotes}
        if (len(set(fixtures)) != len(fixtures) or len(cohort) != 1
                or not all(next(iter(cohort)))
                or any(type(price) not in (int, float) or not math.isfinite(price) or price <= 1 for price in prices)):
            raise ValueError('INVALID_CLUB_COVERAGE_GROUP')
        price = math.prod(prices)
        band = persona['target_odds_band']
        exceptional = (len(group) == 1 and price > band['max']
                       and 'HIGH_ODDS' in (group[0].get('veto_override') or {}).get('gates', []))
        if not exceptional and not (persona['min_legs'] <= len(group) <= persona['max_legs']
                                    and band['min'] <= price <= band['max']):
            raise ValueError('CLUB_COVERAGE_PROFILE_GATE')
        return _identity(group)

    unique = {}
    for group in [*options, *proposed]:
        unique.setdefault(checked(group), group)
    ordered = sorted(unique.values(), key=rank)
    before, available = _fixtures(options), _fixtures(ordered)
    selected = [options[0]] if options else []
    used = {_identity(group) for group in selected}
    covered = _fixtures(selected)
    # Each iteration introduces a new fixture. This loop is bounded by the
    # same maximum complete-option count, not by a new combination budget.
    while len(selected) < maximum and available - covered:
        useful = [group for group in ordered if _identity(group) not in used
                  and _fixtures([group]) - covered]
        if not useful:
            break
        group = min(useful, key=lambda value: (-len(_fixtures([value]) - covered), rank(value)))
        selected.append(group)
        used.add(_identity(group))
        covered.update(_fixtures([group]))
    for group in ordered:
        if len(selected) == maximum:
            break
        if _identity(group) not in used:
            selected.append(group)
            used.add(_identity(group))
    selected.sort(key=rank)
    after = _fixtures(selected)
    final_search = {**search,
                    'combinations_examined': search['combinations_examined'] + extra['combinations_examined'],
                    'retained_option_fixture_count': len(after),
                    'eligible_fixtures_without_option_count': search['eligible_fixture_count'] - len(after)}
    diagnostics = {
        'version': VERSION, 'mode': 'ACTIVE', 'status': 'COMPARED_AND_BOUNDED',
        'baseline_options': len(options), 'challenger_options': len(proposed), 'final_options': len(selected),
        'beam_width': search['beam_width'], 'max_options': maximum,
        'baseline_retained_fixture_ids': sorted(before), 'final_retained_fixture_ids': sorted(after),
        'newly_represented_fixture_ids': sorted(after - before),
        'no_longer_represented_fixture_ids': sorted(before - after),
        'omitted_option_count': len(ordered) - len(selected),
        'baseline_best_retained': not options or _identity(options[0]) in used,
        'same_eligible_source_rows': True, 'new_probability_or_evidence_threshold': False,
        'new_model_fits': 0, 'new_api_or_llm_calls': 0,
        'additional_combinations_examined': extra['combinations_examined'],
        'exhaustive_search_claimed': False, 'new_live_profile_authority': False,
    }
    return selected, final_search, diagnostics
