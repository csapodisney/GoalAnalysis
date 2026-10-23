"""Final publication cutoffs survive selection latency, persistence, and display."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from test_model_integration import predictor_fixture
from test_portfolio_flow import NOW, RecordedReviewer, environment

import goal_analysis.jobs.portfolio as portfolio_job
from goal_analysis import tipsters
from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS, validate_settings
from goal_analysis.portfolio_dashboard import ArthurState
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS


@pytest.mark.parametrize("seconds_from_last_kickoff", [-900, -899, 0, 1])
def test_slow_persona_selection_cannot_publish_locked_legacy_model_or_persona_picks(
    tmp_path, monkeypatch, seconds_from_last_kickoff,
):
    config, football, odds, cache, _, _, _ = environment(tmp_path)
    clock = [NOW]
    captured = {}
    original_builder = tipsters.build_tipster_recommendations
    original_guard = portfolio_job._guard_final_publication

    def slow_builder(enriched, models, settings, now, *, diversity_options=None):
        cards = original_builder(enriched, models, settings, now,
                                 diversity_options=diversity_options)
        captured["personas"] = deepcopy(cards)
        # The real selector first finds valid upcoming picks; processing then
        # crosses the publication lockout, including its exact boundary.
        clock[0] = max(datetime.fromisoformat(f["kickoff"]) for f in enriched["fixtures"])
        clock[0] += timedelta(seconds=seconds_from_last_kickoff)
        return cards

    def capture_guard(report, published_at):
        if any(item.get("legs") for item in report.get("recommendations", [])):
            captured["before_guard"] = deepcopy(report)
        original_guard(report, published_at)

    monkeypatch.setattr(tipsters, "build_tipster_recommendations", slow_builder)
    monkeypatch.setattr(portfolio_job, "_guard_final_publication", capture_guard)
    reviewer = RecordedReviewer()
    report = portfolio_job.run_portfolio(
        config, {}, football, odds, cache, NOW.date(), reviewer,
        clock=lambda: clock[0], model_predictor=predictor_fixture,
    )["artifacts"]["report"]

    selected_before = {r["profile_id"] for r in captured["before_guard"]["recommendations"] if r["legs"]}
    # The synthetic predictor need not clear the club's positive-value gates;
    # the final lockout must still remove the model and DAILY_223 selections.
    assert captured["personas"]
    assert captured["before_guard"]["tickets"]
    assert {"daily223", *MODEL_PROFILE_IDS}.issubset(selected_before)
    assert len(reviewer.calls) == 1
    assert report["finished_at"] == clock[0].isoformat()
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert all(not report[group] for group in ("tickets", "preview_tickets", "profile_proposals"))
    assert all(not r["legs"] for r in report["recommendations"])

    cards = {r["profile_id"]: r for r in report["recommendations"]}
    for profile in selected_before:
        card = cards[profile]
        assert card["status"] == ("ABSTAINED" if profile in TIPSTER_PROFILE_IDS else "NO_FIXTURES")
        assert card["reason_code"] == "EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT"
        assert card["combined_price"] is card["stake_eur"] is card["portfolio_ticket_id"] is None
        assert not card["playable"] and not card["selected_for_portfolio"]
        assert "recommendation_id" not in card
    assert report["tipster_club"]["published_count"] == 0
    assert report["recommendation_overlap"] == []
    assert report["funnel"]["tickets"] == report["funnel"]["recommendations"] == 0
    assert any(d.get("code") == "EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT" for d in report["diagnostics"])
    original_hash = report["report_sha256"]
    assert original_hash == canonical_sha256({k: v for k, v in report.items() if k != "report_sha256"})

    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: clock[0])
    assert ledger.save_run(report)["saved"] is True
    assert ledger.save_run(report)["saved"] is False
    assert ledger.list_runs() == [report]
    assert ledger.list_tickets(date=NOW.date().isoformat()) == []
    assert ledger.list_recommendations(date=NOW.date().isoformat(), archive=True) == []
    with pytest.raises(ValueError, match="Nincs rögzíthető"):
        ledger.freeze_run(report["run_id"])

    view = ArthurState(tmp_path).snapshot(NOW.date().isoformat())
    assert not view["tickets"] and not view["recommendations"] and not view["recommendation_archive"]
    assert not view["freezes"]
    assert len(view["tipster_club"]["recommendations"]) == len(TIPSTER_PROFILE_IDS)
    assert all(not r["legs"] and r["status"] == "ABSTAINED" for r in view["tipster_club"]["recommendations"])
    assert view["history"] == [report]
    assert view["latest"]["report_sha256"] == original_hash
    assert ledger.list_runs() == [report]  # Reads do not rewrite the canonical report.


def test_dashboard_does_not_resurrect_unindexed_legs_from_a_late_immutable_report():
    rejected_profile, abstaining_profile = TIPSTER_PROFILE_IDS[:2]
    late_pick = {
        "profile_id": rejected_profile, "status": "READY", "recommendation_id": "rejected-late-pick",
        "legs": [{"fixture_id": "api_football:1", "kickoff": NOW.isoformat()}],
    }
    abstention = {"profile_id": abstaining_profile, "status": "ABSTAINED", "legs": [],
                  "reason_code": "EVENT_STARTED_BEFORE_PUBLICATION"}
    original = {"finished_at": (NOW + timedelta(seconds=1)).isoformat(),
                "recommendations": [late_pick, abstention]}
    untouched = deepcopy(original)
    club = ArthurState.tipster_snapshot(validate_settings({}), [original], [], {"members": []})
    assert club["recommendations"] == [abstention]
    assert club["overlap"] == []
    assert original == untouched
