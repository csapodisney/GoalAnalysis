"""Immutable SHADOW declarations and real first own-presence witnesses.

Local SQLite source/receipt protections follow existing append-only stores.
Hashes supplied by a caller are never evidence of a stored registration.
No fit or forecast writer is introduced. No model implementation or profitable
effect is certified by registering a declaration with named model versions.
"""
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from goal_analysis.agents.weather_market_validation import _scope, market_outcome, protocol_payload
from goal_analysis.agents.weather_native_dataset import (
    _result as canonical_result,
)
from goal_analysis.agents.weather_native_dataset import (
    _witness,
    load_native_weather_dataset,
)
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.jobs.the_stats_results import _result
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.training_datasets import encoded, sha
from goal_analysis.storage import Database
from goal_analysis.storage.historical_results import decision_content
from goal_analysis.storage.temporal import _source_hash

VERSION = 'weather-market-protocol-store-v1'
MAX_PROTOCOL_BYTES = 512_000
MAX_BINDING_BYTES = 2_000_000
MAX_TRAINING = 128
MAX_HOLDOUT = 100
MAX_PROTOCOLS = 128
MAX_CONTENT_VERSIONS = 16


def _bounded(value, limit):
    data = encoded(value)
    if not 0 < len(data) <= limit:
        raise ValueError('WEATHER_PROTOCOL_BYTE_BUDGET')
    return data.decode('utf-8')


def _intent(protocol, training_ids, holdout_sources):
    protocol_payload(protocol)
    if (set(protocol) != {'version', 'family', 'competition_id', 'season_id',
            'training_end', 'validation_start', 'validation_end', 'training_envelopes_sha256',
            'baseline_model_version', 'weather_model_version', 'holdout_fixtures'}
            or any(set(r) != {'fixture_id', 'kickoff', 'home_team_id', 'away_team_id'}
                   for r in protocol['holdout_fixtures'])):
        raise ValueError('WEATHER_PROTOCOL_EXACT_DECLARATION_SCHEMA_REQUIRED')
    if (not isinstance(training_ids, list) or not 1 <= len(training_ids) <= MAX_TRAINING
            or any(not isinstance(k, str) or not re.fullmatch(r'the_stats_api:mt_\d{1,30}', k) for k in training_ids)
            or len(set(training_ids)) != len(training_ids)
            or not isinstance(holdout_sources, dict) or not 1 <= len(holdout_sources) <= MAX_HOLDOUT
            or set(holdout_sources) != {r['fixture_id'] for r in protocol['holdout_fixtures']}
            or set(training_ids) & set(holdout_sources)):
        raise ValueError('WEATHER_PROTOCOL_EXACT_SOURCE_SELECTION_REQUIRED')
    selection = {'training_ids': training_ids, 'holdout_sources': holdout_sources}
    p, s = _bounded(protocol, MAX_PROTOCOL_BYTES), _bounded(selection, MAX_PROTOCOL_BYTES)
    return sha([json.loads(p), json.loads(s)]), p, s



