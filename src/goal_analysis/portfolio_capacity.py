"""Post-allocation capacity accounting; never changes publication eligibility."""

from __future__ import annotations

import math
from collections import Counter

VERSION = "portfolio-capacity-live-paper-v1"
READY = {"READY", "MODEL_READY"}
ALIAS_FIELDS = (
    "candidate_id", "fixture_id", "market_key", "selection_key", "period",
    "decimal_price", "bookmaker", "region", "currency", "quote_provider",
    "quote_source_id", "quote_source_sha256", "quote_observed_at",
)


def _legs_match(first, second):
    if not first or not second or len(first) != len(second):
        return False
    for a, b in zip(first, second, strict=True):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        price = a.get("decimal_price")
        if (not a.get("candidate_id") or not a.get("fixture_id")
                or type(price) not in (int, float) or not math.isfinite(price) or price <= 1):
            return False
        if any(a.get(field) != b.get(field) for field in ALIAS_FIELDS):
            return False
    return True


def classify(report):
    """Separate ready LIVE/PAPER recommendations and actual LIVE tickets.

    REVIEWED is an alias, not a new readiness grant: its exact saved ticket
    must already be READY/LIVE. Ambiguous aliases are excluded, not guessed.
    READY/MODEL_READY cards keep their existing final publication labels.
    """
    cards = [c for c in report.get("recommendations", [])
             if isinstance(c, dict) and c.get("legs")]
    tickets = [t for t in report.get("tickets", []) if isinstance(t, dict)]
    ticket_ids = Counter(t.get("ticket_id") for t in tickets if t.get("ticket_id"))
    published = {t["ticket_id"]: t for t in tickets
                 if t.get("ticket_id") and ticket_ids[t["ticket_id"]] == 1
                 and t.get("status") == "READY" and t.get("publication_scope") == "LIVE"
                 and t.get("legs")}
    profiles = Counter(c.get("profile_id") for c in cards if c.get("profile_id"))
    live, paper, excluded = [], [], []
    for index, card in enumerate(cards):
        profile = card.get("profile_id")
        scope = card.get("publication_scope")
        status = card.get("status")
        reason = None
        if not profile or profiles[profile] != 1:
            reason = "MISSING_OR_DUPLICATE_PROFILE"
        elif scope not in {"LIVE", "PAPER_AUDITION"}:
            reason = "UNKNOWN_OR_NONPUBLISHABLE_SCOPE"
        elif status == "REVIEWED":
            ticket = published.get(card.get("portfolio_ticket_id"))
            if (scope != "LIVE" or not card.get("selected_for_portfolio") or not ticket
                    or ticket.get("profile_id") != profile
                    or not _legs_match(card["legs"], ticket["legs"])):
                reason = "REVIEWED_WITHOUT_EXACT_READY_LIVE_TICKET"
        elif status not in READY:
            reason = "NOT_READY"
        if reason:
            excluded.append({"profile_id": profile, "status": status,
                             "publication_scope": scope or "UNKNOWN",
                             "reason": reason, "nonempty_card_index": index})
        elif scope == "LIVE":
            live.append(card)
        else:
            paper.append(card)
    return {
        "version": VERSION, "decision_effect": "NONE",
        "capacity_count_scope": "LIVE_READY_RECOMMENDATIONS_ONLY",
        "live_cards": live, "paper_cards": paper,
        "nonempty_recommendations": len(cards),
        "published_ready_live_tickets": len(published),
        "published_ready_live_ticket_ids": sorted(published),
        "ambiguous_ticket_ids": sorted(k for k, n in ticket_ids.items() if n > 1),
        "excluded_nonempty_recommendations": excluded,
        "paper_contributes_to_live_target": False,
        "readiness_or_publication_rights_changed": False,
    }
