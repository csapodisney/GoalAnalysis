"""Self-contained public controls; original private archive tests stay intact."""
import json
import re
import socket
import subprocess
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
import stadium_public_fixture as fixture

from goal_analysis.jobs import stadium_locations_shadow as geo
from goal_analysis.normalization.canonical import utc


def test_curated_fixture_is_explicit_distinct_readable_projection_with_exact_lineage():
    packet, raw, blobs = fixture.load()
    original = json.loads((fixture.ORIGINAL / 'reviewed-locations.json').read_bytes())
    assert packet['test_only'] is True and len(packet['records']) == 69
    assert fixture.PUBLIC_PACKET != fixture.ORIGINAL_PACKET
    assert fixture.PROTECTED not in blobs and fixture.PUBLIC_IDENTITY in blobs
    assert not raw.startswith((b'\x1f\x8b', b'UEsDB', b'H4sI'))
    assert json.loads(blobs[fixture.PUBLIC_IDENTITY])['fixture_kind'].startswith('CURATED_TESTONLY')
    fields = ('wikidata_id', 'native_name', 'native_city', 'coordinate_statement', 'lastrevid', 'alias_note')
    assert [{k: r[k] for k in fields} for r in packet['records']] == [{k: r[k] for k in fields} for r in original['records']]
    assert packet['available_at'] == original['available_at']
    assert utc(packet['test_fixture_lineage']['curated_at']) > utc(packet['available_at'])
    assert packet['test_fixture_lineage']['clock_fields'] == 'COPIED_HISTORICAL_TEST_SCENARIOS_NOT_NEW_OBSERVATION_RECEIPTS'


def test_unreported_original_source_lengths_stay_absent_instead_of_becoming_zero():
    original = json.loads((fixture.ORIGINAL / 'reviewed-locations.json').read_bytes())
    packet, _, blobs = fixture.load()
    missing = [(i, key) for i, record in enumerate(original['records'])
               for key in ('wikidata_source', 'identity_source') if 'size_bytes' not in record[key]
               and record[key]['sha256'] != fixture.PROTECTED]
    assert missing
    for i, key in missing:
        source = packet['records'][i][key]
        assert 'size_bytes' not in source
        assert sha256(blobs[source['sha256']]).hexdigest() == source['sha256']
        assert len(blobs[source['sha256']]) > 0
    projection, = [r['identity_source'] for r in packet['records']
                   if r['identity_source']['sha256'] == fixture.PUBLIC_IDENTITY]
    assert projection['size_bytes'] == len(blobs[fixture.PUBLIC_IDENTITY]) == 381


def test_public_controls_never_read_private_html_even_when_local_archive_exists(tmp_path, monkeypatch):
    original = Path.read_bytes

    def forbid_private(path):
        assert path.name != fixture.PROTECTED + '.bin', 'Private HTML must not be opened by public controls'
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', forbid_private)
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
    monkeypatch.setattr(subprocess, 'Popen', Mock(side_effect=AssertionError('NO_CHILD_PROCESS')))
    packet, folder = fixture.install(tmp_path, monkeypatch, pin_test_only=True)
    rows = geo.read_locations(tmp_path, as_of=utc(packet['available_at']))
    assert len(rows) == 69
    assert (folder / (fixture.PROTECTED + '.bin')).exists() is False
    nuremberg = rows[('Max-Morlock-Stadion', 'Nuremberg')]
    assert nuremberg['payload']['venue_id'] == 'wikidata:Q152599'
    assert nuremberg['payload']['bundle_sha256'] == fixture.PUBLIC_PACKET
    assert nuremberg['source_receipts'][1]['content_sha256'] == fixture.PUBLIC_IDENTITY
    assert ('Max-Morlock-Stadion', 'Nuremberg-guess') not in rows


def test_new_packet_is_rejected_under_unchanged_production_pin(tmp_path, monkeypatch):
    pin = geo.REVIEWED_BUNDLE
    assert pin == fixture.ORIGINAL_PACKET
    packet, _ = fixture.install(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='MISSING_OR_OVERSIZED_STADIUM_SOURCE'):
        geo.read_locations(tmp_path, as_of=utc(packet['available_at']))
    assert geo.REVIEWED_BUNDLE == pin


def test_curated_bytes_never_pass_as_original_private_hash(tmp_path, monkeypatch):
    _, folder = fixture.install(tmp_path, monkeypatch, pin_test_only=True)
    (folder / (fixture.PROTECTED + '.bin')).write_bytes((fixture.PUBLIC / 'identity-projection.json').read_bytes())
    with pytest.raises(ValueError, match='STADIUM_SOURCE_HASH_MISMATCH'):
        geo._bytes(folder, fixture.PROTECTED, '.bin', 2_000_000)


