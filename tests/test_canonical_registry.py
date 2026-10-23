from dataclasses import replace
from datetime import UTC, datetime, timedelta
import sqlite3

import pytest
from test_canonical_entities import graph, identity

from goal_analysis.storage.db import Database
from goal_analysis.storage.canonical_registry import CanonicalRegistry, MappingEvidence, ProviderBinding

NOW = datetime(2026, 10, 20, 8, tzinfo=UTC)


def proof(**changes):
    return MappingEvidence(**{"method": "VERIFIED_PROVIDER_MAPPING", "source_url": "https://api.example.test/teams/1",
        "content_sha256": "a" * 64, "observed_at": NOW, **changes})


def setup_registry(tmp_path, now=NOW):
    registry = CanonicalRegistry(Database(tmp_path / "sports.sqlite3"), clock=lambda: now)
    registry.initialize()
    return registry


def test_all_provider_links_roundtrip_and_reimport_idempotent(tmp_path):
    registry = setup_registry(tmp_path)
    entities = graph()
    home = entities[2].entity_id
    bindings = [ProviderBinding(p, "team", source_id, home) for p, source_id in (
        ("api_football", "33"), ("the_stats_api", "tm_33"),
        ("football_data_co_uk", "E0:verified-team-33"), ("the_odds_api", "event-team-33"))]
    assert registry.register(entities, bindings, proof())["bindings_created"] == 4
    registry.initialize()
    assert registry.register(entities, bindings, proof()) == {
        "entities_created": 0, "versions_created": 0, "bindings_created": 0, "proofs_created": 0}
    for binding in bindings:
        assert registry.resolve(binding.provider, "team", binding.provider_id, as_of=NOW) == home
    for entity in entities:
        assert registry.entity(entity.entity_id, as_of=NOW) == entity
    assert registry.resolve("api_football", "team", "missing", as_of=NOW) is None
    with registry.database.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM canonical_entities")


def test_cannot_reassign_provider_id_or_partially_write_conflicting_batch(tmp_path):
    registry = setup_registry(tmp_path)
    entities = graph()
    home, away = entities[2:4]
    first = ProviderBinding("the_stats_api", "team", "tm_1", home.entity_id)
    second = replace(first, entity_id=away.entity_id)
    with pytest.raises(ValueError, match="CONFLICTING"):
        registry.register(entities, [first, second], proof())
    assert registry.entity(home.entity_id, as_of=NOW) is None
    registry.register(entities, [first], proof())
    with pytest.raises(ValueError, match="CONFLICTING"):
        registry.register(entities, [second], proof())
    assert registry.resolve(first.provider, first.kind, first.provider_id, as_of=NOW) == home.entity_id


def test_mapping_not_visible_before_receipt_and_attributes_are_versioned(tmp_path):
    later = NOW + timedelta(days=1)
    registry = setup_registry(tmp_path, later)
    entities = graph()
    binding = ProviderBinding("api_football", "team", "33", entities[2].entity_id)
    registry.register(entities, [binding], proof())
    assert registry.resolve("api_football", "team", "33", as_of=NOW) is None
    assert registry.entity(binding.entity_id, as_of=NOW) is None
    assert registry.resolve("api_football", "team", "33", as_of=later) == binding.entity_id
    renamed = list(entities)
    renamed[2] = replace(entities[2], name="New name")
    registry.clock = lambda: later + timedelta(hours=1)
    registry.register(renamed, [binding], proof(observed_at=later + timedelta(hours=1)))
    assert registry.entity(binding.entity_id, as_of=later).name == "Home"
    assert registry.entity(binding.entity_id, as_of=later + timedelta(hours=1)).name == "New name"


def test_future_missing_or_nondeterministic_evidence_is_rejected(tmp_path):
    registry = setup_registry(tmp_path)
    with pytest.raises(ValueError, match="future"):
        registry.register(graph(), [], proof(observed_at=NOW + timedelta(seconds=1)))
    for changes in ({"method": "FUZZY"}, {"content_sha256": ""}, {"source_url": "https://secret@api.test/x"}):
        with pytest.raises(ValueError):
            proof(**changes)
    with pytest.raises(ValueError):
        registry.register(graph(), [ProviderBinding("api_football", "team", "1", identity("team", "missing"))], proof())


def test_provider_scoping_case_and_name_collisions_remain_distinct(tmp_path):
    registry = setup_registry(tmp_path)
    entities = graph()
    entities[3] = replace(entities[3], name=entities[2].name)
    bindings = [ProviderBinding("api_football", "team", "ABC", entities[2].entity_id),
                ProviderBinding("api_football", "team", "abc", entities[3].entity_id)]
    registry.register(entities, bindings, proof())
    assert registry.resolve("api_football", "team", "ABC", as_of=NOW) != registry.resolve("api_football", "team", "abc", as_of=NOW)


def test_same_time_conflicting_attributes_roll_back_and_unbound_entities_retain_proofs(tmp_path):
    registry = setup_registry(tmp_path)
    entities = graph()
    registry.register(entities, [], proof())
    assert registry.provenance(entities[4].entity_id, as_of=NOW) == [proof().to_record()]
    changes = list(entities)
    changes[2] = replace(changes[2], name="Conflicting")
    with pytest.raises(ValueError, match="CONFLICTING_ENTITY"):
        registry.register(changes, [], proof())
    assert registry.entity(entities[2].entity_id, as_of=NOW).name == "Home"
    other_source = proof(source_url="https://second.example.test/teams")
    result = registry.register(entities, [], other_source)
    assert result["versions_created"] == 0 and result["proofs_created"] == len(entities)
    assert len(registry.provenance(entities[4].entity_id, as_of=NOW)) == 2


def test_multi_page_import_rolls_back_all_pages_on_late_conflict(tmp_path):
    registry = setup_registry(tmp_path)
    entities = graph()
    bad = list(entities)
    bad[2] = replace(bad[2], name="Later conflicting page")
    with pytest.raises(ValueError, match="CONFLICTING_ENTITY"):
        registry.register_many([(entities, [], proof()), (bad, [], proof())])
    assert registry.entity(entities[2].entity_id, as_of=NOW) is None
