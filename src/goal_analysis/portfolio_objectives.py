"""Product objectives and honest pre-settlement capacity diagnostics."""

from __future__ import annotations

import math

from goal_analysis.operating_policy import current_contract
from goal_analysis.portfolio_capacity import classify

VERSION = "arthur-portfolio-objectives-v2-live-paper"
MIN_READY = 20
MAX_READY = 30
MIN_NON_DAILY_PRICE = 10.0
MAX_NON_DAILY_PRICE = 50.0
TARGET_DAILY_WINS = 3
STRETCH_DAILY_WINS = 4
STAKE_EUR = 1.0


def _price(item):
    value = item.get("combined_price")
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def assess_objectives(report):
    """Measure final recommendations without inventing future outcomes."""
    if current_contract(report.get('settings', report.get('policy', {}))):
        return _current_objectives(report)
    capacity = classify(report)
    ready = capacity["live_cards"]
    paper = capacity["paper_cards"]
    non_daily = [item for item in ready if item.get("profile_id") != "daily223"]
    below, above, missing = [], [], []
    for item in non_daily:
        price = _price(item)
        identifier = str(item.get("profile_id", "unknown"))
        if price is None:
            missing.append(identifier)
        elif price < MIN_NON_DAILY_PRICE:
            below.append(identifier)
        elif price > MAX_NON_DAILY_PRICE:
            above.append(identifier)
    count = len(ready)
    return {
        "version": VERSION,
        "ready_recommendations": count,
        "ready_live_recommendations": count,
        "ready_paper_recommendations": len(paper),
        "ready_recommendations_all_scopes": count + len(paper),
        "ready_live_profile_ids": sorted(item["profile_id"] for item in ready),
        "ready_paper_profile_ids": sorted(item["profile_id"] for item in paper),
        "non_ready_recommendations_with_legs": capacity["nonempty_recommendations"] - count - len(paper),
        "capacity_count_scope": capacity["capacity_count_scope"],
        "published_ready_live_tickets": capacity["published_ready_live_tickets"],
        "published_ready_live_ticket_ids": capacity["published_ready_live_ticket_ids"],
        "excluded_nonempty_recommendations": capacity["excluded_nonempty_recommendations"],
        "ambiguous_ticket_ids": capacity["ambiguous_ticket_ids"],
        "paper_contributes_to_live_target": False,
        "readiness_or_publication_rights_changed": False,
        "decision_effect": "NONE",
        "target_ready_range": {"minimum": MIN_READY, "maximum": MAX_READY},
        "capacity_status": ("BELOW_TARGET" if count < MIN_READY else
                            "ABOVE_TARGET" if count > MAX_READY else "IN_TARGET"),
        "capacity_shortfall": max(0, MIN_READY - count),
        "non_daily_target_price_range": {
            "minimum": MIN_NON_DAILY_PRICE, "maximum": MAX_NON_DAILY_PRICE},
        "non_daily_below_price_range": sorted(below),
        "non_daily_above_price_range": sorted(above),
        "non_daily_missing_price": sorted(missing),
        "stake_eur_per_ticket": STAKE_EUR,
        "settlement_kpi": {"target_daily_winners": TARGET_DAILY_WINS,
                           "stretch_daily_winners": STRETCH_DAILY_WINS,
                           "status": "PENDING_SETTLEMENT"},
        "filler_generation_allowed": False,
        "daily223_protected": True,
    }


def _current_objectives(report):
    """The generation target counts canonical DRAFT/PAPER cards separately from LIVE."""
    from goal_analysis.generated_tickets import _fallback

    cards = [card for card in report.get('generated_tickets', [])
             if isinstance(card, dict) and card.get('legs') and card.get('status') in {'DRAFT', 'READY'}]
    live = [card for card in cards if card['status'] == 'READY' and card.get('publication_scope') == 'LIVE']
    paper = [card for card in cards if card['status'] == 'READY' and card.get('publication_scope') != 'LIVE']
    below, above, missing, exceptions = [], [], [], []
    for card in cards:
        if card.get('profile_id') == 'daily223':
            continue
        price, profile = _price(card), card.get('profile_id', 'unknown')
        if price is None:
            missing.append(profile)
        elif 5 <= price < 15 and _fallback(card):
            exceptions.append(profile)
        elif price < 15:
            below.append(profile)
        elif price > 40:
            above.append(profile)
    count = len(cards)
    return {
        'version': 'arthur-portfolio-objectives-v3-generated-canonical',
        'generated_recommendations': count, 'generated_count': count,
        'generated_profile_ids': sorted(card['profile_id'] for card in cards),
        'generated_ticket_ids': sorted(card['ticket_id'] for card in cards),
        'draft_generated_recommendations': sum(card['status'] == 'DRAFT' for card in cards),
        'ready_recommendations': len(live),
        'ready_live_recommendations': len(live), 'ready_paper_recommendations': len(paper),
        'ready_recommendations_all_scopes': len(live) + len(paper),
        'ready_live_profile_ids': sorted(card['profile_id'] for card in live),
        'ready_paper_profile_ids': sorted(card['profile_id'] for card in paper),
        'published_ready_live_tickets': len(live),
        'published_ready_live_ticket_ids': sorted(card['ticket_id'] for card in live),
        'non_ready_recommendations_with_legs': sum(card['status'] == 'DRAFT' for card in cards),
        'capacity_count_scope': 'CANONICAL_GENERATED_TICKETS_ALL_STATUSES_AND_SCOPES',
        'target_generated_range': {'minimum': 15, 'maximum': 30},
        'capacity_status': 'BELOW_TARGET' if count < 15 else 'ABOVE_TARGET' if count > 30 else 'IN_TARGET',
        'capacity_shortfall': max(0, 15 - count),
        'non_daily_target_price_range': {'minimum': 15.0, 'maximum': 40.0},
        'non_daily_below_price_range': sorted(below), 'non_daily_above_price_range': sorted(above),
        'non_daily_missing_price': sorted(missing), 'cautious_odds_exception_profile_ids': sorted(exceptions),
        'excluded_nonempty_recommendations': report.get('generation_capacity', {}).get('excluded_profiles', []),
        'ambiguous_ticket_ids': [], 'paper_contributes_to_generation_target': True,
        'paper_contributes_to_live_target': False, 'readiness_or_publication_rights_changed': False,
        'decision_effect': 'NONE', 'stake_eur_per_ticket': STAKE_EUR,
        'settlement_kpi': {'target_daily_winners': TARGET_DAILY_WINS,
                           'stretch_daily_winners': STRETCH_DAILY_WINS, 'status': 'PENDING_SETTLEMENT'},
        'filler_generation_allowed': False, 'daily223_protected': True,
    }
