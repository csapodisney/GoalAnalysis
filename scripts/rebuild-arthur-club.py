"""Rebuild only club cards from saved inputs; dry-run unless --publish is explicit.

No provider, Astra, Nemotron or model-fitting requests are made. Existing source
times remain intact. Published revisions preserve all original immutable rows.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.engine.diversification import (
    _against,
    _eligible,
    _option_order,
    _refresh_diagnostics,
    abstain,
    ticket_signature,
)
from goal_analysis.engine.publication_window import allows_new_ticket
from goal_analysis.engine.veto_overrides import verified_veto_override, veto_candidate_signature
from goal_analysis.jobs.portfolio import RunLock, recommendation_overlap, write_portfolio_bundle
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash
from goal_analysis.strategy_provenance import attach_strategy_provenance
from goal_analysis.team.routing import apply_publication_policy
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS
from goal_analysis.tipsters.recommendations import build_tipster_recommendations

BERLIN = ZoneInfo("Europe/Berlin")
CLUB = set(TIPSTER_PROFILE_IDS)
PRESERVED_GROUPS = ("tickets", "preview_tickets", "profile_proposals", "recommendations", "profiles")


def preserved(report):
    return {group: [deepcopy(item) for item in report.get(group, [])
                    if item["profile_id"] not in CLUB] for group in PRESERVED_GROUPS}


def validate_boundary(report, now):
    """Reject the whole correction rather than silently altering a protected card."""
    if date.fromisoformat(report["date"]) < now.astimezone(BERLIN).date():
        raise ValueError("Past-day packages cannot be rebuilt")
    for group in ("tickets", "preview_tickets", "recommendations"):
        for item in report.get(group, []):
            if item.get("legs") and not allows_new_ticket(item, now):
                raise ValueError(f"15-minute publication lockout: {item['profile_id']}")


def _inherit_proofs(enriched, source, now):
    """Reuse only complete saved, candidate-bound envelopes, never reconstruct consent."""
    candidates = {item["candidate_id"]: item for item in enriched["candidates"]}
    restored = 0
    for card in source.get("recommendations", []):
        if card["profile_id"] not in CLUB:
            continue
        for leg in card.get("legs", []):
            candidate = candidates.get(leg.get("source_candidate_id", leg.get("candidate_id")))
            source_leg = {**leg, "candidate_id": leg.get("source_candidate_id", leg.get("candidate_id"))}
            if candidate is None or veto_candidate_signature(candidate) != veto_candidate_signature(source_leg):
                continue
            if isinstance(leg.get("odds_movement"), dict):
                candidate["odds_movement"] = deepcopy(leg["odds_movement"])
            if isinstance(leg.get("contrarian_review"), dict):
                candidate["contrarian_review"] = deepcopy(leg["contrarian_review"])
            proof = deepcopy(candidate)
            for key in ("veto_override_context", "astra_veto_review"):
                if key in leg:
                    proof[key] = deepcopy(leg[key])
            if verified_veto_override(proof, now=now):
                candidate.update({key: proof[key] for key in ("veto_override_context", "astra_veto_review")})
                restored += 1
    return restored


def package_exposure(cards):
    unique = {}
    for card in cards:
        if card.get("legs"):
            unique.setdefault(ticket_signature(card), card)
    counts = Counter(leg["fixture_id"] for card in unique.values() for leg in card["legs"])
    limit = max(1, math.floor(len(unique) * 0.5))
    if any(count > limit for count in counts.values()):
        raise ValueError("Club rebuild exceeds 50% fixture exposure; no package was published")
    return {"package_count": len(unique), "allowed_per_fixture": limit,
            "maximum_fixture_count": max(counts.values(), default=0),
            "maximum_fixture_share": max(counts.values(), default=0) / len(unique) if unique else 0,
            "fixture_counts": dict(sorted(counts.items())), "compliant": True}


def select_clubs(cards, options, anchors, now, day):
    """Soft ticket similarity against fixed nonclub cards, with a final 50% cap."""
    anchors = [deepcopy(card) for card in anchors if card.get("legs")]
    # Portfolio cards and their aliases are one economic ticket.
    unique = {}
    for card in anchors:
        unique.setdefault(ticket_signature(card), card)
    anchors = list(unique.values())
    selected = {}
    available = {card["profile_id"] for card in cards if card.get("legs")}
    while available:
        choices = []
        for profile in sorted(available):
            for option in options.get(profile, []):
                if _eligible(option, now, day):
                    adjusted = _against(option, anchors)
                    if adjusted is not None:
                        choices.append((profile, adjusted))
        if not choices:
            break
        profile, option = min(choices, key=lambda pair: (*_option_order(pair[1]), pair[0]))
        card = deepcopy(option["recommendation"])
        card["diversification"] = {"policy_version": "club-only-fixed-anchors-v1",
            "unpenalized_score": option["quality"], "adjusted_score": option["adjusted_quality"],
            **option["similarity"]}
        selected[profile] = card
        anchors.append(card)
        available.remove(profile)
    result = [selected.get(card["profile_id"], abstain(card, "NO_ELIGIBLE_NON_DUPLICATE_TICKET")
                           if card.get("legs") else card) for card in cards]
    return result, package_exposure(anchors)


def rebuild(source, artifacts, now, run_id):
    validate_boundary(source, now)
    original = preserved(source)
    report = deepcopy(source)
    settings = deepcopy(source["settings"])
    settings.update(date=source["date"])
    enriched = deepcopy(artifacts["candidate_analysis"])
    enriched.update(date=source["date"], fixtures=deepcopy(artifacts["fixtures"]["records"]))
    inherited = _inherit_proofs(enriched, source, now)
    options = {}
    cards = build_tipster_recommendations(enriched, artifacts["numeric_models"], settings, now,
                                         diversity_options=options)
    cards, exposure = select_clubs(cards, options, original["recommendations"], now, source["date"])
    scoped = {"recommendations": cards, "tickets": [], "preview_tickets": [], "profiles": []}
    if source.get("team"):
        apply_publication_policy(scoped, source["team"])
    scoped["recommendations"] = [item for item in scoped["recommendations"] if item["profile_id"] in CLUB]
    scoped_settings = {**settings, "enabled_profiles": [item["profile_id"] for item in cards]}
    manifest = attach_strategy_provenance(scoped, scoped_settings)
    by_id = {item["profile_id"]: item for item in scoped["recommendations"]}
    report["recommendations"] = [by_id.get(item["profile_id"], item)
                                  for item in report["recommendations"]]
    # Publication routing may remove suspended cards; validate the true final denominator.
    exposure = package_exposure(report["recommendations"])
    for group in ("profiles",):
        for item in report.get(group, []):
            if item["profile_id"] in by_id:
                card = by_id[item["profile_id"]]
                item.update(construction_status=card["status"], published=bool(card.get("legs")))
    report.update(run_id=run_id, observed_at=now.isoformat(), finished_at=now.isoformat(),
                  preview=source["date"] > now.astimezone(BERLIN).date().isoformat())
    report["club_rebuild"] = {"version": "saved-input-club-rebuild-v1", "source_run_id": source["run_id"],
        "source_report_sha256": _hash(source), "source_observed_at": source["observed_at"],
        "source_finished_at": source.get("finished_at"), "source_data_reused": True,
        "provider_requests": 0, "model_fits": 0, "astra_requests": 0, "nemotron_requests": 0,
        "astra_review_status": "INHERITED_SOURCE_REVIEW_NOT_REFRESHED",
        "astra_reviewed_at": source.get("astra", {}).get("reviewed_at"),
        "inherited_astra_message": source.get("astra", {}).get("message"),
        "restored_candidate_bound_overrides": inherited,
        "inherited_source_usage": deepcopy(source.get("usage", {})),
        "strategy_manifest": manifest, "exposure": exposure,
        "preserved_nonclub_sha256": _hash(original),
        "record_identity_note": "New ledger recommendation IDs/times; original report cards and all prior rows retained."}
    report.setdefault("astra", {}).update(
        scope="INHERITED_SOURCE_REVIEW", fresh_for_club_rebuild=False, source_run_id=source["run_id"],
        message="A korábbi forrásolt Astra-értékelés átvéve; az új klubkombinációkhoz nem készült új webes ellenőrzés.")
    report["tipster_club"] = {**report.get("tipster_club", {}), "additional_llm_calls": 0,
        "nemotron_style": {"status": "DISABLED", "usage": {"requests": 0}, "selected": {}},
        "profile_count": len(cards), "published_count": sum(bool(card.get("legs")) for card in cards)}
    report["usage"] = {"football_calls": 0, "football_odds_calls": 0, "odds_calls": 0,
                       "odds_credits_reserved": 0, "odds_provider_usage": [], "llm_calls": 0,
                       "openai": {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                  "total_tokens": 0, "web_search_calls": 0},
                       "source_data_reused": True}
    if "diversification" in report:
        diagnostics = report["diversification"]
        diagnostics["club_rebuild_policy"] = "club-only-fixed-anchors-v1"
        for key in ("assigned", "abstained_profiles"):
            diagnostics[key] = {profile: value for profile, value in diagnostics.get(key, {}).items()
                                if profile not in CLUB}
        for card in scoped["recommendations"]:
            if card.get("legs"):
                diagnostics["assigned"][card["profile_id"]] = {
                    "fixture_ids": sorted({leg["fixture_id"] for leg in card["legs"]}),
                    "main_ticket": False, "scope": card.get("publication_scope"),
                    **card.get("diversification", {})}
            else:
                diagnostics["abstained_profiles"][card["profile_id"]] = {"reason_code": card.get("reason_code")}
    report["recommendation_overlap"] = recommendation_overlap(report["recommendations"])
    report.setdefault("funnel", {})["recommendations"] = sum(bool(card.get("legs")) for card in report["recommendations"])
    _refresh_diagnostics(report)
    if preserved(report) != original:
        raise ValueError("Protected nonclub objects changed")
    validate_boundary(report, now)
    report.pop("report_sha256", None)
    report["report_sha256"] = _hash(report)
    return report


def read_source(root, day, source_run, expected):
    path = root / "data/arthur/ledger.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT snapshot_json,snapshot_hash FROM portfolio_runs WHERE run_id=? AND date=?",
                         (source_run, day)).fetchone()
        if row is None:
            raise ValueError("Saved source run/date not found")
        source = json.loads(row[0])
        if _hash(source) != row[1]:
            raise ValueError("Source run hash mismatch")
        current = db.execute("SELECT run_id,snapshot_hash FROM portfolio_freeze_revisions WHERE date=? ORDER BY revision DESC LIMIT 1",
                             (day,)).fetchone()
        if current is None:
            current = db.execute("SELECT run_id,snapshot_hash FROM portfolio_freezes WHERE date=?", (day,)).fetchone()
        if current is None or current != (source_run, expected):
            raise ValueError("Source run/hash must match the currently effective freeze")
        if db.execute("SELECT 1 FROM portfolio_wagers w JOIN portfolio_tickets t ON t.ticket_id=w.ticket_id WHERE t.date=?",
                      (day,)).fetchone():
            raise ValueError("A wager exists on this date; correction cannot be published")
    directory = root / "reports/arthur" / source_run
    artifacts = {name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
                 for name in ("candidate_analysis", "fixtures", "numeric_models")}
    disk_report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if disk_report != source:
        raise ValueError("Report artifacts differ from the immutable saved source")
    return source, artifacts


def immutable_rows(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return {table: {row[0]: tuple(row) for row in db.execute(f"SELECT {columns} FROM {table}")}
                for table, columns in (
                    ("portfolio_runs", "*"),
                    ("portfolio_tickets", "ticket_id,run_id,date,profile_id,created_at,snapshot_json,snapshot_hash"),
                    ("portfolio_wagers", "*"),
                    ("portfolio_recommendations", "recommendation_id,run_id,date,profile_id,created_at,snapshot_json"))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--expected-snapshot-hash", required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--reason", default="Klubprofilok javítása: állandó kombinációs stílusok, magasabb összszorzók.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.source_run):
        parser.error("Invalid source run ID")
    if not re.fullmatch(r"[a-f0-9]{64}", args.expected_snapshot_hash):
        parser.error("Invalid snapshot hash")
    day = args.date.isoformat()
    with RunLock(ROOT / "data/arthur/run.lock"):
        source, artifacts = read_source(ROOT, day, args.source_run, args.expected_snapshot_hash)
        now = datetime.now(UTC)
        run_id = f"{day}-club-{uuid4().hex[:12]}"
        report = rebuild(source, artifacts, now, run_id)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report.pop("report_sha256", None)
        report["report_sha256"] = _hash(report)
        directory = ROOT / ("reports/arthur" if args.publish else "analysis") / run_id
        audit = {"run_id": run_id, "published": False, "source_run_id": source["run_id"],
                 "output_directory": str(directory), "preserved_nonclubs": preserved(source) == preserved(report),
                 "clubs": [{"profile_id": item["profile_id"], "name": item["profile_name"],
                            "legs": len(item.get("legs", [])), "combined_price": item.get("combined_price"),
                            "status": item["status"]} for item in report["recommendations"] if item["profile_id"] in CLUB],
                 "exposure": report["club_rebuild"]["exposure"]}
        if args.publish:
            path = ROOT / "data/arthur/ledger.sqlite3"
            before = immutable_rows(path)
            backup = ROOT / "data/arthur/backups" / f"{run_id}.sqlite3"
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(backup) as copy:
                original.backup(copy)
            # All ledger constraints are rehearsed on a separate copy first.
            rehearsal_path = ROOT / "analysis" / f"{run_id}-rehearsal.sqlite3"
            rehearsal_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(backup) as original, sqlite3.connect(rehearsal_path) as copy:
                original.backup(copy)
            rehearsal = PortfolioLedger(rehearsal_path)
            rehearsal.save_run(report)
            rehearsal.revise_freeze(run_id, args.expected_snapshot_hash, args.reason)
            read_source(ROOT, day, args.source_run, args.expected_snapshot_hash)
            validate_boundary(report, datetime.now(UTC))
            write_portfolio_bundle(directory, {"artifacts": {**artifacts, "report": report}})
            ledger = PortfolioLedger(path)
            ledger.save_run(report)
            validate_boundary(report, datetime.now(UTC))
            audit["revision"] = ledger.revise_freeze(run_id, args.expected_snapshot_hash, args.reason)
            after = immutable_rows(path)
            if any(after[table].get(key) != row for table, rows in before.items() for key, row in rows.items()):
                raise RuntimeError("Original immutable rows changed; inspect preserved backup")
            audit.update(published=True, backup=str(backup), original_immutable_rows_preserved=True,
                         actual_wagers_unchanged=before["portfolio_wagers"] == after["portfolio_wagers"])
        else:
            write_portfolio_bundle(directory, {"artifacts": {"report": report}})
        (directory / "club-rebuild-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(audit, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
