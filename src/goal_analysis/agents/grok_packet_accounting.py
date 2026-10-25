"""Inventories at the assembled Grok packet boundary; no sporting-data inference."""

from collections import Counter
from copy import deepcopy


def _fields(value, path=()):
    if isinstance(value, dict) and value:
        return {
            key: item
            for name, child in value.items()
            for key, item in _fields(child, (*path, name)).items()
        }
    if isinstance(value, list) and value:
        return {
            key: item
            for index, child in enumerate(value)
            for key, item in _fields(child, (*path, index)).items()
        }
    return {path: value}


def _categories(packet):
    result = {key: value for key, value in packet.items() if key != "fixtures"}
    fixtures = packet.get("fixtures", [])
    result["fixtures.identity"] = [
        {
            key: value
            for key, value in row.items()
            if key
            not in {
                "weather",
                "existing_context",
                "expert_context",
                "numeric_models",
                "historical_form",
                "the_stats_api",
            }
        }
        for row in fixtures
    ]
    for key in (
        "weather",
        "existing_context",
        "expert_context",
        "numeric_models",
        "historical_form",
        "the_stats_api",
    ):
        result["fixtures." + key] = [row[key] for row in fixtures if key in row]
    return result


def packet_accounting(before, after, wire, excluded, *, refused=False):
    """Account for every assembled category; raw report whitelist is a separate boundary.

    Shared transport pools are intentionally not assigned arbitrary per-category
    byte savings. Exact expansion verifies that no assembled value was omitted.
    """
    from goal_analysis.agents.grok_packet_tables import expand_tables

    if expand_tables(wire) != after:
        raise ValueError("Transport accounting requires exact semantic round-trip")
    first, last = _categories(before), _categories(after)
    categories = {}
    for key in sorted(set(first) | set(last)):
        a, b = _fields(first.get(key, [])), _fields(last.get(key, []))
        changed = [path for path in a.keys() & b.keys() if a[path] != b[path]]
        excerpts = sum(
            isinstance(a[path], str)
            and isinstance(b[path], str)
            and len(b[path]) < len(a[path])
            and b[path].endswith(" …")
            for path in changed
        )
        categories[key] = {
            "assembled_fields": len(a),
            "retained_fields": len(b),
            "omitted_fields": len(a.keys() - b.keys()),
            "added_fields": len(b.keys() - a.keys()),
            "prose_excerpt_shortened_fields": excerpts,
            "other_changed_fields": len(changed) - excerpts,
            "transport_omitted_fields": 0,
            "transport_changed_fields": 0,
            "dispatched_fields": 0 if refused else None,
        }
    facts = Counter(
        fact.get("category", "UNKNOWN")
        for fixture in after.get("fixtures", [])
        for fact in fixture.get("the_stats_api", {}).get("facts", [])
    )
    return {
        "basis": "ASSEMBLED_VALIDATED_PACKET_BEFORE_OPTIONAL_PROSE_COMPACTION",
        "assembly_complete": True,
        "categories": categories,
        "statistics_fact_categories": dict(sorted(facts.items())),
        "excluded_profile_reasons": dict(
            sorted(Counter(row["reason_code"] for row in excluded).items())
        ),
        "lossless_transport_verified": True,
        "transport": "READABLE_TABLES_V1"
        if "encoding" in wire
        else "DENSE_DICTIONARIES_V1"
        if "dense_encoding" in wire
        else "COLUMN_TABLES_V1"
        if "table_encoding" in wire
        else "PLAIN_JSON",
        "compression_operations": deepcopy(wire.get("compression_counts", {})),
        "request_dispatched": False,
        "dispatch_note": "PACKET_REFUSED_NO_REQUEST"
        if refused
        else "BUILDER_INVENTORY_ONLY_NOT_PROVIDER_COMPLETION",
    }


def incomplete_refusal_inventory(report):
    """Do not invent retained category counts when a structural bound stops assembly."""
    return {
        "assembly_complete": False,
        "boundary": "STRUCTURAL_LIMIT_BEFORE_COMPLETE_PACKET",
        "raw_report_rows": {
            key: len(report[key])
            for key in ("recommendations", "tickets")
            if isinstance(report, dict) and isinstance(report.get(key), list)
        },
        "categories": None,
        "category_counts_unavailable_reason": "ASSEMBLY_STOPPED_BEFORE_COMPLETE_VALIDATION",
        "request_dispatched": False,
        "dispatch_note": "PACKET_REFUSED_NO_REQUEST",
    }
