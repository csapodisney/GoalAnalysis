"""Expanded original facility sources and point-in-time release boundaries."""
import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_native_venue_shadow import NOW
from test_stadium_locations_shadow import FIXTURES, ORIGINAL, seal, sources

from goal_analysis.jobs import stadium_locations_shadow as geo
from goal_analysis.normalization.canonical import utc


def predecessor(root):
    old = Path(__file__).parent/'fixtures/stadium-locations-20261022'
    folder = root/'data/arthur/stadium-locations';folder.mkdir(parents=True, exist_ok=True)
    raw = (old/'reviewed-locations.json').read_bytes()
    (folder/(geo.PREVIOUS_BUNDLE+'.json')).write_bytes(raw)
    packet = json.loads(raw)
    for record in packet['records']:
        for kind in ('wikidata_source', 'identity_source'):
            name = record[kind]['sha256']+'.bin'
            (folder/name).write_bytes((old/name).read_bytes())
    return packet


def test_original_predecessor_remains_available_without_retroactive_new_facilities(tmp_path):
    old = predecessor(tmp_path);folder = tmp_path/'data/arthur/stadium-locations'
    (folder/(geo.REVIEWED_BUNDLE+'.json')).write_bytes((FIXTURES/'reviewed-locations.json').read_bytes())
    cutoff = utc(old['available_at'])+timedelta(seconds=1)
    rows = geo.read_locations(tmp_path, as_of=cutoff)
    assert len(rows) == 6 and ('BUKO Stadion','Velsen') not in rows
    assert {r['payload']['bundle_sha256'] for r in rows.values()} == {geo.PREVIOUS_BUNDLE}
    with pytest.raises(ValueError):
        geo.read_locations(tmp_path, as_of=utc(old['available_at'])-timedelta(microseconds=1))


@pytest.mark.parametrize('name,city,q',[
    ('BUKO Stadion','Velsen','Q1851894'),
    ('Heinz-von-Heiden-Arena','Hannover','Q152633'),
    ('Stade Francis-Le Basser','Laval','Q1128228'),
    ('U-Power Stadium','Monza','Q1851616'),
    ('Decathlon Arena - Stade Pierre Mauroy','Lille','Q1410402'),
    ('Wildparkstadion','Karlsruhe','Q590268'),
    ('Estadio de Mendizorroza','Vitoria-Gasteiz','Q1369390'),
    ('Stade Gabriel Montpied','Clermont-Ferrand','Q1128263'),
    ('Vonovia Ruhrstadion','Bochum','Q373769'),
    ('Stade du Roudourou','Guingamp','Q1817621'),
    ('Stadio Ciro Vigorito','Benevento','Q1671970'),
    ('Volkswagen Arena','Wolfsburg','Q155603'),
    ('Fritz-Walter-Stadion','Kaiserslautern','Q154298'),
    ('City Ground','Nottingham','Q593446'),
    ('Stade de la Beaujoire','Nantes','Q1053455'),
    ('Municipal de Balaidos','Vigo','Q1326578'),
    ('Jahnstadion Regensburg','Regensburg','Q641459'),
    ('Audi Sportpark','Ingolstadt','Q479619'),
    ('Stade Saint Symphorien','Metz','Q1483409'),
    ('Riverside','Middlesbrough','Q822886'),
    ('Groupama Stadium','Lyon','Q8507'),
    ('Borussia-Park','Monchengladbach','Q154074'),
    ('Etihad Stadium','Manchester','Q48159'),
    ("St. Mary's Stadium",'Southampton','Q567339'),
    ('La Rosaleda','Malaga','Q695879'),
    ('Stade des Alpes','Grenoble','Q2299298'),
    ('Cino e Lillo Del Duca','Ascoli Piceno','Q94591'),
    ('Ennio Tardini','Parma','Q610546'),
    ('Parc des Sports','Annecy','Q428115'),
    ('Mac3Park Stadion','Zwolle','Q1141104'),
])
def test_reviewed_facility_ids_are_exact_not_first_search_result(tmp_path,monkeypatch,name,city,q):
    sources(tmp_path,monkeypatch)
    rows=geo.read_locations(tmp_path,as_of=NOW)
    assert rows[(name,city)]['payload']['venue_id']=='wikidata:'+q
    assert (name,city+'-guess') not in rows
    assert rows[(name,city)]['payload']['native_venue_id'] is None


def test_unreported_original_coordinate_precision_stays_unknown(tmp_path,monkeypatch):
    sources(tmp_path,monkeypatch)
    record=geo.read_locations(tmp_path,as_of=NOW)[('Toughsheet Community Stadium','Horwich')]
    assert record['payload']['reported_precision_degrees'] is None
    assert record['payload']['coordinate_precision_status']=='UNREPORTED'
    assert record['payload']['precision_is_not_accuracy_radius'] is True
    assert record['payload']['latitude']==53.580555555556


