"""Bounded retained/online union before explicitly enabled NORMAL model calls.

No I/O or source admission is performed here. The injected reader owns physical
raw/receipt validation and is called ONCE for the complete fixture universe.
The old coordinator path remains unchanged unless its caller opts into this API.
"""
from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from goal_analysis.jobs.native_history_compaction import PROVENANCE, reconstruct
from goal_analysis.jobs.point_in_time import require_chronological_availability
from goal_analysis.models._data import aware_time, fixture_identity, identifier, prepare_history
from goal_analysis.models.history_sample_shadow import _history_id, select_sample
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.live_identity import field_id, provider_id
from goal_analysis.normalization.training_datasets import sha, snapshot, validate

VERSION = 'normal-witnessed-history-standard-venue-v1'
MAX_TRUTHS = 10000
MAX_RAW_ALIASES = 200 * 100
PROFILES = ('gauss', 'kepler', 'laplace')


def _comp(fixture):
    if not isinstance(fixture, dict):
        raise TypeError('INVALID_TARGET_COMPETITION')
    if fixture.get('source_provider') == 'the_stats_api':
        return field_id(fixture, 'league')
    league = fixture.get('league', {})
    nested = league.get('id') if isinstance(league, dict) else None
    return identifier(fixture.get('league_id', fixture.get('api_football_league_id', nested)))


def canonical_history_comp(record):
    """Compare canonical competition keys without modifying original row fields."""
    if not isinstance(record, dict):
        raise TypeError('INVALID_HISTORY_COMPETITION')
    if record.get('provider') == 'the_stats_api':
        return provider_id(record['league_id'], 'league', 'the_stats_api')
    return identifier(record['league_id'])


def _clock_issue(row, cutoff):
    if not isinstance(row, dict):
        return 'MALFORMED_ONLINE_ROW'
    try:
        for key in ('observed_at', 'available_at', 'ingested_at', 'published_at'):
            if row.get(key) is not None and aware_time(row[key]) > cutoff:
                return 'FUTURE_KNOWLEDGE'
        return None
    except (TypeError, ValueError, OverflowError):
        return 'INVALID_KNOWLEDGE_CLOCK'


def _truth(row):
    return {k: v for k, v in row.items() if k not in PROVENANCE and not k.startswith('_')}


