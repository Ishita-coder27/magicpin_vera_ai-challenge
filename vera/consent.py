"""Customer eligibility: may Vera message this customer, for this purpose,
on the merchant's behalf? Deterministic and conservative."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .views import CustomerView

# Which consent scopes cover which outreach purpose. `reminder_opt_in: true`
# is an explicit opt-in to service reminders, so it covers transactional
# purposes (never promotional ones).
_PURPOSE_SCOPES = {
    "recall": ("recall_reminders", "appointment_reminders", "promotional_offers", "treatment_followup"),
    "appointment": ("appointment_reminders", "recall_reminders", "treatment_followup", "program_updates"),
    "refill": ("refill_reminders", "delivery_notifications"),
    "followup": ("followup", "program_updates", "kids_program_updates", "bridal_package_followup",
                 "treatment_followup", "appointment_reminders"),
    "winback": ("winback_offers", "promotional_offers", "recall_reminders", "renewal_reminders"),
    "promotional": ("promotional_offers",),
}
_TRANSACTIONAL = {"recall", "appointment", "refill", "followup"}


@dataclass
class Eligibility:
    ok: bool
    reason: str
    basis: str = ""


def check(customer: Optional[CustomerView], merchant_id: str, purpose: str) -> Eligibility:
    if customer is None or not customer.raw:
        return Eligibility(False, "customer context not available")
    if customer.merchant_id and merchant_id and customer.merchant_id != merchant_id:
        return Eligibility(False, "customer belongs to a different merchant")
    channel = str(customer.prefs.get("channel", "")).lower()
    if channel in ("none", "none_recorded") or customer.raw.get("identity", {}).get("phone_redacted", "x") is None:
        return Eligibility(False, "no reachable WhatsApp channel on record")
    scopes = customer.consent_scope
    opted_in = bool(customer.consent.get("opted_in_at")) and bool(scopes)
    reminder_opt_in = customer.prefs.get("reminder_opt_in")
    if not opted_in and reminder_opt_in is not True:
        return Eligibility(False, "customer has not opted in to merchant outreach")

    wanted = set(_PURPOSE_SCOPES.get(purpose or "followup", ()))
    for s in scopes:
        # Exact scope names only: "promotional_offers_x" must not satisfy
        # "promotional_offers". "<x>_followup" scopes are the one family match.
        if s in wanted or (purpose == "followup" and s.endswith("_followup")):
            return Eligibility(True, f"consent scope '{s}' covers {purpose}", s)
    if purpose in _TRANSACTIONAL and reminder_opt_in is True:
        return Eligibility(True, f"reminder opt-in covers transactional {purpose}", "reminder_opt_in")
    return Eligibility(False, f"consent scope {scopes or '[]'} does not cover {purpose}")
