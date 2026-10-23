"""The inventory runs after collection and cannot consume quota or erase facts."""
from test_the_stats_context import Transport, report, run

from goal_analysis.jobs import league_coverage


def test_actual_collector_includes_missing_inventory_without_extra_provider_calls(tmp_path):
    transport = Transport()
    result = run(tmp_path, transport, report())
    audit = result["league_coverage"]
    assert audit["mode"] == "SHADOW"
    assert audit["catalogue"]["status"] == "MISSING"
    assert audit["active_competitions"] == 4  # Real fixture plus three conditional groups.
    assert audit["external_requests"] == audit["new_collector_mappings_activated"] == 0
    assert len(transport.calls) == result["usage"]["requests"]
    assert result["coverage"]["matched"] == 1


def test_failed_inventory_preserves_collected_facts_and_requests(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("test inventory failure")

    monkeypatch.setattr(league_coverage, "inspect_league_coverage", broken)
    transport = Transport()
    result = run(tmp_path, transport, report())
    assert result["league_coverage"]["status"] == "UNAVAILABLE"
    assert result["coverage"]["matched"] == 1
    assert result["coverage"]["available_facts"] > 0
    assert len(transport.calls) == result["usage"]["requests"]
