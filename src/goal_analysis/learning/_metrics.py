"""Read-only, cutoff-aware measurements over the complete Arthur ledger.

No current active index, latest-result shortcut, or 100-run API limit is used.
Legacy strategy versions are never inferred from deployment dates or current code.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from goal_analysis.storage.monetary_projection import (
    project_recorded_cohort,
    project_recorded_wager,
)
from goal_analysis.storage.portfolio_ledger import (
    _bookmaker_return_provenance,
    _hash,
    _leg_result,
    _stamp,
)
from goal_analysis.storage.recommendation_ledger import abstention_profiles

from .forecast_quality import evaluate as evaluate_forecasts

TABLES = (
    "portfolio_runs", "portfolio_tickets", "portfolio_recommendations", "portfolio_wagers",
    "portfolio_results", "portfolio_result_observations", "portfolio_settlements",
    "portfolio_settlement_observations", "portfolio_bookmaker_returns", "portfolio_freezes",
    "portfolio_freeze_revisions", "portfolio_bookmaker_return_provenance",
)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _before(value, cutoff):
    return _stamp(value) <= cutoff


def _number(value, minimum=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def _read(path: Path):
    provenance = "portfolio_bookmaker_return_provenance"
    data = {name: [] for name in TABLES}
    data["_optional_read_quality"] = {provenance: {"status": "ABSENT_TABLE", "physical_table_present": False}}
    if not path.is_file():
        return data
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in TABLES:
            if name not in present:
                continue
            if name == provenance:
                try:
                    # Reuse the actual ledger schema predicate; this never creates DDL.
                    _bookmaker_return_provenance(db, "", "")
                except (ValueError, TypeError, sqlite3.DatabaseError):
                    data["_optional_read_quality"][name] = {
                        "status": "INVALID_SCHEMA", "physical_table_present": True,
                        "reason": "INVALID_OPTIONAL_BOOKMAKER_RETURN_PROVENANCE_SCHEMA",
                        "rows_unexamined": True,
                    }
                    continue
                data["_optional_read_quality"][name] = {"status": "VALID_SCHEMA", "physical_table_present": True}
                data[name] = [dict(row) for row in db.execute("SELECT * FROM portfolio_bookmaker_return_provenance")]
            else:
                data[name] = [dict(row) for row in db.execute(f"SELECT rowid AS _rowid,* FROM {name}")]
    return data


def _snapshot(row, field="snapshot_json", hash_field="snapshot_hash"):
    value = json.loads(row[field])
    if not isinstance(value, dict):
        raise TypeError("Invalid snapshot")
    if hash_field and _hash(value) != row[hash_field]:
        raise ValueError("Snapshot integrity mismatch")
    return value


def _reference(table, key, payload):
    return {"table": table, "key": str(key), "hash": digest(payload)}


def _observations(data, entity, source, id_key, payload_field, hash_field, cutoff, quality):
    values = {row["id"]: row for row in data[source]}
    observations = data[entity]
    if not observations:
        # Original timestamps can be used for old ledgers, but not invented re-observations.
        observations = [dict(row, **{id_key: row["id"]}) for row in data[source]]
        if observations:
            quality["legacy_observation_fallback"] += len(observations)
    latest = {}
    key_name = "fixture_id" if id_key == "result_id" else "ticket_id"
    for observation in observations:
        try:
            row = values[observation[id_key]]
            when = _stamp(observation["observed_at"])
            if when > cutoff or not _before(row["observed_at"], cutoff):
                quality["cutoff_excluded"] += 1
                continue
            if when < _stamp(row["observed_at"]):
                raise ValueError("Observation precedes source")
            payload = _snapshot(row, payload_field, hash_field)
            key = observation[key_name]
            rank = when, observation.get("id", observation["_rowid"])
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, payload, _reference(source, row["id"], payload), row[hash_field])
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    return latest


def _version(record, run):
    value = record.get("strategy_version")
    if isinstance(value, str) and value.strip():
        return value[:160]
    value = run.get("policy", {}).get("strategy_version")
    return value[:160] if isinstance(value, str) and value.strip() else "UNKNOWN_LEGACY"


def _ticket_outcome(legs):
    statuses = {leg["outcome"] for leg in legs}
    if not statuses:
        return "REVIEW"
    for status in ("LOST", "REVIEW", "PENDING"):
        if status in statuses:
            return status
    return "VOID" if statuses == {"VOID"} else "WON"


def _record(record, run, result_map, cutoff, reference, quality):
    published = _stamp(run.get("finished_at", run["observed_at"]))
    # An early draft timestamp cannot make a post-kickoff completed run prospective.
    created = max(_stamp(record.get("created_at", published)), published)
    if created > cutoff or not isinstance(record.get("legs"), list) or not record["legs"]:
        raise ValueError("Not a published recommendation")
    refs, legs = [reference], []
    for index, original in enumerate(record["legs"]):
        kickoff = _stamp(original["kickoff"])
        if kickoff <= created:
            raise ValueError("Post-kickoff recommendation")
        fixture_id = str(original["fixture_id"])
        observation = result_map.get(fixture_id)
        result = None
        if observation and kickoff <= observation[0][0] <= cutoff:
            result = observation[1]
            refs.append(observation[2])
        measured = _leg_result(original, result)
        model_probability = original.get("model_probability")
        if not _number(model_probability) or model_probability > 1:
            model_probability = None
        legs.append({
            "index": index, "fixture_id": fixture_id, "kickoff": kickoff.isoformat(),
            "market": f"{original.get('period', 'FULL_TIME')}:{original.get('market_key', 'UNKNOWN')}",
            "selection": str(original.get("selection_key", "UNKNOWN")),
            "decimal_price": original.get("decimal_price") if _number(
                original.get("decimal_price"), 1.000001
            ) else None,
            "model_probability": model_probability,
            "model_version": original.get("model_details", {}).get("version", "UNKNOWN_LEGACY"),
            "confidence_kind": original.get("confidence_kind"),
            "model_details": deepcopy(original.get("model_details", {})),
            "missing_support": bool(original.get("missing_support")),
            "quote_provenance": {key: deepcopy(original.get(key)) for key in (
                "quote_provider", "quote_source_id", "bookmaker", "region", "currency",
                "api_football_fixture_id", "sport_key", "provider_market_id",
                'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker',
                "candidate_id", "quote_candidate_id", "source_candidate_id",
                "quoted_at", "quote_timestamp_raw", "settlement", "line",
            )},
            **measured,
        })
    if len({leg["fixture_id"] for leg in legs}) != len(legs):
        raise ValueError("Duplicate fixture in a ticket")
    originals = [item for group in ("recommendations", "tickets", "preview_tickets")
                 for item in run.get(group, []) if isinstance(item, dict)]
    original_match = any(
        item.get("profile_id") == record.get("profile_id")
        and item.get("legs") == record.get("legs")
        and item.get("strategy_version") == record.get("strategy_version")
        and item.get("strategy_manifest_sha256") == record.get("strategy_manifest_sha256")
        and item.get("publication_scope") == record.get("publication_scope")
        for item in originals
    )
    return {
        "id": str(record.get("recommendation_id", record.get("ticket_id", reference["key"]))),
        "run_id": run["run_id"], "date": run["date"],
        "profile_id": str(record.get("profile_id", "UNKNOWN"))[:120],
        "version": _version(record, run), "created_at": created.isoformat(),
        "portfolio_ticket_id": record.get("portfolio_ticket_id", record.get("ticket_id")),
        "legs": legs, "outcome": _ticket_outcome(legs), "source_refs": refs, "financial": None,
        "publication_scope": record.get("publication_scope"),
        "strategy_manifest_sha256": record.get("strategy_manifest_sha256"),
        "strategy_manifest": deepcopy(run.get("strategy_manifest")),
        "run_reference": _reference("portfolio_runs", run["run_id"], run),
        "original_run_match": original_match,
    }


def _active_recommendations(records, withdrawals=(), actual_ids=(), run_order=None):
    selected = {}
    run_order = run_order or {}
    events = [(item, False) for item in records] + [(item, True) for item in withdrawals]
    for item, withdraw in sorted(events, key=lambda event: (
        _stamp(event[0]["created_at"]), run_order.get(event[0]["run_id"], 0),
        event[1], event[0]["run_id"], event[0]["id"],
    )):
        key = item["date"], item["profile_id"]
        old = selected.get(key)
        if withdraw:
            if old and old["portfolio_ticket_id"] not in actual_ids and all(
                _stamp(leg["kickoff"]) > _stamp(item["created_at"]) for leg in old["legs"]
            ):
                selected.pop(key)
            continue
        if old is None or all(_stamp(leg["kickoff"]) > _stamp(item["created_at"]) for leg in old["legs"]):
            selected[key] = item
    return list(selected.values())


def _effective_freezes(data, runs, cutoff, quality):
    """Select one verified prospective chain at the historical observation cutoff."""
    originals = {row["date"]: row for row in data["portfolio_freezes"]}
    revisions = defaultdict(list)
    for row in data["portfolio_freeze_revisions"]:
        try:
            if not _before(row["frozen_at"], cutoff):
                quality["cutoff_excluded"] += 1
                continue
            if row["date"] not in originals:
                raise ValueError("Orphan package correction")
            revisions[row["date"]].append(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    effective = []
    for day, original in originals.items():
        try:
            if not _before(original["frozen_at"], cutoff):
                quality["cutoff_excluded"] += 1
                continue
            chain = [original, *sorted(revisions[day], key=lambda row: row["revision"])]
            previous, ancestor_legs = None, []
            for ordinal, row in enumerate(chain, 1):
                snapshot = _snapshot(row)
                if (row["run_id"] not in runs
                        or any(snapshot.get(key) != row[key] for key in ("date", "run_id", "frozen_at"))
                        or snapshot.get("date") != day
                        or digest(snapshot["report"]) != digest(runs[row["run_id"]])
                        or not snapshot.get("recommendations")):
                    raise ValueError("Frozen run mismatch")
                frozen_at = _stamp(row["frozen_at"])
                run = runs[row["run_id"]]
                if _stamp(run.get("finished_at", run["observed_at"])) > frozen_at:
                    raise ValueError("Freeze precedes completed publication")
                if ordinal > 1 and (
                    row["revision"] != ordinal or snapshot.get("revision") != ordinal
                    or row["parent_snapshot_hash"] != previous[0]["snapshot_hash"]
                    or snapshot.get("parent_snapshot_hash") != row["parent_snapshot_hash"]
                    or snapshot.get("reason") != row["reason"]
                    or frozen_at < _stamp(previous[0]["frozen_at"])
                    or _stamp(run["observed_at"]) <= _stamp(previous[1]["report"]["observed_at"])
                ):
                    raise ValueError("Invalid package correction chain")
                legs = [leg for item in snapshot["recommendations"] for leg in item["legs"]]
                if not legs or any(_stamp(leg["kickoff"]) <= frozen_at for leg in ancestor_legs + legs):
                    raise ValueError("Post-kickoff freeze or correction")
                ancestor_legs.extend(legs)
                previous = row, snapshot
            effective.append((*previous, "portfolio_freeze_revisions" if len(chain) > 1 else "portfolio_freezes"))
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    return effective


def _actual_record(ticket, wager, settlement, returns, cutoff, return_provenance=(), provenance_quality=None):
    if settlement:
        _, payload, reference, settlement_hash = settlement
        ticket["source_refs"].append(reference)
        ticket["outcome"] = payload.get("outcome", "REVIEW")
        settled_legs = payload.get("legs", [])
        if len(settled_legs) != len(ticket["legs"]):
            raise ValueError("Settlement leg count mismatch")
        for leg, settled in zip(ticket["legs"], settled_legs, strict=True):
            if leg["fixture_id"] != str(settled["fixture_id"]):
                raise ValueError("Settlement fixture mismatch")
            leg["outcome"] = settled["outcome"]
    else:
        ticket["outcome"] = "PENDING"
        for leg in ticket["legs"]:
            leg["outcome"] = "PENDING"
        settlement_hash = None
    ticket["wager"] = dict(wager)
    ticket["ticket_id"] = wager["ticket_id"]
    ticket["as_of"] = cutoff.isoformat()
    payload = dict(settlement[1]) if settlement else {}
    if settlement:
        payload["settlement_hash"] = settlement_hash
        payload["observed_at"] = settlement[0][0].isoformat()
    confirmed = [row for row in returns if row["ticket_id"] == wager["ticket_id"]
                 and row["settlement_hash"] == settlement_hash and _before(row["recorded_at"], cutoff)]
    if len(confirmed) == 1:
        row = confirmed[0]
        confirmation = dict(row)
        metas = [meta for meta in return_provenance if meta.get("ticket_id") == wager["ticket_id"]
                 and meta.get("settlement_hash") == settlement_hash]
        if (provenance_quality or {}).get("status") == "INVALID_SCHEMA":
            confirmation["provenance_status"] = "INVALID_PROVENANCE"
            confirmation["provenance_diagnostic"] = provenance_quality
        elif len(metas) == 1:
            meta = metas[0]
            required = {"payout_basis", "fee_tax_inclusion", "confirmed_at", "contract_version"}
            valid_meta = required <= set(meta) and meta["contract_version"] == "bookmaker-gross-return-v1"
            valid_meta = valid_meta and meta.get("payout_basis") == "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN" and meta.get("fee_tax_inclusion") == "EXCLUDED"
            try:
                valid_meta = valid_meta and isinstance(meta.get("confirmed_at"), str) and bool(_stamp(meta["confirmed_at"]))
            except (ValueError, TypeError):
                valid_meta = False
            if valid_meta:
                confirmation.update(contract_version=meta["contract_version"],
                    payout_basis=meta["payout_basis"],
                    fee_tax_inclusion=meta["fee_tax_inclusion"],
                    provenance_confirmed_at=meta["confirmed_at"], provenance_status="RECORDED_PROVENANCE")
            else:
                confirmation["provenance_status"] = "INVALID_PROVENANCE"
            ticket["source_refs"].append(_reference("portfolio_bookmaker_return_provenance",
                                                   f"{wager['ticket_id']}:{settlement_hash}", meta))
        elif metas:
            confirmation["provenance_status"] = "INVALID_PROVENANCE"
        payload["bookmaker_return"] = confirmation
        ticket["source_refs"].append(_reference("portfolio_bookmaker_returns", row["_rowid"], row))
    elif confirmed:
        payload["bookmaker_return"] = {"provenance_status": "INVALID_PROVENANCE"}
    ticket["settlement"] = payload
    projection = project_recorded_wager(ticket)
    # Preserve the original partial-VOID presentation, without changing sports evidence.
    if ticket["outcome"] == "WON" and any(leg["outcome"] == "VOID" for leg in ticket["legs"]):
        if projection["status"] != "KNOWN":
            ticket["outcome"] = "REVIEW"
        elif projection["payout_eur"] == projection["stake_eur"]:
            ticket["outcome"] = "VOID"
    projection["settled_at"] = settlement[0][0].isoformat() if settlement else None
    ticket["financial"] = projection
    ticket["source_refs"].append(_reference("portfolio_wagers", wager["ticket_id"], wager))
    return ticket


def _counts(items):
    counts = Counter(item["outcome"] for item in items)
    decided = counts["WON"] + counts["LOST"]
    return {
        "total": len(items), "decided": decided,
        **{key.lower(): counts[key] for key in ("WON", "LOST", "VOID", "PENDING", "REVIEW")},
        "hit_rate_pct": round(100 * counts["WON"] / decided, 4) if decided else None,
    }


def _summarize(records, actual=False):
    legs = [leg for record in records for leg in record["legs"]]
    if actual:
        projection = project_recorded_cohort(records)
        financial = {
            "available": bool(records) and projection["complete_financial_coverage"],
            "settled_stake_eur": projection["settled_stake_eur"],
            "payout_eur": projection["payout_eur"], "net_eur": projection["net_eur"],
            "roi_pct": projection["yield_pct"],
            "unverified_price_tickets": sum(bool(row.get("financial") and
                not row["financial"].get("verified_price")) for row in records),
            "costs_included": False, "open_stake_eur": projection["open_stake_eur"],
            "known_subset_net_eur": projection["known_subset_net_eur"],
            "monetary_projection": projection,
            "bank_cash_receipt_eur": None, "after_cost_system_net_eur": None,
        }
    else:
        settled = [r["financial"] for r in records if r["financial"] and r["financial"]["net_eur"] is not None]
        stake = sum(Decimal(str(r["stake_eur"])) for r in settled)
        payout = sum(Decimal(str(r["payout_eur"])) for r in settled)
        unverified = sum(bool(r["financial"] and not r["financial"]["verified_price"]) for r in records)
        money_available = actual and bool(records) and not unverified
        financial = {
            "available": money_available,
            "settled_stake_eur": round(float(stake), 2) if money_available else None,
            "payout_eur": round(float(payout), 2) if money_available else None,
            "net_eur": round(float(payout - stake), 2) if money_available else None,
            "roi_pct": round(float((payout - stake) / stake * 100), 4) if money_available and stake else None,
            "unverified_price_tickets": unverified,
            "costs_included": False,
            "open_stake_eur": round(sum(r["financial"]["stake_eur"] for r in records
                                        if r["financial"] and r["outcome"] in {"PENDING", "REVIEW"}), 2)
            if actual else None,
        }
    tickets = _counts(records)
    unique_selections = {(l["fixture_id"], l["market"], l["selection"]) for l in legs}
    return {
        "tickets": tickets, "legs": _counts(legs),
        "unique_fixtures": len({l["fixture_id"] for l in legs}),
        "unique_selections": len(unique_selections),
        "missing_odds_legs": sum(l["decimal_price"] is None for l in legs),
        "small_sample": tickets["decided"] < 30, "financial": financial,
        "shared_fixture_occurrences": len(legs) - len({l["fixture_id"] for l in legs}),
    }


def measure(path: Path, as_of: datetime, *, include_records: bool = False,
            closing_quotes=()) -> dict:
    """Read original ledger forecasts; optional closing quotes must be source-verified.

    No archive scan or provider lookup is performed. Receipt-only prices never
    become closing quotes. Default missing closing coverage is explicitly null.
    """
    cutoff, data, quality = _stamp(as_of), _read(path), Counter()
    closing_quotes = tuple(closing_quotes)
    runs = {}
    for row in data["portfolio_runs"]:
        try:
            if not all(_before(row[k], cutoff) for k in ("observed_at", "saved_at")):
                quality["cutoff_excluded"] += 1
                continue
            run = _snapshot(row)
            if not _before(run.get("finished_at", run["observed_at"]), cutoff):
                quality["cutoff_excluded"] += 1
                continue
            runs[row["run_id"]] = run
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    results = _observations(data, "portfolio_result_observations", "portfolio_results",
                            "result_id", "payload_json", "payload_hash", cutoff, quality)
    settlements = _observations(data, "portfolio_settlement_observations", "portfolio_settlements",
                                "settlement_id", "settlement_json", "settlement_hash", cutoff, quality)
    recommendations, actual, frozen = [], [], []
    for row in data["portfolio_recommendations"]:
        try:
            if row["run_id"] not in runs or not _before(row["created_at"], cutoff):
                quality["cutoff_excluded"] += 1
                continue
            record = _snapshot(row, hash_field=None)
            recommendations.append(_record(record, runs[row["run_id"]], results, cutoff,
                _reference("portfolio_recommendations", row["recommendation_id"], record), quality))
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    # Older run snapshots are valid source evidence even if their optional recommendation
    # index was never migrated. Preserve only original selections and their original version.
    indexed_runs = {row["run_id"] for row in data["portfolio_recommendations"]}
    for run_id, run in runs.items():
        if run_id in indexed_runs:
            continue
        original = run.get("recommendations")
        if original is None:
            original = run.get("tickets", []) + run.get("preview_tickets", [])
        if not isinstance(original, list):
            quality["invalid_records"] += 1
            continue
        for source in original:
            if not isinstance(source, dict) or not source.get("legs"):
                continue
            try:
                record = deepcopy(source)
                if "ticket_id" in record:
                    record.setdefault("portfolio_ticket_id", record["ticket_id"])
                record.setdefault("recommendation_id", digest([run_id, record["profile_id"]])[:24])
                record.setdefault("created_at", run.get("finished_at", run["observed_at"]))
                recommendations.append(_record(record, run, results, cutoff,
                    _reference("portfolio_runs", run_id, run), quality))
                quality["legacy_original_snapshot_selections"] += 1
            except (KeyError, TypeError, ValueError, AttributeError):
                quality["invalid_records"] += 1
    tickets = {row["ticket_id"]: row for row in data["portfolio_tickets"]}
    for wager in data["portfolio_wagers"]:
        try:
            row = tickets[wager["ticket_id"]]
            if row["run_id"] not in runs or not _before(wager["recorded_at"], cutoff):
                quality["cutoff_excluded"] += 1
                continue
            record = _snapshot(row)
            item = _record(record, runs[row["run_id"]], results, cutoff,
                           _reference("portfolio_tickets", row["ticket_id"], record), quality)
            actual.append(_actual_record(item, wager, settlements.get(wager["ticket_id"]),
                                         data["portfolio_bookmaker_returns"], cutoff,
                                         data["portfolio_bookmaker_return_provenance"],
                                         data.get("_optional_read_quality", {}).get("portfolio_bookmaker_return_provenance")))
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
            quality["invalid_records"] += 1
    frozen_packages = 0
    for row, snapshot, table in _effective_freezes(data, runs, cutoff, quality):
        try:
            package = []
            for record in snapshot["recommendations"]:
                if any(_stamp(l["kickoff"]) <= _stamp(row["frozen_at"]) for l in record["legs"]):
                    raise ValueError("Post-kickoff freeze")
                package.append(_record(record, runs[row["run_id"]], results, cutoff,
                    _reference(table, f"{row['date']}:r{row['revision']}" if "revision" in row
                               else row["date"], snapshot), quality))
            frozen.extend(package)
            frozen_packages += 1
        except (KeyError, TypeError, ValueError, AttributeError):
            quality["invalid_records"] += 1
    actual_ids = {record["portfolio_ticket_id"] for record in actual}
    withdrawals = [{"date": run["date"], "profile_id": profile, "run_id": run["run_id"],
                    "created_at": run.get("finished_at", run["observed_at"]),
                    "id": "abstained:" + profile}
                   for run in runs.values() for profile in abstention_profiles(run)]
    active = _active_recommendations(recommendations, withdrawals, actual_ids,
                                    {row["run_id"]: row["_rowid"] for row in data["portfolio_runs"]})
    def signature(record):
        return record["date"], record["profile_id"], tuple(sorted(
            (l["fixture_id"], l["market"], l["selection"]) for l in record["legs"]
        ))
    actual_signatures = {signature(record) for record in actual}
    research = [r for r in active if r["portfolio_ticket_id"] not in actual_ids
                and signature(r) not in actual_signatures]
    scopes = {"actual": actual, "research": research, "frozen": frozen}
    metrics, evidence = {}, []

    def add_evidence(scope, kind, label, values, records):
        refs = {json.dumps(ref, sort_keys=True): ref for record in records for ref in record["source_refs"]}
        item = {"scope": scope, "kind": kind, "label": label, "metrics": deepcopy(values),
                "source_refs": [refs[key] for key in sorted(refs)]}
        item["evidence_id"] = "ev-" + digest(item)[:24]
        evidence.append(item)

    for scope, records in scopes.items():
        summary = _summarize(records, scope == "actual")
        add_evidence(scope, "SCOPE_SUMMARY", scope, summary, records)
        by_profile, by_version, by_market = defaultdict(list), defaultdict(list), defaultdict(list)
        for record in records:
            by_profile[record["profile_id"]].append(record)
            by_version[record["version"]].append(record)
            for leg in record["legs"]:
                by_market[leg["market"]].append((record, leg))
        summary["by_profile"], summary["by_version"], summary["by_market"] = [], [], []
        for kind, groups, target, field in (("PROFILE", by_profile, "by_profile", "profile_id"),
                                          ("VERSION", by_version, "by_version", "version")):
            for label, items in sorted(groups.items()):
                values = _summarize(items, scope == "actual")
                summary[target].append({field: label, "tickets": values["tickets"], "legs": values["legs"]})
                add_evidence(scope, kind, label, values, items)
        for market, items in sorted(by_market.items()):
            values = {"legs": _counts([leg for _, leg in items])}
            summary["by_market"].append({"market": market, **values})
            add_evidence(scope, "MARKET", market, values, [r for r, _ in items])
        quality_scores = evaluate_forecasts(records, as_of=cutoff, closing_quotes=closing_quotes)
        # Keep the LLM briefing compact: no individual forecasts, per-version
        # groups, or closing archive payload is added to calibration evidence.
        summary["calibration"] = {key: quality_scores[key] for key in (
            "eligible_selections", "validated", "brier_binary", "log_loss_binary",
            "log_loss_floor", "log_loss_floor_applied", "bins", "basis", "excluded",
        )}
        add_evidence(scope, "CALIBRATION", "Original model predictions", summary["calibration"], records)
        summary["forecast_quality"] = quality_scores
        metrics[scope] = summary
    dates = sorted(r["date"] for r in runs.values())
    coverage = {
        "optional_read_quality": data.get("_optional_read_quality", {}),
        "runs_total": len(data["portfolio_runs"]), "runs_as_of": len(runs),
        "recommendations_total": len(data["portfolio_recommendations"]),
        "recommendations_as_of": len(recommendations), "actual_wagers_as_of": len(actual),
        "frozen_packages_as_of": frozen_packages, "earliest_date": dates[0] if dates else None,
        "latest_date": dates[-1] if dates else None,
        "cutoff_excluded": quality["cutoff_excluded"], "invalid_records": quality["invalid_records"],
        "legacy_observation_fallback": quality["legacy_observation_fallback"],
        "legacy_original_snapshot_selections": quality["legacy_original_snapshot_selections"],
        "unknown_version_recommendations": sum(r["version"] == "UNKNOWN_LEGACY" for r in active),
        "unverified_actual_prices": sum(not r["financial"]["verified_price"] for r in actual),
    }
    add_evidence("all", "DATA_QUALITY", "Ledger coverage and cutoff", coverage,
                 [record for records in scopes.values() for record in records])
    output = {"metrics": metrics, "evidence": evidence, "coverage": coverage}
    if include_records:
        # Explicit internal export for the supervisor. Existing learning reports
        # remain unchanged. Published includes manually played recommendations;
        # wager selection must not change the prospective paper cohort.
        output["records"] = {**deepcopy(scopes), "published": deepcopy(active)}
    return output
