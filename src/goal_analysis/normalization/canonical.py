"""Versioned canonical contracts, additive to the protected legacy adapters.

Names are attributes, never identity evidence. Unknown CSV timezones remain
unknown; only aware timestamps can enter the UTC kickoff field.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import ClassVar

SCHEMA_VERSION = 1
KINDS = frozenset({"competition", "season", "team", "player", "fixture",
                   "market", "outcome", "bookmaker"})


def text(value, field="value"):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a nonempty, unpadded string")
    return value


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must have an explicit timezone")
    return value.astimezone(UTC)


def canonical_id(kind, stable_key):
    if kind not in KINDS:
        raise ValueError("unknown entity kind")
    seed = json.dumps([kind, text(stable_key, "stable_key")], ensure_ascii=False,
                      separators=(",", ":"))
    return f"arthur:{kind}:" + hashlib.sha256(seed.encode()).hexdigest()


def check_id(value, kind):
    if not isinstance(value, str) or not re.fullmatch(rf"arthur:{kind}:[0-9a-f]{{64}}", value):
        raise ValueError(f"invalid {kind} identity")


@dataclass(frozen=True, slots=True, kw_only=True)
class Entity:
    entity_id: str
    KIND: ClassVar[str]
    LINKS: ClassVar[tuple] = ()
    OPTIONAL_LINKS: ClassVar[frozenset] = frozenset()

    def __post_init__(self):
        check_id(self.entity_id, self.KIND)
        for field, kind in self.LINKS:
            value = getattr(self, field)
            if value is not None or field not in self.OPTIONAL_LINKS:
                check_id(value, kind)

    def to_record(self):
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = utc(value).isoformat()
        return {"schema_version": SCHEMA_VERSION, "kind": self.KIND, "data": data}


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalCompetition(Entity):
    KIND: ClassVar[str] = "competition"
    name: str
    country: str | None = None

    def __post_init__(self):
        Entity.__post_init__(self)
        text(self.name, "name")
        if self.country is not None:
            text(self.country, "country")


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalSeason(Entity):
    KIND: ClassVar[str] = "season"
    LINKS: ClassVar[tuple] = (("competition_id", "competition"),)
    competition_id: str
    label: str

    def __post_init__(self):
        Entity.__post_init__(self)
        text(self.label, "label")


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalTeam(Entity):
    KIND: ClassVar[str] = "team"
    name: str

    def __post_init__(self):
        Entity.__post_init__(self)
        text(self.name, "name")


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalPlayer(CanonicalTeam):
    KIND: ClassVar[str] = "player"


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalFixture(Entity):
    KIND: ClassVar[str] = "fixture"
    LINKS: ClassVar[tuple] = (("competition_id", "competition"), ("season_id", "season"),
                             ("home_team_id", "team"), ("away_team_id", "team"))
    competition_id: str
    season_id: str
    home_team_id: str
    away_team_id: str
    kickoff: datetime | None
    status: str
    match_date: str | None = None
    kickoff_local_time: str | None = None

    def __post_init__(self):
        Entity.__post_init__(self)
        if self.home_team_id == self.away_team_id:
            raise ValueError("fixture teams must differ")
        if self.status not in {"SCHEDULED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED",
                               "SUSPENDED", "ABANDONED", "UNKNOWN"}:
            raise ValueError("unknown fixture status")
        if self.kickoff is not None:
            object.__setattr__(self, "kickoff", utc(self.kickoff))
        if self.match_date is not None:
            if date.fromisoformat(self.match_date).isoformat() != self.match_date:
                raise ValueError("invalid match date")
        if self.kickoff_local_time is not None:
            value = time.fromisoformat(self.kickoff_local_time)
            if value.tzinfo is not None or self.match_date is None:
                raise ValueError("local kickoff requires a date and remains timezone-unknown")
        if self.kickoff is None and self.match_date is None:
            raise ValueError("fixture requires a known kickoff or explicit local date")


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalMarket(Entity):
    KIND: ClassVar[str] = "market"
    LINKS: ClassVar[tuple] = (("fixture_id", "fixture"), ("team_id", "team"),
                             ("player_id", "player"))
    OPTIONAL_LINKS: ClassVar[frozenset] = frozenset({"team_id", "player_id"})
    fixture_id: str
    market_key: str
    period: str
    settlement: str
    line: str | None = None
    team_id: str | None = None
    player_id: str | None = None

    def __post_init__(self):
        Entity.__post_init__(self)
        for key in ("market_key", "period", "settlement"):
            text(getattr(self, key), key)
        if self.line is not None:
            try:
                if not isinstance(self.line, str) or not Decimal(self.line).is_finite():
                    raise ValueError("line must be a finite decimal string")
                # Fixed-point formatting preserves every source digit;
                # Decimal.normalize() would round with the ambient context.
                value = format(Decimal(self.line), "f")
                if "." in value:
                    value = value.rstrip("0").rstrip(".")
                object.__setattr__(self, "line", "0" if Decimal(value) == 0 else value)
            except InvalidOperation as error:
                raise ValueError("invalid market line") from error


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalOutcome(Entity):
    KIND: ClassVar[str] = "outcome"
    LINKS: ClassVar[tuple] = (("market_id", "market"),)
    market_id: str
    selection_key: str

    def __post_init__(self):
        Entity.__post_init__(self)
        text(self.selection_key, "selection_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalBookmaker(CanonicalTeam):
    KIND: ClassVar[str] = "bookmaker"


ENTITY_TYPES = {cls.KIND: cls for cls in (CanonicalCompetition, CanonicalSeason, CanonicalTeam,
    CanonicalPlayer, CanonicalFixture, CanonicalMarket, CanonicalOutcome, CanonicalBookmaker)}


def entity_from_record(record):
    if (not isinstance(record, dict) or set(record) != {"schema_version", "kind", "data"}
            or type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION
            or not isinstance(record["kind"], str) or record["kind"] not in ENTITY_TYPES
            or not isinstance(record["data"], dict)):
        raise ValueError("invalid canonical entity record")
    cls = ENTITY_TYPES[record["kind"]]
    if set(record["data"]) - {field.name for field in fields(cls)}:
        raise ValueError("unknown entity fields")
    try:
        return cls(**record["data"])
    except TypeError as error:
        raise ValueError("missing or invalid entity fields") from error


def validate_graph(entities):
    """Reject dangling/wrong-kind links and contradictory copies before storage."""
    indexed = {}
    for entity in entities:
        if not isinstance(entity, Entity):
            raise ValueError("expected canonical entity")
        previous = indexed.get(entity.entity_id)
        if previous is not None and previous != entity:
            raise ValueError("conflicting entity versions in one graph")
        indexed[entity.entity_id] = entity
    for entity in indexed.values():
        for field, kind in entity.LINKS:
            value = getattr(entity, field)
            if value is not None and (value not in indexed or indexed[value].KIND != kind):
                raise ValueError("unresolved entity link: " + field)
        if isinstance(entity, CanonicalFixture):
            if indexed[entity.season_id].competition_id != entity.competition_id:
                raise ValueError("fixture season/competition mismatch")
    return indexed
