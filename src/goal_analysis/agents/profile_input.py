"""Readable full-day tables; raw provenance stays in the immutable snapshot."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime

from goal_analysis.jobs.agent_snapshot import SharedSnapshot, digest, json_text, require

VIEW_VERSION = 'ten-agent-readable-decision-tables-v3'
FINAL_REQUEST_PROJECTION = 'FINAL_EXACT_SUBTREES_V1'
SOURCE_DETAILS_POLICY = 'READ_DATA_EXACT_ROWS_V1'


NEWS_COVERAGE_REQUEST_PROJECTION = 'REQUEST_NEWS_COVERAGE_TABLE_V1'


def news_coverage_request_data(data):
    """Lossless repeated news coverage only; full daily inputs remain available."""
    result = deepcopy(data)
    if not isinstance(result, dict) or 'coverage_data_projection' in result:
        return result
    path = ('snapshot', 'families', 'sourced_news', 'coverage', 'by_fixture_topic')
    parent = result
    for key in path[:-1]:
        if not isinstance(parent, dict) or key not in parent:
            return result
        parent = parent[key]
    if not isinstance(parent, dict):
        return result
    rows = parent.get(path[-1])
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        return result
    table = encode_table(rows)
    parent[path[-1]] = table
    result['coverage_data_projection'] = {
        'version': NEWS_COVERAGE_REQUEST_PROJECTION, 'source_data_sha256': digest(data),
        'path': list(path), 'source_rows_sha256': digest(rows),
        'instruction': 'Only the declared news-coverage list is encoded as NAMED_COLUMNS_V1. '
                       'Decode columns with lookups and defaults, preserve missing_rows distinctly '
                       'from null, and retain every original row in order. The direct_rows_sha256 '
                       'binds the original list. UNKNOWN stays unknown; no fixture or topic is filtered. '
                       'All other request fields, including the full calendar and candidate catalogue, '
                       'are unchanged. This representation is not a claim that all source rows were read.',
    }
    return result if len(json_text(result)) < len(json_text(data)) else deepcopy(data)


def encode_table(records):
    """Exact rows with named columns, visible defaults and optional value lookups.

    A lookup column contains a zero-based index into that named column's lookup.
    Missing cells have explicit row indexes, distinct from an actual null value.
    There is no binary encoding, hidden executable decoder or numeric rounding.
    """
    require(isinstance(records, list) and all(isinstance(row, dict) for row in records), 'TABLE_ROWS_REQUIRED')
    keys = list(dict.fromkeys(key for row in records for key in row))
    defaults, missing, columns, lookups = {}, {}, [], {}
    values = {}
    for key in keys:
        absent = [index for index, row in enumerate(records) if key not in row]
        if absent:
            missing[key] = absent
        present = [row[key] for row in records if key in row]
        unique = {}
        for value in present:
            unique.setdefault(json_text(value), deepcopy(value))
        if len(unique) == 1:
            defaults[key] = next(iter(unique.values()))
            continue
        column = [deepcopy(row.get(key)) for row in records]
        dictionary = list(unique.values())
        indices = {json_text(value): index for index, value in enumerate(dictionary)}
        refs = [indices[json_text(row[key])] if key in row else None for row in records]
        # A lookup is used only when its explicit dictionary actually saves bytes.
        use_lookup = len(json_text({'values': dictionary, 'indices': refs})) < len(json_text(column))
        columns.append({'name': key, 'encoding': 'lookup' if use_lookup else 'literal'})
        values[key] = refs if use_lookup else column
        if use_lookup:
            lookups[key] = dictionary
    return {'format': 'NAMED_COLUMNS_V1', 'row_count': len(records), 'columns': columns,
            'defaults': defaults, 'missing_rows': missing, 'lookups': lookups,
            'rows': [[values[column['name']][index] for column in columns] for index in range(len(records))],
            'direct_rows_sha256': digest(records)}


def decode_table(table):
    """Internal exactness control; the host sees readable columns and lookups."""
    require(table.get('format') == 'NAMED_COLUMNS_V1' and len(table['rows']) == table['row_count'],
            'INVALID_TABLE')
    decoded = []
    for index, cells in enumerate(table['rows']):
        require(len(cells) == len(table['columns']), 'TABLE_WIDTH_CHANGED')
        row = deepcopy(table['defaults'])
        for column, cell in zip(table['columns'], cells, strict=True):
            name = column['name']
            if index in table['missing_rows'].get(name, []):
                continue
            row[name] = deepcopy(table['lookups'][name][cell] if column['encoding'] == 'lookup' else cell)
        for name, missing in table['missing_rows'].items():
            if index in missing:
                row.pop(name, None)
        decoded.append(row)
    require(digest(decoded) == table['direct_rows_sha256'], 'DIRECT_ROW_RECONSTRUCTION_FAILED')
    return decoded


def decode_candidates(odds_view, producer, fixture_rows):
    """Reconstruct declared direct candidate fields, including producer changes."""
    encoded = odds_view['candidate_tables'][producer]
    if encoded.get('format') == 'SPARSE_PRODUCER_DIFFERENCES_V3':
        base = decode_table(odds_view['candidate_tables'][encoded['base_producer']])
        records = [deepcopy(base[index]) if index is not None else {}
                   for index in encoded['base_row_indexes']]
        for change in encoded['changes']:
            name = change['field']
            for row_index, item in zip(change['row_indexes'], change['values'], strict=True):
                records[row_index][name] = deepcopy(item)
        for name, indexes in encoded['removed_fields'].items():
            for index in indexes:
                records[index].pop(name, None)
        require(digest(records) == encoded['direct_rows_sha256'], 'CANDIDATE_DELTA_RECONSTRUCTION_FAILED')
    elif encoded.get('format') == 'BASE_ROW_DELTAS_V1':
        base = decode_table(odds_view['candidate_tables'][encoded['base_producer']])
        records = []
        for delta in decode_table(encoded['table']):
            if 'standalone' in delta:
                records.append(delta['standalone'])
                continue
            row = deepcopy(base[delta['base_row_index']])
            for key in delta['removed_fields']:
                row.pop(key, None)
            row.update(deepcopy(delta['overrides']))
            records.append(row)
        require(digest(records) == encoded['direct_rows_sha256'], 'CANDIDATE_DELTA_RECONSTRUCTION_FAILED')
    else:
        records = decode_table(encoded)
    fixtures = {row['fixture_id']: row for row in fixture_rows}
    evidence_values = decode_table(odds_view['evidence_values']) if 'evidence_values' in odds_view else []
    feature_matches = {}
    if any('source_evidence_indexes' in row for row in records):
        for feature in decode_candidate_features(odds_view, producer):
            if isinstance(feature, dict):
                feature_matches.setdefault(json_text(feature.get('candidate_id')), []).append(feature)
    result = []
    for row in records:
        row = deepcopy(row)
        if 'source_candidate_index' in row:
            row['candidate_id'] = deepcopy(odds_view['candidate_ids'][row.pop('source_candidate_index')])
        if 'source_evidence_indexes' in row:
            row['evidence'] = [deepcopy(evidence_values[index]) for index in row.pop('source_evidence_indexes')]
            matches = feature_matches.get(json_text(row.get('candidate_id')), [])
            for item in row['evidence']:
                if item.pop('generated_history_template', False):
                    require(len(matches) == 1, 'GENERATED_HISTORY_FEATURE_AMBIGUOUS')
                    item['evidence_id'] = f"history-v1:{row['candidate_id']}:{item['category']}"
                    item['statement'] = _history_statement(row, matches[0], item['category'])
        inherited = row.pop('inherited_fixture_fields', [])
        for key in inherited:
            require(key not in row and key in fixtures.get(row.get('fixture_id'), {}),
                    'MISSING_OR_CONFLICTING_FIXTURE_JOIN')
            row[key] = deepcopy(fixtures[row['fixture_id']][key])
        result.append(row)
    return result


def _intern(rows, index, row):
    """Share an exact named component; never merge by a business identifier."""
    key = json_text(row)
    if key not in index:
        index[key] = len(rows)
        rows.append(deepcopy(row))
    return index[key]


def _dataset(value, *, family, location):
    """Only the documented snapshot shape has raw row/provenance backing refs."""
    if not (isinstance(value, dict) and isinstance(value.get('manifest'), dict)
            and isinstance(value.get('rows'), list) and 'lineage' in value):
        return deepcopy(value)
    result = deepcopy(value)
    result['rows'] = _reference(value['rows'], family=family, location=f'{location}/rows',
                                count=len(value['rows']))
    lineage = value['lineage']
    if isinstance(lineage, dict):
        result['lineage'] = _without_raw(lineage, {'input_partition', 'selected_record_origins'},
                                        family=family, location=f'{location}/lineage')
    elif isinstance(lineage, list):
        result['lineage'] = _reference(lineage, family=family, location=f'{location}/lineage',
                                      count=len(lineage))
    return result


def _history_statement(candidate, feature, category):
    """Exact existing history-v1 template, used only after full string equality."""
    window = {'historical': 'historical_20', 'venue_form': 'venue_10'}.get(category)
    require(window is not None, 'UNKNOWN_HISTORY_TEMPLATE')
    home, away = feature['profiles']['home'], feature['profiles']['away']
    h, a = home[window], away[window]
    recent = ''
    if category == 'venue_form':
        rh, ra = home['recent_5'], away['recent_5']
        recent = (f"Utolsó 5: hazai oldal {rh['market_hits']}/{rh['sample_size']}, "
                  f"vendég oldal {ra['market_hits']}/{ra['sample_size']}; "
                  'helyszíni/aktuális arány: 60/40. ')
    return (f"{candidate['period']} {candidate['market_key']} / {candidate['selection_key']}; "
            f"{window}: hazai oldal {h['market_hits']}/{h['sample_size']}, "
            f"vendég oldal {a['market_hits']}/{a['sample_size']}. {recent}"
            'Megfigyelt piaci mintázat, nem becsült nyerési esély.')


def _reference(value, *, family, location, count=None):
    return {'detail_level': 'RAW_BACKING_REFERENCE_NOT_HOST_RAW_READ', 'family': family,
            'location': location, 'canonical_value_sha256': digest(value),
            'row_count': count, 'physical_source_validation': 'NOT_PERFORMED'}


def _without_raw(value, fields, *, family, location):
    if not isinstance(value, dict):
        return deepcopy(value)
    return {key: (_reference(item, family=family, location=f'{location}/{key}',
                            count=len(item) if isinstance(item, (list, dict)) else None)
                  if key in fields else deepcopy(item)) for key, item in value.items()}


def _history_rows(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) and all(
        isinstance(row.get(key), str) and row[key]
        for key in ('fixture_id', 'home_team_id', 'away_team_id', 'kickoff')) for row in rows):
        return deepcopy(rows)
    dates, teams = Counter(), Counter()
    invalid = 0
    for row in rows:
        teams[row['home_team_id']] += 1
        teams[row['away_team_id']] += 1
        try:
            clock = datetime.fromisoformat(row['kickoff'])
            if clock.tzinfo is None or clock.utcoffset() is None:
                raise ValueError('NAIVE_HISTORY_CLOCK')
            dates[clock.astimezone(UTC).date().isoformat()] += 1
        except ValueError:
            invalid += 1
    return {'detail_level': 'COMPLETE_COHORT_COUNTS_NOT_INDIVIDUAL_MATCH_READ',
        'source_rows_sha256': digest(rows), 'row_count': len(rows),
        'utc_date_counts': dict(sorted(dates.items())), 'team_appearance_counts': dict(sorted(teams.items())),
        'invalid_or_naive_kickoff_count': invalid, 'historical_feature_availability_certified': False,
        'raw_rows_location': 'SHARED_SNAPSHOT_TOOL_BACKING', 'raw_rows_read_by_host': 'NOT_CLAIMED'}


def _history(value):
    if not isinstance(value, dict):
        return deepcopy(value)
    result = deepcopy(value)
    online = result.get('collected_online')
    if isinstance(online, dict) and 'records' in online:
        online['records'] = _history_rows(online['records'])
    consumed = result.get('consumed_model_input')
    if isinstance(consumed, dict):
        original = value['consumed_model_input']
        for key in ('history', 'online_history'):
            if key in consumed:
                consumed[key] = _history_rows(consumed[key])
        if 'dataset' in consumed:
            consumed['dataset'] = _dataset(original['dataset'], family='retained_history',
                                           location='consumed_model_input/dataset')
        if isinstance(original.get('selected_record_origins'), list):
            consumed['selected_record_origins'] = _reference(original['selected_record_origins'],
                family='retained_history', location='consumed_model_input/selected_record_origins',
                count=len(original['selected_record_origins']))
        if isinstance(original.get('fixtures'), list) and all(isinstance(row, dict) for row in original['fixtures']):
            consumed['fixtures'] = _calendar(original['fixtures'])
        for key in ('diagnostics', 'history_selection', 'selection'):
            if isinstance(original.get(key), dict):
                consumed[key] = _selection_membership(original[key], family='retained_history',
                                                       location=f'consumed_model_input/{key}')
    return result


def _selection_membership(value, *, family, location):
    if not isinstance(value, dict):
        return deepcopy(value)
    fields = {'isolated_neutral_ids', 'unknown_history_ids', 'quarantined_ids', 'unknown_target_ids',
              'selected_history_ids', 'excluded_target_ids'}
    return {key: (_reference(item, family=family, location=f'{location}/{key}', count=len(item))
                  if key in fields and isinstance(item, list) else deepcopy(item))
            for key, item in value.items()}


def _calendar(value):
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        return deepcopy(value)
    raw_fields = {'calendar_event_receipt', 'fixture_source', 'native_competition_route'}
    rows = [{key: deepcopy(item) for key, item in row.items() if key not in raw_fields} for row in value]
    return {'fixtures': encode_table(rows), 'raw_fields': sorted(raw_fields),
            'raw_fields_lookup': 'calendar original fixture_id + raw field name',
            'original_rows_sha256': digest(value), 'all_fixture_rows_preserved': True}


def _candidate_values(value, fixtures):
    if not isinstance(value, dict):
        return deepcopy(value)
    require(not {'candidate_ids', 'candidate_tables', 'candidate_feature_links', 'feature_values',
        'history_samples', 'history_windows', 'history_profiles', 'history_workloads',
        'sample_memberships', 'evidence_values', 'reconstruction'}.intersection(value),
        'RESERVED_ODDS_VIEW_FIELD')
    result, candidate_tables, feature_tables = {}, {}, {}
    names = ('feature_values', 'history_samples', 'history_windows', 'history_profiles',
             'history_workloads', 'sample_memberships', 'evidence_values')
    tables, indexes = {name: [] for name in names}, {name: {} for name in names}
    candidate_ids, candidate_index = [], {}
    base_rows, base_ids = [], {}
    fixture_fields = {'source_provider', 'provider_fixture_id', 'provider_league_id',
        'provider_season_id', 'provider_home_team_id', 'provider_away_team_id',
        'config_league_id', 'home_team', 'away_team', 'competition', 'competition_type',
        'kickoff', 'season', 'venue_name', 'venue_city', 'venue_country', 'neutral_venue',
        'source_id', 'event_status'}

    def share(name, row):
        return _intern(tables[name], indexes[name], row)

    def source_index(identifier):
        return _intern(candidate_ids, candidate_index, identifier)

    def feature_body(row):
        projected = {key: deepcopy(item) for key, item in row.items() if key != 'candidate_id'}
        profiles = projected.get('profiles')
        if isinstance(profiles, dict):
            for role, profile in profiles.items():
                if not isinstance(profile, dict):
                    continue
                require(not any(key.endswith('_view_ref') for key in profile), 'RESERVED_HISTORY_VIEW_FIELD')
                for window in ('historical_20', 'recent_5', 'venue_10'):
                    sample = profile.get(window)
                    if not isinstance(sample, dict):
                        continue
                    base = {key: deepcopy(item) for key, item in sample.items()
                            if key not in {'market_hits', 'descriptive_hit_rate'}}
                    for key in ('fixture_ids', 'source_ids', 'missing_period_scores'):
                        if key in sample:
                            membership = {'field': key, 'canonical_value_sha256': digest(sample[key]),
                                'row_count': len(sample[key]) if isinstance(sample[key], list) else None,
                                'detail_level': 'RAW_SAMPLE_MEMBERSHIP_IN_ORIGINAL_CANDIDATE_FEATURE',
                                'raw_rows_read_by_host': False}
                            base[key] = {'membership_ref': share('sample_memberships', membership)}
                    summary = {'sample_ref': share('history_samples', base),
                        **{key: deepcopy(sample[key]) for key in ('market_hits', 'descriptive_hit_rate')
                           if key in sample}}
                    profile[window] = {'window_view_ref': share('history_windows', summary)}
                if isinstance(profile.get('observed_workload'), dict):
                    profile['observed_workload'] = {
                        'workload_view_ref': share('history_workloads', profile['observed_workload'])}
                profiles[role] = {'profile_view_ref': share('history_profiles', profile)}
        return projected

    for producer in ('candidate_input', 'enriched', 'club_enriched'):
        container = value.get(producer)
        if not isinstance(container, dict):
            result[producer] = deepcopy(container)
            continue
        metadata = _without_raw(container, {'candidates', 'history_analysis', 'calendar_universe',
            'calendar_scope_receipt'}, family='odds_and_price_history', location=producer)
        analyses = container.get('history_analysis')
        analysis_by_id = {}
        if isinstance(analyses, dict) and isinstance(analyses.get('candidates'), list):
            links = []
            for feature in analyses['candidates']:
                if not isinstance(feature, dict):
                    links.append({'unrecognized_value': deepcopy(feature)})
                    continue
                identity = feature.get('candidate_id')
                analysis_by_id.setdefault(json_text(identity), []).append(feature)
                links.append({'source_candidate_index': source_index(identity),
                              'feature_ref': share('feature_values', feature_body(feature))})
            feature_tables[producer] = encode_table(links)
            metadata['history_analysis_metadata'] = _without_raw(analyses, {'candidates', 'sources'},
                family='odds_and_price_history', location=f'{producer}/history_analysis')
        source_rows = container.get('candidates')
        if isinstance(source_rows, list) and all(isinstance(row, dict) for row in source_rows):
            rows = []
            for original in source_rows:
                require(not {'inherited_fixture_fields', 'source_candidate_index',
                             'source_evidence_indexes'}.intersection(original), 'RESERVED_CANDIDATE_VIEW_FIELD')
                fixture = fixtures.get(original.get('fixture_id'), {})
                inherited = [key for key in fixture_fields if key in original and key in fixture
                             and json_text(original[key]) == json_text(fixture[key])]
                row = {key: deepcopy(item) for key, item in original.items() if key not in inherited}
                row['inherited_fixture_fields'] = sorted(inherited)
                if 'candidate_id' in row:
                    row['source_candidate_index'] = source_index(row.pop('candidate_id'))
                evidence = row.get('evidence')
                if isinstance(evidence, list) and all(isinstance(item, dict) for item in evidence):
                    evidence_refs = []
                    matches = analysis_by_id.get(json_text(original.get('candidate_id')), [])
                    for evidence_item in evidence:
                        require('generated_history_template' not in evidence_item, 'RESERVED_EVIDENCE_VIEW_FIELD')
                        item = deepcopy(evidence_item)
                        category = item.get('category')
                        expected_id = f"history-v1:{original.get('candidate_id')}:{category}"
                        if (isinstance(original.get('candidate_id'), str) and original['candidate_id']
                                and len(matches) == 1 and category in {'historical', 'venue_form'}
                                and item.get('evidence_id') == expected_id):
                            try:
                                expected_statement = _history_statement(original, matches[0], category)
                            except (KeyError, TypeError, ValueError):
                                expected_statement = None
                            if expected_statement is not None and item.get('statement') == expected_statement:
                                item.pop('statement')
                                item.pop('evidence_id')
                                item['generated_history_template'] = True
                        evidence_refs.append(share('evidence_values', item))
                    row.pop('evidence')
                    row['source_evidence_indexes'] = evidence_refs
                rows.append(row)
            if producer == 'candidate_input':
                base_rows = rows
                for index, row in enumerate(rows):
                    base_ids.setdefault(row.get('source_candidate_index'), []).append(index)
                candidate_tables[producer] = encode_table(rows)
            elif base_rows:
                membership, changes, removed = [], {}, {}
                for index, row in enumerate(rows):
                    matches = base_ids.get(row.get('source_candidate_index'), [])
                    base_index = matches[0] if len(matches) == 1 else None
                    membership.append(base_index)
                    base = base_rows[base_index] if base_index is not None else {}
                    for key, item in row.items():
                        if key not in base or json_text(item) != json_text(base[key]):
                            column = changes.setdefault(key, {'field': key, 'row_indexes': [], 'values': []})
                            column['row_indexes'].append(index)
                            column['values'].append(deepcopy(item))
                    for key in base:
                        if key not in row:
                            removed.setdefault(key, []).append(index)
                candidate_tables[producer] = {'format': 'SPARSE_PRODUCER_DIFFERENCES_V3',
                    'base_producer': 'candidate_input', 'base_row_indexes': membership,
                    'changes': list(changes.values()), 'removed_fields': removed,
                    'direct_rows_sha256': digest(rows)}
            else:
                candidate_tables[producer] = encode_table(rows)
        result[producer] = metadata
    for key, item in value.items():
        if key not in result:
            result[key] = (_reference(item, family='odds_and_price_history', location=key,
                                     count=len(item) if isinstance(item, (dict, list)) else None)
                           if key in {'odds_bulk', 'historical_runs'} else deepcopy(item))
    result.update(candidate_ids=candidate_ids, candidate_tables=candidate_tables,
        candidate_feature_links=feature_tables, **{name: encode_table(rows) for name, rows in tables.items()},
        reconstruction={'candidate_ids': 'source_candidate_index addresses the complete original-ID dictionary; external IDs never change.',
            'fixture_join': 'Add inherited_fixture_fields from the exact fixture_id. Quote overrides win only when present in that producer.',
            'producer_variants': 'Ordered base_row_indexes plus field-specific changes preserve every producer row, duplicate, conflict and missing field.',
            'history': 'feature_ref -> feature_values; profile_view_ref -> history_profiles; window_view_ref -> history_windows; sample_ref -> history_samples; workload_view_ref -> history_workloads. Each is an exact named row.',
            'sample_membership': 'membership_ref identifies the complete raw-list hash/count; individual raw rows are not claimed read.',
            'evidence': 'source_evidence_indexes address evidence_values. generated_history_template means the exactly verified existing history-v1 explanatory template, using this candidate and its explicit home/away window counts. Other statements remain literal.',
            'descriptive_rates_are_forecast_probability': False})
    return result


def decode_candidate_features(odds_view, producer):
    """Expand named history components for semantic tests and owner evidence text."""
    links = odds_view.get('candidate_feature_links', {}).get(producer)
    if links is None:
        return []
    features = decode_table(odds_view['feature_values'])
    profiles = decode_table(odds_view['history_profiles'])
    windows = decode_table(odds_view['history_windows'])
    samples = decode_table(odds_view['history_samples'])
    workloads = decode_table(odds_view['history_workloads'])
    memberships = decode_table(odds_view['sample_memberships'])
    expanded = []
    for link in decode_table(links):
        if 'unrecognized_value' in link:
            expanded.append(deepcopy(link['unrecognized_value']))
            continue
        row = deepcopy(features[link['feature_ref']])
        row['candidate_id'] = deepcopy(odds_view['candidate_ids'][link['source_candidate_index']])
        source_profiles = row.get('profiles')
        if isinstance(source_profiles, dict):
            for role, value in source_profiles.items():
                if not isinstance(value, dict) or set(value) != {'profile_view_ref'}:
                    continue
                profile = deepcopy(profiles[value['profile_view_ref']])
                for window in ('historical_20', 'recent_5', 'venue_10'):
                    value = profile.get(window)
                    if not isinstance(value, dict) or set(value) != {'window_view_ref'}:
                        continue
                    metrics = deepcopy(windows[value['window_view_ref']])
                    sample = deepcopy(samples[metrics.pop('sample_ref')])
                    for key in ('fixture_ids', 'source_ids', 'missing_period_scores'):
                        value = sample.get(key)
                        if isinstance(value, dict) and set(value) == {'membership_ref'}:
                            sample[key] = deepcopy(memberships[value['membership_ref']])
                    sample.update(metrics)
                    profile[window] = sample
                workload = profile.get('observed_workload')
                if isinstance(workload, dict) and set(workload) == {'workload_view_ref'}:
                    profile['observed_workload'] = deepcopy(workloads[workload['workload_view_ref']])
                source_profiles[role] = profile
        expanded.append(row)
    return expanded

def _context(family, value):
    if not isinstance(value, dict):
        return deepcopy(value)
    # Facts remain direct; repeated collection/proof bodies have explicit backing.
    result = _without_raw(value, {'collection', 'player_collection', 'target_proofs',
        'previous_venue_collection'}, family=family, location=family)
    for key in ('categories', 'fixtures'):
        if isinstance(result.get(key), list) and all(isinstance(row, dict) for row in result[key]):
            result[key] = encode_table(result[key])
    for key in ('by_fixture', 'categories'):
        if isinstance(result.get(key), dict):
            if all(isinstance(row, dict) and 'map_fixture_id' not in row for row in result[key].values()):
                result[key] = {'format': 'FIXTURE_VALUE_COLUMNS_V3',
                    'table': encode_table([{'map_fixture_id': fixture_id, **deepcopy(row)}
                                           for fixture_id, row in result[key].items()])}
            elif all(isinstance(row, list) and all(isinstance(item, dict) for item in row)
                     for row in result[key].values()):
                values, index, links = [], {}, []
                for fixture_id, rows in result[key].items():
                    links.append({'fixture_id': fixture_id,
                                  'category_rows': [_intern(values, index, item) for item in rows]})
                result[key] = {'format': 'FIXTURE_CATEGORY_ROWS_V3', 'values': encode_table(values),
                               'links': encode_table(links)}
            else:
                result[key] = encode_table([{'fixture_id': fixture_id, 'value': row}
                                           for fixture_id, row in result[key].items()])
    for key in ('schedule', 'rotation'):
        source_view = result.get(key)
        if isinstance(source_view, dict):
            source_view = _without_raw(source_view, {'dataset'}, family=family, location=key)
            if isinstance(source_view.get('features'), list) and all(
                    isinstance(row, dict) for row in source_view['features']):
                rows = []
                for index, row in enumerate(source_view['features']):
                    row = deepcopy(row)
                    if 'dataset' in row:
                        row['dataset'] = _dataset(row['dataset'], family=family,
                                                  location=f'{key}/features/{index}/dataset')
                    rows.append(row)
                source_view['features'] = encode_table(rows)
            result[key] = source_view
    return result


def decode_context(value, *, source_shapes=None):
    """Expand context tables; legacy category wrappers need an original-shape hint.

    ``source_shapes`` is readback metadata from the original producer, never a
    decision-view change. Raw dataset/provenance refs remain explicit refs.
    """
    result = deepcopy(value)
    if not isinstance(result, dict):
        return result
    shapes = {} if source_shapes is None else source_shapes
    require(isinstance(shapes, dict) and all(item in {'list', 'mapping'} for item in shapes.values()),
            'INVALID_CONTEXT_SOURCE_SHAPE_HINT')
    for key in ('by_fixture', 'categories'):
        item = result.get(key)
        if not isinstance(item, dict):
            continue
        if item.get('format') == 'FIXTURE_VALUE_COLUMNS_V3':
            result[key] = {row['map_fixture_id']: {name: value for name, value in row.items()
                           if name != 'map_fixture_id'} for row in decode_table(item['table'])}
        elif item.get('format') == 'FIXTURE_CATEGORY_ROWS_V3':
            rows = decode_table(item['values'])
            result[key] = {link['fixture_id']: [deepcopy(rows[index]) for index in link['category_rows']]
                           for link in decode_table(item['links'])}
        elif item.get('format') == 'NAMED_COLUMNS_V1':
            rows = decode_table(item)
            wrappers = bool(rows) and all(set(row) == {'fixture_id', 'value'} for row in rows)
            hint = shapes.get(key)
            if wrappers or hint == 'mapping':
                if key == 'categories':
                    require(hint is not None, 'AMBIGUOUS_CONTEXT_SHAPE_REQUIRES_SOURCE_HINT')
                require((not rows or wrappers) and all(isinstance(row['fixture_id'], str) for row in rows)
                        and len({row['fixture_id'] for row in rows}) == len(rows),
                        'CONTEXT_MAPPING_WRAPPER_REQUIRED')
                mapping = {row['fixture_id']: row['value'] for row in rows}
                if hint == 'list':
                    # The unchanged encoder first encodes categories lists, then
                    # passes that table through its dictionary-wrapper branch.
                    require(mapping.get('format') == 'NAMED_COLUMNS_V1', 'CONTEXT_NESTED_LIST_TABLE_REQUIRED')
                    result[key] = decode_table(mapping)
                else:
                    result[key] = mapping
            else:
                result[key] = rows
    return result


def _source_candidate_id(leg, owner_id):
    explicit = [leg[key] for key in ('source_candidate_id', 'quote_candidate_id')
                if key in leg and leg[key] is not None]
    require(all(isinstance(value, str) and value for value in explicit)
            and len(set(explicit)) <= 1, 'CONFLICTING_OR_INVALID_SOURCE_CANDIDATE_ALIASES')
    source = explicit[0] if explicit else leg.get('candidate_id')
    require(isinstance(source, str) and source, 'SOURCE_CANDIDATE_ID_REQUIRED')
    display = leg.get('candidate_id')
    require(display == source or display == owner_id + '-' + digest([owner_id, source])[:20],
            'UNRECOGNIZED_OWNER_CANDIDATE_ALIAS')
    return source


def final_option_details(snapshot: SharedSnapshot, owner_id: str, options: list[dict], *,
                         source_access: bool = False) -> dict:
    """Readable facts for this owner's already proposed options, without reselection.

    PLAN used the entire day. This expansion is never a shared candidate filter.
    Its payload still passes the runner's unchanged FINAL character limit.
    """
    require(type(source_access) is bool, 'SOURCE_ACCESS_FLAG_MUST_BE_BOOLEAN')
    original = snapshot.data()
    require(owner_id in original['owner_ids'] and isinstance(options, list)
            and all(isinstance(option, dict) and option.get('agent_id') == owner_id
                    and isinstance(option.get('legs'), list) for option in options),
            'ONLY_THIS_OWNERS_PROPOSED_OPTIONS')
    candidate_ids, fixture_ids, source_pairs = set(), set(), set()
    for option in options:
        from goal_analysis.ticket_product_policy import validate_option_product

        validate_option_product(option)
        for leg in option['legs']:
            require(isinstance(leg, dict) and isinstance(leg.get('candidate_id'), str)
                    and leg.get('fixture_id') in original['fixture_ids'], 'SOURCE_BOUND_OPTION_LEG_REQUIRED')
            source_id = _source_candidate_id(leg, owner_id)
            candidate_ids.add(source_id)
            fixture_ids.add(leg['fixture_id'])
            source_pairs.add((source_id, leg['fixture_id']))
    odds = original['families']['odds_and_price_history']['value']
    odds = odds if isinstance(odds, dict) else {}
    candidates, history = {}, {}
    for producer in ('candidate_input', 'enriched', 'club_enriched'):
        container = odds.get(producer)
        if not isinstance(container, dict):
            continue
        source_rows = container.get('candidates')
        candidates[producer] = [deepcopy(row) for row in (source_rows if isinstance(source_rows, list) else [])
            if isinstance(row, dict) and row.get('candidate_id') in candidate_ids]
        analyses = container.get('history_analysis')
        if isinstance(analyses, dict) and isinstance(analyses.get('candidates'), list):
            rows = []
            for source_row in analyses['candidates']:
                if not isinstance(source_row, dict) or source_row.get('candidate_id') not in candidate_ids:
                    continue
                row = deepcopy(source_row)
                profiles = row.get('profiles')
                for role, profile in (profiles.items() if isinstance(profiles, dict) else []):
                    if not isinstance(profile, dict):
                        continue
                    for window in ('historical_20', 'recent_5', 'venue_10'):
                        if window in profile:
                            profile[window] = _without_raw(profile[window],
                                {'fixture_ids', 'source_ids', 'missing_period_scores'},
                                family='odds_and_price_history',
                                location=f'{producer}/{row["candidate_id"]}/{role}/{window}')
                rows.append(row)
            history[producer] = rows
    found = {row['candidate_id'] for rows in candidates.values() for row in rows}
    require(found == candidate_ids, 'OWN_OPTION_SOURCE_CANDIDATE_NOT_FOUND')
    require(all((row['candidate_id'], row.get('fixture_id')) in source_pairs
                for rows in candidates.values() for row in rows), 'OWN_OPTION_FIXTURE_IDENTITY_MISMATCH')
    contexts = {}
    for family in ('team_stats_xg', 'players_squad_xi', 'schedule_venue_pitch', 'weather'):
        envelope = original['families'][family]
        value = envelope['value']
        selected = {}
        if isinstance(value, dict):
            for key in ('categories', 'by_fixture'):
                if isinstance(value.get(key), dict):
                    selected[key] = {fixture: deepcopy(row) for fixture, row in value[key].items()
                                     if fixture in fixture_ids}
            if isinstance(value.get('fixtures'), list):
                selected['fixtures'] = [deepcopy(row) for row in value['fixtures']
                    if isinstance(row, dict) and row.get('fixture_id') in fixture_ids]
        contexts[family] = {'status': envelope['status'], 'reason': envelope.get('reason'),
            'source_refs': deepcopy(envelope['source_refs']), 'original_value_sha256': envelope['value_sha256'],
            'selected_fixture_values': selected}
    result = {'version': 'owner-selected-readable-details-v1', 'owner_id': owner_id,
        'snapshot_id': snapshot.snapshot_id, 'option_ids': [row['option_id'] for row in options],
        'candidate_ids': sorted(candidate_ids), 'fixture_ids': sorted(fixture_ids),
        'selection_basis': 'THIS_OWNER_ALREADY_PROPOSED_OPTIONS_AFTER_FULL_CALENDAR_PLAN',
        'source_candidates_by_producer': candidates, 'history_features_by_producer': history,
        'fixture_context': contexts, 'all_raw_rows_read_by_host': False,
        'model_probabilities': {key: deepcopy(row) for key, row in original['model_probabilities'].items()
                                if row['fixture_id'] in fixture_ids},
        'new_probability_or_price_created': False}

    if source_access:
        def row_reference(row, path):
            return {'format': SOURCE_DETAILS_POLICY, 'candidate_id': row['candidate_id'],
                **({'fixture_id': deepcopy(row['fixture_id'])} if 'fixture_id' in row else {}),
                'namespace': 'raw', 'path': path,
                'source_value_sha256': digest(row), 'total_chars': len(json_text(row))}

        selected_candidates, selected_history = {}, {}
        for producer in candidates:
            container = odds[producer]
            path = ['families', 'odds_and_price_history', 'value', producer]
            rows = container.get('candidates')
            selected_candidates[producer] = [row_reference(row, [*path, 'candidates', index])
                for index, row in enumerate(rows if isinstance(rows, list) else [])
                if isinstance(row, dict) and row.get('candidate_id') in candidate_ids]
            if producer in history:
                rows = container['history_analysis']['candidates']
                selected_history[producer] = [row_reference(row, [*path, 'history_analysis', 'candidates', index])
                    for index, row in enumerate(rows)
                    if isinstance(row, dict) and row.get('candidate_id') in candidate_ids]
        result['source_candidates_by_producer'] = selected_candidates
        result['history_features_by_producer'] = selected_history
        result['source_details_policy'] = {
            'version': SOURCE_DETAILS_POLICY,
            'instructions': 'These two producer groups contain exact original raw-row locators, not inline row values. Use read_data with this namespace/path, your current agent/session/snapshot/phase identity, mode=text, offset=0 and bounded limit; follow next_offset until complete. Verify source_value_sha256 and total_chars. Original UNKNOWN values, clocks and full historical membership remain in the immutable raw bundle. Read any detail you need before relying on it. A locator is availability, not proof that the row was read or influenced your choice.',
            'raw_rows_preserved_in_shared_bundle': True, 'all_raw_rows_read_by_host': False}
    return result


def final_shared_reference(snapshot: SharedSnapshot, plan_view: dict) -> dict:
    """FINAL reuses its own full PLAN by hash; this does not claim a raw reread."""
    original = snapshot.data()
    require(plan_view.get('view', {}).get('source_snapshot_id') == snapshot.snapshot_id
            and plan_view.get('fixture_ids') == original['fixture_ids']
            and plan_view.get('owner_ids') == original['owner_ids'], 'FOREIGN_PLAN_VIEW')
    require(all(plan_view['families'][name]['value_sha256'] == envelope['value_sha256']
                for name, envelope in original['families'].items()), 'PLAN_FAMILY_BINDING_CHANGED')
    return {**{key: deepcopy(original[key]) for key in (
                'version', 'run_id', 'target_date', 'as_of', 'owner_ids', 'fixture_ids')},
        'view': {'version': 'owner-final-shared-reference-v1', 'phase': 'FINAL',
            'source_snapshot_id': snapshot.snapshot_id, 'plan_view_sha256': digest(plan_view),
            'scope': 'THIS_OWNER_PREVIOUS_FULL_CALENDAR_PLAN_PLUS_OWN_SELECTED_READABLE_DETAILS',
            'full_inventory_was_supplied_in': 'PLAN', 'new_candidate_selection_allowed': False,
            'full_raw_values_read_by_host': 'NOT_CLAIMED'},
        'families': {name: {key: deepcopy(item) for key, item in envelope.items() if key != 'value'}
                     for name, envelope in original['families'].items()}}


def final_request_data(data):
    """Lossless FINAL-wire duplicates only; original evidence is never rewritten."""
    require(isinstance(data, dict), 'FINAL_DATA_OBJECT_REQUIRED')
    reference_key = '__arthur_final_ref__'

    def reserved(value):
        if isinstance(value, dict):
            return reference_key in value or any(reserved(row) for row in value.values())
        return isinstance(value, list) and any(reserved(row) for row in value)

    if {'exact_values', 'final_data_projection'}.intersection(data) or reserved(data):
        return deepcopy(data)  # Original cap still applies; no ambiguous decoding.

    def untouched(path):
        if path and path[0] in {'snapshot', 'own_plan', 'own_option_admission'}:
            return True
        return (len(path) >= 3 and path[0] == 'tool_results'
                and (path[2] == 'request' or len(path) >= 4
                     and path[2] == 'result' and path[3] not in {'value', 'diagnostics'}))

    def protected(path):
        return (len(path) <= 1 or path[0] == 'validated_own_options' and len(path) == 2
                or path[0] == 'tool_results' and len(path) <= 3)

    counts = Counter()

    def scan(value, path):
        if not isinstance(value, (dict, list)) or untouched(path):
            return
        if not protected(path):
            raw = json_text(value)
            if len(raw) >= 256:
                counts[raw] += 1
        for key, row in (value.items() if isinstance(value, dict) else enumerate(value)):
            scan(row, (*path, key))

    scan(data, ())
    values, originals = {}, {}

    def project(value, path, inline=False):
        if not isinstance(value, (dict, list)) or untouched(path):
            return deepcopy(value)
        raw = json_text(value)
        if not inline and not protected(path) and counts[raw] > 1:
            key = digest(value)
            require(key not in originals or originals[key] == raw, 'EXACT_SUBTREE_HASH_COLLISION')
            if key not in values:
                originals[key] = raw
                values[key] = {'source_value_sha256': key, 'value': project(value, path, True)}
            return {reference_key: key}
        if isinstance(value, dict):
            return {key: project(row, (*path, key)) for key, row in value.items()}
        return [project(row, (*path, index)) for index, row in enumerate(value)]

    result = project(data, ())
    if not values:
        return deepcopy(data)
    result['exact_values'] = values
    result['final_data_projection'] = {
        'version': FINAL_REQUEST_PROJECTION, 'source_data_sha256': digest(data),
        'reconstruction': 'In the six original data fields, recursively replace exact one-key __arthur_final_ref__ objects with exact_values[key].value, expanding nested references first. Verify each restored value SHA256 against its table key and source_value_sha256; remove exact_values and final_data_projection to recover the original data and verify source_data_sha256. All values are supplied in this same request; order, UNKNOWN, clocks and numbers are unchanged. This is not extra source-read or profit evidence.',
        'original_evidence_and_tools_unchanged': True}
    return result if len(json_text(result)) < len(json_text(data)) else deepcopy(data)


def public_tool_results(tool_results):
    """Bound raw manifests and losslessly reuse exact own profiles and source legs."""
    public = deepcopy(tool_results)
    for original, projected in zip(tool_results, public, strict=True):
        result = projected['result']
        replacements = []
        for location in ('value', 'diagnostics'):
            container = result.get(location)
            if not isinstance(container, dict) or 'sample_manifests' not in container:
                continue
            manifests = container['sample_manifests']
            if not isinstance(manifests, (dict, list)):
                continue
            reference = {'detail_level': 'RAW_SAMPLE_MANIFESTS_IN_OWN_FULL_TOOL_RESULT_JOURNAL',
                'canonical_value_sha256': digest(manifests), 'entry_count': len(manifests),
                'raw_manifest_rows_read_by_host': False}
            if isinstance(manifests, dict):
                reference['manifest_rows'] = {key: len(rows) if isinstance(rows, list) else None
                                              for key, rows in manifests.items()}
            container['sample_manifests'] = reference
            replacements.append(f'{location}/sample_manifests')
        value = result.get('value')
        if result.get('role') == 'candidate_scenarios' and isinstance(value, dict):
            by_option, producer = value.get('by_option'), value.get('producer_result')
            profiles = producer.get('profiles') if isinstance(producer, dict) else None
            if (isinstance(by_option, dict) and by_option and isinstance(profiles, list)
                    and profiles and all(isinstance(row, dict) for row in profiles)):
                canonical_options = {json_text(row): key for key, row in by_option.items()
                                     if isinstance(key, str) and isinstance(row, dict)}
                references = [canonical_options.get(json_text(row)) for row in profiles]
                if all(key is not None for key in references):
                    producer['profiles'] = {
                        'encoding': 'EXACT_BY_OPTION_PROFILE_REFERENCES_V1',
                        'ordered_option_ids': references,
                        'source_profiles_sha256': digest(profiles),
                        'source_profile_count': len(profiles),
                        'reconstruction': 'Replace this object with [result.value.by_option[id] for each id in ordered_option_ids]. Preserve order and duplicate references. These are exact full values already supplied in this same result, not omitted raw evidence.',
                    }
                    replacements.append('value/producer_result/profiles')
            # Only these two producer schemas contain copies of full source legs.
            # The table is local to this result; no value is fetched or omitted.
            if 'exact_leg_values' not in value:
                bindings = []

                def add_bindings(container, path, bindings=bindings):
                    rows = container.get('leg_bindings') if isinstance(container, dict) else None
                    if isinstance(rows, list):
                        for index, row in enumerate(rows):
                            if isinstance(row, dict) and isinstance(row.get('leg'), dict):
                                bindings.append((row, f'{path}/leg_bindings/{index}/leg'))

                for option_id, profile in (by_option.items() if isinstance(by_option, dict) else []):
                    models = profile.get('models') if isinstance(profile, dict) else None
                    for model, model_result in (models.items() if isinstance(models, dict) else []):
                        add_bindings(model_result, f'value/by_option/{option_id}/models/{model}')
                kernel = producer.get('existing_kernel_result') if isinstance(producer, dict) else None
                models = kernel.get('models') if isinstance(kernel, dict) else None
                for model, model_result in (models.items() if isinstance(models, dict) else []):
                    cards = model_result.get('cards') if isinstance(model_result, dict) else None
                    for index, card in enumerate(cards if isinstance(cards, list) else []):
                        add_bindings(card, f'value/producer_result/existing_kernel_result/models/{model}/cards/{index}')
                # A producer-owned reserved marker must never be decoded as ours.
                if any(binding['leg'].get('encoding') == 'EXACT_SCENARIO_LEG_REFERENCE_V1'
                       for binding, _ in bindings):
                    bindings = []
                groups = {}
                for binding, path in bindings:
                    groups.setdefault(json_text(binding['leg']), []).append((binding, path))
                entries = {}
                for rows in groups.values():
                    if len(rows) < 2:
                        continue
                    leg = rows[0][0]['leg']
                    key = f'leg-{len(entries)}'
                    entries[key] = deepcopy(leg)
                    reference = {'encoding': 'EXACT_SCENARIO_LEG_REFERENCE_V1',
                        'table_key': key, 'source_leg_sha256': digest(leg)}
                    for binding, path in rows:
                        binding['leg'] = deepcopy(reference)
                        replacements.append(path)
                if entries:
                    value['exact_leg_values'] = {'encoding': 'EXACT_SCENARIO_LEG_VALUES_V1',
                        'values': entries,
                        'reconstruction': 'Within this result only, replace each EXACT_SCENARIO_LEG_REFERENCE_V1 at the two declared leg_bindings paths with a detached copy of values[table_key], verifying source_leg_sha256. Preserve every binding and list position. Restore these legs before expanding EXACT_BY_OPTION_PROFILE_REFERENCES_V1; then remove exact_leg_values. Full leg values are supplied here unchanged, including UNKNOWN and clocks.'}
        if replacements:
            result['prompt_projection'] = {'original_result_sha256': digest(original['result']),
                'replaced_paths': replacements, 'original_result_validation_unchanged': True,
                'result_hash_fields_scope': 'ORIGINAL_FULL_RESULT_NOT_THIS_PROMPT_PROJECTION',
                'raw_manifest_rows_read_by_host': False}
    return public


_MODEL_MARKETS = {'home': ('FULL_TIME:h2h', 'home'), 'draw': ('FULL_TIME:h2h', 'draw'),
    'away': ('FULL_TIME:h2h', 'away'), 'over25': ('FULL_TIME:totals_2_5', 'over'),
    'under25': ('FULL_TIME:totals_2_5', 'under'), 'btts_yes': ('FULL_TIME:btts', 'yes')}


def _models(value, probabilities):
    if not isinstance(value, dict):
        return deepcopy(value)
    result = _without_raw(value, {'model_input_uses'}, family='numeric_models', location='numeric_models')
    models = value.get('results')
    if not isinstance(models, dict):
        return result
    require(not {'named_model_tables', 'model_reconstruction'}.intersection(value), 'RESERVED_MODEL_VIEW_FIELD')
    components = ('training_selection', 'window_training_counts', 'library_versions', 'variants',
                  'fits', 'ensemble', 'calibration', 'windows', 'validation', 'classifier', 'rating_model')
    names = ('predictions', 'details', 'diagnostics', 'training_manifests', 'training_uses', *components)
    tables, indexes = {name: [] for name in names}, {name: {} for name in names}
    probability_index = {(row['model_id'], row['fixture_id'], row['market'], row['outcome']): (key, row)
                         for key, row in probabilities.items()}

    def share(name, row):
        return _intern(tables[name], indexes[name], row)

    def detail(value):
        if not (isinstance(value, dict) and isinstance(value.get('version'), str)
                and ('training_count' in value or 'history_count' in value) and 'cutoff' in value):
            return deepcopy(value)
        data = deepcopy(value)
        require('named_component_refs' not in data, 'RESERVED_MODEL_DETAIL_FIELD')
        refs = {}
        for name in components:
            item = data.get(name)
            if isinstance(item, (dict, list)):
                if name == 'training_selection' and isinstance(item, dict):
                    item = {key: _selection_membership(row, family='numeric_models',
                            location=f'model_details/training_selection/{key}')
                            for key, row in item.items()}
                refs[name] = share(name, {'value': item})
                data.pop(name)
        data['named_component_refs'] = refs
        return {'model_details_ref': share('details', data)}

    def block(row, model):
        if not isinstance(row, dict):
            return deepcopy(row)
        data = deepcopy(row)
        predictions = data.get('predictions')
        if isinstance(predictions, list) and all(isinstance(item, dict) for item in predictions):
            refs = []
            for prediction in predictions:
                item = deepcopy(prediction)
                values, probability_refs = item.get('probabilities'), {}
                if isinstance(values, dict):
                    for name, number in values.items():
                        market, outcome = _MODEL_MARKETS.get(name, (None, None))
                        record = probability_index.get((model, item.get('fixture_id'), market, outcome))
                        if record is not None and json_text(record[1]['value']) == json_text(number):
                            probability_refs[name] = record[0]
                    if set(probability_refs) == set(values):
                        item.pop('probabilities')
                        item.update(probability_refs=probability_refs,
                                    source_prediction_sha256=digest(prediction),
                                    probability_view_path='model_probabilities')
                if 'model_details' in item:
                    item['model_details'] = detail(item['model_details'])
                refs.append(share('predictions', item))
            data['predictions'] = {'shared_table': 'predictions', 'ordered_rows': refs,
                                   'source_rows_sha256': digest(predictions)}
        diagnostics = data.get('diagnostics')
        if isinstance(diagnostics, list) and all(isinstance(item, dict) for item in diagnostics):
            data['diagnostics'] = {'shared_table': 'diagnostics',
                'ordered_rows': [share('diagnostics', item) for item in diagnostics],
                'source_rows_sha256': digest(diagnostics)}
        training = data.get('training_datasets')
        if isinstance(training, dict) and isinstance(training.get('manifests'), list) and isinstance(training.get('uses'), list):
            for field, name in (('manifests', 'training_manifests'), ('uses', 'training_uses')):
                source = training[field]
                if all(isinstance(item, dict) for item in source):
                    training[field] = {'shared_table': name,
                        'ordered_rows': [share(name, item) for item in source], 'source_rows_sha256': digest(source)}
        if isinstance(row.get('competition_results'), dict):
            data['competition_results'] = {key: block(child, model) for key, child in row['competition_results'].items()}
        return data

    result['results'] = {model: block(row, model) for model, row in models.items()}
    result['named_model_tables'] = {name: encode_table(rows) for name, rows in tables.items()}
    result['model_reconstruction'] = {
        'ordered_rows': 'Each row index addresses shared_table in named_model_tables; order and duplicate references are significant.',
        'model_details_ref': 'Row in details; named_component_refs names the exact component table and row. All other fields are literal.',
        'dataset_conflicts': 'Same dataset_id with different canonical manifests has separate rows; never merged by dataset_id.',
        'probability_admission': 'Raw predictions retain original values. Only the separate model_probabilities index is admitted for scenario use.',
        'raw_selection_membership': 'Explicit original full hash/count; individual raw membership rows were not read by the host.'}
    return result


def decode_model_results(value):
    """Expand exact named model components, leaving declared raw backing refs explicit."""
    if not isinstance(value, dict) or 'named_model_tables' not in value:
        return deepcopy(value.get('results') if isinstance(value, dict) else value)
    tables = {name: decode_table(table) for name, table in value['named_model_tables'].items()}

    def rows(reference):
        return [deepcopy(tables[reference['shared_table']][index]) for index in reference['ordered_rows']]

    def block(source):
        if not isinstance(source, dict):
            return deepcopy(source)
        data = deepcopy(source)
        for field in ('predictions', 'diagnostics'):
            if isinstance(data.get(field), dict) and 'shared_table' in data[field]:
                data[field] = rows(data[field])
        predictions = data.get('predictions')
        for prediction in predictions if isinstance(predictions, list) else []:
            if not isinstance(prediction, dict):
                continue
            detail = prediction.get('model_details')
            if isinstance(detail, dict) and set(detail) == {'model_details_ref'}:
                detail = deepcopy(tables['details'][detail['model_details_ref']])
                for name, index in detail.pop('named_component_refs').items():
                    detail[name] = deepcopy(tables[name][index]['value'])
                prediction['model_details'] = detail
        training = data.get('training_datasets')
        if isinstance(training, dict):
            for field in ('manifests', 'uses'):
                if isinstance(training.get(field), dict) and 'shared_table' in training[field]:
                    training[field] = rows(training[field])
        if isinstance(data.get('competition_results'), dict):
            data['competition_results'] = {name: block(child) for name, child in data['competition_results'].items()}
        return data

    return {model: block(row) for model, row in value['results'].items()}

def _prior_run(value):
    if not isinstance(value, dict) or not isinstance(value.get('run_id'), str):
        return deepcopy(value)
    result = {key: deepcopy(value[key]) for key in (
        'run_id', 'date', 'observed_at', 'finished_at', 'construction_status', 'report_sha256') if key in value}
    result.update(detail_level='PRIOR_REPORT_SUMMARY_NOT_FULL_REPORT_READ', original_value_sha256=digest(value))
    for group in ('tickets', 'recommendations', 'generated_tickets'):
        rows = value.get(group)
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            result[group] = [{key: deepcopy(row[key]) for key in (
                'ticket_id', 'recommendation_id', 'profile_id', 'date', 'status', 'outcome', 'played',
                'stake_eur', 'settled_stake_eur', 'payout_eur', 'net_eur', 'cash_flow_eur',
                'bank_cash_receipt_eur', 'after_cost_system_net_eur', 'currency') if key in row} for row in rows]
    result['unknown_costs_defaulted_to_zero'] = False
    return result


def decision_view(snapshot: SharedSnapshot) -> dict:
    original = snapshot.data()
    fixture_rows = original['families']['calendar']['value']
    fixtures = {row['fixture_id']: row for row in fixture_rows}
    result = {key: deepcopy(value) for key, value in original.items()
              if key not in {'families', 'source_manifest', 'model_probabilities', 'calendar_receipt'}}
    result['view'] = {'version': VIEW_VERSION, 'source_snapshot_id': snapshot.snapshot_id,
        'format_instructions': 'Each NAMED_COLUMNS_V1 row follows named columns. Literal columns contain exact values; lookup columns contain zero-based indexes into lookups[column name]. Defaults apply except missing_rows. Candidate IDs and fixture IDs are original identifiers. source_candidate_index addresses the full candidate_ids dictionary. All named feature/model row references and sparse producer differences are described in the corresponding reconstruction object. Each table is semantic JSON, not executable or binary. All fixture and candidate rows are present; no common shortlist.',
        'raw_values_location': 'UNCHANGED_SHARED_SNAPSHOT_BACKING',
        'raw_values_read_by_host': 'NOT_CLAIMED', 'all_raw_sources_consumer_verified': False,
        'fixture_or_candidate_shortlist_applied': False, 'original_numeric_values_changed': False,
        'source_hashes_are_physical_attestation': False}
    result['calendar_receipt'] = _without_raw(original['calendar_receipt'], {'producer_receipt'},
        family='calendar', location='calendar_receipt')
    manifest_rows = [({'source_ref': key, **{field: deepcopy(value) for field, value in row.items()
        if field != 'producer_value'}} if isinstance(row, dict)
        else {'source_ref': key, 'unrecognized_source_value': deepcopy(row)})
        for key, row in original['source_manifest'].items()]
    result['source_manifest'] = encode_table(manifest_rows)
    result['model_probabilities'] = encode_table([{'probability_id': key, **deepcopy(row)}
                                                for key, row in original['model_probabilities'].items()])
    families, prior_summaries = {}, {}
    for name, envelope in original['families'].items():
        value = envelope['value']
        if name == 'calendar':
            projected = _calendar(value)
        elif name == 'retained_history':
            projected = _history(value)
        elif name == 'odds_and_price_history':
            projected = _candidate_values(value, fixtures)
        elif name == 'numeric_models':
            projected = _models(value, original['model_probabilities'])
        elif name in {'team_stats_xg', 'players_squad_xi', 'schedule_venue_pitch', 'weather'}:
            projected = _context(name, value)
        elif name == 'own_outcomes_and_costs':
            projected = _without_raw(value, {'historical_runs'}, family=name, location=name)
        else:
            # Historical tool public aggregates and news are not replaced by a count.
            projected = deepcopy(value)
        if name in {'odds_and_price_history', 'own_outcomes_and_costs'} and isinstance(value, dict):
            prior = value.get('historical_runs')
            if isinstance(prior, list):
                refs = []
                for row in prior:
                    key = digest(row)
                    prior_summaries.setdefault(key, _prior_run(row))
                    refs.append(key)
                projected['historical_runs'] = {'view_path': 'prior_run_summaries',
                    'source_value_sha256': digest(prior), 'ordered_run_refs': refs, 'source_row_count': len(prior)}
        families[name] = {**{key: deepcopy(item) for key, item in envelope.items() if key != 'value'},
            'value': projected, 'view_value_sha256': digest(projected),
            'value_sha256_scope': 'ORIGINAL_FULL_SNAPSHOT_FAMILY_VALUE',
            'detail_level': 'DIRECT_VALUES_WITH_EXPLICIT_RAW_BACKING_REFERENCES'}
    result['families'] = families
    result['prior_run_summaries'] = prior_summaries
    require(result['fixture_ids'] == original['fixture_ids'] and result['owner_ids'] == original['owner_ids'],
            'CALENDAR_OR_OWNER_COVERAGE_CHANGED')
    return result
