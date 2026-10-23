"""Bind witnessed calendar/XI diagnostics to research legs without rank effects."""
from datetime import timedelta

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.rotation import LINEUP_MAX_AGE_HOURS
from goal_analysis.features.schedule import MAX_AGE_HOURS
from goal_analysis.jobs.rotation_shadow import inspect_rotation
from goal_analysis.jobs.schedule_shadow import target_fixture
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import validate

VERSION = 'native-calendar-rotation-option-context-shadow-v1'


def _bind_reviews(options, specialists, contexts, dataset_id):
    count = sum(len(o['recommendation']['legs']) for rows in options.values() for o in rows)
    count += sum(len(s.get('candidate_legs', [])) for s in specialists.values())
    if count > 10000:
        raise ValueError('BOUNDED_ROTATION_LEG_BINDINGS_REQUIRED')
    context_hashes = {fid: canonical_sha256(row) for fid, (_, row) in contexts.items()}

    def bind(leg):
        index, row = contexts.get(leg.get('fixture_id'), (None, {}))
        return {'fixture_id': leg.get('fixture_id'), 'leg_sha256': canonical_sha256(leg),
            'context_row_index': index, 'context_sha256': context_hashes.get(leg.get('fixture_id')),
            'source_dataset_id': dataset_id, 'feature_dataset_id': row.get('feature_dataset_id'),
            'context_status': row.get('status', 'UNKNOWN'),
            'data_use': 'USED_REVIEW' if row.get('calendar_teams_verified', 0) else 'MISSING',
            'reason': row.get('reason', 'MISSING_NATIVE_CALENDAR_ROTATION_CONTEXT'),
            'probability_adjustment': 0, 'ranking_multiplier': 1., 'live_decision_effect': False}

    option_reviews = [{'profile_id': profile, 'option_index': i, 'option_sha256': canonical_sha256(option),
        'legs': [bind(leg) for leg in option['recommendation']['legs']]}
        for profile, rows in options.items() for i, option in enumerate(rows)]
    specialist_reviews = {key: [bind(leg) for leg in s.get('candidate_legs', [])]
                          for key, s in specialists.items()}
    return option_reviews, specialist_reviews, count


