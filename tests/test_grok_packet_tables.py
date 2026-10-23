"""Lossless transport checks for large all-ticket review batches."""

import json
from copy import deepcopy

import pytest

from goal_analysis.agents.grok_packet_tables import expand_tables, wire_packet


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_small_packets_keep_familiar_structure():
    value = {"fixtures": [{"fixture_id": "1", "home_team": "Vasas"}]}
    assert wire_packet(value) is value
    assert expand_tables(value) == value


def test_large_heterogeneous_tables_preserve_null_missing_false_and_exact_numbers():
    rows = []
    for index in range(250):
        row = {"id": str(index), "source": "https://example.org/fixtures?id=123456",
               "quoted_at": "2026-10-18T00:00:00+00:00", "period": "FULL_TIME",
               "numeric": {"probability": .123456789012345, "empty": {}, "zero": 0,
                           "boolean": False, "nullable": None},
               "form": {"results_newest_first": list("WWLDDWWLLW" * 2)},
               "comment": "Árvíztűrő tükörfúrógép — Über 2.5 " * 4 + str(index % 3)}
        if index % 2:
            row["optional"] = None
        if index % 3:
            row["detail"] = {"more": [{"a": 1, "b": None}, {"a": 2}]}
        rows.append(row)
    packet = {"fixtures": rows, "cutoff": "2026-10-18T00:00:00+00:00"}
    before = deepcopy(packet)
    wire = wire_packet(packet)
    assert "table_encoding" in wire and "shared_text" in wire
    assert "$results" in encode(wire)
    assert len(encode(wire)) < len(encode(packet)) / 2
    assert expand_tables(wire) == packet == before
    assert wire_packet(packet) == wire


def test_conflicting_nested_shapes_and_literal_dot_fields_fall_back_losslessly():
    packet = {"rows": [{"id": i, "a.b": 1, "mixed": None if i % 2 else {"x": 1},
                        "text": "Known source repeated exactly. " * 30} for i in range(100)]}
    assert expand_tables(wire_packet(packet)) == packet


def test_mixed_scalar_object_and_empty_values_round_trip():
    packet = {"rows": [{"i": i, "mixed": [None, {}, [], False, 0, 1.2345678901234],
                        "shape": [None, {}, {"deep": {"x": 3}}, [1, 2]][i % 4],
                        "long_text": "Same factual statement without truncation. " * 30}
                       for i in range(120)]}
    assert expand_tables(wire_packet(packet)) == packet


def test_wrong_table_width_is_rejected():
    with pytest.raises(ValueError, match="width"):
        expand_tables({"$table": 1, "columns": ["a", "b"], "rows": [[1]]})


def test_unique_official_urls_share_only_prefix_and_round_trip_exactly():
    packet = {"rows": [{"id": i,
        "url": f"https://v3.football.api-sports.io/odds?fixture={i}&bookmaker=8&bet={i % 7}",
        "weather": f"https://api.open-meteo.com/v1/forecast?latitude={40+i/100}&longitude=-3&timezone=UTC",
        "text": "Preserved historical evidence. " * 20} for i in range(180)]}
    before = deepcopy(packet)
    wire = wire_packet(packet)
    assert "shared_url_prefixes" in wire and "$url" in encode(wire)
    assert len(encode(wire)) < len(encode(packet))
    assert expand_tables(wire) == packet == before
    assert wire_packet(packet) == wire


def test_malformed_url_reference_is_rejected():
    with pytest.raises(ValueError, match="URL reference"):
        expand_tables({"shared_url_prefixes": {"u1": "https://example.org/"},
                       "source": {"$url": ["u1", 5]}})


def test_sparse_shared_context_preserves_absence_and_nullable_fields():
    rows = [{"id": i, "text": "Full historical numeric evidence is preserved. " * 20,
             **({"context": {"status": "SOURCED", "asof": "2026-10-18T00:00:00+00:00",
                              "nullable": None, "false": False, "empty": {}}} if i % 5 else {})}
            for i in range(180)]
    packet = {"fixtures": rows}
    wire = wire_packet(packet)
    assert '"absent"' in encode(wire)
    assert expand_tables(wire) == packet
    assert "context" not in expand_tables(wire)["fixtures"][0]


