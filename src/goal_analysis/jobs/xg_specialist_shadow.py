"""Opt-in xG-only challenger selector with a separate shared-model control.

No fixed xG/base probability blend: raw xG probabilities rank experimental
combinations; GAUSS/LAPLACE independently screen evidence. Never LIVE/READY.
"""
import hashlib
import math
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.cup_specialist_shadow import _persona, _prices
from goal_analysis.jobs.xg_shadow import inspect_xg_rates
from goal_analysis.models.xg_ensemble import predict_xg
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import field_id
from goal_analysis.storage.source_observations import read_source_raw, source_key
from goal_analysis.tipsters.recommendations import _fixtures, _models, _options, _pool, _ranked

VERSION = 'xg-specialist-shadow-v1'
SOURCE_FILES = ('jobs/xg_specialist_shadow.py', 'jobs/cup_specialist_shadow.py', 'jobs/xg_shadow.py',
                'models/xg_rates.py', 'models/xg_ensemble.py', 'tipsters/recommendations.py',
                'engine/ticket_options.py', 'engine/ticket_scoring.py', 'engine/model_recommendations.py',
                'team/metrics.py', 'providers/the_stats_odds.py', 'storage/raw_evidence.py',
                'storage/source_observations.py', 'storage/http_observations.py',
                'storage/temporal.py', 'normalization/leakage.py', 'normalization/identity.py',
                'normalization/live_identity.py', 'normalization/temporal.py',
                'normalization/training_datasets.py')


def _policy():
    persona = _persona()
    persona.update(profile_id='shadow_xg_specialist', name='xG-specialista · SHADOW',
                   tagline='Kétoldali helyszíni xG-mintából külön kutatási kombináció.',
                   personality='Önálló xG-Poisson-becslés, külön GAUSS/LAPLACE-kontrollal.')
    return persona


