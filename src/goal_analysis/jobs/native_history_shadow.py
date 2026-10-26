"""Already downloaded native raw results -> receipt-bound SHADOW model inputs.

No fetch, admission write, alias guess or production decision. Downloading old
results today can support a forecast today; it cannot certify old Pi features.
"""
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.models._data import CLOSURE_DELAY
from goal_analysis.models.history_sample_shadow import select_sample
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import field_id, native_id
from goal_analysis.normalization.leakage import LookAheadError, model_inputs
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.source_observations import read_source_raw, source_key

from .native_history_compaction import HistoryCompactor
from .point_in_time import require_chronological_availability
from .the_stats_live import _identity, normalize_history

VERSION = 'witnessed-native-raw-history-lossless-shadow-v2'
MAX_METADATA = 2000
MAX_SOURCES = 200
MAX_ROWS = 10000
MAX_SOURCE_BYTES = 5_000_000


def _query(source):
    url = urlsplit(source['source_url'] or '')
    return parse_qs(url.query) if url.path == '/api/football/matches' else None


def _page(body, query):
    data, meta = body['data'], body['meta']
    if (not isinstance(data, list) or len(data) > 100
            or type(meta['page']) is not int or type(meta['per_page']) is not int
            or type(meta['total_pages']) is not int or meta['per_page'] != 100
            or not 0 <= meta['total_pages'] <= 100000
            or meta['page'] != int(query.get('page', ['1'])[0])
            or query.get('per_page', ['100']) != ['100']
            or meta['page'] < 1 or (data and meta['page'] > meta['total_pages'])
            or (not data and meta['total_pages'] != 0)):
        raise ValueError('INVALID_PAGE')
    return data


def _envelope(payload, proof, kind):
    times = proof['times']
    payload = {**payload, **{k: times[k] for k in
                             ('observed_at', 'available_at', 'ingested_at', 'published_at')}}
    identity = [kind, payload['fixture_id'], proof['source_key']]
    if proof.get('observation_id'):
        identity.append(proof['observation_id'])
    return {'record_id': sha(identity),
        'payload': payload, 'payload_sha256': sha(payload), 'times': times,
        'receipt_id': proof['receipt_id'], 'source_key': proof['source_key'],
        'source_sha256': proof['source_sha256']}