@pytest.mark.parametrize('fault', ['packet_missing', 'packet_corrupt', 'packet_oversize',
                                  'identity_missing', 'identity_corrupt', 'identity_oversize',
                                  'geography_missing', 'geography_corrupt'])
def test_original_strict_hash_and_byte_guards_apply_to_curated_packet(tmp_path, monkeypatch, fault):
    packet, folder = fixture.install(tmp_path, monkeypatch, pin_test_only=True)
    digest, suffix = (fixture.PUBLIC_PACKET, '.json') if fault.startswith('packet') else (
        fixture.PUBLIC_IDENTITY if fault.startswith('identity') else packet['records'][0]['wikidata_source']['sha256'], '.bin')
    path = folder / (digest + suffix)
    if fault.endswith('missing'):
        path.unlink()
    elif fault.endswith('corrupt'):
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path.write_bytes(b' ' * (110001 if suffix == '.json' else 2000001))
    with pytest.raises(ValueError):
        geo.read_locations(tmp_path, as_of=utc(packet['available_at']))


@pytest.mark.parametrize('fault', ['wrong_coordinate', 'wrong_revision', 'future_source', 'future_available',
                                  'wrong_datum', 'ambiguous_alias', 'bad_precision', 'expired'])
def test_curated_input_does_not_relax_geography_identity_or_point_in_time_guards(tmp_path, monkeypatch, fault):
    packet, folder = fixture.install(tmp_path, monkeypatch, pin_test_only=True)
    packet = deepcopy(packet)
    cutoff = utc(packet['available_at'])
    record = packet['records'][0]
    if fault == 'wrong_coordinate':
        record['coordinate_statement']['latitude'] = 0
    elif fault == 'wrong_revision':
        record['lastrevid'] += 1
    elif fault == 'future_source':
        record['identity_source']['observed_at'] = (cutoff + timedelta(seconds=1)).isoformat()
    elif fault == 'future_available':
        packet['available_at'] = (cutoff + timedelta(seconds=1)).isoformat()
    elif fault == 'wrong_datum':
        record['coordinate_statement']['globe'] = 'http://www.wikidata.org/entity/Q111'
    elif fault == 'ambiguous_alias':
        packet['records'][1]['native_name'] = record['native_name']
        packet['records'][1]['native_city'] = record['native_city']
    elif fault == 'bad_precision':
        record['coordinate_statement']['precision'] = 1
    else:
        cutoff += timedelta(days=91)
    raw = json.dumps(packet, sort_keys=True, separators=(',', ':')).encode()
    digest = sha256(raw).hexdigest()
    (folder / (digest + '.json')).write_bytes(raw)
    monkeypatch.setattr(geo, 'REVIEWED_BUNDLE', digest)
    with pytest.raises((ValueError, KeyError)):
        geo.read_locations(tmp_path, as_of=cutoff)


@pytest.mark.parametrize('fault', ['packet', 'identity', 'lineage'])
def test_public_loader_refuses_tampered_projection_or_lineage(tmp_path, monkeypatch, fault):
    for path in fixture.PUBLIC.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    name = {'packet': 'reviewed-locations-testonly.json', 'identity': 'identity-projection.json', 'lineage': 'LINEAGE.json'}[fault]
    path = tmp_path / name
    if fault == 'lineage':
        row = json.loads(path.read_bytes())
        row['production_release'] = True
        path.write_text(json.dumps(row), encoding='utf-8')
    else:
        path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(fixture, 'PUBLIC', tmp_path)
    with pytest.raises(AssertionError):
        fixture.load()


def test_installer_refuses_production_roots_or_uncontrolled_use(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='PYTEST_MONKEYPATCH'):
        fixture.install(tmp_path, None)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/arthur-settings.json').write_text('{}')
    with pytest.raises(ValueError, match='PRODUCTION_ROOT'):
        fixture.install(tmp_path, monkeypatch, pin_test_only=True)
    assert not (tmp_path / 'data').exists()


def test_curated_public_text_has_no_credential_shapes_or_encoded_private_blob():
    files = [p for p in fixture.PUBLIC.iterdir() if p.is_file()]
    pattern = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
    assert len(files) == 4
    assert sum(p.stat().st_size for p in files) < 110000
    for path in files:
        raw = path.read_bytes()
        assert not pattern.search(raw)
        raw.decode('utf-8')
    assert (fixture.PUBLIC / 'identity-projection.json').stat().st_size == 381
