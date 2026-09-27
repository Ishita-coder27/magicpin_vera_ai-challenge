"""Deterministic message-quality lint (development + runtime gate).

Separates "not enough evidence" (legitimate: thin data, honest message) from
"generic wording despite evidence" (a defect). Never penalises a message just
for having no numbers.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .ctx import Ctx

_GENERIC_OPENERS = re.compile(r"^(hi|hello|dear)\b[^,]*,\s*(hope|i hope|greetings|we wanted|i wanted|just checking)", re.I)
_VAGUE_CTA = re.compile(r"would you like to (explore|consider|think)|interested in (exploring|learning)|let me know if", re.I)
_URGENCY = re.compile(r"\b(hurry|last chance|only today|limited time|act now|don't miss out)\b", re.I)
_SOCIAL = re.compile(r"\b(\d+|many|most|other) (merchants|salons|clinics|restaurants|gyms|pharmacies|dentists) (in your area|nearby|already|are)\b", re.I)


def anchors(ctx: Ctx, body: str) -> List[str]:
    """Merchant/customer-specific facts that actually appear in the body."""
    M, C = ctx.merchant, ctx.customer
    low = body.lower()
    found = []
    for o in M.active_offer_titles():
        if o.lower() in low:
            found.append(f"offer:{o}")
    for t in M.review_themes:
        q = str(t.get("common_quote") or "")
        if q and q.lower()[:25] in low:
            found.append("review_quote")
    for key in ("views", "calls", "ctr", "leads", "directions"):
        v = M.perf.get(key)
        if isinstance(v, (int, float)) and v and (str(v) in body or f"{v * 100:.1f}".rstrip("0").rstrip(".") + "%" in body):
            found.append(f"perf:{key}")
    for k, v in M.agg.items():
        if isinstance(v, (int, float)) and v >= 1 and re.search(r"(?<![\d,])" + re.escape(f"{int(v):,}") + r"(?![\d,])", body):
            found.append(f"agg:{k}")
    for e in ctx.ledger:
        src = e.source if isinstance(e.source, str) else " ".join(e.source)
        if re.search(r"merchant\.|customer\.|trigger\.payload", src) and e.text and str(e.text).lower()[:18] in low:
            found.append(f"ledger:{src.split()[0]}")
    for key in ("views", "calls", "leads", "ctr"):
        d = M.delta(key)
        if d and f"{abs(d) * 100:.0f}%" in body:
            found.append(f"delta:{key}")
    for k, v in (ctx.tp or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            forms = {str(v), f"{abs(v) * 100:.0f}%"} if abs(v) <= 1.5 else {str(v), f"{v:,}"}
            if any(f in body for f in forms):
                found.append(f"trigger:{k}")
    for t in M.review_themes:
        n = t.get("occurrences_30d")
        if n and re.search(rf"\b{n} reviews\b", body):
            found.append("review_theme")
    if re.search(r"plan (lapsed|ends|renews|expired)|unverified", low):
        found.append("account_state")
    if C is not None and C.first_name:
        toks = [w for w in re.findall(r"[a-z]{3,}", C.first_name.lower()) if w not in ("mr", "mrs", "ms")]
        if any(w in low for w in toks):
            found.append("customer:name")
    if "you asked" in low or "we sketched" in low or "i flagged" in low or "i suggested" in low:
        found.append("history")
    return sorted(set(found))


def lint(ctx: Ctx, body: str, rationale: str = "") -> Dict[str, List[str]]:
    issues, notes = [], []
    if _GENERIC_OPENERS.search(body):
        issues.append("generic opening")
    if _VAGUE_CTA.search(body):
        issues.append("vague CTA")
    if _URGENCY.search(body) and not re.search(r"deadline|expires|today|tonight|tomorrow|recall", ctx.kind + str(ctx.tp)):
        issues.append("unsupported urgency")
    if _SOCIAL.search(body):
        issues.append("possible fabricated social proof")
    a = anchors(ctx, body)
    thin = ctx.placeholder or not ctx.tp
    universal = ctx.spec.family in ("compliance", "knowledge")   # category-level knowledge applies to every merchant
    if not a and not universal:
        (notes if thin else issues).append("no merchant-specific anchor" + (" (thin trigger)" if thin else ""))
    if len(body) > 700:
        notes.append(f"long ({len(body)} chars)")
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?%?", rationale):
        if len(n) > 2 and n not in body:
            issues.append(f"rationale number {n} not in body")
    return {"issues": issues, "notes": notes, "anchors": a}
