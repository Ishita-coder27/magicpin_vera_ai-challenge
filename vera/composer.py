"""compose(): the pure, HTTP-independent entry point.

Pipeline: views -> Ctx (evidence ledger) -> planner (WHAT) -> realizer (HOW)
-> validator -> optional LLM polish (re-validated) -> repair/fallback.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from . import language, llm
from .config import COMPOSER_VERSION, CONFIG
from .ctx import Ctx, Draft
from .planner import estimate_value, plan, rationale
from .realize import realize
from .store import context_hash
from .validate import Validation, preserves_facts, ungrounded_numbers, validate
from .views import CategoryView, CustomerView, MerchantView

log = logging.getLogger("vera.composer")


@dataclass
class Composition:
    body: str
    cta: str
    send_as: str
    suppression_key: str
    rationale: str
    template_name: str
    template_params: List[str]
    draft: Draft
    ctx: Ctx
    value: Dict[str, Any]
    validation: Dict[str, Any]
    audit: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.draft.extra.get("blocked"))

    def public(self) -> dict:
        return {"body": self.body, "cta": self.cta, "send_as": self.send_as,
                "suppression_key": self.suppression_key, "rationale": self.rationale,
                "template_name": self.template_name, "template_params": self.template_params}


def _suppression_key(trigger: dict, merchant_id: str, customer_id: Optional[str], family: str) -> str:
    sk = trigger.get("suppression_key")
    if sk:
        return str(sk)
    return f"{trigger.get('kind', family)}:{merchant_id}:{customer_id or '-'}:{trigger.get('id', '')}"


def _validate(ctx: Ctx, body: str, draft: Draft, prior: Iterable[str]) -> Validation:
    return validate(body, facts=ctx.facts, taboos=ctx.category.taboos + ctx.policy.extra_taboos,
                    audience="customer" if draft.send_as == "merchant_on_behalf" else "merchant",
                    cta=draft.cta, lang_code=draft.lang_applied if draft.send_as != "vera" else ctx.lang.code,
                    prior_bodies=prior, merchant_name=ctx.merchant.name,
                    near_dup=0.92)  # across conversations: a new batch/date is a new message


def _repair(ctx: Ctx, draft: Draft, prior: List[str]):
    """Drop beats that carry the failure (ungrounded number/name, taboo) one at
    a time; last resort is hook + ask only, which is always grounded."""
    beats = list(draft.beats)
    for i in range(len(beats) - 1, -1, -1):
        trial = Draft(**{**draft.__dict__, "beats": beats[:i] + beats[i + 1:]})
        body, tn, tp = realize(ctx, trial)
        v = _validate(ctx, body, trial, prior)
        if v.ok:
            return body, tn, tp, v, trial
        if ungrounded_numbers(beats[i], ctx.facts):
            beats = beats[:i] + beats[i + 1:]
    trial = Draft(**{**draft.__dict__, "beats": []})
    body, tn, tp = realize(ctx, trial)
    return body, tn, tp, _validate(ctx, body, trial, prior), trial


_INLINE_ARTIFACTS = {"review_reply", "review_ask"}      # short enough to show in the first message
# (verification steps and offer set-up go inline only when the same ask was already made)
_CTA_FRAME = re.compile(r"(want me to \w+|shall i \w+|reply yes)", re.I)


def _cta_intent(text: str) -> str:
    """Coarse intent of the final ask: draft / setup / send / book / confirm / other."""
    last = [s for s in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if s]
    t = last[-1].lower() if last else ""
    for intent, pat in (("draft", r"draft|write|prepare"), ("setup", r"set (it )?up|put .* live|switch|relaunch|add"),
                        ("send", r"send|share|pull"), ("book", r"book|hold|slot|1 or 2"), ("confirm", r"confirm|reply yes|queue")):
        if re.search(pat, t):
            return intent
    return "other"


def _cta_frame(text: str) -> str:
    last = [s for s in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if s]
    m = _CTA_FRAME.search(last[-1]) if last else None
    return m.group(1).lower() if m else ""


def select_strategy(ctx: Ctx, d: Draft, prior: List[str]) -> Draft:
    """Deterministic strategy choice. The decision (what to offer) never
    changes here, only how the offer is made:
      question-led   curious asks            continuation-led  planning / open thread
      action-led     compliance              artifact-led      short drafts, or the merchant
      proof/metric/opportunity-led: everything else (from the strategy's genre)
    Artifact-led puts the draft itself in the message instead of re-offering
    it, so a repeated 'Want me to draft…?' never reaches the same merchant."""
    genre = d.genre
    if genre == "curious_ask":
        d.extra["strategy"] = "question-led"
    elif genre in ("planning_artifact", "momentum") and d.extra.get("proposal") or genre == "planning_artifact":
        d.extra["strategy"] = "continuation-led"
    elif ctx.spec.family == "compliance":
        d.extra["strategy"] = "action-led"
    elif d.send_as == "vera" and d.artifact and not d.extra.get("blocked"):
        offer = d.extra.get("offer")
        repeated = ((bool(_cta_frame(d.ask)) and any(_cta_frame(p) == _cta_frame(d.ask) for p in prior))
                    or bool(offer and any(offer in p for p in prior))
                    or any(d.ask and d.ask in p for p in prior))           # the exact same ask already went out
        if d.artifact in _INLINE_ARTIFACTS or repeated:
            from . import artifacts
            text, _ask, next_step = artifacts.deliverable(ctx, d.artifact, d.extra)
            if len(text) <= 420:
                d.beats.append(f"Here's a ready draft:\n{text}")
                d.ask = "Reply YES and I'll queue it — or tell me what to change."
                d.ask_hi = "YES likhiye toh queue kar dungi — ya bataiye kya badalna hai."
                d.extra.update({"strategy": "artifact-led", "artifact_inline": True, "next_step": next_step})
                return d
        d.extra["strategy"] = {"diagnose_fix": "metric-led", "seasonal_reframe": "metric-led", "momentum": "metric-led",
                               "research_signal": "proof-led", "demand_signal": "proof-led", "competitive_response": "proof-led",
                               "contrarian_event": "opportunity-led", "contrarian_redirect": "opportunity-led"}.get(genre, "opportunity-led")
    else:
        d.extra.setdefault("strategy", "customer-service" if d.send_as != "vera" else "opportunity-led")
    return d


def compose_full(category: Optional[dict], merchant: Optional[dict], trigger: Optional[dict],
                 customer: Optional[dict] = None, *, now: Optional[datetime] = None, clock_trusted: bool = False,
                 prior_bodies: Iterable[str] = (), last_merchant_text: Optional[str] = None,
                 use_llm: bool = True) -> Composition:
    trigger = trigger or {}
    cat_v, mer_v = CategoryView(category), MerchantView(merchant)
    cus_v = CustomerView(customer) if customer else None
    customer_facing = bool(cus_v) and (str(trigger.get("scope")) == "customer" or trigger.get("customer_id"))
    lang = language.for_customer(cus_v) if customer_facing else language.for_merchant(mer_v, cat_v, last_merchant_text)
    ctx = Ctx(category=cat_v, merchant=mer_v, trigger=trigger, customer=cus_v, now=now,
              clock_trusted=clock_trusted, lang=lang)
    draft = plan(ctx)
    if draft.send_as == "vera" and customer_facing:
        # Rerouted to the owner (consent gap / approval): owner's language applies.
        ctx.lang = language.for_merchant(mer_v, cat_v, last_merchant_text)
    prior = list(prior_bodies) + mer_v.recent_vera_bodies()
    # Only offers the merchant didn't engage with count as "already asked":
    # a history CTA they said yes to is continuity, not repetition.
    draft = select_strategy(ctx, draft, list(prior_bodies) + mer_v.recent_vera_bodies(ignored_only=True))
    body, tname, tparams = realize(ctx, draft)
    v = _validate(ctx, body, draft, prior)
    repaired = False
    repeat = [i for i in v.issues if "repeat" in i or "duplicate" in i]
    if repeat:
        # Stripping sentences to dodge the repetition check produces a weaker
        # copy of the same message. Nothing new to say -> don't send.
        draft.extra["blocked"] = True
        draft.extra["block_reason"] = "no new information since the last message to this merchant"
    elif not v.ok:
        log.info("draft failed validation (%s); repairing", v.issues)
        body, tname, tparams, v, draft = _repair(ctx, draft, prior)
        repaired = True
    llm_used = False
    if use_llm and CONFIG.llm_enabled and v.ok:
        payload = {
            "audience": "customer" if draft.send_as == "merchant_on_behalf" else "merchant",
            "send_as": draft.send_as, "category": cat_v.slug, "voice": ctx.policy.voice,
            "tone": cat_v.tone, "language": draft.lang_applied if draft.send_as != "vera" else ctx.lang.code,
            "salutation": ctx.sal if draft.send_as == "vera" else "", "cta_shape": draft.cta,
            "draft": body, "evidence": [e.as_dict() for e in ctx.ledger][:12],
            "taboos": cat_v.taboos[:10], "genre": draft.genre,
        }
        polished = llm.polish(payload)
        if polished and polished != body:
            v2 = _validate(ctx, polished, draft, prior)
            if v2.ok and not preserves_facts(body, polished):
                v2 = Validation(False, ["LLM rewrite changed or added facts"])
            if v2.ok and language.detect(polished) != language.detect(body):
                v2 = Validation(False, ["LLM rewrite changed the language"])
            if v2.ok and _cta_frame(polished) and _cta_frame(body) and _cta_intent(polished) != _cta_intent(body):
                v2 = Validation(False, ["LLM rewrite changed the CTA intent"])
            if v2.ok and len(polished) <= max(len(body) * 1.3, 400):
                body, v, llm_used = polished, v2, True
                tparams = [tparams[0], polished.replace("\n", " | "), ""]
    grounded = len(re.findall(r"\d[\d,.]*", body))  # every number already passed the grounding check
    val = estimate_value(ctx, draft, grounded)
    sk = _suppression_key(trigger, mer_v.id, cus_v.id if cus_v else None, ctx.spec.family)
    audit = {
        "composer_version": COMPOSER_VERSION,
        "context_hashes": {"category": context_hash(category or {}), "merchant": context_hash(merchant or {}),
                           "trigger": context_hash(trigger), "customer": context_hash(customer) if customer else None},
        "trigger_id": trigger.get("id"), "trigger_kind": ctx.kind, "family": ctx.spec.family,
        "genre": draft.genre, "strategy": draft.extra.get("strategy"), "language": ctx.lang.code if draft.send_as == "vera" else draft.lang_applied,
        "placeholder_payload": ctx.placeholder, "evidence": [e.as_dict() for e in ctx.ledger],
        "expected_action": draft.expected_action, "adjusted_action": draft.adjusted_action,
        "validation": v.as_dict(), "repaired": repaired, "llm_used": llm_used, "fallback_used": not llm_used,
    }
    return Composition(body=body, cta=draft.cta if draft.beats or draft.ask else "none", send_as=draft.send_as,
                       suppression_key=sk, rationale=rationale(ctx, draft), template_name=tname,
                       template_params=tparams, draft=draft, ctx=ctx, value=val.as_dict(),
                       validation=v.as_dict(), audit=audit)


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    """Challenge entry point (brief §7.1). Deterministic for the same inputs."""
    return compose_full(category, merchant, trigger, customer).public()
