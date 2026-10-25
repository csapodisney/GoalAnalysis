"""Public synthetic codec boundaries, not fabricated provider responses or real fixtures."""

import hashlib
import json
import random
from copy import deepcopy

import pytest
from test_grok_advisory import NOW, Advisor, configure, recommendation, report

from goal_analysis.agents.grok_packet_accounting import packet_accounting
from goal_analysis.agents.grok_packet_readable import pack_readable
from goal_analysis.agents.grok_packet_tables import expand_tables, wire_packet
from goal_analysis.jobs import grok_advisory as job


def payload(seed=0, count=8):
    rng = random.Random(seed)
    fixtures = []
    for index in range(count):
        row = {
            "fixture_id": f"synthetic:{index}",
            "home_team": "Árvíztűrő",
            "away_team": "Visitors",
            "kickoff": "2026-10-25T18:00:00+00:00",
            "probability": rng.random(),
            "price": 2.31,
            "nullable": None,
            "false": False,
            "zero": 0,
            "empty": {},
            "literal": [
                "@0",
                "!Hnot-a-hash",
                "!J0:foo",
                "!S",
                "~t:t1",
                "literal $shared marker",
                "",
            ],
            "source": {
                "content_hash": hashlib.sha256(str(index).encode()).hexdigest(),
                "http_observation_id": hashlib.md5(str(index).encode()).hexdigest(),
                "source_url": f"https://example.test/teams/tm_{index}/stats?season_id=sn_2026",
                "observed_at": "2026-10-25T12:03:04.123456+00:00",
            },
            "the_stats_api": {
                "facts": [
                    {
                        "category": "synthetic",
                        "status": "UNKNOWN",
                        "subject_id": f"tm_{index}",
                        "data": {
                            "players": [
                                {"player_id": f"pl_{index * 100 + i}", "number": i, "unknown": None}
                                for i in range(12)
                            ]
                        },
                        "source": {
                            "content_hash": hashlib.sha256(f"fact:{index}".encode()).hexdigest(),
                            "source_url": f"https://example.test/teams/tm_{index}/players",
                        },
                    }
                ]
            },
        }
        if index % 2:
            row.pop("nullable")
        if index % 3:
            row["optional"] = {"literal_null": None, "negative_zero": -0.0}
        fixtures.append(row)
    return {"fixtures": fixtures, "selections": [], "recommendations": [], "evidence": []}


@pytest.mark.parametrize("seed", range(30))
def test_exact_numeric_string_null_absence_and_identity_roundtrip(seed):
    original = payload(seed, seed % 9 + 2)
    before = deepcopy(original)
    packed = pack_readable(original)
    decoded = expand_tables(packed)
    assert decoded == original == before
    # JSON identity also distinguishes 0/-0.0 and bool/int, unlike Python equality.
    assert json.dumps(decoded, sort_keys=True, ensure_ascii=False) == json.dumps(
        original, sort_keys=True, ensure_ascii=False
    )
    assert packed["compression_counts"]["opaque_provenance_values"] > 0
    assert packed["compression_counts"]["templated_source_strings"] > 0


def test_small_transport_and_existing_plain_literals_remain_unchanged():
    value = payload(count=1)
    assert wire_packet(value) is value
    assert expand_tables(value) == value


@pytest.mark.parametrize(
    "component", ["packet_sha256", "encoding", "fields", "texts", "objects", "templates", "body"]
)
def test_tampered_identity_or_dictionary_fails_closed(component):
    original = payload(count=26)
    for name in ("first", "second", "third"):
        original[name] = {
            "text": "Repeated synthetic dictionary witness " * 4,
            "null": None,
            "false": False,
            "zero": 0,
            "empty": {},
            "missing": None,
        }
    packed = pack_readable(original)
    if component == "packet_sha256":
        packed[component] = "0" * 64
    elif component == "encoding":
        packed[component] += "altered"
    elif component == "body":
        packed[component]["tampered_price"] = 1.99
    else:
        assert packed[component]
        key = next(iter(packed[component]))
        packed[component][key] = "TAMPERED"
    with pytest.raises(ValueError):
        expand_tables(packed)


@pytest.mark.parametrize("n", [True, -1, 20_001, "3", None])
def test_vector_dimensions_are_bounded_before_expansion(n):
    packed = pack_readable({"fixtures": []})
    packed["body"] = {"$vectors": n, "columns": [], "vectors": []}
    with pytest.raises(ValueError):
        expand_tables(packed)


@pytest.mark.parametrize(
    "vector",
    [
        {"s": {"01": 3}},
        {"s": {"-1": 3}},
        {"s": {"2": 3}},
        {"r": [[3, True]]},
        {"r": [[3, -1]]},
        {"r": [[3, 0]]},
        {"r": [[3, 3]]},
        {"r": [[3, 1]]},
        {"unknown": []},
    ],
)
def test_sparse_and_run_vectors_cannot_hide_out_of_range_or_invalid_rows(vector):
    packed = pack_readable({"fixtures": []})
    packed["body"] = {"$vectors": 2, "columns": ["fixture_id"], "vectors": [vector]}
    with pytest.raises(ValueError):
        expand_tables(packed)


