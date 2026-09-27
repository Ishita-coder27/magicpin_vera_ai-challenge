#!/usr/bin/env python3
"""Local evaluation harness (development only — NOT the official judge).

Simulates the judge's lifecycle against the bot (in-process by default, or a
live URL with --url): warmup push, a 60-minute window of 5-minute ticks, mid-
window context injection (new digest items, perf updates, new triggers, new
customers + recall), scripted merchant replies (engaged / auto-reply / hostile
/ off-topic / later / objection), then prints structural + heuristic checks.

    python local_eval.py               # in-process
    python local_eval.py --url http://localhost:8080
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from generate_submission import load_expanded  # noqa: E402
from vera.evidence import FactIndex  # noqa: E402
from vera.validate import ungrounded_numbers  # noqa: E402

VALID_CTA = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}
QUALIFYING = ["would you", "do you", "can you tell", "what if", "how about"]
PERSONAS = [
    ("engaged", ["Yes, go ahead", "Confirm", "thanks"]),
    ("auto_reply", ["Thank you for contacting us! Our team will respond shortly."] * 4),
    ("hostile_then_gst", ["This is useless, stop bothering me", "Can you file my GST?"]),
    ("off_topic", ["Can you help me get a business loan?", "ok fine, go ahead"]),
    ("later", ["busy now, message me tomorrow"]),
    ("objection", ["too expensive", "hmm ok, do it"]),
    ("question", ["How much does this cost?", "what do I need to do?", "ok send it"]),
    ("hinglish", ["haan bhai, bhej do", "theek hai confirm"]),
]


def _derivations(facts, contexts, now):
    """Accept simple deterministic derivations a verifying judge would accept:
    pairwise sums/differences/products of context integers, and day counts
    between dates in the contexts and the tick's `now`."""
    from vera.formatting import parse_dt
    ints, dates = set(), []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool) and 1 <= abs(o) <= 100000:
            ints.add(int(o)) if float(o).is_integer() else None
        elif isinstance(o, str):
            if re.match(r"20\d\d-\d\d-\d\d", o):
                dates.append(parse_dt(o))
            for x in re.findall(r"₹\s?([\d,]+)", o):
                ints.add(int(x.replace(",", "")))
    for ctx in contexts:
        walk(ctx or {})
    small = sorted(ints)[:200]
    for a in small:
        for b in small:
            facts.register(a + b)
            facts.register(abs(a - b))
            if a <= 50 or b <= 50:
                facts.register(a * b)
    ref = parse_dt(now) if now else None
    for d in [x for x in dates if x] + ([ref] if ref else []):
        for e in [x for x in dates if x]:
            facts.register(abs((e - d).days))


class InProcess:
    def __init__(self):
        import logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("vera").setLevel(logging.WARNING)
        from fastapi.testclient import TestClient
        import bot
        bot.ENGINE.teardown()
        self.c = TestClient(bot.app)

    def call(self, method, path, body=None):
        t = time.time()
        r = self.c.request(method, path, json=body)
        return r.status_code, r.json(), (time.time() - t) * 1000


