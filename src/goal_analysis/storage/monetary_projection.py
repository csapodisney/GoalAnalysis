"""Pure recorded-wager arithmetic; never a bank balance or after-cost profit.

Adapters must supply already verified source rows and the observed settlement
hash. This module does not turn a self-consistent dictionary into source proof.
No clock, database, network, configuration, or current stake policy is consulted.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

VERSION = "arthur-recorded-money-projection-v1"
GROSS = "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN"
LEGACY = "LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED"
RETURN_CONTRACT_VERSION = "bookmaker-gross-return-v1"
TERMINAL = frozenset({"WON", "LOST", "VOID"})


def _digest(value):
    # Diagnostic input identity, not a claim of a stored ledger/source hash.
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         default=str, allow_nan=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decimal(value, *, positive=False, cents=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    if len(str(value)) > 256:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (positive and number <= 0):
            return None
        with localcontext() as ctx:
            ctx.prec = 270
            if cents and number != number.quantize(Decimal("0.01")):
                return None
        if not math.isfinite(float(number)):
            return None
        return number
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _money(number):
    with localcontext() as ctx:
        ctx.prec = 550
        value = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Recorded amount cannot be represented")
    return result


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError:
        return None


def project_recorded_wager(record, *, contract="CURRENT_HASH_V2", allow_estimated_price_arithmetic=False):
    """Project one actual wager, keeping unknown returns and money roles explicit.

    Optional ``as_of`` enforces supplied observation clocks without inventing
    observations. A02 exposes current settlement/return hashes on read views;
    raw SQL adapters supply those same stored hashes after their own cutoff read.
    """
    if not isinstance(record, Mapping):
        raise TypeError("Recorded wager mapping required")
    if contract not in {"CURRENT_HASH_V2", "LEGACY_RECORDED_V1"}:
        raise ValueError("Unknown recorded-money contract")
    ticket_id = record.get("ticket_id", record.get("portfolio_ticket_id"))
    wager = record.get("wager")
    result = {
        "monetary_projection_version": VERSION, "monetary_contract": contract, "ticket_id": ticket_id,
        "verified_cash": False, "revision_binding": "CURRENT_HASH_REQUIRED", "warnings": [],
        "estimated_price_display_allowance": contract == "LEGACY_RECORDED_V1" and allow_estimated_price_arithmetic is True,
        "input_sha256": _digest(record), "basis": "MANUALLY_RECORDED",
        "status": "UNKNOWN", "reason": None, "stake_eur": None,
        "payout_eur": None, "net_eur": None, "open_stake_eur": None,
        "cash_flow_eur": None, "cash_flow_basis": "RECORDED_STAKE_AND_RETURN_ARITHMETIC_NOT_BANK_CASH",
        "settled": False, "sports_outcome": record.get("outcome", "PENDING"),
        "settlement_hash": None, "actual_price_source": "UNKNOWN_LEGACY",
        "verified_price": False, "payout_basis": "UNKNOWN",
        "fee_tax_inclusion": "UNKNOWN", "reported_return_eur": None,
        "reported_return_arithmetic_net_eur": None,
        "gross_sports_return_eur": None, "gross_sports_net_eur": None,
        "bank_cash_receipt_eur": None, "after_cost_system_net_eur": None,
        "costs_included": False, "source_authentication": "CALLER_VERIFIED_ROWS_REQUIRED",
        "excluded_evidence": [],
    }
    def unknown(reason, *, status="UNKNOWN"):
        result.update(status=status, reason=reason)
        return result
    if not isinstance(ticket_id, str) or not ticket_id:
        return unknown("MISSING_WAGER_ID", status="INVALID")
    if not isinstance(wager, Mapping) or not wager:
        return unknown("NO_RECORDED_WAGER", status="NOT_ACTUAL")
    if record.get("currency", "EUR") != "EUR" or wager.get("currency", "EUR") != "EUR":
        return unknown("UNSUPPORTED_CURRENCY_NO_FX_INFERENCE", status="INVALID")
    if wager.get("ticket_id", ticket_id) != ticket_id:
        return unknown("WAGER_IDENTITY_MISMATCH", status="INVALID")
    stake = _decimal(wager.get("stake_eur"), positive=True, cents=True)
    if stake is None:
        return unknown("INVALID_OR_MISSING_ORIGINAL_STAKE", status="INVALID")
    result["stake_eur"] = _money(stake)
    result["open_stake_eur"] = _money(stake)
    source_tags = [wager[key] for key in ("price_source", "actual_price_source")
                   if wager.get(key) is not None]
    source_conflict = len(set(map(str, source_tags))) > 1
    source = str(source_tags[0]) if source_tags and not source_conflict else "UNKNOWN_LEGACY"
    result["actual_price_source"] = source
    if source_conflict:
        result["excluded_evidence"].append("CONTRADICTORY_PRICE_SOURCE_TAGS")
    cutoff = None
    if "as_of" in record:
        cutoff = _time(record["as_of"])
        observed = _time(wager.get("recorded_at"))
        if cutoff is None or observed is None:
            return unknown("INVALID_WAGER_CUTOFF_OR_OBSERVATION", status="INVALID")
        if observed > cutoff:
            return unknown("WAGER_NOT_OBSERVED_BY_CUTOFF", status="FUTURE")
    settlement = record.get("settlement")
    settlement = settlement if isinstance(settlement, Mapping) else {}
    current_hash = settlement.get("settlement_hash")
    current_hash = current_hash if isinstance(current_hash, str) and re.fullmatch(r"[0-9a-f]{64}", current_hash) else None
    result["settlement_hash"] = current_hash
    outcome = record.get("outcome", settlement.get("outcome", "PENDING"))
    # Only a real partial-VOID read view may differ from its raw terminal
    # identity. Monetary admission below still requires its current return.
    underlying = settlement.get("research_outcome", settlement.get("outcome"))
    source_legs = settlement.get("legs", [])
    valid_legs = isinstance(source_legs, list) and bool(source_legs) and all(isinstance(leg, Mapping) for leg in source_legs)
    partial_view = (outcome in {"REVIEW", "VOID"} and underlying == "WON" and valid_legs
        and any(leg.get("outcome") == "VOID" for leg in source_legs)
        and any(leg.get("outcome") == "WON" for leg in source_legs)
        and all(leg.get("outcome") in {"WON", "VOID"} for leg in source_legs))
    if (contract == "CURRENT_HASH_V2" and settlement.get("research_outcome") in TERMINAL
            and settlement.get("outcome") in TERMINAL
            and settlement["research_outcome"] != settlement["outcome"]
            and not (partial_view and settlement["research_outcome"] == "WON"
                     and settlement["outcome"] == "VOID")):
        return unknown("CONTRADICTORY_RAW_RESEARCH_SETTLEMENT_IDENTITY", status="INVALID")
    if contract == "CURRENT_HASH_V2" and outcome in TERMINAL:
        if current_hash is None:
            return unknown("TERMINAL_SETTLEMENT_HASH_MISSING_OR_INVALID", status="INVALID")
        if underlying not in TERMINAL:
            return unknown("TERMINAL_SETTLEMENT_IDENTITY_MISSING", status="INVALID")
        if outcome != underlying and not partial_view:
            return unknown("CONTRADICTORY_TERMINAL_SETTLEMENT_IDENTITY", status="INVALID")
        if not valid_legs:
            return unknown("TERMINAL_SETTLEMENT_LEGS_MISSING_OR_INVALID", status="INVALID")
    if partial_view:
        outcome = underlying
    elif contract == "LEGACY_RECORDED_V1" and outcome == "REVIEW" and underlying in TERMINAL:
        # Public v1 display compatibility carries an explicit unverified role.
        outcome = underlying
    result["sports_outcome"] = outcome
    if cutoff is not None and settlement:
        observed = _time(settlement.get("observed_at", settlement.get("updated_at", settlement.get("settled_at"))))
        if observed is None:
            return unknown("SETTLEMENT_OBSERVATION_UNKNOWN")
        if observed > cutoff:
            result["excluded_evidence"].append("SETTLEMENT_AFTER_CUTOFF")
            outcome = "PENDING"
            result["sports_outcome"] = outcome
    if outcome not in TERMINAL:
        result["cash_flow_eur"] = -_money(stake)
        if contract == "LEGACY_RECORDED_V1":
            result.update(payout_eur=0.0, net_eur=0.0,
                          payout_basis="LEGACY_DISPLAY_PLACEHOLDER_NOT_ACTUAL_PAYOUT")
            result["warnings"].append("ZERO_IS_LEGACY_UNSETTLED_DISPLAY_NOT_OBSERVED_PAYOUT_OR_NET")
        return unknown("UNRESOLVED_SPORTS_OR_MONETARY_SETTLEMENT", status="OPEN")
    legs = settlement.get("legs", [])
    if not isinstance(legs, list) or any(not isinstance(leg, Mapping) for leg in legs):
        return unknown("MALFORMED_SETTLEMENT_LEGS")
    if contract == "CURRENT_HASH_V2":
        if outcome == "VOID" and any(leg.get("outcome") != "VOID" for leg in legs):
            return unknown("FULL_VOID_LEG_IDENTITY_MISMATCH", status="INVALID")
        if outcome == "LOST" and not any(leg.get("outcome") == "LOST" for leg in legs):
            return unknown("LOST_LEG_IDENTITY_MISSING", status="INVALID")
        if outcome == "WON" and any(leg.get("outcome") not in {"WON", "VOID"} for leg in legs):
            return unknown("WON_LEG_IDENTITY_MISMATCH", status="INVALID")
    partial_void = outcome == "WON" and any(leg.get("outcome") == "VOID" for leg in legs)
    confirmation = settlement.get("bookmaker_return")
    confirmation = confirmation if isinstance(confirmation, Mapping) else {}
    admitted_return = None
    if confirmation:
        legacy_unbound = (contract == "LEGACY_RECORDED_V1" and current_hash is None
            and confirmation.get("settlement_hash") is None
            and confirmation.get("ticket_id", ticket_id) == ticket_id
            and confirmation.get("payout_basis", LEGACY) == LEGACY
            and not confirmation.get("contract_version")
            and confirmation.get("provenance_status") not in {"INVALID", "INVALID_PROVENANCE"})
        if legacy_unbound:
            amount = _decimal(confirmation.get("payout_eur"), cents=True)
            if amount is not None:
                admitted_return = amount
                result.update(payout_basis=LEGACY, reported_return_eur=_money(amount),
                    reported_return_arithmetic_net_eur=_money(amount - stake),
                    revision_binding="LEGACY_UNBOUND_REPORTED_ARITHMETIC")
                result["warnings"].append("UNBOUND_LEGACY_AMOUNT_NOT_VERIFIED_CASH_OR_CURRENT_REVISION")
            else:
                result["excluded_evidence"].append("INVALID_RECORDED_RETURN")
        elif (current_hash is None or confirmation.get("settlement_hash") != current_hash
                or confirmation.get("ticket_id", ticket_id) != ticket_id):
            result["excluded_evidence"].append("RETURN_REVISION_OR_WAGER_IDENTITY_MISMATCH")
        elif cutoff is not None and (_time(confirmation.get("recorded_at")) is None
                                     or _time(confirmation["recorded_at"]) > cutoff):
            result["excluded_evidence"].append("RETURN_NOT_OBSERVED_BY_CUTOFF")
        elif confirmation.get("provenance_status") in {"INVALID", "INVALID_PROVENANCE"}:
            result["excluded_evidence"].append("INVALID_RETURN_PROVENANCE")
        else:
            amount = _decimal(confirmation.get("payout_eur"), cents=True)
            if amount is not None:
                payout_basis = confirmation.get("payout_basis") or LEGACY
                inclusion = confirmation.get("fee_tax_inclusion") or "UNKNOWN"
                if payout_basis not in {GROSS, LEGACY}:
                    result["excluded_evidence"].append("UNSUPPORTED_RETURN_BASIS_NO_CASH_OR_FEE_INFERENCE")
                elif payout_basis == GROSS:
                    result.update(payout_basis=GROSS, fee_tax_inclusion=inclusion,
                                  reported_return_eur=_money(amount),
                                  reported_return_arithmetic_net_eur=_money(amount - stake))
                    if (inclusion != "EXCLUDED"
                            or confirmation.get("contract_version") != RETURN_CONTRACT_VERSION
                            or _time(confirmation.get("provenance_confirmed_at")) is None
                            or (cutoff is not None and _time(confirmation["provenance_confirmed_at"]) > cutoff)):
                        return unknown("INVALID_TYPED_GROSS_PROVENANCE", status="INVALID")
                    if amount < stake:
                        return unknown("TYPED_GROSS_BELOW_ORIGINAL_STAKE", status="INVALID")
                    admitted_return = amount
                else:
                    admitted_return = amount
                    result.update(payout_basis=payout_basis, fee_tax_inclusion=inclusion,
                                  reported_return_eur=_money(amount),
                                  reported_return_arithmetic_net_eur=_money(amount - stake))
            else:
                result["excluded_evidence"].append("INVALID_RECORDED_RETURN")
    if admitted_return is not None:
        payout = admitted_return
        if outcome == "LOST" and payout != 0:
            return unknown("RETURN_CONTRADICTS_LOST_SETTLEMENT")
        if outcome == "VOID" and payout != stake:
            return unknown("RETURN_CONTRADICTS_FULL_VOID_SETTLEMENT")
    elif partial_void:
        if contract == "LEGACY_RECORDED_V1":
            result.update(payout_eur=0.0, net_eur=0.0, cash_flow_eur=-_money(stake),
                          payout_basis="LEGACY_DISPLAY_PLACEHOLDER_NOT_ACTUAL_PAYOUT")
            result["warnings"].append("ZERO_IS_LEGACY_UNSETTLED_DISPLAY_NOT_OBSERVED_PAYOUT_OR_NET")
            return unknown("PARTIAL_VOID_REQUIRES_CURRENT_HASH_BOUND_RETURN", status="OPEN")
        return unknown("PARTIAL_VOID_REQUIRES_CURRENT_HASH_BOUND_RETURN")
    elif outcome == "LOST":
        payout = Decimal(0)
        result["payout_basis"] = "RECORDED_STAKE_AND_LOST_SPORTS_SETTLEMENT"
    elif outcome == "VOID":
        payout = stake
        result["payout_basis"] = "RECORDED_STAKE_AND_FULL_VOID_SPORTS_SETTLEMENT"
    else:
        price = _decimal(wager.get("actual_combined_price"), positive=True)
        if price is None or price <= 1 or source_conflict:
            return unknown("WINNING_PRICE_OR_CURRENT_RETURN_NOT_VERIFIED")
        if source != "USER_CONFIRMED" and not (contract == "LEGACY_RECORDED_V1" and allow_estimated_price_arithmetic is True):
            return unknown("WINNING_PRICE_OR_CURRENT_RETURN_NOT_VERIFIED")
        with localcontext() as ctx:
            ctx.prec = 550
            payout = Decimal(str(_money(stake * price)))
        result["verified_price"] = source == "USER_CONFIRMED"
        if source == "USER_CONFIRMED":
            result["payout_basis"] = "USER_CONFIRMED_ACCEPTED_PRICE_SPORTS_SETTLEMENT"
        else:
            result["payout_basis"] = "LEGACY_RECORDED_PRICE_ARITHMETIC"
            result["warnings"].append("ESTIMATED_PRICE_ARITHMETIC_NOT_VERIFIED_ACTUAL_PAYOUT_OR_PROFIT")
    result.update(status="KNOWN", reason="RECORDED_SETTLEMENT_ARITHMETIC_AVAILABLE",
                  payout_eur=_money(payout), net_eur=_money(payout - stake),
                  open_stake_eur=0.0, cash_flow_eur=_money(payout - stake), settled=True)
    if result["payout_basis"] not in {LEGACY, "LEGACY_RECORDED_PRICE_ARITHMETIC"}:
        result.update(gross_sports_return_eur=_money(payout), gross_sports_net_eur=_money(payout - stake))
    return result


def project_recorded_cohort(records, *, unassigned_source_records=0, contract="CURRENT_HASH_V2", allow_estimated_price_arithmetic=False):
    """Complete key accounting; shared fixtures never merge distinct wager IDs."""
    if not isinstance(records, (list, tuple)):
        raise TypeError("Complete recorded cohort list required")
    if type(unassigned_source_records) is not int or unassigned_source_records < 0:
        raise ValueError("Invalid unassigned source count")
    grouped, unkeyed = {}, []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            unkeyed.append({"source_index": index, "reason": "NON_MAPPING_RECORD"})
            continue
        key = record.get("ticket_id", record.get("portfolio_ticket_id"))
        if not isinstance(key, str) or not key:
            unkeyed.append({"source_index": index, "reason": "MISSING_WAGER_ID"})
            continue
        grouped.setdefault(key, []).append((index, record))
    projected, duplicates, conflicts = [], [], []
    for key, aliases in sorted(grouped.items()):
        identities = {_digest(record) for _, record in aliases}
        if len(identities) != 1:
            conflicts.append({"ticket_id": key, "source_indices": [i for i, _ in aliases],
                              "record_sha256": sorted(identities)})
            continue
        projected.append(project_recorded_wager(aliases[0][1], contract=contract,
                         allow_estimated_price_arithmetic=allow_estimated_price_arithmetic))
        duplicates.extend({"ticket_id": key, "source_index": index,
                           "canonical_source_index": aliases[0][0]} for index, _ in aliases[1:])
    known = [row for row in projected if row["status"] == "KNOWN"]
    opened = [row for row in projected if row["status"] == "OPEN"]
    excluded = [row for row in projected if row["status"] not in {"KNOWN", "OPEN"}]
    complete = not (excluded or conflicts or unkeyed or unassigned_source_records)
    def sum_money(name, rows):
        return _money(sum((Decimal(str(row[name])) for row in rows), Decimal(0)))
    known_stake = sum_money("stake_eur", known)
    known_payout = sum_money("payout_eur", known)
    known_net = sum_money("net_eur", known)
    valid_stake = [row for row in projected if row["stake_eur"] is not None]
    open_rows = [row for row in projected if row["open_stake_eur"] is not None]
    original_stake = sum_money("stake_eur", valid_stake)
    open_stake = sum_money("open_stake_eur", open_rows)
    return {
        "version": VERSION, "basis": "RECORDED_SETTLEMENT_ARITHMETIC_NOT_BANK_CASH",
        "monetary_contract": contract, "verified_cash": False,
        "legacy_unbound_arithmetic_records": sum(row["revision_binding"] == "LEGACY_UNBOUND_REPORTED_ARITHMETIC" for row in projected),
        "source_records": len(records), "unique_wager_keys": len(grouped),
        "projected_records": len(projected), "known_closed_records": len(known),
        "open_records": len(opened), "unknown_or_invalid_records": len(excluded),
        "complete_financial_coverage": complete, "closed_financial_complete": complete,
        "stake_eur": original_stake if not conflicts and not unkeyed else None,
        "known_original_stake_eur": original_stake, "open_stake_eur": open_stake,
        "settled_stake_eur": known_stake if complete else None,
        "payout_eur": known_payout if complete else None,
        "net_eur": known_net if complete else None,
        "yield_pct": round(100 * known_net / known_stake, 4) if complete and known_stake else None,
        "cash_flow_eur": _money(Decimal(str(known_payout)) - Decimal(str(original_stake))) if complete else None,
        "known_subset_settled_stake_eur": known_stake,
        "known_subset_payout_eur": known_payout, "known_subset_net_eur": known_net,
        "duplicate_references": duplicates, "conflicting_wagers": conflicts,
        "unkeyed_records": unkeyed, "unassigned_source_records": unassigned_source_records,
        "missing_reasons": dict(Counter(row["reason"] for row in excluded)),
        "records": projected, "bank_cash_receipt_eur": None,
        "after_cost_system_net_eur": None, "costs_included": False,
        "fee_tax_or_subscription_allocation_inferred": False,
        "cohort_input_sha256": _digest(records),
    }
