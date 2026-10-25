"""Sixth original source release preserves prior clocks, bounds and provenance."""

import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from test_native_venue_shadow import NOW, environment
from test_stadium_location_releases import install_original_release
from test_stadium_locations_shadow import sources

from goal_analysis.jobs import stadium_locations_shadow as geo
from goal_analysis.normalization.canonical import utc

FIXTURES = Path(__file__).parent / 'fixtures/stadium-locations-20261024-sixth'
FIFTH = Path(__file__).parent / 'fixtures/stadium-locations-20261023-fifth'
PACKET = json.loads((FIXTURES / 'reviewed-locations.json').read_bytes())
AT = utc(PACKET['available_at'])
SIXTH_SHA = '92d68ac3f097bf6a3cb0abef945e94ce2f49e05ef42dc5d7aa7cd480dd1e3dc7'
FIFTH_SHA = '6a6ef106af3d782708df8df56aa5ce533c99302f3b7fb41eab858c21a66a9ceb'
NEW = [
    ('Ewood Park', 'Blackburn', 'Q245085'),
    ('Unipol Domus', 'Cagliari', 'Q30880646'),
    ('Olympiastadion', 'Berlin', 'Q151374'),
    ('Sportpark Höhenberg', 'Cologne', 'Q2312740'),
    ('Signal Iduna Park', 'Dortmund', 'Q150928'),
    ('London Stadium', 'London', 'Q172190'),
    ('Max-Morlock-Stadion', 'Nuremberg', 'Q152599'),
    ('Hänsch-Arena', 'Meppen', 'Q1671611'),
    ('Voith-Arena', 'Heidenheim an der Brenz', 'Q548138'),
    ('WWK ARENA', 'Augsburg', 'Q167418'),
    ('Stamford Bridge', 'London', 'Q171458'),
    ('Deepdale', 'Preston', 'Q1141958'),
    ('Bramall Lane', 'Sheffield', 'Q897533'),
]


def install(root):
    return install_original_release(root, FIXTURES.name)[1]


@pytest.mark.parametrize('name,city,qid', NEW)
def test_new_stadium_resolves_exact_alias_with_original_source_times(tmp_path, name, city, qid):
    install(tmp_path)
    result = geo.read_locations(tmp_path, as_of=AT)
    row = result[(name, city)]
    assert len(result) == 69
    assert row['payload']['venue_id'] == 'wikidata:' + qid
    assert row['payload']['bundle_sha256'] == SIXTH_SHA
    assert (name, city + '-guess') not in result
    original = next(r for r in PACKET['records'] if r['wikidata_id'] == qid)
    expected = [original[k] for k in ('wikidata_source', 'identity_source')]
    assert len(row['source_receipts']) == 2
    for receipt, source in zip(row['source_receipts'], expected, strict=True):
        assert receipt['content_sha256'] == source['sha256']
        assert receipt['url'] == source['source_url']
        assert receipt['times']['observed_at'] == source['observed_at']
        assert receipt['times']['available_at'] == PACKET['available_at']
        assert utc(source['observed_at']) < AT


def test_sixth_packet_keeps_all_fifth_records_and_original_raw_bytes():
    raw = (FIXTURES / 'reviewed-locations.json').read_bytes()
    old_raw = (FIFTH / 'reviewed-locations.json').read_bytes()
    assert sha256(raw).hexdigest() == SIXTH_SHA == geo.REVIEWED_BUNDLE
    assert sha256(old_raw).hexdigest() == FIFTH_SHA
    assert len(raw) == 103_344 and 80_000 < len(raw) <= 110_000
    assert PACKET['records'][:56] == json.loads(old_raw)['records']
    assert PACKET['reviewed_at'] == PACKET['available_at'] == geo.REVIEWED_AT
    assert len({(r['native_name'], r['native_city']) for r in PACKET['records']}) == 69
    for record in PACKET['records']:
        for key in ('wikidata_source', 'identity_source'):
            digest = record[key]['sha256']
            assert sha256((FIXTURES / (digest + '.bin')).read_bytes()).hexdigest() == digest
    for record in PACKET['records'][:56]:
        for key in ('wikidata_source', 'identity_source'):
            name = record[key]['sha256'] + '.bin'
            assert (FIXTURES / name).read_bytes() == (FIFTH / name).read_bytes()


