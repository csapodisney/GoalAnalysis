from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RejectionCode(StrEnum):
    DUPLICATE_FIXTURE = "duplicate_fixture"
    COMPETITION_NOT_ALLOWED = "competition_not_allowed"
    NOT_ON_TARGET_DATE = "not_on_target_date"
    STATUS_NOT_SCHEDULED = "status_not_scheduled"
    ALREADY_STARTED = "already_started"
    SHORTLIST_CAP = "shortlist_cap"


@dataclass(frozen=True, slots=True)
class Rejection:
    fixture_id: str
    code: RejectionCode
    detail: str
