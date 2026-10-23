import copy
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage import Database
from goal_analysis.storage.sports_news import NewsStore

PUBLISHED = datetime(2026, 10, 17, 11, tzinfo=UTC)
FETCHED = PUBLISHED + timedelta(minutes=5)
OBSERVED = FETCHED + timedelta(seconds=3)
POST_ID = (int(PUBLISHED.timestamp() * 1000) - 1288834974657) << 22
SOURCE = f"https://x.com/ClubNews/status/{POST_ID}"


def result():
    return {"status": "COMPLETE", "model": "grok-4.7", "citations": [SOURCE],
            "claims": [{"claim_id": "claim-1", "fixture_id": "123", "kind": "INJURY",
                        "claim": "The model reports that the goalkeeper may miss the match.",
                        "source_url": SOURCE, "author": "ClubNews",
                        "published_at": PUBLISHED.isoformat(), "fetched_at": FETCHED.isoformat(),
                        "content_status": "UNVERIFIED",
                        "provenance_status": "CITATION_AND_TIMESTAMP_CHECKED"}]}


@pytest.fixture
def store(tmp_path):
    archive = NewsStore(Database(tmp_path / "news.sqlite3"))
    archive.initialize()
    return archive


def ingest(store, data=None, request_id="request-1", observed=OBSERVED):
    return store.ingest_grok_result(data or result(), request_id=request_id, observed_at=observed)


def test_idempotence_and_summary_with_no_main_schema_dependency(store):
    store.initialize()
    first, second = ingest(store), ingest(store)
    assert first["imports_created"] == first["assertions_created"] == 1
    assert second["imports_created"] == second["assertions_created"] == 0
    assert first["import_id"] == second["import_id"]
    assert store.summary() == {
        "total_assertions": 1, "total_sources": 1, "total_fixtures": 1,
        "total_imports": 1, "claims_skipped": 0, "total_associations": 0,
        "by_kind": {"INJURY": 1}, "by_content_status": {"UNVERIFIED": 1},
        "by_provenance_status": {"CITATION_AND_TIMESTAMP_CHECKED": 1},
    }


def test_preserves_original_source_and_distinguishes_model_summary(store):
    ingest(store)
    claim = store.claims()[0]
    for key in ("claim", "source_url", "author", "fixture_id", "kind",
                "content_status", "provenance_status"):
        assert claim[key] == result()["claims"][0][key]
    assert claim["representation"] == "MODEL_GENERATED_SUMMARY"
    assert claim["event_at"] is None
    assert claim["source_fetched_at"] == FETCHED.isoformat(timespec="microseconds")
    assert claim["observed_at"] == OBSERVED.isoformat(timespec="microseconds")
    assert claim["published_at"] == PUBLISHED.isoformat(timespec="microseconds")
    assert claim["request_id"] == "request-1"
    assert claim["origin_claim_id"] == "claim-1"
    assert claim["model"] == "grok-4.7"
    assert claim["license_status"] == "UNREVIEWED_COMMERCIAL_USE"
    assert "post_text" not in claim


def test_identical_assertion_dedup_preserves_request_local_ids_and_revisions(store):
    ingest(store)
    second = result()
    second["claims"][0]["claim_id"] = "different-local-id"
    second["claims"][0]["fetched_at"] = (FETCHED + timedelta(hours=1)).isoformat()
    counts = ingest(store, second, "request-2", OBSERVED + timedelta(hours=1))
    assert counts["assertions_created"] == 0
    assert counts["assertions_reused"] == 1
    revised = copy.deepcopy(second)
    revised["claims"][0]["claim"] = "A later model summary says the goalkeeper will play."
    ingest(store, revised, "request-3", OBSERVED + timedelta(hours=2))
    assert store.summary()["total_assertions"] == 2
    assert store.summary()["total_sources"] == 1
    assert store.summary()["total_imports"] == 3
    assert len(store.claims()) == 2  # No invented supersession of separate assertions.
    assert store.claims(as_of=OBSERVED)[0]["origin_claim_id"] == "claim-1"


def test_only_accepted_and_cited_claims_imported_even_if_overall_review_unavailable(store):
    data = result()
    data["status"] = "UNAVAILABLE"
    unaccepted = copy.deepcopy(data["claims"][0])
    unaccepted.pop("provenance_status")
    unaccepted["claim_id"] = "unaccepted"
    data["claims"].append(unaccepted)
    uncited = copy.deepcopy(data["claims"][0])
    uncited["source_url"] = "https://x.com/Other/status/9999999999999999999"
    uncited["claim_id"] = "uncited"
    data["claims"].append(uncited)
    counts = ingest(store, data)
    assert counts["claims_accepted"] == 1
    assert counts["claims_skipped"] == 2
    assert len(store.claims()) == 1