def _result_content_source(sources, db, source, cutoff):
    """Authenticate content separately from an unproven later fetch claim.

    An exact, earlier immutable result and its own receipt may bind identical
    content to the first archived raw response. This never authenticates the
    later URL/time, creates an HTTP alias, or relaxes the shared raw reader.
    """
    record = canonical_result(source)
    selected = {'version_id': source['version_id'],
                **_witness(db, 'result', source['version_id'], source, cutoff)}
    pointer = {'content_hash': record['source_sha256'], 'source_url': record['source_url'],
               'observed_at': record['observed_at']}
    evidence = {'mode': 'EXACT_RESULT_SOURCE', 'selected_result': selected}
    try:
        body, proof = sources.raw(pointer, cutoff)
    except ValueError as error:
        if str(error) != 'RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH':
            raise
        raw = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                         (record['source_sha256'],)).fetchone()
        if (raw is None or raw['provider'] != 'the_stats_api'
                or utc(raw['fetched_at']) >= utc(record['observed_at'])):
            raise ValueError('WEATHER_PROTOCOL_EARLIER_CONTENT_SOURCE_REQUIRED') from error
        original_pointer = {'content_hash': raw['content_hash'], 'source_url': raw['source_url'],
                            'observed_at': utc(raw['fetched_at']).isoformat(timespec='microseconds')}
        candidates = db.execute("""SELECT * FROM historical_result_versions
            WHERE match_id=? AND observed_at=? LIMIT ?""",
            (source['match_id'], original_pointer['observed_at'], MAX_CONTENT_VERSIONS+1)).fetchall()
        if not 1 <= len(candidates) <= MAX_CONTENT_VERSIONS:
            raise ValueError('WEATHER_PROTOCOL_CONTENT_VERSION_BUDGET_OR_MISSING') from error
        verified = []
        for candidate in candidates:
            sources._budget()
            candidate = dict(candidate)
            original = canonical_result(candidate)
            receipt = _witness(db, 'result', candidate['version_id'], candidate, cutoff)
            if (original['source_sha256'] != original_pointer['content_hash']
                    or original['source_url'] != original_pointer['source_url']
                    or utc(original['observed_at']) != utc(original_pointer['observed_at'])
                    or decision_content(original) != decision_content(record)
                    or sha(original['raw']) != sha(record['raw'])):
                raise ValueError('WEATHER_PROTOCOL_ORIGINAL_RESULT_CONTENT_MISMATCH') from error
            verified.append({'version_id': candidate['version_id'], **receipt})
        if len(verified) != 1:
            raise ValueError('WEATHER_PROTOCOL_AMBIGUOUS_ORIGINAL_RESULT_CONTENT') from error
        # A second strict read authenticates only this original pointer.
        body, proof = sources.raw(original_pointer, cutoff)
        evidence.update(mode='ORIGINAL_RESULT_CONTENT_WITH_UNVERIFIED_LATER_FETCH',
                        original_result=verified[0], unverified_later_pointer=pointer,
                        later_fetch_verified=False)
        pointer = original_pointer
    evidence['raw'] = proof
    return record, body, pointer, evidence