def review_rotation_options(root, fixtures, options, specialists, *, as_of):
    """Existing readers run once. Unknowns remain visible on the source scope.

    Accepted calendar context is not an XI comparison or a rotation cause.
    Source datasets and envelopes are retained once, referenced by each leg.
    """
    before = canonical_sha256([fixtures, options, specialists])
    try:
        if not isinstance(fixtures, list) or len(fixtures) > 1000:
            raise ValueError('BOUNDED_ROTATION_FIXTURES_REQUIRED')
        ids = [f['fixture_id'] for f in fixtures]
        if len(set(ids)) != len(ids):
            raise ValueError('UNIQUE_ROTATION_FIXTURES_REQUIRED')
        cutoff = utc(as_of)
        view = inspect_rotation(root, fixtures, as_of=cutoff)
        validate(view['dataset'])
        for name in ('schedule_dataset', 'competition_dataset'):
            validate(view['dataset']['lineage'][name])
        schedule = view['dataset']['lineage']['schedule_dataset']
        schedules = {r['fixture_id']: r for r in schedule['rows']}
        if len(schedules) != len(schedule['rows']):
            raise ValueError('AMBIGUOUS_ROTATION_SCHEDULE_ROWS')
        features = {}
        if len(view['features']) != len(view['dataset']['rows']):
            raise ValueError('ROTATION_ROW_COUNT_CONFLICT')
        for index, feature in enumerate(view['features']):
            validate(feature['dataset'])
            if ({k: v for k, v in feature.items() if k != 'dataset'} != view['dataset']['rows'][index]
                    or feature['fixture_id'] in features):
                raise ValueError('ROTATION_FEATURE_ROW_CONFLICT')
            features[feature['fixture_id']] = (index, feature)
        targets, proofs, target_issues = _targets(root, {f['fixture_id']: f for f in fixtures}, cutoff)
        contexts = []
        for fixture in fixtures:
            target = target_fixture(fixture); fid = fixture['fixture_id']
            row = {'fixture_id': fid, 'kickoff': fixture['kickoff'], 'status': 'UNKNOWN',
                'reason': 'MISSING_OR_LOCKED_CALENDAR_ROTATION_CONTEXT',
                'calendar_teams_verified': 0, 'xi_comparison_teams': 0, 'target_receipt': proofs.get(fid),
                'source_row_index': None, 'source_row_sha256': None, 'feature_dataset_id': None,
                'source_envelopes': [], 'teams': [], 'probability_adjustment': 0,
                'rotation_probability': None, 'rotation_cause': None, 'team_strength_effect': None,
                'ranking_multiplier': 1., 'live_decision_effect': False}
            pair = features.get(target['fixture_id'])
            if pair is not None:
                index, feature = pair; work = schedules[target['fixture_id']]
                expected = {(side, target[side+'_team_id']) for side in ('home', 'away')}
                if (feature['dataset']['rows'][0] != target or
                        {(t['side'], t['team_id']) for t in feature['teams']} != expected or
                        {(t['side'], t['team_id']) for t in work['teams']} != expected or
                        len(feature['teams']) != 2 or len(work['teams']) != 2):
                    raise ValueError('ROTATION_EXACT_TARGET_OR_TEAM_CONFLICT')
                if any(t.get(k) is not None for t in feature['teams']
                       for k in ('rotation_probability', 'rotation_cause', 'team_strength_effect')):
                    raise ValueError('UNVALIDATED_ROTATION_EFFECT')
                row.update(source_row_index=index, source_row_sha256=canonical_sha256(view['dataset']['rows'][index]),
                    feature_dataset_id=feature['dataset']['manifest']['dataset_id'],
                    source_envelopes=feature['dataset']['lineage'], teams=feature['teams'])
                if fid in targets:
                    row['calendar_teams_verified'] = sum(t['status'] == 'AVAILABLE_SHADOW' for t in work['teams'])
                    row['xi_comparison_teams'] = sum(t['status'] == 'XI_COMPARISON_SHADOW' for t in feature['teams'])
                    row['status'] = 'SOURCE_BOUND_CONTEXT_SHADOW' if row['calendar_teams_verified'] else 'UNKNOWN'
                    row['reason'] = ('DIAGNOSTIC_CONTEXT_ONLY_NO_VALIDATED_EFFECT' if row['calendar_teams_verified']
                                     else 'MISSING_OR_INVALID_TEAM_CALENDARS')
                else:
                    row['reason'] = 'UNVERIFIED_FRESH_NATIVE_TARGET'
            contexts.append(row)
        indexed = {r['fixture_id']: (i, r) for i, r in enumerate(contexts)}
        option_reviews, specialist_reviews, count = _bind_reviews(options, specialists, indexed,
            view['dataset']['manifest']['dataset_id'])
        if before != canonical_sha256([fixtures, options, specialists]):
            raise ValueError('ROTATION_CONTEXT_INPUT_MUTATED')
        return {'version': VERSION, 'mode': 'SHADOW', 'status': 'OBSERVED', 'as_of': cutoff.isoformat(),
            'source_view': view, 'contexts': contexts, 'target_issues': target_issues,
            'option_reviews': option_reviews, 'specialist_reviews': specialist_reviews, 'leg_bindings': count,
            'input_sha256': before, 'probability_adjustment': 0, 'ranking_multiplier': 1.,
            'live_decision_effect': False, 'external_requests': 0, 'production_writes': 0,
            'effect_validation_complete': False,
            'summary': {'fixture_contexts': len(contexts),
                'calendar_teams_verified': sum(r['calendar_teams_verified'] for r in contexts),
                'xi_comparison_teams': sum(r['xi_comparison_teams'] for r in contexts)}}
    except Exception:  # noqa: BLE001 - optional context cannot veto or modify stable branches
        return {'version': VERSION, 'mode': 'SHADOW', 'status': 'UNAVAILABLE',
            'reason': 'NATIVE_CALENDAR_ROTATION_CONTEXT_FAILED', 'contexts': [],
            'option_reviews': [], 'specialist_reviews': {}, 'leg_bindings': 0,
            'probability_adjustment': 0, 'ranking_multiplier': 1., 'live_decision_effect': False}


def completion_checks(review, completed, *, options, specialists):
    """Recheck actual leg hashes and expiring context, never grant publication."""
    try:
        contexts = review.get('contexts', [])
        indexed = {r['fixture_id']: (i, r) for i, r in enumerate(contexts)}
        dataset_id = review['source_view']['dataset']['manifest']['dataset_id']
        option_reviews, specialist_reviews, count = _bind_reviews(options, specialists, indexed, dataset_id)
        bound = (option_reviews == review['option_reviews'] and specialist_reviews == review['specialist_reviews']
                 and count == review['leg_bindings'])
        checks = []
        for row in contexts:
            valid = bound and row['calendar_teams_verified'] > 0
            if valid:
                valid &= utc(row['kickoff']) > completed+timedelta(minutes=15)
                age = (completed-utc(row['target_receipt']['times']['observed_at'])).total_seconds()
                valid &= 0 <= age <= 1800
            current_xis_fresh = True
            for envelope in row['source_envelopes']:
                payload = envelope['payload']
                age = (completed-utc(envelope['times']['observed_at'])).total_seconds()
                if payload.get('scope') == 'ALL_COMPETITIONS_ALL_STATUSES':
                    valid &= 0 <= age <= MAX_AGE_HOURS*3600
                elif payload.get('fixture_id') == row['fixture_id'].removeprefix('the_stats_api:'):
                    current_xis_fresh &= 0 <= age <= LINEUP_MAX_AGE_HOURS*3600
            checks.append({'fixture_id': row['fixture_id'], 'calendar_context_eligible_at_completion': bool(valid),
                'xi_context_eligible_at_completion': bool(valid and current_xis_fresh and row['xi_comparison_teams']>0),
                'publication_eligible': False, 'ranking_multiplier': 1.})
        return {'status': 'OBSERVED', 'leg_bindings_current': bound, 'context_checks': checks,
                'publication_eligible': False}
    except Exception:  # noqa: BLE001 - failure is diagnostic, not a portfolio veto
        return {'status': 'UNAVAILABLE', 'leg_bindings_current': False, 'context_checks': [],
                'publication_eligible': False}
