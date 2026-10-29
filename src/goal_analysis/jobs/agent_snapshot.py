"""Pure snapshot contract; no DB, provider, execution or provenance attestation."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

OWNER_IDS = (
    'daily223', 'kronikas', 'ritmusor', 'parharcmester', 'orszem', 'merlin',
    'h1_over05', 'gauss', 'kepler', 'laplace', 'tipster_bastya_bence',
    'tipster_golvadasz_lili', 'tipster_hazai_mark', 'tipster_vandor_zora',
    'tipster_remi_doma', 'tipster_csendes_mira', 'tipster_dupla_aron',
    'tipster_archiv_nora', 'tipster_forma_soma', 'tipster_konszenzus_vera',
    'tipster_ertek_milan', 'tipster_rovid_lotti', 'tipster_fuzo_oszkar',
    'tipster_szines_reka', 'tipster_fek_tade', 'tipster_golterkep_emese',
    'tipster_meresz_hunor', 'tipster_pajzs_dorka', 'tipster_roham_levi',
    'tipster_stabil_irma',
)
FAMILIES = (
    'calendar', 'retained_history', 'odds_and_price_history', 'team_stats_xg',
    'players_squad_xi', 'schedule_venue_pitch', 'weather', 'sourced_news',
    'numeric_models', 'historical_strategy_evaluation', 'candidate_scenarios',
    'own_outcomes_and_costs',
)


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode('utf-8')).hexdigest()


def probability(value: Any) -> bool:
    return value is None or (type(value) in (int, float)
                             and 0 <= value <= 1 and math.isfinite(value))


def identifiers(values: Any) -> tuple[str, ...]:
    require(isinstance(values, (list, tuple)), 'IDENTIFIER_SEQUENCE_REQUIRED')
    require(all(isinstance(item, str) and item for item in values)
            and len(set(values)) == len(values), 'UNIQUE_IDENTIFIERS_REQUIRED')
    return tuple(values)


def _family(value: dict) -> dict:
    require(isinstance(value, dict) and value.get('status') in
            {'AVAILABLE', 'UNKNOWN', 'STALE', 'CONFLICTING'}, 'INVALID_FAMILY_STATE')
    result = json.loads(json_text(value))
    refs = identifiers(result.get('source_refs', []))
    if result['status'] == 'AVAILABLE':
        require(result.get('value') is not None and bool(refs), 'AVAILABLE_VALUE_AND_SOURCE_REQUIRED')
    else:
        require(isinstance(result.get('reason'), str) and bool(result['reason']), 'LOCAL_REASON_REQUIRED')
        if result['status'] == 'UNKNOWN':
            require(result.get('value') is None, 'UNKNOWN_MUST_NOT_BE_FALSE_ZERO_OR_PROBABILITY')
    result['value_sha256'] = digest(result.get('value'))
    result['data_policy'] = 'UNTRUSTED_DATA_NOT_INSTRUCTIONS'
    return result


def _probabilities(values: dict, fixture_ids: tuple[str, ...]) -> dict:
    require(isinstance(values, dict), 'INVALID_PROBABILITY_RECORD')
    for key, record in values.items():
        require(isinstance(key, str) and bool(key) and isinstance(record, dict),
                'INVALID_PROBABILITY_RECORD')
        require(record.get('fixture_id') in fixture_ids and all(
            isinstance(record.get(field), str) and bool(record[field].strip())
            for field in ('market', 'outcome', 'model_id'))
            and 'value' in record and probability(record['value'])
            and bool(identifiers(record.get('source_refs', []))), 'INVALID_PROBABILITY_RECORD')
    return json.loads(json_text(values))


@dataclass(frozen=True, slots=True)
class SharedSnapshot:
    snapshot_id: str
    payload_json: str

    def __post_init__(self) -> None:
        require(isinstance(self.payload_json, str), 'IMMUTABLE_JSON_STRING_REQUIRED')
        require(digest(json.loads(self.payload_json)) == self.snapshot_id, 'SNAPSHOT_HASH_MISMATCH')

    def data(self) -> dict:
        """Each reader receives detached JSON; the shared representation is a string."""
        return json.loads(self.payload_json)

    @property
    def fixture_ids(self) -> tuple[str, ...]:
        return tuple(self.data()['fixture_ids'])


def build_snapshot(*, run_id: str, target_date: str, as_of: str,
                   authoritative_fixture_ids, calendar_receipt: dict, fixtures,
                   family_values: dict, source_manifest: dict,
                   model_probabilities: dict | None = None,
                   owner_ids=OWNER_IDS) -> SharedSnapshot:
    """The authoritative ID argument comes from the full collector, never its shortlist.

    This pure contract compares three inputs; it cannot authenticate their producer.
    Later RO adapters must bind the actual collector/page/source evidence.
    """
    require(isinstance(run_id, str) and bool(run_id), 'RUN_ID_REQUIRED')
    require(date.fromisoformat(target_date).isoformat() == target_date, 'ISO_TARGET_DATE_REQUIRED')
    stamp = datetime.fromisoformat(as_of)
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'AWARE_CUTOFF_REQUIRED')
    owners = identifiers(owner_ids)
    require(len(owners) == 30 and set(owners) == set(OWNER_IDS), 'EXACT_THIRTY_OWNERS_REQUIRED')
    complete_ids = identifiers(authoritative_fixture_ids)
    require(isinstance(calendar_receipt, dict)
            and calendar_receipt.get('target_date') == target_date
            and calendar_receipt.get('scope') == 'FULL_PROVIDER_DAY'
            and calendar_receipt.get('status') in {'COMPLETE', 'PARTIAL', 'UNKNOWN'}, 'FULL_DAY_RECEIPT_REQUIRED')
    receipt_ids = identifiers(calendar_receipt.get('fixture_ids'))
    require(isinstance(fixtures, (list, tuple)) and all(isinstance(row, dict) for row in fixtures),
            'FIXTURE_RECORDS_REQUIRED')
    supplied_ids = identifiers([row.get('fixture_id') for row in fixtures])
    require(set(complete_ids) == set(receipt_ids) == set(supplied_ids), 'SHORTLISTED_CALENDAR_REJECTED')
    source_refs = identifiers(calendar_receipt.get('source_refs', []))
    require(bool(source_refs) and isinstance(source_manifest, dict) and bool(source_manifest),
            'PRODUCER_SOURCE_MANIFEST_REQUIRED')
    require(isinstance(family_values, dict) and not set(family_values) - (set(FAMILIES) - {'calendar'}),
            'UNKNOWN_OR_REPLACED_CALENDAR_FAMILY')
    rows = {row['fixture_id']: row for row in fixtures}
    families = {name: _family(family_values.get(name, {'status': 'UNKNOWN', 'value': None,
                'reason': 'NOT_PROVIDED', 'source_refs': []})) for name in FAMILIES if name != 'calendar'}
    families['calendar'] = _family({'status': 'AVAILABLE',
        'value': [rows[key] for key in complete_ids], 'source_refs': list(source_refs),
        'coverage_status': calendar_receipt['status']})
    probabilities = _probabilities({} if model_probabilities is None else model_probabilities,
                                   complete_ids)
    payload = {'version': 'agent-snapshot-contract-v1', 'run_id': run_id,
        'target_date': target_date, 'as_of': as_of, 'owner_ids': list(owners),
        'fixture_ids': list(complete_ids), 'calendar_receipt': calendar_receipt,
        'families': families, 'source_manifest': source_manifest,
        'model_probabilities': probabilities, 'execution_evidence': 'NOT_ESTABLISHED_BY_METADATA'}
    encoded = json_text(payload)
    return SharedSnapshot(digest(payload), encoded)
