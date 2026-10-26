"""Explicit TESTONLY fixture installer; never substitute private archive bytes."""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

PUBLIC = Path(__file__).parent / 'fixtures/stadium-locations-testonly-curated-20261026'
ORIGINAL = Path(__file__).parent / 'fixtures/stadium-locations-20261024-sixth'
PROTECTED = 'a51be549ec36777ac012cd53f8d7a743d53e8e1791595c62dc0454e108c3c6ba'
ORIGINAL_PACKET = '92d68ac3f097bf6a3cb0abef945e94ce2f49e05ef42dc5d7aa7cd480dd1e3dc7'
PUBLIC_PACKET = '1cca986ad758a3e0f51fa9a94975187b6770943254ddac39b2d235f06e2baa1f'
PUBLIC_IDENTITY = '0e250eff055644fcb148d43661283220988ad733ebd2616ddd76212d7932d012'


def load():
    """Validate readable public derivation against already tracked metadata."""
    original_raw = (ORIGINAL / 'reviewed-locations.json').read_bytes()
    packet_raw = (PUBLIC / 'reviewed-locations-testonly.json').read_bytes()
    identity_raw = (PUBLIC / 'identity-projection.json').read_bytes()
    lineage = json.loads((PUBLIC / 'LINEAGE.json').read_bytes())
    assert sha256(original_raw).hexdigest() == ORIGINAL_PACKET
    assert sha256(packet_raw).hexdigest() == PUBLIC_PACKET and len(packet_raw) == 103767
    assert sha256(identity_raw).hexdigest() == PUBLIC_IDENTITY and len(identity_raw) == 381
    original, packet, identity = map(json.loads, (original_raw, packet_raw, identity_raw))
    expected = deepcopy(original)
    record, = [r for r in expected['records'] if r['identity_source']['sha256'] == PROTECTED]
    kind = 'CURATED_TESTONLY_METADATA_PROJECTION_NOT_HTTP_RESPONSE'
    assert identity == {'fixture_kind': kind, 'native_name': record['native_name'],
                        'native_city': record['native_city'], 'wikidata_id': record['wikidata_id'],
                        'original_reviewed_identity_url': record['identity_source']['source_url'],
                        'original_private_source_sha256': PROTECTED}
    record['identity_source'].update(sha256=PUBLIC_IDENTITY, size_bytes=381, fixture_kind=kind)
    expected['test_only'] = True
    expected['test_fixture_lineage'] = {
        'original_reviewed_packet_sha256': ORIGINAL_PACKET, 'curated_at': lineage['created_at'],
        'clock_fields': 'COPIED_HISTORICAL_TEST_SCENARIOS_NOT_NEW_OBSERVATION_RECEIPTS',
        'production_release': False, 'original_raw_read_by_builder': False}
    assert packet == expected
    assert lineage['fixture_kind'] == 'CURATED_TESTONLY' and lineage['production_release'] is False
    assert lineage['protected_html_read_or_encoded'] is False and lineage['external_requests'] == 0
    assert lineage['original_packet_sha256'] == ORIGINAL_PACKET
    assert lineage['original_private_source_sha256'] == PROTECTED
    assert lineage['curated_packet_sha256'] == PUBLIC_PACKET and lineage['curated_packet_size_bytes'] == len(packet_raw)
    assert lineage['curated_identity_sha256'] == PUBLIC_IDENTITY and lineage['curated_identity_size_bytes'] == len(identity_raw)
    assert lineage['modified_record_count'] == 1 and lineage['preserved_record_count'] == 68
    assert lineage['affected_entity'] == {k: record[k] for k in ('wikidata_id', 'native_name', 'native_city')}
    blobs = {}
    for r in packet['records']:
        for key in ('wikidata_source', 'identity_source'):
            source = r[key]
            digest = source['sha256']
            assert digest != PROTECTED  # No private-body read, even if locally present.
            if digest not in blobs:
                body = identity_raw if digest == PUBLIC_IDENTITY else (ORIGINAL / (digest + '.bin')).read_bytes()
                assert sha256(body).hexdigest() == digest and len(body) <= 2000000
                if 'size_bytes' in source:
                    assert len(body) == source['size_bytes']
                blobs[digest] = body
    return packet, packet_raw, blobs


def install(root, monkeypatch, *, pin_test_only=False):
    """Install only distinct hashes in an isolated test root; explicit pin opt-in."""
    root = Path(root).resolve()
    if root == Path(__file__).resolve().parents[1] or (root / 'config/arthur-settings.json').exists():
        raise ValueError('TEST_FIXTURE_MUST_NOT_BE_INSTALLED_IN_PRODUCTION_ROOT')
    if monkeypatch is None:
        raise ValueError('EXPLICIT_ISOLATED_PYTEST_MONKEYPATCH_REQUIRED')
    packet, raw, blobs = load()
    folder = root / 'data/arthur/stadium-locations'
    folder.mkdir(parents=True, exist_ok=False)
    (folder / (PUBLIC_PACKET + '.json')).write_bytes(raw)
    for digest, body in blobs.items():
        (folder / (digest + '.bin')).write_bytes(body)
    if pin_test_only:
        from goal_analysis.jobs import stadium_locations_shadow as geo
        monkeypatch.setattr(geo, 'REVIEWED_BUNDLE', PUBLIC_PACKET)
    return packet, folder
