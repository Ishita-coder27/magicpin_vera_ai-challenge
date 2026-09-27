"""Stateful engine behind the HTTP API: context store, conversations,
merchant memory, suppression, and the per-tick signal auction."""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import language
from .composer import Composition, compose_full
from .config import CONFIG
from .conversation import Conversation, MerchantMemory, respond
from .ctx import Ctx
from .formatting import parse_dt
from .kinds import spec_for
from .language import LangPlan
from .planner import plan as plan_draft
from .store import ContextStore
from .views import CategoryView, CustomerView, MerchantView

log = logging.getLogger("vera.engine")

SEND_THRESHOLD = 0.30        # below this expected value, silence beats a send
THREAD_HOLD_S = 30 * 60      # an engaged live thread outranks lower-value nudges
MERCHANT_GAP_S = 15 * 60     # min simulated gap between proactive sends to one merchant
# Policy A: one proactive merchant message per tick (default, kept after the A/B test).
# Policy B: also allow a second one when it is a different opportunity family and
# both are high-value. Experiment only (qa/unseen200.py --policy B).
POLICY = "A"
POLICY_B_MIN_SCORE = 0.6


class Engine:
    def __init__(self) -> None:
        self.store = ContextStore()
        self.started = time.time()
        self._lock = threading.RLock()
        self.conversations: Dict[str, Conversation] = {}
        self.memory: Dict[str, MerchantMemory] = {}
        self.suppressed: Dict[str, str] = {}      # suppression_key -> conversation_id
        self.audit_log: List[dict] = []
        self.last_tick_now: Optional[str] = None
        self._pool = cf.ThreadPoolExecutor(max_workers=8)

    # ------------------------------------------------------------ helpers
    def mem(self, merchant_id: Optional[str]) -> MerchantMemory:
        mid = merchant_id or "_unknown"
        with self._lock:
            if mid not in self.memory:
                self.memory[mid] = MerchantMemory(mid)
            return self.memory[mid]

    def teardown(self) -> None:
        with self._lock:
            self.store.clear()
            self.conversations.clear()
            self.memory.clear()
            self.suppressed.clear()
            self.audit_log.clear()

    def clock_trusted(self, now: Optional[datetime]) -> bool:
        """Is the judge's `now` on the same timeline as the triggers? If most
        stored triggers are already 'expired' at `now`, the clock is skewed
        (e.g. a simulator sending wall-clock time against an April dataset):
        then expiry is not enforced and the judge's `available_triggers` list
        is the source of truth for what's active."""
        if now is None:
            return False
        exps = [parse_dt(r.payload.get("expires_at")) for r in self.store.all("trigger")]
        exps = [e for e in exps if e]
        if not exps:
            return True
        expired = sum(1 for e in exps if e < now)
        return expired / len(exps) <= 0.5

    def _contexts(self, trigger: dict) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
        mid = trigger.get("merchant_id") or (trigger.get("payload") or {}).get("merchant_id")
        merchant = self.store.payload("merchant", mid)
        category = None
        if merchant:
            category = self.store.payload("category", merchant.get("category_slug"))
        cid = trigger.get("customer_id") or (trigger.get("payload") or {}).get("customer_id")
        customer = self.store.payload("customer", cid) if cid else None
        return category, merchant, customer

    # ------------------------------------------------------------ tick
    def tick(self, now_iso: str, available: List[str]) -> List[dict]:
        t0 = time.time()
        now = parse_dt(now_iso)
        self.last_tick_now = now_iso
        trusted = self.clock_trusted(now)
        seen, candidates, skipped = set(), [], []
        for tid in available[:500]:
            if tid in seen:
                continue
            seen.add(tid)
            if time.time() - t0 > CONFIG.request_budget_s * 0.6:
                skipped.append((tid, "time budget"))
                break
            rec = self.store.get("trigger", tid)
            if not rec:
                skipped.append((tid, "unknown trigger"))
                continue
            trg = rec.payload
            exp = parse_dt(trg.get("expires_at"))
            if trusted and exp and now and exp < now:
                skipped.append((tid, "expired"))
                continue
            category, merchant, customer = self._contexts(trg)
            if not merchant:
                skipped.append((tid, "merchant context missing"))
                continue
            if not category:
                # Without the CategoryContext there are no category taboos,
                # peer stats or catalog to ground on: defer rather than send.
                skipped.append((tid, "category context missing — deferring"))
                continue
            mid = merchant.get("merchant_id")
            mem = self.mem(mid)
            spec = spec_for(str(trg.get("kind")), str(trg.get("scope")), trg.get("payload") or {})
            sk = str(trg.get("suppression_key") or f"{trg.get('kind')}:{mid}:{trg.get('customer_id')}:{tid}")
            if sk in self.suppressed:
                skipped.append((tid, "suppressed (already sent)"))
                continue
            customer_scope = str(trg.get("scope")) == "customer" or spec.family.startswith("customer")
            if mem.do_not_contact and not customer_scope:
                skipped.append((tid, f"merchant do-not-contact ({mem.dnc_reason})"))
                continue
            hold = parse_dt(mem.hold_until) if mem.hold_until else None
            if hold and now and now < hold and ctx_urgency(trg) < 5 and not customer_scope:
                skipped.append((tid, f"merchant asked for time (hold until {mem.hold_until})"))
                continue
            if mem.auto_reply_strikes >= 2 and spec.base_value < 0.9 and not customer_scope:
                skipped.append((tid, "merchant number is auto-replying"))
                continue
            if spec.family in mem.declined_families and ctx_urgency(trg) < 4:
                skipped.append((tid, f"merchant declined {spec.family} before"))
                continue
            if customer_scope and not customer:
                skipped.append((tid, "customer context not yet available — deferring"))
                continue
            try:
                comp = compose_full(category, merchant, trg, customer, now=now, clock_trusted=trusted,
                                    prior_bodies=mem.sent_bodies, use_llm=False)
            except Exception as e:  # one bad context never takes the tick down
                log.exception("compose failed for %s", tid)
                skipped.append((tid, f"compose error {type(e).__name__}"))
                continue
            if comp.blocked:
                skipped.append((tid, f"blocked: {comp.draft.extra.get('block_reason')}"))
                continue
            if not comp.validation.get("ok"):
                skipped.append((tid, f"failed validation {comp.validation.get('issues')}"))
                continue
            score = comp.value["score"]
            if score < SEND_THRESHOLD:
                skipped.append((tid, f"low expected value {score}"))
                continue
            candidates.append((score, tid, trg, comp, mid, customer_scope))

        actions = self._select(candidates, now, skipped)
        actions = self._polish(actions, t0)
        out = []
        for score, tid, trg, comp, mid, customer_scope in actions:
            out.append(self._commit(tid, trg, comp, mid, now_iso, score))
        self.audit_log.append({"tick": now_iso, "clock_trusted": trusted, "sent": [a["trigger_id"] for a in out],
                               "skipped": skipped[:50], "ms": int((time.time() - t0) * 1000)})
        self.audit_log = self.audit_log[-200:]
        return out

    def _select(self, candidates, now, skipped):
        """One good thing per merchant per tick; customer sends are per customer.
        A live, engaged thread outranks a lower-value unrelated nudge."""
        candidates.sort(key=lambda c: (-c[0], c[1]))
        chosen, used_merchants, used_customers = [], set(), set()
        first_family: Dict[str, Tuple[str, float]] = {}
        for c in candidates:
            score, tid, trg, comp, mid, customer_scope = c
            if customer_scope:
                cid = trg.get("customer_id")
                if cid in used_customers:
                    skipped.append((tid, "customer already messaged this tick"))
                    continue
                used_customers.add(cid)
                chosen.append(c)
                continue
            if mid in used_merchants:
                fam0, s0 = first_family.get(mid, ("", 0.0))
                fam = comp.ctx.spec.family
                second_ok = (POLICY == "B" and fam != fam0 and s0 >= POLICY_B_MIN_SCORE and score >= POLICY_B_MIN_SCORE
                             and sum(1 for c2 in chosen if c2[4] == mid) < 2)
                if not second_ok:
                    skipped.append((tid, "merchant already has a higher-value message this tick"))
                    continue
                chosen.append(c)
                continue
            if self._thread_live(mid) and ctx_urgency(trg) < 4:
                skipped.append((tid, "merchant is mid-conversation; not interrupting"))
                continue
            mem = self.mem(mid)
            last = parse_dt(mem.last_proactive) if mem.last_proactive else None
            if last and now and 0 <= (now - last).total_seconds() < MERCHANT_GAP_S and ctx_urgency(trg) < 4:
                skipped.append((tid, "attention budget: merchant messaged recently"))
                continue
            used_merchants.add(mid)
            first_family[mid] = (comp.ctx.spec.family, score)
            chosen.append(c)
            if len(chosen) >= CONFIG.max_actions_per_tick:
                break
        return chosen

    def _thread_live(self, mid: str) -> bool:
        with self._lock:
            for conv in self.conversations.values():
                if conv.merchant_id == mid and not conv.ended and conv.audience == "merchant":
                    merchant_turns = [t for t in conv.turns if t["role"] != "bot"]
                    if merchant_turns and time.time() - merchant_turns[-1]["ts"] < THREAD_HOLD_S and conv.mode != "WAIT":
                        return True
        return False

    def _polish(self, actions, t0):
        """If an LLM is configured, rewrite the chosen messages in parallel
        within the remaining budget; anything late keeps its validated
        deterministic body."""
        if not CONFIG.llm_enabled or not actions:
            return actions
        remaining = max(0.5, CONFIG.request_budget_s - (time.time() - t0) - 0.5)
        futures = {}
        for i, (score, tid, trg, comp, mid, cs) in enumerate(actions):
            category, merchant, customer = self._contexts(trg)
            futures[self._pool.submit(compose_full, category, merchant, trg, customer,
                                      prior_bodies=self.mem(mid).sent_bodies, use_llm=True)] = i
        done, _ = cf.wait(futures, timeout=remaining)
        out = list(actions)
        for f in done:
            try:
                comp2 = f.result()
                if comp2.validation.get("ok"):
                    i = futures[f]
                    s, tid, trg, _, mid, cs = out[i]
                    out[i] = (s, tid, trg, comp2, mid, cs)
            except Exception:
                pass
        return out

    def _commit(self, tid, trg, comp: Composition, mid, now_iso, score) -> dict:
        cid = trg.get("customer_id") if comp.send_as == "merchant_on_behalf" else None
        base = f"conv_{(mid or 'm')[:24]}_{comp.ctx.kind or 'msg'}"
        if cid:
            base = f"conv_{cid[:24]}_{comp.ctx.kind}"
        conv_id = base + "_" + hashlib.md5(f"{tid}|{comp.suppression_key}".encode()).hexdigest()[:6]
        with self._lock:
            n = 2
            while conv_id in self.conversations:
                conv_id = f"{base}_{n}"
                n += 1
            d = comp.draft
            conv = Conversation(id=conv_id, merchant_id=mid, customer_id=cid, trigger_id=tid, send_as=comp.send_as,
                                mode="DISCOVER" if d.genre == "curious_ask" else "PROPOSE",
                                plan={"family": comp.ctx.spec.family, "genre": d.genre, "artifact": d.artifact,
                                      "action": d.action, "extra": _slim(d.extra), "cta": comp.cta,
                                      "anchor_short": d.anchor.split(";")[0] if d.anchor else "", "why_now": d.why_now},
                                lang="hinglish" if comp.ctx.lang.hindiish else "en")
            if d.extra.get("artifact_inline"):
                conv.executed, conv.mode = True, "EXECUTE"
                conv.plan["next_step"] = d.extra.get("next_step", "queue it")
            conv.add("bot", comp.body)
            conv.bot_bodies.append(comp.body)
            self.conversations[conv_id] = conv
            self.suppressed[comp.suppression_key] = conv_id
            mem = self.mem(mid)
            mem.sent_bodies = (mem.sent_bodies + [comp.body])[-20:]
            if comp.send_as == "vera":
                mem.last_proactive = now_iso
        action = {
            "conversation_id": conv_id, "merchant_id": mid, "customer_id": cid, "send_as": comp.send_as,
            "trigger_id": tid, "template_name": comp.template_name, "template_params": comp.template_params,
            "body": comp.body, "cta": comp.cta, "suppression_key": comp.suppression_key, "rationale": comp.rationale,
        }
        log.info("send %s -> %s (%s, score %.2f)", tid, conv_id, comp.draft.genre, score)
        return action

    # ------------------------------------------------------------ reply
    def reply(self, body: dict) -> dict:
        conv_id = str(body.get("conversation_id") or "")
        mid = body.get("merchant_id")
        cid = body.get("customer_id")
        role = str(body.get("from_role") or "merchant")
        msg = str(body.get("message") or "")
        with self._lock:
            conv = self.conversations.get(conv_id)
            if conv is None:
                conv = self._adopt(conv_id, mid, cid, role)
                self.conversations[conv_id] = conv
            if mid and not conv.merchant_id:
                conv.merchant_id = mid
        mem = self.mem(conv.merchant_id or f"conv:{conv_id}")
        conv.add(role, msg)
        ctx = self._ctx_for(conv, msg)
        out = respond(conv, mem, msg, ctx)
        if out.get("action") == "wait" and role == "merchant":
            base = parse_dt(body.get("received_at")) or parse_dt(self.last_tick_now) or datetime.now(timezone.utc)
            mem.hold_until = (base + timedelta(seconds=int(out.get("wait_seconds") or 0))).isoformat()
        elif role == "merchant" and out.get("action") == "send":
            mem.hold_until = None             # merchant is back; the hold no longer applies
        if out.get("action") == "send":
            conv.add("bot", out["body"])
        return out

    def _adopt(self, conv_id: str, mid: Optional[str], cid: Optional[str], role: str) -> Conversation:
        """A reply on a conversation we didn't start (restart, replay harness).
        Infer the most useful open thread for this merchant so an explicit
        'yes' can still go straight to execution."""
        conv = Conversation(id=conv_id, merchant_id=mid, customer_id=cid,
                            send_as="merchant_on_behalf" if role == "customer" else "vera")
        merchant = self.store.payload("merchant", mid)
        if not merchant or role == "customer":
            return conv
        category = self.store.payload("category", merchant.get("category_slug"))
        trg = self._best_open_trigger(mid)
        if trg is None:
            trg = _history_trigger(merchant)
        try:
            ctx = Ctx(CategoryView(category), MerchantView(merchant), trg, None)
            d = plan_draft(ctx)
            conv.trigger_id = trg.get("id")
            conv.plan = {"family": ctx.spec.family, "genre": d.genre, "artifact": d.artifact, "action": d.action,
                         "extra": _slim(d.extra), "cta": d.cta, "anchor_short": (d.anchor or "").split(";")[0],
                         "why_now": d.why_now}
        except Exception:
            log.exception("could not infer plan for adopted conversation")
        return conv

    def _best_open_trigger(self, mid: str) -> Optional[dict]:
        best, best_u = None, -1
        for rec in self.store.all("trigger"):
            t = rec.payload
            if t.get("merchant_id") == mid and str(t.get("scope")) != "customer":
                u = ctx_urgency(t)
                if u > best_u:
                    best, best_u = t, u
        return best

    def _ctx_for(self, conv: Conversation, last_text: str) -> Optional[Ctx]:
        merchant = self.store.payload("merchant", conv.merchant_id)
        if not merchant:
            return None
        category = self.store.payload("category", merchant.get("category_slug"))
        trigger = self.store.payload("trigger", conv.trigger_id) or {"kind": "conversation", "scope": "merchant"}
        customer = self.store.payload("customer", conv.customer_id) if conv.customer_id else None
        cv = CustomerView(customer) if customer else None
        mv, catv = MerchantView(merchant), CategoryView(category)
        lang = language.for_customer(cv) if cv else language.for_merchant(mv, catv, last_text)
        ctx = Ctx(catv, mv, trigger, cv, lang=lang)
        # Replies may quote what Vera already said; its numbers are grounded too.
        ctx.facts.register_many(conv.bot_bodies)
        return ctx


