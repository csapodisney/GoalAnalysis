"""Bounded offline witnesses for already committed local source records.

Only the additive temporal index is written. Provider clients, legacy source
tables, decision paths and scheduler configuration are not changed here.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from goal_analysis.storage.db import Database
from goal_analysis.storage.temporal import NAMESPACES, TemporalStore

VERSION = "temporal-availability-backfill-v1"


def backfill_temporal(root, *, max_rows=150_000, batch_size=1000, clock=None, cursors=None):
    """Capture at most max_rows across all namespaces, without provider calls.

    Optional cursors are source rowids returned by an earlier bounded call.
    They are scan positions, never event times or evidence of availability.
    Missing proofs remain missing; rejected rows are counted and can be
    rechecked later by restarting that namespace from an earlier cursor.
    """
    if (type(max_rows) is not int or not 1 <= max_rows <= 1_000_000
            or type(batch_size) is not int or not 1 <= batch_size <= 10_000):
        raise ValueError("INVALID_TEMPORAL_BACKFILL_BUDGET")
    if cursors is not None and (
            not isinstance(cursors, dict) or set(cursors) - set(NAMESPACES)
            or any(type(value) is not int or not 0 <= value <= 2**63 - 1
                   for value in cursors.values())):
        raise ValueError("INVALID_TEMPORAL_BACKFILL_CURSORS")
    starts = {namespace: (cursors or {}).get(namespace, 0) for namespace in NAMESPACES}
    rows = {namespace: {"scanned": 0, "created": 0, "rejected": 0,
        "rejection_counts": {}, "start_after": starts[namespace],
        "next_after": starts[namespace], "exhausted": False} for namespace in NAMESPACES}
    report = {"version": VERSION, "mode": "SHADOW",
        "effective_data_path": "LEGACY_UNCHANGED", "external_requests": 0,
        "scanned": 0, "created": 0, "rejected": 0, "by_namespace": rows,
        "cursors": dict(starts), "exhausted": False, "pending": True,
        "pending_namespaces": list(NAMESPACES), "rejection_examples": [],
        "max_rows": max_rows, "batch_size": batch_size,
        "availability_basis": "SOURCE_PRESENCE_UPPER_BOUND"}
    root = Path(root).resolve()
    path = root / "data/goal_analysis.sqlite3"
    if not path.is_file():
        return {**report, "status": "MISSING_SOURCE", "summary": None}
    store = TemporalStore(Database(path), root / "data/raw", clock=clock)
    store.initialize()
    for namespace in NAMESPACES:
        item, reasons = rows[namespace], Counter()
        while report["scanned"] < max_rows:
            budget = min(batch_size, max_rows - report["scanned"])
            batch = store.capture(namespace, after=item["next_after"], limit=budget)
            for key in ("scanned", "created"):
                item[key] += batch[key]
                report[key] += batch[key]
            item["rejected"] += len(batch["rejected"])
            report["rejected"] += len(batch["rejected"])
            for rejection in batch["rejected"]:
                reasons[rejection["reason"]] += 1
                if len(report["rejection_examples"]) < 1000:
                    report["rejection_examples"].append({"namespace": namespace, **rejection})
            item.update(next_after=batch["next_after"], exhausted=batch["exhausted"])
            report["cursors"][namespace] = batch["next_after"]
            if batch["exhausted"]:
                break
            if not batch["scanned"]:
                raise ValueError("TEMPORAL_CAPTURE_MADE_NO_PROGRESS")
        item["rejection_counts"] = dict(reasons)
    report["pending_namespaces"] = [namespace for namespace, item in rows.items()
                                    if not item["exhausted"]]
    report["pending"] = bool(report["pending_namespaces"])
    report["exhausted"] = not report["pending"]
    report["status"] = "ROW_LIMIT" if report["pending"] else "PARTIAL" if report["rejected"] else "COMPLETE"
    report["summary"] = store.summary()
    return report


def main():
    """Explicit local invocation; no autonomous background work is installed."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--max-rows", type=int, default=150_000)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--cursors-json", type=json.loads)
    arguments = parser.parse_args()
    result = backfill_temporal(arguments.root, max_rows=arguments.max_rows,
        batch_size=arguments.batch_size, cursors=arguments.cursors_json)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
