"""Per-run dataset capture. This module never changes the model inputs."""
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from goal_analysis.normalization.training_datasets import snapshot

_CURRENT = ContextVar('arthur_training_dataset_capture', default=None)


class Capture:
    def __init__(self):
        self.datasets, self.uses = {}, []

    def __enter__(self):
        self.token = _CURRENT.set(self)
        return self

    def __exit__(self, *error):
        _CURRENT.reset(self.token)

    def manifests(self, profile):
        identifiers = sorted({row['dataset_id'] for row in self.uses if row['profile'] in {profile, 'shared'}})
        return [self.datasets[identifier]['manifest'] for identifier in identifiers]


def record(profile, slot, rows, now, *, recipe, lineage):
    capture = _CURRENT.get()
    if capture is None:
        return None
    value = snapshot(rows, role=slot, as_of=now, recipe=recipe, lineage=lineage)
    identifier = value['manifest']['dataset_id']
    capture.datasets[identifier] = value
    use = {'profile': profile, 'slot': slot, 'dataset_id': identifier, 'status': 'OBSERVED'}
    capture.uses.append(use)
    return use


def recipe(module_file, version, **parameters):
    return {'model_version': version,
        'implementation_sha256': hashlib.sha256(Path(module_file).read_bytes()).hexdigest(),
        'history_preparation_sha256': hashlib.sha256(Path(__file__).with_name('_data.py').read_bytes()).hexdigest(),
        'parameters': parameters}


def goal_rows(sample):
    return [[r['full_time_home'], r['full_time_away'], r['home_team_id'], r['away_team_id']] for r in sample]


def lineage(sample):
    return [{k:v for k,v in row.items() if not k.startswith('_')} for row in sample]


@contextmanager
def fitting(profile, slot, rows, now, *, recipe, lineage):
    use = record(profile, slot, rows, now, recipe=recipe, lineage=lineage)
    try:
        yield
    except Exception as error:
        if use is not None:
            use.update(status='FIT_CALL_FAILED', error_type=type(error).__name__)
        raise
    else:
        if use is not None:
            use['status'] = 'FIT_CALL_RETURNED'