def ctx_urgency(trg: dict) -> int:
    try:
        return int(trg.get("urgency") or 2)
    except (TypeError, ValueError):
        return 2


def _slim(extra: dict) -> dict:
    """Keep conversation state small and JSON-friendly."""
    out = {}
    extra = {k: v for k, v in (extra or {}).items() if k not in ("artifact_inline",)}
    for k, v in (extra or {}).items():
        if k == "item" and isinstance(v, dict):
            out[k] = {kk: v.get(kk) for kk in ("id", "title", "source", "summary", "actionable", "kind")}
        elif isinstance(v, (str, int, float, list, bool)) or v is None:
            out[k] = v
    return out


def _history_trigger(merchant: dict) -> dict:
    """Synthesize a planning trigger from the merchant's own open request in
    conversation history ('Yes please, focus on whitening and aligners')."""
    mv = MerchantView(merchant)
    pend = mv.pending_merchant_intent()
    if pend:
        mturn, vturn = pend
        topic = str(mturn.get("body", ""))
        return {"id": f"history:{mv.id}", "kind": "active_planning_intent", "scope": "merchant",
                "merchant_id": mv.id, "urgency": 3,
                "payload": {"intent_topic": _topic_from(vturn, mturn), "merchant_last_message": topic}}
    return {"id": f"listing:{mv.id}", "kind": "listing_check", "scope": "merchant", "merchant_id": mv.id,
            "urgency": 2, "payload": {}}


def _topic_from(vturn: Optional[dict], mturn: dict) -> str:
    v = str((vturn or {}).get("body", "")).lower()
    m = str(mturn.get("body", "")).lower()
    focus = re.search(r"focus on ([a-z ,&]+)", m)
    if "post" in v:
        return f"google posts{' on ' + focus.group(1).strip() if focus else ''}"
    if "list" in v:
        return "customer list"
    return m[:60] or "next step"