def test_claims_as_of_never_leak_publication_or_result_receipt(store):
    ingest(store)
    assert store.claims(as_of=PUBLISHED) == []
    assert store.claims(as_of=OBSERVED - timedelta(microseconds=1)) == []
    assert len(store.claims(as_of=OBSERVED)) == 1
    assert store.claims(fixture_id="other") == []
    with pytest.raises(ValueError, match="timezone-aware"):
        store.claims(as_of=OBSERVED.replace(tzinfo=None))


@pytest.mark.parametrize("changes", [
    {"fetched_at": (OBSERVED + timedelta(hours=1)).isoformat()},
    {"published_at": (PUBLISHED + timedelta(seconds=2)).isoformat()},
    {"author": "DifferentAuthor"}, {"kind": "MADE_UP"}, {"claim": ""},
])
def test_invalid_accepted_claim_rejects_entire_import(store, changes):
    data = result()
    invalid = copy.deepcopy(data["claims"][0])
    invalid.update(claim_id="bad-claim", **changes)
    data["claims"].append(invalid)
    with pytest.raises(ValueError):
        ingest(store, data)
    assert store.summary()["total_imports"] == 0
    assert store.summary()["total_assertions"] == 0


def test_conflicting_duplicate_claim_id_is_rejected(store):
    data = result()
    data["claims"].append({**data["claims"][0], "claim": "A conflicting assertion."})
    with pytest.raises(ValueError, match="conflicting"):
        ingest(store, data)
    assert store.summary()["total_imports"] == 0


def test_write_failure_rolls_back_import_and_assertion(store):
    with store.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_link BEFORE INSERT
            ON sports_news_import_claims
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated storage failure"):
        ingest(store)
    assert store.summary()["total_imports"] == 0
    assert store.summary()["total_assertions"] == 0


def measurements():
    return ({"measured_at": PUBLISHED - timedelta(minutes=10),
             "source_ref": "api_football:odds:sha256-before", "metrics": {"decimal_odds": 2.0}},
            {"measured_at": OBSERVED + timedelta(minutes=10),
             "source_ref": "api_football:odds:sha256-after", "metrics": {"decimal_odds": 1.8}})


def test_observational_measurement_link_idempotence_and_as_of(store):
    ingest(store)
    assertion = store.claims()[0]["assertion_id"]
    before, after = measurements()
    measured = OBSERVED + timedelta(minutes=11)
    first = store.record_association(assertion, fixture_id="123", observed_at=measured,
                                      before=before, after=after, metadata={"market": "1X2"})
    second = store.record_association(assertion, fixture_id="123", observed_at=measured,
                                       before=before, after=after, metadata={"market": "1X2"})
    assert first["associations_created"] == 1
    assert second["associations_created"] == 0
    assert first["association_id"] == second["association_id"]
    assert store.associations(as_of=measured - timedelta(microseconds=1)) == []
    saved = store.associations(as_of=measured)[0]
    assert saved["interpretation"] == "OBSERVATIONAL_NOT_CAUSAL"
    assert saved["before_metrics"] == before["metrics"]
    assert saved["after_metrics"] == after["metrics"]
    assert saved["metadata"] == {"market": "1X2"}
    assert "effect" not in saved and "causal_coefficient" not in saved


def test_association_requires_known_matching_assertion_and_nonfuture_measurements(store):
    ingest(store)
    before, after = measurements()
    assertion = store.claims()[0]["assertion_id"]
    for fixture, observed in (("wrong", OBSERVED + timedelta(hours=1)), ("123", OBSERVED)):
        with pytest.raises(ValueError):
            store.record_association(assertion, fixture_id=fixture, observed_at=observed,
                                     before=before, after=after)
    assert store.summary()["total_associations"] == 0


@pytest.mark.parametrize("table", ["imports", "assertions", "import_claims", "associations"])
def test_immutable_news_tables_reject_update_and_delete(store, table):
    ingest(store)
    before, after = measurements()
    store.record_association(store.claims()[0]["assertion_id"], fixture_id="123",
                             observed_at=OBSERVED + timedelta(hours=1), before=before, after=after)
    field = {"imports": "import_id", "assertions": "assertion_id",
             "import_claims": "import_id", "associations": "association_id"}[table]
    with store.database.connect() as connection:
        for sql in (f"DELETE FROM sports_news_{table}",
                    f"UPDATE sports_news_{table} SET {field} = {field}"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(sql)
