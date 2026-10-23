"""One actual native fit -> witnessed-price options -> player review, SHADOW.

There is no public supplied-model argument: this entry point fits through the
strict native reader once. Its separate cards can never replace LIVE cards.
"""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.model_recommendations import build_model_recommendations
from goal_analysis.engine.publication_odds import apply_publication_odds
from goal_analysis.jobs.cup_specialist_shadow import _prices, inspect_cup_specialist
from goal_analysis.jobs.motivation_specialist_shadow import inspect_motivation_specialist
from goal_analysis.jobs.native_history_shadow import replay_native_history
from goal_analysis.jobs.native_venue_shadow import review_venue_options
from goal_analysis.jobs.player_option_shadow import review_player_options
from goal_analysis.jobs.prospective_movement_shadow import review_prospective_movement
from goal_analysis.jobs.rotation_option_shadow import review_rotation_options
from goal_analysis.jobs.weather_option_shadow import review_weather_options
from goal_analysis.jobs.xg_specialist_shadow import _targets, inspect_xg_specialist
from goal_analysis.models.xg_ensemble import _verify
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import validate

VERSION = 'native-model-options-player-cup-xg-human-weather-rotation-venue-movement-shadow-v8'


def _cup_review(root, enriched, models, dataset_id, target_proofs, price_proofs, cutoff):
    """Separate hypothesis from the SAME fit and strictly verified inputs.

    Source failure is local to this optional expert. No replacement of numeric
    cards, probability boost, additional fit or request is possible here.
    """
    unavailable = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_CUP_REVIEW_FAILED', 'mode': 'SHADOW',
                   'candidate_legs': [], 'source_bindings': [], 'playable': False,
                   'live_decision_effect': False, 'external_requests': 0, 'additional_model_fits': 0}
    if dataset_id is None:
        return {**unavailable, 'reason': 'MISSING_NATIVE_INPUT_DATASET'}
    try:
        result = inspect_cup_specialist(root, deepcopy(enriched), deepcopy(models),
                                        as_of=cutoff, enabled=True)
        bindings = []
        for leg in result['candidate_legs']:
            fid = leg['fixture_id']
            predictions = {}
            for profile in ('gauss', 'laplace'):
                matches = [p for p in models[profile]['predictions'] if p['fixture_id'] == fid]
                if len(matches) != 1:
                    raise ValueError('AMBIGUOUS_CUP_NATIVE_PREDICTION')
                predictions[profile] = canonical_sha256(matches[0])
            bindings.append({'leg_sha256': canonical_sha256(leg), 'fixture_id': fid,
                'source_candidate_id': leg['source_candidate_id'],
                'native_prediction_sha256': predictions, 'native_input_dataset_id': dataset_id,
                'price_receipt': deepcopy(price_proofs[leg['source_candidate_id']]),
                'target_receipt': deepcopy(target_proofs[fid]),
                'taxonomy_receipt_ids': deepcopy(leg['cup_context']['source_receipt_ids'])})
        result.update(source_bindings=bindings, native_models_sha256=canonical_sha256(models),
            verified_inputs_sha256=canonical_sha256(enriched), native_input_dataset_id=dataset_id,
            integration_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
        scoped_ids = {f['fixture_id'] for f in enriched['fixtures']}
        jointly_modeled = scoped_ids.intersection(*[
            {p['fixture_id'] for p in models.get(profile, {}).get('predictions', [])}
            for profile in ('gauss', 'laplace')])
        result.setdefault('diagnostics', {}).update(
            fresh_source_bound_target_fixtures=len(scoped_ids),
            required_two_model_fixture_count=len(jointly_modeled))
        return result
    except Exception:  # noqa: BLE001 - isolated optional expert, cannot veto saved/native cards
        return unavailable


def _xg_review(root, enriched, models, dataset_id, target_proofs, price_proofs, cutoff):
    """Bind the existing xG challenger to the same native fit, never blend it.

    The expert performs its own descriptive xG rate fit once; no additional
    GAUSS/KEPLER/LAPLACE fit, request or archive admission occurs here.
    """
    unavailable = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_XG_REVIEW_FAILED', 'mode': 'SHADOW',
                   'candidate_legs': [], 'source_bindings': [], 'playable': False,
                   'live_decision_effect': False, 'new_external_requests': 0,
                   'actual_new_shared_model_fits': 0, 'xg_rate_refit_performed': False}
    if dataset_id is None:
        return {**unavailable, 'reason': 'MISSING_NATIVE_INPUT_DATASET'}
    refitted = False
    try:
        result = inspect_xg_specialist(root, deepcopy(enriched), deepcopy(models),
                                       as_of=cutoff, enabled=True)
        refitted = result.get('xg_rate_refit_performed') is True
        fit = result.get('xg_fit')
        if fit is not None:
            _verify(fit)
            validate(fit['dataset'])
        bindings = []
        for leg in result['candidate_legs']:
            fid, prediction = leg['fixture_id'], leg['xg_prediction']
            _verify(prediction)
            if (fit is None or prediction['input_dataset_id'] != fit['dataset']['manifest']['dataset_id']
                    or prediction['source_fit_sha256'] != fit['result_sha256']
                    or leg['price_receipt'] != price_proofs[leg['source_candidate_id']]
                    or leg['target_receipt'] != target_proofs[fid]):
                raise ValueError('XG_SOURCE_BINDING_CONFLICT')
            predictions = {}
            for profile in ('gauss', 'laplace'):
                matches = [p for p in models[profile]['predictions'] if p['fixture_id'] == fid]
                if len(matches) != 1:
                    raise ValueError('AMBIGUOUS_XG_NATIVE_PREDICTION')
                predictions[profile] = canonical_sha256(matches[0])
            indices = [i for i, row in enumerate(fit['dataset']['rows'])
                if (row['competition_id'], row['season_id']) ==
                   (prediction['competition_id'], prediction['season_id'])]
            if not indices:
                raise ValueError('MISSING_XG_CONSUMED_ROWS')
            bindings.append({'leg_sha256': canonical_sha256(leg), 'fixture_id': fid,
                'source_candidate_id': leg['source_candidate_id'],
                'native_prediction_sha256': predictions, 'native_input_dataset_id': dataset_id,
                'price_receipt': deepcopy(price_proofs[leg['source_candidate_id']]),
                'target_receipt': deepcopy(target_proofs[fid]),
                'xg_prediction_sha256': canonical_sha256(prediction),
                'xg_fit_sha256': fit['result_sha256'],
                'xg_dataset_id': fit['dataset']['manifest']['dataset_id'],
                'xg_consumed_row_indices': indices,
                'xg_dataset_ref': ['xg_fit', 'dataset'],
                'xg_lineage_sha256': fit['dataset']['manifest']['lineage_sha256']})
        result.update(source_bindings=bindings, native_models_sha256=canonical_sha256(models),
            verified_inputs_sha256=canonical_sha256(enriched), native_input_dataset_id=dataset_id,
            integration_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
        return result
    except Exception:  # noqa: BLE001 - optional expert cannot veto or alter the stable branches
        return {**unavailable, 'xg_rate_refit_performed': refitted}


def _motivation_review(root, enriched, models, dataset_id, target_proofs, price_proofs, cutoff):
    """Source-bound review context, never a motivational probability or veto.

    Context may describe stale targets, but only already verified current
    prices/targets can bind a review leg. No fresh source is synthesized.
    """
    unavailable = {'status': 'UNAVAILABLE', 'reason': 'NATIVE_MOTIVATION_REVIEW_FAILED',
        'mode': 'SHADOW', 'candidate_legs': [], 'source_bindings': [], 'playable': False,
        'live_decision_effect': False, 'new_external_requests': 0, 'actual_new_shared_model_fits': 0,
        'mental_motivation': None, 'probability_adjustment': 0, 'ranking_multiplier': 1.}
    if dataset_id is None:
        return {**unavailable, 'reason': 'MISSING_NATIVE_INPUT_DATASET'}
    try:
        result = inspect_motivation_specialist(root, deepcopy(enriched), deepcopy(models),
                                               as_of=cutoff, enabled=True)
        datasets = result.get('source_datasets')
        if datasets is not None:
            for dataset in datasets.values():
                validate(dataset)
        bindings = []
        for leg in result['candidate_legs']:
            fid = leg['fixture_id']
            if (datasets is None or leg['target_receipt'] != target_proofs[fid]
                    or leg['price_receipt'] != price_proofs[leg['source_candidate_id']]):
                raise ValueError('HUMAN_REVIEW_SOURCE_BINDING_CONFLICT')
            predictions = {}
            for profile in ('gauss', 'laplace'):
                matches = [p for p in models[profile]['predictions'] if p['fixture_id'] == fid]
                if len(matches) != 1:
                    raise ValueError('AMBIGUOUS_HUMAN_NATIVE_PREDICTION')
                predictions[profile] = canonical_sha256(matches[0])
            news_indices = [i for i, row in enumerate(datasets['news']['rows']) if row['fixture_id'] == fid]
            stakes_indices = [i for i, row in enumerate(datasets['stakes']['rows'])
                              if 'the_stats_api:'+row['fixture_id'] == fid]
            if len(news_indices) != 1 or len(stakes_indices) > 1:
                raise ValueError('AMBIGUOUS_HUMAN_CONTEXT_ROWS')
            claims = leg['motivation_review_context']['fresh_human_claims']
            bindings.append({'leg_sha256': canonical_sha256(leg), 'fixture_id': fid,
                'source_candidate_id': leg['source_candidate_id'],
                'native_prediction_sha256': predictions, 'native_input_dataset_id': dataset_id,
                'target_receipt': deepcopy(target_proofs[fid]),
                'price_receipt': deepcopy(price_proofs[leg['source_candidate_id']]),
                'context_sha256': canonical_sha256(leg['motivation_review_context']),
                'news_assertion_hashes': sorted(c['assertion_hash'] for c in claims),
                'news_receipt_ids': sorted({p['receipt_id'] for c in claims for p in c['provenances']}),
                'news_row_indices': news_indices, 'stakes_row_indices': stakes_indices,
                'source_datasets_ref': ['source_datasets'],
                'source_dataset_ids': {k: d['manifest']['dataset_id'] for k, d in datasets.items()},
                'source_lineage_sha256': {k: d['manifest']['lineage_sha256'] for k, d in datasets.items()},
                'claim_truth_status': 'UNVERIFIED', 'independent_review_required': True})
        result.update(source_bindings=bindings, native_models_sha256=canonical_sha256(models),
            context_inputs_sha256=canonical_sha256(enriched), native_input_dataset_id=dataset_id,
            integration_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            context_scope='SOURCE_BOUND_IDENTITIES_NOT_FRESHNESS_OR_WAGER_AUTHORIZATION')
        return result
    except Exception:  # noqa: BLE001 - optional review cannot alter other experts or saved cards
        return unavailable


def replay_native_options(root, report, enriched, settings, *, as_of,
                          profiles=('gauss', 'kepler', 'laplace'), artifact_dir=None):
    """Never reuse stored predictions, refresh a receipt, or fake missing odds.

    The old builder's low-odds research candidates remain behind the existing
    publication band. Only complete in-band options go to player option review.
    The specialist consumes the SAME freshly computed native models and raw
    candidates; its existing two-model, freshness and absence gates stay intact.
    """
    cutoff = utc(as_of)
    root = Path(root).resolve()
    if (not isinstance(enriched, dict) or not isinstance(enriched.get('fixtures'), list)
            or len(enriched['fixtures']) > 1000 or not isinstance(enriched.get('candidates'), list)
            or len(enriched['candidates']) > 10000 or not isinstance(settings, dict)
            or any(not isinstance(c, dict) for c in enriched['candidates'])):
        raise ValueError('BOUNDED_NATIVE_OPTIONS_REQUIRED')
    inputs_hash = canonical_sha256([report, enriched, settings])
    native = replay_native_history(root, enriched['fixtures'], as_of=cutoff,
                                   profiles=profiles, artifact_dir=artifact_dir)
    view, models = native['source_view'], native['numeric_models']
    fixtures = {f['fixture_id']: f for f in view['fixtures']
                if utc(f['kickoff']) > cutoff + timedelta(minutes=15)}
    targets, target_proofs, target_gaps = _targets(root, fixtures, cutoff)
    scoped = {fid: f for fid, f in fixtures.items() if fid in targets}
    candidates = [c for c in enriched['candidates'] if c.get('fixture_id') in scoped]
    # An ambiguous source alias cannot become many independent positive tips.
    identifiers = Counter(c.get('candidate_id') for c in candidates
                          if isinstance(c.get('candidate_id'), str))
    unique = [c for c in candidates if isinstance(c.get('candidate_id'), str)
              and identifiers[c['candidate_id']] == 1]
    prices, price_proofs, price_gaps = _prices(root, unique, scoped, cutoff)
    clock_gaps = 0
    verified = []
    for quote in prices:
        # A caller cannot make yesterday's raw body fresh by editing q.observed.
        # A later repeated receipt needs its own independently proven evidence.
        proof = price_proofs[quote['candidate_id']]
        if utc(quote['quote_observed_at']) != utc(proof['times']['observed_at']):
            clock_gaps += 1
        else:
            verified.append(quote)
    model_settings = {**deepcopy(settings), 'date': enriched['date'],
                      'enabled_profiles': [p for p in profiles if p in models]}
    options = {}
    cards = build_model_recommendations(models, list(scoped.values()), verified,
        model_settings, cutoff, diversity_options=options)
    before_band = sum(len(rows) for rows in options.values())
    research, options = apply_publication_odds({'recommendations': cards}, options, model_settings)
    # No ID, stake or status in these hypothetical cards can authorize a wager.
    for card in research['recommendations']:
        card.update(playable=False, selected_for_portfolio=False, portfolio_ticket_id=None, stake_eur=None)
    for rows in options.values():
        for option in rows:
            option['recommendation'].update(playable=False, selected_for_portfolio=False,
                                             portfolio_ticket_id=None, stake_eur=None)
    detached = {**deepcopy(enriched), 'fixtures': list(scoped.values()), 'candidates': verified}
    player = review_player_options(root, research, options, detached, models, as_of=cutoff)
    cup = _cup_review(root, detached, models, view.get('dataset', {}).get('manifest', {}).get('dataset_id'),
                      target_proofs, price_proofs, cutoff)
    xg = _xg_review(root, detached, models, view.get('dataset', {}).get('manifest', {}).get('dataset_id'),
                    target_proofs, price_proofs, cutoff)
    # Keep coverage gaps visible even when current prices/metadata are stale.
    # The expert independently checks target freshness; its candidate inputs
    # are still ONLY the parent's verified prices, with bound current receipts.
    context_inputs = {**detached, 'fixtures': deepcopy(view['fixtures'])}
    motivation = _motivation_review(root, context_inputs, models,
        view.get('dataset', {}).get('manifest', {}).get('dataset_id'), target_proofs, price_proofs, cutoff)
    weather = review_weather_options(root, view['fixtures'], options,
        {'player': player.get('specialist', {}), 'cup': cup, 'xg': xg, 'motivation': motivation}, as_of=cutoff)
    rotation = review_rotation_options(root, view['fixtures'], options,
        {'player': player.get('specialist', {}), 'cup': cup, 'xg': xg, 'motivation': motivation}, as_of=cutoff)
    venue = review_venue_options(root, view['fixtures'], options,
        {'player': player.get('specialist', {}), 'cup': cup, 'xg': xg, 'motivation': motivation}, as_of=cutoff)
    try:
        movement = review_prospective_movement(root, list(scoped.values()), verified, models, options, as_of=cutoff)
    except Exception:  # noqa: BLE001 - optional observer cannot discard existing native cards
        movement = {'status':'UNAVAILABLE', 'mode':'SHADOW', 'decision_effect':'NONE',
            'actual_elapsed_24h_pairs':0, 'reason':'PROSPECTIVE_REVIEW_FAILED'}
    bindings = []
    for profile, rows in options.items():
        by_fixture = {p['fixture_id']: p for p in models[profile].get('predictions', [])}
        for index, option in enumerate(rows):
            card = option['recommendation']
            bindings.append({'profile_id': profile, 'option_index': index,
                'option_sha256': canonical_sha256(option), 'legs': [
                    {'leg_sha256': canonical_sha256(leg), 'fixture_id': leg['fixture_id'],
                     'source_candidate_id': leg['source_candidate_id'],
                     'native_prediction_sha256': canonical_sha256(by_fixture[leg['fixture_id']]),
                     'native_input_dataset_id': view['dataset']['manifest']['dataset_id'],
                     'price_receipt': deepcopy(price_proofs[leg['source_candidate_id']]),
                     'target_receipt': deepcopy(target_proofs[leg['fixture_id']])}
                    for leg in card['legs']]})
    if inputs_hash != canonical_sha256([report, enriched, settings]):
        raise ValueError('NATIVE_SHADOW_INPUT_MUTATED')
    native['options_shadow'] = {'version': VERSION, 'mode': 'SHADOW', 'as_of': cutoff.isoformat(),
        'status': 'COMPLETE', 'live_promotion': False, 'decision_effect': 'NONE',
        'real_wager_placed': False, 'external_requests': 0, 'production_writes': 0,
        'additional_model_fit_runs': 0, 'input_sha256': inputs_hash,
        'additional_model_fit_runs_scope': 'SHARED_GAUSS_KEPLER_LAPLACE_ONLY',
        'descriptive_xg_rate_fit_runs': int(xg['xg_rate_refit_performed']),
        'models_sha256': canonical_sha256(models), 'options': options, 'cards': research['recommendations'],
        'option_source_bindings': bindings, 'player_review': player, 'cup_review': cup, 'xg_review': xg,
        'motivation_review': motivation,
        'weather_review': weather,
        'rotation_review': rotation,
        'venue_review': venue,
        'odds_movement_review': movement,
        'publication_odds': research.get('publication_odds'),
        'summary': {'native_predictions': sum(len(m.get('predictions', [])) for m in models.values()),
            'fresh_source_bound_target_fixtures': len(scoped), 'verified_price_candidates': len(verified),
            'options_before_odds_band': before_band, 'complete_in_band_options': len(bindings),
            'player_options_reviewed': player['summary']['options_reviewed'],
            'player_supported_selections': player['summary']['supported_selections'],
            'cup_status': cup['status'], 'cup_candidate_legs': len(cup['candidate_legs']),
            'xg_status': xg['status'], 'xg_candidate_legs': len(xg['candidate_legs']),
            'xg_accepted_matches': (xg.get('xg_fit') or {}).get('accepted_matches', 0),
            'motivation_status': motivation['status'],
            'motivation_review_legs': len(motivation['candidate_legs']),
            'weather_status': weather['status'], 'weather_leg_bindings': weather['leg_bindings'],
            'venue_status': venue['status'], 'venue_leg_bindings': venue['leg_bindings'],
            'odds_movement_status': movement['status'],
            'odds_movement_elapsed_pairs': movement['actual_elapsed_24h_pairs'],
            'rotation_status': rotation['status'], 'rotation_leg_bindings': rotation['leg_bindings']},
        'gaps': {'target': target_gaps, 'price': price_gaps,
                 'ambiguous_or_missing_candidate_aliases': len(candidates) - len(unique),
                 'price_receipt_clock_not_raw_bound': clock_gaps},
        'historical_calibration_or_current_quote_time_certified': False}
    return native
