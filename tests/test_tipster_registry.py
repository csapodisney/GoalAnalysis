"""Thirty-member migration and policy isolation; no real model, provider or data writes."""

import json
import sqlite3
from copy import deepcopy
from datetime import timedelta

import pytest
from test_portfolio_ledger import NOW
from test_team_manager import manifest, measured, record

from goal_analysis.config.portfolio import (
    DEFAULT_PROBATION_PROFILE_IDS,
    DEFAULT_SETTINGS,
    LEGACY_PROFILE_IDS,
    MODEL_PROFILE_IDS,
    PROFILE_IDS,
    load_settings,
    validate_settings,
)
from goal_analysis.learning._metrics import digest
from goal_analysis.team.manager import TeamManager, _json
from goal_analysis.team.metrics import POLICY, PROFILES, assess, build_cohorts
from goal_analysis.team.routing import apply_publication_policy, generation_plan, validate_plan
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS

OLD_PROFILES = [*LEGACY_PROFILE_IDS, *MODEL_PROFILE_IDS]


@pytest.mark.parametrize("saved,expected_old", [
    ({"profile_registry_version": 3, "enabled_profiles": ["daily223", "merlin"]}, ["daily223", "merlin"]),
    ({"profile_registry_version": 3, "enabled_profiles": ["daily223", "gauss"]}, ["daily223", "gauss"]),
    ({"profile_registry_version": 2, "enabled_profiles": ["daily223", "gauss"]}, ["daily223", "gauss", "laplace"]),
    ({"enabled_profiles": ["daily223", "merlin"]}, ["daily223", "merlin", *MODEL_PROFILE_IDS]),
])
def test_effective_upgrade_adds_only_new_twenty_and_never_rewrites_file(tmp_path, saved, expected_old):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    before = path.read_bytes()
    result = load_settings(path)
    assert result["profile_registry_version"] == 4
    assert result["enabled_profiles"] == [*expected_old, *TIPSTER_PROFILE_IDS]
    assert len(TIPSTER_PROFILE_IDS) == 20 and len(set(PROFILE_IDS)) == 30
    assert load_settings(path) == result and path.read_bytes() == before
    assert result["stake_eur"] == 1 and result["max_ticket_count"] == 5


def test_saved_v4_disabled_new_profiles_stay_disabled(tmp_path):
    path = tmp_path / "settings.json"
    saved = {"profile_registry_version": 4, "enabled_profiles": ["daily223", TIPSTER_PROFILE_IDS[4]]}
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert load_settings(path)["enabled_profiles"] == saved["enabled_profiles"]
    assert load_settings(path)["enabled_profiles"] == saved["enabled_profiles"]
    assert DEFAULT_SETTINGS["profile_registry_version"] == 4
    assert MODEL_PROFILE_IDS == ("gauss", "kepler", "laplace")


@pytest.mark.parametrize("profiles", [
    ["daily223", "daily223"], ["daily223", "unknown-new-code"], ["daily223", []], "daily223",
])
def test_migration_does_not_hide_invalid_old_settings(tmp_path, profiles):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"profile_registry_version": 3, "enabled_profiles": profiles}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)


def test_old_frozen_job_settings_and_rosters_remain_exactly_compatible():
    settings = {**deepcopy(DEFAULT_SETTINGS), "profile_registry_version": 3,
                "enabled_profiles": list(OLD_PROFILES)}
    before = deepcopy(settings)
    assert validate_settings(settings) == before and settings == before
    old = generation_plan(OLD_PROFILES, {"policy_version": "grail-prospective-unit-v1", "members": [
        {"profile_id": "gauss", "status": "SUSPENDED"}]})
    snapshot = deepcopy(old)
    assert validate_plan(old, OLD_PROFILES) == snapshot and old == snapshot
    assert old["paper_profiles"] == ["laplace"] and old["blocked_profiles"] == ["gauss"]
    assert not set(TIPSTER_PROFILE_IDS) & set(old["profile_states"])
    with pytest.raises(ValueError):
        validate_plan(old, PROFILE_IDS)


def test_readonly_roster_has_twenty_new_probation_members_without_backdated_epochs(tmp_path):
    manager = TeamManager(tmp_path, clock=lambda: NOW)
    snapshot = manager.snapshot()
    assert len(snapshot["members"]) == 30 and not list(tmp_path.iterdir())
    newcomers = [m for m in snapshot["members"] if m["profile_id"] in TIPSTER_PROFILE_IDS]
    assert len(newcomers) == 20
    assert all(m["status"] == "PROBATION" and m["started_at"] is None
               and m["evidence_since"] is None and not m["eligible_for_live"] for m in newcomers)
    plan = generation_plan(PROFILE_IDS, snapshot)
    assert len(plan["live_profiles"]) == 9
    assert plan["paper_profiles"] == list(DEFAULT_PROBATION_PROFILE_IDS)
    assert POLICY["family_size"] == len(PROFILES) == 30
    assert POLICY["version"] == "grail-prospective-unit-v2-roster30"
    assert (POLICY["min_tickets"], POLICY["min_days"], POLICY["min_unique_fixtures"]) == (260, 90, 300)


