"""Deterministic descriptions of exact consumed rows, without fitting or I/O."""
import hashlib
import json
import math
from datetime import datetime

from .canonical import utc

VERSION = 'training-dataset-v1'


def canonical(value):
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec='microseconds')
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        if {'api_key', 'password', 'authorization', 'secret', 'token'} & {key.casefold() for key in value}:
            raise ValueError('SENSITIVE_DATASET_FIELD')
        return {key: canonical(item) for key, item in value.items()}
    raise ValueError('INVALID_DATASET_VALUE')


def encoded(value):
    return json.dumps(canonical(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def snapshot(rows, *, role, as_of, recipe, lineage):
    if not isinstance(rows, list) or not isinstance(recipe, dict) or not recipe:
        raise ValueError('DATASET_ROWS_AND_RECIPE_REQUIRED')
    if not isinstance(role, str) or not role or not isinstance(lineage, (dict, list)):
        raise ValueError('DATASET_ROLE_AND_LINEAGE_REQUIRED')
    values, recipe, lineage = canonical(rows), canonical(recipe), canonical(lineage)
    manifest = {'version': VERSION, 'role': role, 'as_of': utc(as_of).isoformat(timespec='microseconds'),
        'row_count': len(values), 'rows_sha256': sha(values), 'recipe': recipe,
        'lineage_sha256': sha(lineage), 'ordering': 'EXACT_CONSUMPTION_ORDER'}
    manifest['dataset_id'] = sha(manifest)
    return {'manifest': manifest, 'rows': values, 'lineage': lineage}


def validate(value):
    if not isinstance(value, dict) or set(value) != {'manifest', 'rows', 'lineage'}:
        raise ValueError('INVALID_DATASET_SNAPSHOT')
    manifest = value['manifest']
    if not isinstance(manifest, dict) or set(manifest) != {
            'version', 'role', 'as_of', 'row_count', 'rows_sha256', 'recipe',
            'lineage_sha256', 'ordering', 'dataset_id'}:
        raise ValueError('INVALID_DATASET_MANIFEST')
    rebuilt = snapshot(value['rows'], role=manifest['role'], as_of=manifest['as_of'],
                       recipe=manifest['recipe'], lineage=value['lineage'])
    if encoded(rebuilt) != encoded(value):
        raise ValueError('DATASET_HASH_OR_SCHEMA_MISMATCH')
    return rebuilt