def training_sources(root, protocol, training_ids, *, as_of):
    """Reconstruct exact training envelopes from real native result/ERA5 stores.

    Composite receipt IDs bind existing receipts; no new source clock is made.
    Availability is the latest knowledge required by the combined training row.
    Counts here are sources, never proof of the30/20market validation threshold.
    """
    if not isinstance(training_ids, list) or not 1 <= len(training_ids) <= MAX_TRAINING:
        raise ValueError('WEATHER_PROTOCOL_TRAINING_BUDGET')
    source = load_native_weather_dataset(root, as_of=as_of)
    rows = {r['fixture_id']: r for r in source['matches']}
    lineage = {r['source_key']: r for r in source['dataset']['lineage'] if r['namespace'] == 'result'}
    envelopes = []
    path = Path(root).resolve()/'data/goal_analysis.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        sources = Sources(root, db, deadline=monotonic()+30)
        raw_proofs = {}
        for key in training_ids:
            m = rows.get(key)
            if m is None or 'weather' not in m or not m['weather'].get('source_validation'):
                raise ValueError('WEATHER_PROTOCOL_REAL_TRAINING_SOURCE_MISSING')
            selected = db.execute('SELECT * FROM historical_result_versions WHERE version_id=?',
                                  (m['result_version_id'],)).fetchone()
            if selected is None:
                raise ValueError('WEATHER_PROTOCOL_SELECTED_RESULT_MISSING')
            record, body, pointer, proof = _result_content_source(sources, db, dict(selected), as_of)
            expected = {'version_id': m['result_version_id'],
                        **{k: v for k, v in lineage[m['result_version_id']].items()
                           if k not in {'namespace', 'source_key'}}}
            if proof['selected_result'] != expected:
                raise ValueError('WEATHER_PROTOCOL_SELECTED_RESULT_SNAPSHOT_CHANGED')
            items = body['data'] if isinstance(body['data'], list) else [body['data']]
            if len(items) > 100:
                raise ValueError('WEATHER_PROTOCOL_RAW_RESULT_ROW_BUDGET')
            exact = [r for r in items if isinstance(r, dict) and r.get('id') == record['provider_id']]
            if len(exact) != 1 or sha(exact[0]) != sha(record['raw']):
                raise ValueError('WEATHER_PROTOCOL_RESULT_ORIGINAL_RAW_MISMATCH')
            payload = _result(exact[0], pointer); full = payload['score']['fulltime']
            half = payload['score']['halftime']
            if ((m['home_goals'], m['away_goals']) != (full['home'], full['away'])
                    or (m['half_home_goals'], m['half_away_goals']) != (half['home'], half['away'])):
                raise ValueError('WEATHER_PROTOCOL_RESULT_NORMALIZED_SCORE_MISMATCH')
            sources.result(payload, {'fixture_id': key, 'kickoff': m['kickoff'],
                'provider_league_id': record['competition_id'], 'provider_season_id': record['season_id'],
                'provider_home_team_id': record['home_id'], 'provider_away_team_id': record['away_id']}, as_of)
            raw_proofs[key] = proof
    for key in training_ids:
        m = rows.get(key)
        if m is None or 'weather' not in m or not m['weather'].get('source_validation'):
            raise ValueError('WEATHER_PROTOCOL_REAL_TRAINING_SOURCE_MISSING')
        p = {k: m[k] for k in ('fixture_id', 'home_team_id', 'away_team_id', 'kickoff', 'status',
            'result_period', 'home_goals', 'away_goals', 'half_home_goals', 'half_away_goals')}
        p.update(competition_id=m['league_id'], season_id=m['season'], weather=m['weather'])
        _scope(p, protocol); market_outcome(p, protocol['family'])
        result = lineage[m['result_version_id']]; weather = m['weather']
        observed = max(utc(result['times']['observed_at']), utc(weather['observed_at']))
        evidence = raw_proofs[key]
        receipts = [evidence['selected_result'], evidence['raw']]
        if 'original_result' in evidence:
            receipts.append(evidence['original_result'])
        available = max(utc(weather['available_at']),
                        *(utc(r['times']['available_at']) for r in receipts))
        times = {'observed_at': observed.isoformat(), 'available_at': available.isoformat(),
            'published_at': result['times']['published_at'], 'ingested_at': None,
            'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND',
            'event_time_kind': 'REANALYSIS_TARGET', 'event_time': m['kickoff']}
        require_known_before(times, as_of=as_of)
        envelopes.append({'record_id': 'weather-training:'+key, 'payload': p, 'payload_sha256': sha(p),
            'source_sha256': sha([result['source_sha256'], raw_proofs[key], weather]),
            'receipt_id': sha(['COMPOSITE_EXISTING_SOURCE_RECEIPTS',
                               [r['receipt_id'] for r in receipts], weather['receipt_id']]),
            'source_evidence': evidence, 'times': times})
    return envelopes


def _bindings(root, db, protocol, selection, cutoff):
    training = training_sources(root, protocol, selection['training_ids'], as_of=cutoff)
    if sha(training) != protocol['training_envelopes_sha256']:
        raise ValueError('WEATHER_PROTOCOL_TRAINING_SOURCE_BINDING_CHANGED')
    sources = Sources(root, db, deadline=monotonic()+30)
    targets = []
    for fixture in protocol['holdout_fixtures']:
        declared = selection['holdout_sources'][fixture['fixture_id']]
        if (not isinstance(declared, dict) or set(declared) != {'source', 'is_neutral'}
                or type(declared['is_neutral']) is not bool):
            raise ValueError('WEATHER_PROTOCOL_SCHEDULE_SOURCE_REQUIRED')
        leg = {**fixture, 'provider_league_id': protocol['competition_id'].removeprefix('the_stats_api:'),
            'provider_season_id': protocol['season_id'],
            'provider_home_team_id': fixture['home_team_id'].removeprefix('the_stats_api:'),
            'provider_away_team_id': fixture['away_team_id'].removeprefix('the_stats_api:'),
            'neutral_venue': declared['is_neutral'], 'fixture_source': declared['source']}
        targets.append({'fixture_id': fixture['fixture_id'], 'proof': sources.target(leg, cutoff)})
    return {'source_cutoff': utc(cutoff).isoformat(timespec='microseconds'),
        'training': training, 'holdout_sources': targets}


