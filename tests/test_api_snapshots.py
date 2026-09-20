import json
from pathlib import Path

from goal_analysis.providers import ApiFootballClient
from goal_analysis.storage import Database, SnapshotStore


def test_client_preserves_exact_raw_response(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    snapshots = SnapshotStore(database, tmp_path / "raw")
    raw = b'{ "response": [], "errors": [] }\n'

    def transport(url, headers):
        return json.loads(raw), {"content-type": "application/json"}, raw

    client = ApiFootballClient("fake", transport=transport, snapshot_store=snapshots)
    client.request("fixtures", {"date": "2026-09-20"})

    with database.connect() as connection:
        row = connection.execute(
            "SELECT content_hash, source_url FROM raw_snapshots"
        ).fetchone()
    assert row is not None
    assert snapshots.read(row["content_hash"]) == raw
    assert "x-apisports-key" not in row["source_url"]
