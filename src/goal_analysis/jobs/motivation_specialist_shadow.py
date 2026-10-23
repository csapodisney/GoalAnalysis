"""Explicit human-context review selector, not a mental-state probability model.

Witnessed news determines which fixtures need this specialist's review. Shared
numerical models rank them unchanged. Unverified claims and descriptive standings
cannot establish motivation, qualification, indifference or a causal uplift.
"""
import hashlib
import math
import sqlite3
from collections import Counter
from copy import deepcopy
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.cup_specialist_shadow import _persona, _prices
from goal_analysis.jobs.human_factors_shadow import inspect_human_factors
from goal_analysis.jobs.stakes_shadow import inspect_stakes
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.news_kinds import HUMAN_KINDS
from goal_analysis.tipsters.recommendations import _fixtures, _models, _options, _pool, _ranked

VERSION = 'motivation-specialist-shadow-v1'
SOURCE_FILES = ('jobs/motivation_specialist_shadow.py', 'jobs/cup_specialist_shadow.py',
                'jobs/xg_specialist_shadow.py', 'jobs/human_factors_shadow.py', 'jobs/stakes_shadow.py',
                'features/human_factors.py', 'features/stakes.py', 'jobs/competition_types.py',
                'jobs/official_rules_shadow.py',
                'normalization/competition_types.py', 'normalization/news_kinds.py',
                'storage/sports_news.py', 'storage/temporal.py', 'storage/raw_evidence.py',
                'normalization/leakage.py', 'normalization/temporal.py', 'tipsters/catalog.py',
                'tipsters/recommendations.py', 'engine/ticket_options.py', 'engine/ticket_scoring.py',
                'engine/model_recommendations.py', 'providers/the_stats_odds.py')


def _review_context(human, stakes, cutoff):
    """A recent import cannot make an old publication current or true."""
    fresh, stale = [], []
    for claim in human.get('human_factors', []):
        age = (cutoff - utc(claim['published_at'])).total_seconds()
        provenance = [p for p in claim['provenances'] if
                      0 <= (cutoff - utc(p['observed_at'])).total_seconds() <= 21600]
        if 0 <= age <= 172800 and provenance and claim['kind'] in HUMAN_KINDS:
            fresh.append(deepcopy(claim))
        else:
            stale.append(claim['assertion_hash'])
    return {'fixture_id': human['fixture_id'], 'fresh_human_claims': fresh,
            'stale_claim_hashes': sorted(stale),
            'source_posts': len({c['source_key'] for c in fresh}),
            'same_kind_multiple_assertions': any(count > 1 for count in Counter(c['kind'] for c in fresh).values()),
            'interpretation': 'MODEL_SUMMARIES_REQUIRE_INDEPENDENT_CONTENT_AND_APPLICABILITY_REVIEW',
            'independent_corroboration': 'NOT_ESTABLISHED',
            'independent_review_required': bool(fresh), 'claim_truth_status': 'UNVERIFIED',
            'mental_motivation': None, 'probability_adjustment': 0,
            'standings_context': {k: deepcopy(v) for k, v in stakes.items() if k != 'dataset'} if stakes else None,
            'official_rules_registry': (stakes or {}).get('official_rules_context', {}).get('status', 'UNKNOWN'),
            'mathematical_stakes_used_for_selection': False,
            'empty_news_interpretation': 'UNKNOWN_COVERAGE_NOT_NO_RISK'}


