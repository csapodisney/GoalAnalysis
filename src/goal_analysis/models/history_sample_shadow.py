"""Opt-in offline sample-policy experiment; its probabilities never enter selection.

Neutral history belongs to a different home-advantage regime. This candidate
policy isolates it without changing any existing model fit/prediction gate.
The legacy control is deliberately left intact, including its missing-flag
compatibility. Receipt-based historical availability is NOT certified here.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from goal_analysis.agents import canonical_sha256

from ._data import aware_time, fixture_identity, identifier, prepare_history

POLICY_VERSION = 'explicit-non-neutral-history-shadow-v1'


def _history_id(row):
    if row.get('provider') == 'the_stats_api':
        from goal_analysis.normalization.live_identity import provider_id

        return provider_id(row['fixture_id'], 'fixture', 'the_stats_api')
    return identifier(row['fixture_id'])


def select_sample(fixtures: list[dict], history: list[dict], now) -> dict:
    """Validate/conflict-check BEFORE removing neutral rows, never infer False.

    An unknown venue flag on any version quarantines that history fixture.
    Valid True/False or score/identity contradictions remain visible to the
    existing shared conflict checker. No outcome-dependent sample selection.
    """
    now = aware_time(now)
    if not isinstance(fixtures, list) or not isinstance(history, list):
        raise TypeError('Fixture and history lists required')
    known, unknown_ids, quarantined_ids, sources, counts = [], set(), set(), {}, Counter()
    target_ids, unknown_targets = set(), set()
    for row in fixtures:
        try:
            external, identity = fixture_identity(row, row.get('fixture', {}))
            target_ids.add(identity)
            if type(row.get('neutral_venue')) is not bool:
                unknown_targets.add(external)
        except (KeyError, TypeError, ValueError, AttributeError):
            counts['INVALID_TARGET_IDENTITY'] += 1
    shadow_fixtures = []
    for row in fixtures:
        value = deepcopy(row)
        try:
            external, _ = fixture_identity(row, row.get('fixture', {}))
        except (KeyError, TypeError, ValueError, AttributeError):
            external = None
        if type(row.get('neutral_venue')) is not bool or external in unknown_targets:
            # The shared fixture normalizer rejects None; no legacy False default.
            value['neutral_venue'] = None
            counts['UNKNOWN_TARGET_VENUE'] += 1
        shadow_fixtures.append(value)
    for row in history:
        try:
            identity = _history_id(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            counts['INVALID_HISTORY_IDENTITY'] += 1
            continue
        if type(row.get('neutral_venue')) is not bool:
            unknown_ids.add(identity)
            counts['UNKNOWN_HISTORY_VENUE'] += 1
            # Test existing chronology/score/source eligibility independently of
            # the missing flag. A future or invalid version cannot quarantine
            # a past valid observation. This temporary flag is never selected.
            eligible_unknown, _ = prepare_history([{**row, 'neutral_venue': False}],
                                                   now, target_ids)
            if eligible_unknown:
                quarantined_ids.add(identity)
            continue
        known.append(row)
        sources[identity] = row.get('provider')
    quarantined = [row for row in known if _history_id(row) in quarantined_ids]
    counts['KNOWN_ROWS_QUARANTINED_BY_UNKNOWN_VERSION'] = len(quarantined)
    eligible = [row for row in known if _history_id(row) not in quarantined_ids]
    prepared, issues = prepare_history(eligible, now, target_ids)
    neutral = [row for row in prepared if row['neutral_venue'] is True]
    selected = []
    for row in prepared:
        if row['neutral_venue'] is not False:
            continue
        value = {key: val for key, val in row.items() if not key.startswith('_')}
        if sources[row['fixture_id']] == 'the_stats_api':
            value['provider'] = 'the_stats_api'
        selected.append(value)
    return {'version': POLICY_VERSION, 'mode': 'SHADOW', 'decision_effect': 'NONE',
        'input_history_rows': len(history), 'prepared_history_rows': len(prepared),
        'selected_history_rows': len(selected), 'isolated_neutral_rows': len(neutral),
        'isolated_neutral_ids': sorted(row['fixture_id'] for row in neutral),
        'unknown_history_ids': sorted(unknown_ids),
        'unknown_eligible_history_ids_quarantined': sorted(quarantined_ids),
        'unknown_target_ids': sorted(unknown_targets),
        'boundary_counts': dict(sorted(counts.items())), 'history_diagnostics': issues,
        'selected_history_sha256': canonical_sha256(selected),
        'fixtures': shadow_fixtures, 'history': selected}


def evaluate(fixtures, history, now, *, enabled=False,
             enabled_profiles=('gauss', 'kepler', 'laplace'), artifact_dir=None):
    """At most two model runs, original control + isolated treatment, no fallback.

    Switch is deliberately a strict bool, off by default. Callers must keep
    these outputs separate from numeric_models/recommendation inputs. The
    optional JSON dataset archives use the same inside-repository safety gate
    as predict_models, under separate control and treatment directories.
    """
    if type(enabled) is not bool:
        raise ValueError('SHADOW switch must be boolean')
    base = {'version': POLICY_VERSION, 'mode': 'SHADOW', 'decision_effect': 'NONE',
            'live_promotion': False, 'external_requests': 0,
            'point_in_time_provider_history_certified': False}
    if not enabled:
        return {**base, 'status': 'DISABLED', 'model_runs': 0}
    from goal_analysis.models import predict_models

    now = aware_time(now)
    selected = select_sample(fixtures, history, now)
    root = Path(artifact_dir) if artifact_dir is not None else None
    control = predict_models(deepcopy(fixtures), deepcopy(history), now,
        enabled_profiles=enabled_profiles, artifact_dir=root / 'control' if root else None)
    treatment = predict_models(selected['fixtures'], selected['history'], now,
        enabled_profiles=enabled_profiles, artifact_dir=root / 'treatment' if root else None)
    return {**base, 'status': 'COMPLETE', 'model_runs': 2,
        'implementation_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'as_of': now.isoformat(), 'input_sha256': canonical_sha256([fixtures, history]),
        'selection': {k: v for k, v in selected.items() if k not in {'fixtures', 'history'}},
        'control': control, 'treatment': treatment}
