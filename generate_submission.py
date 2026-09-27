#!/usr/bin/env python3
"""Generate submission.jsonl (30 lines) from the official test pairs using the
real engine — no hand edits.

    python generate_submission.py            # writes submission.jsonl
    python generate_submission.py --judge-prompts out.jsonl
        # also writes the exact scoring prompt judge_simulator.LLMScorer would
        # build for each message (for offline judging)

Expands the dataset first if dataset/expanded/ is missing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from vera.composer import compose_full  # noqa: E402

EXPANDED = ROOT / "dataset" / "expanded"


def load_expanded():
    if not (EXPANDED / "test_pairs.json").exists():
        subprocess.run([sys.executable, "generate_dataset.py", "--out", "./expanded"], cwd=ROOT / "dataset", check=True)

    def load(sub, key):
        return {json.loads(p.read_text())[key]: json.loads(p.read_text()) for p in (EXPANDED / sub).glob("*.json")}

    cats = {json.loads(p.read_text())["slug"]: json.loads(p.read_text()) for p in (EXPANDED / "categories").glob("*.json")}
    return (cats, load("merchants", "merchant_id"), load("customers", "customer_id"), load("triggers", "id"),
            json.loads((EXPANDED / "test_pairs.json").read_text())["pairs"])


def judge_prompt(action, category, merchant, trigger, customer):
    """Mirror of judge_simulator.LLMScorer.score() prompt construction."""
    body = action.get("body", "")
    return f"""SCORE THIS MESSAGE:

=== CONTEXT PROVIDED TO BOT ===
Category: {category.get('slug', 'unknown')}
Voice: {category.get('voice', {}).get('tone', 'unknown')}
Taboos: {category.get('voice', {}).get('vocab_taboo', [])[:5]}

Merchant: {merchant.get('identity', {}).get('name', 'unknown')}
Owner: {merchant.get('identity', {}).get('owner_first_name', 'unknown')}
Locality: {merchant.get('identity', {}).get('locality', 'unknown')}
Languages: {merchant.get('identity', {}).get('languages', [])}
Performance: views={merchant.get('performance', {}).get('views', '?')}, calls={merchant.get('performance', {}).get('calls', '?')}, ctr={merchant.get('performance', {}).get('ctr', '?')}
Signals: {merchant.get('signals', [])}
Active Offers: {[o.get('title') for o in merchant.get('offers', []) if o.get('status') == 'active']}

Trigger Kind: {trigger.get('kind', 'unknown')}
Trigger Payload: {json.dumps(trigger.get('payload', {}))}
Trigger Urgency: {trigger.get('urgency', '?')}

Customer: {json.dumps(customer.get('identity', {})) if customer else 'None (merchant-facing)'}

=== BOT'S MESSAGE ===
Body ({len(body)} chars): "{body}"
CTA: {action.get('cta', 'none')}
Send As: {action.get('send_as', 'vera')}

Score each dimension 0-10 with clear reasoning. Be STRICT."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "submission.jsonl"))
    ap.add_argument("--judge-prompts", default=None)
    args = ap.parse_args()
    cats, merchants, customers, triggers, pairs = load_expanded()
    lines, prompts = [], []
    for p in pairs:
        t = triggers[p["trigger_id"]]
        m = merchants[p["merchant_id"]]
        c = customers.get(p["customer_id"]) if p.get("customer_id") else None
        cat = cats[m["category_slug"]]
        comp = compose_full(cat, m, t, c)
        if not comp.validation["ok"]:
            print(f"[warn] {p['test_id']} validation: {comp.validation['issues']}", file=sys.stderr)
        rec = {"test_id": p["test_id"], **comp.public()}
        lines.append(rec)
        if args.judge_prompts:
            prompts.append({"test_id": p["test_id"], "kind": t["kind"], "category": m["category_slug"],
                            "prompt": judge_prompt(rec, cat, m, t, c)})
    with open(args.out, "w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # Verify.
    parsed = [json.loads(l) for l in open(args.out, encoding="utf-8")]
    assert len(parsed) == len(pairs) == 30, f"expected 30 lines, got {len(parsed)}"
    req = {"test_id", "body", "cta", "send_as", "suppression_key", "rationale"}
    for r in parsed:
        missing = req - r.keys()
        assert not missing, f"{r.get('test_id')} missing {missing}"
        assert r["body"].strip(), f"{r['test_id']} empty body"
    print(f"wrote {len(parsed)} lines to {args.out}")
    if args.judge_prompts:
        with open(args.judge_prompts, "w", encoding="utf-8") as f:
            for p in prompts:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"wrote judge prompts to {args.judge_prompts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