def inspect_motivation_specialist(root, enriched, model_results, *, as_of, enabled=False):
    """No APIs, writes, supplied opinions, rule flags or daily weight changes.

    With no independently verified rules/content, only a review challenger can
    be constructed. This does not close the separate motivation-effect gate5.7.
    """
    if type(enabled) is not bool:
        raise ValueError('BOOLEAN_SHADOW_FLAG_REQUIRED')
    cutoff, persona = utc(as_of), _persona()
    persona.update(profile_id='shadow_motivation_specialist', name='Motivációspecialista · SHADOW review',
                   tagline='Forrásolt emberi tényezők külön felülvizsgálata.',
                   personality='Mentális állapotot nem talál ki; a numerikus rangsor változatlan.')
    recipe = {'version': VERSION, 'persona': persona, 'publication_max_age_seconds': 172800,
              'model_import_max_age_seconds': 21600, 'news_kinds': sorted(HUMAN_KINDS),
              'target_and_price_receipt_max_age_seconds': 1800, 'lockout_seconds': 900,
              'motivational_probability_adjustment': 0, 'motivational_rank_multiplier': 1.,
              'selection_scope': 'FRESH_WITNESSED_HUMAN_CONTEXT_REQUIRING_REVIEW',
              'implementation_files_sha256': {name: hashlib.sha256(
                  (Path(__file__).resolve().parent.parent / name).read_bytes()).hexdigest() for name in SOURCE_FILES}}
    result = {'version': VERSION, 'mode': 'SHADOW', 'status': 'DISABLED', 'enabled': enabled,
              'profile_id': persona['profile_id'], 'as_of': cutoff.isoformat(), 'recipe': recipe,
              'recipe_sha256': canonical_sha256(recipe), 'candidate_legs': [], 'combined_price': None,
              'probability': None, 'playable': False, 'selected_for_portfolio': False,
              'live_decision_effect': False, 'ranking_multiplier': 1., 'probability_adjustment': 0,
              'mental_motivation': None, 'independent_review_required': False,
              'causal_or_calibrated_motivation_effect': False, 'new_external_requests': 0,
              'production_writes': 0, 'actual_new_shared_model_fits': 0,
              'source_datasets': None,
              'full_shared_model_pit_certified': False}
    if not enabled:
        return result
    if (not isinstance(enriched, dict) or not isinstance(enriched.get('fixtures'), list)
            or len(enriched['fixtures']) > 1000 or not isinstance(enriched.get('candidates'), list)
            or len(enriched['candidates']) > 10000 or any(not isinstance(c, dict) for c in enriched['candidates'])
            or not isinstance(model_results, dict)):
        raise ValueError('BOUNDED_MOTIVATION_SPECIALIST_INPUT_REQUIRED')
    try:
        root = Path(root).resolve()
        fixtures = {k: f for k, f in _fixtures(enriched['fixtures'], enriched['date'], cutoff).items()
                    if (utc(f['kickoff']) - cutoff).total_seconds() > 900}
        news = inspect_human_factors(root, list(fixtures.values()), as_of=cutoff)
        standings = inspect_stakes(root, list(fixtures.values()), as_of=cutoff)
        # Both trusted readers expose SHADOW datasets, never caller-supplied verified=True flags.
        if news['status'] == 'UNAVAILABLE' or standings['status'] == 'UNAVAILABLE':
            raise ValueError('OPTIONAL_MOTIVATION_READER_UNAVAILABLE')
        # Exact reader inputs remain inspectable once; legs reference rows and
        # lineage instead of pretending dataset IDs alone prove consumed news.
        result['source_datasets'] = {'news': news['dataset'], 'stakes': standings['dataset']}
        stakes_by_id = {'the_stats_api:' + s['fixture_id']: s for s in standings['features']}
        views = {h['fixture_id']: _review_context(h, stakes_by_id.get(h['fixture_id']), cutoff) for h in news['rows']}
        targets, target_proofs, target_issues = _targets(root, fixtures, cutoff)
        eligible = {fid: fixtures[fid] for fid in targets if views.get(fid, {}).get('fresh_human_claims')}
        prices, receipts, price_issues = _prices(root, [c for c in enriched['candidates']
                                                      if c.get('fixture_id') in eligible], eligible, cutoff)
        models = _models(model_results, eligible, cutoff)
        pool, valid_quotes = _pool({**enriched, 'candidates': prices}, eligible, models, cutoff)
        ranked = _ranked(pool, persona, cutoff)
        # No invented sign/size from news sentiment, a table position, or missing news.
        options, search = _options(ranked, persona)
        options = [rows for rows in options if 3 <= len(rows) <= 5
                   and 10 <= math.prod(r['quote']['decimal_price'] for r in rows) <= 50]
        legs = []
        for row in options[0] if options else []:
            quote = deepcopy(row['quote'])
            fid = quote['fixture_id']
            quote.update(source_candidate_id=quote['candidate_id'], selection_score=row['score'],
                         score_kind='UNCHANGED_NUMERIC_PERSONA_RANK_NOT_MOTIVATION_PROBABILITY',
                         shared_control_probability=row['probability'], shared_control_components=deepcopy(row['components']),
                         motivation_review_context=deepcopy(views[fid]), target_receipt=deepcopy(target_proofs[fid]),
                         price_receipt=deepcopy(receipts[quote['candidate_id']]))
            legs.append(quote)
        result.update(status='SHADOW_REVIEW_CANDIDATE' if legs else 'ABSTAINED', candidate_legs=legs,
                      combined_price=math.prod(l['decimal_price'] for l in legs) if legs else None,
                      independent_review_required=bool(legs), fixture_evidence=views,
                      reason='HUMAN_CONTEXT_REVIEW_ONLY' if legs else 'NO_FRESH_HUMAN_CONTEXT_CONTROLLED_COMBINATION',
                      diagnostics={'witnessed_news_imports': news['accepted_assertion_imports'],
                                   'news_statuses': news['counts'], 'news_issues': news['issues'],
                                   'human_fixtures': sum(bool(v['fresh_human_claims']) for v in views.values()),
                                   'stakes_team_statuses': standings['counts'], 'stakes_issues': standings['issues'],
                                   'standings_table_diagnostics': standings['table_diagnostics'],
                                   'news_dataset_id': news['dataset']['manifest']['dataset_id'],
                                   'stakes_dataset_id': standings['dataset']['manifest']['dataset_id'],
                                   'official_rules_registry': standings['official_rules_registry'],
                                   'witnessed_fresh_target_fixtures': len(targets), 'target_issues': target_issues,
                                   'verified_raw_price_candidates': len(prices), 'price_issues': price_issues,
                                   'valid_quote_candidates': valid_quotes, 'control_modeled_fixtures': len(models),
                                   'numeric_ranked_selections': len(ranked), 'compliant_combinations': len(options),
                                   'search': search})
    except (ValueError, TypeError, KeyError, OSError, OverflowError, sqlite3.Error) as error:
        result.update(status='UNAVAILABLE', reason=type(error).__name__)
    return result
