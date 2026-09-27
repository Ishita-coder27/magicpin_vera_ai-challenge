#!/usr/bin/env python3
"""Score the 30 canonical submission messages with the OFFICIAL simulator's
LLMScorer (same system prompt, same prompt construction, same parsing).

    SIM_PROVIDER=gemini SIM_MODEL=gemini-3.8-flash SIM_API_KEY=... \
    python qa/official_score_30.py submission.jsonl qa/official_scores_30.json
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
args = sys.argv[1:]
import qa.run_official_simulator  # noqa: F401,E402  (applies config + retry)
import judge_simulator as js  # noqa: E402
from generate_submission import load_expanded  # noqa: E402

sub = Path(args[0] if args else ROOT / "submission.jsonl")
out = Path(args[1] if len(args) > 1 else ROOT / "qa" / "official_scores_30.json")
cats, merchants, customers, triggers, pairs = load_expanded()
scorer = js.LLMScorer(js.create_provider(), None)
fallbacks = 0
rows = []
for line in sub.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    p = next(p for p in pairs if p["test_id"] == r["test_id"])
    m, t = merchants[p["merchant_id"]], triggers[p["trigger_id"]]
    c = customers.get(p["customer_id"]) if p.get("customer_id") else None
    s = scorer.score(r, cats[m["category_slug"]], m, t, c)
    fb = s.specificity_reason.startswith("Fallback")
    fallbacks += fb
    rows.append({"test_id": r["test_id"], "kind": t["kind"], "category": m["category_slug"], "fallback": fb,
                 "specificity": s.specificity, "category_fit": s.category_fit, "merchant_fit": s.merchant_fit,
                 "decision_quality": s.decision_quality, "engagement_compulsion": s.engagement_compulsion,
                 "total": s.total, "hint": s.hint,
                 "reasons": {"specificity": s.specificity_reason, "category_fit": s.category_fit_reason,
                             "merchant_fit": s.merchant_fit_reason, "decision_quality": s.decision_quality_reason,
                             "engagement": s.engagement_reason}})
    print(f"{r['test_id']} {t['kind']:26} {m['category_slug']:12} {s.total:2}/50{'  (FALLBACK)' if fb else ''}", flush=True)
real = [x for x in rows if not x["fallback"]]
dims = ["specificity", "category_fit", "merchant_fit", "decision_quality", "engagement_compulsion"]
summary = {"scored": len(rows), "fallbacks": fallbacks,
           "avg_total_real": round(sum(x["total"] for x in real) / max(1, len(real)), 2),
           **{d: round(sum(x[d] for x in real) / max(1, len(real)), 2) for d in dims}}
out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))
print(json.dumps(summary))
