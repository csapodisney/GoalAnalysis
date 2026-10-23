"""Tiny isolated copy of one actual N4 source; mutation clocks are test-only."""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from goal_analysis.normalization.temporal import project_times
from goal_analysis.storage import Database
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.http_observations import HttpObservations
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore, _source_hash

FIXTURE=Path(__file__).parent/'fixtures/native-reanalysis-20261023/actual-source.json'
ORIGINAL=json.loads(FIXTURE.read_bytes())

def install(root, *, change=None, raw_metadata=None, bundle_change=None):
    body=deepcopy(ORIGINAL);tables=body['tables'];row=tables['sports_weather_snapshots'][0]
    stored=json.loads(row['snapshot_json']);old_raw=stored['raw_payload_hash']
    if change:change(stored)
    raw=encoded(stored['raw_payload']).encode();rawsha=sha256(raw).hexdigest()
    weather_raw=next(r for r in tables['raw_snapshots'] if r['content_hash']==old_raw)
    body['raw_files'].pop(weather_raw['relative_path'])
    weather_raw.update(content_hash=rawsha,relative_path='test/'+rawsha+'.json',size_bytes=len(raw))
    if raw_metadata:raw_metadata(weather_raw)
    body['raw_files'][weather_raw['relative_path']]=raw.decode()
    stored['raw_payload_hash']=stored['weather']['raw_payload_hash']=rawsha
    for field in row:
        if field not in {'snapshot_id','snapshot_json'}:row[field]=stored[field]
    row['snapshot_json']=encoded(stored);row['snapshot_id']=digest(stored)
    receipt=next(r for r in tables['temporal_receipts'] if r['namespace']=='weather')
    times=project_times('weather',row,available_at=receipt['available_at'])
    source_sha=_source_hash(row)
    receipt.update(source_key=row['snapshot_id'],source_sha256=source_sha,
        receipt_id=digest(['weather',row['snapshot_id'],source_sha]),times_json=encoded(times),
        **{k:times[k] for k in ('observed_at','published_at','ingested_at','available_at')})
    if bundle_change:bundle_change(body)
    database=Database(root/'data/goal_analysis.sqlite3');database.initialize()
    HistoricalResults(database).initialize();WeatherStore(database).initialize()
    TemporalStore(database,root/'data/raw').initialize();HttpObservations(database,root).initialize()
    for name,rawtext in body['raw_files'].items():
        p=root/'data/raw'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(rawtext.encode())
    with database.transaction() as db:
        for table,rows in tables.items():
            for r in rows:
                columns=','.join(r);marks=','.join('?' for _ in r)
                db.execute(f'INSERT INTO {table} ({columns}) VALUES ({marks})',tuple(r.values()))
    return body,database

def original_venue_bytes(monkeypatch,root):
    """Reuse unchanged fixture bytes; avoid13MB of per-case redundant copying.

    Only byte location changes in tests. Real code pins, hashes, entity claims,
    official pages, release times and HTTP/temporal readers are still executed.
    """
    from goal_analysis.jobs import stadium_locations_shadow as geo
    folder=Path(__file__).parent/'fixtures/stadium-locations-20261022-fourth'
    original=geo._bytes
    def read(actual_folder,digest,suffix,limit):
        assert actual_folder==root/'data/arthur/stadium-locations'
        if suffix=='.json':
            raw=(folder/'reviewed-locations.json').read_bytes()
            if len(raw)>limit or sha256(raw).hexdigest()!=digest:
                raise ValueError('TEST_REQUIRES_ORIGINAL_APPLICABLE_RELEASE')
            return raw
        return original(folder,digest,suffix,limit)
    monkeypatch.setattr(geo,'_bytes',read)
