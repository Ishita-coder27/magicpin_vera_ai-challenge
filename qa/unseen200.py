#!/usr/bin/env python3
"""220 unseen scenarios (none copied from the canonical examples), generated
deterministically, with internal quality metrics. Not an official score.

    python qa/unseen200.py                 # policy A (1 proactive message / merchant / tick)
    python qa/unseen200.py --policy B      # policy B (allow an independent 2nd opportunity)
    python qa/unseen200.py --json out.json

Mix: 50 conflicting-signal, 30 sparse-trigger, 30 consent, 20 context-update,
20 active-conversation-vs-new-trigger, 20 auto-reply/repetition,
20 obvious-action-is-wrong, 30 category-specific.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient  # noqa: E402

import bot  # noqa: E402
from generate_submission import load_expanded  # noqa: E402
from vera import engine as engine_mod  # noqa: E402
from vera.composer import compose, compose_full  # noqa: E402
from vera.evidence import FactIndex  # noqa: E402
from vera.quality import anchors, lint  # noqa: E402
from vera.validate import overclaims, similarity, ungrounded_numbers  # noqa: E402

CATS, MERCH, CUST, TRIG, _ = load_expanded()
C = TestClient(bot.app)
rnd = random.Random(20260927)
SLUGS = sorted(CATS)
NOUN = {"dentists": "Dental Care", "salons": "Salon", "restaurants": "Kitchen", "gyms": "Fitness", "pharmacies": "Medicos"}
FOREIGN = {"dentists": ["covers", "thali", "haircut", "balayage", "molecule"], "salons": ["patients", "caries", "covers", "molecule"],
           "restaurants": ["caries", "patients", "balayage", "molecule"], "gyms": ["caries", "patients", "thali", "balayage"],
           "pharmacies": ["covers", "haircut", "balayage", "caries", "thali"]}
QUAL = ["would you", "do you", "can you tell", "what if", "how about"]
M = defaultdict(int)
LAT = []
FAILS = defaultdict(list)


# ------------------------------------------------------------------ helpers
def push(scope, cid, payload, v=1):
    return C.post("/v1/context", json={"scope": scope, "context_id": cid, "version": v, "payload": payload})


def tick(ids, now):
    t0 = time.perf_counter()
    r = C.post("/v1/tick", json={"now": now, "available_triggers": ids}).json()["actions"]
    LAT.append((time.perf_counter() - t0) * 1000)
    return r


def reply(conv, msg, mid, role="merchant", cid=None, turn=2, at=None):
    body = {"conversation_id": conv, "merchant_id": mid, "customer_id": cid, "from_role": role, "message": msg, "turn_number": turn}
    if at:
        body["received_at"] = at
    t0 = time.perf_counter()
    r = C.post("/v1/reply", json=body).json()
    LAT.append((time.perf_counter() - t0) * 1000)
    return r


def reset():
    bot.ENGINE.teardown()
    for k, v in CATS.items():
        push("category", k, v)


def new_merchant(i, slug=None):
    slug = slug or SLUGS[i % 5]
    base = copy.deepcopy(rnd.choice([m for m in MERCH.values() if m["category_slug"] == slug]))
    mid = f"m_u{i}"
    base["merchant_id"] = mid
    base["identity"]["name"] = f"{rnd.choice(['Aarogya', 'Neel', 'Saffron', 'Kavach', 'Urban', 'Tulsi', 'Vistara'])} {NOUN[slug]}"
    base["identity"]["owner_first_name"] = rnd.choice(["Nisha", "Farhan", "Deepa", "Rohan", "Meenal", "Sanjay", "Ira"])
    p = base.setdefault("performance", {})
    p.update({"views": rnd.randint(200, 8000), "calls": rnd.randint(1, 90), "ctr": round(rnd.uniform(0.01, 0.08), 3),
              "leads": rnd.randint(0, 40), "delta_7d": {"views_pct": round(rnd.uniform(-0.5, 0.5), 2),
                                                          "calls_pct": round(rnd.uniform(-0.5, 0.5), 2)}})
    cat = CATS[slug]["offer_catalog"]
    r = rnd.random()
    base["offers"] = ([] if r < 0.3 else [{"id": f"o{j}", "title": o["title"], "status": rnd.choice(["active", "expired", "paused"])}
                                          for j, o in enumerate(rnd.sample(cat, 3))])
    base["identity"]["verified"] = rnd.random() > 0.3
    push("merchant", mid, base)
    return base


def trig(i, m, kind, payload, urgency=3, scope="merchant", cid=None, exp="2026-12-31T00:00:00Z"):
    t = {"id": f"t_u{i}_{kind}", "scope": scope, "kind": kind, "merchant_id": m["merchant_id"], "customer_id": cid,
         "payload": payload, "urgency": urgency, "suppression_key": f"u{i}:{kind}", "expires_at": exp}
    push("trigger", t["id"], t)
    return t


def check_message(a, m, cat, t, cust=None, tag=""):
    """Per-message quality metrics."""
    M["messages"] += 1
    body = a["body"]
    f = FactIndex(cat, m, t, cust)
    comp = compose_full(cat, m, t, cust, now=NOW_DT, clock_trusted=True)   # same inputs -> ledger for derived values
    f.register_many([e.text for e in comp.ctx.ledger] + [e.value for e in comp.ctx.ledger if e.value is not None])
    f.register_many(re.findall(r"\d[\d,]*", a.get("rationale", "")))
    if ungrounded_numbers(body, f) or re.search(r"\b\d+ (salons|clinics|restaurants|gyms|pharmacies) (in your area|nearby)", body, re.I):
        M["unsupported_claim"] += 1
        FAILS["unsupported_claim"].append((tag, body[:120]))
    if a["send_as"] == "vera" and re.sub(r'"[^"]*"', "", body).count("?") > 1:
        M["cta_conflict"] += 1
        FAILS["cta_conflict"].append((tag, body[:120]))
    slug = m["category_slug"]
    if any(re.search(r"(?<![A-Za-z])" + w + r"(?![A-Za-z])", body, re.I) for w in FOREIGN[slug]):
        M["category_mismatch"] += 1
        FAILS["category_mismatch"].append((tag, body[:120]))
    others = [x["identity"]["name"] for x in MERCH.values()]
    if any(o in body for o in others if o != m["identity"]["name"] and len(o) > 8):
        M["merchant_mismatch"] += 1
    if overclaims(body):
        M["execution_overclaim"] += 1
        FAILS["execution_overclaim"].append((tag, body[:120]))
    if a["send_as"] == "vera" and comp.ctx.spec.family not in ("compliance", "knowledge") and not anchors(comp.ctx, body):
        M["generic_message"] += 1
        FAILS["generic_message"].append((tag, body[:120]))


# ------------------------------------------------------------------ scenario families
KINDS = ["research_digest", "perf_dip", "perf_spike", "festival_upcoming", "competitor_opened", "review_theme_emerged",
         "curious_ask_due", "milestone_reached", "dormant_with_vera", "gbp_unverified", "renewal_due", "category_seasonal"]


def payload_for(kind, slug, rich=True):
    if not rich:
        return {"placeholder": True}
    return {
        "research_digest": {"top_item_id": rnd.choice(CATS[slug]["digest"])["id"]},
        "perf_dip": {"metric": rnd.choice(["calls", "views"]), "delta_pct": -round(rnd.uniform(0.15, 0.6), 2), "window": "7d"},
        "perf_spike": {"metric": "calls", "delta_pct": round(rnd.uniform(0.15, 0.6), 2), "window": "7d"},
        "festival_upcoming": {"festival": rnd.choice(["Holi", "Eid", "Diwali", "Onam"]), "date": "2026-10-20", "days_until": rnd.choice([3, 12, 40, 150])},
        "competitor_opened": {"competitor_name": rnd.choice(["Prime", "Crest", "Nova"]) + " " + NOUN[slug], "distance_km": round(rnd.uniform(0.3, 3), 1)},
        "review_theme_emerged": {"theme": "wait_time", "occurrences_30d": rnd.randint(2, 7), "trend": "rising"},
        "curious_ask_due": {}, "milestone_reached": {"metric": "review_count", "value_now": 96, "milestone_value": 100},
        "dormant_with_vera": {"days_since_last_merchant_message": rnd.randint(14, 60)},
        "gbp_unverified": {"verified": False, "verification_path": "postcard_or_phone_call"},
        "renewal_due": {"days_remaining": rnd.randint(3, 20), "plan": "Pro"},
        "category_seasonal": {"season": "monsoon_2026", "trends": ["umbrella_demand_+30"]},
    }[kind]


def conflicting(n=50):
    """Several simultaneous triggers for one merchant: exactly one proactive send
    (policy A) and it must be the highest-priority family."""
    prio = {"compliance": 3, "planning": 2}
    for i in range(n):
        reset()
        m = new_merchant(i)
        slug = m["category_slug"]
        kinds = rnd.sample(KINDS, 3)
        ts = [trig(i * 10 + j, m, k, payload_for(k, slug), urgency=rnd.randint(1, 4)) for j, k in enumerate(kinds)]
        if i % 5 == 0:   # add a compliance item that must win
            d = next((x for x in CATS[slug]["digest"] if x.get("kind") in ("compliance", "alert")), None)
            if d:
                ts.append(trig(i * 10 + 9, m, "regulation_change", {"top_item_id": d["id"], "deadline_iso": "2026-12-01"}, urgency=4))
        acts = [a for a in tick([t["id"] for t in ts], "2026-04-26T10:00:00Z") if a["send_as"] == "vera"]
        M["conflicting_cases"] += 1
        if len(acts) > POLICY_MAX:
            M["over_messaging"] += 1
            FAILS["over_messaging"].append((f"conf{i}", [a["trigger_id"] for a in acts]))
        if i % 5 == 0 and any(t["kind"] == "regulation_change" for t in ts) and acts and "regulation" not in acts[0]["trigger_id"]:
            M["priority_error"] += 1
            FAILS["priority_error"].append((f"conf{i}", acts[0]["trigger_id"]))
        for a in acts:
            t = next(t for t in ts if t["id"] == a["trigger_id"])
            check_message(a, m, CATS[slug], t, tag=f"conf{i}")
        M["extra_sends"] += max(0, len(acts) - 1)


def sparse(n=30):
    for i in range(n):
        reset()
        m = new_merchant(100 + i)
        slug = m["category_slug"]
        k = KINDS[i % len(KINDS)]
        t = trig(100 + i, m, k, {"placeholder": True}, urgency=rnd.randint(1, 3))
        acts = tick([t["id"]], "2026-04-26T10:00:00Z")
        M["sparse_cases"] += 1
        if not acts:
            M["sparse_nosend"] += 1
            continue
        check_message(acts[0], m, CATS[slug], t, tag=f"sparse{i}:{k}")


def consent_cases(n=30):
    variants = ["none", "wrong_scope", "wrong_merchant", "no_channel", "ok_recall", "ok_promo_winback", "stopped"]
    for i in range(n):
        reset()
        m = new_merchant(200 + i)
        v = variants[i % len(variants)]
        cu = copy.deepcopy(rnd.choice(list(CUST.values())))
        cu["customer_id"] = f"c_u{i}"
        cu["merchant_id"] = m["merchant_id"]
        cu["consent"] = {"opted_in_at": "2025-06-01", "scope": ["recall_reminders"]}
        cu["preferences"] = {"channel": "whatsapp", "reminder_opt_in": True}
        kind = "recall_due"
        if v == "none":
            cu["consent"], cu["preferences"]["reminder_opt_in"] = {"opted_in_at": None, "scope": []}, False
        elif v == "wrong_scope":
            cu["consent"]["scope"], cu["preferences"]["reminder_opt_in"] = ["promotional_offers_x"], False
        elif v == "wrong_merchant":
            cu["merchant_id"] = "m_someone_else"
        elif v == "no_channel":
            cu["preferences"]["channel"] = "none_recorded"
        elif v == "ok_promo_winback":
            cu["consent"]["scope"], kind = ["promotional_offers"], "customer_lapsed_soft"
        if kind == "recall_due" and m["category_slug"] not in ("dentists", "salons", "gyms"):
            kind = "appointment_tomorrow"      # recall isn't native to this vertical; the engine routes it to the owner
        push("customer", cu["customer_id"], cu)
        t = trig(200 + i, m, kind, {"placeholder": True}, scope="customer", cid=cu["customer_id"])
        if v == "stopped":
            a0 = tick([t["id"]], "2026-04-26T10:00:00Z")
            if a0:
                reply(a0[0]["conversation_id"], "STOP", m["merchant_id"], role="customer", cid=cu["customer_id"])
            t = trig(260 + i, m, kind, {"placeholder": True}, scope="customer", cid=cu["customer_id"])
        acts = tick([t["id"]], "2026-04-26T11:00:00Z")
        to_customer = [a for a in acts if a["send_as"] == "merchant_on_behalf"]
        should = v in ("ok_recall", "ok_promo_winback")
        M["consent_cases"] += 1
        if bool(to_customer) != should:
            M["consent_failure"] += 1
            FAILS["consent_failure"].append((f"consent{i}:{v}", [a["body"][:60] for a in acts]))
        for a in to_customer:
            check_message(a, m, CATS[m["category_slug"]], t, cust=cu, tag=f"consent{i}")


def context_updates(n=20):
    for i in range(n):
        reset()
        m = new_merchant(300 + i)
        slug = m["category_slug"]
        m["offers"] = []
        push("merchant", m["merchant_id"], m, 2)
        t = trig(300 + i, m, "perf_dip", {"metric": "calls", "delta_pct": -0.3, "window": "7d"})
        a1 = tick([t["id"]], "2026-04-26T10:00:00Z")
        m3 = copy.deepcopy(m)
        live = next(o["title"] for o in CATS[slug]["offer_catalog"] if "@" in o["title"])
        m3["offers"] = [{"id": "o_new", "title": live, "status": "active"}]
        push("merchant", m["merchant_id"], m3, 3)
        push("merchant", m["merchant_id"], m, 1)                  # stale replay must be rejected
        t2 = {**t, "suppression_key": f"u{i}:upd", "payload": {**t["payload"], "delta_pct": -0.45}}
        push("trigger", t["id"], t2, 2)
        a2 = tick([t["id"]], "2026-04-26T11:00:00Z")
        M["context_update_cases"] += 1
        stale = (not a2) or ("no live offer" in a2[0]["body"].lower())
        if stale:
            M["stale_context_failure"] += 1
            FAILS["stale_context_failure"].append((f"upd{i}", a2[0]["body"][:120] if a2 else "(no send)"))
        elif a1 and a1[0]["body"] == a2[0]["body"]:
            M["stale_context_failure"] += 1


def active_conversation(n=20):
    for i in range(n):
        reset()
        m = new_merchant(400 + i)
        slug = m["category_slug"]
        t1 = trig(400 + i, m, "review_theme_emerged", payload_for("review_theme_emerged", slug))
        a = tick([t1["id"]], "2026-04-26T10:00:00Z")
        if not a:
            continue
        reply(a[0]["conversation_id"], "interesting, tell me more", m["merchant_id"])
        t2 = trig(450 + i, m, "curious_ask_due", {}, urgency=2)
        b = tick([t2["id"]], "2026-04-26T10:05:00Z")
        M["active_conv_cases"] += 1
        if b:
            M["interruption"] += 1
            FAILS["interruption"].append((f"active{i}", b[0]["body"][:80]))


def auto_reply(n=20):
    forms = ["Thank you for contacting {n}! We will get back to you shortly.", "Thanks for your message. Our team will respond soon.",
             "We are currently closed. Business hours 10am-8pm.", "This is an automated response.",
             "Hello! Welcome to {n}. Visit us or call during working hours."]
    for i in range(n):
        reset()
        m = new_merchant(500 + i)
        msg = forms[i % len(forms)].format(n=m["identity"]["name"])
        acts = [reply(f"au{i}_{k}", msg, m["merchant_id"])["action"] for k in range(4)]
        M["auto_reply_cases"] += 1
        if "send" in acts or "end" not in acts:
            M["auto_reply_failure"] += 1
            FAILS["auto_reply_failure"].append((f"auto{i}", msg[:40], acts))


def obvious_wrong(n=20):
    """Cases where the obvious action is wrong; check the engine avoids it."""
    for i in range(n):
        reset()
        k = i % 4
        if k == 0:     # weekend match -> no dine-in promo
            m = new_merchant(600 + i, "restaurants")
            t = trig(600 + i, m, "ipl_match_today", {"match": "RCB vs CSK", "match_time_iso": "2026-05-02T19:30:00+05:30", "is_weeknight": False})
            bad = lambda b: "dine-in match promo tonight" not in b.lower() and "skip" not in b.lower()
        elif k == 1:   # far-off festival -> no promo now
            m = new_merchant(600 + i, "salons")
            t = trig(600 + i, m, "festival_upcoming", {"festival": "Diwali", "date": "2026-11-08", "days_until": 170})
            bad = lambda b: "discount now would just be wasted" not in b
        elif k == 2:   # competitor cheaper -> no price match
            m = new_merchant(600 + i, "dentists")
            m["offers"] = [{"id": "o", "title": "Dental Cleaning @ ₹299", "status": "active"}]
            push("merchant", m["merchant_id"], m, 2)
            t = trig(600 + i, m, "competitor_opened", {"competitor_name": "Smile Hub", "distance_km": 1.1, "their_offer": "Dental Cleaning @ ₹199"})
            bad = lambda b: re.search(r"(?<!wouldn't )(?<!not )\bmatch (that|their) price|drop (your|the) price|₹199 too", b, re.I) is not None or ("wouldn't match" not in b and "wrong fight" not in b)
        else:          # seasonal gym dip -> retention, not ad spend
            m = new_merchant(600 + i, "gyms")
            m.setdefault("customer_aggregate", {})["total_active_members"] = rnd.randint(80, 400)
            push("merchant", m["merchant_id"], m, 2)
            t = trig(600 + i, m, "seasonal_perf_dip", {"metric": "views", "delta_pct": -0.3, "window": "7d", "is_expected_seasonal": True,
                                                       "season_note": "post_resolution_window_apr_jun"})
            bad = lambda b: "retention" not in b.lower()
        acts = tick([t["id"]], "2026-04-26T10:00:00Z")
        M["obvious_wrong_cases"] += 1
        if not acts or bad(acts[0]["body"]):
            M["obvious_action_taken"] += 1
            FAILS["obvious_action_taken"].append((f"obv{i}", acts[0]["body"][:120] if acts else "(no send)"))
        elif acts:
            check_message(acts[0], m, CATS[m["category_slug"]], t, tag=f"obv{i}")


def category_specific(n=30):
    for i in range(n):
        reset()
        slug = SLUGS[i % 5]
        m = new_merchant(700 + i, slug)
        k = KINDS[(i * 7) % len(KINDS)]
        t = trig(700 + i, m, k, payload_for(k, slug))
        acts = tick([t["id"]], "2026-04-26T10:00:00Z")
        M["category_cases"] += 1
        if acts:
            check_message(acts[0], m, CATS[slug], t, tag=f"cat{i}:{slug}:{k}")
            r = reply(acts[0]["conversation_id"], "yes do it", m["merchant_id"])
            if r.get("action") == "send":
                if any(q in r["body"].lower() for q in QUAL):
                    M["conversation_state_failure"] += 1
                    FAILS["conversation_state_failure"].append((f"cat{i}", r["body"][:80]))
                if overclaims(r["body"]):
                    M["execution_overclaim"] += 1
                    FAILS["execution_overclaim"].append((f"cat{i}-reply", r["body"][:100]))


def semantic_repetition(n=20):
    """Same merchant, sequence of different triggers over hours: consecutive
    messages must not be paraphrases of each other."""
    for i in range(n):
        reset()
        m = new_merchant(800 + i)
        slug = m["category_slug"]
        bodies = []
        for j, k in enumerate(rnd.sample(KINDS, 4)):
            t = trig(800 + i * 10 + j, m, k, payload_for(k, slug, rich=rnd.random() > 0.4))
            acts = tick([t["id"]], f"2026-04-2{6 + j}T10:00:00Z")
            if acts:
                bodies.append(acts[0]["body"])
        M["repetition_sequences"] += 1
        for x in range(len(bodies)):
            for y in range(x + 1, len(bodies)):
                if similarity(bodies[x], bodies[y]) > 0.6:
                    M["semantic_repetition"] += 1
                    FAILS["semantic_repetition"].append((f"rep{i}", bodies[x][:60], bodies[y][:60]))


def determinism():
    outs = []
    for _ in range(3):
        rnd.seed(42)
        reset()
        m = new_merchant(999)
        t = trig(999, m, "perf_dip", payload_for("perf_dip", m["category_slug"]))
        outs.append(json.dumps(tick([t["id"]], "2026-04-26T10:00:00Z"), sort_keys=True))
    M["deterministic"] = int(len(set(outs)) == 1)


POLICY_MAX = 1
from vera.formatting import parse_dt as _pd  # noqa: E402
NOW_DT = _pd("2026-04-26T10:00:00Z")


def main():
    global POLICY_MAX
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="A", choices=["A", "B"])
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    engine_mod.POLICY = args.policy
    POLICY_MAX = 1 if args.policy == "A" else 2
    t0 = time.time()
    for fn in (conflicting, sparse, consent_cases, context_updates, active_conversation, auto_reply, obvious_wrong,
               category_specific, semantic_repetition, determinism):
        fn()
    msgs = max(1, M["messages"])
    rate = lambda k, d=msgs: f"{M[k]}/{d} ({100 * M[k] / max(1, d):.1f}%)"
    cases = sum(M[k] for k in ("conflicting_cases", "sparse_cases", "consent_cases", "context_update_cases", "active_conv_cases",
                                "auto_reply_cases", "obvious_wrong_cases", "category_cases")) + M["repetition_sequences"]
    q = statistics.quantiles(LAT, n=100)
    report = {
        "policy": args.policy, "scenarios": cases, "messages_checked": M["messages"],
        "generic_message_rate": rate("generic_message"), "unsupported_claim_rate": rate("unsupported_claim"),
        "semantic_repetition": f"{M['semantic_repetition']} pairs in {M['repetition_sequences']} sequences",
        "cta_conflicts": rate("cta_conflict"), "category_mismatch": rate("category_mismatch"),
        "merchant_mismatch": rate("merchant_mismatch"), "execution_overclaims": M["execution_overclaim"],
        "stale_context_failures": rate("stale_context_failure", M["context_update_cases"]),
        "consent_failures": rate("consent_failure", M["consent_cases"]),
        "conversation_state_failures": M["conversation_state_failure"],
        "auto_reply_failures": rate("auto_reply_failure", M["auto_reply_cases"]),
        "obvious_action_taken": rate("obvious_action_taken", M["obvious_wrong_cases"]),
        "interruptions_of_live_thread": rate("interruption", M["active_conv_cases"]),
        "sparse_no_send": f"{M['sparse_nosend']}/{M['sparse_cases']}", "priority_errors": M["priority_error"],
        "over_messaging": M["over_messaging"], "extra_sends_policy_B": M["extra_sends"],
        "deterministic": bool(M["deterministic"]),
        "latency_ms": {"p50": round(q[49], 1), "p95": round(q[94], 1), "p99": round(q[98], 1), "max": round(max(LAT), 1)},
        "runtime_s": round(time.time() - t0, 1),
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    for k, v in FAILS.items():
        print(f"\n[{k}] {len(v)}")
        for x in v[:4]:
            print("   ", x)
    if args.json:
        Path(args.json).write_text(json.dumps({"report": report, "failures": {k: v[:20] for k, v in FAILS.items()}}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
