"""Bind weather context/unknowns to each research leg without changing ranks."""
from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.native_weather_shadow import inspect_native_weather


def review_weather_options(root, fixtures, options, specialists, *, as_of):
    before = canonical_sha256([fixtures, options, specialists])
    try:
        audit = inspect_native_weather(root, fixtures, as_of=as_of)
        contexts = {r['fixture_id']: (i, r) for i, r in enumerate(audit['rows'])}
        count = sum(len(o['recommendation']['legs']) for rows in options.values() for o in rows)
        count += sum(len(s.get('candidate_legs', [])) for s in specialists.values())
        if count > 10000:
            raise ValueError('BOUNDED_WEATHER_LEG_BINDINGS_REQUIRED')

        def bind(leg):
            index, row = contexts.get(leg.get('fixture_id'), (None, {}))
            return {'leg_sha256': canonical_sha256(leg), 'fixture_id': leg.get('fixture_id'),
                'context_row_index': index, 'context_sha256': canonical_sha256(row) if row else None,
                'dataset_id': audit['dataset']['manifest']['dataset_id'],
                'context_status': row.get('status', 'UNKNOWN'),
                'reason': row.get('reason', 'MISSING_NATIVE_WEATHER_CONTEXT'),
                'data_use': 'CONTEXT_ONLY_NO_VALIDATED_EFFECT' if row.get('status') ==
                            'FORECAST_CONTEXT_VERIFIED_SHADOW' else 'WITHHELD_UNVERIFIED_CONTEXT',
                'probability_adjustment': 0, 'ranking_multiplier': 1., 'live_decision_effect': False}

        option_reviews = [{'profile_id': profile, 'option_index': i,
            'option_sha256': canonical_sha256(option),
            'legs': [bind(leg) for leg in option['recommendation']['legs']]}
            for profile, rows in options.items() for i, option in enumerate(rows)]
        specialist_reviews = {key: [bind(leg) for leg in s.get('candidate_legs', [])]
                              for key, s in specialists.items()}
        if before != canonical_sha256([fixtures, options, specialists]):
            raise ValueError('WEATHER_SHADOW_INPUT_MUTATED')
        return {'mode': 'SHADOW', 'status': 'OBSERVED', 'source_audit': audit,
            'option_reviews': option_reviews, 'specialist_reviews': specialist_reviews,
            'input_sha256': before, 'leg_bindings': count, 'probability_adjustment': 0,
            'ranking_multiplier': 1., 'external_requests': 0, 'production_writes': 0,
            'live_decision_effect': False, 'heldout_effect_validation_complete': False}
    except Exception:  # noqa: BLE001 - context auditing cannot veto or mutate model options
        return {'mode': 'SHADOW', 'status': 'UNAVAILABLE', 'reason': 'NATIVE_WEATHER_CONTEXT_AUDIT_FAILED',
                'option_reviews': [], 'specialist_reviews': {}, 'leg_bindings': 0,
                'probability_adjustment': 0, 'ranking_multiplier': 1., 'live_decision_effect': False}


def completion_checks(review, completed):
    from datetime import timedelta

    from goal_analysis.agents.weather_expert import POLICY
    from goal_analysis.normalization.canonical import utc

    checks = []
    for row in review.get('source_audit', {}).get('rows', []):
        eligible = row['status'] == 'FORECAST_CONTEXT_VERIFIED_SHADOW'
        if eligible:
            eligible &= utc(row['kickoff']) > completed+timedelta(minutes=15)
            for key, seconds in (('target_receipt', 1800),
                                 ('forecast_receipt', POLICY['forecast_freshness_hours']*3600)):
                age = (completed-utc(row[key]['times']['observed_at'])).total_seconds()
                eligible &= 0 <= age <= seconds
        checks.append({'fixture_id': row['fixture_id'], 'context_eligible_at_completion': bool(eligible),
                       'publication_eligible': False, 'ranking_multiplier': 1.})
    return checks
