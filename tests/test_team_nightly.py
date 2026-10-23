"""Real local learning/team/scout orchestration; every external transport is fake."""

from copy import deepcopy
from datetime import timedelta

from test_portfolio_ledger import NOW, Football, fixture, report, ticket
from test_team_manager import manifest
from test_team_scouting import GitHub

from goal_analysis.config.portfolio import DEFAULT_PROBATION_PROFILE_IDS, DEFAULT_SETTINGS
from goal_analysis.jobs.nightly import NightlyWorkflow
from goal_analysis.learning import LearningAgent
from goal_analysis.learning._metrics import digest
from goal_analysis.learning.reviewer import llm_result
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.manager import TeamManager
from goal_analysis.team.routing import generation_plan


def test_real_nightly_learning_team_and_scout_retry_preserves_future_only_decisions(tmp_path, monkeypatch):
    clock = [NOW]
    manager = TeamManager(tmp_path, clock=lambda: clock[0])
    version = manifest("gauss")
    monkeypatch.setattr("goal_analysis.team.manager.strategy_manifest", lambda settings: version)
    manager.evaluate(clock[0])  # Actual enrollment precedes every test publication.
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3",
                             now=lambda: clock[0], refresh_call_limit=100)
    before = generation_plan(DEFAULT_SETTINGS["enabled_profiles"], manager.snapshot())
    assert "gauss" in before["live_profiles"] and before["paper_profiles"] == list(DEFAULT_PROBATION_PROFILE_IDS)
    for index in range(260):
        clock[0] = NOW + timedelta(days=index + 1)
        item = ticket(f"ticket-{index}", "gauss", fixture_ids=tuple(range(index * 3 + 1, index * 3 + 4)))
        item.update(date=clock[0].date().isoformat(), created_at=clock[0].isoformat(),
                    strategy_version="v1", strategy_manifest_sha256=version["manifest_sha256"],
                    publication_scope="LIVE")
        for offset, leg in enumerate(item["legs"], 1):
            fixture_id = index * 3 + offset
            leg.update(kickoff=(clock[0] + timedelta(hours=3)).isoformat(), provider_market_id=5,
                       bookmaker="b_api_football_8", quote_provider="api_football",
                       quote_source_id=f"https://v3.football.api-sports.io/odds?fixture={fixture_id}&bookmaker=8&bet=5")
        original = report(item, identifier=f"run-{index}", observed_at=clock[0])
        original.update(date=item["date"], recommendations=[deepcopy(item)], strategy_manifest=version)
        ledger.save_run(original)
    clock[0] = NOW + timedelta(days=400)
    football = Football({i: fixture(i, fulltime=(0, 0), halftime=(0, 0)) for i in range(1, 781)})
    for _ in range(8):
        assert ledger.refresh_results(football)["errors"] == []
    assert len(football.calls) == 780
    github, model_calls = GitHub(), []
    monkeypatch.setattr("goal_analysis.team.scouting.github_transport", github)

    def review(self, packet):
        model_calls.append(deepcopy(packet))
        return llm_result("COMPLETE", {"requests": 1, "input_tokens": 100, "output_tokens": 20,
                                      "total_tokens": 120}, proposals=[packet["proposal_options"][0]])

    monkeypatch.setattr("goal_analysis.learning.CodexLearningReviewer.review", review)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: clock[0])
    first = workflow._learn(clock[0])
    assert first["llm"]["status"] == "COMPLETE"
    assert first["team"]["status"] == "COMPLETE" and first["scouting"]["status"] == "COMPLETE"
    assert len(model_calls) == 1 and len(github.calls) == 9
    after = manager.snapshot()
    gauss = next(m for m in after["members"] if m["profile_id"] == "gauss")
    assert gauss["status"] == "SUSPENDED" and gauss["decision"] == "SUSPEND"
    assert gauss["metrics"]["tickets"] == 260 and gauss["metrics"]["unique_fixtures"] == 780
    assert gauss["metrics"]["roi_pct"] == -100
    assert any(plan["code"] == "REVIEW_LOSS_PATTERN" for plan in gauss["training_plan"])
    assert all(event["automatic_wager_changes"] is False for event in after["events"])
    future = generation_plan(DEFAULT_SETTINGS["enabled_profiles"], after)
    assert "gauss" not in future["live_profiles"] and "gauss" in future["blocked_profiles"]
    assert "gauss" in before["live_profiles"]  # The previously frozen run plan stays unchanged.
    persisted = LearningAgent(tmp_path, clock=lambda: clock[0]).latest()
    assert "team" not in persisted and "scouting" not in persisted
    assert persisted["report_hash"] == digest({k: v for k, v in persisted.items() if k != "report_hash"})
    assert workflow._learn(clock[0]) == first
    assert len(model_calls) == 1 and len(github.calls) == 9
    assert len(manager.snapshot()["events"]) == 1
    assert LearningAgent(tmp_path, clock=lambda: clock[0]).latest() == persisted
    assert ledger.list_tickets(mode="actual") == []