def _exact_selected(fixtures, rows, cutoff):
    """Use existing policy, then recover exact originals instead of clock grafts."""
    selection = select_sample(deepcopy(fixtures), deepcopy(rows), cutoff)
    target_ids = set()
    for target in fixtures:
        try:
            target_ids.add(fixture_identity(target, target.get('fixture', {}))[1])
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    originals = {}
    for raw in rows:
        try:
            prepared, _ = prepare_history([raw], cutoff, target_ids)
            if not prepared:
                continue
            normalized = {k: v for k, v in prepared[0].items() if not k.startswith('_')}
            if raw.get('provider') == 'the_stats_api':
                normalized['provider'] = 'the_stats_api'
            originals.setdefault(sha(normalized), []).append(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    exact = []
    for selected in selection['history']:
        compatible = originals[sha(selected)]
        # Same normalized source/observation; tie-break exact whole originals.
        # No available_at/ingested_at is borrowed from another representative.
        exact.append(deepcopy(min(compatible, key=sha)))
    return selection, exact


def prepare_inputs(fixtures, online_history, as_of, *, retained_reader=None):
    """Pure policy boundary. Callback signature: reader(fixtures, as_of=cutoff)."""
    cutoff = aware_time(as_of)
    if not isinstance(fixtures, list) or not isinstance(online_history, list):
        raise TypeError('Fixture and history lists required')
    if len(fixtures) > 1000 or len(online_history) > MAX_RAW_ALIASES:
        raise ValueError('NORMAL_HISTORY_INPUT_BOUND')
    originals = deepcopy(online_history)
    online, ledger = [], []
    for index, row in enumerate(originals):
        try:
            row_hash = sha(row)
            issue = _clock_issue(row, cutoff)
        except (ValueError, TypeError):
            row_hash = sha({'noncanonical_type': type(row).__name__})
            issue = 'NONCANONICAL_ONLINE_ROW'
        try:
            normalized_id = _history_id(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            normalized_id = None
        raw_id = row.get('fixture_id') if isinstance(row, dict) else None
        ledger.append({'origin': 'ONLINE', 'input_index': index, 'row_sha256': row_hash,
                       'fixture_id': raw_id if type(raw_id) in (str, int) else None,
                       'normalized_fixture_id': normalized_id,
                       'clock_issue': issue})
        if issue is None:
            online.append(row)
    if len({sha(_truth(row)) for row in online}) > MAX_TRUTHS:
        raise ValueError('ONLINE_TRUTH_BOUND_EXCEEDED_NO_FIRST_N')
    retained, reader_diagnostics, dataset = [], [], None
    if retained_reader is None:
        reader_diagnostics.append({'code': 'RETAINED_READER_NOT_CONFIGURED'})
    else:
        try:
            # Exactly one callback: splitting 200 sources per league is forbidden.
            view = retained_reader(deepcopy(fixtures), as_of=cutoff)
            if view['status'] not in {'AVAILABLE', 'GAPS'}:
                reader_diagnostics.append({'code': 'RETAINED_NOT_ADMITTED', 'status': view['status'],
                                           'gaps': deepcopy(view.get('gaps', {}))})
            else:
                if aware_time(view['as_of']) != cutoff:
                    raise ValueError('RETAINED_CUTOFF_MISMATCH')
                dataset = validate(view['dataset'])
                if aware_time(dataset['manifest']['as_of']) != cutoff:
                    raise ValueError('RETAINED_DATASET_CUTOFF_MISMATCH')
                groups = dataset['lineage']['lossless_history_aliases']
                if len(groups) > MAX_TRUTHS or sum(len(g['aliases']) for g in groups) > MAX_RAW_ALIASES:
                    raise ValueError('RETAINED_TRUTH_OR_ALIAS_BOUND')
                retained = model_inputs(reconstruct(groups), as_of=cutoff)
                native = {f['fixture_id']: f for f in fixtures if f.get('source_provider') == 'the_stats_api'}
                proven = {}
                for target in view['fixtures']:
                    original = native[target['fixture_id']]
                    if any(target.get(k) != v for k, v in original.items()):
                        raise ValueError('RETAINED_TARGET_BINDING_MISMATCH')
                    proven[target['fixture_id']] = target
                competitions = {_comp(native[key]) for key in proven}
                if any(row.get('provider') != 'the_stats_api' or row['league_id'] not in competitions for row in retained):
                    raise ValueError('RETAINED_UNPROVEN_TARGET_COMPETITION')
                if len(online) + len(retained) > MAX_RAW_ALIASES:
                    raise ValueError('UNION_RAW_ALIAS_BOUND_EXCEEDED_NO_FIRST_N')
                if len({sha(_truth(row)) for row in online + retained}) > MAX_TRUTHS:
                    raise ValueError('UNION_TRUTH_BOUND_EXCEEDED_NO_FIRST_N')
                reader_diagnostics.append({'code': 'RETAINED_COMPLETE_BOUNDED_READ', 'status': view['status'],
                    'gaps': deepcopy(view.get('gaps', {})), 'dataset_id': dataset['manifest']['dataset_id'],
                    'source_count': view.get('source_count'), 'verified_target_ids': sorted(proven)})
        except Exception as error:  # noqa: BLE001 - local retained reader cannot kill valid online models
            retained, dataset = [], None
            safe_codes = {'RETAINED_CUTOFF_MISMATCH', 'RETAINED_DATASET_CUTOFF_MISMATCH', 'RETAINED_TRUTH_OR_ALIAS_BOUND',
                          'RETAINED_TARGET_BINDING_MISMATCH', 'RETAINED_UNPROVEN_TARGET_COMPETITION',
                          'UNION_TRUTH_BOUND_EXCEEDED_NO_FIRST_N', 'UNION_RAW_ALIAS_BOUND_EXCEEDED_NO_FIRST_N'}
            reader_diagnostics.append({'code': 'RETAINED_READER_UNAVAILABLE', 'failure_class': type(error).__name__,
                                       'reason': str(error) if str(error) in safe_codes else 'INVALID_OR_UNAVAILABLE_RETAINED_SOURCE'})
    rows = online + retained
    for row in retained:
        ledger.append({'origin': 'RETAINED', 'row_sha256': sha(row), 'fixture_id': row['fixture_id'],
                       'normalized_fixture_id': _history_id(row), 'clock_issue': None})
    selection, exact = _exact_selected(fixtures, rows, cutoff)
    online_hashes, retained_hashes = {sha(row) for row in online}, {sha(row) for row in retained}
    selected_origins = []
    for row in exact:
        key = sha(row)
        in_online, in_retained = key in online_hashes, key in retained_hashes
        assert in_online or in_retained, 'Selected representative is absent from exact source universe'
        origin = 'BOTH' if in_online and in_retained else 'ONLINE' if in_online else 'RETAINED'
        selected_origins.append({'record_sha256': key, 'fixture_id': row['fixture_id'], 'origin': origin,
            'source_id': row['source_id'], 'observed_at': row['observed_at'],
            'available_at': row.get('available_at')})
    origin_counts = {origin: sum(row['origin'] == origin for row in selected_origins)
                     for origin in ('ONLINE', 'RETAINED', 'BOTH')}
    selected_ids = {_history_id(row) for row in exact}
    _, selected_online = _exact_selected(fixtures, online, cutoff)
    # A known retained contradiction/unknown version cannot be hidden by choosing
    # the older online-only fallback for Kepler.
    selected_online = [row for row in selected_online if _history_id(row) in selected_ids]
    conflicts = {row['fixture_id'] for row in selection['history_diagnostics'] if row['code'] == 'CONFLICTING_HISTORY'}
    unknown = set(selection['unknown_eligible_history_ids_quarantined'])
    for entry in ledger:
        identifier = entry['normalized_fixture_id']
        if entry['clock_issue']:
            entry['partition'] = entry['clock_issue']
        elif identifier in conflicts:
            entry['partition'] = 'CONFLICTING_IDENTITY'
        elif identifier in unknown:
            entry['partition'] = 'UNKNOWN_VENUE_IDENTITY'
        elif identifier in selected_ids:
            entry['partition'] = 'SELECTED_STANDARD_TRUTH_ALIAS'
        else:
            entry['partition'] = 'NONSTANDARD_TARGET_INVALID_OR_INELIGIBLE'
    consumed = snapshot(exact, role='normal_standard_venue_history', as_of=cutoff,
        recipe={'version': VERSION, 'policy': selection['version'], 'truth_bound': MAX_TRUTHS,
                'raw_alias_bound': MAX_RAW_ALIASES, 'selection': 'CONFLICT_BEFORE_EXPLICIT_STANDARD_VENUE'},
        lineage={'input_partition': ledger,
                 'selected_record_origins': selected_origins,
                 'retained_dataset': dataset['manifest'] if dataset else None,
                 'retained_lineage_sha256': sha(dataset['lineage']) if dataset else None,
                 'online_clock_basis': 'EXISTING_NORMAL_OBSERVED_CLOCK_NOT_NEW_PIT_CERTIFICATION'})
    return {'version': VERSION, 'mode': 'NORMAL_EXPLICIT', 'as_of': cutoff.isoformat(),
        'fixtures': selection['fixtures'], 'history': exact, 'online_history': selected_online,
        'selection': {k: v for k, v in selection.items() if k not in {'fixtures', 'history'}},
        'dataset': consumed, 'partition_counts': dict(Counter(e['partition'] for e in ledger)),
        'selected_record_origins': selected_origins, 'selected_record_origin_counts': origin_counts,
        'retained_selected_records': origin_counts['RETAINED']+origin_counts['BOTH'],
        'retained_only_selected_records': origin_counts['RETAINED'],
        'reader_diagnostics': reader_diagnostics, 'retained_reader_calls': int(retained_reader is not None),
        'historical_feature_availability_certified': False}


def _kepler_inputs(combined, online):
    """Retained chronology is stricter; the existing online approximation survives."""
    online_ids = {_history_id(row) for row in online}
    added = [row for row in combined if _history_id(row) not in online_ids]
    if not added:
        return online, 'ORIGINAL_ONLINE_KICKOFF_PLUS_3H_APPROXIMATION_NOT_CERTIFIED_PIT'
    try:
        require_chronological_availability(added)
        clocks = sorted({aware_time(row['kickoff']) for row in combined})
        for row in added:
            release = aware_time(row['kickoff']) + timedelta(hours=3)
            index = bisect_right(clocks, release)
            if index < len(clocks) and (aware_time(row['available_at']) > clocks[index]
                    or aware_time(row['observed_at']) > clocks[index]):
                raise ValueError('UNPROVEN_CHRONOLOGICAL_FEATURE_AVAILABILITY')
        return combined, 'RETAINED_CHRONOLOGY_VERIFIED_ONLINE_APPROXIMATION_NOT_CERTIFIED_PIT'
    except (KeyError, TypeError, ValueError, OverflowError):
        return online, 'RETAINED_CHRONOLOGY_UNPROVEN_ORIGINAL_ONLINE_APPROXIMATION_FALLBACK'


def _merge(profile, competitions):
    values = list(competitions.values())
    result = {'profile_id': profile, 'predictions': [], 'diagnostics': [],
              'competition_results': deepcopy(competitions)}
    for value in values:
        result['predictions'].extend(deepcopy(value.get('predictions', [])))
        result['diagnostics'].extend(deepcopy(value.get('diagnostics', [])))
    statuses = {value.get('status', 'MODEL_ERROR') for value in values}
    if not values:
        result['status'] = 'NO_FIXTURES'
    elif result['predictions']:
        result['status'] = 'READY'
    elif len(statuses) == 1:
        result['status'] = next(iter(statuses))
    else:
        result['status'] = 'MODEL_ERROR' if 'MODEL_ERROR' in statuses else 'INSUFFICIENT_HISTORY'
    result['message'] = 'Competition-isolated existing numerical model; estimates remain uncalibrated.'
    manifests, uses = {}, []
    for value in values:
        data = value.get('training_datasets', {})
        manifests.update({m['dataset_id']: m for m in data.get('manifests', [])})
        uses.extend(data.get('uses', []))
    result['training_datasets'] = {'version': 'training-dataset-v1', 'mode': 'NORMAL_EXPLICIT',
        'manifests': [manifests[key] for key in sorted(manifests)], 'uses': uses,
        'archive': {'status': 'SEE_COMPETITION_RESULTS', 'scope': 'PER_COMPETITION'}}
    return result


def predict_normal(fixtures, online_history, now, *, retained_reader=None, predictor,
                   enabled_profiles=PROFILES, artifact_dir=None):
    """Explicit new caller; injected predictor is the unchanged predict_models."""
    enabled = tuple(dict.fromkeys(enabled_profiles))
    if not set(enabled) <= set(PROFILES):
        raise ValueError('UNSUPPORTED_NORMAL_NUMERICAL_PROFILE')
    view = prepare_inputs(fixtures, online_history, now, retained_reader=retained_reader)
    groups, issues = {}, []
    for fixture in view['fixtures']:
        try:
            groups.setdefault(_comp(fixture), []).append(fixture)
        except (KeyError, TypeError, ValueError):
            issues.append({'code': 'INVALID_TARGET_COMPETITION', 'fixture_id': fixture.get('fixture_id')})
    results = {profile: {} for profile in enabled}
    inputs = []
    for comp, targets in sorted(groups.items()):
        combined = [row for row in view['history'] if canonical_history_comp(row) == comp]
        online = [row for row in view['online_history'] if canonical_history_comp(row) == comp]
        kepler, basis = _kepler_inputs(combined, online)
        for profile in enabled:
            history = kepler if profile == 'kepler' else combined
            path = Path(artifact_dir)/comp/profile if artifact_dir is not None else None
            inputs.append({'profile_id': profile, 'competition_id': comp,
                'history_rows': len(history), 'history_sha256': sha(history),
                'basis': basis if profile == 'kepler' else 'CURRENT_CUTOFF_STANDARD_VENUE_UNION'})
            try:
                output = predictor(deepcopy(targets), deepcopy(history), aware_time(now),
                                   enabled_profiles=(profile,), artifact_dir=path)
                results[profile][comp] = output[profile]
                if not isinstance(output[profile], dict):
                    raise TypeError('Invalid numerical model response')
            except Exception as error:  # noqa: BLE001 - isolate one competition/model
                results[profile][comp] = {'profile_id': profile, 'status': 'MODEL_ERROR', 'predictions': [],
                    'diagnostics': [{'code': 'MODEL_RUN_FAILED', 'competition_id': comp,
                                     'failure_class': type(error).__name__}]}
    return {'numeric_models': {profile: _merge(profile, values) for profile, values in results.items()},
            'input_view': view, 'model_input_uses': inputs, 'fixture_issues': issues}