def test_invalid_current_release_is_not_replaced_by_favorable_old_packet(tmp_path,monkeypatch):
    packet=sources(tmp_path,monkeypatch);predecessor(tmp_path)
    packet['records'][0]['coordinate_statement']['latitude']=0
    seal(tmp_path,packet,monkeypatch)
    with pytest.raises(ValueError):geo.read_locations(tmp_path,as_of=NOW)


def test_expanded_release_has_unique_exact_aliases_and_sources():
    assert len(ORIGINAL['records'])==69
    assert len({(r['native_name'],r['native_city']) for r in ORIGINAL['records']})==69
    assert len({r['wikidata_id'] for r in ORIGINAL['records']})==69


def install_original_release(root, name):
    source=Path(__file__).parent/'fixtures'/name
    packet=json.loads((source/'reviewed-locations.json').read_bytes())
    folder=root/'data/arthur/stadium-locations';folder.mkdir(parents=True,exist_ok=True)
    from hashlib import sha256

    raw=(source/'reviewed-locations.json').read_bytes();digest=sha256(raw).hexdigest()
    (folder/(digest+'.json')).write_bytes(raw)
    for record in packet['records']:
        for kind in ('wikidata_source','identity_source'):
            filename=record[kind]['sha256']+'.bin'
            (folder/filename).write_bytes((source/filename).read_bytes())
    return packet,folder/(digest+'.json')


@pytest.mark.parametrize('boundary,delta,count',[
    ('first',0,6),('second',-1,6),('second',0,23),
    ('third',-1,23),('third',0,34),
    ('fourth',-1,34),('fourth',0,44),
])
def test_four_actual_releases_admit_only_facilities_known_at_each_boundary(tmp_path,boundary,delta,count):
    first,_=install_original_release(tmp_path,'stadium-locations-20261022')
    second,_=install_original_release(tmp_path,'stadium-locations-20261022-expanded')
    third,_=install_original_release(tmp_path,'stadium-locations-20261022-third')
    fourth,_=install_original_release(tmp_path,'stadium-locations-20261022-fourth')
    cutoff=utc({'first':first,'second':second,'third':third,'fourth':fourth}[boundary]['available_at'])+timedelta(microseconds=delta)
    rows=geo.read_locations(tmp_path,as_of=cutoff)
    assert len(rows)==count
    assert (('Stade du Roudourou','Guingamp') in rows)==(count>=34)
    assert (('BUKO Stadion','Velsen') in rows)==(count>=23)
    assert (('Groupama Stadium','Lyon') in rows)==(count==44)


@pytest.mark.parametrize('fault',['missing','corrupt'])
@pytest.mark.parametrize('release',['expanded','third'])
def test_applicable_intermediate_release_failure_cannot_fall_back_to_first(tmp_path,fault,release):
    predecessor(tmp_path)
    second,path=install_original_release(tmp_path,'stadium-locations-20261022-'+release)
    if fault=='missing':path.unlink()
    else:path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):
        geo.read_locations(tmp_path,as_of=utc(second['available_at']))


def test_future_release_bytes_are_not_a_dependency_of_a_historical_cutoff(tmp_path):
    second,_=install_original_release(tmp_path,'stadium-locations-20261022-expanded')
    folder=tmp_path/'data/arthur/stadium-locations'
    (folder/(geo.REVIEWED_BUNDLE+'.json')).write_bytes(b'corrupt future file')
    assert len(geo.read_locations(tmp_path,as_of=utc(second['available_at'])))==23


def test_current_missing_release_does_not_fall_back_to_older_present_records(tmp_path):
    install_original_release(tmp_path,'stadium-locations-20261022-expanded')
    with pytest.raises(ValueError):geo.read_locations(tmp_path,as_of=utc(geo.REVIEWED_AT))


@pytest.mark.parametrize('release,count,limit',[
    ('stadium-locations-20261022',6,50_000),
    ('stadium-locations-20261022-expanded',23,50_000),
    ('stadium-locations-20261022-third',34,50_000),
    ('stadium-locations-20261022-fourth',44,64_000),
    ('stadium-locations-20261023-fifth',56,80_000),
])
def test_larger_current_packet_does_not_widen_historical_byte_limits(tmp_path,monkeypatch,release,count,limit):
    packet,path=install_original_release(tmp_path,release)
    if count==44:assert 50_000<path.stat().st_size<64_000
    original=geo._bytes;limits=[]

    def bounded(folder,digest,suffix,byte_limit):
        if suffix=='.json':limits.append(byte_limit)
        return original(folder,digest,suffix,byte_limit)

    monkeypatch.setattr(geo,'_bytes',bounded)
    assert len(geo.read_locations(tmp_path,as_of=utc(packet['available_at'])))==count
    assert limits==[limit]


def test_oversized_current_release_is_rejected_before_json_decode(tmp_path):
    packet,path=install_original_release(tmp_path,'stadium-locations-20261022-fourth')
    path.write_bytes(b' '*64_001)
    with pytest.raises(ValueError,match='MISSING_OR_OVERSIZED_STADIUM_SOURCE'):
        geo.read_locations(tmp_path,as_of=utc(packet['available_at']))
