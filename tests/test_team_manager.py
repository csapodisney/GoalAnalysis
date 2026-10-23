"""Synthetic supervisor decisions only: no provider, model or wager side effects."""

import sqlite3
from copy import deepcopy
from datetime import timedelta

import pytest
from test_learning import populated
from test_portfolio_ledger import NOW, Football, fixture, report, ticket

from goal_analysis.config.portfolio import DEFAULT_PROBATION_PROFILE_IDS
from goal_analysis.learning._findings import findings_and_proposals
from goal_analysis.learning._metrics import digest, measure
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.manager import TeamManager
from goal_analysis.team.metrics import POLICY, PROFILES, assess, build_cohorts


def manifest(profile="laplace", suffix="v1"):
    base = {"schema_version": 1, "code_sha256": digest(suffix),
            "settings_sha256": digest({"settings": 1}), "source_files": {"models.py": digest(suffix)}}
    identity = digest(base)
    return {**base, "manifest_sha256": identity, "profiles": {profile: {"version_id": suffix}}}


def record(index, *, profile="laplace", version="v1", won=True, start=NOW):
    publication = start + timedelta(days=index + 1)
    original = manifest(profile, version)
    return {
        "id": f"{profile}-{index}", "profile_id": profile, "run_id": f"run-{index}",
        "created_at": publication.isoformat(), "date": publication.date().isoformat(),
        "publication_scope": "PAPER_AUDITION" if profile == "laplace" else "LIVE",
        "version": version, "strategy_manifest_sha256": original["manifest_sha256"],
        "strategy_manifest": original, "original_run_match": True,
        "outcome": "WON" if won else "LOST", "financial": None,
        "source_refs": [{"table": "portfolio_recommendations", "key": str(index), "hash": digest(index)}],
        "run_reference": {"table": "portfolio_runs", "key": str(index), "hash": digest([index])},
        "legs": [{"fixture_id": f"api_football:{index * 2 + leg + 1}",
                  "kickoff": (publication + timedelta(hours=3)).isoformat(),
                  "market": "FULL_TIME:h2h", "selection": "home", "decimal_price": price,
                  "outcome": "WON" if won else "LOST", "model_probability": None,
                  "quote_provenance": {"quote_provider": "api_football", "bookmaker": "b_api_football_8",
                                       "region": "eu", "currency": "EUR", "provider_market_id": 1,
                                       "quote_source_id": "https://v3.football.api-sports.io/odds"
                                       f"?fixture={index * 2 + leg + 1}&bookmaker=8&bet=1"}}
                 for leg, price in enumerate((1.4, 1.5))],
    }


def measured(records):
    return {"records": {"published": records, "actual": [], "research": [], "frozen": []},
            "coverage": {"invalid_records": 0}}


def cohort(records, profile="laplace", status="PROBATION", as_of=None):
    member = dict(TeamManager._initial(profile, NOW.isoformat()), status=status)
    cutoff = as_of or NOW + timedelta(days=400)
    groups, evidence = build_cohorts(measured(records), [member], cutoff)
    return assess(groups, [member], cutoff)[0], evidence


def test_readonly_snapshot_no_side_effects_and_safe_catalogue(tmp_path):
    manager = TeamManager(tmp_path, clock=lambda: NOW)
    snapshot = manager.snapshot()
    assert not list(tmp_path.iterdir())
    assert [m["profile_id"] for m in snapshot["members"]] == list(PROFILES)
    assert len(snapshot["members"]) == 30
    assert all(m["status"] == ("PROBATION" if m["profile_id"] in DEFAULT_PROBATION_PROFILE_IDS else "ACTIVE")
               for m in snapshot["members"])
    assert manager.effective_profiles(["daily223", "laplace", "evil", "gauss", "gauss"]) == ["daily223", "gauss"]
    assert snapshot["automatic_wager_changes"] is False
    assert not list(tmp_path.iterdir())