def native_history_view(root, fixtures, *, as_of):
    """Bounded read-only input view. Unknown venue/identity/receipt remains a gap.

    Exact native target IDs, season, sides, kickoff and explicit venue must
    agree with the target's raw source. Historical seasons remain explicit;
    past seasons of the SAME competition are allowed, other leagues are not.
    Conflicting/unknown eligible history versions quarantine their match before
    model fitting, irrespective of goals or which version looks convenient.
    """
    cutoff = utc(as_of)
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('INVALID_NATIVE_TARGETS')
    root = Path(root).resolve()
    path = root / 'data/goal_analysis.sqlite3'
    base = {'version': VERSION, 'mode': 'SHADOW', 'decision_effect': 'NONE',
        'live_promotion': False, 'as_of': cutoff.isoformat(), 'external_requests': 0,
        'ledger_writes': 0, 'original_fixture_sha256': sha(fixtures)}
    counts, fixture_envelopes, history_envelopes, proofs = Counter(), [], [], {}
    if not path.is_file():
        return {**base, 'status': 'MISSING_SOURCE', 'fixtures': [], 'history': [], 'gaps': {}}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'raw_snapshots', 'temporal_receipts'} <= tables:
            return {**base, 'status': 'MISSING_PROOF_TABLES', 'fixtures': [], 'history': [], 'gaps': {}}
        decoded = {}

        def read(source, alias=None):
            alias = alias or source.get('_alias') or {
                'content_hash': source['content_hash'], 'source_url': source['source_url']}
            key = source_key(alias)
            if key not in decoded:
                if not 0 < source['size_bytes'] <= MAX_SOURCE_BYTES:
                    raise ValueError('SOURCE_BYTE_LIMIT')
                raw_row = {k: v for k, v in source.items() if k != '_alias'}
                body, proof = read_source_raw(db, raw_row, root, cutoff, alias)
                query = _query(proof)
                if query is None:
                    raise ValueError('WRONG_SOURCE_ENDPOINT')
                decoded[key] = (_page(body, query), proof)
                proofs[proof.get('observation_id', proof['source_key'])] = proof
            return decoded[key]

        seen_targets = Counter(f.get('fixture_id') for f in fixtures if isinstance(f, dict))
        target_ids = set()
        for fixture in fixtures:
            try:
                identifier = fixture_identity(fixture)
                if fixture.get('source_provider') != 'the_stats_api':
                    raise ValueError('NON_NATIVE_TARGET')
                target_ids.add(identifier)
                if seen_targets[fixture['fixture_id']] != 1:
                    raise ValueError('AMBIGUOUS_TARGET')
                source = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? "
                    "AND provider='the_stats_api'", (fixture['fixture_source']['content_hash'],)).fetchone()
                if source is None:
                    raise ValueError('MISSING_TARGET_SOURCE')
                source = dict(source)
                rows, proof = read(source, fixture['fixture_source'])
                matches = [r for r in rows if isinstance(r, dict) and r.get('id') == identifier]
                if len(matches) != 1:
                    raise ValueError('AMBIGUOUS_RAW_TARGET')
                row = matches[0]
                _, comp, season, home, away = _identity(row)
                if ((comp, season, home, away) != tuple(fixture['provider_' + k + '_id'] for k in
                        ('league', 'season', 'home_team', 'away_team'))
                        or utc(row['utc_date']) != utc(fixture['kickoff'])
                        or type(row.get('is_neutral')) is not bool
                        or row['is_neutral'] is not fixture.get('neutral_venue')
                        or row.get('status') != 'scheduled' or fixture.get('provider_status') != 'NS'):
                    raise ValueError('RAW_TARGET_IDENTITY_OR_VENUE_MISMATCH')
                if utc(row['utc_date']) <= cutoff + timedelta(minutes=15):
                    raise ValueError('PREMATCH_LOCKOUT')
                fixture_envelopes.append(_envelope(deepcopy(fixture), proof, 'target'))
            except (KeyError, ValueError, TypeError, AttributeError, OSError, OverflowError):
                counts['UNPROVEN_TARGET'] += 1

        targets = model_inputs(fixture_envelopes, as_of=cutoff)
        competitions = {f['provider_league_id'] for f in targets}
        # Scope BEFORE bounding: unrelated domestic/odds metadata must not
        # occupy this competition's entire history budget as the archive grows.
        clauses, params = [], []
        for comp in sorted(competitions):
            clauses.append('(instr(source_url,?)>0 OR substr(source_url,-?)=?)')
            token = 'competition_id=' + comp
            params.extend((token + '&', len(token), token))
        # The first raw URL is immutable. Actual later URLs belong to HTTP
        # observations, so scope both kinds BEFORE applying metadata budgets.
        # Identical bytes at the same URL need only their earliest eligible
        # witness; distinct byte versions remain for conflict quarantine.
        http = '''SELECT NULL AS content_hash,NULL AS source_url,NULL AS observed_at,
            NULL AS response_id,NULL AS receipt_id WHERE 0'''
        http_params = ()
        if {'the_stats_http_observations', 'the_stats_http_witnesses'} <= tables:
            http = '''SELECT content_hash,source_url,observed_at,response_id,receipt_id
                FROM (SELECT o.*,w.receipt_id,
                    row_number() OVER (PARTITION BY o.content_hash,o.source_url
                        ORDER BY w.available_at,o.response_id) AS ordinal
                    FROM the_stats_http_observations o JOIN the_stats_http_witnesses w USING(response_id)
                    WHERE julianday(w.available_at)<=julianday(?)
                    AND julianday(o.observed_at)<=julianday(?)
                    AND julianday(o.ingested_at)<=julianday(?)) WHERE ordinal=1'''
            http_params = (cutoff.isoformat(),)*3
        sources = []
        if clauses:
            rows = db.execute('WITH available_http AS (' + http + '''), aliases AS (
                SELECT content_hash,source_url,fetched_at AS observed_at,NULL AS response_id,NULL AS receipt_id
                FROM raw_snapshots r WHERE provider='the_stats_api'
                AND NOT EXISTS (SELECT 1 FROM available_http h
                    WHERE h.content_hash=r.content_hash AND h.source_url=r.source_url)
                UNION ALL SELECT * FROM available_http)
                SELECT r.*,a.source_url AS alias_url,a.observed_at AS alias_observed,
                    a.response_id AS alias_id,a.receipt_id AS alias_receipt
                FROM aliases a JOIN raw_snapshots r USING(content_hash)
                WHERE r.provider='the_stats_api'
                AND a.source_url LIKE 'https://api.thestatsapi.com/api/football/matches?%'
                AND instr(a.source_url,'status=finished')>0 AND (''' +
                ' OR '.join(c.replace('source_url', 'a.source_url') for c in clauses) +
                ') ORDER BY a.observed_at DESC,a.content_hash,a.source_url,a.response_id LIMIT ?',
                (*http_params, *params, MAX_METADATA + 1))
            for value in rows:
                source = dict(value)
                alias = {'content_hash': source['content_hash'], 'source_url': source.pop('alias_url')}
                observed, identifier, receipt = (source.pop(k) for k in
                    ('alias_observed', 'alias_id', 'alias_receipt'))
                if identifier is not None:
                    alias.update(provider='the_stats_api', observed_at=observed,
                        http_observation_id=identifier, http_receipt_id=receipt,
                        http_observation_status='VERIFIED')
                source['_alias'] = alias
                sources.append(source)
        if len(sources) > MAX_METADATA:
            return {**base, 'status': 'LIMIT_REACHED', 'fixtures': targets, 'history': [],
                    'gaps': {**counts, 'METADATA_LIMIT': 1}}
        applicable = []
        for source in sources:
            query = _query(source['_alias'])
            if (query is not None and query.get('status') == ['finished']
                    and len(query.get('competition_id', [])) == 1
                    and query['competition_id'][0] in competitions):
                applicable.append((source, query))
        truncated = len(applicable) > MAX_SOURCES
        if truncated:
            counts['SOURCE_LIMIT'] += 1
        scanned = 0
        compactor = HistoryCompactor()
        for source, query in applicable[:MAX_SOURCES]:
            try:
                rows, proof = read(source)
                lower, upper = query['date_from'][0], query['date_to'][0]
                # Strict date schema; +00:00 is required so URL bounds are UTC.
                from datetime import date

                if (date.fromisoformat(lower).isoformat() != lower
                        or date.fromisoformat(upper).isoformat() != upper or lower > upper
                        or query.get('utc_offset') != ['+00:00']):
                    raise ValueError('INVALID_HISTORY_WINDOW')
                for row in rows:
                    scanned += 1
                    # Raw work remains bounded by strict source_count and page100.
                    # MAX_ROWS now counts distinct truth; all aliases stay in lineage.
                    if scanned > MAX_SOURCES * 100:
                        truncated = True
                        counts['RAW_OCCURRENCE_LIMIT'] += 1
                        break
                    try:
                        native_id(field_id(row, 'season'), 'sn')
                        kickoff = utc(row['utc_date'])
                        if not lower <= kickoff.date().isoformat() <= upper:
                            raise ValueError('OUTSIDE_SOURCE_DATE_WINDOW')
                        record = normalize_history(row, query['competition_id'][0],
                            {'observed_at': proof['times']['observed_at'], 'source_url': proof['source_url']},
                            cutoff)
                        if record['fixture_id'] in target_ids:
                            counts['TARGET_HISTORY_EXCLUDED'] += 1
                            continue
                        if kickoff + CLOSURE_DELAY >= cutoff:
                            raise ValueError('UNCLOSED_RESULT')
                        # Missing/null is not the legacy False default.
                        record['neutral_venue'] = row.get('is_neutral')
                        if any(type(record['full_time_' + side]) is not int
                               or not 0 <= record['full_time_' + side] <= 50 for side in ('home', 'away')):
                            raise ValueError('INVALID_REGULATION_SCORE')
                        compactor.add(_envelope(record, proof, 'history'))
                        if len(compactor) > MAX_ROWS:
                            truncated = True
                            counts['ROW_LIMIT'] += 1
                            break
                    except (KeyError, TypeError, ValueError, OverflowError):
                        counts['INVALID_OR_UNCLOSED_HISTORY_ROW'] += 1
            except (KeyError, TypeError, ValueError, OSError, OverflowError):
                counts['UNPROVEN_HISTORY_SOURCE'] += 1
            if truncated:
                break
        history_envelopes = compactor.envelopes()
        # Incomplete bounds cannot silently hide a contradictory version.
        if truncated:
            history_envelopes = []
        # Identical raw duplicate rows get one envelope; differing content may
        # not borrow that same ID. select_sample handles cross-source conflicts.
        unique = {}
        for envelope in history_envelopes:
            key = envelope['record_id']
            if key in unique and unique[key] != envelope:
                raise ValueError('CONFLICTING_RAW_HISTORY_IDENTITY')
            unique[key] = envelope
        history_envelopes = sorted(unique.values(), key=lambda r: r['record_id'])
        history = model_inputs(history_envelopes, as_of=cutoff)
    selection = select_sample(targets, history, cutoff)
    selected_keys = {r['fixture_id'] for r in selection['history']}
    # Keep provenance/availability lost by legacy model normalization. Both
    # distinct source versions remain for the shared conflict check/dedup.
    selected = [r for r in history if r['fixture_id'] in selected_keys and r['neutral_venue'] is False]
    dataset = snapshot(selected, role='witnessed_native_raw_history', as_of=cutoff,
        recipe={'version': VERSION, 'sample_policy': selection['version'],
                'competition_ids': sorted(competitions)},
        lineage={'sources': sorted(proofs.values(), key=lambda p: (p['source_key'], p.get('observation_id', ''))),
                 'history_envelopes': [{k:v for k,v in e.items() if k != 'payload'}
                                       for e in history_envelopes],
                 'fixture_envelopes': fixture_envelopes,
                 'lossless_history_aliases': compactor.aliases()})
    return {**base, 'status': 'LIMIT_REACHED' if truncated else 'GAPS' if counts else 'AVAILABLE',
        'fixtures': selection['fixtures'], 'history': selected, 'dataset': dataset,
        'selection': {k:v for k,v in selection.items() if k not in {'fixtures', 'history'}},
        'gaps': dict(counts), 'scanned_history_rows': scanned, 'source_count': len(proofs),
        'verified_target_count': len(targets), 'history_source_candidates': len(applicable),
        'materialized_history_observations': len(compactor),
        'raw_occurrence_budget': MAX_SOURCES * 100,
        'row_budget_unit': 'DISTINCT_EXACT_NORMALIZED_TRUTH_ENVELOPES',
        'history_coverage_scope': 'OBSERVED_PAGES_ONLY_NOT_COMPLETE_PROVIDER_HISTORY',
        'historical_feature_availability_certified': False}


