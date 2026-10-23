"""Freeze team decisions at run start and enforce them before publication."""

from copy import deepcopy

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import (
    DEFAULT_PROBATION_PROFILE_IDS,
    MODEL_PROFILE_IDS,
    PROFILE_IDS,
    TIPSTER_PROFILE_IDS,
)


def generation_plan(requested: list[str], snapshot: dict | None = None) -> dict:
    snapshot = snapshot or {}
    if snapshot.get("error") or snapshot.get("status") == "UNAVAILABLE":
        raise ValueError("Team supervision unavailable; publication blocked")
    members = {m["profile_id"]: m for m in snapshot.get("members", [])}
    states = {p: members.get(p, {}).get("status", "PROBATION" if p in DEFAULT_PROBATION_PROFILE_IDS else "ACTIVE")
              for p in requested}
    if any(p not in PROFILE_IDS for p in requested) or any(
        status not in {"ACTIVE", "PROBATION", "SUSPENDED"} for status in states.values()
    ):
        raise ValueError("Invalid team roster")
    plan = {
        "schema_version": 1, "name": "A Szent Grál", "mode": "LIVE_AUTONOMOUS",
        "evaluation_id": (snapshot.get("latest_evaluation") or {}).get("evaluation_id"),
        "policy_version": snapshot.get("policy_version") or snapshot.get("policy", {}).get("version"),
        "requested_profiles": list(requested),
        "profile_states": states,
        "live_profiles": [p for p in requested if states[p] == "ACTIVE"],
        "paper_profiles": [p for p in requested if states[p] == "PROBATION"],
        "blocked_profiles": [p for p in requested if states[p] == "SUSPENDED"],
        "automatic_wager_changes": False,
    }
    plan["plan_sha256"] = canonical_sha256(plan)
    return plan


def validate_plan(plan: dict, requested: list[str]) -> dict:
    if not isinstance(plan, dict) or plan.get("requested_profiles") != requested:
        raise ValueError("Team plan does not match requested profiles")
    value = deepcopy(plan)
    supplied_hash = value.pop("plan_sha256", None)
    if canonical_sha256(value) != supplied_hash:
        raise ValueError("Team plan hash mismatch")
    canonical = generation_plan(requested, {
        "members": [{"profile_id": p, "status": s} for p, s in plan.get("profile_states", {}).items()],
        "policy_version": plan.get("policy_version"),
        "latest_evaluation": {"evaluation_id": plan.get("evaluation_id")},
    })
    if canonical != plan:
        raise ValueError("Inconsistent team publication plan")
    return deepcopy(plan)


def apply_publication_policy(report: dict, plan: dict) -> None:
    """Paper output stays immutable/research-visible but cannot be a live ticket."""
    report["team"] = deepcopy(plan)
    live, paper = set(plan["live_profiles"]), set(plan["paper_profiles"])
    separate_profiles = {*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS}
    allow_fallback = not any(p not in live for p in plan["requested_profiles"]
                             if p not in separate_profiles) and bool(live)
    for group in ("tickets", "preview_tickets", "profile_proposals"):
        report[group] = [item for item in report.get(group, [])
                         if item["profile_id"] in live or (item["profile_id"] == "available" and allow_fallback)]
        for item in report[group]:
            item.update(publication_scope="LIVE", team_status="ACTIVE", team_plan_sha256=plan["plan_sha256"])
    for item in report.get("recommendations", []):
        profile = item["profile_id"]
        item["team_status"] = plan["profile_states"].get(profile, "ACTIVE")
        item["publication_scope"] = "PAPER_AUDITION" if profile in paper else "LIVE"
        item["team_plan_sha256"] = plan["plan_sha256"]
        if profile in paper:
            item.update(playable=False, selected_for_portfolio=False, portfolio_ticket_id=None, stake_eur=None)
            warning = "Próbaidős agent: kizárólag papírpróba, valódi tét nélkül."
            if warning not in item.setdefault("quality_warnings", []):
                item["quality_warnings"].append(warning)
        elif profile in live and item.get("method_kind") == "LOCAL_STATISTICAL_MODEL":
            item["quality_warnings"] = [warning for warning in item.get("quality_warnings", [])
                                        if "csak papírteszt" not in warning]
            warning = "Éles ajánlócsapat; kísérleti, nem kalibrált modellbecslés. Nincs automatikus fogadás."
            if warning not in item["quality_warnings"]:
                item["quality_warnings"].append(warning)
        elif profile not in live and profile != "available":
            item.update(publication_scope="SUSPENDED", legs=[], playable=False,
                        combined_price=None, stake_eur=None, status="SUSPENDED")
    present = {r["profile_id"] for r in report.get("recommendations", [])}
    for profile in plan["blocked_profiles"]:
        if profile not in present:
            report.setdefault("recommendations", []).append({
                "profile_id": profile, "status": "SUSPENDED", "publication_scope": "SUSPENDED",
                "team_status": "SUSPENDED", "legs": [], "playable": False, "stake_eur": None,
                "combined_price": None, "quality_warnings": ["A Szent Grál felfüggesztette ezt a módszert."],
                "team_plan_sha256": plan["plan_sha256"],
            })
    published = {item["profile_id"] for item in report["tickets"]}
    for item in report.get("profiles", []):
        item["published"] = item["profile_id"] in published
