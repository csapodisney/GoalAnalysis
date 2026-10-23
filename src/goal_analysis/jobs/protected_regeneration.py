"""Pure recomposition of fresh non-DAILY options around an immutable published DAILY.

This module does not read providers, save a run, modify a freeze or place a wager.
The caller verifies the effective freeze, backs up/rehearses the ledger and performs
its compare-and-swap revision only after final package review and validation.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.diversification import (
    ERRORS,
    _refresh_diagnostics,
    allocate,
    ready_exposure,
)
from goal_analysis.engine.publication_odds import apply_publication_odds, publication_odds_issue
from goal_analysis.engine.publication_window import allows_new_ticket
from goal_analysis.engine.specialist_contracts import valid_specialist_construction
from goal_analysis.jobs.portfolio import _guard_final_publication, recommendation_overlap
from goal_analysis.team.routing import apply_publication_policy
from goal_analysis.tipsters.contracts import valid_construction

VERSION = "protected-daily-fresh-regeneration-v1"
GROUPS = ("tickets", "recommendations", "profile_proposals", "preview_tickets", "profiles")
BERLIN = ZoneInfo("Europe/Berlin")


def _stamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Protected regeneration requires timezone-aware timestamps")
    return stamp.astimezone(UTC)


def _daily(report):
    result = {}
    for group in GROUPS:
        rows = report.get(group, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {group} group")
        result[group] = [deepcopy(row) for row in rows if row.get("profile_id") == "daily223"]
    return result


def _restore(report, frozen):
    for group in GROUPS:
        report[group] = [*deepcopy(frozen[group]),
                         *[row for row in report.get(group, [])
                           if row.get("profile_id") != "daily223"]]


def _source_boundary(report, source_report, now, expected_source_run_id,
                     expected_source_report_sha256):
    now = _stamp(now)
    if source_report.get("run_id") != expected_source_run_id:
        raise ValueError("Protected source run identity changed")
    source_hash = canonical_sha256(source_report)
    if expected_source_report_sha256 is not None and source_hash != expected_source_report_sha256:
        raise ValueError("Protected source report hash mismatch")
    if not report.get("run_id") or report["run_id"] == source_report["run_id"]:
        raise ValueError("A new immutable run id is required")
    if report.get("date") != source_report.get("date"):
        raise ValueError("Fresh and protected reports must belong to the same day")
    if date.fromisoformat(report["date"]) < now.astimezone(BERLIN).date():
        raise ValueError("Past-day packages cannot be regenerated")
    observed = _stamp(report["observed_at"])
    if not _stamp(source_report["observed_at"]) < observed <= now:
        raise ValueError("Fresh report must be newer than the source and not in the future")
    frozen = _daily(source_report)
    protected_cards = [row for group in GROUPS for row in frozen[group] if row.get("legs")]
    if not protected_cards:
        raise ValueError("No protected DAILY card exists in the source")
    current_fixtures = {row.get("fixture_id"): row for row in report.get("fixtures", [])}
    for card in protected_cards:
        if not allows_new_ticket(card, now):
            raise ValueError("Protected DAILY reaches the 15-minute lockout; package unchanged")
        for leg in card["legs"]:
            if _stamp(leg["kickoff"]).astimezone(BERLIN).date().isoformat() != report["date"]:
                raise ValueError("Protected DAILY fixture belongs to another day")
            current = current_fixtures.get(leg.get("fixture_id"))
            if current is None:
                raise ValueError("Protected DAILY fixture missing from the fresh calendar")
            if _stamp(current["kickoff"]) != _stamp(leg["kickoff"]):
                raise ValueError("Protected DAILY fixture kickoff changed in the fresh calendar")
            status = current.get("provider_status", current.get("event_status"))
            if status not in {"NS", "SCHEDULED"}:
                raise ValueError("Protected DAILY fixture is not confirmed scheduled")
    plan = report.get("team", {})
    if (any(card.get("publication_scope", "LIVE") == "LIVE" for card in protected_cards)
            and plan.get("profile_states", {}).get("daily223", "ACTIVE") != "ACTIVE"):
        raise ValueError("Current team plan conflicts with protected DAILY publication")
    return frozen, source_hash


def validate_protected_daily(report, source_report, *, now, expected_source_run_id,
                             expected_source_report_sha256=None):
    """Repeat after Grok and immediately before persistence; reject any DAILY mutation.

    This is not a substitute for the ledger's transactional effective-freeze check.
    Empty/abstaining non-DAILY cards are allowed. Existing catalogue contracts and
    configured publication bounds remain the sources of minimum legs and odds.
    """
    now = _stamp(now)
    frozen, source_hash = _source_boundary(
        report, source_report, now, expected_source_run_id, expected_source_report_sha256,
    )
    if _daily(report) != frozen:
        raise ValueError("Protected DAILY objects changed")
    if report.get("real_wager_placed") is True:
        raise ValueError("Regeneration cannot place a wager")
    if not _stamp(report["observed_at"]) <= _stamp(
        report.get("finished_at", report["observed_at"])
    ) <= now:
        raise ValueError("Publication finish must be between collection start and now")
    for group in GROUPS:
        profiles = [row.get("profile_id") for row in report.get(group, [])]
        if any(count > 1 for count in Counter(profiles).values()):
            raise ValueError(f"Duplicate profile in {group}")
        for card in report.get(group, []):
            if not card.get("legs") or card.get("profile_id") == "daily223":
                continue
            if not allows_new_ticket(card, now):
                raise ValueError("Non-DAILY card reaches the 15-minute publication lockout")
            if any(_stamp(leg["kickoff"]).astimezone(BERLIN).date().isoformat()
                   != report["date"] for leg in card["legs"]):
                raise ValueError("Non-DAILY fixture belongs to another day")
            if not valid_construction(card, now=now) or not valid_specialist_construction(card):
                raise ValueError(f"Fixed construction contract violated: {card.get('profile_id')}")
            odds_issue = publication_odds_issue(card, report.get("settings", {}))
            if odds_issue:
                raise ValueError(f"Publication odds contract violated: {card.get('profile_id')}: "
                                 f"{odds_issue}")
    exposure = ready_exposure(report.get("tickets", []))
    if not exposure["compliant"]:
        raise ValueError("Final READY package exceeds the existing fixture exposure limit")
    return {"version": VERSION, "source_run_id": expected_source_run_id,
            "source_report_sha256": source_hash,
            "protected_objects_sha256": canonical_sha256(frozen),
            "protected_group_counts": {key: len(value) for key, value in frozen.items()},
            "daily223_exactly_preserved": True, "ready_exposure": exposure,
            "validated_at": now.isoformat(), "automatic_wager_changes": False}


def _restore_fresh_strategy_tags(report, fresh_report):
    """Reallocation templates predate the final provenance decoration; keep its tags."""
    fields = ("strategy_version", "strategy_manifest_sha256", "method_version")
    tags = {}
    for group in GROUPS:
        for row in fresh_report.get(group, []):
            if row.get("profile_id") != "daily223":
                tags.setdefault(row.get("profile_id"), {}).update(
                    {key: deepcopy(row[key]) for key in fields if key in row})
    for group in GROUPS:
        for row in report.get(group, []):
            if row.get("profile_id") != "daily223":
                row.update(deepcopy(tags.get(row.get("profile_id"), {})))


def compose_protected_daily(fresh_report, source_report, options, *, now,
                             expected_source_run_id, expected_source_report_sha256=None):
    """Choose fresh alternatives against the original DAILY, preserving all five groups.

    Capture options from the fresh pipeline without saving it. Call this function,
    review the resulting *whole* package once, repeat validate_protected_daily, then
    save and append a freeze revision using the separately verified freeze hash.
    """
    now = _stamp(now)
    frozen, _ = _source_boundary(
        fresh_report, source_report, now, expected_source_run_id, expected_source_report_sha256,
    )
    if any(fresh_report.get(key) in ERRORS | {"TEAM_PAUSED"}
           for key in ("construction_status", "status")):
        raise ValueError("Failed fresh collection cannot be published as a regenerated package")
    output = deepcopy(fresh_report)
    _restore(output, frozen)
    settings = output["settings"]
    plan = output.get("team")
    output, eligible_options = apply_publication_odds(output, options, settings)
    if _daily(output) != frozen:
        raise ValueError("Publication odds policy attempted to alter the protected DAILY")
    allocate(output, eligible_options, settings, now, plan=plan)
    # Allocation updates the profiles' published flags. Restore the whole DAILY record.
    _restore(output, frozen)
    if plan:
        scoped = {group: [deepcopy(row) for row in output.get(group, [])
                          if row.get("profile_id") != "daily223"] for group in GROUPS}
        apply_publication_policy(scoped, plan)
        for group in GROUPS:
            output[group] = scoped.get(group, [])
        _restore(output, frozen)
    _guard_final_publication(output, now)
    if _daily(output) != frozen:
        raise ValueError("Publication guard attempted to alter the protected DAILY")
    _restore_fresh_strategy_tags(output, fresh_report)
    _refresh_diagnostics(output)
    output["recommendation_overlap"] = recommendation_overlap(output.get("recommendations", []))
    use = {}
    for card in output.get("tickets", []):
        for leg in card.get("legs", []):
            use.setdefault(leg["fixture_id"], []).append(card["ticket_id"])
    output["overlap"] = [{"fixture_id": key, "ticket_ids": values, "ticket_count": len(values)}
                         for key, values in sorted(use.items()) if len(values) > 1]
    if isinstance(output.get("funnel"), dict):
        output["funnel"].update(
            tickets=len(output.get("tickets", [])),
            preview_tickets=len(output.get("preview_tickets", [])),
            recommendations=sum(bool(card.get("legs"))
                                for card in output.get("recommendations", [])),
        )
    if output.get("grok_review"):
        output["grok_review"] = {"status": "PENDING_RECOMPOSED_PACKAGE_REVIEW",
                                 "reason": "DAILY_RESTORED_AND_NONDAILY_REALLOCATED"}
    output["finished_at"] = now.isoformat()
    output["protected_regeneration"] = validate_protected_daily(
        output, source_report, now=now, expected_source_run_id=expected_source_run_id,
        expected_source_report_sha256=expected_source_report_sha256,
    )
    output["protected_regeneration"].update(
        fresh_run_id=fresh_report["run_id"],
        fresh_report_sha256=canonical_sha256(fresh_report),
        fresh_source_data_reused=True, additional_provider_requests=0,
        additional_model_fits=0, additional_llm_requests=0,
        preserved_daily_strategy_manifest_sha256=source_report.get("strategy_manifest", {}).get(
            "manifest_sha256"),
    )
    output.pop("report_sha256", None)
    output["report_sha256"] = canonical_sha256(output)
    return output