def _targets(root, fixtures, cutoff):
    """Exact, witnessed target metadata; no assumed neutral venue or status."""
    targets, proofs, issues, cache = {}, {}, Counter(), {}
    path = root / 'data/goal_analysis.sqlite3'
    if not path.is_file():
        return {}, {}, {'MISSING_TARGET_DATABASE': len(fixtures)}
    bytes_read = 0
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for fid, fixture in fixtures.items():
            try:
                source = fixture['fixture_source']
                digest, key = source['content_hash'], source_key(source)
                if key not in cache:
                    row = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                                     (digest,)).fetchone()
                    if row is None or row['size_bytes'] > 4 * 1024 * 1024:
                        raise ValueError('MISSING_OR_OVERSIZED_TARGET_SOURCE')
                    if len(cache) >= 1000 or bytes_read + row['size_bytes'] > 64 * 1024 * 1024:
                        raise OverflowError('TARGET_SOURCE_BUDGET')
                    body, proof = read_source_raw(db, dict(row), root, cutoff, source)
                    data = body.get('data')
                    if isinstance(data, dict):
                        data = [data]  # Exact native match-detail endpoint, same source/identity checks.
                    if not isinstance(data, list) or len(data) > 10000:
                        raise ValueError('BOUNDED_TARGET_LIST_REQUIRED')
                    by_id = {}
                    for value in data:
                        if not isinstance(value, dict):
                            continue
                        by_id.setdefault(value.get('id'), []).append(value)
                    cache[key] = by_id, proof
                    bytes_read += row['size_bytes']
                by_id, proof = cache[key]
                age = (cutoff - utc(proof['times']['observed_at'])).total_seconds()
                if not 0 <= age <= 1800:
                    raise ValueError('STALE_TARGET_METADATA')
                if (proof['source_url'] != fixture['fixture_source']['source_url']
                        or len(by_id.get(fixture['provider_fixture_id'], [])) != 1):
                    raise ValueError('AMBIGUOUS_OR_WRONG_TARGET_SOURCE')
                match = by_id[fixture['provider_fixture_id']][0]
                if (field_id(match, 'competition') != fixture['provider_league_id']
                        or field_id(match, 'season') != fixture['provider_season_id']
                        or match['home_team']['id'] != fixture['provider_home_team_id']
                        or match['away_team']['id'] != fixture['provider_away_team_id']
                        or utc(match['utc_date']) != utc(fixture['kickoff'])
                        or match.get('status') != 'scheduled' or match.get('is_neutral') is not False):
                    raise ValueError('TARGET_IDENTITY_STATUS_OR_VENUE_UNPROVEN')
                targets[fid] = {'fixture_id': match['id'], 'competition_id': fixture['provider_league_id'],
                                'season_id': fixture['provider_season_id'],
                                'home_team_id': fixture['provider_home_team_id'],
                                'away_team_id': fixture['provider_away_team_id'],
                                'neutral_venue': False, 'kickoff': utc(match['utc_date']).isoformat()}
                proofs[fid] = proof
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                issues[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return targets, proofs, dict(issues)


def inspect_xg_specialist(root, enriched, model_results, *, as_of, enabled=False):
    """Read actual stored inputs; caller cannot supply a fitted xG artifact.

    The result remains an uncalibrated challenger. It does not replace the
    separately validated, density-dependent ensemble or published probabilities.
    """
    if type(enabled) is not bool:
        raise ValueError('BOOLEAN_SHADOW_FLAG_REQUIRED')
    cutoff, persona = utc(as_of), _policy()
    recipe = {'version': VERSION, 'persona': persona, 'xg_min_probability': .2,
              'xg_min_estimated_edge': .05, 'max_xg_control_disagreement': .2,
              'probability_combination': 'NONE_SEPARATE_XG_CHALLENGER_AND_GAUSS_LAPLACE_CONTROL',
              'target_and_price_receipt_max_age_seconds': 1800, 'lockout_seconds': 900,
              'implementation_files_sha256': {
                  name: hashlib.sha256((Path(__file__).resolve().parent.parent / name).read_bytes()).hexdigest()
                  for name in SOURCE_FILES}}
    result = {'version': VERSION, 'mode': 'SHADOW', 'status': 'DISABLED', 'enabled': enabled,
              'profile_id': persona['profile_id'], 'as_of': cutoff.isoformat(),
              'recipe': recipe, 'recipe_sha256': canonical_sha256(recipe),
              'candidate_legs': [], 'combined_price': None, 'probability': None,
              'playable': False, 'selected_for_portfolio': False, 'live_decision_effect': False,
              'ranking_multiplier': 1., 'probability_adjustment': 0,
              'new_external_requests': 0, 'production_writes': 0,
              'actual_new_shared_model_fits': 0, 'xg_rate_refit_performed': False,
              'xg_fit': None,
              'calibrated': False, 'full_shared_model_pit_certified': False}
    if not enabled:
        return result
    if (not isinstance(enriched, dict) or not isinstance(enriched.get('fixtures'), list)
            or len(enriched['fixtures']) > 1000 or not isinstance(enriched.get('candidates'), list)
            or len(enriched['candidates']) > 10000
            or any(not isinstance(c, dict) for c in enriched['candidates'])
            or not isinstance(model_results, dict)):
        raise ValueError('BOUNDED_XG_SPECIALIST_INPUT_REQUIRED')
    try:
        root = Path(root).resolve()
        fit_view = inspect_xg_rates(root, as_of=cutoff)
        if fit_view['status'] == 'LIMIT_REACHED':
            raise ValueError('INCOMPLETE_XG_SOURCE_BUDGET')
        fit = fit_view['fit']
        result['xg_rate_refit_performed'] = True
        # Preserve the exact fitted prefix and physical lineage once, rather
        # than retaining only a dataset ID which cannot be inspected per leg.
        result['xg_fit'] = fit
        fixtures = _fixtures(enriched['fixtures'], enriched['date'], cutoff)
        fixtures = {k: f for k, f in fixtures.items()
                    if (utc(f['kickoff']) - cutoff).total_seconds() > 900}
        targets, target_proofs, target_issues = _targets(root, fixtures, cutoff)
        predictions, prediction_status = {}, Counter()
        for fid, target in targets.items():
            prediction = predict_xg(fit, target, as_of=cutoff)
            prediction_status[prediction['status']] += 1
            if prediction['status'] == 'RAW_XG_PROBABILITIES_SHADOW':
                predictions[fid] = prediction
        eligible = {fid: fixtures[fid] for fid in predictions}
        prices, receipts, price_issues = _prices(root, [c for c in enriched['candidates']
                                                      if c.get('fixture_id') in eligible], eligible, cutoff)
        models = _models(model_results, eligible, cutoff)
        pool, valid_quotes = _pool({**enriched, 'candidates': prices}, eligible, models, cutoff)
        controlled = _ranked(pool, persona, cutoff)
        ranked = []
        for row in controlled:
            probability = predictions[row['quote']['fixture_id']]['probabilities'][row['key']]
            edge = probability * row['quote']['decimal_price'] - 1
            if (probability < .2 or edge < .05
                    or abs(probability - row['probability']) > .2):
                continue
            # xG-only positive edge is normalized separately. Shared model
            # probabilities and heuristic scores are never added to it.
            ranked.append({**row, 'score': edge / (1 + edge), 'xg_probability': probability,
                           'xg_estimated_edge': edge})
        options, search = _options(ranked, persona)
        options = [rows for rows in options if 3 <= len(rows) <= 5
                   and 10 <= math.prod(row['quote']['decimal_price'] for row in rows) <= 50]
        chosen = options[0] if options else []
        legs = []
        for row in chosen:
            quote = deepcopy(row['quote'])
            fid = quote['fixture_id']
            quote.update(source_candidate_id=quote['candidate_id'],
                         xg_probability=row['xg_probability'], xg_estimated_edge=row['xg_estimated_edge'],
                         selection_score=row['score'], score_kind='NORMALIZED_POSITIVE_XG_EDGE_NOT_PROBABILITY',
                         shared_control_probability=row['probability'],
                         shared_control_components=deepcopy(row['components']),
                         xg_prediction=deepcopy(predictions[fid]),
                         target_receipt=deepcopy(target_proofs[fid]),
                         price_receipt=deepcopy(receipts[quote['candidate_id']]))
            legs.append(quote)
        result.update(status='SHADOW_CANDIDATE' if legs else 'ABSTAINED', candidate_legs=legs,
                      combined_price=math.prod(l['decimal_price'] for l in legs) if legs else None,
                      reason='CONSTRUCTED_SHADOW_ONLY' if legs else 'NO_WITNESSED_XG_CONTROLLED_COMBINATION',
                      diagnostics={'actual_xg_matches': fit['accepted_matches'],
                                   'xg_dataset_id': fit['dataset']['manifest']['dataset_id'],
                                   'xg_fit_sha256': fit['result_sha256'], 'xg_reader_issues': fit_view.get('issues', {}),
                                   'witnessed_fresh_target_fixtures': len(targets),
                                   'target_issues': target_issues, 'xg_prediction_statuses': dict(prediction_status),
                                   'two_sided_xg_prediction_fixtures': len(predictions),
                                   'verified_raw_price_candidates': len(prices), 'price_issues': price_issues,
                                   'valid_quote_candidates': valid_quotes, 'control_modeled_fixtures': len(models),
                                   'control_eligible_selections': len(controlled), 'xg_ranked_selections': len(ranked),
                                   'compliant_combinations': len(options), 'search': search})
    except (ValueError, TypeError, KeyError, OSError, OverflowError, sqlite3.Error) as error:
        result.update(status='UNAVAILABLE', reason=type(error).__name__)
    return result