def test_tiny_historical_and_hit_rate_only_never_change_status():
    small, _ = cohort([record(i, profile="gauss", won=False) for i in range(30)], "gauss", "ACTIVE")
    assert small["status"] == "ACTIVE" and small["decision"] == "KEEP"
    assert "INSUFFICIENT_TICKETS" in small["qualification"]["blockers"]
    older, _ = cohort([record(i, start=NOW - timedelta(days=500)) for i in range(300)])
    assert older["metrics"]["tickets"] == 0 and older["decision"] == "KEEP"
    unpriced = [record(i) for i in range(260)]
    for item in unpriced:
        item["legs"][0]["decimal_price"] = None
    unknown, _ = cohort(unpriced)
    assert unknown["status"] == "PROBATION" and unknown["metrics"]["roi_pct"] is None
    assert "MISSING_ODDS" in unknown["qualification"]["blockers"]


def test_predeclared_minima_and_bootstrap_decisions_are_deterministic():
    records = [record(i) for i in range(260)]
    first, evidence = cohort(records)
    second, evidence_again = cohort(records)
    assert first == second and evidence == evidence_again
    assert first["decision"] == "PROMOTE" and first["status"] == "ACTIVE"
    assert first["metrics"]["tickets"] == 260
    assert first["metrics"]["unique_fixtures"] == 520
    assert first["metrics"]["roi_pct"] == pytest.approx(110)
    assert first["metrics"]["ci_low_pct"] > POLICY["promotion_margin_pct"]
    assert first["metrics"]["actual"]["financial"]["roi_pct"] is None
    assert first["awards"][0]["earned"] and first["awards"][0]["qualified"]
    assert first["podium"]["rank"] == 1
    item = evidence[0]
    assert item["evidence_id"] == "team-ev-" + digest({k: v for k, v in item.items() if k != "evidence_id"})[:24]
    assert first["evidence_ids"] == [item["evidence_id"]] and len(item["source_refs"]) == 520
    losses, _ = cohort([record(i, profile="gauss", won=False) for i in range(260)], "gauss", "ACTIVE")
    assert losses["decision"] == "SUSPEND" and losses["status"] == "SUSPENDED"
    assert losses["metrics"]["ci_high_pct"] < -5


@pytest.mark.parametrize("mutation,blocker", [
    (lambda r: r["legs"][0].update(decimal_price=None), "MISSING_ODDS"),
    (lambda r: r["legs"][0]["quote_provenance"].update(quote_source_id="https://example.org"), "AMBIGUOUS_PROVENANCE"),
    (lambda r: r["legs"][0]["quote_provenance"].update(bookmaker="another-book"), "AMBIGUOUS_PROVENANCE"),
    (lambda r: r.update(outcome="PENDING"), "UNRESOLVED_RESULTS"),
    (lambda r: r["legs"][0].update(outcome="VOID"), "UNRESOLVED_RESULTS"),
    (lambda r: r.update(publication_scope=None), "AMBIGUOUS_PROVENANCE"),
])
def test_one_missing_mature_record_blocks_favourable_subset(mutation, blocker):
    records = [record(i) for i in range(261)]
    mutation(records[0])
    result, _ = cohort(records)
    assert result["metrics"]["tickets"] == 260
    assert result["status"] == "PROBATION" and blocker in result["qualification"]["blockers"]
    assert result["metrics"]["ci_low_pct"] is None


def test_versions_and_quote_ages_are_not_reconstructed_or_pooled():
    records = [record(i, version="old" if i < 259 else "new") for i in range(260)]
    result, _ = cohort(records)
    assert result["metrics"]["tickets"] == 1 and result["metrics"]["other_version_tickets"] == 259
    assert result["decision"] == "KEEP"
    records[-1]["strategy_manifest"]["code_sha256"] = "tampered"
    result, _ = cohort(records)
    assert "UNKNOWN_VERSION" in result["qualification"]["blockers"]
    records[-1]["strategy_manifest"]["profiles"] = []
    result, _ = cohort(records)
    assert "UNKNOWN_VERSION" in result["qualification"]["blockers"]
    # Timestamp is intentionally absent: original source identity matters, not quote age.
    good, _ = cohort([record(i) for i in range(260)])
    assert good["decision"] == "PROMOTE"


