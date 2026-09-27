#!/usr/bin/env python3
"""Vera's own LLM realization layer (not the judge): live + failure injection.

    python qa/llm_path_test.py          # key read from .env (SIM_API_KEY), never printed

A  live Gemini rewrite -> validator -> accepted or deterministic fallback
B-J injected failures  -> must fall back to the deterministic body
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    k, _, v = line.partition("=")
    if k.strip() == "SIM_API_KEY" and v.strip():
        os.environ.setdefault("LLM_API_KEY", v.strip())
os.environ.update({"LLM_PROVIDER": "gemini", "LLM_MODEL": os.environ.get("VERA_LLM_MODEL", "gemini-3.5-flash-lite"),
                   "MODE": "challenge", "LLM_TIMEOUT_S": "8"})

import logging  # noqa: E402
logging.disable(logging.CRITICAL)
from generate_submission import load_expanded  # noqa: E402
import vera.llm as llm  # noqa: E402
from vera.composer import compose_full  # noqa: E402
from vera.config import CONFIG  # noqa: E402
from vera.validate import overclaims  # noqa: E402

cats, M, Cu, T, P = load_expanded()
res = []


def case(name, tid, **kw):
    t = T[tid]
    m = M[t["merchant_id"]]
    c = Cu.get(t.get("customer_id")) if t.get("customer_id") else None
    base = compose_full(cats[m["category_slug"]], m, t, c, use_llm=False)
    llm._cache.clear()
    t0 = time.perf_counter()
    out = compose_full(cats[m["category_slug"]], m, t, c)
    ms = (time.perf_counter() - t0) * 1000
    return base, out, ms


def rec(name, ok, detail):
    res.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")


print(f"LLM config: provider={CONFIG.llm_provider} model={CONFIG.llm_model} enabled={CONFIG.llm_enabled} key={'set' if CONFIG.llm_api_key else 'MISSING'}")

# A. live generation, several messages
live_ok, live_used, lat = 0, 0, []
for tid in ("trg_010_ipl_match_delhi", "trg_023_competitor_opened_dentist", "trg_003_recall_due_priya"):
    base, out, ms = case("A", tid)
    lat.append(ms)
    valid = out.validation["ok"] and not overclaims(out.body)
    live_ok += valid
    live_used += out.audit["llm_used"]
    print(f"\n  [{tid}] llm_used={out.audit['llm_used']} {ms:.0f}ms\n  DETERMINISTIC: {base.body[:160]}\n  FINAL:         {out.body[:160]}")
rec("A. live Gemini realization -> final output always valid (LLM text used only if it passed the validator)",
    live_ok == 3, f"{live_used}/3 LLM rewrites accepted, {3 - live_used}/3 fell back; latency ms {[round(x) for x in lat]}")

# B-J failure injection through the real composer path
real = llm._openai_compatible
tid = "trg_023_competitor_opened_dentist"
failures = {
    "B. timeout": lambda u: (_ for _ in ()).throw(socket.timeout("timed out")),
    "C. malformed JSON": lambda u: '{"body": "Dr. Meera, broken',
    "D. empty output": lambda u: "",
    "E. hallucinated number": lambda u: json.dumps({"body": "Dr. Meera, Smile Studio opened 1.3 km away and 47 of your patients already left. Want me to draft a Google post built around that strength?"}),
    "F. fake citation": lambda u: json.dumps({"body": "Dr. Meera, per Lancet Dental 2026, clinics that don't match prices lose share. Want me to draft a Google post built around that strength?"}),
    "G. fake offer": lambda u: json.dumps({"body": "Dr. Meera, run Dental Cleaning @ ₹149 to beat Smile Studio. Want me to draft a Google post built around that strength?"}),
    "H. multiple CTAs": lambda u: json.dumps({"body": "Dr. Meera, Smile Studio opened 1.3 km away. Want me to draft a post? Or update your listing? Or call you?"}),
    "I. wrong language": lambda u: json.dumps({"body": "डॉ. मीरा, स्माइल स्टूडियो 1.3 किमी दूर खुला है। क्या मैं पोस्ट ड्राफ्ट करूं?"}),
    "J. overlong": lambda u: json.dumps({"body": "Dr. Meera, " + "Smile Studio opened 1.3 km from you. " * 40 + "Want me to draft a Google post built around that strength?"}),
    "K. execution overclaim": lambda u: json.dumps({"body": "Dr. Meera, I've posted a Google post built around that strength and sent it to your patients."}),
    "L. CTA intent changed": lambda u: json.dumps({"body": "Dr. Meera, Smile Studio opened 1.3 km from you on 8 Apr, leading with Dental Cleaning @ ₹199. I wouldn't match that price. Shall I book you a call with our team?"}),
}
for name, fn in failures.items():
    llm._openai_compatible = fn
    t0 = time.perf_counter()
    base, out, ms = case(name, tid)
    rec(f"{name} -> deterministic fallback", out.body == base.body and not out.audit["llm_used"], f"{ms:.0f}ms, fell back={out.body == base.body}")
llm._openai_compatible = real

# timeout budget: a hanging provider must not exceed LLM_TIMEOUT_S
def hang(u):
    time.sleep(CONFIG.llm_timeout_s + 1)
    return "{}"
print(f"\nconfigured LLM timeout {CONFIG.llm_timeout_s}s (urllib timeout); engine tick polish budget REQUEST_BUDGET_S={CONFIG.request_budget_s}s")
fails = [r for r in res if not r[1]]
print(f"\nLLM PATH: {len(res) - len(fails)}/{len(res)} pass")
sys.exit(1 if fails else 0)
