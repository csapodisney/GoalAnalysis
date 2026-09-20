import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from goal_analysis.dashboard import DashboardState, discover_reports, make_handler


def report(status="COMPLETE", price=12.4):
    return {
        "date": "2026-09-21",
        "observed_at": "2026-09-21T06:30:00+00:00",
        "construction_status": status,
        "combined_price": price,
        "bookmaker_selection": {"selected": "book-a", "method": "FALLBACK"},
        "input_candidate_count": 10,
        "eligible_candidate_count": 6,
        "data_issues": [{"status": "TEST"}],
        "usage": {"football_calls": 2},
        "legs": [
            {
                "home_team": "Home",
                "away_team": "Away",
                "kickoff": "2026-09-21T12:00:00+00:00",
                "market_key": "h2h",
                "selection_key": "home",
                "period": "FULL_TIME",
                "decimal_price": 2.0,
                "support_score": 75.0,
            }
        ],
    }


def test_report_discovery_uses_only_valid_run_directories(tmp_path):
    reports = tmp_path / "reports/daily223"
    valid = reports / "valid-run"
    valid.mkdir(parents=True)
    (valid / "report.json").write_text(json.dumps(report()), "utf-8")
    invalid = reports / "invalid-run"
    invalid.mkdir()
    (invalid / "report.json").write_text("not-json", "utf-8")

    result = discover_reports(reports)

    assert len(result) == 1
    assert result[0]["run_id"] == "valid-run"
    assert result[0]["bookmaker"] == "book-a"
    assert result[0]["legs"][0]["decimal_price"] == 2.0


def test_dashboard_status_reports_presence_but_never_secret_values(tmp_path, monkeypatch):
    config = tmp_path / "config/daily223-live.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "leagues": [{"competition_id": "DE1"}],
                "odds_region": "eu",
                "preferred_bookmakers": ["betano"],
            }
        ),
        "utf-8",
    )
    monkeypatch.setenv("API_FOOTBALL_KEY", "football-secret")
    monkeypatch.setenv("THE_ODDS_API_KEY", "odds-secret")
    state = DashboardState(tmp_path, config)

    serialized = json.dumps(state.snapshot())

    assert '"api_football": true' in serialized
    assert '"the_odds_api": true' in serialized
    assert "football-secret" not in serialized
    assert "odds-secret" not in serialized


def test_run_endpoint_requires_the_private_dashboard_token():
    class FakeState:
        starts = 0

        def snapshot(self):
            return {"ok": True}

        def start_run(self):
            self.starts += 1
            return True, "started"

    state = FakeState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(state, "private-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/api/status") as response:
            assert json.loads(response.read()) == {"ok": True}
        bad = urllib.request.Request(f"{base}/api/run", data=b"", method="POST")
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(bad)
        assert raised.value.code == 403
        good = urllib.request.Request(
            f"{base}/api/run",
            data=b"",
            method="POST",
            headers={"X-Dashboard-Token": "private-token"},
        )
        with urllib.request.urlopen(good) as response:
            assert response.status == 202
        assert state.starts == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