def test_cutoff_maturity_and_cross_day_correlation():
    records = [record(i) for i in range(260)]
    result, _ = cohort(records, as_of=NOW + timedelta(days=100))
    assert result["metrics"]["total_tickets"] == 100
    assert result["metrics"]["tickets"] == 96  # 72h plus kickoff offset.
    assert result["metrics"]["immature_tickets"] == 4
    records[-1]["legs"][0]["fixture_id"] = records[0]["legs"][0]["fixture_id"]
    records[-1]["legs"][0]["quote_provenance"] = deepcopy(records[0]["legs"][0]["quote_provenance"])
    result, _ = cohort(records)
    assert "SHARED_FIXTURES_ACROSS_DAYS" in result["qualification"]["blockers"]
    assert result["decision"] == "KEEP"


def test_suspended_cannot_automatically_promote_even_with_winning_history():
    result, _ = cohort([record(i) for i in range(260)], status="SUSPENDED")
    assert result["status"] == "SUSPENDED"
    assert "SUSPENDED_REQUIRES_MANUAL_RETURN" in result["qualification"]["blockers"]


def test_daily_evaluation_idempotency_atomic_event_and_manual_reversal(tmp_path, monkeypatch):
    time = [NOW]
    manager = TeamManager(tmp_path, clock=lambda: time[0])
    monkeypatch.setattr("goal_analysis.team.manager.measure", lambda *a, **kw: measured([record(i) for i in range(260)]))
    monkeypatch.setattr("goal_analysis.team.manager.strategy_manifest", lambda settings: manifest())
    enrolled = manager.evaluate(NOW)
    assert enrolled["status"] == "NO_DATA" and not enrolled["events"]
    time[0] = NOW + timedelta(days=400)
    promoted = manager.evaluate(time[0])
    assert len(promoted["events"]) == 1 and promoted["events"][0]["to_status"] == "ACTIVE"
    assert manager.effective_profiles(["laplace"]) == ["laplace"]
    assert manager.evaluate(time[0] + timedelta(seconds=-1)) == promoted
    snapshot = manager.snapshot()
    assert snapshot["latest_evaluation"] == promoted and len(snapshot["events"]) == 1
    assert next(m for m in snapshot["members"] if m["profile_id"] == "laplace")["decision"] == "PROMOTE"
    time[0] += timedelta(hours=1)
    manager.set_status("laplace", "SUSPENDED", "Ellenőrzés idejére felfüggesztve.")
    manager.set_status("daily223", "SUSPENDED", "Kézi felülbírálás.")
    assert manager.effective_profiles(["daily223", "gauss", "laplace"]) == ["gauss"]
    time[0] += timedelta(hours=1)
    restored = manager.set_status("laplace", "PROBATION", "Új papírpróba indul.")
    laplace = next(m for m in restored["members"] if m["profile_id"] == "laplace")
    assert laplace["evidence_since"] == time[0].isoformat()
    assert not laplace["metrics_current_epoch"]
    assert manager.effective_profiles(["laplace"]) == []
    restored = manager.set_status("laplace", "ACTIVE", "Kifejezett kézi kivétel.")
    assert restored["events"][-1]["source"] == "MANUAL"
    assert restored["events"][-1]["reason_code"] == "MANUAL_OVERRIDE"
    assert manager.effective_profiles(["laplace"]) == ["laplace"]
    assert manager.evaluate(time[0]) == promoted  # Even manual events cannot rewrite today's report.
    with sqlite3.connect(manager.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM team_events")
    with sqlite3.connect(manager.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE team_evaluations SET payload_json='{}'")
    with pytest.raises(ValueError):
        manager.set_status("unreviewed-repository", "ACTIVE", "No executable catalogue injection.")
    with pytest.raises(ValueError):
        manager.set_status("gauss", "ACTIVE", "", as_of=NOW)


def test_future_historical_execution_and_invalid_records_fail_closed(tmp_path):
    manager = TeamManager(tmp_path, clock=lambda: NOW)
    for timestamp in (NOW + timedelta(seconds=1), NOW - timedelta(days=1)):
        with pytest.raises(ValueError):
            manager.evaluate(timestamp)
    assert not list(tmp_path.iterdir())
    bad = measured([record(i) for i in range(260)])
    bad["coverage"]["invalid_records"] = 1
    member = TeamManager._initial("laplace", NOW.isoformat())
    result, _ = build_cohorts(bad, [member], NOW + timedelta(days=400))
    assert "AMBIGUOUS_PROVENANCE" in result["laplace"]["qualification"]["blockers"]
    assert result["laplace"]["metrics"]["ci_low_pct"] is None


def test_optional_export_preserves_learning_output_and_published_wager_independence(tmp_path):
    ledger = populated(tmp_path)
    plain = measure(ledger.path, NOW + timedelta(hours=8))
    exported = measure(ledger.path, NOW + timedelta(hours=8), include_records=True)
    records = exported.pop("records")
    assert exported == plain
    assert len(records["published"]) == 3 and len(records["actual"]) == 1
    assert len(records["research"]) == 2 and len(records["frozen"]) == 3
    assert all(r["original_run_match"] for r in records["published"])
    assert "records" not in plain


def test_real_ledger_original_provenance_and_results_export(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    item = ticket(profile="gauss")
    original = manifest("gauss")
    item.update(strategy_version="v1", strategy_manifest_sha256=original["manifest_sha256"],
                publication_scope="LIVE")
    for index, leg in enumerate(item["legs"], 1):
        leg.update(bookmaker="b_api_football_8", quote_provider="api_football",
                   provider_market_id=5,
                   quote_source_id=f"https://v3.football.api-sports.io/odds?fixture={index}&bookmaker=8&bet=5")
    saved = report(item)
    saved.update(strategy_manifest=original, recommendations=[deepcopy(item)])
    ledger.save_run(saved)
    ledger.now = lambda: NOW + timedelta(hours=6)
    ledger.refresh_results(Football({i: fixture(i) for i in (1, 2, 3)}))
    data = measure(ledger.path, NOW + timedelta(days=4), include_records=True)
    member = TeamManager._initial("gauss", (NOW - timedelta(days=1)).isoformat())
    groups, evidence = build_cohorts(data, [member], NOW + timedelta(days=4))
    assert groups["gauss"]["metrics"]["tickets"] == 1
    assert groups["gauss"]["metrics"]["roi_pct"] == 1100
    assert groups["gauss"]["metrics"]["version"] == original["manifest_sha256"]
    assert groups["gauss"]["qualification"]["blockers"] == [
        "INSUFFICIENT_TICKETS", "INSUFFICIENT_DAYS", "INSUFFICIENT_FIXTURES"]
    assert any(r["table"] == "portfolio_runs" for r in evidence[0]["source_refs"])
    assert any(r["table"] == "portfolio_results" for r in evidence[0]["source_refs"])


def test_current_runtime_code_and_settings_change_cannot_qualify_old_history():
    member = TeamManager._initial("laplace", NOW.isoformat())
    old = measured([record(i) for i in range(260)])
    for changed in (manifest("laplace", "new-code"), {}):
        groups, _ = build_cohorts(old, [member], NOW + timedelta(days=400), expected_manifest=changed)
        result = assess(groups, [member], NOW + timedelta(days=400))[0]
        assert "CURRENT_VERSION_MISMATCH" in result["qualification"]["blockers"]
        assert result["metrics"]["tickets"] == 0 and result["decision"] == "KEEP"
        assert result["awards"] == [] and result["podium"] is None
    old["records"]["published"][-1]["original_run_match"] = False
    groups, _ = build_cohorts(old, [member], NOW + timedelta(days=400), expected_manifest=manifest())
    assert "CURRENT_VERSION_MISMATCH" in groups["laplace"]["qualification"]["blockers"]


def test_learning_plans_verify_metrics_proposals_and_profile_evidence(tmp_path, monkeypatch):
    cutoff = NOW + timedelta(days=400)
    item = {"scope": "research", "kind": "PROFILE", "label": "gauss",
            "metrics": {"tickets": {"decided": 10, "lost": 8, "won": 2}},
            "source_refs": [{"table": "portfolio_recommendations", "key": "1", "hash": digest(1)}]}
    item["evidence_id"] = "ev-" + digest(item)[:24]
    _, proposals = findings_and_proposals([item])
    report_data = {"report_id": "verified-learning", "as_of": cutoff.isoformat(),
                   "recommendations": proposals, "evidence": [item]}
    report_data["report_hash"] = digest(report_data)
    data = dict(measured([]), evidence=[item])
    manager = TeamManager(tmp_path, clock=lambda: cutoff)
    reference, plans, evidence = manager._training(report_data, data, cutoff)
    assert reference["report_id"] == "verified-learning"
    assert set(plans) == {"gauss"}
    assert plans["gauss"][0]["code"] == "REVIEW_LOSS_PATTERN"
    assert plans["gauss"][0]["evidence_ids"] == [item["evidence_id"]]
    assert evidence == [item] and plans["gauss"][0]["execution"].endswith("NO_MODEL_WEIGHT_CHANGE")
    # A checksum alone cannot bless fabricated proposal wording or invented metrics.
    fake = deepcopy(report_data)
    fake.pop("report_hash")
    fake["recommendations"][0]["detail"] = "Guaranteed profit, automatically increase stakes."
    fake["report_hash"] = digest(fake)
    assert manager._training(fake, data, cutoff)[1:] == ({}, [])
    fake = deepcopy(report_data)
    fake.pop("report_hash")
    fake["evidence"][0]["metrics"]["tickets"]["lost"] = 100000
    fake["report_hash"] = digest(fake)
    assert manager._training(fake, data, cutoff)[1:] == ({}, [])
    report_data["report_hash"] = "wrong"
    assert manager._training(report_data, data, cutoff) == (None, {}, [])


def test_awards_require_current_qualification_and_are_not_raw_hit_rate():
    decision, _ = cohort([record(i) for i in range(260)])
    assert decision["podium"]["earned"] is True
    small, _ = cohort([record(i) for i in range(259)])
    assert small["awards"] == [] and small["podium"] is None
    stopped, _ = cohort([record(i) for i in range(260)], status="SUSPENDED")
    assert stopped["awards"] == [] and stopped["podium"] is None


def test_api_football_provenance_matches_canonical_fixture_and_exact_market():
    from goal_analysis.team.metrics import _unit_return

    item = record(0)
    assert _unit_return(item)[1] is None
    for patch in (
        {"api_football_fixture_id": 999999,
         "quote_source_id": "https://v3.football.api-sports.io/odds?fixture=999999&bookmaker=8&bet=1"},
        {"provider_market_id": 8,
         "quote_source_id": "https://v3.football.api-sports.io/odds?fixture=1&bookmaker=8&bet=8"},
        {"provider_market_id": None},
    ):
        broken = deepcopy(item)
        broken["legs"][0]["quote_provenance"].update(patch)
        assert _unit_return(broken) == (None, "AMBIGUOUS_PROVENANCE")
    broken = deepcopy(item)
    broken["legs"][0]["market"] = "FIRST_HALF:h2h"
    assert _unit_return(broken)[1] == "AMBIGUOUS_PROVENANCE"


def test_odds_api_original_crosswalk_candidate_hash_is_required():
    from goal_analysis.agents import canonical_sha256
    from goal_analysis.team.metrics import _unit_return

    item = record(0)
    for leg in item["legs"]:
        event_id = "provider-event-" + leg["fixture_id"]
        quote = leg["quote_provenance"]
        quote.update(quote_provider="the_odds_api", bookmaker="knownbook",
                     quote_source_id=f"https://api.the-odds-api.com/v4/sports/soccer_epl/events/{event_id}/odds")
        quote["candidate_id"] = "d223-" + canonical_sha256({
            "fixture_id": leg["fixture_id"], "event_id": event_id, "market": "h2h",
            "selection": "home", "period": "FULL_TIME", "bookmaker": "knownbook"})[:24]
    assert _unit_return(item)[1] is None
    numeric = deepcopy(item)
    for leg in numeric["legs"]:
        quote = leg["quote_provenance"]
        quote["quote_candidate_id"] = quote["candidate_id"]
        quote["candidate_id"] = "gauss-new-selection-id"
    assert _unit_return(numeric)[1] is None
    item["legs"][0]["selection"] = "away"
    assert _unit_return(item)[1] == "AMBIGUOUS_PROVENANCE"
