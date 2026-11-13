"""Synthetic exact-alias release controls; no live source, DB, or provider calls."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from goal_analysis.jobs import stadium_locations_shadow as geo

AT = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)


def _packet(root, *, aliases, at, duplicate_coordinates=False):
    folder = root / 'data' / 'arthur' / 'stadium-locations'
    folder.mkdir(parents=True, exist_ok=True)
    point = {'latitude': 48.4, 'longitude': -4.46, 'altitude': None,
             'precision': .0001, 'globe': 'http://www.wikidata.org/entity/Q2'}
    claim = {'rank': 'normal', 'mainsnak': {'snaktype': 'value',
             'datavalue': {'value': point}}}
    entity = {'entities': {'Q1': {'id': 'Q1', 'lastrevid': 1,
              'claims': {'P625': [claim, deepcopy(claim)] if duplicate_coordinates else [claim]}}}}
    sources = []
    for url, raw in (
        ('https://www.wikidata.org/wiki/Special:EntityData/Q1.json',
         json.dumps(entity, sort_keys=True).encode()),
        ('https://testonly.invalid/facility', b'TESTONLY stadium identity fixture'),
    ):
        digest = sha256(raw).hexdigest()
        (folder / (digest + '.bin')).write_bytes(raw)
        sources.append({'requested_url': url, 'source_url': url,
                        'observed_at': (at - timedelta(seconds=1)).isoformat(),
                        'sha256': digest, 'size_bytes': len(raw), 'status': 200})
    row = {'wikidata_id': 'Q1', 'lastrevid': 1, 'coordinate_statement': point,
           'wikidata_source': sources[0], 'identity_source': sources[1],
           'native_name': 'TESTONLY Stadium', 'native_city': 'TESTONLY City',
           'quality': 'SYNTHETIC_TESTONLY', 'alias_note': 'EXPLICIT_TESTONLY_ALIAS'}
    records = [{**deepcopy(row), 'native_name': name, 'native_city': city} for name, city in aliases]
    packet = {'version': geo.VERSION, 'license': 'CC0-1.0', 'available_at': at.isoformat(),
              'reviewed_at': at.isoformat(), 'local_review_max_age_days': 90, 'records': records}
    raw = json.dumps(packet, sort_keys=True).encode()
    digest = sha256(raw).hexdigest()
    (folder / (digest + '.json')).write_bytes(raw)
    return digest, len(records), at.isoformat(), len(raw)


def test_stadium_new_alias_is_exact_name_and_city(tmp_path, monkeypatch):
    release = _packet(tmp_path, aliases=[('TESTONLY Stadium', 'TESTONLY City')], at=AT)
    monkeypatch.setattr(geo, 'ADDITIONAL_REVIEWED_RELEASES', (release,))
    locations = geo.read_locations(tmp_path, as_of=AT)
    assert set(locations) == {('TESTONLY Stadium', 'TESTONLY City')}
    assert ('TESTONLY Stadium', 'Other City') not in locations
    assert ('TESTONLY Stadum', 'TESTONLY City') not in locations
    assert locations[('TESTONLY Stadium', 'TESTONLY City')]['payload']['coordinate_scope'] == 'VERIFIED_STADIUM'


def test_stadium_new_release_does_not_backdate_alias_or_source_clock(tmp_path, monkeypatch):
    old = _packet(tmp_path, aliases=[('TESTONLY Old', 'TESTONLY City')], at=AT)
    later = AT + timedelta(minutes=1)
    new = _packet(tmp_path, aliases=[('TESTONLY Old', 'TESTONLY City'),
                                   ('TESTONLY New', 'TESTONLY City')], at=later)
    monkeypatch.setattr(geo, 'ADDITIONAL_REVIEWED_RELEASES', (new, old))
    before = geo.read_locations(tmp_path, as_of=later - timedelta(microseconds=1))
    after = geo.read_locations(tmp_path, as_of=later)
    assert set(before) == {('TESTONLY Old', 'TESTONLY City')}
    assert set(after) == {('TESTONLY Old', 'TESTONLY City'), ('TESTONLY New', 'TESTONLY City')}
    assert after[('TESTONLY New', 'TESTONLY City')]['times']['available_at'] == later.isoformat()
    assert after[('TESTONLY New', 'TESTONLY City')]['times']['published_at'] is None


def test_stadium_two_coordinate_claims_stay_rejected(tmp_path, monkeypatch):
    release = _packet(tmp_path, aliases=[('TESTONLY Stadium', 'TESTONLY City')],
                      at=AT, duplicate_coordinates=True)
    monkeypatch.setattr(geo, 'ADDITIONAL_REVIEWED_RELEASES', (release,))
    with pytest.raises(ValueError, match='AMBIGUOUS_OR_INAPPLICABLE_STADIUM_COORDINATES'):
        geo.read_locations(tmp_path, as_of=AT)

