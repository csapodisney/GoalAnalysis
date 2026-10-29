"""Source-bound NORMAL venue correction, preserving original collected inputs.

The public adapter performs only bounded direct SQLite/raw reads. The pure core
accepts a source-reader callback for TESTONLY controls. No timestamp, receipt,
raw file or original normal collection is changed, and nothing is admitted.
"""
import sqlite3
import time
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import date
from pathlib import Path

from goal_analysis.jobs.native_history_shadow import MAX_SOURCE_BYTES, MAX_SOURCES, _page, _query
from goal_analysis.jobs.the_stats_live import normalize_fixture, normalize_history
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.source_observations import read_source_raw, source_key

MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024
MAX_READ_SECONDS = 30


def _history_pointer(value):
    return value['source_url'], utc(value['observed_at'])


def _sources(fixtures, packet):
    sources = list(packet.get('sources', []))
    sources += [f.get('fixture_source') for f in fixtures if isinstance(f, dict)
                and f.get('source_provider') == 'the_stats_api']
    unique = {}
    for value in sources:
        if isinstance(value, dict):
            unique.setdefault(source_key(value), value)
    return list(unique.values())


def _raw_rows(source, cutoff, reader):
    body, proof = reader(source)
    require_known_before(proof['times'], as_of=cutoff)
    if (proof['source_url'] != source['source_url']
            or utc(proof['times']['observed_at']) != utc(source['observed_at'])):
        raise ValueError('ONLINE_SOURCE_URL_OR_CLOCK_MISMATCH')
    query = _query(source)
    if query is None:
        raise ValueError('ONLINE_SOURCE_ENDPOINT_MISMATCH')
    return _page(body, query)


def correct_inputs(fixtures, history_packet, *, as_of, source_reader):
    """Correct only copied native venue flags after all original fields match."""
    cutoff = utc(as_of)
    targets, history = deepcopy(fixtures), deepcopy(history_packet.get('records', []))
    sources = _sources(fixtures, history_packet)
    counts, ledger, cache = Counter(), [], {}
    scope_limited = len(sources) > MAX_SOURCES
    pointers = {}
    for source in history_packet.get('sources', []):
        try:
            pointers.setdefault(_history_pointer(source), []).append(source)
        except (KeyError, TypeError, ValueError):
            continue

    def rows(source):
        if scope_limited:
            raise ValueError('ONLINE_VENUE_SOURCE_BOUND_NO_FIRST_N')
        key = source_key(source)
        if key not in cache:
            cache[key] = _raw_rows(source, cutoff, source_reader)
        return cache[key]

    for kind, records in (('fixture', targets), ('history', history)):
        for item in records:
            native = isinstance(item, dict) and item.get('source_provider' if kind == 'fixture' else 'provider') == 'the_stats_api'
            if not native:
                continue
            original = deepcopy(item)
            reason, raw_values = 'EXACT_SOURCE_FLAG', []
            relevant = []
            try:
                if kind == 'fixture':
                    relevant = [item['fixture_source']]
                else:
                    relevant = pointers[(item['source_id'], utc(item['observed_at']))]
                for source in relevant:
                    native_id = item['provider_fixture_id'] if kind == 'fixture' else item['fixture_id']
                    matches = [r for r in rows(source) if isinstance(r, dict) and r.get('id') == native_id]
                    if len(matches) != 1:
                        raise ValueError('ONLINE_SOURCE_MATCH_AMBIGUOUS_OR_ABSENT')
                    raw = matches[0]
                    if kind == 'history':
                        normalized = normalize_history(raw, item['league_id'], source, cutoff)
                    else:
                        if item.get('native_competition_route') is not None:
                            normalized = normalize_fixture(raw, None, date.fromisoformat(item['target_date']), source,
                                operating_contract_version=2, native_route=item['native_competition_route'])
                        else:
                            league = {'api_football_id': int(item['config_league_id']), 'season': item['season'],
                                'competition_id': item['competition'], 'competition_type': item['competition_type'],
                                'odds_sport_key': item['sport_key']}
                            normalized = normalize_fixture(raw, league, date.fromisoformat(item['target_date']), source)
                    if any(item.get(k) != v for k, v in normalized.items() if k != 'neutral_venue'):
                        raise ValueError('ONLINE_NORMALIZED_RECORD_DIFFERS_FROM_RAW')
                    raw_values.append(raw.get('is_neutral'))
                if not raw_values or any(type(value) is not bool for value in raw_values):
                    reason, neutral = 'RAW_VENUE_UNKNOWN', None
                elif len(set(raw_values)) != 1:
                    reason, neutral = 'CONFLICTING_RAW_VENUE_VERSIONS', None
                else:
                    neutral = raw_values[0]
            except Exception as error:  # noqa: BLE001 - source failure is local to this native record
                neutral = None
                safe = {'ONLINE_VENUE_SOURCE_BOUND_NO_FIRST_N', 'ONLINE_SOURCE_MATCH_AMBIGUOUS_OR_ABSENT',
                        'ONLINE_NORMALIZED_RECORD_DIFFERS_FROM_RAW', 'ONLINE_SOURCE_URL_OR_CLOCK_MISMATCH',
                        'ONLINE_SOURCE_ENDPOINT_MISMATCH'}
                reason = str(error) if str(error) in safe else 'ONLINE_VENUE_SOURCE_UNPROVEN'
            item['neutral_venue'] = neutral
            counts[reason] += 1
            ledger.append({'kind': kind, 'fixture_id': item['fixture_id'], 'reason': reason,
                'original_normalized_row_sha256': sha(original), 'corrected_model_row_sha256': sha(item),
                'original_neutral_value': original.get('neutral_venue'), 'model_neutral_value': neutral,
                'source_keys': [list(source_key(p)) for p in relevant]})
    return {'fixtures': targets, 'history': history, 'diagnostics': dict(counts),
            'bindings': ledger, 'original_inputs_unchanged': True,
            'new_receipts_or_availability_clocks': 0, 'source_binding_count': len(sources)}


