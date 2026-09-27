"""Category intelligence as data.

Adding a vertical should mean adding an entry here (and a CategoryContext from
the judge), not writing new code. The pushed CategoryContext stays the source
of truth for facts (offers, peer stats, digest, taboos); this file only holds
*judgment*: how the vertical weighs opportunities, what it calls its customers,
what Vera can credibly deliver for it, and which framings to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class CategoryPolicy:
    slug: str
    people: str                      # what the merchant calls their customers
    person: str                      # singular
    voice: str                       # short description used in LLM prompt + rationale
    # Opportunity-family multipliers for the signal auction (1.0 = neutral).
    family_weight: Dict[str, float] = field(default_factory=dict)
    # Concrete things Vera can draft for this vertical, by purpose.
    artifact: Dict[str, str] = field(default_factory=dict)
    sensitive: bool = False          # health-adjacent: no outcome claims, no hype
    discount_first_ok: bool = True   # may a message lead with a discount?
    customer_emoji: str = ""         # at most one, customer-facing only
    extra_taboos: List[str] = field(default_factory=list)
    proof_preference: List[str] = field(default_factory=list)


_BASE_ARTIFACTS = {
    "post": "a Google Business post",
    "customer_note": "a short WhatsApp note for your customers",
    "review_reply": "short replies to those reviews",
    "review_ask": "a one-line review request for recent happy customers",
    "offer_setup": "the offer on your listing",
}

POLICIES: Dict[str, CategoryPolicy] = {
    "dentists": CategoryPolicy(
        slug="dentists", people="patients", person="patient",
        voice="peer-clinical, collegial, evidence-first; technical terms welcome; zero hype",
        family_weight={"compliance": 1.35, "knowledge": 1.25, "reputation": 1.05,
                       "event": 0.7, "performance_up": 0.9},
        artifact={**_BASE_ARTIFACTS,
                  "customer_note": "a 3-line patient-education WhatsApp",
                  "knowledge": "a 3-line patient-education WhatsApp for that group",
                  "compliance": "a one-page SOP note for your records",
                  "post": "a Google post"},
        sensitive=True, discount_first_ok=False, customer_emoji="🦷",
        extra_taboos=["cure", "guarantee", "painless", "risk-free"],
        proof_preference=["citation", "trial_n", "cohort_count", "peer_benchmark"],
    ),
    "salons": CategoryPolicy(
        slug="salons", people="clients", person="client",
        voice="warm, practical, fellow-professional; service + price over percentages",
        family_weight={"event": 1.15, "customer_service": 1.1, "reputation": 1.05, "compliance": 0.9},
        artifact={**_BASE_ARTIFACTS,
                  "customer_note": "a WhatsApp note for your regular clients",
                  "knowledge": "a short client-care note you can share",
                  "post": "a Google post with the service and price"},
        customer_emoji="✨",
        proof_preference=["service_price", "trend", "review_quote", "calls"],
    ),
    "restaurants": CategoryPolicy(
        slug="restaurants", people="customers", person="customer",
        voice="operator-to-operator, brisk, numbers-first (covers, AOV, delivery share)",
        family_weight={"event": 1.2, "reputation": 1.15, "performance_down": 1.05, "knowledge": 0.85},
        artifact={**_BASE_ARTIFACTS,
                  "customer_note": "a WhatsApp note for your regulars",
                  "knowledge": "a short note for your team and regulars",
                  "post": "a Google post"},
        customer_emoji="🍽️",
        proof_preference=["orders", "delivery_share", "review_theme", "trend"],
    ),
    "gyms": CategoryPolicy(
        slug="gyms", people="members", person="member",
        voice="coach-to-operator, disciplined, no-shame; retention over acquisition in lulls",
        family_weight={"customer_winback": 1.15, "performance_down": 1.0, "event": 0.95, "knowledge": 0.9},
        artifact={**_BASE_ARTIFACTS,
                  "customer_note": "a WhatsApp note for your members",
                  "knowledge": "a short coach's note for your members",
                  "post": "a Google post"},
        customer_emoji="💪",
        extra_taboos=["shred", "guaranteed results"],
        proof_preference=["member_count", "churn", "trend", "seasonal"],
    ),
    "pharmacies": CategoryPolicy(
        slug="pharmacies", people="customers", person="customer",
        voice="trustworthy, precise, neighbourhood-pharmacist; operational not salesy",
        family_weight={"compliance": 1.5, "customer_service": 1.15, "knowledge": 1.05,
                       "event": 0.85, "performance_up": 0.85},
        artifact={**_BASE_ARTIFACTS,
                  "customer_note": "a short WhatsApp note for your repeat customers",
                  "knowledge": "a short customer advisory you can share at the counter",
                  "compliance": "the customer WhatsApp note plus the replacement steps",
                  "post": "a Google post"},
        sensitive=True, discount_first_ok=False,
        extra_taboos=["cure", "guarantee", "no side effects", "risk-free"],
        proof_preference=["batch", "molecule", "chronic_count", "repeat_rate"],
    ),
}

DEFAULT_POLICY = CategoryPolicy(
    slug="default", people="customers", person="customer",
    voice="warm, practical, peer-to-peer; specific over generic",
    artifact=dict(_BASE_ARTIFACTS),
)

# Words that must never appear in any Vera message regardless of vertical.
GLOBAL_TABOOS = [
    "i hope you're doing well", "i hope you are doing well", "i wanted to reach out",
    "as an ai", "i am here to help you", "leverage", "amazing deal", "act now",
    "limited time only", "100% guaranteed",
]

# What Vera can actually do (brief §3). Used to decline out-of-scope asks
# honestly instead of inventing capabilities.
VERA_CAPABILITIES = [
    "Google Business Profile updates (photos, hours, description, posts)",
    "review replies and review requests",
    "offers and campaigns on magicpin",
    "WhatsApp messages to your opted-in customers",
    "performance insights for your listing",
]


def policy_for(slug: str) -> CategoryPolicy:
    return POLICIES.get(slug, DEFAULT_POLICY)