class Remote:
    def __init__(self, url):
        import urllib.request
        self.url, self.u = url.rstrip("/"), urllib.request

    def call(self, method, path, body=None):
        import urllib.error
        data = json.dumps(body).encode() if body is not None else None
        req = self.u.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        t = time.time()
        try:
            with self.u.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read()), (time.time() - t) * 1000
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), (time.time() - t) * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    args = ap.parse_args()
    bot = Remote(args.url) if args.url else InProcess()
    cats, merchants, customers, triggers, pairs = load_expanded()
    lat = defaultdict(list)
    fails = Counter()
    notes = []

    def push(scope, cid, payload, v=1):
        s, j, ms = bot.call("POST", "/v1/context", {"scope": scope, "context_id": cid, "version": v, "payload": payload,
                                                     "delivered_at": "2026-04-26T10:00:00Z"})
        lat["context"].append(ms)
        return s, j

    # ---- warmup
    bot.call("POST", "/v1/teardown")
    for slug, c in cats.items():
        push("category", slug, c)
    for mid, m in merchants.items():
        push("merchant", mid, m)
    for cid, c in customers.items():
        push("customer", cid, c)
    s, h, _ = bot.call("GET", "/v1/healthz")
    warm_ok = h.get("contexts_loaded") == {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}
    if not warm_ok:
        fails["warmup_counts"] += 1
    # version semantics
    m1 = merchants["m_001_drmeera_dentist_delhi"]
    s_same, _ = push("merchant", m1["merchant_id"], m1, 1)
    if s_same != 409:
        fails["idempotency"] += 1

    # ---- test window: 12 ticks, triggers released over time
    trig_list = list(triggers.values())
    released, actions = [], []
    injected = False
    for k in range(12):
        now = f"2026-04-26T{10 + (k * 5) // 60:02d}:{(k * 5) % 60:02d}:00Z"
        for t in trig_list[k * 9:(k + 1) * 9]:
            push("trigger", t["id"], t)
            released.append(t["id"])
        if k == 6 and not injected:   # adaptive injection
            injected = True
            cat = copy.deepcopy(cats["dentists"])
            cat["digest"].append({"id": "d_inj_sdf", "kind": "research", "title": "Silver diamine fluoride arrests 81% of early child caries",
                                  "source": "IJDR Nov 2026, p.3", "trial_n": 640, "patient_segment": "children",
                                  "summary": "Two-year, 6-city trial. Twice-yearly application performed best."})
            push("category", "dentists", cat, 2)
            cats["dentists"] = cat
            m = copy.deepcopy(merchants["m_002_bharat_dentist_mumbai"])
            m["performance"].update({"calls": 19, "ctr": 0.033, "delta_7d": {"views_pct": 0.25, "calls_pct": 0.4}})
            push("merchant", m["merchant_id"], m, 2)
            merchants[m["merchant_id"]] = m
            new_t = {"id": "trg_inj_research", "scope": "merchant", "kind": "research_digest", "source": "external",
                     "merchant_id": "m_014_dr_asha_dentist_chandigarh", "customer_id": None,
                     "payload": {"category": "dentists", "top_item_id": "d_inj_sdf"}, "urgency": 2,
                     "suppression_key": "research:dentists:inj", "expires_at": "2026-12-01T00:00:00Z"}
            push("trigger", new_t["id"], new_t)
            triggers[new_t["id"]] = new_t
            released.append(new_t["id"])
            nc = {"customer_id": "c_inj_meena", "merchant_id": "m_001_drmeera_dentist_delhi",
                  "identity": {"name": "Meena", "phone_redacted": "<phone>", "language_pref": "hi-en mix"},
                  "relationship": {"first_visit": "2025-10-01", "last_visit": "2026-04-02", "visits_total": 3,
                                   "services_received": ["cleaning"]},
                  "state": "lapsed_soft", "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp",
                                                          "reminder_opt_in": True},
                  "consent": {"opted_in_at": "2025-10-01", "scope": ["recall_reminders"]}}
            push("customer", nc["customer_id"], nc)
            customers[nc["customer_id"]] = nc
            rt = {"id": "trg_inj_recall", "scope": "customer", "kind": "recall_due", "source": "internal",
                  "merchant_id": nc["merchant_id"], "customer_id": nc["customer_id"],
                  "payload": {"service_due": "6_month_cleaning", "last_service_date": "2026-04-02",
                              "available_slots": [{"iso": "2026-10-07T18:00:00+05:30", "label": "Wed 7 Oct, 6pm"}]},
                  "urgency": 3, "suppression_key": "recall:c_inj_meena", "expires_at": "2026-12-01T00:00:00Z"}
            push("trigger", rt["id"], rt)
            triggers[rt["id"]] = rt
            released.append(rt["id"])
        s, j, ms = bot.call("POST", "/v1/tick", {"now": now, "available_triggers": list(released)})
        lat["tick"].append(ms)
        if s != 200 or "actions" not in j:
            fails["tick_malformed"] += 1
            continue
        for a in j["actions"]:
            a["_tick"] = k
            a["_now"] = now
            actions.append(a)

    # ---- per-action checks
    by_cat, by_fam = defaultdict(list), defaultdict(list)
    halluc, multi_q, bad_schema, repeats = [], [], 0, 0
    seen_bodies = set()
    for a in actions:
        t = triggers.get(a["trigger_id"], {})
        m = merchants.get(a["merchant_id"], {})
        cid = a.get("customer_id") or t.get("customer_id")
        c = customers.get(cid) if cid else None
        cat = cats.get(m.get("category_slug"), {})
        if not (a.get("body") and a.get("cta") in VALID_CTA and a.get("send_as") in ("vera", "merchant_on_behalf")
                and a.get("suppression_key") and a.get("rationale") and a.get("conversation_id")):
            bad_schema += 1
        facts = FactIndex(cat, m, t, c)
        facts.register_many(re.findall(r"\d[\d,]*", a.get("rationale", "")))
        _derivations(facts, (cat, m, t, c), a.get("_now"))
        bad = ungrounded_numbers(a["body"], facts)
        if bad:
            halluc.append((a["trigger_id"], bad))
        if a["send_as"] == "vera" and re.sub(r'"[^"]*"', "", a["body"]).count("?") > 1:
            multi_q.append(a["trigger_id"])
        if a["body"] in seen_bodies:
            repeats += 1
        seen_bodies.add(a["body"])
        by_cat[m.get("category_slug", "?")].append(a)
        by_fam[t.get("kind", "?")].append(a)

    # adaptation checks
    inj_research = next((a for a in actions if a["trigger_id"] == "trg_inj_research"), None)
    inj_recall = next((a for a in actions if a["trigger_id"] == "trg_inj_recall"), None)
    adapt = {
        "new digest item used (81%, IJDR)": bool(inj_research and "81%" in inj_research["body"] and "IJDR" in inj_research["body"]),
        "customer added mid-test got recall": bool(inj_recall and inj_recall["send_as"] == "merchant_on_behalf"
                                                    and "Meena" in inj_recall["body"]),
    }
    bharat_later = [a for a in actions if a["merchant_id"] == "m_002_bharat_dentist_mumbai" and a["_tick"] >= 6]
    if bharat_later:
        adapt["no stale 'calls down 50%' after perf update"] = not any("down 50%" in a["body"] for a in bharat_later)

    # ---- conversations with personas
    conv_results = []
    reps_in_conv = 0
    for i, a in enumerate([a for a in actions if a["send_as"] == "vera"][:len(PERSONAS) * 2]):
        name, script = PERSONAS[i % len(PERSONAS)]
        bodies, trace = [a["body"]], []
        for turn, msg in enumerate(script):
            s, r, ms = bot.call("POST", "/v1/reply", {"conversation_id": a["conversation_id"], "merchant_id": a["merchant_id"],
                                                      "customer_id": None, "from_role": "merchant", "message": msg,
                                                      "received_at": "2026-04-26T11:00:00Z", "turn_number": turn + 2})
            lat["reply"].append(ms)
            act = r.get("action")
            trace.append(act)
            if act == "send":
                if r["body"] in bodies:
                    reps_in_conv += 1
                bodies.append(r["body"])
                if name in ("engaged", "hinglish") and turn == 0 and any(q in r["body"].lower() for q in QUALIFYING):
                    fails["requalified_after_yes"] += 1
            if act == "end":
                break
        ok = {
            "engaged": trace[:1] == ["send"],
            "auto_reply": "end" in trace and trace.index("end") <= 2,
            "hostile_then_gst": trace[0] in ("end", "send"),
            "off_topic": trace[0] == "send",
            "later": trace == ["wait"],
            "objection": trace[0] == "send",
            "question": trace[0] == "send",
            "hinglish": trace[0] == "send",
        }[name]
        conv_results.append((name, a["trigger_id"], trace, ok))

    # ---- report
    def p(x, q):
        return statistics.quantiles(x, n=100)[q - 1] if len(x) >= 2 else (x[0] if x else 0)

    print("=" * 64)
    print("VERA LOCAL EVALUATION  (local heuristics — NOT the official judge)")
    print("=" * 64)
    print(f"Warmup counts correct:            {'PASS' if warm_ok else 'FAIL'}")
    print(f"Idempotent re-push -> 409:         {'PASS' if not fails['idempotency'] else 'FAIL'}")
    print(f"Triggers released / actions sent:  {len(released)} / {len(actions)}  (restraint: one per merchant per tick, suppression, consent)")
    print(f"Schema errors:                     {bad_schema}")
    print(f"Hallucinated-number failures:      {len(halluc)} {halluc[:3]}")
    print(f"Multiple-question (multi-CTA):     {len(multi_q)} {multi_q[:3]}")
    print(f"Repeated bodies across sends:      {repeats}")
    print(f"Repeated bodies within a conv:     {reps_in_conv}")
    print(f"Re-qualified after explicit yes:   {fails['requalified_after_yes']}")
    print("\nBy category (sends):")
    for k in sorted(by_cat):
        print(f"  {k:14} {len(by_cat[k])}")
    print("\nBy trigger kind (sends):")
    for k in sorted(by_fam):
        print(f"  {k:28} {len(by_fam[k])}")
    print("\nAdaptation:")
    for k, v in adapt.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("\nConversation personas:")
    for name, tid, trace, ok in conv_results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:18} {tid:40} {trace}")
    print("\nLatency (ms):")
    for k in ("context", "tick", "reply"):
        if lat[k]:
            print(f"  {k:8} p50={p(lat[k], 50):7.1f}  p95={p(lat[k], 95):7.1f}  max={max(lat[k]):7.1f}")
    total_fail = (bad_schema + len(halluc) + len(multi_q) + repeats + reps_in_conv + sum(fails.values())
                  + sum(1 for v in adapt.values() if not v) + sum(1 for *_, ok in conv_results if not ok))
    print(f"\nOVERALL: {'PASS' if total_fail == 0 else f'{total_fail} issue(s)'}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