def test_large_fallback_keeps_every_fixture_and_numeric_value():
    original = payload(count=160)
    for index, fixture in enumerate(original["fixtures"]):
        fixture["the_stats_api"]["facts"][0]["data"]["players"] = [
            {"player_id": f"pl_{index * 1000 + i}", "number": i, "unknown": None} for i in range(60)
        ]
    before = deepcopy(original)
    wire = wire_packet(original)
    assert "encoding" in wire
    assert expand_tables(wire) == before == original
    # A larger payload may still exceed the ceiling; codec success is not permission to send.
    assert len(json.dumps(wire)) < len(json.dumps(original))


def test_per_category_prose_omission_and_transport_counts_include_refused_packet():
    before = payload(count=2)
    before["evidence"] = [{"statement": "Some unusually long test excerpt"}]
    after = deepcopy(before)
    after["evidence"][0]["statement"] = "Some …"
    after["evidence"][0]["statement_truncated"] = True
    accounted = packet_accounting(
        before, after, wire_packet(after), [{"reason_code": "PREMATCH_LOCKOUT"}], refused=True
    )
    assert accounted["categories"]["evidence"]["prose_excerpt_shortened_fields"] == 1
    assert accounted["categories"]["evidence"]["added_fields"] == 1
    assert accounted["excluded_profile_reasons"] == {"PREMATCH_LOCKOUT": 1}
    assert all(
        row["transport_omitted_fields"]
        == row["transport_changed_fields"]
        == row["dispatched_fields"]
        == 0
        for row in accounted["categories"].values()
    )
    assert accounted["dispatch_note"] == "PACKET_REFUSED_NO_REQUEST"
    assert accounted["statistics_fact_categories"] == {"synthetic": 2}


def test_full_size_refusal_preserves_category_proof_and_never_reserves_budget(
    tmp_path, monkeypatch
):
    configure(tmp_path)
    marker = {
        "packet_accounting": {
            "assembly_complete": True,
            "categories": {"quote_sources": {"transport_omitted_fields": 0}},
            "request_dispatched": False,
        }
    }

    def refuse(*args):
        raise job.PacketTooLarge(marker)

    monkeypatch.setattr(job, "_packet", refuse)
    advisor = Advisor()
    result = job.analyze(tmp_path, report(), advisor=advisor, now=NOW)
    assert result["status"] == "PACKET_TOO_LARGE" and result["coverage"] == marker
    assert not advisor.packets and not (tmp_path / "data/arthur/grok.sqlite3").exists()


def test_early_structural_refusal_reports_unknown_category_counts_without_filling(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    data = report(recommendation(fixtures=range(21)))
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert result["status"] == "PACKET_TOO_LARGE"
    counts = result["coverage"]["packet_accounting"]
    assert counts["assembly_complete"] is False and counts["categories"] is None
    assert counts["raw_report_rows"] == {"recommendations": 1}
    assert counts["request_dispatched"] is False and not advisor.packets
    assert not (tmp_path / "data/arthur/grok.sqlite3").exists()


def test_real_assembled_oversize_boundary_reports_categories_without_request(tmp_path):
    configure(tmp_path)
    rows = [
        recommendation("tipster_szines_reka", range(1, 21)),
        recommendation("tipster_fuzo_oszkar", range(21, 41)),
    ]
    for rec in rows:
        for leg in rec["legs"]:
            leg["evidence"] = [
                {
                    "category": "synthetic_observation",
                    "claim_type": "context",
                    "statement": hashlib.sha256(f"{leg['fixture_id']}:{i}".encode()).hexdigest()
                    * 4,
                    "source_id": f"https://example.test/observation/{leg['fixture_id']}:{i}",
                    "observed_at": NOW.isoformat(),
                }
                for i in range(12)
            ]
    advisor = Advisor()
    data = report(*rows)
    before = deepcopy(data)
    result = job.analyze(tmp_path, data, advisor=advisor, now=NOW)
    assert result["status"] == "PACKET_TOO_LARGE"
    assert result["coverage"]["compaction"]["input_characters"] > 100_000
    accounting = result["coverage"]["packet_accounting"]
    assert accounting["assembly_complete"] is True
    assert accounting["categories"]["evidence"]["retained_fields"] > 0
    assert accounting["lossless_transport_verified"] is True
    assert accounting["dispatch_note"] == "PACKET_REFUSED_NO_REQUEST"
    assert not advisor.packets and data == before
    assert not (tmp_path / "data/arthur/grok.sqlite3").exists()
