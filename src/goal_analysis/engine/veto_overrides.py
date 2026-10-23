"""Source-bound Astra exceptions to named soft heuristics, never hard gates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-source-bound-soft-veto-override-v1"
ALLOWED_GATES = frozenset({"HIGH_ODDS", "LEAGUE_BASE_RATE"})


def _league_history_supported(candidate, analysis):
    if (not isinstance(analysis, Mapping) or analysis.get("status") != "CALCULATED"
            or analysis.get("candidate_id") != candidate.get("candidate_id")
            or analysis.get("period") != candidate.get("period")
            or not analysis.get("history_league_id")
            or ((candidate.get("provider_league_id", candidate.get("api_football_league_id"))) is not None
                and str(analysis["history_league_id"]) != str(candidate.get("provider_league_id", candidate.get("api_football_league_id"))))):
        return False
    try:
        if not all(type(analysis["profiles"][side][window]["sample_size"]) is int
                   and analysis["profiles"][side][window]["sample_size"] >= 3
                   for side in ("home", "away") for window in ("historical_20", "venue_10")):
            return False
    except (KeyError, TypeError):
        return False
    evidence = candidate.get("evidence")
    if not isinstance(evidence, list):
        return False
    for category in ("historical", "venue_form"):
        if not any(isinstance(item, Mapping) and item.get("category") == category
                   and item.get("evidence_id") == f"history-v1:{candidate.get('candidate_id')}:{category}"
                   and isinstance(item.get("source_id"), str)
                   and re.fullmatch(r"history-sha256:[0-9a-f]{64}", item["source_id"])
                   and type(item.get("strength")) in (int, float) and item["strength"] > 0
                   and type(item.get("reliability")) in (int, float) and item["reliability"] > 0
                   for item in evidence):
            return False
    return True


def league_base_rate_context(candidate, analysis, weighted_support, minimum_support):
    """Describe a real rate-derived heuristic shortfall, keeping all samples."""
    if weighted_support >= minimum_support or not _league_history_supported(candidate, analysis):
        return None
    basis = {key: analysis[key] for key in ("candidate_id", "status", "period", "history_league_id")}
    basis["profiles"] = {side: {window: {"sample_size": analysis["profiles"][side][window]["sample_size"]}
                                for window in ("historical_20", "venue_10")}
                         for side in ("home", "away")}
    return {
        "gates": ["LEAGUE_BASE_RATE"], "weighted_support": weighted_support,
        "minimum_weighted_support": minimum_support, "history_basis": basis,
        "basis_kind": "DESCRIPTIVE_TEAM_RATES_WITHIN_CURRENT_LEAGUE_NOT_CALIBRATED_PROBABILITY",
        "hard_guards_preserved": ["QUOTE_AND_FIXTURE_IDENTITY", "EVENT_AND_EVIDENCE_TIMESTAMPS",
                                  "HISTORICAL_AND_VENUE_MINIMUM_SAMPLES", "DAILY_223", "READY_EXPOSURE_CAP"],
    }


def veto_candidate_signature(candidate: Mapping) -> str:
    """Bind attached proof to its exact prospective fixture/product/price."""
    identity = {key: candidate.get(key) for key in (
        "candidate_id", "fixture_id", "market_key", "selection_key", "period",
        "bookmaker", "region", "currency", "decimal_price", "quote_source_id",
        "kickoff",
    )}
    identity["quoted_at"] = candidate.get("quote_timestamp_raw", candidate.get("quoted_at"))
    if candidate.get('quote_provider') == 'the_stats_api':
        identity['quote_observed_at'] = candidate.get('quote_observed_at')
        identity['quote_source_sha256'] = candidate.get('quote_source_sha256')
    return canonical_sha256(identity)


def _strings(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty) and len(value) <= 12
            and all(isinstance(item, str) and item.strip() and len(item) <= 2500
                    for item in value))


def _source_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def verified_veto_override(candidate, review=None, now=None) -> dict | None:
    """Require the actual reviewed candidate, explicit approval and cited sources.

    A free-standing candidate boolean or display annotation grants no authority.
    Attached reviews must also match the current quote snapshot. This validates
    the reviewed exception's provenance; upstream validators still own event,
    identity, minimum samples, probability/edge, exposure and DAILY protection.
    """
    now = now or datetime.now(UTC)
    if not isinstance(candidate, Mapping) or candidate.get("profile_id") == "daily223":
        return None
    context = candidate.get("veto_override_context")
    if not isinstance(context, Mapping) or not _strings(context.get("gates"), nonempty=True):
        return None
    if not set(context["gates"]) <= ALLOWED_GATES:
        return None
    attached = review is None
    envelope = candidate.get("astra_veto_review") if attached else review
    if not isinstance(envelope, Mapping) or envelope.get("status") != "COMPLETE":
        return None
    if attached and envelope.get("candidate_snapshot_sha256") != veto_candidate_signature(candidate):
        return None
    try:
        reviewed_at = datetime.fromisoformat(envelope["reviewed_at"])
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            return None
        if now is not None and (now.tzinfo is None or now.utcoffset() is None or reviewed_at > now):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    records = envelope.get("reviews")
    if not isinstance(records, list):
        return None
    matching = [row for row in records if isinstance(row, Mapping)
                and row.get("candidate_id") == candidate.get("candidate_id")]
    if len(matching) != 1 or matching[0].get("assessment") != "support":
        return None
    record = matching[0]
    proof = record.get("veto_override")
    if not isinstance(proof, Mapping) or proof.get("enabled") is not True:
        return None
    gates, urls, identifiers = (proof.get(key) for key in ("gates", "source_urls", "evidence_ids"))
    if (not _strings(gates, nonempty=True) or not set(gates) <= set(context["gates"])
            or not _strings(urls, nonempty=True) or not _strings(identifiers)
            or not isinstance(proof.get("justification"), str)
            or not proof["justification"].strip() or len(proof["justification"]) > 2500):
        return None
    if "LEAGUE_BASE_RATE" in gates and not _league_history_supported(candidate, context.get("history_basis")):
        return None
    cited_urls, cited_evidence = record.get("source_urls"), record.get("evidence_ids")
    if not _strings(cited_urls) or not _strings(cited_evidence):
        return None
    sources = envelope.get("sources", [])
    if not isinstance(sources, list):
        return None
    retrieved = {source.get("url") for source in sources if isinstance(source, Mapping)
                 and _source_url(source.get("url"))}
    evidence = candidate.get("evidence", [])
    explicit_ids = candidate.get("evidence_ids", [])
    if not isinstance(evidence, list) or not isinstance(explicit_ids, list):
        return None
    available_ids = {item.get("evidence_id", item.get("id")) for item in evidence
                     if isinstance(item, Mapping) and isinstance(item.get("evidence_id", item.get("id")), str)}
    available_ids.update(value for value in explicit_ids if isinstance(value, str))
    if (not set(urls) <= set(cited_urls) & retrieved
            or not set(identifiers) <= set(cited_evidence) & available_ids):
        return None
    return {
        "version": VERSION, "candidate_id": candidate.get("candidate_id"), "gates": sorted(set(gates)),
        "justification": proof["justification"].strip(), "source_urls": sorted(set(urls)),
        "evidence_ids": sorted(set(identifiers)), "reviewed_at": reviewed_at.isoformat(),
        "candidate_snapshot_sha256": veto_candidate_signature(candidate),
        "score_is_probability": False,
    }
