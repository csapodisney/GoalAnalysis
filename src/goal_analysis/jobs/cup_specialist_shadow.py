"""Opt-in, read-only cup selector. It cannot publish, write or request data.

Reuses the existing persona model/ranking/combination engine on a separate copy.
Taxonomy and native prices require actual locally witnessed raw sources. A cup
is not assumed to imply a B team, rotation, motivation or a particular score.
"""
import hashlib
import math
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.model_recommendations import SETTLEMENT
from goal_analysis.jobs.competition_types import inspect_competition_types
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_odds import _price, valid_quote_source
from goal_analysis.storage.source_observations import read_source_raw, source_key
from goal_analysis.tipsters.catalog import TIPSTER_BY_ID
from goal_analysis.tipsters.recommendations import (
    _fixtures,
    _leg,
    _models,
    _options,
    _pool,
    _ranked,
)

VERSION = 'cup-specialist-shadow-v1'
FAMILIES = ('DOMESTIC_CUP', 'INTERNATIONAL_CLUB')


def _persona():
    # Fixed experimental recipe, never edits the live catalogue or accepts
    # caller-supplied daily weights. No extra fit or language model is involved.
    persona = deepcopy(TIPSTER_BY_ID['tipster_bastya_bence'])
    persona.update(profile_id='shadow_cup_specialist', name='Kupaspecialista · SHADOW',
                   tagline='Forrásolt klubkupák külön kutatási kombinációja.',
                   personality='Rögzített kupaazonosság és két numerikus modell; rotációt nem feltételez.',
                   min_legs=3, max_legs=5, preferred_legs=3,
                   target_odds_band={'min': 10, 'max': 50}, preferred_combined_price=math.sqrt(500),
                   reference_odds_band={'min': 10, 'max': 50},
                   model_weights={'gauss': .5, 'kepler': 0., 'laplace': .5})
    persona['selection_policy'].update(
        allowed_market_keys=['home', 'draw', 'away', 'over25', 'under25', 'btts_yes', 'btts_no'],
        required_models=['gauss', 'laplace'], min_models=2, min_probability=.2,
        min_edge=.05, max_model_spread=.15, market_bonus={})
    return persona


