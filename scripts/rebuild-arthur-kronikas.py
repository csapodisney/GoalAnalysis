"""Correct only Kronikas from saved inputs; dry-run unless --publish is explicit.

No provider, model, Astra or Nemotron request is made by this script. An optional
externally obtained Astra review must be bound to the exact saved candidates.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("arthur_club_rebuild", ROOT / "scripts/rebuild-arthur-club.py")
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)

from goal_analysis.agents.astra_review import _ReviewError, _validate_reviews
from goal_analysis.engine.diversification import (
    _against,
    _eligible,
    _option_order,
    abstain,
    ready_exposure,
)
from goal_analysis.engine.portfolio import build_portfolio
from goal_analysis.engine.recommendations import build_recommendations
from goal_analysis.jobs.portfolio import _base_diversity_recommendations, _merge_astra_reviews

PROFILE = "kronikas"


def preserved(report):
    return {group: [deepcopy(item) for item in report.get(group, []) if item["profile_id"] != PROFILE]
            for group in COMMON.PRESERVED_GROUPS}


def supplementary_review(envelope, source, artifacts, now):
    """Validate provenance and evidence, without inferring a fresh review."""
    if envelope is None:
        return None
    if envelope.get("source_run_id") != source["run_id"]:
        raise ValueError("Supplementary review belongs to another source run")
    review = deepcopy(envelope.get("review", {}))
    if review.get("status") != "COMPLETE" or not review.get("reviews"):
        raise ValueError("Supplementary review must be COMPLETE and nonempty")
    stamp = datetime.fromisoformat(review.get("reviewed_at", ""))
    observed = datetime.fromisoformat(artifacts["candidate_analysis"]["observed_at"])
    if stamp.tzinfo is None or stamp.utcoffset() is None or not observed <= stamp <= now:
        raise ValueError("Invalid supplementary review timestamp")
    candidates = {item["candidate_id"]: item for item in artifacts["candidate_analysis"]["candidates"]}
    selected = []
    for item in review["reviews"]:
        candidate = candidates.get(item.get("candidate_id"))
        if candidate is None or (candidate.get("market_key"), candidate.get("selection_key"),
                                 candidate.get("period")) != ("totals_2_5", "over", "FULL_TIME"):
            raise ValueError("Supplementary review must concern saved Kronikas candidates only")
        if envelope.get("candidate_sha256", {}).get(candidate["candidate_id"]) != COMMON._hash(candidate):
            raise ValueError("Supplementary candidate snapshot hash mismatch")
        # Some adapter batches include nullable optional fields for other profiles.
        # They cannot grant new exceptions in this narrowly scoped correction.
        for optional in ("contrarian_rationale", "veto_override"):
            if optional in item:
                if item[optional] is not None:
                    raise ValueError("Supplementary review cannot introduce an override")
                item.pop(optional)
        selected.append(candidate)
    try:
        _validate_reviews({"reviews": review["reviews"]}, selected, review.get("sources", []))
    except _ReviewError as error:
        raise ValueError(str(error)) from error
    return review


def select_kronikas(options, anchors, now, day, main_anchors=None):
    """Choose an eligible nonduplicate alternative without changing other cards."""
    choices = []
    for option in options:
        if not _eligible(option, now, day):
            continue
        adjusted = _against(option, anchors)
        if adjusted is None:
            continue
        try:
            COMMON.package_exposure([*anchors, adjusted["recommendation"]])
        except ValueError:
            continue
        choices.append(adjusted)
    # A candidate-specific review can change caution penalties and ranking.
    # Prefer a completely reviewed, still valid combination over a new draft;
    # never claim review coverage for alternatives outside that actual batch.
    reviewed = [item for item in choices if item["ticket"].get("status") == "READY"
                and item["recommendation"].get("status") == "REVIEWED"]
    ranked = reviewed or choices
    safe_main = [item for item in ranked if main_anchors is None
                 or ready_exposure([*main_anchors, item["ticket"]])["compliant"]]
    return min(safe_main or ranked, key=_option_order) if choices else None


def _replace(group, replacements):
    """Keep order and exact values of every unrelated object."""
    output, inserted = [], False
    for item in group:
        if item["profile_id"] != PROFILE:
            output.append(item)
        elif not inserted:
            output.extend(deepcopy(replacements))
            inserted = True
    if not inserted:
        output.extend(deepcopy(replacements))
    return output


def rebuild(source, artifacts, now, run_id, review_envelope=None):
    COMMON.validate_boundary(source, now)
    original = preserved(source)
    report = deepcopy(source)
    settings = {**deepcopy(source["settings"]), "enabled_profiles": [PROFILE]}
    enriched = deepcopy(artifacts["candidate_analysis"])
    original_observed = enriched["observed_at"]
    # build_portfolio's observed_at is its evaluation clock. This scratch copy
    # evaluates kickoff/evidence expiry now; quote/evidence observation times
    # and the stored source artifact remain exactly as originally acquired.
    enriched.update(date=source["date"], observed_at=now.isoformat(),
                    source_observed_at=original_observed)
    supplement = supplementary_review(review_envelope, source, artifacts, now)
    review = deepcopy(source.get("astra", {}))
    if supplement is not None:
        review = _merge_astra_reviews(review, supplement)
    options = {}
    built = build_portfolio(enriched, settings, astra_review=review,
                            allowed_profiles=[PROFILE], diversity_options=options)
    cards = build_recommendations(enriched, [], settings, now, built, review)
    _base_diversity_recommendations(options, enriched, settings, now, review, {})
    if supplement:
        # Review penalties can move the reviewed route outside the bounded
        # alternatives beam. Re-evaluate just that actual batch through the
        # same construction gates, rather than discarding its costly coverage.
        reviewed_batch = {item["candidate_id"] for item in supplement["reviews"]}
        subset = {**enriched, "candidates": [item for item in enriched["candidates"]
                                             if item["candidate_id"] in reviewed_batch]}
        reviewed_options = {}
        build_portfolio(subset, settings, astra_review=review,
                        allowed_profiles=[PROFILE], diversity_options=reviewed_options)
        _base_diversity_recommendations(reviewed_options, subset, settings, now, review, {})
        options.setdefault(PROFILE, []).extend(reviewed_options.get(PROFILE, []))
    anchors = [card for card in original["recommendations"] if card.get("legs")]
    had_main_slot = any(item["profile_id"] == PROFILE for item in source.get("tickets", []))
    selected = select_kronikas(options.get(PROFILE, []), anchors, now, source["date"],
                              original["tickets"] if had_main_slot else None)
    scoped = {group: [] for group in COMMON.PRESERVED_GROUPS}
    main_slot_reason = None
    if selected is not None:
        ticket, card = deepcopy(selected["ticket"]), deepcopy(selected["recommendation"])
        diagnostics = {"policy_version": "kronikas-only-fixed-anchors-v1",
                       "unpenalized_score": selected["quality"],
                       "adjusted_score": selected["adjusted_quality"], **selected["similarity"]}
        ticket["diversification"] = deepcopy(diagnostics)
        card["diversification"] = deepcopy(diagnostics)
        scoped["profile_proposals"] = [deepcopy(ticket)]
        # Replace the existing slot; never add a new main ticket silently.
        for group in ("tickets", "preview_tickets"):
            if any(item["profile_id"] == PROFILE for item in source.get(group, [])):
                if group == "tickets" and not ready_exposure([*original["tickets"], ticket])["compliant"]:
                    main_slot_reason = "READY_MAIN_PACKAGE_EXPOSURE_LIMIT"
                else:
                    scoped[group] = [deepcopy(ticket)]
        card.update(portfolio_ticket_id=None, selected_for_portfolio=False, playable=False)
        if scoped["tickets"]:
            card.update(portfolio_ticket_id=ticket["ticket_id"], selected_for_portfolio=True)
        scoped["recommendations"] = [card]
    else:
        fallback = next((item for item in cards if item["profile_id"] == PROFILE),
                        next(item for item in source["recommendations"] if item["profile_id"] == PROFILE))
        scoped["recommendations"] = [abstain(fallback, "NO_ELIGIBLE_NON_DUPLICATE_TICKET")]
    for profile in built.get("profiles", []):
        if profile["profile_id"] == PROFILE:
            item = deepcopy(profile)
            item.update(published=bool(scoped["tickets"]),
                        construction_status="COMPLETE" if selected else "CONSTRUCTION_INCOMPLETE",
                        reason="" if selected else "Nincs megfelelő, nem duplikált Góllánc a rögzített feltételekkel.")
            if main_slot_reason:
                item["reason"] = "Önálló, ellenőrzött ajánlat; a napi főcsomag kitettségi korlátja miatt nem kap főszelvényhelyet."
            scoped["profiles"].append(item)
    if source.get("team"):
        COMMON.apply_publication_policy(scoped, source["team"])
    for group in COMMON.PRESERVED_GROUPS:
        scoped[group] = [item for item in scoped[group] if item["profile_id"] == PROFILE]
    manifest = COMMON.attach_strategy_provenance(scoped, settings)
    for group in COMMON.PRESERVED_GROUPS:
        report[group] = _replace(report.get(group, []), scoped[group])
    selected_card = scoped["recommendations"][0]
    reviewed_ids = {item["candidate_id"] for item in review.get("reviews", [])}
    full_review = bool(supplement and selected_card.get("legs")) and all(
        leg["candidate_id"] in reviewed_ids for leg in selected_card.get("legs", []))
    supplement_usage = deepcopy((supplement or {}).get("usage", {}))
    exposure = COMMON.package_exposure(report["recommendations"])
    report.update(run_id=run_id, observed_at=now.isoformat(), finished_at=now.isoformat(),
                  preview=source["date"] > now.astimezone(COMMON.BERLIN).date().isoformat())
    report["kronikas_rebuild"] = {
        "version": "saved-input-kronikas-rebuild-v1", "source_run_id": source["run_id"],
        "source_report_sha256": COMMON._hash(source), "source_observed_at": source["observed_at"],
        "source_candidate_observed_at": original_observed, "evaluation_at": now.isoformat(),
        "source_data_reused": True, "provider_requests": 0, "model_fits": 0,
        "astra_requests": supplement_usage.get("requests", 0), "in_process_astra_requests": 0,
        "nemotron_requests": 0,
        "astra_review_status": "SUPPLEMENTARY_KRONIKAS_REVIEW" if supplement else "INHERITED_SOURCE_REVIEW_NOT_REFRESHED",
        "astra_reviewed_at": review.get("reviewed_at"),
        "supplementary_review_sha256": COMMON._hash(review_envelope) if supplement else None,
        "supplementary_review_usage": supplement_usage,
        "selected_review_coverage_complete": full_review,
        "main_slot_preserved": bool(scoped["tickets"]), "main_slot_reason": main_slot_reason,
        "inherited_source_usage": deepcopy(source.get("usage", {})),
        "strategy_manifest": manifest, "exposure": exposure,
        "preserved_other_profiles_sha256": COMMON._hash(original),
        "record_identity_note": "New ledger IDs/times; all unrelated report objects and original rows retained.",
    }
    report["astra"] = review
    report["astra"].update(
        scope="KRONIKAS_SUPPLEMENT_WITH_INHERITED_SOURCE_REVIEW" if supplement else "INHERITED_SOURCE_REVIEW",
        fresh_for_kronikas_rebuild=full_review, source_run_id=source["run_id"],
        message=("A Krónikás választásai célzott kiegészítő értékelést kaptak; a többi profil korábbi értékelése változatlan."
                 if full_review else "A Krónikás részleges kiegészítő értékelést kapott; nem minden kiválasztott választás ellenőrzött. A többi profil korábbi értékelése változatlan."
                 if supplement else "A korábbi forrásolt Astra-értékelés átvéve; az új Góllánchoz nem készült új webes ellenőrzés."))
    report["usage"] = {"football_calls": 0, "football_odds_calls": 0, "odds_calls": 0,
                       "odds_credits_reserved": 0, "odds_provider_usage": [],
                       "llm_calls": supplement_usage.get("requests", 0),
                       "openai": {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                  "total_tokens": 0, "web_search_calls": 0, **supplement_usage},
                       "source_data_reused": True}
    diagnostics = report.get("diversification", {})
    if diagnostics:
        diagnostics["kronikas_rebuild_policy"] = "kronikas-only-fixed-anchors-v1"
        diagnostics["ready_exposure"] = ready_exposure(report["tickets"])
        for key in ("assigned", "abstained_profiles"):
            diagnostics.setdefault(key, {}).pop(PROFILE, None)
        card = scoped["recommendations"][0]
        if card.get("legs"):
            diagnostics["assigned"][PROFILE] = {"fixture_ids": sorted({leg["fixture_id"] for leg in card["legs"]}),
                "main_ticket": bool(scoped["tickets"]), "scope": card.get("publication_scope"),
                **card.get("diversification", {})}
        else:
            diagnostics["abstained_profiles"][PROFILE] = {"reason_code": card.get("reason_code")}
    report["recommendation_overlap"] = COMMON.recommendation_overlap(report["recommendations"])
    fixture_use = defaultdict(list)
    for ticket in report["tickets"]:
        for leg in ticket.get("legs", []):
            fixture_use[leg["fixture_id"]].append(ticket["ticket_id"])
    report["overlap"] = [{"fixture_id": fixture, "ticket_ids": ids, "ticket_count": len(ids)}
                         for fixture, ids in sorted(fixture_use.items()) if len(ids) > 1]
    report["construction_status"] = (
        "COMPLETE" if len(report["tickets"]) >= source["settings"].get("target_ticket_count", 2)
        else "PARTIAL" if report["tickets"] else "CONSTRUCTION_INCOMPLETE")
    report.setdefault("funnel", {}).update(
        recommendations=sum(bool(card.get("legs")) for card in report["recommendations"]),
        tickets=len(report["tickets"]), preview_tickets=len(report["preview_tickets"]))
    if preserved(report) != original:
        raise ValueError("Protected other-profile objects changed")
    COMMON.validate_boundary(report, now)
    report.pop("report_sha256", None)
    report["report_sha256"] = COMMON._hash(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--expected-snapshot-hash", required=True)
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--reason", default="Krónikás javítása: állandó, többmeccses Over 2,5 Góllánc.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.source_run):
        parser.error("Invalid source run ID")
    if not re.fullmatch(r"[a-f0-9]{64}", args.expected_snapshot_hash):
        parser.error("Invalid snapshot hash")
    envelope = json.loads(args.review_file.read_text(encoding="utf-8")) if args.review_file else None
    day = args.date.isoformat()
    with COMMON.RunLock(ROOT / "data/arthur/run.lock"):
        source, artifacts = COMMON.read_source(ROOT, day, args.source_run, args.expected_snapshot_hash)
        now = datetime.now(UTC)
        run_id = f"{day}-kronikas-{uuid4().hex[:12]}"
        report = rebuild(source, artifacts, now, run_id, envelope)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report.pop("report_sha256", None)
        report["report_sha256"] = COMMON._hash(report)
        directory = ROOT / ("reports/arthur" if args.publish else "analysis") / run_id
        card = next(item for item in report["recommendations"] if item["profile_id"] == PROFILE)
        audit = {"run_id": run_id, "published": False, "source_run_id": source["run_id"],
                 "output_directory": str(directory), "preserved_other_profiles": preserved(source) == preserved(report),
                 "kronikas": {"legs": len(card.get("legs", [])), "combined_price": card.get("combined_price"),
                              "status": card["status"], "candidate_ids": [leg["candidate_id"] for leg in card.get("legs", [])]},
                 "exposure": report["kronikas_rebuild"]["exposure"]}
        if args.publish:
            path = ROOT / "data/arthur/ledger.sqlite3"
            before = COMMON.immutable_rows(path)
            backup = ROOT / "data/arthur/backups" / f"{run_id}.sqlite3"
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(backup) as copy:
                original.backup(copy)
            rehearsal_path = ROOT / "analysis" / f"{run_id}-rehearsal.sqlite3"
            rehearsal_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(backup) as original, sqlite3.connect(rehearsal_path) as copy:
                original.backup(copy)
            rehearsal = COMMON.PortfolioLedger(rehearsal_path)
            rehearsal.save_run(report)
            rehearsal.revise_freeze(run_id, args.expected_snapshot_hash, args.reason)
            COMMON.read_source(ROOT, day, args.source_run, args.expected_snapshot_hash)
            COMMON.validate_boundary(report, datetime.now(UTC))
            saved_artifacts = {**artifacts, "report": report}
            if envelope:
                saved_artifacts["kronikas_supplementary_review"] = envelope
            COMMON.write_portfolio_bundle(directory, {"artifacts": saved_artifacts})
            ledger = COMMON.PortfolioLedger(path)
            ledger.save_run(report)
            COMMON.validate_boundary(report, datetime.now(UTC))
            audit["revision"] = ledger.revise_freeze(run_id, args.expected_snapshot_hash, args.reason)
            after = COMMON.immutable_rows(path)
            if any(after[table].get(key) != row for table, rows in before.items() for key, row in rows.items()):
                raise RuntimeError("Original immutable rows changed; inspect preserved backup")
            audit.update(published=True, backup=str(backup), original_immutable_rows_preserved=True,
                         actual_wagers_unchanged=before["portfolio_wagers"] == after["portfolio_wagers"])
        else:
            COMMON.write_portfolio_bundle(directory, {"artifacts": {"report": report}})
        (directory / "kronikas-rebuild-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(audit, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
