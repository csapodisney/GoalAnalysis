"""Versioned fictional characters, loaded once from a pinned immutable rulebook."""
from copy import deepcopy

from .profile_config import CATALOG_VERSION, freeze, load_configuration

__all__ = ['CATALOG_VERSION', 'TIPSTER_BY_ID', 'TIPSTER_CATALOG', 'TIPSTER_PROFILE_IDS', 'get_catalogue']

_CONFIGURATION = load_configuration()
TIPSTER_CATALOG = _CONFIGURATION['profiles']
TIPSTER_PROFILE_IDS = tuple(p['profile_id'] for p in TIPSTER_CATALOG)
TIPSTER_BY_ID = freeze({p['profile_id']: p for p in TIPSTER_CATALOG})


def get_catalogue():
    """Detached JSON-compatible display data; shared mathematical policy stays fixed."""
    return deepcopy(TIPSTER_CATALOG)
