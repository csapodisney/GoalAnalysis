"""Per-field, point-in-time source priority. New selections remain SHADOW."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

from .canonical import KINDS, check_id, text, utc

VERSION = "source-priority-v2"
PRIORITIES = {
    "sports": ("the_stats_api", "api_football", "football_data_co_uk"),
    "odds": ("the_stats_api", "the_odds_api", "api_football"),
    "weather": ("open_meteo",),
    "news": ("grok",),
}


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("finite JSON data required") from error


@dataclass(frozen=True, slots=True)
class Observation:
    entity_id: str
    field: str
    provider: str
    value_json: str
    observed_at: datetime
    available_at: datetime
    source_url: str
    evidence_sha256: str
    identity_verified: bool
    quoted_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.entity_id, str) or len(self.entity_id.split(":")) != 3:
            raise ValueError("canonical observation identity required")
        kind = self.entity_id.split(":")[1]
        if kind not in KINDS:
            raise ValueError("unknown observation identity kind")
        check_id(self.entity_id, kind)
        text(self.field, "field")
        text(self.provider, "provider")
        if type(self.identity_verified) is not bool:
            raise ValueError("explicit identity verification state required")
        parsed = urlsplit(text(self.source_url, "source_url"))
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
                or any(re.search(r"api.?key|token|secret|password|authorization", key, re.IGNORECASE)
                       for key, _ in parse_qsl(parsed.query))):
            raise ValueError("credential-free evidence URL required")
        if not isinstance(self.evidence_sha256, str) or not re.fullmatch("[0-9a-f]{64}", self.evidence_sha256):
            raise ValueError("evidence SHA-256 required")
        for field in ("observed_at", "available_at", "quoted_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, utc(value))
            elif field != "quoted_at":
                raise ValueError("observation timestamps required")
        # Canonical JSON is immutable; never retain caller-owned nested objects.
        object.__setattr__(self, "value_json", _json(json.loads(self.value_json)))

    @classmethod
    def from_value(cls, entity_id, field, provider, value, observed_at, available_at,
                   source_url, evidence_sha256, identity_verified, quoted_at=None):
        return cls(entity_id, field, provider, _json(value), observed_at, available_at,
                   source_url, evidence_sha256, identity_verified, quoted_at)

    def to_record(self):
        record = {"entity_id": self.entity_id, "field": self.field, "provider": self.provider,
                "value": json.loads(self.value_json), "observed_at": self.observed_at.isoformat(),
                "available_at": self.available_at.isoformat(), "source_url": self.source_url,
                "evidence_sha256": self.evidence_sha256,
                "identity_verified": self.identity_verified,
                "quoted_at": self.quoted_at.isoformat() if self.quoted_at else None}
        record["observation_id"] = hashlib.sha256(_json(record).encode()).hexdigest()
        return record


def _effective_time(item, domain):
    return item.quoted_at if domain == "odds" else item.observed_at


def _explain(result, evaluated, *, domain, cutoff, max_age):
    """Retain conflicting evidence and explain order without changing selection."""
    order, latest = [], {}
    chosen = (result["selected"] or {}).get("observation_id")
    for item, reason in evaluated:
        record = item.to_record()
        moment = _effective_time(item, domain)
        priority = PRIORITIES[domain]
        order.append({"observation_id": record["observation_id"], "provider": item.provider,
            "effective_at": moment.isoformat() if moment else None,
            "observed_at": record["observed_at"], "available_at": record["available_at"],
            "age_seconds": (cutoff - moment).total_seconds() if moment else None,
            "eligible": reason is None, "rejection_reason": reason,
            "source_priority": priority.index(item.provider) + 1 if item.provider in priority else None,
            "selected": record["observation_id"] == chosen})
        if reason is None:
            previous = latest.get(item.provider, [])
            if not previous or moment > _effective_time(previous[0], domain):
                latest[item.provider] = [item]
            elif moment == _effective_time(previous[0], domain):
                latest[item.provider].append(item)
    # Stable identity breaks timestamp ties; an absent quote time sorts last.
    order.sort(key=lambda row: row["observation_id"])
    order.sort(key=lambda row: (row["effective_at"] is not None, row["effective_at"] or ""),
               reverse=True)
    for rank, row in enumerate(order, 1):
        row["freshness_rank"] = rank
    current = [item for items in latest.values() for item in items]
    values = {item.value_json for item in current}
    result["freshness_order"] = order
    result["freshness_limit_seconds"] = max_age.total_seconds()
    result["conflict"] = {
        "status": "CONFLICT" if len(values) > 1 else "AGREEMENT" if len(latest) > 1 else
                  "SINGLE_SOURCE" if latest else "NO_ELIGIBLE_SOURCE",
        "distinct_values": len(values), "provider_count": len(latest),
        "observation_ids": sorted({item.to_record()["observation_id"] for item in current})}
    return result


def resolve_field(observations, *, domain, as_of, max_age=timedelta(minutes=30), mode="SHADOW"):
    """Fresh primary wins; fallback is eligible only when primary is unavailable.

    This does not merge probabilities or alter any betting/ranking threshold.
    Receipt time is never a replacement for an unknown quote timestamp.
    """
    if mode != "SHADOW" or domain not in PRIORITIES:
        raise ValueError("known source domain and SHADOW mode required")
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise ValueError("positive freshness limit required")
    cutoff = utc(as_of)
    items = tuple(observations)
    if any(not isinstance(item, Observation) for item in items):
        raise ValueError("typed source observations required")
    if len({(item.entity_id, item.field) for item in items}) > 1:
        raise ValueError("observations must refer to the same entity and field")
    eligible, rejected, evaluated = [], [], []
    for item in items:
        reason = None
        value = json.loads(item.value_json)
        if item.provider not in PRIORITIES[domain]:
            reason = "UNSUPPORTED_SOURCE"
        elif not item.identity_verified:
            reason = "MISSING_LINK"
        elif value is None:
            reason = "MISSING_VALUE"
        elif item.observed_at > cutoff or item.available_at > cutoff:
            reason = "FUTURE_OBSERVATION"
        elif item.observed_at > item.available_at:
            reason = "INVALID_OBSERVATION_ORDER"
        elif cutoff - item.observed_at > max_age:
            reason = "STALE_OBSERVATION"
        elif item.quoted_at is not None and item.quoted_at > min(cutoff, item.observed_at, item.available_at):
            reason = "FUTURE_QUOTE"
        elif item.quoted_at is not None and cutoff - item.quoted_at > max_age:
            reason = "STALE_QUOTE"
        elif domain == "odds" and item.quoted_at is None:
            reason = "MISSING_QUOTE_TIME"
        elif domain == "odds" and (type(value) not in (int, float) or value <= 1):
            reason = "INVALID_PRICE"
        if reason:
            rejected.append({"provider": item.provider, "evidence_sha256": item.evidence_sha256,
                             "observation_id": item.to_record()["observation_id"], "reason": reason})
        else:
            eligible.append(item)
        evaluated.append((item, reason))
    result = {"version": VERSION, "mode": mode, "status": "MISSING", "selected": None,
              "resolution_reason": "NO_ELIGIBLE_OBSERVATION", "rejected": rejected}
    if not eligible:
        return _explain(result, evaluated, domain=domain, cutoff=cutoff, max_age=max_age)
    priority = PRIORITIES[domain]
    preferred = min(priority.index(item.provider) for item in eligible)
    pool = [item for item in eligible if priority.index(item.provider) == preferred]
    # A later API receipt can contain an older bookmaker quote. Freshness of
    # a price follows its quote time, never the time we happened to fetch it.
    latest = max(_effective_time(item, domain) for item in pool)
    pool = [item for item in pool if _effective_time(item, domain) == latest]
    if len({item.value_json for item in pool}) > 1:
        result["resolution_reason"] = "CONFLICTING_SAME_TIME"
        return _explain(result, evaluated, domain=domain, cutoff=cutoff, max_age=max_age)
    chosen = min(pool, key=lambda item: (item.evidence_sha256, item.source_url,
                                        item.available_at, item.value_json,
                                        item.to_record()["observation_id"]))
    result.update(status="RESOLVED", selected=chosen.to_record(),
                  resolution_reason="PRIMARY" if preferred == 0 else "FALLBACK_PRIMARY_UNAVAILABLE")
    return _explain(result, evaluated, domain=domain, cutoff=cutoff, max_age=max_age)