def _prices(root, candidates, fixtures, cutoff):
    """Verify exact native outcome prices against bytes and a local receipt."""
    accepted, proofs, issues, cache = [], {}, Counter(), {}
    bytes_read = 0
    path = root / 'data/goal_analysis.sqlite3'
    if not path.is_file():
        return [], {}, {'MISSING_PRICE_DATABASE': len(candidates)}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for candidate in candidates:
            try:
                fixture = fixtures[candidate['fixture_id']]
                if (not valid_quote_source(candidate) or candidate.get('quoted_at') is not None
                        or candidate.get('settlement') != SETTLEMENT
                        or candidate.get('quote_available') is not True):
                    raise ValueError('UNVERIFIED_NATIVE_QUOTE')
                age = (cutoff - utc(candidate['quote_observed_at'])).total_seconds()
                if not 0 <= age <= 1800:
                    raise ValueError('STALE_OR_FUTURE_PRICE_RECEIPT')
                digest = candidate['quote_source_sha256']
                source = {'provider': 'the_stats_api', 'content_hash': digest,
                          'source_url': candidate['quote_source_id'],
                          'observed_at': candidate['quote_observed_at']}
                key = source_key(source)
                if key not in cache:
                    row = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                                     (digest,)).fetchone()
                    if row is None or row['size_bytes'] > 4 * 1024 * 1024:
                        raise ValueError('MISSING_OR_OVERSIZED_NATIVE_PRICE_SOURCE')
                    if len(cache) >= 1000 or bytes_read + row['size_bytes'] > 64 * 1024 * 1024:
                        raise OverflowError('NATIVE_PRICE_SOURCE_BUDGET')
                    body, proof = read_source_raw(db, dict(row), root, cutoff, source)
                    bytes_read += row['size_bytes']
                    cache[key] = body['data'], proof, row['size_bytes']
                data, proof, _size = cache[key]
                if (proof['source_url'] != candidate['quote_source_id']
                        or data['match_id'] != fixture['provider_fixture_id']
                        or utc(proof['times']['observed_at']) > utc(candidate['quote_observed_at'])):
                    raise ValueError('PRICE_SOURCE_IDENTITY_OR_CLOCK_CONFLICT')
                books = [b for b in data['bookmakers'] if b.get('bookmaker') == candidate['provider_bookmaker']]
                if len(books) != 1 or candidate['period'] != 'FULL_TIME':
                    raise ValueError('AMBIGUOUS_BOOK_OR_UNSUPPORTED_PERIOD')
                market = {'h2h': 'match_odds', 'totals_2_5': 'total_goals', 'btts': 'btts'}[candidate['market_key']]
                outcomes = books[0]['markets'][market]
                if market == 'total_goals':
                    outcomes = outcomes['2.5']
                price = _price(outcomes[candidate['selection_key']])
                offered = candidate['decimal_price']
                if (type(offered) not in (int, float) or not math.isfinite(offered)
                        or price is None or price != offered):
                    raise ValueError('SOURCE_PRICE_MISMATCH')
                accepted.append(deepcopy(candidate))
                proofs[candidate['candidate_id']] = proof
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                issues[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return accepted, proofs, dict(issues)


def inspect_cup_specialist(root, enriched, model_results, *, as_of, enabled=False):
    """Explicit feature flag; even a successful result is never READY/playable.

    Shared model provenance checks are inherited from the current persona engine;
    this experiment does not certify full LIVE point-in-time model calibration.
    """
    if type(enabled) is not bool:
        raise ValueError('BOOLEAN_SHADOW_FLAG_REQUIRED')
    cutoff = utc(as_of)
    persona = _persona()
    recipe = {'version': VERSION, 'families': list(FAMILIES), 'persona': persona,
              'price_receipt_max_age_seconds': 1800, 'lockout_seconds': 900,
              'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result = {'version': VERSION, 'profile_id': persona['profile_id'], 'mode': 'SHADOW',
              'as_of': cutoff.isoformat(), 'status': 'DISABLED', 'enabled': enabled,
              'recipe_sha256': canonical_sha256(recipe), 'recipe': recipe,
              'candidate_legs': [], 'combined_price': None, 'playable': False,
              'selected_for_portfolio': False, 'live_decision_effect': False,
              'probability_adjustment': 0, 'ranking_multiplier': 1., 'external_requests': 0,
              'production_writes': 0, 'additional_model_fits': 0,
              'rotation_or_motivation_inferred_from_cup': False,
              'full_model_availability_or_calibration_certified': False}
    if not enabled:
        return result
    if (not isinstance(enriched, dict) or not isinstance(enriched.get('fixtures'), list)
            or len(enriched['fixtures']) > 1000 or not isinstance(enriched.get('candidates'), list)
            or len(enriched['candidates']) > 10000
            or any(not isinstance(c, dict) for c in enriched['candidates'])
            or not isinstance(model_results, dict)):
        raise ValueError('BOUNDED_CUP_INPUT_REQUIRED')
    root = Path(root).resolve()
    try:
        taxonomy = inspect_competition_types(root, enriched['fixtures'], as_of=cutoff)
        allowed = {row['fixture_id']: row for row in taxonomy['rows']
                   if row['family'] in FAMILIES and row['participant_type'] == 'CLUB'
                   and row.get('source_receipt_ids')}
        target = enriched['date']
        scoped = _fixtures(enriched['fixtures'], target, cutoff)
        scoped = {fid: fixture for fid, fixture in scoped.items()
                  if fid in allowed and (utc(fixture['kickoff']) - cutoff).total_seconds() > 900}
        prices, receipts, issues = _prices(root, [c for c in enriched['candidates']
                                                if c.get('fixture_id') in scoped], scoped, cutoff)
        models = _models(model_results, scoped, cutoff)
        detached = {**enriched, 'candidates': prices}
        pool, valid_quotes = _pool(detached, scoped, models, cutoff)
        ranked = _ranked(pool, persona, cutoff)
        # Existing source-specific single exceptions cannot loosen this new
        # experimental accumulator's fixed shape/band.
        options, search = _options(ranked, persona)
        options = [rows for rows in options if 3 <= len(rows) <= 5
                   and 10 <= math.prod(row['quote']['decimal_price'] for row in rows) <= 50]
        chosen = options[0] if options else []
        legs = [_leg(row, persona, cutoff) for row in chosen]
        for leg in legs:
            leg['cup_context'] = deepcopy(allowed[leg['fixture_id']])
            leg['price_receipt'] = deepcopy(receipts[leg['source_candidate_id']])
            leg['cup_stage_rotation_implication'] = 'UNKNOWN'
        result.update(status='SHADOW_CANDIDATE' if legs else 'ABSTAINED', candidate_legs=legs,
                      combined_price=math.prod(l['decimal_price'] for l in legs) if legs else None,
                      reason='CONSTRUCTED_SHADOW_ONLY' if legs else 'NO_SOURCE_BACKED_COMPLIANT_CUP_COMBINATION',
                      diagnostics={'taxonomy_counts': taxonomy['counts'],
                                   'taxonomy_dataset_id': taxonomy['dataset']['manifest']['dataset_id'],
                                   'eligible_unlocked_cup_fixtures': len(scoped),
                                   'verified_raw_price_candidates': len(prices),
                                   'valid_quote_candidates': valid_quotes,
                                   'modeled_cup_fixtures': len(models), 'ranked_selections': len(ranked),
                                   'compliant_combinations': len(options), 'source_issues': issues,
                                   'search': search})
    except (ValueError, TypeError, KeyError, OSError, OverflowError, sqlite3.Error) as error:
        # Optional explicit observer only. A broken new expert never blocks or
        # mutates a saved package, and missing data cannot become a positive vote.
        result.update(status='UNAVAILABLE', reason=type(error).__name__)
    return result
