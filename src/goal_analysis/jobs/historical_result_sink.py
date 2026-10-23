"""Incremental SHADOW result sink for one already archived historical page.

Legacy ingestion happens first. Failure before the page checkpoint is retried
from its immutable receipt, idempotently repairing either side after a crash.
There is no archive scan, full view, model call or provider request here.
"""
from collections import Counter

from goal_analysis.normalization.historical_results import normalize_archive_result
from goal_analysis.storage.historical_results import HistoricalResults


class HistoricalResultSink:
    def __init__(self, legacy_archive, *, clock):
        self.legacy = legacy_archive
        self.results = HistoricalResults(legacy_archive.database, clock=clock)
        self.results.initialize()
        self.counts = Counter(matches_created=0, versions_created=0, quarantined_records=0)
        self.rejections = Counter()

    def ingest(self, dataset, **source):
        counts = self.legacy.ingest(dataset, **source)
        prepared = []
        for row in dataset["records"]:
            try:
                prepared.append(normalize_archive_result(row, row["raw"],
                    observed_at=source["fetched_at"], source_url=source["source_url"],
                    source_sha256=source["content_hash"]))
            except (ValueError, TypeError, KeyError) as error:
                self.counts["quarantined_records"] += 1
                self.rejections[str(error)] += 1
        self.counts.update(self.results.ingest(prepared))
        return counts

    def summary(self):
        return {"mode": "SHADOW", "effective_data_path": "LEGACY_UNCHANGED",
                "counts": dict(self.counts), "rejection_counts": dict(self.rejections),
                "external_requests": 0, "reconciliation": "NATIVE_IDS_ONLY_INCREMENTAL"}