def replay_native_history(root, fixtures, *, as_of, profiles=('gauss', 'kepler', 'laplace'), artifact_dir=None):
    """Actual model fit on verified inputs, separate from LIVE numeric_models."""
    from goal_analysis.models import predict_models

    enabled = tuple(dict.fromkeys(profiles))
    if not set(enabled) <= {'gauss', 'kepler', 'laplace'}:
        raise ValueError('INVALID_NATIVE_SHADOW_PROFILES')
    view = native_history_view(root, fixtures, as_of=as_of)
    excluded = {}
    if 'kepler' in enabled and view['history']:
        try:
            require_chronological_availability(view['history'])
        except LookAheadError:
            excluded['kepler'] = {'profile_id': 'kepler', 'status': 'UNPROVEN_POINT_IN_TIME_HISTORY',
                                  'predictions': [], 'reason': 'UNPROVEN_CHRONOLOGICAL_FEATURE_AVAILABILITY'}
            enabled = tuple(p for p in enabled if p != 'kepler')
    models = predict_models(view['fixtures'], view['history'], utc(as_of),
                            enabled_profiles=enabled, artifact_dir=artifact_dir) if enabled else {}
    return {'version': VERSION, 'mode': 'SHADOW', 'decision_effect': 'NONE', 'live_promotion': False,
        'as_of': utc(as_of).isoformat(), 'numeric_models': {**models, **excluded},
        'source_view': view, 'external_requests': 0, 'ledger_writes': 0,
        'historical_calibration_certified': False}
