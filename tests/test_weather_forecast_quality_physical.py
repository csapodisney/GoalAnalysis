"""Genuine TESTONLY stores and the public normal learning save/load path.

Controlled fixture IDs/times are not actual prematch forecasts or PIT evidence.
The short factory root and one-letter children avoid Windows MAX_PATH failures.
"""

import json
import math
import shutil
import socket
import sqlite3
import urllib.parse
import urllib.request
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_weather_feature_capture import environment

import goal_analysis
from goal_analysis.jobs import weather_feature_capture
from goal_analysis.learning import LearningAgent
from goal_analysis.learning._metrics import digest, measure
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage import Database
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.storage.temporal import TemporalStore


def test_physical_sources_optional_normal_learning_and_immutable_report_hash(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("wf").resolve()
    production = Path(goal_analysis.__file__).resolve().parents[2]
    (base / "TESTONLY.json").write_text(json.dumps({"TESTONLY": True, "scope": "weather-quality-unit", "controlled_timeline_not_actual_UTC_PIT": True}), encoding="utf-8")
    forbidden = Mock(side_effect=AssertionError("NO_NETWORK_MODEL_OR_EXTERNAL_REQUEST"))
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(goal_analysis.models, "predict_models", forbidden)
    original_init, original_connect = Database.__init__, sqlite3.connect
    constructor_count = []

    def owned(root):
        root = Path(root).resolve()
        if root == production or root.parent != base or not (base / "TESTONLY.json").is_file():
            raise ValueError("PRODUCTION_OR_NONOWNED_ROOT_REJECTED_BEFORE_CONSTRUCTOR_WRITE_COPY")
        if not (root / "TESTONLY.json").is_file():
            raise ValueError("TESTONLY_CHILD_MARKER_REQUIRED")
        return root

    def guarded_init(self, path, *args, **kwargs):
        path = Path(path).resolve()
        assert path.is_relative_to(base)
        owner = next(parent for parent in path.parents if parent.parent == base)
        owned(owner)
        constructor_count.append(path)
        return original_init(self, path, *args, **kwargs)

    def guarded_connect(path, *args, **kwargs):
        value = str(path)
        if value.startswith("file:"):
            value = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
            if len(value) > 3 and value[0] == "/" and value[2] == ":":
                value = value[1:]
        absolute = Path(value).resolve()
        assert absolute.is_relative_to(base)
        owned(next(parent for parent in absolute.parents if parent.parent == base))
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(Database, "__init__", guarded_init)
    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    with pytest.raises(ValueError, match="BEFORE_CONSTRUCTOR"):
        owned(production)
    assert constructor_count == []
    positive = base / "p"
    positive.mkdir()
    (positive / "TESTONLY.json").write_text("TESTONLY_CONTROLLED_UNIT_TIMELINE", encoding="utf-8")
    owned(positive)
    assert len(str(positive / "data/arthur/native-shadow/controlled-1/fit/datasets" / ("f" * 64 + ".json"))) < 250
    run, report, at, old_ledger, _output, database = environment(positive)
    model_at = utc(report["observed_at"])
    capture = weather_feature_capture.capture(positive, run, sha(report), clock=lambda: at)
    assert capture["eligible_feature_pairs"] == 7
    late = model_at + timedelta(hours=10)
    temporal = TemporalStore(database, positive / "data/raw", clock=lambda: late)
    temporal.capture("raw")
    temporal.capture("result")
    normal_path = positive / "data/arthur/normal-learning.sqlite3"
    normal = PortfolioLedger(normal_path, now=lambda: model_at + timedelta(minutes=3))
    normal.save_run(report)
    with closing(sqlite3.connect(old_ledger.as_uri() + "?mode=ro", uri=True)) as db:
        result_row = db.execute("SELECT fixture_id,observed_at,payload_hash,payload_json FROM portfolio_results").fetchone()
    with normal._connection() as db:
        db.execute("INSERT INTO portfolio_results(fixture_id,observed_at,payload_hash,payload_json) VALUES(?,?,?,?)", result_row)
        db.execute("INSERT INTO portfolio_result_observations(fixture_id,result_id,observed_at) VALUES(?,?,?)", (result_row[0], 1, result_row[1]))

    def build(root, cutoff=late):
        owned(root)
        return LearningAgent(root, ledger=SimpleNamespace(path=root / "data/arthur/normal-learning.sqlite3"))._build(cutoff, cutoff)

    first = build(positive)
    diagnostic = first["weather_conditioned_forecast_quality"]
    measured = measure(normal_path, late)
    assert all(first[key] == value for key, value in measured.items())
    assert diagnostic["source_authentication"] == "ACTUAL_PHYSICAL_SOURCE_READERS"
    assert diagnostic["weather_stratum_basis"] == "PREMATCH_FORECAST_FEATURE_PROXY_NOT_REALIZED_WEATHER"
    assert diagnostic["accounting"]["partitions"] == {"SCORED": 7}
    assert diagnostic["baseline_scores"]["brier_binary"] == pytest.approx(.88 / 7)
    assert diagnostic["baseline_scores"]["log_loss_binary"] == pytest.approx(-sum(map(math.log, (.5, .8, .7, .7, .7, .6, .6))) / 7)
    assert first["metrics"]["research"]["forecast_quality"]["brier_binary"] == pytest.approx(.09)
    rows = diagnostic["dataset"]["rows"]
    assert len(rows) == 7 and len({row["fixture_id"] for row in rows}) == 1
    assert len({utc(row["kickoff"]).date() for row in rows}) == len({row["model_version"] for row in rows}) == 1
    assert len({row["market_key"] for row in rows}) == 3
    assert all(row["weather_model_probability"] is None for row in rows)
    assert diagnostic["heldout_weather_comparison_complete"] is diagnostic["live_promotion_allowed"] is False
    baseline_fields = {key: value for key, value in first.items() if key != "weather_conditioned_forecast_quality"}

    def copy_root(name):
        target = base / name
        assert target.parent == base and not target.exists()
        shutil.copytree(positive, target)
        return owned(target)

    for name in ("r", "s", "f", "o", "i", "g"):
        root = copy_root(name)
        if name == "r":
            with closing(sqlite3.connect((root / "data/goal_analysis.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
                relative = db.execute("SELECT relative_path FROM raw_snapshots ORDER BY rowid LIMIT 1").fetchone()[0]
            (root / "data/raw" / relative).write_bytes(b"{}")
        elif name == "f":
            (root / "data/arthur/native-shadow" / run / "analysis.json").write_bytes(b"{}")
        elif name == "o":
            with closing(sqlite3.connect(root / "data/goal_analysis.sqlite3")) as db, db:
                db.execute("DROP TRIGGER temporal_receipts_no_update")
                db.execute("UPDATE temporal_receipts SET source_sha256=? WHERE namespace='raw' AND julianday(observed_at)>julianday(?)", ("f" * 64, (model_at + timedelta(hours=8)).isoformat()))
        else:
            work = root / "data/arthur" / ("native-calibration-capture" if name == "g" else "weather-feature-capture") / run
            body, receipt = json.loads((work / "capture.json").read_bytes()), json.loads((work / "receipt.json").read_bytes())
            if name == "g":
                body["rows"][0]["probability"] = .999
            elif name == "s":
                body["rows"][0]["weather_context_sha256"] = "f" * 64
            else:
                body["rows"][0]["target"]["provider_home_team_id"] = "tm_99999"
            receipt["capture_sha256"] = sha(body)
            receipt["receipt_id"] = sha({key: value for key, value in receipt.items() if key != "receipt_id"})
            (work / "capture.json").write_text(json.dumps(body), encoding="utf-8")
            (work / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        changed = build(root)
        sidecar = changed.pop("weather_conditioned_forecast_quality")
        assert changed == baseline_fields
        assert sidecar.get("baseline_scores", {}).get("eligible_selections", 0) == 0
    early = build(positive, utc(capture["available_at"]) - timedelta(microseconds=1))
    assert early["weather_conditioned_forecast_quality"]["baseline_scores"]["eligible_selections"] == 0

    # Existing normal run/save/load API, once. A historical controlled day makes
    # the existing reviewer-skip branch deterministic; no external call is made.
    reviewer = Mock()
    reviewer.review.side_effect = forbidden
    now = late + timedelta(days=2)
    agent = LearningAgent(positive, ledger=SimpleNamespace(path=normal_path), clock=lambda: now)
    saved = agent.run(late, reviewer=reviewer)
    loaded = agent.latest()
    assert saved == loaded
    assert saved["metrics"] == measured["metrics"] and saved["schema_version"] == 1
    assert saved["weather_conditioned_forecast_quality"]["version"] == diagnostic["version"]
    assert saved["weather_conditioned_forecast_quality"]["dataset"]["manifest"] == diagnostic["dataset"]["manifest"]
    value = deepcopy(saved)
    report_hash = value.pop("report_hash")
    assert digest(value) == report_hash
    value["weather_conditioned_forecast_quality"]["baseline_scores"]["brier_binary"] = .0001
    assert digest(value) != report_hash
    export = next((positive / "reports/learning").glob(saved["date"] + "-*.json"))
    assert json.loads(export.read_bytes()) == saved
    reviewer.review.assert_not_called()
    forbidden.assert_not_called()
