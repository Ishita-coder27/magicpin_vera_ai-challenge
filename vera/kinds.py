"""Trigger-kind registry.

Known kinds map to an opportunity family (what kind of value this is) and a
default CTA shape. Unseen kinds — the judge injects new ones — are inferred from
the kind string and payload keys, so the engine never needs a code change to
handle a new trigger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Families, merchant-facing:
#   knowledge, compliance, performance_down, performance_up, milestone, event,
#   competition, reputation, account, dormant, curious, planning
# Families, customer-facing:
#   customer_service (recall/refill/appointment/trial), customer_winback


@dataclass(frozen=True)
class KindSpec:
    family: str
    cta: str                 # default CTA shape
    base_value: float        # intrinsic merchant value of this family (0-1)
    consent_purpose: str = ""  # customer-facing only: what consent must cover
    fits: Tuple[str, ...] = ()  # categories where the kind is native ("" = all)


KINDS: Dict[str, KindSpec] = {
    "research_digest": KindSpec("knowledge", "open_ended", 0.55),
    "category_research_digest_release": KindSpec("knowledge", "open_ended", 0.55),
    "cde_opportunity": KindSpec("knowledge", "binary_yes_no", 0.4),
    "category_trend_movement": KindSpec("knowledge", "binary_yes_no", 0.5),
    "regulation_change": KindSpec("compliance", "binary_yes_no", 0.85),
    "supply_alert": KindSpec("compliance", "binary_yes_no", 0.95),
    "perf_dip": KindSpec("performance_down", "binary_yes_no", 0.75),
    "seasonal_perf_dip": KindSpec("performance_down", "binary_yes_no", 0.6),
    "perf_spike": KindSpec("performance_up", "binary_yes_no", 0.55),
    "milestone_reached": KindSpec("milestone", "binary_yes_no", 0.45),
    "festival_upcoming": KindSpec("event", "binary_yes_no", 0.5),
    "ipl_match_today": KindSpec("event", "binary_yes_no", 0.65),
    "weather_heatwave": KindSpec("event", "binary_yes_no", 0.6),
    "local_news_event": KindSpec("event", "binary_yes_no", 0.5),
    "category_seasonal": KindSpec("event", "binary_yes_no", 0.55),
    "competitor_opened": KindSpec("competition", "binary_yes_no", 0.6),
    "review_theme_emerged": KindSpec("reputation", "binary_yes_no", 0.7),
    "renewal_due": KindSpec("account", "binary_yes_no", 0.7),
    "winback_eligible": KindSpec("account", "binary_yes_no", 0.55),
    "gbp_unverified": KindSpec("account", "binary_yes_no", 0.65),
    "dormant_with_vera": KindSpec("dormant", "open_ended", 0.4),
    "curious_ask_due": KindSpec("curious", "open_ended", 0.4),
    "scheduled_recurring": KindSpec("curious", "open_ended", 0.35),
    "active_planning_intent": KindSpec("planning", "binary_yes_no", 0.9),
    # customer-facing
    "recall_due": KindSpec("customer_service", "multi_choice_slot", 0.7, "recall",
                           ("dentists", "salons", "gyms")),
    "appointment_tomorrow": KindSpec("customer_service", "binary_confirm_cancel", 0.75, "appointment", ()),
    "chronic_refill_due": KindSpec("customer_service", "binary_confirm_cancel", 0.8, "refill", ("pharmacies",)),
    "trial_followup": KindSpec("customer_service", "binary_yes_no", 0.6, "followup", ("gyms", "salons")),
    "wedding_package_followup": KindSpec("customer_service", "binary_yes_no", 0.6, "followup", ("salons",)),
    "unplanned_slot_open": KindSpec("customer_service", "multi_choice_slot", 0.5, "promotional", ()),
    "customer_lapsed_soft": KindSpec("customer_winback", "binary_yes_no", 0.55, "winback", ()),
    "customer_lapsed_hard": KindSpec("customer_winback", "binary_yes_no", 0.55, "winback", ()),
}

_INFER_RULES = [
    (r"refill", "customer_service", "binary_confirm_cancel", "refill"),
    (r"appointment|booking|reminder", "customer_service", "binary_confirm_cancel", "appointment"),
    (r"recall", "customer_service", "multi_choice_slot", "recall"),
    (r"lapse|winback|churn|win_back", "customer_winback", "binary_yes_no", "winback"),
    (r"trial|followup|follow_up", "customer_service", "binary_yes_no", "followup"),
    (r"regulat|complian|circular|licen[cs]e|audit|recall_alert|supply|alert", "compliance", "binary_yes_no", ""),
    (r"research|digest|study|journal|cde|webinar|trend", "knowledge", "open_ended", ""),
    (r"dip|drop|decline|fall", "performance_down", "binary_yes_no", ""),
    (r"spike|surge|jump|growth", "performance_up", "binary_yes_no", ""),
    (r"milestone|anniversary|crossed", "milestone", "binary_yes_no", ""),
    (r"festival|holiday|match|ipl|weather|heat|rain|monsoon|news|event|season", "event", "binary_yes_no", ""),
    (r"competitor|rival|opened", "competition", "binary_yes_no", ""),
    (r"review|rating|complaint", "reputation", "binary_yes_no", ""),
    (r"renew|subscription|expir|verif|gbp|profile", "account", "binary_yes_no", ""),
    (r"dormant|inactive|silent", "dormant", "open_ended", ""),
    (r"curious|ask|question|cadence|recurring", "curious", "open_ended", ""),
    (r"plan|intent|draft", "planning", "binary_yes_no", ""),
]


def spec_for(kind: str, scope: str = "merchant", payload: Optional[dict] = None) -> KindSpec:
    kind = (kind or "").strip().lower()
    if kind in KINDS:
        return KINDS[kind]
    payload = payload or {}
    for pattern, family, cta, purpose in _INFER_RULES:
        if re.search(pattern, kind):
            is_customer_family = family.startswith("customer")
            if is_customer_family != (scope == "customer"):
                continue
            return KindSpec(family, cta, 0.5, purpose)
    # Fall back on payload shape.
    if scope == "customer":
        return KindSpec("customer_service", "binary_yes_no", 0.5, "followup")
    if "top_item_id" in payload or "digest_item_id" in payload:
        return KindSpec("knowledge", "open_ended", 0.5)
    if "metric" in payload and "delta_pct" in payload:
        fam = "performance_down" if (payload.get("delta_pct") or 0) < 0 else "performance_up"
        return KindSpec(fam, "binary_yes_no", 0.55)
    if any(k in payload for k in ("date", "festival", "event", "match")):
        return KindSpec("event", "binary_yes_no", 0.45)
    return KindSpec("general", "open_ended", 0.35)


def kind_fits_category(kind: str, category_slug: str, scope: str = "customer",
                       payload: Optional[dict] = None) -> bool:
    spec = spec_for(kind, scope, payload)
    return not spec.fits or category_slug in spec.fits


def is_placeholder(payload: Optional[dict]) -> bool:
    """Generated triggers carry `{"placeholder": true}` and nothing usable."""
    if not isinstance(payload, dict) or not payload:
        return True
    meaningful = {k: v for k, v in payload.items() if k not in ("placeholder", "metric_or_topic", "category")}
    return bool(payload.get("placeholder")) or not meaningful
