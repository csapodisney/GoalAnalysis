"""Transparent unit-scale ranking inputs; scores are never calibrated probabilities.

Raw model probabilities, estimated edges and evidence remain on their own legs.
Only a separate utility is normalized before combining or comparing profiles.
"""

from __future__ import annotations

import math

VERSION = "arthur-unit-support-sqrt-length-v1"


def normalize_score(value: float, scale: float = 1.0) -> float:
    """Use a fixed, declared scale, never the strongest surviving candidate."""
    if (type(value) not in (int, float) or not math.isfinite(value)
            or type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0):
        raise ValueError("Score and positive normalization scale must be finite numbers")
    return min(1.0, max(0.0, float(value) / scale))


def ticket_score(normalized_scores) -> float:
    """Mean unit support times sqrt(distinct legs); eligibility is checked upstream."""
    values = list(normalized_scores)
    if not values:
        return 0.0
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
           for v in values):
        raise ValueError("Ticket ranking requires normalized support in [0, 1]")
    return math.fsum(values) / len(values) * math.sqrt(len(values))


def ticket_quality(raw_scores, *, raw_kind="EVIDENCE_HEURISTIC", scale=1.0) -> dict:
    values = list(raw_scores)
    normalized = [normalize_score(value, scale) for value in values]
    return {
        "version": VERSION, "score_kind": raw_kind,
        "raw_leg_scores": values, "normalization_scale": scale,
        "normalized_leg_scores": normalized,
        "normalized_quality": math.fsum(normalized) / len(normalized) if normalized else 0.0,
        "length_factor": math.sqrt(len(normalized)),
        "ticket_score": ticket_score(normalized),
        "calibrated_probability": False,
    }
