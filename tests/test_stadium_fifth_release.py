"""Fifth source release: exact new aliases, exclusions and historical isolation."""
import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from test_stadium_location_releases import install_original_release

from goal_analysis.jobs import stadium_locations_shadow as geo
from goal_analysis.normalization.canonical import utc

FIXTURES=Path(__file__).parent/'fixtures/stadium-locations-20261023-fifth'
PACKET=json.loads((FIXTURES/'reviewed-locations.json').read_bytes())
FIFTH_BUNDLE='6a6ef106af3d782708df8df56aa5ce533c99302f3b7fb41eab858c21a66a9ceb'
FOURTH_BUNDLE='57926fc865a79717e7039d82c56b90cb1efa5fef37bed4d23eb2ba11544f1011'
AT=utc(PACKET['available_at'])
NEW=[
    ('Mestalla','Valencia','Q202762'),
    ('Gran Canaria','Las Palmas de Gran Canaria','Q991419'),
    ('Het Kasteel','Rotterdam','Q668094'),
    ('Stadio Olimpico','Rome','Q159884'),
    ('Stade de Paris','Saint-Ouen','Q1320249'),
    ('Arena Garibaldi-Romeo Anconetani','Pisa','Q115970'),
    ("St James' Park",'Newcastle upon Tyne','Q191774'),
    ('El Molinón','Gijon','Q929315'),
    ('Allianz Arena','Munich','Q127429'),
    ('Andruv Stadion','Olomouc','Q154405'),
    ("Renato Dall'Ara",'Bologna','Q757182'),
    ('Stadio Danilo Martelli','Mantova','Q245720'),
]

def install(root):
    folder=root/'data/arthur/stadium-locations';folder.mkdir(parents=True,exist_ok=True)
    raw=(FIXTURES/'reviewed-locations.json').read_bytes()
    (folder/(sha256(raw).hexdigest()+'.json')).write_bytes(raw)
    for item in PACKET['records']:
        for key in ('wikidata_source','identity_source'):
            name=item[key]['sha256']+'.bin';(folder/name).write_bytes((FIXTURES/name).read_bytes())
    return folder

@pytest.mark.parametrize('name,city,q',NEW)
def test_current_native_alias_resolves_only_exact_reviewed_facility(tmp_path,name,city,q):
    install(tmp_path);rows=geo.read_locations(tmp_path,as_of=AT)
    assert len(rows)==56 and rows[(name,city)]['payload']['venue_id']=='wikidata:'+q
    assert (name,city+'-guess') not in rows
    assert rows[(name,city)]['times']['available_at']==PACKET['available_at']
    assert rows[(name,city)]['payload']['native_venue_id'] is None

@pytest.mark.parametrize('delta,count',[(-1,44),(0,56),(1,56)])
def test_new_own_availability_does_not_relabel_fourth_release(tmp_path,delta,count):
    install_original_release(tmp_path,'stadium-locations-20261022-fourth');install(tmp_path)
    rows=geo.read_locations(tmp_path,as_of=AT+timedelta(microseconds=delta))
    assert len(rows)==count and (('Mestalla','Valencia') in rows)==(count==56)
    if delta<0:assert {r['payload']['bundle_sha256'] for r in rows.values()}=={FOURTH_BUNDLE}

@pytest.mark.parametrize('name,city',[("Stade de l'Aube",'Troyes'),('Estadio de la Cartuja','Seville')])
def test_unresolved_coordinate_disagreement_and_unavailable_identity_page_are_not_admitted(tmp_path,name,city):
    install(tmp_path)
    assert (name,city) not in geo.read_locations(tmp_path,as_of=AT)

@pytest.mark.parametrize('name,city',[('Allianz Arena','Munich'),("St James' Park",'Newcastle upon Tyne')])
def test_unreported_precision_is_not_fabricated_for_new_facilities(tmp_path,name,city):
    install(tmp_path);p=geo.read_locations(tmp_path,as_of=AT)[(name,city)]['payload']
    assert p['reported_precision_degrees'] is None and p['coordinate_precision_status']=='UNREPORTED'
    assert p['precision_is_not_accuracy_radius']

def test_all_four_predecessor_records_retained_exactly():
    fourth=json.loads((Path(__file__).parent/'fixtures/stadium-locations-20261022-fourth/reviewed-locations.json').read_bytes())
    assert PACKET['records'][:44]==fourth['records']
    assert utc(fourth['available_at'])<AT
    assert sha256((FIXTURES/'reviewed-locations.json').read_bytes()).hexdigest()==FIFTH_BUNDLE

def test_current_byte_limit_only_applies_to_current_release(tmp_path,monkeypatch):
    install(tmp_path);limits=[];original=geo._bytes
    def tracked(folder,digest,suffix,limit):
        if suffix=='.json':limits.append(limit)
        return original(folder,digest,suffix,limit)
    monkeypatch.setattr(geo,'_bytes',tracked)
    assert len(geo.read_locations(tmp_path,as_of=AT))==56 and limits==[80_000]

@pytest.mark.parametrize('fault',['missing','corrupt','oversize'])
def test_bad_fifth_release_cannot_fall_back_to_44_favorable_records(tmp_path,fault):
    install_original_release(tmp_path,'stadium-locations-20261022-fourth');folder=install(tmp_path)
    p=folder/(FIFTH_BUNDLE+'.json')
    if fault=='missing':p.unlink()
    elif fault=='corrupt':p.write_bytes(p.read_bytes()+b' ')
    else:p.write_bytes(b' '*80_001)
    with pytest.raises(ValueError):geo.read_locations(tmp_path,as_of=AT)

def test_corrupt_future_release_is_not_dependency_of_original_44_record_view(tmp_path):
    install_original_release(tmp_path,'stadium-locations-20261022-fourth')
    folder=install(tmp_path);(folder/(FIFTH_BUNDLE+'.json')).write_bytes(b'bad future bytes')
    assert len(geo.read_locations(tmp_path,as_of=AT-timedelta(microseconds=1)))==44