def bind_online_venue(root, fixtures, history_packet, *, as_of):
    """Mode=ro/query_only/BEGIN, before any correction; all source work bounded."""
    root, cutoff = Path(root).resolve(), utc(as_of)
    sources = _sources(fixtures, history_packet)
    loaded, failure = {}, None
    path = root/'data/goal_analysis.sqlite3'
    started = time.monotonic()
    try:
        if len(sources) > MAX_SOURCES:
            raise ValueError('ONLINE_VENUE_SOURCE_BOUND_NO_FIRST_N')
        if not path.is_file():
            raise ValueError('MISSING_ONLINE_SOURCE_DATABASE')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            db.set_progress_handler(lambda: int(time.monotonic()-started > MAX_READ_SECONDS), 10000)
            metadata, total = {}, 0
            for source in sources:
                key = source_key(source)
                value = db.execute("SELECT * FROM raw_snapshots WHERE provider='the_stats_api' AND content_hash=?",
                                   (source.get('content_hash'),)).fetchone()
                if value is not None:
                    value = dict(value)
                    if not 0 < value['size_bytes'] <= MAX_SOURCE_BYTES:
                        value = None
                    else:
                        total += value['size_bytes']
                metadata[key] = value
            if total > MAX_TOTAL_SOURCE_BYTES:
                raise ValueError('ONLINE_VENUE_TOTAL_BYTE_BOUND_NO_FIRST_N')
            for source in sources:
                if time.monotonic()-started > MAX_READ_SECONDS:
                    raise ValueError('ONLINE_VENUE_TIME_BOUND_NO_FIRST_N')
                key = source_key(source)
                try:
                    if metadata[key] is None:
                        raise ValueError('MISSING_ONLINE_SOURCE_RAW')
                    loaded[key] = read_source_raw(db, metadata[key], root, cutoff, source)
                except (KeyError, ValueError, TypeError, OSError, sqlite3.Error):
                    loaded[key] = None
            if time.monotonic()-started > MAX_READ_SECONDS:
                raise ValueError('ONLINE_VENUE_TIME_BOUND_NO_FIRST_N')
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
        loaded = {}
        failure = str(error) if isinstance(error, ValueError) else type(error).__name__

    def reader(source):
        value = loaded.get(source_key(source))
        if value is None:
            raise ValueError('ONLINE_VENUE_SOURCE_UNPROVEN')
        return value

    result = correct_inputs(fixtures, history_packet, as_of=cutoff, source_reader=reader)
    result['read_budget_failure'] = failure
    result['read_only'] = True
    result['source_proofs'] = [{'source': deepcopy(source), 'proof': deepcopy(loaded[source_key(source)][1])}
        for source in sources if loaded.get(source_key(source)) is not None]
    return result
