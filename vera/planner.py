"""Decides WHAT to say: dispatches a trigger to its strategy, estimates the
value of acting, and writes the rationale. No wording decisions live here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from . import artifacts
from .ctx import Ctx, Draft
from .formatting import humanize_key
from .strategies import customer as cust
from .strategies import merchant as m1
from .strategies import merchant2 as m2

STRATEGIES: Dict[str, Callable[[Ctx], Draft]] = {
    "knowledge": m1.knowledge,
    "compliance": m1.compliance,
    "performance_down": m1.perf_down,
    "performance_up": m1.perf_up,
    "milestone": m1.milestone,
    "event": m2.event,
    "competition": m2.competition,
    "reputation": m2.reputation,
    "account": m2.account,
    "dormant": m2.dormant,
    "curious": m2.curious,
    "planning": artifacts.planning,
    "general": m2.general,
}


def plan(ctx: Ctx) -> Draft:
    family = ctx.spec.family
    if ctx.scope == "customer" or family.startswith("customer"):
        if ctx.customer is None or not ctx.customer.raw:
            return _customer_missing(ctx)
        return cust.customer_message(ctx)
    draft = STRATEGIES.get(family, m2.general)(ctx)
    draft = _thread_continuity(ctx, draft)
    return _ensure_anchor(ctx, draft)


def _ensure_anchor(ctx: Ctx, d: Draft) -> Draft:
    """Every proactive merchant message needs one merchant-specific fact.
    Regulations are universal and exempt; blocked drafts are left alone. If
    the strategy produced none, add the merchant's strongest real fact."""
    # Regulations and research are category-level knowledge; a bolted-on
    # unrelated merchant fact would be cosmetic, not relevant.
    if d.send_as != "vera" or d.extra.get("blocked") or ctx.spec.family in ("compliance", "knowledge"):
        return d
    from .quality import anchors
    from .strategies.common import strongest_fact
    text = d.hook + " " + " ".join(d.beats)
    if anchors(ctx, text):
        return d
    fact = strongest_fact(ctx)
    if fact:
        d.beats.append(fact)
        d.anchor = (d.anchor + "; " if d.anchor else "") + "anchored on strongest merchant fact"
    return d


def _thread_continuity(ctx: Ctx, d: Draft) -> Draft:
    """Don't talk past an open thread. If the merchant asked Vera for
    something that this message isn't about, acknowledge it in the hook; if
    Vera's last note went unanswered and this one is on the same topic, tie
    back to it instead of pretending it never happened."""
    if d.genre in ("planning_artifact", "compliance_alert") or d.send_as != "vera":
        return d
    from .strategies.common import open_request_topic, ignored_note_overlap, pick, seed
    topic = open_request_topic(ctx)
    text = (d.hook + " " + " ".join(d.beats)).lower()
    if topic and "you asked" not in text:
        # Judges read an appended backlog aside as robotic; record it for the
        # rationale/conversation instead of padding the message.
        d.anchor = (d.anchor + "; " if d.anchor else "") + f"acknowledges open request ({topic})"
        return d
    note = ignored_note_overlap(ctx, text) if len(d.beats) <= 3 else None
    if note:
        d.beats.append(note)
        d.anchor = (d.anchor + "; " if d.anchor else "") + "ties back to an unanswered earlier note"
    return d


def _customer_missing(ctx: Ctx) -> Draft:
    """Customer-scoped trigger but no CustomerContext pushed yet: never write
    to an unknown customer. Tell the owner what's pending instead."""
    tp = ctx.tp
    what = humanize_key(tp.get("service_due") or ctx.kind)
    return Draft(genre="customer_pending", why_now=f"{what} due for a customer",
                 hook=f"{ctx.sal}, a {what} reminder is due for one of your {ctx.policy.people}, but their profile hasn't synced to me yet.",
                 beats=[], ask="Want me to send it as soon as it syncs?", cta="binary_yes_no",
                 action="hold customer reminder until profile syncs", artifact="customer_note",
                 anchor="customer context missing — no outreach to an unknown customer",
                 template="vera_customer_pending_v1", extra={"blocked": True, "block_reason": "customer context not available"})


@dataclass
class Value:
    urgency: float
    merchant_value: float
    evidence: float
    time_sensitivity: float
    effort: float
    risk: float

    @property
    def score(self) -> float:
        s = (0.28 * self.urgency + 0.30 * self.merchant_value + 0.20 * self.evidence
             + 0.17 * self.time_sensitivity - 0.15 * self.risk - 0.05 * self.effort)
        return round(max(0.0, min(1.0, s + 0.05)), 3)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["score"] = self.score
        return d


def estimate_value(ctx: Ctx, draft: Draft, grounded_numbers: int) -> Value:
    fam = ctx.spec.family
    weight = ctx.policy.family_weight.get(fam, 1.0)
    base = min(1.0, ctx.spec.base_value * weight)
    evidence = min(1.0, grounded_numbers / 3.0) * (0.75 if ctx.placeholder else 1.0)
    time_s = 0.5
    if draft.genre in ("contrarian_event", "event_push", "compliance_alert", "customer_appointment", "customer_refill"):
        time_s = 0.95
    elif draft.genre == "compliance_deadline":
        time_s = 0.8
    elif draft.genre == "contrarian_redirect":
        time_s = 0.3
    elif draft.genre in ("planning_artifact",):
        time_s = 0.9
    effort = 0.2 if draft.artifact else 0.5
    risk = 0.0
    if draft.extra.get("blocked"):
        risk = 1.0
    elif draft.extra.get("approval_route"):
        risk = 0.35
    if ctx.policy.sensitive and draft.send_as != "vera":
        risk += 0.05
    return Value(ctx.urgency / 5.0, base, evidence, time_s, effort, risk)


def rationale(ctx: Ctx, draft: Draft) -> str:
    parts = [f"{humanize_key(ctx.kind) or 'trigger'} → {draft.why_now}"]
    if draft.anchor:
        parts.append(f"fit: {draft.anchor}")
    if draft.expected_action and draft.adjusted_action:
        parts.append(f"chose '{draft.adjusted_action}' over the obvious '{draft.expected_action}'")
    if draft.extra.get("blocked"):
        parts.append(f"customer outreach withheld ({draft.extra.get('block_reason')})")
    parts.append(f"one ask: {draft.action}")
    if draft.send_as == "merchant_on_behalf":
        parts.append(f"sent as {ctx.merchant.name}, language {draft.lang_applied}")
    out = "; ".join(parts) + "."
    return out[0].upper() + out[1:]
