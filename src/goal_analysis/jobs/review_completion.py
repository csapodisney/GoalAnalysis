"""Materialize exact final review completion without rebuilding selections."""

from goal_analysis.engine.recommendations import VERSION as RECOMMENDATION_VERSION

MISSING_REVIEW_WARNING = "Astra még nem értékelte az összes választást."
RETAINED_DRAFT_WARNING = (
    "A pontos értékelés elkészült; az ajánlat eredeti konstrukciós vagy publikációs állapota megmaradt."
)


def complete_card(card, review):
    """Consume the already validated whole-card binding; never create READY.

    Called only after profile_context_review.apply has checked the card digest
    and attached its exact source/context-bound answers. Independent model cards
    retain their status, confidence, probability and rank. A classic constructed
    recommendation may finish its original review-only DRAFT transition.
    """
    binding = card.get("profile_context_review", {})
    if binding.get("status") != "SOURCE_BOUND" or not card.get("legs"):
        return
    rows = binding.get("legs", [])
    if len(rows) != len(card["legs"]) or any(
        row.get("status") != "EXACT_PROFILE_CONTEXT_REVIEW_BOUND"
        for row in rows
    ):
        return
    card["review_completion"] = {
        "status": "COMPLETE_EXACT_SOURCE_CONTEXT_BOUND",
        "reviewed_legs": len(rows),
        "unknown_categories": sorted({category for row in rows
                                      for category in row.get("unknown_categories", [])}),
        "publication_eligibility": "REQUIRES_EXISTING_FINAL_CALLER_GUARDS",
        "automatically_made_playable": False,
    }
    warnings = card.get("quality_warnings", [])
    had_missing = MISSING_REVIEW_WARNING in warnings
    card["quality_warnings"] = [warning for warning in warnings
                                if warning != MISSING_REVIEW_WARNING]
    # This is the same classic recommendation state transition as its builder,
    # applied to the final exact answer set. It cannot grant publication, change
    # a model card, override construction, or replace an absent executable price.
    classic = card.get("strategy_version") == RECOMMENDATION_VERSION
    if (classic and had_missing and card.get("status") == "DRAFT"
            and card.get("construction_verified") is True
            and card.get("odds_pending") is False):
        card["status"] = "REVIEWED"
        answers = [leg.get("astra_review", {}) for leg in card["legs"]]
        confidence = "LOW" if any(
            leg.get("missing_support") or answer.get("assessment") == "caution"
            for leg, answer in zip(card["legs"], answers, strict=True)
        ) else "MEDIUM"
        if (confidence == "MEDIUM" and review.get("research_status") == "COMPLETE_WITH_SOURCES"
                and all(answer.get("assessment") == "support" for answer in answers)):
            confidence = "HIGH"
        card["confidence"] = confidence
    elif had_missing and card.get("status") == "DRAFT":
        if RETAINED_DRAFT_WARNING not in card["quality_warnings"]:
            card["quality_warnings"].append(RETAINED_DRAFT_WARNING)
