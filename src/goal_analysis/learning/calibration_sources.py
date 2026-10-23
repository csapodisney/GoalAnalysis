"""Physical, read-only TheStats and archived model dataset bindings."""
from datetime import timedelta
from pathlib import Path
from time import monotonic

from goal_analysis.jobs.the_stats_live import _identity, normalize_history
from goal_analysis.models._data import prepare_history
from goal_analysis.models.datasets import goal_rows, lineage
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.source_observations import read_source_raw
from goal_analysis.storage.training_datasets import DatasetStore


class Sources:
    def __init__(self, root, db, *, dataset_root=None, deadline=None):
        self.root, self.db, self.cache, self.datasets = Path(root), db, {}, {}
        self.dataset_root = Path(dataset_root or self.root/'data/arthur/models/datasets').resolve()
        if not self.dataset_root.is_relative_to(self.root.resolve()):
            raise ValueError('CALIBRATION_DATASET_ROOT_OUTSIDE_PROJECT')
        self.deadline = deadline
        self.historical_cache = {}
        self.read_count = self.read_bytes = self.lineage_rows = 0

    def _budget(self):
        if self.deadline is not None and monotonic() >= self.deadline:
            raise ValueError('CALIBRATION_SOURCE_TIME_BUDGET')

    def raw(self, source, cutoff):
        self._budget()
        key = sha(source), utc(cutoff).isoformat()
        if key in self.cache:
            return self.cache[key]
        self.read_count += 1
        if self.read_count > 2000:
            raise ValueError('CALIBRATION_SOURCE_BUDGET')
        row = self.db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                              (source['content_hash'],)).fetchone()
        if row is None or not 0 < row['size_bytes'] <= 5_000_000:
            raise ValueError('MISSING_OR_OVERSIZED_CALIBRATION_SOURCE')
        self.read_bytes += row['size_bytes']
        if self.read_bytes > 128_000_000:
            raise ValueError('CALIBRATION_TOTAL_SOURCE_BYTE_BUDGET')
        body, proof = read_source_raw(self.db, dict(row), self.root, cutoff, source)
        self.cache[key] = body, proof
        return body, proof

    def historical(self, record, cutoff):
        """Exact original URL/time and normalized record, never nearest/newest."""
        self._budget()
        key = sha(record), utc(cutoff).isoformat()
        if key in self.historical_cache:
            return self.historical_cache[key]
        stamp = utc(record['observed_at']).isoformat(timespec='microseconds')
        url = record['source_id']
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        options = [dict(r) for r in self.db.execute('''SELECT content_hash FROM raw_snapshots
            WHERE provider='the_stats_api' AND source_url=?
            AND julianday(fetched_at)=julianday(?) LIMIT 17''', (url, stamp))]
        if 'the_stats_http_observations' in tables:
            options += [dict(r) for r in self.db.execute('''SELECT content_hash FROM the_stats_http_observations
                WHERE source_url=? AND observed_at=? LIMIT 17''', (url, stamp))]
        hashes = {r['content_hash'] for r in options}
        if len(options) > 32 or len(hashes) != 1:
            raise ValueError('MISSING_OR_AMBIGUOUS_ORIGINAL_TRAINING_SOURCE')
        body, proof = self.raw({'content_hash': next(iter(hashes)), 'source_url': url,
                               'observed_at': stamp}, cutoff)
        data = body.get('data')
        rows = data if isinstance(data, list) else [data]
        if len(rows) > 100:
            raise ValueError('CALIBRATION_SOURCE_ROW_BUDGET')
        candidates = [r for r in rows if isinstance(r, dict)
                      and str(r.get('id')) == record['fixture_id'].removeprefix('the_stats_api:')]
        if len(candidates) != 1:
            raise ValueError('TRAINING_MATCH_NOT_UNIQUE_IN_ORIGINAL_SOURCE')
        row = candidates[0]
        if type(row.get('is_neutral')) is not bool:
            raise ValueError('UNPROVEN_NATIVE_TRAINING_VENUE')
        _, comp, _, _, _ = _identity(row)
        original = normalize_history(row, comp, {'source_url': url, 'observed_at': stamp}, utc(cutoff))
        normalized, _ = prepare_history([original], utc(cutoff), set())
        if sha(lineage(normalized)) != sha([record]):
            raise ValueError('TRAINING_LINEAGE_DIFFERS_FROM_ORIGINAL_RAW')
        self.historical_cache[key] = proof
        return proof

    def training(self, model, version, cutoff):
        captured = model.get('training_datasets') or {}
        manifests = captured.get('manifests')
        uses = captured.get('uses') or []
        if (captured.get('archive', {}).get('status') != 'ARCHIVED'
                or not isinstance(manifests, list) or not 1 <= len(manifests) <= 64
                or not any(r.get('status') == 'FIT_CALL_RETURNED' for r in uses)):
            raise ValueError('MISSING_ORIGINAL_ARCHIVED_TRAINING_DATASETS')
        ids = {m['dataset_id'] for m in manifests}
        if len(ids) != len(manifests) or not {r['dataset_id'] for r in uses} <= ids:
            raise ValueError('AMBIGUOUS_OR_UNBOUND_TRAINING_DATASET')
        receipts = set()
        for manifest in manifests:
            self._budget()
            key = manifest['dataset_id'], sha(manifest), version, utc(cutoff).isoformat()
            if key in self.datasets:
                receipts.update(self.datasets[key])
                continue
            if len(self.datasets) >= 1024:
                raise ValueError('CALIBRATION_DATASET_BUDGET')
            store = DatasetStore(self.dataset_root)
            path = store.path(manifest['dataset_id'])
            if not path.is_file() or path.stat().st_size > 16_000_000:
                raise ValueError('MISSING_OR_OVERSIZED_TRAINING_DATASET')
            dataset = store.read(manifest['dataset_id'])
            if (sha(dataset['manifest']) != sha(manifest) or utc(manifest['as_of']) != utc(cutoff)):
                raise ValueError('ORIGINAL_TRAINING_MANIFEST_OR_CUTOFF_MISMATCH')
            shared = manifest['role'] == 'normalized_history_universe'
            if not shared and manifest['recipe'].get('model_version') != version:
                raise ValueError('TRAINING_MATHEMATICAL_VERSION_MISMATCH')
            origin = dataset['lineage']
            rows = dataset['rows'] if shared else origin.get('matches') if isinstance(origin, dict) else origin
            if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
                raise ValueError('MISSING_BOUNDED_ORIGINAL_TRAINING_LINEAGE')
            self.lineage_rows += len(rows)
            if self.lineage_rows > 100000:
                raise ValueError('CALIBRATION_TOTAL_LINEAGE_BUDGET')
            if not shared and isinstance(origin, list) and sha(dataset['rows']) != sha(goal_rows(rows)):
                raise ValueError('CONSUMED_GOAL_ROWS_DIFFER_FROM_RAW_BOUND_LINEAGE')
            if not shared and isinstance(origin, dict):
                from collections import Counter

                features = origin.get('chronological_features')
                if not isinstance(features, list) or len(features) > 10000:
                    raise ValueError('MISSING_CHRONOLOGICAL_FEATURE_LINEAGE')
                expected = Counter(sha([r['features'], r['label']]) for r in features)
                consumed = Counter(sha(r) for r in dataset['rows'])
                if consumed - expected:
                    raise ValueError('CONSUMED_FEATURE_ROWS_DIFFER_FROM_ORIGINAL_LINEAGE')
            proofs = [self.historical(row, cutoff) for row in rows]
            if version.startswith('kepler-'):
                from goal_analysis.jobs.point_in_time import require_chronological_availability

                enriched = [{**r, **p['times']} for r, p in zip(rows, proofs, strict=True)]
                require_chronological_availability(enriched)
            self.datasets[key] = {p['receipt_id'] for p in proofs}
            receipts.update(self.datasets[key])
        return sorted(ids), sorted(receipts)

    def target(self, leg, cutoff):
        body, proof = self.raw(leg['fixture_source'], cutoff)
        data = body.get('data')
        rows = data if isinstance(data, list) else [data]
        if len(rows) > 100:
            raise ValueError('CALIBRATION_TARGET_BUDGET')
        matches = [r for r in rows if isinstance(r, dict)
                   and 'the_stats_api:' + str(r.get('id')) == leg['fixture_id']]
        if len(matches) != 1 or utc(matches[0]['utc_date']) != utc(leg['kickoff']):
            raise ValueError('FORECAST_TARGET_IDENTITY_MISMATCH')
        _, comp, season, _, _ = _identity(matches[0])
        if (comp, season) != (leg['provider_league_id'], leg['provider_season_id']):
            raise ValueError('FORECAST_TARGET_COMPETITION_OR_SEASON_MISMATCH')
        for side in ('home', 'away'):
            if matches[0][side + '_team']['id'] != leg['provider_' + side + '_team_id']:
                raise ValueError('FORECAST_TARGET_TEAM_MISMATCH')
        if matches[0].get('status') != 'scheduled':
            raise ValueError('FORECAST_TARGET_NOT_SCHEDULED')
        if type(matches[0].get('is_neutral')) is not bool or matches[0]['is_neutral'] is not leg.get('neutral_venue'):
            raise ValueError('FORECAST_TARGET_VENUE_UNPROVEN_OR_CONFLICTING')
        return proof

    def result(self, payload, leg, cutoff):
        from goal_analysis.jobs.the_stats_results import _result

        if (payload.get('provider') != 'the_stats_api'
                or payload.get('provider_match_id') != leg['fixture_id'].removeprefix('the_stats_api:')):
            raise ValueError('NATIVE_RESULT_IDENTITY_REQUIRED')
        body, proof = self.raw(payload['source'], cutoff)
        data = body.get('data')
        rows = data if isinstance(data, list) else [data]
        matches = [r for r in rows if isinstance(r, dict) and r.get('id') == payload['provider_match_id']]
        if (len(matches) != 1 or utc(matches[0]['utc_date']) != utc(leg['kickoff'])
                or matches[0].get('status') != 'finished'
                or utc(leg['kickoff']) + timedelta(minutes=90) > utc(proof['times']['observed_at'])
                or _result(matches[0], payload['source']) != payload):
            raise ValueError('RESULT_NOT_BOUND_TO_ORIGINAL_RAW_AND_PERIOD_SCORE')
        _, comp, season, _, _ = _identity(matches[0])
        if (comp, season) != (leg['provider_league_id'], leg['provider_season_id']):
            raise ValueError('RESULT_COMPETITION_OR_SEASON_MISMATCH')
        for side in ('home', 'away'):
            if matches[0][side + '_team']['id'] != leg['provider_' + side + '_team_id']:
                raise ValueError('RESULT_TEAM_IDENTITY_MISMATCH')
        return proof