def _legacy_store(manager):
    old_members = [manager._initial(p, NOW.isoformat()) for p in OLD_PROFILES]
    cohorts, evidence = build_cohorts(measured([]), old_members, NOW)
    legacy = {"evaluation_id": "legacy-ten-only", "date": NOW.date().isoformat(),
              "as_of": NOW.isoformat(), "created_at": NOW.isoformat(), "status": "NO_DATA",
              "policy_version": "grail-prospective-unit-v1",
              "policy": {**deepcopy(POLICY), "version": "grail-prospective-unit-v1", "family_size": 10},
              "members": assess(cohorts, old_members, NOW), "events": [], "evidence": evidence,
              "automatic_wager_changes": False}
    legacy["snapshot_hash"] = digest(legacy)
    with manager._connection(write=True) as db:
        for member in old_members:
            db.execute("INSERT INTO team_enrollment VALUES (?,?,?)", (member["profile_id"], _json(member), digest(member)))
        db.execute("INSERT INTO team_evaluations VALUES (?,?,?,?)",
                   (legacy["date"], legacy["evaluation_id"], _json(legacy), digest(legacy)))
    return legacy


def test_old_daily_snapshot_not_rewritten_new_epochs_start_only_next_review(tmp_path):
    clock = [NOW]
    manager = TeamManager(tmp_path, clock=lambda: clock[0])
    legacy = _legacy_store(manager)
    original = manager.path.read_bytes()
    assert manager.snapshot()["latest_evaluation"] == legacy
    assert manager.path.read_bytes() == original
    assert manager.evaluate(NOW) == legacy  # Immutable first snapshot/day remains old 10-member one.
    with sqlite3.connect(manager.path) as db:
        assert db.execute("SELECT COUNT(*) FROM team_enrollment").fetchone()[0] == 10
    clock[0] += timedelta(days=1)
    updated = manager.evaluate(clock[0])
    assert len(updated["members"]) == 30
    assert all(m["started_at"] == (clock[0] if m["profile_id"] in TIPSTER_PROFILE_IDS else NOW).isoformat()
               for m in updated["members"])
    assert all(m["status"] == "PROBATION" for m in updated["members"] if m["profile_id"] in TIPSTER_PROFILE_IDS)
    with sqlite3.connect(manager.path) as db:
        row = db.execute("SELECT payload_json,payload_hash FROM team_evaluations WHERE evaluation_id=?",
                         (legacy["evaluation_id"],)).fetchone()
        assert json.loads(row[0]) == legacy and row[1] == digest(legacy)
        assert db.execute("SELECT COUNT(*) FROM team_enrollment").fetchone()[0] == 30


def test_owner_action_enrolls_only_selected_newcomer_and_routes_live_then_suspended(tmp_path):
    clock = [NOW]
    manager = TeamManager(tmp_path, clock=lambda: clock[0])
    newcomer = TIPSTER_PROFILE_IDS[0]
    active = manager.set_status(newcomer, "ACTIVE", "Kifejezett tulajdonosi engedély, nem minősítés.")
    assert manager.effective_profiles([newcomer]) == [newcomer]
    member = next(m for m in active["members"] if m["profile_id"] == newcomer)
    assert member["started_at"] == NOW.isoformat() and member["awards"] == []
    with sqlite3.connect(manager.path) as db:
        assert db.execute("SELECT profile_id FROM team_enrollment").fetchall() == [(newcomer,)]
    assert all(m["started_at"] is None for m in active["members"] if m["profile_id"] != newcomer)
    clock[0] += timedelta(hours=1)
    suspended = manager.set_status(newcomer, "SUSPENDED", "Próba lezárva.")
    assert generation_plan(["daily223", newcomer], suspended)["blocked_profiles"] == [newcomer]
    assert not manager.effective_profiles([newcomer])
    assert suspended["events"][-1]["source"] == "MANUAL"
    assert suspended["automatic_wager_changes"] is False