@pytest.mark.parametrize('delta,count', [(-1, 56), (0, 69), (1, 69)])
def test_sixth_availability_boundary_preserves_fifth_snapshot(tmp_path, delta, count):
    install_original_release(tmp_path, FIFTH.name)
    install(tmp_path)
    result = geo.read_locations(tmp_path, as_of=AT + timedelta(microseconds=delta))
    assert len(result) == count
    assert (('Ewood Park', 'Blackburn') in result) == (count == 69)
    expected = SIXTH_SHA if count == 69 else FIFTH_SHA
    assert {r['payload']['bundle_sha256'] for r in result.values()} == {expected}


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'oversize'])
def test_bad_sixth_release_never_falls_back_to_fifth(tmp_path, fault):
    install_original_release(tmp_path, FIFTH.name)
    path = install(tmp_path)
    if fault == 'missing':
        path.unlink()
    elif fault == 'corrupt':
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path.write_bytes(b' ' * 110_001)
    with pytest.raises(ValueError):
        geo.read_locations(tmp_path, as_of=AT)


def test_sixth_uses_its_measured_bound_and_preserves_fifth_cap(tmp_path, monkeypatch):
    install_original_release(tmp_path, FIFTH.name)
    path = install(tmp_path)
    original = geo._bytes
    limits = []

    def tracked(folder, digest, suffix, limit):
        if suffix == '.json':
            limits.append((digest, limit))
        return original(folder, digest, suffix, limit)

    monkeypatch.setattr(geo, '_bytes', tracked)
    assert len(geo.read_locations(tmp_path, as_of=AT)) == 69
    path.write_bytes(b'future corrupt release')
    assert len(geo.read_locations(tmp_path, as_of=AT - timedelta(microseconds=1))) == 56
    assert limits == [(SIXTH_SHA, 110_000), (FIFTH_SHA, 80_000)]


@pytest.mark.parametrize('qid', ['Q245085', 'Q151374', 'Q171458', 'Q1141958', 'Q897533'])
def test_five_unreported_precisions_remain_unknown(tmp_path, qid):
    install(tmp_path)
    record = next(r for r in PACKET['records'] if r['wikidata_id'] == qid)
    row = geo.read_locations(tmp_path, as_of=AT)[(record['native_name'], record['native_city'])]
    assert record['coordinate_statement']['precision'] is None
    assert row['payload']['reported_precision_degrees'] is None
    assert row['payload']['coordinate_precision_status'] == 'UNREPORTED'
    assert row['payload']['precision_is_not_accuracy_radius'] is True


def test_publisher_kinds_are_not_relabeled_and_rejected_entities_stay_out():
    records = {r['wikidata_id']: r for r in PACKET['records'][56:]}
    assert set(records) == {qid for _, _, qid in NEW}
    assert not {'Q1122869', 'Q15755'} & set(records)
    assert records['Q245085']['identity_source_kind'] == 'DESTINATION_PAGE'
    assert records['Q151374']['identity_source_kind'] == 'VENUE_OPERATOR'
    assert records['Q172190']['identity_source_kind'] == 'VENUE_OPERATOR'
    assert records['Q171458']['identity_source_kind'] == 'OFFICIAL_VISITING_CLUB_CPFC'
    assert records['Q1141958']['identity_source_kind'] == 'VENUE_EVENT_OPERATOR_SODEXO'
    assert records['Q245085']['identity_source']['status'] == 'UNREVIEWED_NOT_ADMITTED'
    assert records['Q245085']['identity_source']['http_status'] == 200
    assert records['Q171458']['identity_source']['status'] == 200


@pytest.mark.parametrize('name,city,qid', [NEW[0], NEW[9], NEW[10]])
def test_new_facility_binds_to_exact_http_witnessed_match(tmp_path, monkeypatch, name, city, qid):
    data, _, _, _ = environment(
        tmp_path, http=True, pointer=True,
        change=lambda match: match['venue'].update(name=name, city=city),
    )
    sources(tmp_path, monkeypatch)
    fixture = data['fixtures'][0]
    result = geo.fixture_location(tmp_path, fixture, as_of=NOW)
    assert result['payload']['venue_id'] == 'wikidata:' + qid
    assert result['payload']['binding_basis'] == 'EXACT_WITNESSED_MATCH_DETAIL_AND_REVIEWED_FACILITY_ALIAS'
    assert len(result['source_receipts']) == 3
    stale = geo.fixture_location(tmp_path, fixture, as_of=NOW + timedelta(minutes=31))
    assert stale is None


@pytest.mark.parametrize('kind', ['wikidata_source', 'identity_source'])
def test_new_source_tampering_is_rejected(tmp_path, kind):
    install(tmp_path)
    digest = PACKET['records'][56][kind]['sha256']
    path = tmp_path / 'data/arthur/stadium-locations' / (digest + '.bin')
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='STADIUM_SOURCE_HASH_MISMATCH'):
        geo.read_locations(tmp_path, as_of=AT)