def test_invalid_absent_row_reference_is_rejected():
    with pytest.raises(ValueError, match="absent"):
        expand_tables({"$table": 1, "columns": ["id"], "rows": [[1]],
                       "defaults": {"status": "SOURCED"}, "absent": {"status": [1]}})


def test_sparse_column_rows_preserve_all_measurements_and_literal_nulls():
    packet = {"rows": [{"id": i, "description": "Unchanged numerical evidence. " * 30,
                        **{f"optional_measurement_{j}": None if i % 7 == 0 else i / (j + 1)
                           for j in range(60) if (j + i) % 17 == 0}} for i in range(180)]}
    wire = wire_packet(packet)
    assert any(isinstance(row, dict) for row in wire["rows"]["rows"])
    assert expand_tables(wire) == packet


@pytest.mark.parametrize("column", ["-1", "01", "5", "not_an_index"])
def test_sparse_invalid_columns_are_rejected(column):
    with pytest.raises(ValueError, match="sparse table column"):
        expand_tables({"$table": 1, "columns": ["a"], "rows": [{column: 1}]})


def test_dense_stats_dictionary_preserves_fact_order_sources_and_all_scalar_types():
    from goal_analysis.agents.grok_packet_dense import pack_dense
    from goal_analysis.agents.grok_packet_tables import _wire_packet_v1

    source = {"provider": "the_stats_api", "source_url": "https://api.thestatsapi.com/api/football/matches?date=2026-10-18",
              "observed_at": "2026-10-18T00:30:24.123456+00:00", "content_hash": "a" * 64,
              "published_at": None}
    available = {"category": "team_season_stats", "status": "AVAILABLE", "source": source,
                 "data": {"expected_goals_for": 1.234567891234567, "zero": 0, "false": False,
                          "missing_value": None, "empty": {}, "literal": "~t:not-a-reference"}}
    unavailable = {"category": "lineup", "status": "UNAVAILABLE", "source": None, "data": None}
    packet = {"fixtures": [{"fixture_id": str(i), "the_stats_api": {
        "mapping": {"source": source}, "facts": [available, unavailable, available]}}
        for i in range(3)] + [{"fixture_id": "empty", "the_stats_api": {"facts": []}},
                             {"fixture_id": "absent", "the_stats_api": {"status": "UNMATCHED"}}]}
    before = deepcopy(packet)
    dense = pack_dense(packet, _wire_packet_v1)
    assert "dense_encoding" in dense
    assert expand_tables(dense) == packet == before
    assert pack_dense(packet, _wire_packet_v1) == dense


def test_dense_field_names_and_text_references_are_contextual_not_value_replacements():
    from goal_analysis.agents.grok_packet_dense import pack_dense
    from goal_analysis.agents.grok_packet_tables import _wire_packet_v1

    packet = {"fixtures": [{"fixture_id": str(i), "numeric_models": {
        "model": {"probabilities": {"home_win": i / 211, "away_win": (211 - i) / 211},
                  "validation": {"training_sample_size": i, "sample_size": i + 2}}},
        "quoted_at": "2026-10-18T00:00:00+00:00", "notes": "numeric_models.validation",
        "literal": ["^0", "~r:WDL", "~u:u1:x", "~x:1"],
        "form": list("WDLLWWWWLDDWLWL"),
        "source": f"https://v3.football.api-sports.io/fixtures?id={i}"}
        for i in range(211)]}
    dense = pack_dense(packet, _wire_packet_v1)
    assert expand_tables(dense) == packet
    assert len(encode(dense)) < len(encode(packet))


def test_dense_reserved_field_alias_collision_uses_existing_lossless_tables():
    from goal_analysis.agents.grok_packet_dense import pack_dense
    from goal_analysis.agents.grok_packet_tables import _wire_packet_v1

    packet = {"fixtures": [{"^0": "literal-key", "repeated_context_label": i} for i in range(3)]}
    assert expand_tables(pack_dense(packet, _wire_packet_v1)) == packet