class WeatherProtocols:
    def __init__(self, root, *, clock=lambda: datetime.now(UTC)):
        self.root, self.clock = Path(root).resolve(), clock
        self.path = self.root/'data/goal_analysis.sqlite3'

    def initialize(self):
        """Explicit writer initialization only; readers never create tables."""
        with Database(self.path).transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS weather_market_protocols (
                protocol_id TEXT PRIMARY KEY, version TEXT NOT NULL,
                protocol_json TEXT NOT NULL, selection_json TEXT NOT NULL,
                bindings_json TEXT NOT NULL, registered_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS weather_market_protocol_receipts (
                protocol_id TEXT PRIMARY KEY REFERENCES weather_market_protocols(protocol_id),
                source_sha256 TEXT NOT NULL, receipt_id TEXT NOT NULL, available_at TEXT NOT NULL)''')
            for table in ('weather_market_protocols', 'weather_market_protocol_receipts'):
                for action in ('UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'weather protocols are immutable'); END''')

    def _row(self, db, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier):
            raise ValueError('INVALID_WEATHER_PROTOCOL_ID')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'weather_market_protocols', 'weather_market_protocol_receipts'} <= tables:
            return None, None
        size = db.execute('''SELECT length(CAST(protocol_json AS BLOB)),
            length(CAST(selection_json AS BLOB)),length(CAST(bindings_json AS BLOB))
            FROM weather_market_protocols WHERE protocol_id=?''', (identifier,)).fetchone()
        if size is None:
            return None, None
        if any(not 0 < n <= limit for n, limit in zip(size,
                (MAX_PROTOCOL_BYTES, MAX_PROTOCOL_BYTES, MAX_BINDING_BYTES), strict=True)):
            raise ValueError('WEATHER_PROTOCOL_BYTE_BUDGET')
        row = dict(db.execute('SELECT * FROM weather_market_protocols WHERE protocol_id=?', (identifier,)).fetchone())
        receipt = db.execute('SELECT * FROM weather_market_protocol_receipts WHERE protocol_id=?', (identifier,)).fetchone()
        return row, dict(receipt) if receipt else None

    def _verify(self, db, row, *, as_of):
        p, selection, bound = [json.loads(row[k]) for k in ('protocol_json', 'selection_json', 'bindings_json')]
        identifier, ptext, stext = _intent(p, selection['training_ids'], selection['holdout_sources'])
        if (row['version'] != VERSION or row['protocol_id'] != identifier
                or row['protocol_json'] != ptext or row['selection_json'] != stext
                or _bounded(bound, MAX_BINDING_BYTES) != row['bindings_json']):
            raise ValueError('WEATHER_PROTOCOL_STORED_CONTENT_MISMATCH')
        registered, cutoff = utc(row['registered_at']), utc(bound['source_cutoff'])
        if not cutoff <= registered <= utc(as_of) or registered > utc(p['training_end']):
            raise ValueError('WEATHER_PROTOCOL_REGISTRATION_TIME_MISMATCH')
        reconstructed = _bindings(self.root, db, p, selection, cutoff)
        if encoded(reconstructed).decode() != row['bindings_json']:
            raise ValueError('WEATHER_PROTOCOL_ORIGINAL_SOURCE_BINDING_CHANGED')
        return p, bound

    def read(self, identifier, *, as_of):
        cutoff = utc(as_of)
        if not self.path.exists():
            raise ValueError('WEATHER_PROTOCOL_STORE_MISSING')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            row, receipt = self._row(db, identifier)
            if row is None or receipt is None:
                raise ValueError('WEATHER_PROTOCOL_SOURCE_OR_FIRST_RECEIPT_MISSING')
            source_sha = _source_hash(row)
            if (receipt['source_sha256'] != source_sha or receipt['receipt_id'] != sha(
                    [VERSION, identifier, source_sha, receipt['available_at']])):
                raise ValueError('WEATHER_PROTOCOL_SOURCE_RECEIPT_MISMATCH')
            p, bound = self._verify(db, row, as_of=cutoff)
            times = {'observed_at': row['registered_at'], 'available_at': receipt['available_at'],
                'published_at': None, 'ingested_at': None,
                'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND',
                'event_time_kind': 'FORECAST_TARGET', 'event_time': p['validation_start']}
            require_known_before(times, as_of=cutoff)
            require_known_before(times, as_of=p['training_end'])
            envelope = {'record_id': 'weather-protocol:'+identifier, 'payload': p,
                'payload_sha256': sha(p), 'source_sha256': source_sha,
                'receipt_id': receipt['receipt_id'], 'times': times}
            return {'store_version': VERSION, 'protocol_id': identifier, 'envelope': envelope,
                'training': bound['training'], 'source_bindings_sha256': sha(bound),
                'source_cutoff': bound['source_cutoff'], 'protocol_store_verified': True,
                'model_implementation_verified': False, 'forecast_store_verified': False,
                'heldout_validation_complete': False, 'live_decision_effect': False}

    def register(self, protocol, *, training_ids, holdout_sources):
        """Explicit local writer; preserve a winner's first committed source/clock.

        A source committed without a receipt remains unusable. An explicit
        repeat can witness it only at its real new clock and before training;
        the reader never repairs or reseals an unwitnessed source.
        """
        identifier, ptext, stext = _intent(protocol, training_ids, holdout_sources)
        protocol, selection = json.loads(ptext), json.loads(stext)
        now = utc(self.clock())
        if not self.path.exists():
            raise ValueError('WEATHER_PROTOCOL_NATIVE_SOURCE_DATABASE_MISSING')
        with RunLock(self.root/'data/arthur/run.lock'):
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
                row, receipt = self._row(db, identifier)
                if receipt:
                    return self.read(identifier, as_of=now)
                if row:
                    self._verify(db, row, as_of=now)
                else:
                    if now > utc(protocol['training_end']):
                        raise ValueError('WEATHER_PROTOCOL_REGISTRATION_AFTER_TRAINING')
                    bound = _bindings(self.root, db, protocol, selection, now)
                    stamp = utc(self.clock())
                    if not now <= stamp <= utc(protocol['training_end']):
                        raise ValueError('WEATHER_PROTOCOL_REGISTRATION_AFTER_TRAINING')
                    row = {'protocol_id': identifier, 'version': VERSION, 'protocol_json': ptext,
                        'selection_json': stext, 'bindings_json': _bounded(bound, MAX_BINDING_BYTES),
                        'registered_at': stamp.isoformat(timespec='microseconds')}
            self.initialize()
            with Database(self.path).transaction() as db:
                db.execute('INSERT OR IGNORE INTO weather_market_protocols VALUES (?,?,?,?,?,?)', tuple(row.values()))
            # Only after the source row's commit is its presence witnessed.
            available = utc(self.clock())
            if not utc(row['registered_at']) <= available <= utc(protocol['training_end']):
                raise ValueError('WEATHER_PROTOCOL_SOURCE_PRESENT_BUT_TIMELY_RECEIPT_MISSING')
            source_sha = _source_hash(row); stamp = available.isoformat(timespec='microseconds')
            receipt_id = sha([VERSION, identifier, source_sha, stamp])
            with Database(self.path).transaction() as db:
                db.execute('INSERT OR IGNORE INTO weather_market_protocol_receipts VALUES (?,?,?,?)',
                    (identifier, source_sha, receipt_id, stamp))
            return self.read(identifier, as_of=available)

    def inspect(self, *, as_of):
        """Bounded read-only discovery; no missing/corrupt protocol counts valid."""
        utc(as_of); accepted, gaps = [], []
        if not self.path.exists():
            return {'protocols': [], 'gaps': ['WEATHER_PROTOCOL_STORE_MISSING']}
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE name='weather_market_protocols'").fetchone():
                return {'protocols': [], 'gaps': ['WEATHER_PROTOCOL_STORE_MISSING']}
            ids = [r[0] for r in db.execute('SELECT protocol_id FROM weather_market_protocols ORDER BY protocol_id LIMIT ?',
                                          (MAX_PROTOCOLS+1,))]
        if len(ids) > MAX_PROTOCOLS:
            raise ValueError('WEATHER_PROTOCOL_DISCOVERY_BUDGET')
        deadline = monotonic()+30
        for identifier in ids:
            if monotonic() >= deadline:
                raise ValueError('WEATHER_PROTOCOL_DISCOVERY_TIME_BUDGET')
            try:
                value = self.read(identifier, as_of=as_of)
                if monotonic() >= deadline:
                    raise ValueError('WEATHER_PROTOCOL_DISCOVERY_TIME_BUDGET')
                accepted.append(value)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                if 'BUDGET' in str(error):
                    raise
                gaps.append({'protocol_id': identifier, 'reason': str(error)})
        return {'protocols': accepted, 'gaps': gaps}