def test_new_member_cannot_inherit_history_or_other_personality_evidence():
    profile = TIPSTER_PROFILE_IDS[0]
    records = [record(i, profile=profile) for i in range(260)]
    other = [record(i, profile=TIPSTER_PROFILE_IDS[1]) for i in range(260)]
    cutoff = NOW + timedelta(days=400)
    entrant = TeamManager._initial(profile, cutoff.isoformat())
    groups, _ = build_cohorts(measured(records + other), [entrant], cutoff,
                              expected_manifest=manifest(profile))
    assert groups[profile]["metrics"]["tickets"] == 0
    assert assess(groups, [entrant], cutoff)[0]["status"] == "PROBATION"
    earlier = TeamManager._initial(profile, NOW.isoformat())
    groups, _ = build_cohorts(measured(other), [earlier], cutoff, expected_manifest=manifest(profile))
    assert groups[profile]["metrics"]["tickets"] == 0
    changed, _ = build_cohorts(measured(records), [earlier], cutoff, expected_manifest=manifest(profile, "changed"))
    assert "CURRENT_VERSION_MISMATCH" in changed[profile]["qualification"]["blockers"]


def test_paper_and_suspended_personas_never_become_live_tickets_or_erase_legacy_fallback():
    profile, suspended = TIPSTER_PROFILE_IDS[:2]
    plan = generation_plan(PROFILE_IDS, {"members": [{"profile_id": suspended, "status": "SUSPENDED"}]})
    item = {"profile_id": profile, "legs": [{"fixture_id": "one"}], "combined_price": 12,
            "stake_eur": 5, "playable": True, "portfolio_ticket_id": "one", "selected_for_portfolio": True}
    snapshot = {"tickets": [deepcopy(item), {"profile_id": "available"}],
                "recommendations": [deepcopy(item), {**deepcopy(item), "profile_id": suspended}]}
    apply_publication_policy(snapshot, plan)
    assert snapshot["tickets"] == [{"profile_id": "available", "publication_scope": "LIVE",
                                     "team_status": "ACTIVE", "team_plan_sha256": plan["plan_sha256"]}]
    paper = next(r for r in snapshot["recommendations"] if r["profile_id"] == profile)
    assert paper["publication_scope"] == "PAPER_AUDITION" and paper["legs"]
    assert not paper["playable"] and not paper["selected_for_portfolio"]
    assert paper["stake_eur"] is None and paper["portfolio_ticket_id"] is None
    stopped = next(r for r in snapshot["recommendations"] if r["profile_id"] == suspended)
    assert stopped["status"] == "SUSPENDED" and stopped["legs"] == []
    assert plan["automatic_wager_changes"] is False


def test_explicit_enrollment_precedes_first_picks_without_replaying_daily_evaluation(tmp_path):
    clock = [NOW]
    manager = TeamManager(tmp_path, clock=lambda: clock[0])
    legacy = _legacy_store(manager)
    clock[0] += timedelta(hours=1)
    enrollment = manager.enroll_missing()
    assert enrollment["enrolled_count"] == 20
    assert enrollment["enrolled_profiles"] == list(TIPSTER_PROFILE_IDS)
    assert enrollment["snapshot"]["latest_evaluation"] == legacy
    assert manager.evaluate(clock[0]) == legacy
    newcomers = [m for m in enrollment["snapshot"]["members"] if m["profile_id"] in TIPSTER_PROFILE_IDS]
    assert all(m["started_at"] == clock[0].isoformat() and m["evidence_since"] == clock[0].isoformat()
               for m in newcomers)
    assert manager.enroll_missing()["enrolled_count"] == 0
    first = record(0, profile=TIPSTER_PROFILE_IDS[0], start=clock[0] - timedelta(days=1) + timedelta(minutes=1))
    first["publication_scope"] = "PAPER_AUDITION"
    cutoff = clock[0] + timedelta(days=4)
    groups, _ = build_cohorts(measured([first]), newcomers, cutoff,
                              expected_manifest=manifest(TIPSTER_PROFILE_IDS[0]))
    assert groups[TIPSTER_PROFILE_IDS[0]]["metrics"]["tickets"] == 1
    with sqlite3.connect(manager.path) as db:
        assert db.execute("SELECT COUNT(*) FROM team_evaluations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM team_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM team_enrollment").fetchone()[0] == 30


def test_enrollment_is_idempotent_and_corrupt_old_state_blocks_new_entries(tmp_path):
    manager = TeamManager(tmp_path, clock=lambda: NOW)
    created = manager.enroll_missing()
    assert created["enrolled_count"] == 30
    assert created["snapshot"]["latest_evaluation"] is None
    assert manager.enroll_missing()["enrolled_count"] == 0
    other = TeamManager(tmp_path / "legacy", clock=lambda: NOW)
    _legacy_store(other)
    # Directly inject corruption in an isolated synthetic DB, not production data.
    with sqlite3.connect(other.path) as db:
        db.execute("DROP TRIGGER team_enrollment_no_update")
        db.execute("UPDATE team_enrollment SET payload_hash='broken' WHERE profile_id='gauss'")
    with pytest.raises(ValueError, match="ellenőrző"):
        other.enroll_missing()
    with sqlite3.connect(other.path) as db:
        assert db.execute("SELECT COUNT(*) FROM team_enrollment").fetchone()[0] == 10
